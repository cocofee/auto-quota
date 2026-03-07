# -*- coding: utf-8 -*-
"""
同义词表静态分析工具

分析 engineering_synonyms.json 中的所有问题：
1. 自映射（key == value）的防御性作用评估
2. 多值数组第二项无效
3. 映射准确性评估
4. 与代码逻辑的冲突检测
5. 过于模糊的映射（如"灯安装"）

用法：
    python tools/analyze_synonyms.py           # 完整分析
    python tools/analyze_synonyms.py --fix     # 生成修复建议JSON
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SYNONYMS_PATH = PROJECT_ROOT / "data" / "engineering_synonyms.json"
AUTO_SYNONYMS_PATH = PROJECT_ROOT / "data" / "auto_synonyms.json"


def load_all_synonyms():
    """加载手工+自动同义词，模拟query_builder的合并逻辑"""
    with open(SYNONYMS_PATH, "r", encoding="utf-8") as f:
        manual_raw = json.load(f)

    manual = {
        k: v for k, v in manual_raw.items()
        if not k.startswith("_") and isinstance(v, list) and v
    }

    auto = {}
    if AUTO_SYNONYMS_PATH.exists():
        with open(AUTO_SYNONYMS_PATH, "r", encoding="utf-8") as f:
            auto_raw = json.load(f)
        auto = {
            k: v for k, v in auto_raw.items()
            if not k.startswith("_") and isinstance(v, list) and v
        }

    # 合并：手工覆盖自动
    merged = {}
    merged.update(auto)
    merged.update(manual)

    # 按key长度降序（同query_builder逻辑）
    sorted_keys = sorted(merged.keys(), key=len, reverse=True)
    return {k: merged[k] for k in sorted_keys}, manual, auto


def analyze_self_mappings(sorted_syns):
    """分析自映射及其防御性作用"""
    results = {
        "exact_self": [],       # 完全自映射 key == v[0]
        "suffix_only": [],      # 仅加安装/敷设后缀
        "defensive_needed": [],  # 删除后有风险的
        "safe_to_delete": [],    # 安全可删的
    }

    all_keys = list(sorted_syns.keys())

    for i, key in enumerate(all_keys):
        vals = sorted_syns[key]
        primary = vals[0] if isinstance(vals, list) else vals

        is_exact = (primary == key)
        suffixes = ["安装", "敷设", "调试", "卡设"]
        is_suffix = any(primary == key + s or primary == key + " " + s for s in suffixes)

        if not is_exact and not is_suffix:
            continue

        if is_exact:
            results["exact_self"].append(key)
        elif is_suffix:
            results["suffix_only"].append({"key": key, "value": primary})

        # 防御性分析：删除此key后，query中包含key时，
        # 是否会被后续更短的key匹配到错误的replacement
        risk_matches = []
        for j in range(i + 1, len(all_keys)):
            other_key = all_keys[j]
            other_val = sorted_syns[other_key]
            other_primary = other_val[0] if isinstance(other_val, list) else other_val
            # 检查：其他key是否是当前key的子串
            if other_key in key and other_key != key:
                # 这个更短的key会匹配到当前key对应的query
                risk_matches.append({
                    "shorter_key": other_key,
                    "would_replace_to": other_primary,
                    "original_stays_as": key  # 自映射保持原样
                })

        if risk_matches:
            results["defensive_needed"].append({
                "key": key,
                "current_value": primary,
                "risks": risk_matches[:3]  # 只显示前3个风险
            })
        else:
            results["safe_to_delete"].append({
                "key": key,
                "current_value": primary,
                "type": "exact" if is_exact else "suffix"
            })

    return results


def analyze_multi_value_waste(manual):
    """分析多值数组第二项无效的情况"""
    wasted = []
    for k, v in manual.items():
        if k.startswith("_"):
            continue
        if isinstance(v, list) and len(v) > 1:
            wasted.append({
                "key": k,
                "used": v[0],
                "wasted": v[1:],
            })
    return wasted


def analyze_vague_mappings(sorted_syns):
    """检测过于模糊的映射"""
    vague = []
    # 过于模糊的定额搜索词（太短或太泛）
    vague_patterns = [
        "灯安装", "管安装", "阀安装", "灯具安装", "设备安装",
        "管道安装", "安装"
    ]
    for k, v in sorted_syns.items():
        primary = v[0] if isinstance(v, list) else v
        if primary in vague_patterns:
            vague.append({"key": k, "value": primary, "reason": "映射词过于模糊，BM25无法有效区分"})
        elif len(primary) <= 2 and primary != k:
            vague.append({"key": k, "value": primary, "reason": f"映射词太短({len(primary)}字)"})
    return vague


def analyze_code_conflicts(sorted_syns):
    """检测与query_builder.py代码逻辑的冲突"""
    conflicts = []

    # 已知冲突1：配电箱不应加"安装"后缀（query_builder.py:466-470）
    if "配电箱" in sorted_syns:
        val = sorted_syns["配电箱"]
        primary = val[0] if isinstance(val, list) else val
        if "安装" in primary:
            conflicts.append({
                "key": "配电箱",
                "synonym_value": primary,
                "code_intent": "代码明确不加'安装'后缀(query_builder.py:466-470)",
                "conflict": "同义词又加回了'安装'，可能导致匹配到'杆上配电设备安装 配电箱'"
            })

    # 已知冲突2：灯具类先经过_normalize_bill_name处理
    # 同义词中的灯具映射可能被代码先行处理掉
    lamp_keys = [k for k in sorted_syns if "灯" in k and
                 not any(x in k for x in ["灯杆", "灯塔", "灯槽", "灯箱", "灯带槽"])]
    for k in lamp_keys:
        val = sorted_syns[k]
        primary = val[0] if isinstance(val, list) else val
        conflicts.append({
            "key": k,
            "synonym_value": primary,
            "code_intent": "灯具类先经过_normalize_bill_name()处理",
            "conflict": "同义词可能不会生效（被代码提前替换了）",
            "severity": "info"  # 不一定是错，需要逐条确认
        })

    # 已知冲突3：阀门类先经过build_quota_query的阀门路由处理
    valve_keys = [k for k in sorted_syns if any(v in k for v in
                  ["闸阀", "蝶阀", "止回阀", "球阀", "截止阀"])]
    for k in valve_keys:
        val = sorted_syns[k]
        primary = val[0] if isinstance(val, list) else val
        if k == primary:  # 自映射
            conflicts.append({
                "key": k,
                "synonym_value": primary,
                "code_intent": "阀门类在build_quota_query中按DN值路由到法兰/螺纹阀门",
                "conflict": "自映射可能阻止代码路由生效（自映射break后不进代码路由？需验证）",
                "severity": "warning"
            })

    return conflicts


def analyze_accuracy(sorted_syns):
    """映射准确性分析（基于造价专业知识的规则检查）"""
    issues = []

    # 已知错误映射规则
    known_wrong = {
        "应急照明灯": {"current": "诱导灯安装", "correct": "应急照明灯具安装",
                     "reason": "应急照明灯和诱导灯(疏散指示灯)是不同的定额子目"},
        "消防应急照明灯": {"current": "诱导灯安装", "correct": "应急照明灯具安装",
                       "reason": "同上，消防应急照明≠疏散指示"},
        "格栅灯": {"current": "灯安装", "correct": "嵌入式灯 组合荧光灯",
                 "reason": "格栅灯是嵌入式荧光灯/LED面板灯，'灯安装'过于模糊"},
        "防爆灯": {"current": "吸顶灯具安装", "correct": "密闭灯安装 防爆灯",
                 "reason": "防爆灯有独立定额子目，代码里也有处理(query_builder:347)"},
        "射灯": {"current": "投光灯", "correct": "点光源艺术装饰灯具 射灯",
               "reason": "室内射灯(轨道灯/嵌入式)≠室外投光灯"},
    }

    for k, v in sorted_syns.items():
        primary = v[0] if isinstance(v, list) else v
        if k in known_wrong:
            info = known_wrong[k]
            if primary == info["current"]:
                issues.append({
                    "key": k,
                    "current": primary,
                    "suggested": info["correct"],
                    "reason": info["reason"],
                    "severity": "error"
                })

    return issues


def main():
    import argparse
    parser = argparse.ArgumentParser(description="同义词表静态分析")
    parser.add_argument("--fix", action="store_true", help="生成修复建议JSON")
    args = parser.parse_args()

    sorted_syns, manual, auto = load_all_synonyms()
    total = len(sorted_syns)

    print(f"=== 同义词表静态分析 ===")
    print(f"手工表: {len(manual)} 条 | 自动表: {len(auto)} 条 | 合并后: {total} 条")
    print()

    # 1. 自映射分析
    self_results = analyze_self_mappings(sorted_syns)
    print(f"--- 1. 自映射分析 ---")
    print(f"完全自映射: {len(self_results['exact_self'])} 条")
    print(f"仅加后缀: {len(self_results['suffix_only'])} 条")
    print(f"有防御作用(删除有风险): {len(self_results['defensive_needed'])} 条")
    print(f"安全可删: {len(self_results['safe_to_delete'])} 条")
    print()

    if self_results["defensive_needed"]:
        print("⚠️ 有防御作用的自映射（删除可能导致错误替换）:")
        for item in self_results["defensive_needed"]:
            print(f"  \"{item['key']}\" → \"{item['current_value']}\"")
            for risk in item["risks"]:
                print(f"    ⚡ 删除后可能被 \"{risk['shorter_key']}\"→\"{risk['would_replace_to']}\" 替换")
        print()

    if self_results["safe_to_delete"]:
        print(f"✅ 安全可删的自映射（无后续key会抢匹配）:")
        for item in self_results["safe_to_delete"][:10]:
            print(f"  \"{item['key']}\" → \"{item['current_value']}\" [{item['type']}]")
        if len(self_results["safe_to_delete"]) > 10:
            print(f"  ... 共 {len(self_results['safe_to_delete'])} 条")
        print()

    # 2. 多值浪费
    wasted = analyze_multi_value_waste(manual)
    print(f"--- 2. 多值数组分析 ---")
    print(f"有多值的条目: {len(wasted)} 条（第二个值起全部无效）")
    for item in wasted[:5]:
        print(f"  \"{item['key']}\": 用={item['used']}, 废={item['wasted']}")
    if len(wasted) > 5:
        print(f"  ... 共 {len(wasted)} 条")
    print()

    # 3. 过于模糊的映射
    vague = analyze_vague_mappings(sorted_syns)
    print(f"--- 3. 过于模糊的映射 ---")
    print(f"模糊映射: {len(vague)} 条")
    for item in vague:
        print(f"  \"{item['key']}\" → \"{item['value']}\"  ({item['reason']})")
    print()

    # 4. 代码冲突
    conflicts = analyze_code_conflicts(sorted_syns)
    real_conflicts = [c for c in conflicts if c.get("severity") != "info"]
    print(f"--- 4. 代码逻辑冲突 ---")
    print(f"冲突: {len(real_conflicts)} 条（另有 {len(conflicts)-len(real_conflicts)} 条需关注的灯具项）")
    for item in real_conflicts:
        print(f"  ❌ \"{item['key']}\" → \"{item['synonym_value']}\"")
        print(f"     代码意图: {item['code_intent']}")
        print(f"     冲突: {item['conflict']}")
    print()

    # 5. 映射准确性
    accuracy = analyze_accuracy(sorted_syns)
    print(f"--- 5. 映射准确性 ---")
    print(f"已知错误映射: {len(accuracy)} 条")
    for item in accuracy:
        print(f"  ❌ \"{item['key']}\": \"{item['current']}\" → 应为 \"{item['suggested']}\"")
        print(f"     原因: {item['reason']}")
    print()

    # 汇总
    print("=" * 60)
    print("汇总:")
    safe_delete_count = len(self_results["safe_to_delete"])
    defensive_count = len(self_results["defensive_needed"])
    print(f"  可安全删除: {safe_delete_count} 条自映射")
    print(f"  需谨慎处理: {defensive_count} 条防御性自映射")
    print(f"  需修正映射: {len(accuracy)} 条已知错误")
    print(f"  需解决冲突: {len(real_conflicts)} 条代码冲突")
    print(f"  需评估模糊: {len(vague)} 条过于模糊的映射")
    print(f"  需清理多值: {len(wasted)} 条数组第二项无效")

    if args.fix:
        fix_plan = {
            "safe_to_delete": [item["key"] for item in self_results["safe_to_delete"]],
            "defensive_self_maps": self_results["defensive_needed"],
            "accuracy_fixes": accuracy,
            "code_conflicts": real_conflicts,
            "vague_mappings": vague,
            "multi_value_waste": wasted,
        }
        fix_path = PROJECT_ROOT / "output" / "temp" / "synonym_fix_plan.json"
        with open(fix_path, "w", encoding="utf-8") as f:
            json.dump(fix_plan, f, ensure_ascii=False, indent=2)
        print(f"\n修复计划已保存到: {fix_path}")


if __name__ == "__main__":
    main()
