# -*- coding: utf-8 -*-
"""
造价HOME XML文件导入工具

从造价HOME的A.xml或OSS下载的XML文件中提取清单-定额对，
导入到经验库和通用知识库。

支持两种编号匹配模式：
1. 编号直接匹配（省份确定时，编号体系一致，命中率78-90%）
2. 名称模糊匹配（编号不通时，用定额名称在目标省份库中搜索）

使用方法：
    # 预览模式（只分析不导入）
    python tools/import_xml.py A.xml --province 重庆安装 --preview

    # 正式导入
    python tools/import_xml.py A.xml --province 重庆安装

    # 限制条数（调试用）
    python tools/import_xml.py A.xml --province 重庆安装 --limit 20 --preview
"""

import argparse
import re
import sys
from pathlib import Path
from collections import Counter

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
from tools.parse_zaojia_xml import parse_file, extract_bill_quota_pairs
from src.text_parser import normalize_bill_text
from db.sqlite import connect as _db_connect
import config


def _load_quota_ids(province: str) -> dict:
    """加载指定省份定额库的所有编号，返回 {编号: 名称} 字典"""
    db_path = config.get_quota_db_path(province)
    if not db_path.exists():
        return {}
    conn = _db_connect(db_path)
    try:
        rows = conn.execute("SELECT quota_id, name FROM quotas").fetchall()
        return {row[0]: row[1] for row in rows}
    finally:
        conn.close()


def _clean_quota_code(code: str) -> str:
    """清洗定额编号：去掉'换'后缀、空格等"""
    code = code.strip().replace(" ", "")
    code = re.sub(r'换$', '', code)
    return code


def convert_xml_to_pairs(xml_path: str) -> list[dict]:
    """解析XML并转换为 import_reference 兼容的格式

    返回:
        [{
            'bill_name': '砖基础',
            'bill_desc': '砖品种...MU10页岩砖...',
            'bill_code': '010101001001',
            'bill_unit': 'm3',
            'bill_pattern': '砖基础 砖品种...',  # 规范化后的文本
            'quotas': [{'code': 'AD0003', 'name': '砖基础 干混砂浆', 'materials': []}],
            'specialty': '01',  # 工程专业代码
        }]
    """
    result, err = parse_file(xml_path)
    if err:
        logger.error(f"XML解析失败: {err}")
        return []
    if not result:
        logger.error("XML解析返回空结果")
        return []

    raw_pairs = extract_bill_quota_pairs(result)
    logger.info(f"XML解析: 格式={result['format']}, 单位工程={len(result['unit_projects'])}个, "
                f"清单-定额对={len(raw_pairs)}条")

    converted = []
    for p in raw_pairs:
        bill_pattern = normalize_bill_text(p['bill_name'], p['bill_feature'])
        quotas = []
        for q in p['quotas']:
            code = _clean_quota_code(q['code'])
            if code:
                quotas.append({
                    'code': code,
                    'name': q['name'],
                    'materials': [],
                })
        if quotas:
            converted.append({
                'bill_name': p['bill_name'],
                'bill_desc': p['bill_feature'],
                'bill_code': p.get('bill_code', ''),
                'bill_unit': p.get('bill_unit', ''),
                'bill_pattern': bill_pattern,
                'section': p.get('unit_project', ''),
                'quotas': quotas,
                'specialty': p.get('specialty', ''),
            })

    return converted


def preview_import(pairs: list[dict], provinces: list[str]):
    """预览模式：分析编号命中率，列出问题，不写入数据库

    参数:
        pairs: convert_xml_to_pairs() 的输出
        provinces: 要尝试的省份定额库列表
    """
    # 加载所有省份的定额编号
    all_id_maps = {}  # {省份: {编号: 名称}}
    for p in provinces:
        id_map = _load_quota_ids(p)
        all_id_maps[p] = id_map
        logger.info(f"  定额库 {p[:25]}: {len(id_map)}条")

    # 合并所有省份的编号集合
    merged_ids = {}
    for p, id_map in all_id_maps.items():
        for qid, name in id_map.items():
            if qid not in merged_ids:
                merged_ids[qid] = (name, p)

    # 逐条校验
    total_quotas = 0
    matched_quotas = 0
    unmatched_quotas = 0
    unmatched_list = []  # 未命中的定额
    matched_pairs = 0  # 所有定额都命中的清单
    partial_pairs = 0  # 部分定额命中
    failed_pairs = 0   # 全部定额未命中

    # 统计同义词缺口（BM25改进点）
    synonym_gaps = []  # (清单名, 外部定额名, 编号)

    for p in pairs:
        pair_matched = 0
        pair_total = 0
        for q in p['quotas']:
            pair_total += 1
            total_quotas += 1
            if q['code'] in merged_ids:
                matched_quotas += 1
                pair_matched += 1
            else:
                unmatched_quotas += 1
                if len(unmatched_list) < 50:
                    unmatched_list.append({
                        'code': q['code'],
                        'name': q['name'],
                        'bill': p['bill_name'],
                    })
                # 记录同义词缺口
                synonym_gaps.append({
                    'bill_name': p['bill_name'],
                    'quota_name': q['name'],
                    'quota_code': q['code'],
                })

        if pair_matched == pair_total:
            matched_pairs += 1
        elif pair_matched > 0:
            partial_pairs += 1
        else:
            failed_pairs += 1

    # 输出报告
    print(f"\n{'='*60}")
    print(f"导入预览报告")
    print(f"{'='*60}")
    print(f"清单总数: {len(pairs)}条")
    print(f"定额总数: {total_quotas}条")
    print(f"")
    print(f"--- 编号命中率 ---")
    rate = matched_quotas * 100 // total_quotas if total_quotas > 0 else 0
    print(f"命中: {matched_quotas}条 ({rate}%)")
    print(f"未命中: {unmatched_quotas}条 ({100-rate}%)")
    print(f"")
    print(f"--- 清单级统计 ---")
    print(f"全部命中: {matched_pairs}条 — 可直接导入")
    print(f"部分命中: {partial_pairs}条 — 命中的部分可导入，未命中的需名称匹配")
    print(f"全部未中: {failed_pairs}条 — 可能是目标省份缺少对应专业的定额库")
    print(f"")

    # 编号前缀分布
    prefix_counter = Counter()
    prefix_hit = Counter()
    for p in pairs:
        for q in p['quotas']:
            pfx = q['code'][:2] if len(q['code']) >= 2 else q['code']
            prefix_counter[pfx] += 1
            if q['code'] in merged_ids:
                prefix_hit[pfx] += 1

    print(f"--- 编号前缀命中率 ---")
    for pfx in sorted(prefix_counter.keys(), key=lambda x: -prefix_counter[x]):
        total = prefix_counter[pfx]
        hit = prefix_hit.get(pfx, 0)
        pct = hit * 100 // total if total > 0 else 0
        print(f"  {pfx}: {hit}/{total} ({pct}%)")

    # 未命中的定额样例
    if unmatched_list:
        print(f"\n--- 未命中样例（前20条）---")
        for u in unmatched_list[:20]:
            print(f"  {u['code']} = {u['name'][:35]}  ← 清单: {u['bill'][:25]}")

    # 可导入统计
    importable = matched_pairs + partial_pairs
    print(f"\n--- 总结 ---")
    print(f"可导入: {importable}条清单（{importable*100//len(pairs)}%）— 至少有1个定额命中")
    print(f"不可导入: {failed_pairs}条 — 需要对应专业的定额库")
    print(f"{'='*60}")

    return {
        'total_pairs': len(pairs),
        'total_quotas': total_quotas,
        'matched_quotas': matched_quotas,
        'matched_pairs': matched_pairs,
        'partial_pairs': partial_pairs,
        'failed_pairs': failed_pairs,
    }


def do_import(pairs: list[dict], project_name: str, provinces: list[str],
              only_matched: bool = True, source: str = "batch_import"):
    """正式导入：写入经验库和通用知识库

    参数:
        pairs: convert_xml_to_pairs() 的输出
        project_name: 项目名称
        provinces: 省份定额库列表
        only_matched: True=只导入编号命中的条目，False=全部导入（编号校验失败的也存）
        source: 数据来源类型（batch_import=候选层，project_import=权威层）
    """
    from tools.import_reference import import_to_experience, convert_to_kb_records

    if only_matched:
        # 预先过滤：只保留至少有1个编号命中的清单
        merged_ids = set()
        for p in provinces:
            id_map = _load_quota_ids(p)
            merged_ids.update(id_map.keys())

        filtered = []
        for p in pairs:
            valid_quotas = [q for q in p['quotas'] if q['code'] in merged_ids]
            if valid_quotas:
                p_copy = dict(p)
                p_copy['quotas'] = valid_quotas
                filtered.append(p_copy)

        logger.info(f"过滤后: {len(filtered)}条清单（原{len(pairs)}条，"
                    f"去掉{len(pairs)-len(filtered)}条全部编号未命中的）")
        pairs = filtered

    if not pairs:
        logger.warning("没有可导入的数据")
        return

    # 导入经验库（跳过逐条向量写入，导入完后统一重建）
    logger.info(f"导入经验库... ({len(pairs)}条，跳过逐条向量写入)")
    exp_stats = import_to_experience(pairs, project_name, all_provinces=provinces,
                                     source=source, skip_vector=True)
    logger.info(f"  经验库: 新增{exp_stats['added']}条, 跳过{exp_stats['skipped']}条")

    # 导入通用知识库
    logger.info("导入通用知识库...")
    kb_records = convert_to_kb_records(pairs)
    from src.universal_kb import UniversalKB
    kb = UniversalKB()
    kb_stats = kb.batch_import(
        kb_records,
        source_province=provinces[0] if provinces else "",
        source_project=project_name,
    )
    logger.info(f"  通用知识库: 新增{kb_stats['added']}条, 合并{kb_stats['merged']}条")

    # 批量重建向量索引（一次性，比逐条快得多）
    if exp_stats['added'] > 0:
        from src.experience_db import ExperienceDB
        logger.info("重建经验库向量索引...")
        exp_db = ExperienceDB()
        exp_db.rebuild_vector_index()
        logger.info("  向量索引重建完成")

    # 汇总
    print(f"\n{'='*50}")
    print(f"导入完成")
    print(f"  项目: {project_name}")
    print(f"  清单: {len(pairs)}条")
    print(f"  经验库: +{exp_stats['added']}条")
    print(f"  通用知识库: +{kb_stats['added']}条")
    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(
        description="造价HOME XML文件导入工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input_file", help="XML文件路径（造价HOME A.xml等）")
    parser.add_argument("--province", required=True,
                        help="主定额库（如 '重庆安装' 或完整名称）")
    parser.add_argument("--aux-provinces", default=None,
                        help="辅助定额库（逗号分隔，如 '重庆房建,重庆市政'）")
    parser.add_argument("--project", default=None, help="项目名称（默认用文件名）")
    parser.add_argument("--preview", action="store_true", help="预览模式（只分析不导入）")
    parser.add_argument("--trust", action="store_true",
                        help="信任模式：数据进权威层（默认进候选层，需人工确认后晋升）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前N条（调试用）")

    args = parser.parse_args()

    # 验证文件
    input_path = Path(args.input_file)
    if not input_path.exists():
        logger.error(f"文件不存在: {input_path}")
        sys.exit(1)

    # 解析省份
    provinces = []
    try:
        main_province = config.resolve_province(args.province)
        provinces.append(main_province)
    except ValueError as e:
        logger.error(f"省份解析失败: {e}")
        sys.exit(1)

    if args.aux_provinces:
        for ap in args.aux_provinces.split(","):
            ap = ap.strip()
            if ap:
                try:
                    resolved = config.resolve_province(ap)
                    if resolved not in provinces:
                        provinces.append(resolved)
                except ValueError:
                    logger.warning(f"辅助定额库解析失败: {ap}")

    project_name = args.project or input_path.stem

    # 第1步：解析XML
    logger.info(f"解析XML: {input_path}")
    pairs = convert_xml_to_pairs(str(input_path))
    if not pairs:
        logger.error("未解析到有效数据")
        sys.exit(1)

    if args.limit > 0:
        pairs = pairs[:args.limit]
        logger.info(f"限制处理前{args.limit}条")

    # 第2步：预览或导入
    if args.preview:
        preview_import(pairs, provinces)
    else:
        # 先预览再导入
        stats = preview_import(pairs, provinces)
        if stats['matched_pairs'] + stats['partial_pairs'] == 0:
            logger.error("没有任何编号命中，请检查省份是否正确")
            sys.exit(1)

        # 根据 --trust 决定数据进哪一层
        source = "project_import" if args.trust else "batch_import"
        layer_hint = "authority（权威层）" if args.trust else "candidate（候选层）"
        print(f"\n开始导入... 数据将进入 {layer_hint}")
        do_import(pairs, project_name, provinces, only_matched=True, source=source)


if __name__ == "__main__":
    main()
