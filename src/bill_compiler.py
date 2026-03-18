"""
编清单编译器。

作用：
1. 标准清单 → passthrough（什么都不做，零回归风险）
2. GQI导出等非标清单 → 单位标准化 + 字段补全 + 标记
3. 自动匹配清单编码（9位国标编码）
4. 自动生成标准项目特征描述（按特征模板填空）

设计原则：
- 不改变现有链路，只做后处理
- 标准清单零影响
- 出问题可直接跳过，系统回到原来的状态
- 新增字段（standard_name/standard_unit/compiled_features），不覆盖原有字段
"""

from loguru import logger
from src.bill_code_matcher import match_bill_codes
from src.bill_feature_builder import build_features_batch, enrich_name_from_desc

# 确定无歧义的单位映射（造价行业里"只"和"个"可能不通用，第一版不转）
UNIT_MAP = {
    # 长度
    "米": "m", "M": "m",
    # 面积
    "㎡": "m2", "平方米": "m2", "平米": "m2", "m²": "m2",
    # 体积
    "m³": "m3", "立方米": "m3", "方": "m3",
    # 重量
    "千克": "kg", "公斤": "kg",
    "吨": "t",
}


def compile_items(items: list[dict], bill_version: str = "2024") -> list[dict]:
    """编译清单项：标准清单直接跳过，非标清单做轻量处理。

    参数:
        items: bill_reader 读出来的原始清单项列表
        bill_version: 清单版本（"2024"或"2013"），决定使用哪个版本的清单库

    返回:
        处理后的清单项列表（同一个列表，原地修改）
    """
    if not items:
        return items

    compiled = 0
    for item in items:
        source = item.get("source_type", "")

        # GQI导出 / 控制值等非标格式：读取阶段已处理，这里只做单位标准化
        if source in ("gqi_export", "control_sheet"):
            _normalize_unit(item)
            compiled += 1
            continue

        # 标准清单：标记 passthrough，不做任何修改
        item.setdefault("source_type", "standard_bill")
        item.setdefault("compile_action", "passthrough")

    if compiled > 0:
        logger.info(f"  编清单编译: {compiled}条非标项已处理, "
                    f"{len(items) - compiled}条标准项跳过")

    # 对名称太笼统的项（如"管道"、"阀门"），用描述中的材质/类型丰富名称
    enrich_name_from_desc(items)

    # 自动补全清单编码+项目特征（没有9位编码的清单项尝试匹配）
    match_bill_codes(items, bill_version=bill_version)

    # 自动生成标准项目特征描述（按特征模板+原料数据填空）
    build_features_batch(items)

    return items


def _normalize_unit(item: dict):
    """单位标准化：只做确定无歧义的映射。"""
    unit = item.get("unit", "")
    if unit in UNIT_MAP:
        item["unit"] = UNIT_MAP[unit]
        item.setdefault("compile_flags", []).append("unit_normalized")
