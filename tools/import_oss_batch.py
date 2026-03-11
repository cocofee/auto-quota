# -*- coding: utf-8 -*-
"""
OSS XML批量导入工具

扫描OSS下载目录，按专业自动路由到对应定额库，
编号精确过滤后以oss_import写入候选层 + 通用知识库。

设计要点（Codex 5.4审核确认）：
1. 全部进候选层（source=oss_import），不直接进权威层
2. 只导入编号在定额库中确实存在的条目（硬过滤）
3. 打batch_id标记，支持整批回滚
4. 同步写通用知识库（清单名→定额名的跨省通用映射）
5. 向量索引最后统一重建（不逐条写，快很多）

使用方法：
    # 预览模式（只统计不导入）
    python tools/import_oss_batch.py --preview

    # 小批量试导（前10个文件）
    python tools/import_oss_batch.py --limit 10

    # 只导福建
    python tools/import_oss_batch.py --province 福建

    # 全量导入
    python tools/import_oss_batch.py

    # 回滚某批次
    python tools/import_oss_batch.py --rollback <batch_id>
"""

import argparse
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

# ============================================================
# 配置：OSS文件目录和省份→定额库映射
# ============================================================

# OSS XML文件所在目录（by_province已去重）
OSS_BASE = Path(r"D:\广联达临时文件\oss_samples\by_province")

# 省份目录 → 定额库映射（按ZYLB字段自动路由）
# key=ZYLB关键词, value=定额库省份简称（用config.resolve_province解析）
PROVINCE_ROUTING = {
    "FJ": {
        "建筑": "福建建筑",
        "市政": "福建市政",
        "园林": "福建园林",
        "安装": "福建安装",
        # 默认（无法识别专业时）
        "_default": ["福建建筑", "福建市政", "福建园林", "福建安装"],
    },
    "ZJ": {
        "建筑": "浙江建筑",
        "市政": "浙江市政",
        "园林": "浙江园林",
        "安装": "浙江安装",
        "_default": ["浙江建筑", "浙江市政", "浙江园林", "浙江安装"],
    },
    "JS": {
        "建筑": "江苏建筑",
        "市政": "江苏市政",
        "安装": "江苏安装",
        "_default": ["江苏建筑", "江苏市政", "江苏安装"],
    },
}


def _detect_specialty(xml_path: str) -> str:
    """从XML文件头部检测工程专业类型

    返回: 专业关键词（建筑/市政/园林/安装/未知）
    """
    try:
        with open(xml_path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(1000)
    except Exception:
        return "未知"

    # 福建格式：ZYLB="房屋建筑与装饰工程"
    m = re.search(r'ZYLB="([^"]+)"', head)
    if m:
        zylb = m.group(1)
        if "建筑" in zylb or "装饰" in zylb:
            return "建筑"
        if "市政" in zylb:
            return "市政"
        if "园林" in zylb or "绿化" in zylb:
            return "园林"
        if "安装" in zylb:
            return "安装"
        if "维护" in zylb:
            return "市政"  # 市政维护归市政
        return "未知"

    # 浙江格式：没有ZYLB字段，用工程分类判断
    # 浙江XML大多是混合工程，定额编号会跨多个专业
    # 直接返回"未知"，让它走_default（全库匹配）
    if "浙江省建设工程计价成果文件数据标准" in head:
        return "未知"

    return "未知"


def _resolve_provinces(prov_code: str, specialty: str) -> list[str]:
    """根据省份代码和专业，解析出定额库全名列表

    返回: 定额库全名列表（如 ["福建省房屋建筑与装饰工程预算定额(2017)"]）
    """
    import config

    routing = PROVINCE_ROUTING.get(prov_code, {})
    if not routing:
        return []

    # 按专业路由
    if specialty in routing:
        short_names = [routing[specialty]]
    else:
        short_names = routing.get("_default", [])

    # 解析为完整省份名
    resolved = []
    for sn in short_names:
        try:
            full_name = config.resolve_province(sn)
            resolved.append(full_name)
        except ValueError:
            logger.warning(f"省份解析失败: {sn}")
    return resolved


def _load_all_quota_ids(provinces: list[str]) -> dict[str, str]:
    """加载多个省份定额库的所有编号

    返回: {编号: 省份全名} 字典
    """
    import config
    from db.sqlite import connect as _db_connect

    result = {}
    for province in provinces:
        db_path = config.get_quota_db_path(province)
        if not db_path.exists():
            logger.warning(f"定额库不存在: {province} → {db_path}")
            continue
        conn = _db_connect(db_path)
        try:
            rows = conn.execute("SELECT quota_id FROM quotas").fetchall()
            for row in rows:
                if row[0] not in result:  # 不覆盖，先到先得
                    result[row[0]] = province
        finally:
            conn.close()
    return result


def scan_oss_files(prov_filter: str = None) -> list[dict]:
    """扫描OSS目录，返回待处理文件列表

    返回: [{"path": "...", "prov_code": "FJ", "filename": "..."}]
    """
    files = []
    for prov_code in sorted(PROVINCE_ROUTING.keys()):
        if prov_filter and prov_filter.upper() != prov_code:
            continue
        prov_dir = OSS_BASE / prov_code
        if not prov_dir.exists():
            continue
        for f in sorted(prov_dir.iterdir()):
            if f.suffix.upper() == ".XML":
                files.append({
                    "path": str(f),
                    "prov_code": prov_code,
                    "filename": f.name,
                })
    return files


def preview_mode(files: list[dict]):
    """预览模式：统计各省文件数和预计数据量，不导入"""
    from tools.parse_zaojia_xml import parse_file, extract_bill_quota_pairs

    print(f"\n{'='*60}")
    print(f"OSS批量导入预览")
    print(f"{'='*60}")
    print(f"待处理文件: {len(files)}个\n")

    for prov_code in sorted(set(f["prov_code"] for f in files)):
        prov_files = [f for f in files if f["prov_code"] == prov_code]
        # 抽样5个估算
        sample_n = min(5, len(prov_files))
        total_pairs = 0
        ok = 0
        specialty_dist = Counter()
        for f in prov_files[:sample_n]:
            specialty = _detect_specialty(f["path"])
            specialty_dist[specialty] += 1
            result, err = parse_file(f["path"])
            if err:
                continue
            pairs = extract_bill_quota_pairs(result)
            total_pairs += len(pairs)
            ok += 1
        avg = total_pairs / max(ok, 1)
        estimated = int(avg * len(prov_files))

        # 看编号匹配率
        provinces = _resolve_provinces(prov_code, "未知")
        quota_ids = _load_all_quota_ids(provinces)

        matched = 0
        total_q = 0
        for f in prov_files[:sample_n]:
            result, err = parse_file(f["path"])
            if err:
                continue
            pairs = extract_bill_quota_pairs(result)
            for p in pairs:
                for q in p.get("quotas", []):
                    total_q += 1
                    if q["code"] in quota_ids:
                        matched += 1
        match_rate = matched / max(total_q, 1) * 100

        print(f"{prov_code}: {len(prov_files)}个文件")
        print(f"  预计清单-定额对: ~{estimated:,}条")
        print(f"  编号匹配率(抽样): {match_rate:.0f}%")
        print(f"  专业分布(抽样): {dict(specialty_dist)}")
        print(f"  目标定额库: {provinces}")
        print()

    print(f"{'='*60}")


def do_batch_import(files: list[dict], batch_id: str, limit: int = 0):
    """批量导入主函数

    参数:
        files: scan_oss_files()返回的文件列表
        batch_id: 批次ID（用于回滚标记）
        limit: 限制处理文件数（0=不限制）
    """
    from tools.parse_zaojia_xml import parse_file, extract_bill_quota_pairs
    from tools.import_xml import convert_xml_to_pairs
    from tools.import_reference import import_to_experience, convert_to_kb_records
    from src.universal_kb import UniversalKB

    if limit > 0:
        files = files[:limit]

    print(f"\n{'='*60}")
    print(f"OSS批量导入")
    print(f"  批次ID: {batch_id}")
    print(f"  文件数: {len(files)}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    # 统计
    total_files = len(files)
    ok_files = 0
    fail_files = 0
    total_exp_added = 0
    total_exp_skipped = 0
    total_kb_added = 0
    total_kb_merged = 0
    prov_stats = defaultdict(lambda: {"files": 0, "pairs": 0, "exp_added": 0})

    # 加载各省所有定额库编号（一次性加载，避免重复IO）
    all_quota_maps = {}  # {prov_code: {编号: 省份全名}}
    for prov_code in set(f["prov_code"] for f in files):
        provinces = _resolve_provinces(prov_code, "未知")
        all_quota_maps[prov_code] = _load_all_quota_ids(provinces)

    kb = UniversalKB()
    source = "oss_import"  # 统一标记，候选层
    # project_name里带batch_id，方便回滚
    project_prefix = f"oss_{batch_id}"

    start_time = time.time()

    for i, f in enumerate(files):
        prov_code = f["prov_code"]
        filepath = f["path"]
        filename = f["filename"]

        # 进度显示
        if (i + 1) % 20 == 0 or i == 0:
            elapsed = time.time() - start_time
            speed = (i + 1) / max(elapsed, 1)
            eta = (total_files - i - 1) / max(speed, 0.01)
            print(f"  进度: {i+1}/{total_files} ({(i+1)*100//total_files}%) "
                  f"| 速度: {speed:.1f}文件/秒 | 预计剩余: {eta:.0f}秒")

        # 1. 检测专业
        specialty = _detect_specialty(filepath)

        # 2. 解析XML
        try:
            pairs = convert_xml_to_pairs(filepath)
        except Exception as e:
            logger.warning(f"文件解析失败: {filename} → {e}")
            fail_files += 1
            continue

        if not pairs:
            fail_files += 1
            continue

        # 3. 编号精确过滤（硬过滤：只保留编号在定额库中存在的）
        quota_map = all_quota_maps.get(prov_code, {})
        filtered = []
        for p in pairs:
            valid_quotas = [q for q in p["quotas"] if q["code"] in quota_map]
            if valid_quotas:
                p_copy = dict(p)
                p_copy["quotas"] = valid_quotas
                filtered.append(p_copy)

        if not filtered:
            fail_files += 1
            continue

        # 4. 确定目标定额库
        # 根据过滤后的编号，找出实际命中了哪些省份的库
        hit_provinces = set()
        for p in filtered:
            for q in p["quotas"]:
                prov_full = quota_map.get(q["code"])
                if prov_full:
                    hit_provinces.add(prov_full)
        provinces_list = sorted(hit_provinces)

        if not provinces_list:
            fail_files += 1
            continue

        # 5. 导入经验库（候选层）
        project_name = f"{project_prefix}_{filename}"
        exp_stats = import_to_experience(
            filtered, project_name,
            all_provinces=provinces_list,
            source=source,
            skip_vector=True,  # 最后统一重建
        )

        # 6. 导入通用知识库（快速模式：只做精确文本去重，跳过向量语义去重）
        kb_records = convert_to_kb_records(filtered)
        kb_stats = kb.batch_import(
            kb_records,
            source_province=provinces_list[0],
            source_project=project_name,
            skip_vector_dedup=True,  # 大批量导入用快速模式，最后统一rebuild
        )

        # 统计
        ok_files += 1
        total_exp_added += exp_stats["added"]
        total_exp_skipped += exp_stats["skipped"]
        total_kb_added += kb_stats["added"]
        total_kb_merged += kb_stats["merged"]
        prov_stats[prov_code]["files"] += 1
        prov_stats[prov_code]["pairs"] += len(filtered)
        prov_stats[prov_code]["exp_added"] += exp_stats["added"]

    # 7. 统一重建向量索引
    if total_exp_added > 0:
        print(f"\n重建经验库向量索引（{total_exp_added}条新数据）...")
        from src.experience_db import ExperienceDB
        exp_db = ExperienceDB()
        exp_db.rebuild_vector_index()
        print("  向量索引重建完成")

    elapsed = time.time() - start_time

    # 汇总
    print(f"\n{'='*60}")
    print(f"OSS批量导入完成")
    print(f"{'='*60}")
    print(f"  批次ID: {batch_id}")
    print(f"  耗时: {elapsed:.0f}秒 ({elapsed/60:.1f}分钟)")
    print(f"  文件: 成功{ok_files} / 失败{fail_files} / 总{total_files}")
    print(f"  经验库(候选层): +{total_exp_added}条 (跳过{total_exp_skipped})")
    print(f"  通用知识库: +{total_kb_added}条 (合并{total_kb_merged})")
    print()
    print(f"  按省份:")
    for pc in sorted(prov_stats.keys()):
        s = prov_stats[pc]
        print(f"    {pc}: {s['files']}文件 / {s['pairs']}条清单 / +{s['exp_added']}经验")
    print(f"\n  回滚命令: python tools/import_oss_batch.py --rollback {batch_id}")
    print(f"{'='*60}")


def rollback(batch_id: str):
    """回滚指定批次的导入数据

    通过project_name前缀匹配删除，因为导入时project_name格式为 oss_{batch_id}_{filename}
    """
    db_path = PROJECT_ROOT / "db" / "common" / "experience.db"
    if not db_path.exists():
        print("经验库不存在")
        return

    conn = sqlite3.connect(str(db_path))
    prefix = f"oss_{batch_id}_%"

    # 先统计
    count = conn.execute(
        "SELECT COUNT(*) FROM experiences WHERE project_name LIKE ? AND source = 'oss_import'",
        (prefix,)
    ).fetchone()[0]

    if count == 0:
        print(f"未找到批次 {batch_id} 的数据")
        conn.close()
        return

    print(f"找到 {count} 条数据属于批次 {batch_id}")
    confirm = input(f"确认删除这 {count} 条数据？(y/N): ")
    if confirm.lower() != "y":
        print("取消回滚")
        conn.close()
        return

    # 标记为deleted（软删除，不物理删除）
    conn.execute(
        "UPDATE experiences SET layer = 'deleted' WHERE project_name LIKE ? AND source = 'oss_import'",
        (prefix,)
    )
    conn.commit()
    conn.close()
    print(f"已回滚 {count} 条数据（标记为deleted）")

    # 重建向量索引
    print("重建向量索引...")
    from src.experience_db import ExperienceDB
    exp_db = ExperienceDB()
    exp_db.rebuild_vector_index()
    print("完成")


def main():
    parser = argparse.ArgumentParser(
        description="OSS XML批量导入工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--preview", action="store_true", help="预览模式（只统计不导入）")
    parser.add_argument("--province", default=None, help="只处理指定省份（FJ/ZJ/JS）")
    parser.add_argument("--limit", type=int, default=0, help="限制处理文件数（调试用）")
    parser.add_argument("--rollback", default=None, help="回滚指定批次ID的数据")
    parser.add_argument("--batch-id", default=None, help="指定批次ID（默认自动生成）")
    args = parser.parse_args()

    # 回滚模式
    if args.rollback:
        rollback(args.rollback)
        return

    # 扫描文件
    prov_filter = None
    if args.province:
        prov_filter = args.province.upper()
        if prov_filter not in PROVINCE_ROUTING:
            # 尝试中文转代码
            cn_map = {"福建": "FJ", "浙江": "ZJ", "江苏": "JS"}
            prov_filter = cn_map.get(args.province, prov_filter)

    files = scan_oss_files(prov_filter)
    if not files:
        print("未找到XML文件")
        return

    # 预览模式
    if args.preview:
        preview_mode(files)
        return

    # 正式导入
    batch_id = args.batch_id or datetime.now().strftime("%Y%m%d_%H%M")
    do_batch_import(files, batch_id, limit=args.limit)


if __name__ == "__main__":
    main()
