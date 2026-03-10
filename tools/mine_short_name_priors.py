# -*- coding: utf-8 -*-
"""
短名称频率先验挖掘脚本

从经验库权威层挖掘"短名称+专业→最常套哪个定额族"的映射，
生成 data/short_name_priors.json，供 bill_cleaner 兜底注入。

原理：
  经验库有9.8万条已验证的"清单→定额"对照数据。
  当清单名称是"水箱""阀门"这类短名称时，统计它们在各专业下
  最常被套到哪个定额族（如 C9消防下的"水箱"→"消防水箱"），
  把高频模式存下来，以后遇到同类短名称就有兜底提示。

用法：
  python tools/mine_short_name_priors.py                 # 生成数据文件
  python tools/mine_short_name_priors.py --dry-run        # 只打印统计不写文件
  python tools/mine_short_name_priors.py --min-freq 5     # 调低最小频率（探索用）
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent.parent

# 复用 self_learn.py 的定额名清洗逻辑（去参数/档位，保留核心名词）
PARAM_SUFFIX_RE = re.compile(
    r'[\(（].*?[\)）]|≤\S+|≥\S+|DN\d+|φ\d+|\d+[mtkg]|\d+mm|'
    r'\d+m2|\d+m3/h|\d+kW|\d+kVA|\d+A'
)


def clean_quota_name(name: str) -> str:
    """从定额名提取核心族名（去参数/档位/单位）

    复用 self_learn.py 的 clean_quota_name() 逻辑，
    例如："消防水箱安装 容积≤20m³" → "消防水箱安装"
    """
    if not name:
        return ''
    cleaned = PARAM_SUFFIX_RE.sub('', name)
    cleaned = re.sub(r'[\d\.\s≤≥]+$', '', cleaned).strip()
    return cleaned


# 短名称判断（和 bill_cleaner.py 的 AMBIGUOUS_SHORT_NAMES 保持一致）
AMBIGUOUS_SHORT_NAMES = {
    "水箱", "阀门", "水泵", "风机", "开关", "灯具", "桥架",
    "风口", "散流器", "管道", "管件", "接头", "仪表",
    "配电箱", "控制柜", "风管", "保温", "支架", "水表",
    "电缆", "电线", "线缆", "风阀", "止回阀", "蝶阀",
}


def is_short_name(bill_name: str) -> bool:
    """判断是否为短名称（和 bill_cleaner 逻辑一致）"""
    name = bill_name.strip()
    cn_chars = len([c for c in name if '\u4e00' <= c <= '\u9fff'])
    if cn_chars == 0 or cn_chars > 6:
        return False
    if name in AMBIGUOUS_SHORT_NAMES:
        return True
    if cn_chars <= 3:
        return True
    return False


def mine_priors(db_path: str, min_freq: int = 20) -> dict:
    """从经验库挖掘短名称频率先验

    参数:
        db_path: experience.db 路径
        min_freq: 最小频率门槛（tier2用，tier1固定>=20）

    返回:
        {"tier1": {...}, "tier2": {...}, "_meta": {...}}
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute('''
        SELECT bill_name, quota_names, specialty, province
        FROM experiences WHERE layer='authority'
    ''').fetchall()
    conn.close()

    total_rows = len(rows)
    print(f"经验库权威层: {total_rows}条")

    # 按 (短名称, 专业) 分组统计
    # group_stats[(name, specialty)] = {"families": Counter, "provinces": set}
    group_stats = defaultdict(lambda: {"families": Counter(), "provinces": set()})
    filtered_count = 0

    for row in rows:
        bill_name = (row["bill_name"] or "").strip()
        if not is_short_name(bill_name):
            continue

        specialty = (row["specialty"] or "").strip()
        if not specialty:
            continue

        # 解析 quota_names（JSON数组），取第一个定额名
        try:
            quota_names = json.loads(row["quota_names"]) if row["quota_names"] else []
        except (json.JSONDecodeError, TypeError):
            continue
        if not quota_names:
            continue

        # 用 clean_quota_name 提取族名
        family = clean_quota_name(str(quota_names[0]))
        if not family or len(family) < 2:
            continue

        province = (row["province"] or "").strip()
        key = (bill_name, specialty)
        group_stats[key]["families"][family] += 1
        if province:
            group_stats[key]["provinces"].add(province)
        filtered_count += 1

    print(f"短名称记录: {filtered_count}条, 分组数: {len(group_stats)}个")

    # 分层统计
    tier1 = {}
    tier2 = {}

    for (name, specialty), stats in sorted(group_stats.items()):
        families = stats["families"]
        total = sum(families.values())
        top_items = families.most_common(2)
        top1_family, top1_count = top_items[0]
        top2_family, top2_count = top_items[1] if len(top_items) > 1 else ("", 0)

        concentration = top1_count / total if total > 0 else 0
        top2_gap = (top1_count - top2_count) / total if total > 0 else 0
        province_count = len(stats["provinces"])

        entry = {
            "short_name": name,
            "specialty": specialty,
            "top_family": top1_family,
            "top_family_count": top1_count,
            "second_family": top2_family,
            "second_family_count": top2_count,
            "total_count": total,
            "concentration": round(concentration, 3),
            "top2_gap": round(top2_gap, 3),
            "province_count": province_count,
        }

        entry_key = f"{name}|{specialty}"

        # tier1: >=20次 + >=90%集中度 + top1>=2×top2
        if (total >= 20
                and concentration >= 0.90
                and top1_count >= 2 * max(top2_count, 1)):
            tier1[entry_key] = entry
        # tier2: >=15次 + >=80%集中度（观测层，不自动注入）
        elif total >= max(min_freq, 15) and concentration >= 0.80:
            tier2[entry_key] = entry

    result = {
        "_meta": {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": f"experience.db authority层",
            "source_rows": total_rows,
            "filtered_short_name_rows": filtered_count,
            "group_by": "(bill_name, specialty)",
            "family_extract": "clean_quota_name() — 去参数/档位，保留核心名词",
            "tier1_threshold": ">=20次 AND >=90%集中度 AND top1>=2×top2",
            "tier2_threshold": ">=15次 AND >=80%集中度（观测层，不自动注入）",
            "tier1_count": len(tier1),
            "tier2_count": len(tier2),
        },
        "tier1": tier1,
        "tier2": tier2,
    }

    return result


def print_summary(result: dict):
    """打印挖掘结果摘要"""
    meta = result["_meta"]
    print(f"\n{'='*60}")
    print(f"短名称频率先验挖掘结果")
    print(f"{'='*60}")
    print(f"数据源: {meta['source']} ({meta['source_rows']}条)")
    print(f"短名称记录: {meta['filtered_short_name_rows']}条")
    print(f"tier1 (可自动注入): {meta['tier1_count']}个")
    print(f"tier2 (仅观测): {meta['tier2_count']}个")

    if result["tier1"]:
        print(f"\n--- tier1 详情 ---")
        for key, info in sorted(result["tier1"].items(),
                                key=lambda x: -x[1]["total_count"]):
            print(f"  {info['short_name']:6s} | {info['specialty']:4s} | "
                  f"→ {info['top_family']:12s} "
                  f"({info['top_family_count']}/{info['total_count']}, "
                  f"{info['concentration']:.0%}) "
                  f"省:{info['province_count']}")

    if result["tier2"]:
        print(f"\n--- tier2 详情（观测层，不注入） ---")
        for key, info in sorted(result["tier2"].items(),
                                key=lambda x: -x[1]["total_count"])[:20]:
            print(f"  {info['short_name']:6s} | {info['specialty']:4s} | "
                  f"→ {info['top_family']:12s} "
                  f"({info['top_family_count']}/{info['total_count']}, "
                  f"{info['concentration']:.0%}) "
                  f"省:{info['province_count']}")
        if len(result["tier2"]) > 20:
            print(f"  ... 共{len(result['tier2'])}个，只显示前20")


def main():
    parser = argparse.ArgumentParser(description="短名称频率先验挖掘")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印统计，不写文件")
    parser.add_argument("--min-freq", type=int, default=15,
                        help="tier2最小频率门槛（默认15）")
    parser.add_argument("--db", type=str,
                        default=str(ROOT / "db" / "common" / "experience.db"),
                        help="经验库路径")
    parser.add_argument("--output", type=str,
                        default=str(ROOT / "data" / "short_name_priors.json"),
                        help="输出文件路径")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"错误: 经验库不存在 {args.db}")
        sys.exit(1)

    result = mine_priors(args.db, min_freq=args.min_freq)
    print_summary(result)

    if not args.dry_run:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"\n已写入: {output_path}")
    else:
        print(f"\n(dry-run模式，未写入文件)")


if __name__ == "__main__":
    main()
