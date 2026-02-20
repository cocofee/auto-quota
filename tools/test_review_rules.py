# -*- coding: utf-8 -*-
"""
审核规则验证脚本 — 从定额库构造测试用例，验证检测规则是否生效

用法：
    python tools/test_review_rules.py --province "北京2024"
    python tools/test_review_rules.py --province "北京2024" --book C4

原理：从定额库抽取真实定额，构造"清单描述X + 故意错配的定额Y"，
      检查审核规则是否能检测出来。
"""

import sys
import os
import sqlite3
import argparse
from pathlib import Path

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import get_quota_db_path, CURRENT_PROVINCE, resolve_province
from src.review_checkers import (
    check_category_mismatch, check_material_mismatch, check_connection_mismatch,
    check_electric_pair, check_sleeve_mismatch, check_pipe_usage,
    extract_description_lines,
)


# ============================================================
# 测试用例定义：每组是 (清单描述, 故意错配的定额名, 期望检测到的规则)
# ============================================================

# 构造测试用例：模拟真实的清单+错误定额配对
TEST_CASES = [
    # === 电气(C4) - category_keywords ===
    {
        "name": "配电箱安装",
        "desc": "照明配电箱 AL-1",
        "wrong_quota": "断路器安装 3P 100A",
        "expect_type": "category_mismatch",
        "label": "配电箱→断路器（类别错误）",
    },
    {
        "name": "桥架安装",
        "desc": "电缆桥架 200×100",
        "wrong_quota": "线槽安装 PVC 100×50",
        "expect_type": "category_mismatch",
        "label": "桥架→线槽（互斥类别）",
    },
    {
        "name": "线槽安装",
        "desc": "PVC线槽 40×25",
        "wrong_quota": "电缆桥架安装 梯式 200×100",
        "expect_type": "category_mismatch",
        "label": "线槽→桥架（互斥类别）",
    },
    {
        "name": "接地极安装",
        "desc": "接地极 L50×5×2500",
        "wrong_quota": "等电位端子箱安装",
        "expect_type": "category_mismatch",
        "label": "接地极→等电位（互斥类别）",
    },

    # === 电气(C4) - electric_pair（配对检测） ===
    {
        "name": "开关安装",
        "desc": "双控开关 一位",
        "wrong_quota": "暗装单控开关 一位",
        "expect_type": "electric_pair_mismatch",
        "label": "双控开关→单控（配对错误）",
    },
    {
        "name": "插座安装",
        "desc": "三相插座 16A",
        "wrong_quota": "暗装单相插座 五孔 10A",
        "expect_type": "electric_pair_mismatch",
        "label": "三相插座→单相（配对错误）",
    },
    {
        "name": "开关安装",
        "desc": "明装 双控开关",
        "wrong_quota": "暗装双控开关 一位",
        "expect_type": "electric_pair_mismatch",
        "label": "明装开关→暗装（配对错误）",
    },
    {
        "name": "配电箱安装",
        "desc": "配电箱 墙上明装 4回路",
        "wrong_quota": "配电箱箱体安装 暗装 半周长1m",
        "expect_type": "electric_pair_mismatch",
        "label": "配电箱明装→暗装（配对错误）",
    },

    # === 管材通用 - 塑料管 vs 铸铁管 ===
    {
        "name": "排水管安装",
        "desc": "塑料管 DN100",
        "wrong_quota": "室外给水球墨铸铁管 DN100",
        "expect_type": "material_mismatch",
        "label": "塑料管→铸铁管（管材错误）",
    },

    # === 消防(C9) - category_keywords ===
    {
        "name": "灭火器配置",
        "desc": "手提式干粉灭火器 4kg",
        "wrong_quota": "灭火装置安装 气体",
        "expect_type": "category_mismatch",
        "label": "灭火器→灭火装置（排除词）",
    },

    # === 消防(C9) - electric_pair ===
    {
        "name": "喷头安装",
        "desc": "闭式喷头 有吊顶 DN15",
        "wrong_quota": "喷头安装 无吊顶 下垂型 DN15",
        "expect_type": "electric_pair_mismatch",
        "label": "有吊顶喷头→无吊顶（配对错误）",
    },
    {
        "name": "灭火器配置",
        "desc": "手提式干粉灭火器 MFZ/ABC4",
        "wrong_quota": "推车式灭火器安装 50kg",
        "expect_type": "electric_pair_mismatch",
        "label": "手提式灭火器→推车式（配对错误）",
    },

    # === 给排水(C10) - material_mismatch ===
    {
        "name": "排水管安装",
        "desc": "柔性铸铁排水管 DN100",
        "wrong_quota": "排水塑料管安装 粘接 DN100",
        "expect_type": "material_mismatch",
        "label": "铸铁管→塑料管（管材错误）",
    },

    # === 给排水(C10) - connection_mismatch ===
    {
        "name": "给水管安装",
        "desc": "钢塑复合管 沟槽连接 DN50",
        "wrong_quota": "钢塑复合管安装 螺纹连接 DN50",
        "expect_type": "connection_mismatch",
        "label": "沟槽连接→螺纹（连接方式错误）",
    },

    # === 机械设备(C1) - category_keywords ===
    {
        "name": "水泵安装",
        "desc": "离心泵 Q=20m3/h",
        "wrong_quota": "冷却塔安装 圆形 200t",
        "expect_type": "category_mismatch",
        "label": "C1: 水泵→冷却塔（类别错误）",
    },
    {
        "name": "冷水机组安装",
        "desc": "螺杆式冷水机组 200RT",
        "wrong_quota": "冷却塔安装 方形 300t",
        "expect_type": "category_mismatch",
        "label": "C1: 冷水机组→冷却塔（互斥）",
    },

    # === C4 补充 - 电缆vs导线 ===
    {
        "name": "电缆敷设",
        "desc": "电力电缆 YJV-3×120",
        "wrong_quota": "塑料绝缘导线穿管 BV-2.5",
        "expect_type": "category_mismatch",
        "label": "C4: 电缆→导线（互斥类别）",
    },
    {
        "name": "变压器安装",
        "desc": "油浸式变压器 630kVA",
        "wrong_quota": "成套配电箱安装 落地式",
        "expect_type": "category_mismatch",
        "label": "C4: 变压器→配电箱（互斥）",
    },

    # === 智能化(C5) - category_keywords ===
    {
        "name": "摄像机安装",
        "desc": "网络高清摄像机 200万像素",
        "wrong_quota": "门禁控制器安装 单门",
        "expect_type": "category_mismatch",
        "label": "C5: 摄像机→门禁（互斥）",
    },
    {
        "name": "交换机安装",
        "desc": "网络交换机 24口",
        "wrong_quota": "网络路由器安装 企业级",
        "expect_type": "category_mismatch",
        "label": "C5: 交换机→路由器（互斥）",
    },
    {
        "name": "光缆敷设",
        "desc": "室内光缆 12芯",
        "wrong_quota": "双绞线穿放 超五类 4对",
        "expect_type": "category_mismatch",
        "label": "C5: 光缆→双绞线（互斥）",
    },

    # === C7 补充 ===
    {
        "name": "新风机组安装",
        "desc": "组合式新风机组 3000m3/h",
        "wrong_quota": "风机盘管安装 卧式暗装",
        "expect_type": "category_mismatch",
        "label": "C7: 新风机组→风机盘管（互斥）",
    },

    # === C9 补充 ===
    {
        "name": "感烟探测器安装",
        "desc": "点型感烟探测器",
        "wrong_quota": "点型探测器安装 感温",
        "expect_type": "category_mismatch",
        "label": "C9: 感烟→感温探测器（配对错误）",
    },
    {
        "name": "防火门监控",
        "desc": "防火门监控模块",
        "wrong_quota": "防火卷帘控制器安装",
        "expect_type": "category_mismatch",
        "label": "C9: 防火门→防火卷帘（互斥）",
    },

    # === C12 刷油防腐 ===
    {
        "name": "管道保温",
        "desc": "管道保温 岩棉管壳 DN100",
        "wrong_quota": "管道防腐 环氧漆 两遍",
        "expect_type": "category_mismatch",
        "label": "C12: 保温→防腐（互斥）",
    },

    # === 通风空调(C7) - category_keywords ===
    {
        "name": "风机盘管安装",
        "desc": "卧式暗装风机盘管 FP-68",
        "wrong_quota": "新风机组安装 组合式",
        "expect_type": "category_mismatch",
        "label": "风机盘管→新风机组（类别错误）",
    },

    # === 不应误报的正确配对（反向测试） ===
    {
        "name": "开关安装",
        "desc": "双控开关 一位",
        "wrong_quota": "暗装双控开关 一位",
        "expect_type": None,  # 不应报错
        "label": "双控→双控（正确匹配，不应误报）",
    },
    {
        "name": "配电箱安装",
        "desc": "照明配电箱 AL-1",
        "wrong_quota": "成套配电箱安装 落地式",
        "expect_type": None,  # 不应报错
        "label": "配电箱→配电箱（正确匹配，不应误报）",
    },

    # ============================================================
    # A册 土建工程
    # ============================================================

    # === A册 - 防水材料互斥 ===
    {
        "name": "屋面防水",
        "desc": "卷材防水 SBS改性沥青 厚4mm",
        "wrong_quota": "涂膜防水 聚氨酯 厚1.5mm",
        "expect_type": "category_mismatch",
        "label": "A: 卷材防水→涂膜防水（防水互斥）",
    },
    {
        "name": "地下室防水",
        "desc": "涂膜防水 聚氨酯 厚2mm",
        "wrong_quota": "卷材防水 SBS 厚4mm",
        "expect_type": "category_mismatch",
        "label": "A: 涂膜防水→卷材防水（防水互斥）",
    },

    # === A册 - 保温材料互斥 ===
    {
        "name": "屋面保温",
        "desc": "挤塑聚苯板 厚100mm 粘贴",
        "wrong_quota": "保温隔热屋面 岩棉板 厚100mm",
        "expect_type": "category_mismatch",
        "label": "A: 挤塑聚苯板→岩棉（保温互斥）",
    },
    {
        "name": "外墙保温",
        "desc": "岩棉板 厚80mm 机械固定",
        "wrong_quota": "保温隔热 挤塑聚苯板 厚80mm",
        "expect_type": "category_mismatch",
        "label": "A: 岩棉板→挤塑聚苯板（保温互斥）",
    },

    # === A册 - 门窗互斥 ===
    {
        "name": "入户门",
        "desc": "木门 套装 单扇",
        "wrong_quota": "铝合金门 单扇",
        "expect_type": "category_mismatch",
        "label": "A: 木门→铝合金门（门互斥）",
    },
    {
        "name": "窗户安装",
        "desc": "铝合金窗 双层中空玻璃",
        "wrong_quota": "塑钢窗 双层中空玻璃",
        "expect_type": "category_mismatch",
        "label": "A: 铝合金窗→塑钢窗（窗互斥）",
    },

    # === A册 - 涂料互斥 ===
    {
        "name": "内墙涂料",
        "desc": "乳胶漆 两遍",
        "wrong_quota": "真石漆 喷涂 两遍",
        "expect_type": "category_mismatch",
        "label": "A: 乳胶漆→真石漆（涂料互斥）",
    },
    {
        "name": "外墙涂料",
        "desc": "氟碳漆 面漆 两遍",
        "wrong_quota": "乳胶漆 面漆 两遍",
        "expect_type": "category_mismatch",
        "label": "A: 氟碳漆→乳胶漆（涂料互斥）",
    },

    # === A册 - 地面面层互斥 ===
    {
        "name": "大堂地面",
        "desc": "大理石 地面面层 600×600",
        "wrong_quota": "面层 花岗岩板 600×600",
        "expect_type": "category_mismatch",
        "label": "A: 大理石→花岗岩（地面互斥）",
    },
    {
        "name": "卧室地面",
        "desc": "木地板 实木 厚18mm",
        "wrong_quota": "地毯 满铺 化纤",
        "expect_type": "category_mismatch",
        "label": "A: 木地板→地毯（地面互斥）",
    },

    # === A册 - 砌筑互斥 ===
    {
        "name": "填充墙",
        "desc": "砌块 混凝土空心砌块 200厚",
        "wrong_quota": "砖砌体 240厚",
        "expect_type": "category_mismatch",
        "label": "A: 砌块→砖砌体（砌筑互斥）",
    },

    # === A册 - 幕墙互斥 ===
    {
        "name": "外墙幕墙",
        "desc": "玻璃幕墙 隐框 中空玻璃",
        "wrong_quota": "石材幕墙 干挂 花岗岩",
        "expect_type": "category_mismatch",
        "label": "A: 玻璃幕墙→石材幕墙（幕墙互斥）",
    },

    # === A册 - 防水配对(electric_pair) ===
    {
        "name": "屋面防水",
        "desc": "防水 卷材 SBS改性沥青 4mm",
        "wrong_quota": "涂膜防水 聚氨酯 1.5mm",
        "expect_type": "electric_pair_mismatch",
        "label": "A: 防水中卷材→涂膜（配对错误）",
    },

    # === A册 - 正确配对反向测试 ===
    {
        "name": "屋面保温",
        "desc": "挤塑聚苯板 厚100mm 粘贴",
        "wrong_quota": "保温隔热屋面 挤塑聚苯板100mm厚 粘贴",
        "expect_type": None,  # 不应报错
        "label": "A: 挤塑聚苯板→挤塑聚苯板（正确，不应误报）",
    },
    {
        "name": "内墙涂料",
        "desc": "乳胶漆 两遍",
        "wrong_quota": "内墙面 乳胶漆 两遍",
        "expect_type": None,  # 不应报错
        "label": "A: 乳胶漆→乳胶漆（正确，不应误报）",
    },

    # ============================================================
    # D册 市政工程
    # ============================================================

    # === D册 - 井类互斥 ===
    {
        "name": "排水检查井",
        "desc": "检查井 砖砌 圆形 D1000",
        "wrong_quota": "雨水口 砖砌 300×500",
        "expect_type": "category_mismatch",
        "label": "D: 检查井→雨水口（井类互斥）",
    },
    {
        "name": "路面雨水收集",
        "desc": "雨水口 铸铁箅子",
        "wrong_quota": "检查井 砖砌 圆形 D700",
        "expect_type": "category_mismatch",
        "label": "D: 雨水口→检查井（井类互斥）",
    },

    # === D册 - 道路面层互斥 ===
    {
        "name": "道路面层",
        "desc": "沥青混凝土 面层 厚5cm",
        "wrong_quota": "水泥混凝土路面 厚20cm",
        "expect_type": "category_mismatch",
        "label": "D: 沥青混凝土→水泥混凝土路面（道路互斥）",
    },

    # === D册 - 管材互斥 ===
    {
        "name": "给水管安装",
        "desc": "球墨铸铁管 DN300",
        "wrong_quota": "钢管安装 焊接 DN300",
        "expect_type": "category_mismatch",
        "label": "D: 球墨铸铁管→钢管（管材互斥）",
    },

    # === D册 - 正确配对反向测试 ===
    {
        "name": "排水检查井",
        "desc": "检查井 砖砌 圆形 D1000",
        "wrong_quota": "检查井 砖砌 直径1000mm",
        "expect_type": None,  # 不应报错
        "label": "D: 检查井→检查井（正确，不应误报）",
    },

    # ============================================================
    # E册 园林绿化 / F册 构筑物
    # ============================================================
    {
        "name": "乔木种植",
        "desc": "乔木 香樟 胸径15cm",
        "wrong_quota": "灌木种植 丛高1.5m",
        "expect_type": "category_mismatch",
        "label": "E: 乔木→灌木（园林互斥）",
    },
    {
        "name": "化粪池",
        "desc": "化粪池 砖砌 12m3",
        "wrong_quota": "隔油池 砖砌 6m3",
        "expect_type": "category_mismatch",
        "label": "F: 化粪池→隔油池（构筑物互斥）",
    },
]


def run_checks(bill_item, quota_name, desc_lines):
    """对一个测试用例运行所有检测规则，返回第一个命中的错误"""
    error = (
        check_category_mismatch(bill_item, quota_name, desc_lines)
        or check_sleeve_mismatch(bill_item, quota_name, desc_lines)
        or check_material_mismatch(bill_item, quota_name, desc_lines)
        or check_connection_mismatch(bill_item, quota_name, desc_lines)
        or check_pipe_usage(bill_item, quota_name, desc_lines)
        or check_electric_pair(bill_item, quota_name, desc_lines)
    )
    return error


def run_tests():
    """运行所有测试用例"""
    passed = 0
    failed = 0
    total = len(TEST_CASES)

    print("=" * 60)
    print("审核规则验证测试")
    print("=" * 60)

    for i, tc in enumerate(TEST_CASES, 1):
        bill_item = {"name": tc["name"], "description": tc["desc"]}
        desc_lines = extract_description_lines(tc["desc"])
        quota_name = tc["wrong_quota"]
        expect = tc["expect_type"]

        error = run_checks(bill_item, quota_name, desc_lines)
        actual_type = error["type"] if error else None

        if expect is None:
            # 反向测试：不应报错
            ok = actual_type is None
        else:
            ok = actual_type == expect

        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1

        # 输出结果
        mark = "+" if ok else "X"
        print(f"  [{mark}] {tc['label']}")
        if not ok:
            print(f"       期望: {expect}")
            print(f"       实际: {actual_type}")
            if error:
                print(f"       原因: {error.get('reason', '')}")

    print()
    print("-" * 60)
    print(f"结果: {passed}/{total} 通过, {failed} 失败")
    print("-" * 60)

    return failed == 0


def run_db_sample_test(province, book_filter=None):
    """从定额库抽取真实定额，构造错配对进行测试

    抽取同一类别下的不同定额，交叉配对检测是否能发现问题。
    """
    db_path = get_quota_db_path(province)
    if not os.path.exists(db_path):
        print(f"定额库不存在: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # 定义互斥配对测试组（从同一类中抽两条不同的定额交叉检测）
    pair_groups = [
        # (搜索词A, 搜索词B, 期望检测到错误)
        # 安装工程
        ("单控开关", "双控开关", True),
        ("明装", "暗装", True),
        ("桥架", "线槽", True),
        ("塑料管", "铸铁管", True),
        # A册 土建
        ("卷材防水", "涂膜防水", True),
        ("挤塑聚苯板", "岩棉", True),
        ("木门", "铝合金门", True),
        ("乳胶漆", "真石漆", True),
        # D册 市政
        ("检查井", "雨水口", True),
    ]

    if book_filter:
        book_cond = f"AND quota_id LIKE '{book_filter}%'"
    else:
        book_cond = ""

    print()
    print("=" * 60)
    print(f"定额库交叉配对测试 (省份: {province})")
    print("=" * 60)

    tested = 0
    detected = 0

    for kw_a, kw_b, expect_error in pair_groups:
        # 从库中各取一条含关键词的定额
        sql_a = f"SELECT quota_id, name FROM quotas WHERE name LIKE ? {book_cond} LIMIT 1"
        sql_b = f"SELECT quota_id, name FROM quotas WHERE name LIKE ? {book_cond} LIMIT 1"

        cursor.execute(sql_a, (f"%{kw_a}%",))
        row_a = cursor.fetchone()
        cursor.execute(sql_b, (f"%{kw_b}%",))
        row_b = cursor.fetchone()

        if not row_a or not row_b:
            print(f"  [跳过] {kw_a} vs {kw_b} — 库中未找到对应定额")
            continue

        # 用A的名称做清单描述，B的定额做匹配结果（故意错配）
        bill_item = {"name": row_a[1], "description": row_a[1]}
        desc_lines = extract_description_lines(row_a[1])
        quota_name = row_b[1]

        error = run_checks(bill_item, quota_name, desc_lines)
        tested += 1

        if error and expect_error:
            detected += 1
            print(f"  [+] {kw_a} vs {kw_b}: 检测到 {error['type']}")
            print(f"       清单: {row_a[1][:40]}")
            print(f"       定额: {row_b[1][:40]}")
        elif not error and not expect_error:
            detected += 1
            print(f"  [+] {kw_a} vs {kw_b}: 正确放行")
        else:
            print(f"  [X] {kw_a} vs {kw_b}: {'未检测到' if expect_error else '误报'}")
            print(f"       清单: {row_a[1][:40]}")
            print(f"       定额: {row_b[1][:40]}")

    conn.close()

    print()
    print(f"定额库测试: {detected}/{tested} 检测正确")
    print()


def main():
    parser = argparse.ArgumentParser(description="审核规则验证测试")
    parser.add_argument("--province", default=None,
                        help=f"省份（默认 {CURRENT_PROVINCE}）")
    parser.add_argument("--book", default=None,
                        help="按册号过滤定额库测试（如 C4, C9）")
    args = parser.parse_args()

    # 第一部分：构造用例测试（不需要数据库）
    all_pass = run_tests()

    # 第二部分：定额库交叉配对测试（需要数据库）
    province = resolve_province(args.province) if args.province else CURRENT_PROVINCE
    run_db_sample_test(province, args.book)

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
