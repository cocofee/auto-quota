"""
清单库同义词挖掘（五步法第4步）

原理：
  清单库里同一个东西有很多不同写法（如"坐便器"、"座便器"、"马桶"），
  但它们的9位编码前缀一样（如都是031001003）。
  同编码下出现次数最多的名称 = "标准名"，其他变体 → 同义词候选。

数据源：bill_library.db（80万条清单项，比旧JSON版大10倍）

输出：
  1. data/bill_synonyms.json — 编清单用的同义词表（替换模式）
  2. output/temp/synonym_suggestions.json — 可补充到 engineering_synonyms 的建议

用法：
    python tools/mine_bill_synonyms.py                   # 挖掘并保存
    python tools/mine_bill_synonyms.py --preview         # 只预览不保存
    python tools/mine_bill_synonyms.py --stats           # 查看统计
"""

import json
import re
import sys
import argparse
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.sqlite import connect

# ============================================================
# 常量
# ============================================================

SOURCE_DB = ROOT / "data" / "bill_library.db"
DATA_DIR = ROOT / "data"

# 措施费编码前缀（费用项目，名称变化多但无同义词价值）
MEASURE_PREFIXES = {"031301", "031302", "031303", "031304", "031305"}

# 参数正则（从清单名称中去掉数字参数、型号等）
RE_PARAMS = re.compile(
    r'[Dd][Nn]\s*\d+|[Dd][Ee]\s*\d+|'
    r'\d+\s*(?:mm²|mm2|平方|kVA|kv|kV|A|W|w)|'
    r'[ΦφΦ]\s*\d+|'
    r'\d+(?:\.\d+)?(?:[x×*]\d+(?:\.\d+)?)*|'
    r'\d+'
)

# 噪声词（动作/位置/修饰词，不是核心名词）
NOISE_WORDS = {"安装", "铺设", "敷设", "制作", "施工", "布线", "架设",
               "检测", "调试", "配管", "布设", "超高"}


# ============================================================
# 核心名词提取
# ============================================================

def extract_core(name):
    """从清单名称中提取核心名词（去掉参数、型号、序号）

    例如：
      "镀锌钢管DN25" → "镀锌钢管"
      "PPR管De32" → "PPR管"
      "1-配电箱AL" → "配电箱"
      "排水塑料管" → "排水塑料管"
    """
    if not name or len(name) < 2:
        return ""
    # 去前面的序号（如 "1-PPR管"、"23.塑料管"）
    s = re.sub(r'^\d+[-.\s]*', '', name)
    # 去括号内容
    s = re.sub(r'[（(][^）)]*[）)]', '', s)
    # 去参数（DN/截面/容量/尺寸等数字）
    s = RE_PARAMS.sub('', s)
    # 去特殊字符，只保留中文和英文字母
    s = re.sub(r'[^\u4e00-\u9fffa-zA-Z]', '', s)
    # 去噪声词
    for w in NOISE_WORDS:
        s = s.replace(w, '')
    s = s.strip()
    # 太长说明没有成功精简，放弃
    if len(s) > 12 or len(s) < 2:
        return ""
    return s


def is_similar(a, b):
    """判断两个核心名词是否高度相似（可以当同义词的那种）

    通过条件（满足任一）：
    1. 一个是另一个的子串（"焊接钢管"包含"钢管"）
    2. 只差1个字（"坐便器" vs "座便器"）
    3. 80%以上字符重叠，且至少2个公共字
    """
    if a == b:
        return False
    # 子串关系
    if a in b or b in a:
        return True
    # 长度差异太大
    if abs(len(a) - len(b)) > 3:
        return False
    # 只差1个字
    if len(a) == len(b):
        diff = sum(1 for ca, cb in zip(a, b) if ca != cb)
        if diff <= 1:
            return True
    # 公共字符占比
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    common = sum(1 for c in shorter if c in longer)
    ratio = common / max(len(shorter), 1)
    return ratio >= 0.8 and common >= 2


# ============================================================
# 挖掘逻辑
# ============================================================

def mine_from_db(conn, min_freq=5):
    """从 bill_library.db 挖掘同义词

    参数:
        min_freq: 名称至少出现多少次才参与（去噪）

    返回:
        (synonyms_dict, stats_dict)
        synonyms_dict: {变体名: 标准名}
        stats_dict: 统计信息
    """
    # 按编码前9位分组，统计每个核心名称的出现次数
    rows = conn.execute("""
        SELECT SUBSTR(bill_code, 1, 9) as prefix,
               bill_name,
               COUNT(*) as cnt
        FROM bill_items
        WHERE bill_name != '' AND LENGTH(bill_name) >= 2
        GROUP BY prefix, bill_name
        HAVING cnt >= ?
        ORDER BY prefix, cnt DESC
    """, (min_freq,)).fetchall()

    # 按前缀分组：{prefix: [(core_name, orig_name, count), ...]}
    code_groups = defaultdict(list)
    for prefix, name, cnt in rows:
        # 跳过措施费
        if any(prefix.startswith(mp) for mp in MEASURE_PREFIXES):
            continue
        core = extract_core(name)
        if core:
            code_groups[prefix].append((core, name, cnt))

    # 每个前缀内，找同义词对
    synonyms = {}  # {变体: 标准名}
    stats = {
        "total_prefixes": len(code_groups),
        "multi_name_prefixes": 0,
        "synonym_pairs": 0,
    }

    for prefix, items in code_groups.items():
        # 按核心名词去重（保留频次最高的原始名称）
        core_map = {}  # {core: (orig_name, total_count)}
        for core, orig, cnt in items:
            key = core.lower()
            if key not in core_map:
                core_map[key] = (core, orig, cnt)
            else:
                # 累加频次
                old_core, old_orig, old_cnt = core_map[key]
                core_map[key] = (old_core, old_orig, old_cnt + cnt)

        unique_cores = list(core_map.values())
        if len(unique_cores) < 2:
            continue

        stats["multi_name_prefixes"] += 1

        # 频次最高的 = 标准名
        unique_cores.sort(key=lambda x: -x[2])
        standard_core, standard_orig, standard_cnt = unique_cores[0]

        for alt_core, alt_orig, alt_cnt in unique_cores[1:]:
            if not is_similar(standard_core, alt_core):
                continue
            # 更长的作为key（清单写法通常更长/更具体）
            if len(alt_core) >= len(standard_core):
                key, val = alt_core, standard_core
            else:
                key, val = standard_core, alt_core

            if key not in synonyms:
                synonyms[key] = val
                stats["synonym_pairs"] += 1

    return synonyms, stats


def load_existing_synonyms():
    """加载所有现有同义词词（engineering + auto + bill_code_matcher硬编码）"""
    known = set()

    for filename in ["engineering_synonyms.json", "auto_synonyms.json"]:
        path = DATA_DIR / filename
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, vals in data.items():
            if key.startswith("_"):
                continue
            known.add(key.lower())
            if isinstance(vals, list):
                for v in vals:
                    known.add(v.lower())

    # 尝试加载硬编码同义词
    try:
        from src.bill_code_matcher import SYNONYMS as BILL_SYNONYMS
        for key in BILL_SYNONYMS:
            known.add(key.lower())
    except (ImportError, AttributeError):
        pass

    return known


# ============================================================
# 输出
# ============================================================

def show_preview(conn):
    """预览挖掘结果"""
    synonyms, stats = mine_from_db(conn)
    existing = load_existing_synonyms()

    # 过滤掉已有的
    new_synonyms = {k: v for k, v in synonyms.items()
                    if k.lower() not in existing}

    print("=" * 60)
    print("清单库同义词挖掘预览")
    print("=" * 60)
    print(f"编码前缀总数: {stats['total_prefixes']}")
    print(f"多名称前缀:   {stats['multi_name_prefixes']}")
    print(f"挖掘同义词对: {stats['synonym_pairs']}")
    print(f"去重后新增:   {len(new_synonyms)}（排除已有词表）")

    # 按标准名分组
    by_standard = defaultdict(list)
    for variant, standard in sorted(new_synonyms.items()):
        by_standard[standard].append(variant)

    # 高价值（>=2个变体）
    high_value = {s: vs for s, vs in by_standard.items() if len(vs) >= 2}
    print(f"\n高价值(>=2变体): {len(high_value)} 个标准名")

    print("\n" + "-" * 60)
    print("高价值同义词（每个标准名有2个以上变体）")
    print("-" * 60)
    for standard, variants in sorted(high_value.items(),
                                      key=lambda x: -len(x[1]))[:40]:
        vs = ", ".join(sorted(variants)[:5])
        extra = f"（+{len(variants)-5}）" if len(variants) > 5 else ""
        print(f"  {standard} ← {vs}{extra}")

    print("\n" + "-" * 60)
    print("单变体同义词样本（前30个）")
    print("-" * 60)
    single = {s: vs for s, vs in by_standard.items() if len(vs) == 1}
    for standard, variants in sorted(single.items())[:30]:
        print(f"  {variants[0]} → {standard}")


def run_mine(conn, save=True):
    """执行挖掘并保存"""
    synonyms, stats = mine_from_db(conn)
    existing = load_existing_synonyms()

    # 过滤已有的
    new_synonyms = {k: v for k, v in synonyms.items()
                    if k.lower() not in existing}

    print("=" * 60)
    print("清单库同义词挖掘结果")
    print("=" * 60)
    print(f"编码前缀总数: {stats['total_prefixes']}")
    print(f"多名称前缀:   {stats['multi_name_prefixes']}")
    print(f"挖掘同义词对: {stats['synonym_pairs']}")
    print(f"去重后新增:   {len(new_synonyms)}（排除已有词表）")

    if not save or not new_synonyms:
        return new_synonyms

    # 保存编清单用的同义词表
    bill_output = {
        "_meta": {
            "description": "清单同义词（从80万条清单库自动挖掘）",
            "source": "bill_library.db，同9位编码不同名称",
            "total_pairs": len(new_synonyms),
            "usage": "编清单(替换模式) + 套定额(追加模式)",
        },
        "synonyms": new_synonyms,
    }
    bill_path = DATA_DIR / "bill_synonyms.json"
    with open(bill_path, "w", encoding="utf-8") as f:
        json.dump(bill_output, f, ensure_ascii=False, indent=2)
    print(f"\n编清单同义词: {bill_path}")

    # 保存套定额建议（格式同engineering_synonyms）
    suggest_path = ROOT / "output" / "temp" / "synonym_suggestions.json"
    suggest_path.parent.mkdir(parents=True, exist_ok=True)
    suggestions = {k: [v] for k, v in sorted(new_synonyms.items())}
    with open(suggest_path, "w", encoding="utf-8") as f:
        json.dump(suggestions, f, ensure_ascii=False, indent=2)
    print(f"套定额建议:   {suggest_path}")

    # 展示高价值样本
    by_standard = defaultdict(list)
    for variant, standard in new_synonyms.items():
        by_standard[standard].append(variant)
    high_value = {s: vs for s, vs in by_standard.items() if len(vs) >= 2}

    if high_value:
        print(f"\n高价值同义词（前20组）:")
        for standard, variants in sorted(high_value.items(),
                                          key=lambda x: -len(x[1]))[:20]:
            vs = ", ".join(sorted(variants)[:5])
            print(f"  {standard} ← {vs}")

    return new_synonyms


def show_stats(conn):
    """显示统计信息"""
    existing = load_existing_synonyms()
    synonyms, stats = mine_from_db(conn)
    new_synonyms = {k: v for k, v in synonyms.items()
                    if k.lower() not in existing}

    print(f"现有同义词词表: {len(existing)} 个词")
    print(f"清单库编码前缀: {stats['total_prefixes']} 个")
    print(f"多名称前缀:     {stats['multi_name_prefixes']} 个")
    print(f"挖掘同义词对:   {stats['synonym_pairs']} 个")
    print(f"新增候选:       {len(new_synonyms)} 个（去重后）")


# ============================================================
# 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="清单库同义词挖掘（五步法第4步）")
    parser.add_argument("--preview", action="store_true", help="只预览不保存")
    parser.add_argument("--stats", action="store_true", help="查看统计")
    args = parser.parse_args()

    if not SOURCE_DB.exists():
        print(f"数据库不存在: {SOURCE_DB}")
        print("请先运行: python tools/extract_bill_data.py")
        return

    conn = connect(SOURCE_DB)
    try:
        if args.stats:
            show_stats(conn)
        elif args.preview:
            show_preview(conn)
        else:
            run_mine(conn, save=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
