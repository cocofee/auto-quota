# -*- coding: utf-8 -*-
"""
贾维斯纠正工具 - 供 Claude Code 直接存入审核纠正结果

用法:
    # 存入单条纠正
    python tools/jarvis_store.py --name "事故风机手动开关" --desc "规格：事故风机手动开关" \
        --quota-ids '["C4-10-50"]' --quota-names '["按钮开关安装"]' \
        --reason "事故风机手动开关是电气按钮开关，不是风口"

    # 批量存入（从JSON文件）
    python tools/jarvis_store.py --file corrections.json

    # 查看经验库中某条清单的现有记录
    python tools/jarvis_store.py --lookup "事故风机手动开关"
"""

import os
import json
import argparse
from pathlib import Path

import config
from src.experience_db import ExperienceDB
from src.text_parser import normalize_bill_text


def _parse_json_list(raw_text: str, field_name: str) -> list:
    try:
        value = json.loads(raw_text)
    except Exception as e:
        raise ValueError(f"{field_name} 不是合法JSON: {e}") from e
    if not isinstance(value, list):
        raise ValueError(f"{field_name} 必须是JSON数组")
    return value


def store_one(name: str, desc: str, quota_ids: list, quota_names: list,
              reason: str = "", specialty: str = "", province: str = None,
              confirmed: bool = False):
    """存入一条纠正到经验库

    参数:
        confirmed: True=用户已确认，直接写权威层；False=自动纠正，写候选层
    """
    province = province or config.get_current_province()
    exp_db = ExperienceDB()

    bill_text = normalize_bill_text(name, desc)

    # 用户确认的纠正直接写权威层（source=user_confirmed），
    # 否则写候选层（source=auto_review）待后续审核
    source = "user_confirmed" if confirmed else "auto_review"
    confidence = 95 if confirmed else 85

    record_id = exp_db.add_experience(
        bill_text=bill_text,
        quota_ids=quota_ids,
        quota_names=quota_names,
        source=source,
        confidence=confidence,
        specialty=specialty,
        province=province,
        notes=f"[贾维斯审核] {reason}",
    )

    if record_id > 0:
        print(f"  存入成功 (id={record_id}): {name} → {quota_ids}")
        # L5跨省闭环：同步到通用知识库（定额名称跨省通用）
        if getattr(config, "UNIVERSAL_KB_SYNC_ENABLED", False) and quota_names:
            try:
                from src.universal_kb import UniversalKB
                kb = UniversalKB()
                # 过滤空字符串
                valid_names = [str(n) for n in quota_names if n]
                if valid_names:
                    kb.learn_from_correction(
                        bill_text=bill_text,
                        quota_names=valid_names,
                        province=province,
                    )
            except Exception as e:
                # 不影响经验库存入，仅记录日志
                from loguru import logger as _logger
                _logger.debug(f"通用知识库同步跳过: {e}")
        return True
    elif record_id == -1:
        print(f"  被校验拦截: {name} → {quota_ids} (定额编号可能不存在)")
        return False
    else:
        print(f"  已存在相同记录，跳过: {name}")
        return False


def store_batch(filepath: str, province: str = None, confirmed: bool = False):
    """从JSON文件批量存入纠正

    支持两种格式：
    格式1（jarvis.md生成）: [{seq, quota_id, quota_name, name, ...}]
    格式2（旧格式）: {"corrections": [{name, quota_ids, quota_names, ...}]}

    参数:
        confirmed: True=用户已确认，直接写权威层；False=自动纠正，写候选层
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"批量文件读取失败: {e}")
        return

    # 兼容两种格式：数组（新格式）或字典（旧格式）
    if isinstance(data, list):
        corrections = data
        is_new_format = True
    elif isinstance(data, dict):
        corrections = data.get("corrections", [])
        is_new_format = False
    else:
        print("批量文件格式错误: 根节点必须是数组或对象")
        return

    if not isinstance(corrections, list):
        print("批量文件格式错误: corrections 必须是数组")
        return
    success = 0
    failed = 0
    invalid = 0

    for item in corrections:
        if not isinstance(item, dict):
            invalid += 1
            continue

        if is_new_format:
            # 新格式: {seq, quota_id, quota_name, name(可选)}
            name = str(item.get("name", "")).strip()
            if not name:
                # 没有清单名称则跳过（无法存入经验库）
                invalid += 1
                print(f"  跳过: 缺少 name 字段 (seq={item.get('seq', '?')})")
                continue
            quota_id = str(item.get("quota_id", "")).strip()
            quota_name = str(item.get("quota_name", "")).strip()
            if not quota_id:
                invalid += 1
                continue
            quota_ids = [quota_id]
            # 保持 quota_names 和 quota_ids 长度一致
            quota_names = [quota_name] if quota_name else [""]
        else:
            # 旧格式: {name, quota_ids, quota_names, ...}
            name = str(item.get("name", "")).strip()
            quota_ids = item.get("quota_ids", [])
            quota_names = item.get("quota_names", [])
            if not name or not isinstance(quota_ids, list) or not quota_ids:
                invalid += 1
                continue
            if not isinstance(quota_names, list):
                quota_names = []

        ok = store_one(
            name=name,
            desc=item.get("description", ""),
            quota_ids=quota_ids,
            quota_names=quota_names,
            reason=item.get("reason", ""),
            specialty=item.get("specialty", ""),
            province=province,
            confirmed=confirmed,
        )
        if ok:
            success += 1
        else:
            failed += 1

    print(f"\n批量存入完成: 成功{success} 失败{failed} 非法{invalid} 共{len(corrections)}条")


def lookup(name: str):
    """查看经验库中某条清单的现有记录"""
    exp_db = ExperienceDB()
    bill_text = name.strip()

    # 精确查找
    results = exp_db.find_experience(bill_text)
    if results:
        print(f"找到 {len(results)} 条记录:")
        for r in results:
            print(f"  [{r.get('source', '?')}] {r.get('bill_text', '')[:40]}")
            print(f"    定额: {r.get('quota_ids', [])}")
            print(f"    置信: {r.get('confidence', 0)}  省份: {r.get('province', '')}")
    else:
        print(f"未找到: {name}")


def main():
    parser = argparse.ArgumentParser(description="贾维斯纠正工具")
    parser.add_argument("--name", help="清单项名称")
    parser.add_argument("--desc", default="", help="清单项描述")
    parser.add_argument("--quota-ids", help="正确的定额编号列表(JSON格式)")
    parser.add_argument("--quota-names", default="[]", help="正确的定额名称列表(JSON格式)")
    parser.add_argument("--reason", default="", help="纠正原因")
    parser.add_argument("--specialty", default="", help="专业分类(如C7)")
    parser.add_argument("--province", default=None, help="省份")
    parser.add_argument("--file", help="批量纠正JSON文件路径")
    parser.add_argument("--lookup", help="查找清单项在经验库中的记录")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="静默模式，抑制模型加载进度条")

    args = parser.parse_args()

    # 静默模式：抑制 tqdm/transformers 的进度条
    if args.quiet:
        os.environ["TQDM_DISABLE"] = "1"
        os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"

    if args.lookup:
        lookup(args.lookup)
    elif args.file:
        store_batch(args.file, province=args.province)
    elif args.name and args.quota_ids:
        try:
            quota_ids = _parse_json_list(args.quota_ids, "quota_ids")
            quota_names = _parse_json_list(args.quota_names, "quota_names")
        except ValueError as e:
            parser.error(str(e))
        store_one(
            name=args.name,
            desc=args.desc,
            quota_ids=quota_ids,
            quota_names=quota_names,
            reason=args.reason,
            specialty=args.specialty,
            province=args.province,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
