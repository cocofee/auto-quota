"""
清单同义词挖掘工具：从清单库41.6万条名称中自动发现"口语→标准名"映射。

原理：
1. 清单库里同一个东西有很多不同写法（如"坐便器"、"座便器"、"马桶"）
2. 但它们的9位编码是一样的（如都是031001003）
3. 同编码下出现次数最多的名称 = "标准名"
4. 其他变体 → 标准名 = 同义词

挖掘策略：
- 同9位编码、不同核心名称的条目，是天然的同义词对
- 名称相似度>阈值的，也是候选同义词
- 人工同义词(engineering_synonyms)优先，自动挖掘的不覆盖

输出：
- data/bill_synonyms.json：编清单用的同义词表（口语→标准名，替换模式）
- 同时打印可以补充到 engineering_synonyms 的建议（追加模式，套定额用）

用法：python tools/mine_bill_synonyms.py
"""

import json
import re
import sys
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.bill_code_matcher import _extract_core_name


def _is_similar(a: str, b: str) -> bool:
    """判断两个名称是否高度相似（可以当同义词用的那种相似）。

    通过的条件（满足任一）：
    1. 一个是另一个的子串（如"焊接钢管"包含"钢管"）
    2. 只差1-2个字（如"坐便器"vs"座便器"、"PPR管"vs"PP-R管"）
    3. 一个是另一个加了前缀/后缀（如"室内消火栓"vs"消火栓"）

    不通过：
    - 两个名称差异超过一半字符
    - 长度差异超过3个字（子串情况除外）
    """
    if a == b:
        return False

    # 子串关系
    if a in b or b in a:
        return True

    # 长度差异太大（超过3个字且不是子串）
    if abs(len(a) - len(b)) > 3:
        return False

    # 计算编辑距离（简化版：逐字符比较）
    # 对于中文，每个字符是一个有意义的单元
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)

    # 公共字符占比
    common = sum(1 for c in shorter if c in longer)
    ratio = common / max(len(shorter), 1)

    # 相似度要求：80%以上字符重叠，且至少有2个公共字
    if ratio >= 0.8 and common >= 2:
        return True

    # 只差1个字（如"坐便器"vs"座便器"）
    if len(a) == len(b):
        diff = sum(1 for ca, cb in zip(a, b) if ca != cb)
        if diff <= 1:
            return True

    return False


def mine():
    """从清单库挖掘同义词。"""
    lib_path = ROOT / "data" / "bill_library_all.json"
    if not lib_path.exists():
        print("清单库文件不存在")
        return

    with open(lib_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 第1步：按9位编码分组，收集所有核心名称及其出现次数
    # code9 → {core_name: count}
    code_names = defaultdict(Counter)

    for lib_name, lib_data in data.get("libraries", {}).items():
        weight = 3 if "2024" in lib_name else 1
        for item in lib_data.get("items", []):
            code = item.get("code", "")
            name = item.get("name", "")
            if len(code) < 9 or not name:
                continue
            if not re.match(r"^0[1-9]\d{7}", code):
                continue
            code9 = code[:9]
            core = _extract_core_name(name)
            if core and len(core) >= 2:
                code_names[code9][core] += weight

    # 第2步：找出名称相似的同义词对
    # 策略：同编码下的名称，只有"高度相似"的才算同义词
    # 高度相似 = 编辑距离小、或一个是另一个的子串、或只差几个字
    synonyms = {}  # 变体名 → 标准名
    stats = {"codes_with_multi_names": 0, "synonym_pairs": 0}

    for code9, name_counts in code_names.items():
        if len(name_counts) < 2:
            continue
        stats["codes_with_multi_names"] += 1

        # 出现次数最多的 = 标准名
        names_sorted = name_counts.most_common()
        standard = names_sorted[0][0]

        for variant, count in names_sorted[1:]:
            if variant == standard:
                continue
            # 相似度检查：只接受高度相似的名称对
            if not _is_similar(variant, standard):
                continue

            if variant not in synonyms:
                synonyms[variant] = standard
                stats["synonym_pairs"] += 1

    # 第3步：加载现有同义词，避免冲突
    existing_path = ROOT / "data" / "engineering_synonyms.json"
    existing_synonyms = set()
    if existing_path.exists():
        with open(existing_path, "r", encoding="utf-8") as f:
            eng_syn = json.load(f)
        for key in eng_syn:
            if not key.startswith("_"):
                existing_synonyms.add(key)

    # 也加载 bill_code_matcher 的硬编码同义词
    from src.bill_code_matcher import SYNONYMS as BILL_SYNONYMS
    for key in BILL_SYNONYMS:
        existing_synonyms.add(key)

    # 过滤掉已有的
    new_synonyms = {}
    for variant, standard in synonyms.items():
        if variant not in existing_synonyms:
            new_synonyms[variant] = standard

    # 第4步：输出结果
    print("=" * 70)
    print("清单同义词挖掘结果")
    print("=" * 70)
    print(f"\n清单库编码数: {len(code_names)}")
    print(f"多名称编码数: {stats['codes_with_multi_names']}")
    print(f"挖掘同义词对: {stats['synonym_pairs']}")
    print(f"去重后新增:   {len(new_synonyms)}（排除已有engineering_synonyms和硬编码）")

    # 按标准名分组展示
    by_standard = defaultdict(list)
    for variant, standard in sorted(new_synonyms.items()):
        by_standard[standard].append(variant)

    print(f"\n涉及标准名:   {len(by_standard)}个")

    # 挑出高价值同义词（变体数>=2或与engineering_synonyms可合并的）
    high_value = {s: vs for s, vs in by_standard.items() if len(vs) >= 2}
    print(f"高价值(>=2变体): {len(high_value)}个标准名")

    print("\n" + "-" * 70)
    print("高价值同义词（每个标准名有2个以上变体）")
    print("-" * 70)
    for standard, variants in sorted(high_value.items(),
                                      key=lambda x: -len(x[1]))[:50]:
        vs = ", ".join(sorted(variants)[:5])
        extra = f"（+{len(variants)-5}）" if len(variants) > 5 else ""
        print(f"  {standard} ← {vs}{extra}")

    print("\n" + "-" * 70)
    print("单变体同义词样本（前30个）")
    print("-" * 70)
    single = {s: vs for s, vs in by_standard.items() if len(vs) == 1}
    for standard, variants in sorted(single.items())[:30]:
        print(f"  {variants[0]} → {standard}")

    # 第5步：保存结果
    output_path = ROOT / "data" / "bill_synonyms.json"
    output_data = {
        "_meta": {
            "description": "清单同义词（从清单库自动挖掘）",
            "source": "41.6万条清单库，同9位编码不同名称",
            "total_pairs": len(new_synonyms),
            "usage": "编清单(替换模式) + 套定额(追加模式)",
        },
        # 按标准名分组存储，方便两种模式使用
        # 编清单：variant → standard（替换）
        # 套定额：variant → standard（追加搜索词）
        "synonyms": new_synonyms,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"\n已保存到: {output_path}")

    # 第6步：生成可补充到 engineering_synonyms 的建议
    suggest_path = ROOT / "output" / "temp" / "synonym_suggestions.json"
    suggest_path.parent.mkdir(parents=True, exist_ok=True)
    # 格式和engineering_synonyms一致：key → [value]
    suggestions = {}
    for variant, standard in sorted(new_synonyms.items()):
        suggestions[variant] = [standard]
    with open(suggest_path, "w", encoding="utf-8") as f:
        json.dump(suggestions, f, ensure_ascii=False, indent=2)
    print(f"套定额建议补充: {suggest_path}")

    return new_synonyms


if __name__ == "__main__":
    mine()
