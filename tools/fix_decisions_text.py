"""
用batch_test_report.json中的实际清单文本重新生成决策文件
确保存入经验库的文本和实际清单完全一致
"""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.text_parser import normalize_bill_text

# 读取原始决策（包含正确答案）
with open("output/review/agent_decisions_batch8_red.json", "r", encoding="utf-8") as f:
    old_decisions = json.load(f)

# 读取batch_test_report（包含实际清单文本）
with open("output/batch_test_report.json", "r", encoding="utf-8") as f:
    report = json.load(f)

# 用实际文本覆盖决策文件中的描述
# 构建查找表：(name, file) → actual_desc
red_items = report.get("red_items", [])

# 重新生成决策（用实际文本）
new_decisions = []
used_red_indices = set()

for old_d in old_decisions["decisions"]:
    if not old_d.get("correct_quota_ids"):
        # 跳过无答案的
        continue

    old_name = old_d["name"]

    # 从红色项中找匹配的实际描述（可能有多个同名的）
    matched = False
    for i, ri in enumerate(red_items):
        if i in used_red_indices:
            continue
        if ri["bill_name"] == old_name:
            # 检查是否内容大致对应（通过部分关键词匹配）
            old_desc = old_d.get("description", "")
            actual_desc = ri.get("bill_desc", "")

            # 简单匹配：看关键特征是否一致
            should_match = False
            if old_name == "水喷淋钢管":
                # 按DN匹配
                for dn in ["65", "50", "32", "600"]:
                    if dn in old_desc and dn in actual_desc:
                        should_match = True
                        break
            elif old_name == "一般填料套管":
                for spec in ["1700*500", "2100*500", "500*260", "730*420",
                             "1700", "2100", "500*260", "730*420"]:
                    if spec in old_desc and spec in actual_desc:
                        should_match = True
                        break
            elif old_name in ["碳钢通风管道"]:
                for spec in ["≤320", "320", "≤1000", "1000", "≤450", "450"]:
                    if spec in old_desc and spec in actual_desc:
                        should_match = True
                        break
            elif old_name in ["其他管道绝热"]:
                for spec in ["橡塑保温-40", "橡塑保温-36", "绝热材料-9",
                             "闭孔橡塑保温-40", "闭孔橡塑保温-36", "橡塑绝热材料-9"]:
                    if spec in old_desc and spec in actual_desc:
                        should_match = True
                        break
            elif old_name == "配电箱" or "配电箱" in old_name:
                for spec in ["600*500*300", "600*800*200"]:
                    if spec in old_desc and spec in actual_desc:
                        should_match = True
                        break
                if not should_match and old_name in actual_desc:
                    should_match = True
            else:
                # 其他项目直接名称匹配
                should_match = True

            if should_match:
                new_d = old_d.copy()
                new_d["description"] = actual_desc  # 用实际描述！
                new_decisions.append(new_d)
                used_red_indices.add(i)
                matched = True
                break

    if not matched:
        # 没找到对应的红色项，保留原始决策
        new_decisions.append(old_d)

# 输出
output = {
    "reviewer": "claude_code_agent",
    "review_date": "2026-02-17",
    "note": "使用batch_test_report中的实际清单文本（修正版）",
    "total_decisions": len(new_decisions),
    "decisions": new_decisions,
}

output_path = "output/review/agent_decisions_batch8_red_v2.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

# 验证：对比一下存入文本
print(f"决策文件v2: {output_path}")
print(f"决策数: {len(new_decisions)}")
print(f"匹配到实际文本: {len(used_red_indices)} 条")
print()

# 检查几个关键项
for d in new_decisions:
    if d["name"] in ["一般填料套管", "碳钢通风管道", "其他管道绝热"]:
        normalized = normalize_bill_text(d["name"], d["description"])
        print(f"[{d['name']}] → {d['correct_quota_ids']}")
        print(f"  normalized: {normalized[:80]}...")
        print()
