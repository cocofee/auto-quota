"""
预算数据导入工具
功能：
1. 读取做好的预算Excel文件（广联达/造价Home等导出，清单+定额交替排列格式）
2. 解析清单→定额对应关系
3. 一次导入，自动往两个库都存：
   - 经验库：带定额编号，同省份项目可直接匹配
   - 通用知识库：定额名称模式，跨省份通用

使用方法：
    # 导入一个做好的预算文件（默认北京）
    python tools/import_reference.py 预算文件.xlsx

    # 指定省份和项目名
    python tools/import_reference.py 预算文件.xlsx --province 北京 --project 丰台安置房

    # 查看解析结果但不导入（调试用）
    python tools/import_reference.py 预算文件.xlsx --dry-run

Excel格式说明（广联达云计价导出格式）：
- 清单行：有序号，编码为12位数字，有项目特征描述
- 定额行：无序号，编码为 C4-4-31 或 C10-1-5 格式，无项目特征描述
- 章节标题行：只有"项目名称"列有值，其余为空
"""

import argparse
import re
import sys
from pathlib import Path

# 把项目根目录加入路径，这样才能导入 src/ 下的模块
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
from src.text_parser import normalize_bill_text
import config


def read_excel_pairs(excel_path: str) -> list[dict]:
    """
    读取造价Home导出的Excel，解析清单→定额对应关系

    返回:
        [
            {
                "bill_name": "钢筋混凝土蓄水池",
                "bill_desc": "项目特征描述...",
                "bill_code": "010507006001",
                "bill_unit": "m³",
                "bill_pattern": "钢筋混凝土蓄水池 项目特征...",  # 用于通用知识库
                "quotas": [
                    {"code": "5-325", "name": "混凝土蓄水池 C30"},
                    {"code": "5-90",  "name": "模板安装"},
                ]
            },
            ...
        ]
    """
    import openpyxl

    wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    try:
        # 遍历所有Sheet（造价Home导出的Excel可能有多个工作簿）
        pairs = []
        sheet_names = wb.sheetnames
        logger.info(f"Excel包含 {len(sheet_names)} 个工作簿: {sheet_names[:10]}{'...' if len(sheet_names) > 10 else ''}")

        for sheet_name in sheet_names:
            ws = wb[sheet_name]
            current_bill = None  # 当前正在处理的清单项
            current_section = ""  # 当前分部标题（如"给排水工程"）
            sheet_pairs = 0  # 当前Sheet解析出的清单数

            for row in ws.iter_rows(min_row=1, values_only=True):
                # 跳过空行
                if not row or all(cell is None for cell in row):
                    continue

                # 确保至少有3列
                cells = list(row) + [None] * (7 - len(row)) if len(row) < 7 else list(row)

                col_a = str(cells[0] or "").strip()  # 序号
                col_b = str(cells[1] or "").strip()  # 项目编码 / 定额编号
                col_c = str(cells[2] or "").strip()  # 项目名称 / 定额名称
                col_d = str(cells[3] or "").strip()  # 项目特征描述
                col_e = str(cells[4] or "").strip()  # 计量单位

                # 跳过表头行（"序号"、"项目编码"等文字）
                if col_a in ("序号", "") and col_b in ("项目编码", "编码", ""):
                    if col_c in ("项目名称", "名称", ""):
                        continue

                # 判断行类型
                row_type = _classify_row(col_a, col_b, col_c, col_d)

                if row_type == "bill":
                    # 清单行：保存之前的清单（如果有），开始新的清单
                    if current_bill and current_bill["quotas"]:
                        pairs.append(current_bill)
                        sheet_pairs += 1

                    # 构建清单模式文本（使用共享的规范化函数，确保和匹配时格式一致）
                    bill_pattern = normalize_bill_text(col_c, col_d)

                    current_bill = {
                        "bill_name": col_c,
                        "bill_desc": col_d,
                        "bill_code": col_b,
                        "bill_unit": col_e,
                        "bill_pattern": bill_pattern,
                        "section": current_section,  # 继承当前分部标题
                        "quotas": [],
                    }

                elif row_type == "quota" and current_bill is not None:
                    # 定额行：挂到当前清单下（带空的materials列表，后续主材行填充）
                    current_bill["quotas"].append({
                        "code": col_b,
                        "name": col_c,
                        "materials": [],  # 主材列表，由后续material行填充
                    })

                elif row_type == "material" and current_bill is not None:
                    # 主材行：挂到当前清单的最后一条定额下
                    if current_bill["quotas"]:
                        current_bill["quotas"][-1]["materials"].append({
                            "code": col_b,
                            "name": col_c,
                            "unit": col_e,
                        })

                elif row_type == "other" and col_c and len(col_c) >= 2:
                    # 可能是分部/章节标题行（如"给排水工程"、"电气安装"）
                    # 保存下来供后续清单项继承
                    current_section = col_c

            # 保存当前Sheet最后一个清单
            if current_bill and current_bill["quotas"]:
                pairs.append(current_bill)
                sheet_pairs += 1

            if sheet_pairs > 0:
                logger.debug(f"  工作簿 '{sheet_name}': {sheet_pairs} 条清单")

        return pairs
    finally:
        wb.close()


def _classify_row(col_a: str, col_b: str, col_c: str, col_d: str) -> str:
    """
    判断一行是清单行、定额行、主材行还是标题行

    判断顺序很重要（优先级从高到低）：
    1. 清单行：9-12位数字编码 + 有项目特征描述（col_d非空）
    2. 主材行：含Z@或纯数字7位+，无项目特征描述（先排除主材，再判定额）
    3. 定额行：编码格式为 X-XXX 或 字母+数字（如D00003）
    4. 标题行/其他：不符合以上规则
    """
    has_serial = bool(re.match(r'^\d+$', col_a))  # 序号是纯数字
    has_bill_code = bool(re.match(r'^\d{9,12}$', col_b))  # 9-12位数字编码
    has_desc = bool(col_d)  # 有项目特征描述

    # ① 清单行：有12位编码+有项目特征描述，或有序号+名称+描述
    # 关键：必须有 has_desc，否则12位主材编码（如080801013001）会误判
    if has_bill_code and col_c and has_desc:
        return "bill"
    if has_serial and col_c and has_desc:
        return "bill"

    # ② 主材行（必须在定额之前判断，否则Z@编码会被定额正则误匹配）
    # 主材特征：无序号、有名称、无项目特征描述、编码含Z@或纯数字7位以上
    # 例如：01190031（日光灯）、26010101Z@2（单联单控开关）、28110000Z@121（电缆）
    # 注意：有序号的行是清单项（如 [7] 080801013001 防鼠板），不是主材
    if col_c and not col_d and not has_serial:
        is_material_code = bool(re.match(r'^\d{7,}', col_b)) or 'Z@' in col_b
        if is_material_code:
            return "material"

    # ③ 定额行：编码格式为 X-XXX 或 字母开头+数字
    # 常见定额编号格式：5-325, 8-2947, D00003, 1-790, 5-92换, AD0003换
    # 江西等省份特殊格式：1-45 换（带空格）、借14-17 换（借用其他册）、补子目1
    cleaned_code = col_b.replace(" ", "").rstrip("换")  # 去空格和"换"后缀
    if cleaned_code.startswith("借"):
        cleaned_code = cleaned_code[1:]  # 去"借"前缀

    is_quota_code = bool(re.match(
        r'^[A-Za-z]?\d{1,2}(-\d+)+$', cleaned_code  # X-XXX 格式，支持多级如 4-3-8、5-1-967、10-11-31
    )) or bool(re.match(
        r'^[A-Za-z]\d{3,}', cleaned_code  # 字母开头+3位以上数字（如 D00003, B010, C00187@1）
    )) or bool(re.match(
        r'^补子目', col_b  # 补充子目（如"补子目1"）
    ))

    if is_quota_code and col_c:
        return "quota"

    # ④ 兜底：没有项目特征描述的12位编码清单（少数Excel格式描述在别的列）
    if has_bill_code and col_c and has_serial:
        return "bill"

    return "other"


def convert_to_kb_records(pairs: list[dict]) -> list[dict]:
    """
    将清单→定额对转换为通用知识库的导入格式

    关键：只保留定额名称（不保留编号），因为编号是省份专属的
    同时通过 specialty_classifier 自动判断每条记录的专业册号
    """
    from src.specialty_classifier import classify as classify_specialty, parse_section_title

    records = []

    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        bill_pattern = pair.get("bill_pattern", "")
        if not isinstance(bill_pattern, str) or not bill_pattern.strip():
            continue

        # 主定额名称模式列表
        quota_patterns = []
        quotas = pair.get("quotas", [])
        if not isinstance(quotas, list):
            quotas = []
        for q in quotas:
            if not isinstance(q, dict):
                continue
            name = str(q.get("name", "")).strip()
            if name:
                quota_patterns.append(name)

        if not quota_patterns:
            continue

        # 判断专业：优先用分部标题，其次用关键词匹配
        section = pair.get("section", "")
        specialty = None
        if section:
            specialty = parse_section_title(section)
        if not specialty:
            classification = classify_specialty(pair.get("bill_name", ""), pair.get("bill_desc", ""))
            specialty = classification.get("primary")

        records.append({
            "bill_pattern": bill_pattern,
            "quota_patterns": quota_patterns,
            "associated_patterns": [],  # 暂时为空，后续可从定额关系中提取
            "param_hints": {},          # 暂时为空，后续可从text_parser提取
            "specialty": specialty,     # 专业册号（如"C10"）
        })

    return records


def import_to_experience(pairs: list[dict], project_name: str, province: str = None):
    """
    将清单→定额对导入经验库（带定额编号，同省份可直接匹配）

    参数:
        pairs: read_excel_pairs() 返回的清单→定额对列表
        project_name: 项目名称（用于标记来源）

    返回:
        {"added": 新增数, "skipped": 跳过数}
    """
    from src.experience_db import ExperienceDB

    exp_db = ExperienceDB()
    added = 0
    skipped = 0

    for pair in pairs:
        if not isinstance(pair, dict):
            skipped += 1
            logger.warning(f"经验库导入跳过: 记录结构非法（期望dict，实际{type(pair).__name__}）")
            continue
        bill_text = pair.get("bill_pattern", "")  # 清单名称+特征描述
        if not isinstance(bill_text, str) or not bill_text.strip():
            skipped += 1
            logger.warning(f"经验库导入跳过: 清单'{pair.get('bill_name', '')[:40]}' 缺少有效bill_pattern")
            continue
        quotas = pair.get("quotas", [])
        if not isinstance(quotas, list):
            skipped += 1
            logger.warning(f"经验库导入跳过: 清单'{pair.get('bill_name', '')[:40]}' quotas结构非法")
            continue
        quota_ids = [q.get("code", "") for q in quotas if isinstance(q, dict) and q.get("code")]
        quota_names = [q.get("name", "") for q in quotas if isinstance(q, dict) and q.get("code")]

        # 提取主材信息：每条定额下的materials合并成一个列表
        # 格式：[{"quota_code": "4-14-379", "name": "单联单控开关", "unit": "只"}, ...]
        materials = []
        for q in quotas:
            if not isinstance(q, dict) or not q.get("code"):
                continue
            for m in q.get("materials", []):
                if isinstance(m, dict) and m.get("name"):
                    materials.append({
                        "quota_code": q["code"],
                        "name": m["name"],
                        "code": m.get("code", ""),
                        "unit": m.get("unit", ""),
                    })

        if not quota_ids:
            skipped += 1
            continue

        try:
            record_id = exp_db.add_experience(
                bill_text=bill_text,
                quota_ids=quota_ids,
                quota_names=quota_names,
                materials=materials,  # 主材信息
                confidence=90,  # 项目导入给90分（权威层，可直通匹配）
                source="project_import",
                project_name=project_name,
                province=province,
            )
            if record_id > 0:
                added += 1
            else:
                skipped += 1
        except Exception as e:
            logger.warning(
                f"经验库导入失败并跳过: bill='{pair.get('bill_name', '')[:40]}', "
                f"code='{pair.get('bill_code', '')}', error={e}"
            )
            skipped += 1

    return {"added": added, "skipped": skipped}


def _select_quota_db() -> str:
    """交互式选择定额库版本

    列出 db/provinces/ 下所有已导入的定额库，让用户选择。
    经验导入必须绑定具体的定额库版本，才能：
    1. 校验定额编号是否存在
    2. 判断经验是否过期（定额库更新后 stale 检测）

    返回:
        完整的省份定额版本名称（如 "北京市建设工程施工消耗量标准(2024)"）
    """
    import sqlite3 as _sqlite3
    db_provinces = config.list_db_provinces()

    if not db_provinces:
        print("\n  [错误] 没有找到已导入的定额库")
        print(f"  请先运行 python tools/import_all.py 导入定额数据")
        sys.exit(1)

    if len(db_provinces) == 1:
        selected = db_provinces[0]
        print(f"\n  只有1个定额库，自动选择: {selected}")
        return selected

    # 列出所有已导入的定额库（含定额条数，帮助用户区分）
    print("\n  请选择该预算文件使用的定额库:")
    print()
    for i, p in enumerate(db_provinces, 1):
        db_path = config.get_quota_db_path(p)
        db_info = ""
        if db_path.exists():
            try:
                conn = _sqlite3.connect(str(db_path), timeout=5)
                count = conn.execute("SELECT COUNT(*) FROM quotas").fetchone()[0]
                conn.close()
                db_info = f"{count}条定额"
            except Exception:
                db_info = "数据库已存在"
        print(f"    [{i}] {p}  ({db_info})")

    print()
    while True:
        try:
            choice = input(f"  输入编号 [1-{len(db_provinces)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(db_provinces):
                selected = db_provinces[idx]
                print(f"  → 已选择: {selected}")
                return selected
            print(f"  编号超出范围，请输入 1-{len(db_provinces)}")
        except ValueError:
            print(f"  请输入数字编号")
        except EOFError:
            # 非交互环境（如管道/脚本调用），无法读取输入，直接退出
            print("\n  [错误] 非交互环境无法选择定额库，请用 --province 参数指定")
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="预算数据导入工具 - 从做好的预算Excel导入经验（一次导入，经验库+通用知识库两边都存）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 导入预算文件（交互式选择定额库版本）
  python tools/import_reference.py 预算文件.xlsx

  # 指定定额库版本（跳过交互选择）
  python tools/import_reference.py 预算文件.xlsx --province "北京市建设工程施工消耗量标准(2024)"

  # 指定项目名称
  python tools/import_reference.py 预算文件.xlsx --project 丰台安置房

  # 查看解析结果但不导入（调试用）
  python tools/import_reference.py 预算文件.xlsx --dry-run
        """,
    )
    parser.add_argument("input_file", help="带定额的预算Excel文件（广联达/造价Home等导出）")
    parser.add_argument("--province", default=None,
                        help="定额库版本全称（如 '北京市建设工程施工消耗量标准(2024)'）。"
                             "不指定则交互式选择。")
    parser.add_argument("--project", default=None, help="项目名称（默认用文件名）")
    parser.add_argument("--dry-run", action="store_true", help="只解析不导入（调试用）")

    args = parser.parse_args()

    # 验证文件
    input_path = Path(args.input_file)
    if not input_path.exists():
        logger.error(f"文件不存在: {input_path}")
        sys.exit(1)

    # 确定定额库版本：指定了就解析匹配，没指定就交互选择
    if args.province:
        try:
            province = config.resolve_province(args.province)
        except ValueError as e:
            logger.error(f"省份解析失败: {e}")
            sys.exit(1)
        # 校验该省份是否有定额库
        db_path = config.get_quota_db_path(province)
        if not db_path.exists():
            logger.error(f"该省份尚未导入定额库: {province}")
            logger.error(f"请先运行: python tools/import_all.py --province \"{province}\"")
            sys.exit(1)
    else:
        province = _select_quota_db()

    project_name = args.project or input_path.stem

    # 第1步：解析Excel
    logger.info(f"解析文件: {input_path}")
    pairs = read_excel_pairs(str(input_path))
    logger.info(f"解析完成: {len(pairs)}条清单→定额对应关系")

    if not pairs:
        logger.warning("未找到有效的清单→定额数据，请检查文件格式")
        sys.exit(1)

    # 打印解析结果摘要
    total_quotas = sum(
        len(p.get("quotas", []))
        for p in pairs
        if isinstance(p, dict) and isinstance(p.get("quotas", []), list)
    )
    logger.info(f"  清单项: {len(pairs)}条")
    logger.info(f"  定额项: {total_quotas}条")
    avg_per_bill = (total_quotas / len(pairs)) if pairs else 0
    logger.info(f"  平均每条清单: {avg_per_bill:.1f}条定额")

    # 打印前5条示例
    logger.info("--- 示例（前5条）---")
    for i, pair in enumerate(pairs[:5]):
        quotas = pair.get("quotas", []) if isinstance(pair, dict) else []
        if not isinstance(quotas, list):
            quotas = []
        quota_names = [str(q.get("name", ""))[:30] for q in quotas if isinstance(q, dict) and q.get("name")]
        if not quota_names:
            quota_names = ["(无有效定额名称)"]
        bill_name = pair.get("bill_name", "") if isinstance(pair, dict) else ""
        logger.info(f"  [{i+1}] {str(bill_name)[:40]}")
        logger.info(f"      → {', '.join(quota_names)}")

    if args.dry_run:
        logger.info("--- dry-run模式，不导入 ---")
        for i, pair in enumerate(pairs):
            bill_name = pair.get("bill_name", "") if isinstance(pair, dict) else ""
            bill_desc = pair.get("bill_desc", "") if isinstance(pair, dict) else ""
            bill_code = pair.get("bill_code", "") if isinstance(pair, dict) else ""
            bill_pattern = pair.get("bill_pattern", "") if isinstance(pair, dict) else ""
            quotas = pair.get("quotas", []) if isinstance(pair, dict) else []
            if not isinstance(quotas, list):
                quotas = []
            print(f"\n--- 第{i+1}条 ---")
            print(f"  清单: {bill_name}")
            if bill_desc:
                print(f"  特征: {str(bill_desc)[:100]}")
            print(f"  编码: {bill_code}")
            print(f"  模式: {str(bill_pattern)[:80]}")
            for q in quotas:
                if not isinstance(q, dict):
                    continue
                print(f"  定额: {q.get('code', '')} → {q.get('name', '')}")
        return

    # 第2步：导入经验库（带定额编号，同省份直接用）
    logger.info("导入经验库...")
    exp_stats = import_to_experience(pairs, project_name, province=province)
    logger.info(f"  经验库: 新增{exp_stats['added']}条, 跳过{exp_stats['skipped']}条")

    # 第3步：导入通用知识库（定额名称模式，跨省份通用）
    logger.info("导入通用知识库...")
    kb_records = convert_to_kb_records(pairs)
    from src.universal_kb import UniversalKB
    kb = UniversalKB()

    kb_stats = kb.batch_import(
        kb_records,
        source_province=province,
        source_project=project_name,
    )
    logger.info(f"  通用知识库: 新增{kb_stats['added']}条, 合并{kb_stats['merged']}条")

    # 打印总结
    logger.info("=" * 50)
    logger.info("导入完成")
    logger.info(f"  项目: {project_name}")
    logger.info(f"  定额库: {province}")
    logger.info(f"  清单项: {len(pairs)}条")
    logger.info(f"  经验库: +{exp_stats['added']}条（带定额编号，同省直接用）")
    logger.info(f"  通用知识库: +{kb_stats['added']}条（定额名称模式，跨省通用）")
    logger.info(f"  数据层级: 权威层（project_import → authority，可直通匹配）")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
