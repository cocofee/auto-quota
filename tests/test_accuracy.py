"""
准确率测试：用广联达导出的标准文件验证匹配精度

读取"云计价分部分项清单带定额表.xlsx"，其中包含清单→正确定额的对应关系。
用系统匹配清单，对比匹配结果和正确答案。
"""
import sys
sys.path.insert(0, ".")
import openpyxl
from src.text_parser import parser as text_parser
from src.hybrid_searcher import HybridSearcher
from src.param_validator import ParamValidator

# === 1. 读取标准文件，提取清单→正确定额的对应关系 ===
wb = openpyxl.load_workbook(
    r"C:\Users\Administrator\Desktop\云计价分部分项清单带定额表.xlsx",
    read_only=True, data_only=True,
)
ws = wb[wb.sheetnames[0]]

# 解析文件：提取 (清单, [正确定额]) 对
test_cases = []  # [(清单dict, [定额编号...])]
current_bill = None
current_quotas = []

for row_idx, row in enumerate(ws.iter_rows(values_only=True), 1):
    if row_idx <= 2:
        continue

    a = row[0] if len(row) > 0 else None
    b = row[1] if len(row) > 1 else None
    c = row[2] if len(row) > 2 else None
    d = row[3] if len(row) > 3 else None
    e = row[4] if len(row) > 4 else None
    f = row[5] if len(row) > 5 else None

    # 清单行（有序号）
    if a is not None and str(a).strip().isdigit():
        # 保存上一条
        if current_bill:
            test_cases.append((current_bill, current_quotas))

        current_bill = {
            "code": str(b).strip() if b else "",
            "name": str(c).strip() if c else "",
            "description": str(d).strip() if d else "",
            "unit": str(e).strip() if e else "",
            "quantity": f,
        }
        current_quotas = []

    # 定额行
    elif b and str(b).strip().startswith("C"):
        quota_id = str(b).strip()
        # 去掉"换"后缀和空格
        quota_id = quota_id.split()[0].rstrip("换").strip()
        current_quotas.append(quota_id)

# 最后一条
if current_bill:
    test_cases.append((current_bill, current_quotas))

print(f"共提取 {len(test_cases)} 条测试用例")

# === 2. 用系统匹配，对比结果 ===
searcher = HybridSearcher()
validator = ParamValidator()

lines = []
lines.append(f"{'=' * 80}")
lines.append(f"准确率测试：{len(test_cases)} 条清单")
lines.append(f"{'=' * 80}")

exact_match = 0     # 主定额编号完全匹配
partial_match = 0   # 主定额编号相近（同一小节）
no_match = 0        # 完全不匹配
no_result = 0       # 搜索无结果

for i, (bill, correct_quotas) in enumerate(test_cases, 1):
    name = bill["name"]
    desc = bill["description"]

    # 用新的query构建
    search_query = text_parser.build_quota_query(name, desc)
    full_query = f"{name} {desc}".strip()

    # 搜索
    candidates = searcher.search(search_query, top_k=10)

    if not candidates:
        no_result += 1
        lines.append(f"\n第{i:2d}条 [{name}] query=[{search_query}]")
        lines.append(f"  正确: {correct_quotas}")
        lines.append(f"  结果: 搜索无结果 ✗")
        continue

    # 参数验证
    validated = validator.validate_candidates(full_query, candidates)
    matched = [c for c in validated if c.get("param_match", True)]

    # 取Top1
    if matched:
        top = matched[0]
    else:
        top = validated[0] if validated else None

    if not top:
        no_result += 1
        lines.append(f"\n第{i:2d}条 [{name}] query=[{search_query}]")
        lines.append(f"  正确: {correct_quotas}")
        lines.append(f"  结果: 无匹配候选 ✗")
        continue

    system_quota_id = top.get("quota_id", "")
    correct_main = correct_quotas[0] if correct_quotas else ""

    # 判断匹配程度
    if system_quota_id == correct_main:
        exact_match += 1
        mark = "✓"
    elif system_quota_id.rsplit("-", 1)[0] == correct_main.rsplit("-", 1)[0]:
        # 同一小节（如C10-2-123 vs C10-2-120，都是室内给水钢塑复合管）
        partial_match += 1
        mark = "≈"
    else:
        no_match += 1
        mark = "✗"

    lines.append(f"\n第{i:2d}条 [{name}] query=[{search_query}]")
    lines.append(f"  正确: {correct_main:15s} | 系统: {system_quota_id:15s} {mark}")
    if mark != "✓":
        top_name = top.get("name", "")[:50]
        lines.append(f"  系统定额名: {top_name}")
        lines.append(f"  参数: match={top.get('param_match')}, score={top.get('param_score', 0):.2f}, {top.get('param_detail', '')[:60]}")

# === 3. 统计 ===
total = len(test_cases)
lines.append(f"\n{'=' * 80}")
lines.append(f"测试结果统计（{total}条清单）:")
lines.append(f"  精确匹配 ✓: {exact_match:3d} ({exact_match*100//total}%)")
lines.append(f"  近似匹配 ≈: {partial_match:3d} ({partial_match*100//total}%)")
lines.append(f"  不匹配   ✗: {no_match:3d} ({no_match*100//total}%)")
lines.append(f"  无结果     : {no_result:3d} ({no_result*100//total}%)")
lines.append(f"  准确率(精确+近似): {(exact_match+partial_match)*100//total}%")
lines.append(f"{'=' * 80}")

output = "\n".join(lines)
with open("accuracy_test_result.txt", "w", encoding="utf-8") as f:
    f.write(output)
print(f"\n测试完成！结果写入 accuracy_test_result.txt")
print(f"精确匹配: {exact_match}/{total} ({exact_match*100//total}%)")
print(f"近似匹配: {partial_match}/{total}")
print(f"准确率(精确+近似): {(exact_match+partial_match)*100//total}%")
