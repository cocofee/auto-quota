"""
编清单评测工具 — 测试清单编码匹配器的准确率。

测试内容：
  1. 固定用例测试：手工标注的清单名称→9位国标编码，逐条对比
  2. 专业路由测试：从现有benchmark试卷提取清单名称，检查附录路由是否正确
  3. 12位编码编序测试：同名不同规格的清单项，序号是否递增

用法：
  python tools/run_compile_benchmark.py              # 跑全部测试
  python tools/run_compile_benchmark.py --detail      # 打印每条详情
  python tools/run_compile_benchmark.py --only code   # 只跑编码匹配测试
  python tools/run_compile_benchmark.py --only route  # 只跑专业路由测试
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.bill_code_matcher import match_bill_code, match_bill_codes, _route_appendix, _sheet_name_to_appendix


# ============================================================
# 第一部分：固定用例（手工标注，清单名称 → 9位国标编码）
# ============================================================

# 每条用例：(名称, 描述, 期望9位编码, 期望附录, 说明)
FIXED_CASES = [
    # K 给排水
    ("镀锌钢管", "DN100 螺纹连接 室内给水", "031001002", "K", "给排水管道"),
    ("PPR管", "DN25 热熔连接 给水", None, "K", "塑料管→同义词→塑料管"),
    ("铸铁排水管", "DN100 承插连接", None, "K", "排水管道"),
    ("洗脸盆", "陶瓷 台上盆", None, "K", "卫生器具"),
    ("坐便器", "陶瓷 虹吸式", None, "K", "卫生器具→同义词→大便器"),
    ("散热器", "钢制柱型 六柱", None, "K", "采暖设备"),

    # J 消防
    ("消火栓钢管", "DN100 沟槽连接 室内消防", "030901002", "J", "消防管道，不是给排水"),
    ("感烟探测器", "智能型 吸顶安装", None, "J", "火灾报警设备"),
    ("室内消火栓", "SN65 减压稳压型", None, "J", "消火栓箱"),
    ("灭火器", "手提式干粉 4kg", None, "J", "灭火器配置"),

    # D 电气
    ("配电箱", "XL-21 落地安装 12回路", None, "D", "成套配电箱"),
    ("电力电缆", "YJV-3×120+2×70 电缆沟敷设", None, "D", "电缆"),
    ("配管", "SC20 暗敷", None, "D", "电线导管"),
    ("灯具", "LED筒灯 嵌入式", None, "D", "照明灯具"),
    ("桥架", "镀锌槽式 200×100", None, "D", "电缆桥架"),
    ("接地极", "镀锌角钢 L50×5×2500", None, "D", "防雷接地"),

    # G 通风空调
    ("通风管道", "镀锌钢板 δ=0.75mm 矩形", None, "G", "通风管道"),
    ("风机盘管", "卧式暗装 制冷量3.5kW", None, "G", "空调末端设备"),
    ("散流器", "方形 600×600 铝合金", None, "G", "风口"),

    # H 工业管道
    ("低压管道", "碳钢 DN200 焊接", None, "H", "工业管道"),

    # E 建筑智能化
    ("综合布线", "六类非屏蔽网线 信息点", None, "E", "弱电"),
    ("监控摄像头", "网络高清 球形 室外", None, "E", "安防监控"),

    # M 刷油防腐
    ("管道保温", "橡塑保温 δ=25mm", None, "M", "绝热保温"),
]


def run_code_match_test(detail: bool = False) -> dict:
    """跑固定用例编码匹配测试。"""
    total = len(FIXED_CASES)
    code_correct = 0  # 9位编码完全匹配
    route_correct = 0  # 附录路由正确
    matched = 0  # 有匹配结果
    errors = []  # 错误记录

    for name, desc, expected_code, expected_appendix, note in FIXED_CASES:
        result = match_bill_code(name, desc)

        got_code = result["code"] if result else None
        got_appendix = result["appendix"] if result else None
        got_score = result["match_score"] if result else 0

        # 检查附录路由
        route_ok = (got_appendix == expected_appendix) if result else False
        if route_ok:
            route_correct += 1

        # 检查编码（只检查有期望编码的用例）
        code_ok = False
        if expected_code and result:
            code_ok = (got_code == expected_code)
            if code_ok:
                code_correct += 1

        if result:
            matched += 1

        # 记录错误
        if not route_ok or (expected_code and not code_ok):
            errors.append({
                "name": name, "desc": desc, "note": note,
                "expected_code": expected_code, "expected_appendix": expected_appendix,
                "got_code": got_code, "got_appendix": got_appendix,
                "got_score": got_score,
                "route_ok": route_ok, "code_ok": code_ok,
            })

        if detail:
            # 状态标记
            status = "[OK]" if (route_ok and (not expected_code or code_ok)) else "[NG]"
            code_str = f"{got_code}" if got_code else "未匹配"
            print(f"  {status} {name:12s} → {code_str:12s} "
                  f"(附录:{got_appendix or '?'}/{expected_appendix}) "
                  f"分:{got_score:5.1f}  {note}")

    # 有期望编码的用例数
    code_testable = sum(1 for _, _, c, _, _ in FIXED_CASES if c)

    result = {
        "total": total,
        "matched": matched,
        "route_correct": route_correct,
        "route_accuracy": round(route_correct / total * 100, 1),
        "code_testable": code_testable,
        "code_correct": code_correct,
        "code_accuracy": round(code_correct / max(code_testable, 1) * 100, 1),
        "errors": errors,
    }
    return result


# ============================================================
# 第二部分：专业路由测试（从现有benchmark试卷提取）
# ============================================================

# 定额册号（C4/C9/C10...）→ 对应的清单附录字母（安装工程专用）
QUOTA_TO_APPENDIX = {
    "C1": "A",   # 机械设备
    "C2": "B",   # 热力设备
    "C3": "C",   # 静置设备
    "C4": "D",   # 电气
    "C5": "E",   # 建筑智能化
    "C6": "F",   # 仪表
    "C7": "G",   # 通风空调
    "C8": "H",   # 工业管道
    "C9": "J",   # 消防
    "C10": "K",  # 给排水
    "C11": "L",  # 通信
    "C12": "M",  # 刷油防腐
    "C13": "N",  # 其他
}


def _detect_paper_type(data: dict) -> str:
    """判断试卷是安装还是非安装。

    看定额编号：C开头的是安装（如C4-3-12），纯数字开头是非安装（如1-8-123）。
    非安装试卷的specialty字段（C1/C2...）只代表章节号，不代表安装册号。
    """
    items = data.get("items", [])
    c_prefix = 0
    non_c = 0
    for item in items[:50]:  # 看前50条够了
        qids = item.get("quota_ids", [])
        if qids and qids[0].startswith("C"):
            c_prefix += 1
        elif qids:
            non_c += 1
    return "install" if c_prefix > non_c else "non_install"


def _get_expected_major(paper_name: str) -> str:
    """从试卷名称推断期望的专业大类编码。

    例如:
      "宁夏房屋建筑装饰工程计价定额(2019)" → "01"
      "浙江省市政工程预算定额(2018)" → "04"
      "江西省园林绿化工程消耗量定额" → "05"
      "广东省通用安装工程综合定额(2018)" → "03"
    """
    if "园林" in paper_name or "绿化" in paper_name:
        return "05"
    if "市政" in paper_name:
        return "04"
    if "房屋建筑" in paper_name or "装饰" in paper_name:
        return "01"
    if "安装" in paper_name:
        return "03"
    if "仿古" in paper_name:
        return "09"
    if "修缮" in paper_name:
        return "07"
    return "03"  # 默认安装


def run_route_test(detail: bool = False) -> dict:
    """从benchmark试卷提取清单名称，测试专业路由准确率。

    安装试卷：比较附录字母（A-N），和之前一样。
    非安装试卷：比较专业大类编码（01/04/05），不比较章节细分。
    """
    papers_dir = PROJECT_ROOT / "tests" / "benchmark_papers"
    if not papers_dir.exists():
        return {"total": 0, "correct": 0, "accuracy": 0, "note": "试卷目录不存在"}

    total = 0
    correct = 0       # 无hint路由正确
    correct_hint = 0  # 有hint路由正确（安装用appendix hint，非安装用major hint）
    errors = []
    # 分类统计
    install_total = install_correct = install_hint = 0
    noninst_total = noninst_correct = noninst_hint = 0

    for fpath in sorted(papers_dir.glob("*.json")):
        if fpath.name.startswith("_"):
            continue
        # 跳过脏数据试卷（名称不规范，路由测试无意义）
        if "脏数据" in fpath.name:
            continue

        data = json.loads(fpath.read_text(encoding="utf-8"))
        items = data.get("items", [])
        paper_type = _detect_paper_type(data)
        paper_name = data.get("province", fpath.stem)
        expected_major = _get_expected_major(fpath.stem)

        # 用expected_major判断是否安装（比用code格式更准确）
        # 宁夏/浙江/江西等省安装试卷的定额编号不带C前缀，但确实是安装工程
        is_install_paper = (expected_major == "03")

        for item in items:
            bill_name = item.get("bill_name", "").strip()
            specialty = item.get("specialty", "").strip()  # 如 "C4"
            bill_text = item.get("bill_text", "")

            if not bill_name or not specialty:
                continue
            if not specialty.startswith("C"):
                continue

            # 用bill_text作为description（去掉名称部分）
            desc = bill_text.replace(bill_name, "").strip()

            # 路由
            got = _route_appendix(bill_name, desc)

            total += 1

            if is_install_paper:
                # 安装试卷：比较附录字母
                expected_appendix = QUOTA_TO_APPENDIX.get(specialty, "")
                if not expected_appendix:
                    continue
                is_correct = (got == expected_appendix)
                install_total += 1
                if is_correct:
                    install_correct += 1

                # 有hint路由
                result_hint = match_bill_code(bill_name, desc, hint_appendix=expected_appendix)
                got_hint = result_hint["appendix"] if result_hint else ""
                if got_hint == expected_appendix:
                    correct_hint += 1
                    install_hint += 1

                if is_correct:
                    correct += 1
                else:
                    errors.append({
                        "bill_name": bill_name,
                        "expected": f"{specialty}→{expected_appendix}",
                        "got": got or "(空)",
                        "type": "安装",
                    })
            else:
                # 非安装试卷：比较专业大类
                is_correct = (got == expected_major)
                noninst_total += 1
                if is_correct:
                    noninst_correct += 1

                # 有major hint路由
                got_with_hint = _route_appendix(bill_name, desc,
                                                hint_major=expected_major)
                is_hint_correct = (got_with_hint == expected_major)
                if is_hint_correct:
                    correct_hint += 1
                    noninst_hint += 1

                if is_correct:
                    correct += 1
                else:
                    errors.append({
                        "bill_name": bill_name,
                        "expected": f"大类{expected_major}",
                        "got": got or "(空)",
                        "type": paper_name,
                    })

    # 打印错误样本（最多20条）
    if detail and errors:
        print(f"\n  路由错误样本（共{len(errors)}条，显示前20条）：")
        for e in errors[:20]:
            print(f"    [NG] {e['bill_name']:20s}  期望:{e['expected']}  实际:{e['got']}  [{e['type']}]")

    result = {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / max(total, 1) * 100, 1),
        "correct_hint": correct_hint,
        "accuracy_hint": round(correct_hint / max(total, 1) * 100, 1),
        "error_count": len(errors),
        "error_samples": errors[:20],
        "install_total": install_total,
        "install_correct": install_correct,
        "install_accuracy": round(install_correct / max(install_total, 1) * 100, 1),
        "install_hint": install_hint,
        "install_hint_accuracy": round(install_hint / max(install_total, 1) * 100, 1),
        "noninst_total": noninst_total,
        "noninst_correct": noninst_correct,
        "noninst_accuracy": round(noninst_correct / max(noninst_total, 1) * 100, 1),
        "noninst_hint": noninst_hint,
        "noninst_hint_accuracy": round(noninst_hint / max(noninst_total, 1) * 100, 1),
    }
    return result


def run_neighbor_test(detail: bool = False) -> dict:
    """测试邻居投票对路由准确率的提升。

    思路：按专业分组（模拟真实Excel每个sheet一个专业），
    每组用match_bill_codes做批量匹配（含邻居投票），对比逐条匹配结果。
    """
    from collections import defaultdict

    papers_dir = PROJECT_ROOT / "tests" / "benchmark_papers"
    if not papers_dir.exists():
        return {"total": 0, "correct": 0, "accuracy": 0}

    total = 0
    correct_single = 0  # 逐条匹配正确数（无邻居）
    correct_batch = 0   # 批量匹配正确数（有邻居投票）
    corrected_items = []  # 被邻居投票修正的项目

    for fpath in sorted(papers_dir.glob("*.json")):
        if fpath.name.startswith("_"):
            continue
        if "脏数据" in fpath.name:
            continue

        data = json.loads(fpath.read_text(encoding="utf-8"))
        items = data.get("items", [])
        paper_name = data.get("province", fpath.stem)
        expected_major = _get_expected_major(fpath.stem)
        is_install_paper = (expected_major == "03")

        if not is_install_paper:
            continue  # 非安装试卷邻居投票意义不大（都是同大类）

        # 按专业分组（模拟真实Excel每个sheet一个专业）
        # 真实Excel里，给排水项目在"给排水"sheet，电气项目在"电气"sheet
        groups = defaultdict(list)  # specialty -> [(bill_item, expected_appendix)]
        for item in items:
            bill_name = item.get("bill_name", "").strip()
            specialty = item.get("specialty", "").strip()
            bill_text = item.get("bill_text", "")

            if not bill_name or not specialty or not specialty.startswith("C"):
                continue

            expected_app = QUOTA_TO_APPENDIX.get(specialty, "")
            if not expected_app:
                continue

            desc = bill_text.replace(bill_name, "").strip()
            groups[specialty].append(({
                "name": bill_name,
                "description": desc,
            }, expected_app))

        # 逐组处理
        for specialty, group_items in groups.items():
            if len(group_items) < 2:
                # 单条没有邻居效果
                for bi, exp_app in group_items:
                    result = match_bill_code(bi["name"], bi["description"])
                    total += 1
                    if result and result.get("appendix") == exp_app:
                        correct_single += 1
                        correct_batch += 1
                continue

            # 逐条匹配（无邻居上下文）
            for bi, exp_app in group_items:
                result = match_bill_code(bi["name"], bi["description"])
                total += 1
                if result and result.get("appendix") == exp_app:
                    correct_single += 1

            # 批量匹配（有邻居投票）— 同专业的项目共享section
            batch_items = []
            expected_labels = []
            for bi, exp_app in group_items:
                batch_items.append({
                    "name": bi["name"],
                    "description": bi["description"],
                    # 同专业共享section（模拟同一sheet内的分部）
                    "section": f"{paper_name}_{specialty}",
                    "sheet_name": "",
                })
                expected_labels.append(exp_app)

            match_bill_codes(batch_items)

            for bi, exp_app in zip(batch_items, expected_labels):
                bm = bi.get("bill_match")
                got_app = bm.get("appendix", "") if bm else ""
                if got_app == exp_app:
                    correct_batch += 1

                # 记录被邻居投票修正的项目
                if bm and "_neighbor" in bm.get("match_method", ""):
                    corrected_items.append({
                        "name": bi["name"],
                        "paper": paper_name[:10],
                        "got": got_app,
                        "expected": exp_app,
                        "ok": got_app == exp_app,
                    })

    if detail and corrected_items:
        print(f"\n  邻居投票修正了{len(corrected_items)}条:")
        for c in corrected_items[:20]:
            status = "[OK]" if c["ok"] else "[NG]"
            print(f"    {status} {c['name']:15s} → {c['got']}  "
                  f"期望:{c['expected']}  [{c['paper']}]")

    result = {
        "total": total,
        "correct_single": correct_single,
        "accuracy_single": round(correct_single / max(total, 1) * 100, 1),
        "correct_batch": correct_batch,
        "accuracy_batch": round(correct_batch / max(total, 1) * 100, 1),
        "corrected": len(corrected_items),
        "corrected_ok": sum(1 for c in corrected_items if c["ok"]),
        "corrected_wrong": sum(1 for c in corrected_items if not c["ok"]),
    }
    return result


# ============================================================
# 第三部分：12位编码编序测试
# ============================================================

def run_seq_test(detail: bool = False) -> dict:
    """测试同一9位编码下多条清单项的12位编码序号是否递增。"""
    # 模拟一批清单：3条同名镀锌钢管 + 2条配电箱 + 1条电缆
    test_items = [
        {"name": "镀锌钢管", "description": "DN100 螺纹连接 给水"},
        {"name": "镀锌钢管", "description": "DN50 螺纹连接 给水"},
        {"name": "镀锌钢管", "description": "DN25 螺纹连接 给水"},
        {"name": "配电箱", "description": "XL-21 12回路 落地"},
        {"name": "配电箱", "description": "PZ30 6回路 明装"},
        {"name": "电力电缆", "description": "YJV-3×120 电缆沟"},
    ]

    match_bill_codes(test_items)

    passed = 0
    failed = 0
    results = []

    for item in test_items:
        bm = item.get("bill_match", {})
        code9 = bm.get("code", "")
        code12 = bm.get("code_12", "")
        ok = len(code12) == 12 and code12[:9] == code9

        if ok:
            passed += 1
        else:
            failed += 1

        results.append({
            "name": item["name"],
            "code_9": code9,
            "code_12": code12,
            "ok": ok,
        })

        if detail:
            status = "[OK]" if ok else "[NG]"
            print(f"  {status} {item['name']:12s} → 9位:{code9}  12位:{code12}")

    # 检查序号递增
    # 镀锌钢管应该是 xxx001, xxx002, xxx003
    pipe_codes = [r["code_12"] for r in results if r["name"] == "镀锌钢管"]
    seq_ok = True
    if len(pipe_codes) == 3:
        seqs = [int(c[-3:]) for c in pipe_codes if len(c) == 12]
        if seqs != [1, 2, 3]:
            seq_ok = False
            failed += 1
        else:
            passed += 1
    if detail:
        status = "[OK]" if seq_ok else "[NG]"
        print(f"  {status} 镀锌钢管序号递增: {[c[-3:] for c in pipe_codes]}")

    return {
        "total_items": len(test_items),
        "passed": passed,
        "failed": failed,
        "seq_ok": seq_ok,
    }


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="编清单评测工具")
    parser.add_argument("--detail", action="store_true", help="打印每条详情")
    parser.add_argument("--only", choices=["code", "route", "seq", "neighbor"],
                        help="只跑指定测试")
    args = parser.parse_args()

    print("=" * 60)
    print("编清单评测（清单编码匹配器）")
    print("=" * 60)

    results = {}

    # 测试1：固定用例编码匹配
    if not args.only or args.only == "code":
        print("\n[测试1] 固定用例编码匹配")
        print("-" * 40)
        r = run_code_match_test(detail=args.detail)
        results["code_match"] = r
        print(f"\n  匹配率: {r['matched']}/{r['total']} ({round(r['matched']/max(r['total'],1)*100,1)}%)")
        print(f"  路由准确率: {r['route_correct']}/{r['total']} ({r['route_accuracy']}%)")
        print(f"  编码准确率: {r['code_correct']}/{r['code_testable']} ({r['code_accuracy']}%)")

    # 测试2：专业路由（从benchmark试卷）
    if not args.only or args.only == "route":
        print(f"\n[测试2] 专业路由（benchmark试卷）")
        print("-" * 40)
        r = run_route_test(detail=args.detail)
        results["route"] = r
        print(f"\n  无hint路由: {r['correct']}/{r['total']} ({r['accuracy']}%)")
        print(f"  有hint路由: {r['correct_hint']}/{r['total']} ({r['accuracy_hint']}%)")
        if r["error_count"] > 0:
            print(f"  无hint错误: {r['error_count']}条")

    # 测试3：邻居投票效果
    if not args.only or args.only == "neighbor":
        print(f"\n[测试3] 邻居投票（batch匹配 vs 单条匹配）")
        print("-" * 40)
        r = run_neighbor_test(detail=args.detail)
        results["neighbor"] = r
        print(f"\n  安装试卷（{r['total']}条）:")
        print(f"    单条匹配: {r['correct_single']}/{r['total']} ({r['accuracy_single']}%)")
        print(f"    批量匹配: {r['correct_batch']}/{r['total']} ({r['accuracy_batch']}%)")
        delta = r['accuracy_batch'] - r['accuracy_single']
        sign = "+" if delta >= 0 else ""
        print(f"    邻居投票: 修正{r['corrected']}条 "
              f"(正确{r['corrected_ok']}+错误{r['corrected_wrong']})")
        print(f"    净效果: {sign}{delta:.1f}%")

    # 测试4：12位编码编序
    # 测试4：12位编码编序
    if not args.only or args.only == "seq":
        print(f"\n[测试4] 12位编码编序")
        print("-" * 40)
        r = run_seq_test(detail=args.detail)
        results["seq"] = r
        seq_status = "通过" if r["failed"] == 0 else f"{r['failed']}项失败"
        print(f"\n  结果: {seq_status}")

    # 汇总
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    if "code_match" in results:
        r = results["code_match"]
        print(f"  编码匹配: 路由{r['route_accuracy']}% | 编码{r['code_accuracy']}%")
    if "route" in results:
        r = results["route"]
        print(f"  试卷路由: 无hint {r['accuracy']}% | 有hint {r['accuracy_hint']}%")
        if r.get("install_total"):
            print(f"    安装({r['install_total']}条): 无hint {r['install_accuracy']}% | 有hint {r['install_hint_accuracy']}%")
        if r.get("noninst_total"):
            print(f"    非安装({r['noninst_total']}条): 无hint {r['noninst_accuracy']}% | 有hint {r['noninst_hint_accuracy']}%")
    if "seq" in results:
        r = results["seq"]
        print(f"  编序测试: {'通过' if r['failed']==0 else '失败'}")
    if "neighbor" in results:
        r = results["neighbor"]
        delta = r['accuracy_batch'] - r['accuracy_single']
        sign = "+" if delta >= 0 else ""
        print(f"  邻居投票: 单条{r['accuracy_single']}% → 批量{r['accuracy_batch']}% ({sign}{delta:.1f}%)")

    print()


if __name__ == "__main__":
    main()
