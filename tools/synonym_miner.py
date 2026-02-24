# -*- coding: utf-8 -*-
"""
从经验库自动挖掘同义词

思路：
  同一个定额被不同清单文本匹配 → 这些清单的核心名词是同义词。

  例如：
    经验库中 C10-2-79 对应的清单有：
      - "镀锌钢管DN25 螺纹连接"  → 核心名词："镀锌钢管"
      - "白铁管DN25 丝扣连接"    → 核心名词："白铁管"
    → 同义词候选：白铁管 → 镀锌钢管（以定额库写法为准）

  过滤条件：
    - 只用权威层数据（用户确认过的，质量有保证）
    - 同一定额至少3条不同清单才分组
    - 候选对出现次数 >= 2 才输出

用法:
    python tools/synonym_miner.py --preview            # 预览挖掘结果
    python tools/synonym_miner.py --output data/auto_synonyms.json  # 写入文件
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# 把项目根目录加入 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config

# 需要从名词中过滤掉的词（参数词、动作词、修饰词、标签词）
_PARAM_WORDS = {
    "安装", "铺设", "敷设", "制作", "施工", "布线", "架设", "连接",
    "以内", "以上", "以下", "不超过", "单管", "双管",
    "名称", "类型", "型号", "规格", "材质", "材料",
    "超高", "配管", "布设",
}

# 电缆型号前缀（这些是具体型号不是名词，需要去掉）
_CABLE_MODELS = re.compile(
    r'(?:wdz[a-z]*|yjy|yjv|bv|bvr|rvv|kvv|syv|rvs|'
    r'nhyjv|nhbv|zr|zbn|zc|zra|nh)[a-z]*'
)

# 提取核心名词时需要去掉的正则模式
_RE_PARAMS = re.compile(
    r'[Dd][Nn]\s*\d+|[Dd][Ee]\s*\d+|'    # DN25, De32
    r'\d+\s*(?:mm²|mm2|平方)|'              # 4mm²
    r'\d+\s*(?:kVA|kv|kV|A)|'               # 30kVA
    r'[ΦφΦ]\s*\d+|'                         # Φ25
    r'\d+\s*[回路]|'                         # 4回路
    r'公称直径\s*(?:\(mm(?:以内)?\))?\s*\d+|'  # 公称直径(mm)25
    r'\d+(?:\.\d+)?\s*[吨t]'                 # 2.5吨
)

# 核心名词最大长度（超过的大概率是整段描述没有被成功精简）
_MAX_NOUN_LEN = 12


def extract_core_nouns(bill_name: str) -> str:
    """从清单名称中提取核心名词（去掉参数、型号、动作词）

    只保留材质/物品类型相关的中文和关键字母，用于同义词对比。

    例如：
      "镀锌钢管DN25" → "镀锌钢管"
      "PPR管De32" → "ppr管"
      "配电箱1-AL" → "配电箱"
      "电力电缆WDZ-YJY" → "电力电缆"
    """
    s = bill_name.lower()

    # 去括号及内容
    s = re.sub(r'[（(][^）)]*[）)]', '', s)

    # 去参数（DN/截面/容量等）
    s = _RE_PARAMS.sub('', s)

    # 去电缆型号前缀
    s = _CABLE_MODELS.sub('', s)

    # 去纯数字和编号（如 1-AL、B1-AP 等）
    s = re.sub(r'[a-z]*\d+[-a-z\d]*', '', s)  # 先去包含数字的英文+数字串
    s = re.sub(r'\d+', '', s)

    # 去标点、空格和特殊字符（只保留中文和部分英文）
    s = re.sub(r'[^\u4e00-\u9fffa-z]', '', s)

    # 去动作词和标签词
    for w in _PARAM_WORDS:
        s = s.replace(w, '')

    s = s.strip()

    # 超长说明截断为空（不太可能是有效的核心名词）
    if len(s) > _MAX_NOUN_LEN:
        return ""

    # 去重：如果前半段和后半段相同（"碳钢通风管道碳钢通风管道"），取前半段
    if len(s) >= 4 and len(s) % 2 == 0:
        half = len(s) // 2
        if s[:half] == s[half:]:
            s = s[:half]

    return s


def mine_synonyms(min_group_size: int = 3, min_occurrence: int = 2) -> dict:
    """从经验库挖掘同义词

    参数:
        min_group_size: 同一定额至少多少条不同清单才分组
        min_occurrence: 候选名词对至少出现多少次才输出

    返回:
        dict，格式同 engineering_synonyms.json：{清单名词: [定额名词]}
    """
    db_path = config.get_experience_db_path()
    if not db_path.exists():
        print(f"经验库不存在: {db_path}")
        return {}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 只取权威层数据（用户确认过的），优先用 bill_name（更短更干净）
    cursor.execute("""
        SELECT bill_name, bill_text, quota_ids, quota_names
        FROM experiences
        WHERE layer = 'authority' AND quota_ids IS NOT NULL AND quota_ids != '[]'
    """)

    # 按第一个定额编号分组（同一个定额的不同清单写法）
    quota_groups = defaultdict(list)  # {quota_id: [(core_noun, ...)]}
    for row in cursor.fetchall():
        try:
            qids = json.loads(row["quota_ids"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not qids:
            continue

        first_qid = qids[0]  # 按第一个定额分组
        # 优先用 bill_name（短名称），不行再用 bill_text
        name = row["bill_name"] or row["bill_text"] or ""
        core = extract_core_nouns(name)
        if core and len(core) >= 2:  # 核心名词至少2字
            quota_groups[first_qid].append({
                "core_noun": core,
                "quota_names": row["quota_names"],
            })

    conn.close()

    # 从每个分组中找不同的核心名词 → 同义词候选
    noun_pairs = defaultdict(int)  # {(noun_a, noun_b): count}
    noun_to_quota_name = {}  # {core_noun: 定额名称}（记录哪个名词对应哪个定额写法）

    for qid, items in quota_groups.items():
        if len(items) < min_group_size:
            continue

        # 收集该定额下所有不同的核心名词
        unique_nouns = set()
        for item in items:
            unique_nouns.add(item["core_noun"])
            # 从定额名称中也提取核心名词（作为"标准写法"参考）
            try:
                qnames = json.loads(item["quota_names"]) if isinstance(item["quota_names"], str) else []
                if qnames:
                    quota_core = extract_core_nouns(qnames[0])
                    if quota_core:
                        noun_to_quota_name[item["core_noun"]] = quota_core
            except (json.JSONDecodeError, TypeError):
                pass

        # 两两配对
        nouns = sorted(unique_nouns)
        for i in range(len(nouns)):
            for j in range(i + 1, len(nouns)):
                if nouns[i] != nouns[j]:
                    pair = (nouns[i], nouns[j])
                    noun_pairs[pair] += 1

    # 过滤：只保留出现次数 >= min_occurrence 的候选对
    result = {}
    for (noun_a, noun_b), count in sorted(noun_pairs.items(), key=lambda x: -x[1]):
        if count < min_occurrence:
            continue

        # 过滤：两个名词必须共享至少一个中文bigram（防止完全不相关的物品配对）
        # 例如"截止阀"和"止回阀"共享"阀" → 但要求bigram（2字），所以不会配对
        # "镀锌钢管"和"白铁管"不共享bigram → 也不会配对...
        # 改为：至少一个名词被另一个包含，或共享至少2个字
        if not _are_related_nouns(noun_a, noun_b):
            continue

        # 较长的名词通常是更具体的写法 → 作为key（清单常用名）
        # 较短的或定额库中的写法 → 作为value（定额常用名）
        if len(noun_a) >= len(noun_b):
            key, val = noun_a, noun_b
        else:
            key, val = noun_b, noun_a

        # 跳过完全相同或仅大小写不同
        if key == val:
            continue

        if key not in result:
            result[key] = [val]

    return result


def _are_related_nouns(a: str, b: str) -> bool:
    """判断两个名词是否相关（至少一个包含关系，或共享2+字符）

    宽松标准：适用于同义词挖掘场景。
    太严格会漏掉"白铁管"→"镀锌钢管"这类真同义词。
    太宽松会引入"截止阀"→"止回阀"这类假同义词。
    """
    # 包含关系：一方是另一方的子串
    if a in b or b in a:
        return True

    # 共享至少2个中文字符
    chinese_a = set(re.findall(r'[\u4e00-\u9fff]', a))
    chinese_b = set(re.findall(r'[\u4e00-\u9fff]', b))
    shared = chinese_a & chinese_b
    return len(shared) >= 2


def main():
    parser = argparse.ArgumentParser(description="从经验库自动挖掘同义词")
    parser.add_argument("--preview", action="store_true", help="预览挖掘结果（不写文件）")
    parser.add_argument("--output", type=str, default=None, help="输出文件路径")
    parser.add_argument("--min-group", type=int, default=3, help="最小分组大小（默认3）")
    parser.add_argument("--min-count", type=int, default=2, help="最小出现次数（默认2）")
    args = parser.parse_args()

    print(f"正在从经验库挖掘同义词（min_group={args.min_group}, min_count={args.min_count}）...")
    synonyms = mine_synonyms(
        min_group_size=args.min_group,
        min_occurrence=args.min_count,
    )

    if not synonyms:
        print("未挖掘到有效同义词候选（经验库权威层数据不足或同义词过少）")
        return

    print(f"\n挖掘到 {len(synonyms)} 条同义词候选：")
    for key, val in list(synonyms.items())[:20]:  # 只显示前20条
        print(f"  {key} → {val[0]}")
    if len(synonyms) > 20:
        print(f"  ... 共 {len(synonyms)} 条")

    if args.preview:
        return

    # 写入文件
    output_path = args.output or str(PROJECT_ROOT / "data" / "auto_synonyms.json")
    output = {
        "_说明": "自动从经验库挖掘的同义词，由 tools/synonym_miner.py 生成，请勿手动编辑",
        "_generated_from": "经验库权威层数据",
    }
    output.update(synonyms)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n已写入: {output_path}")


if __name__ == "__main__":
    main()
