"""
清单数据清洗模块
功能：
1. 从特征描述中提取真实名称（如"名称：Y型过滤器" → 替代项目名称"法兰"）
2. 调用 specialty_classifier 给每条清单打专业标签
3. 调用 text_parser 提取结构化参数
4. 线缆类型自动标签（电线/电缆/光缆/双绞线）

在匹配前对清单数据做预处理，让后续搜索和匹配更准确。

使用位置：在 main.py 读取清单后、匹配前调用
"""

import re
import sys
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.specialty_classifier import classify as classify_specialty
from src.specialty_classifier import parse_section_title
from src.text_parser import parser as text_parser


def _section_has_specialty(section: str) -> bool:
    """判断 section 标题能否识别出专业"""
    return parse_section_title(section) is not None


def clean_bill_items(items: list[dict]) -> list[dict]:
    """
    批量清洗清单项目列表

    清洗内容：
    1. 名称修正：如果特征描述中有"名称：xxx"，用xxx替代原名称（更准确）
    2. 专业分类：给每条清单打上专业册号标签
    3. 参数提取：解析DN、截面、电流、材质等结构化参数

    参数:
        items: bill_reader.read_excel() 返回的清单项列表

    返回:
        清洗后的清单项列表（原地修改并返回）
    """
    for item in items:
        name = item.get("name", "")
        desc = item.get("description", "") or ""
        section = item.get("section", "") or ""

        # 第1步：名称修正（从特征描述中提取真实名称）
        real_name = extract_real_name(name, desc)
        if real_name and real_name != name:
            item["original_name"] = name  # 保留原始名称
            item["name"] = real_name       # 用真实名称替代
            logger.debug(f"名称修正: '{name}' → '{real_name}'")

        # 第2步：专业分类
        # 优先用 section_title（分部标题），如果识别不了就用 sheet_name 兜底
        # 兜底条件：section为空 或 section是中文标题但识别不了专业（如"通头管件"错别字）
        # 不兜底：section是定额编号格式（如"C10-5-38"），因为"自动加定额"格式文件的
        #         section就是定额编号，不代表该清单项的专业
        section_for_classify = section
        if not _section_has_specialty(section):
            # section识别不了专业，判断是否该用 sheet_name 兜底
            # 定额编号格式（C开头+数字+横杠）不用兜底，其他情况可以
            is_quota_id = bool(section and re.match(r'^C\d+-', section))
            if not is_quota_id:
                sheet = item.get("sheet_name", "") or ""
                if sheet and _section_has_specialty(sheet):
                    section_for_classify = sheet

        classification = classify_specialty(
            item["name"], desc, section_title=section_for_classify
        )
        item["specialty"] = classification.get("primary")
        item["specialty_name"] = classification.get("primary_name")
        item["specialty_fallbacks"] = classification.get("fallbacks", [])
        item["specialty_confidence"] = classification.get("confidence")

        # 第3步：参数提取（如果还没有的话）
        if "params" not in item or not item["params"]:
            full_text = f"{item['name']} {desc}".strip()
            item["params"] = text_parser.parse(full_text)

        # 第4步：线缆类型标签（电线/电缆/光缆/双绞线）
        cable_type = _classify_cable_type(item["name"], desc)
        if cable_type:
            item["cable_type"] = cable_type

    # 统计清洗结果
    name_fixed = sum(1 for i in items if "original_name" in i)
    classified = sum(1 for i in items if i.get("specialty"))
    with_params = sum(1 for i in items if i.get("params"))
    cable_tagged = sum(1 for i in items if i.get("cable_type"))

    logger.info(f"清单清洗完成: {len(items)}条, "
                f"名称修正{name_fixed}条, 专业分类{classified}条, "
                f"有参数{with_params}条, 线缆标签{cable_tagged}条")

    return items


def extract_real_name(bill_name: str, description: str) -> str | None:
    """
    从特征描述中提取真实名称

    场景：清单的"项目名称"有时是笼统的（如"法兰安装"），
    但特征描述里有"名称：Y型过滤器"这样更具体的信息。
    用更具体的名称做搜索，匹配更准确。

    参数:
        bill_name: 清单项目名称
        description: 特征描述文字

    返回:
        更具体的名称，如果找不到则返回None
    """
    if not description:
        return None

    # 在特征描述中查找"名称：xxx"格式
    # 支持多种分隔符：冒号（中英文）、等号
    patterns = [
        r'名称[：:]\s*(.+?)(?:\n|$)',     # "名称：Y型过滤器"
        r'(?:^|\n)\d*[.、．]?\s*名称[：:]\s*(.+?)(?:\n|$)',  # "1.名称：Y型过滤器"
    ]

    for pattern in patterns:
        match = re.search(pattern, description)
        if match:
            real_name = match.group(1).strip()
            # 过滤掉太短或太长的（不太可能是有效名称）
            if 2 <= len(real_name) <= 30:
                # 检查是否和原名称差异较大（如果差不多就不替换了）
                if real_name != bill_name and real_name not in bill_name:
                    # 检查是否是纯型号（如APE-Z、AK-1、AP-RFM）
                    # 型号对搜索定额没用，"成套配电箱"这样的中文名称才有用
                    chinese_chars = sum(1 for c in real_name if '\u4e00' <= c <= '\u9fff')
                    if chinese_chars < len(real_name) / 2:
                        # 中文字符不到一半 → 是型号，不替换
                        logger.debug(f"跳过型号替换: '{bill_name}' 不替换为 '{real_name}'")
                        continue
                    return real_name

    return None


# ============================================================
# 线缆类型自动识别
# ============================================================

# 阻燃/耐火/低烟无卤等修饰前缀（不影响线缆类型判断）
_CABLE_PREFIX = r'(?:WDZ[A-Z]*-?|ZA[N]?-?|NH-?|N-?|ZR[A-E]?-?|ZC-?)*'

# 电线型号（B开头为主，按长度倒序避免短前缀误匹配）
_WIRE_MODELS = [
    'BLVV', 'BVVB', 'BLXF', 'BLV', 'BLX',
    'BVR', 'BVV', 'BXF', 'BXR', 'BYJ', 'BV', 'BX', 'BY',
]

# 按1根计算的电线（R开头）
_SINGLE_WIRE_MODELS = ['RVSP', 'RYJS', 'RVS', 'RVV', 'RVVP', 'RYJ']

# 电缆型号（按长度倒序）
_CABLE_MODELS = [
    # DJ系列（计算机电缆）
    'DJYPVP22', 'DJYPVRP22', 'DJYVP22', 'DJYVRP22',
    # BT系列（矿物绝缘电缆）
    'BBTRZ', 'BTTVZ', 'BTLY', 'BTTZ', 'TBTRZY',
    # YJ系列（交联电缆）
    'ZANYJFE', 'JKLYJV', 'JKRYJV', 'YJLV', 'YJFE', 'JKLYJ', 'JKRYJ',
    'JKYJ', 'KYJY', 'YJV', 'YJY', 'YJE',
    # KV系列（控制电缆）
    'KVVRP', 'KVVP', 'KVVR', 'KVV',
    # VV系列（电力电缆）
    'VV22', 'VV23', 'VV39', 'VLV', 'VV', 'VY',
    # JK系列（架空电缆）
    'JKLV', 'JKV',
    # HY系列（通信电缆）
    'HBYV', 'HYAT', 'HYA', 'HYV',
    # JH系列（防水电缆）
    'JHSB', 'JHS',
    # 其他
    'JYLY', 'YTTW', 'YZW', 'PYY', 'AVR', 'AV',
]

# 双绞线型号
_TWISTED_PAIR_MODELS = ['UTPCAT', 'SYKV', 'SYWV', 'SYV', 'UTP', 'CAT']

# 中文关键词匹配（优先级高于型号匹配）
_KEYWORD_MAP = [
    # 光缆（最先匹配，避免被"线"类误匹配）
    (['光纤', '光缆'], '光缆'),
    # 双绞线
    (['双绞线', '网线', '网络线'], '双绞线'),
]


def _classify_cable_type(name: str, desc: str) -> str | None:
    """
    从清单名称和特征描述中识别线缆类型

    返回: "电线" / "电线(单根)" / "电缆" / "光缆" / "双绞线" / None
    """
    text = f"{name} {desc}".upper()  # 统一大写匹配型号
    text_cn = f"{name} {desc}"       # 保留中文原文匹配关键词

    # 第1步：中文关键词匹配（光缆、双绞线等）
    for keywords, cable_type in _KEYWORD_MAP:
        for kw in keywords:
            if kw in text_cn:
                return cable_type

    # 第2步：型号前缀匹配（处理阻燃前缀如 WDZ-BYJ、WDZN-YJV 等）
    # 按1根计算的电线（优先于普通电线，因为 RVV 不能被 BV 误匹配）
    for model in _SINGLE_WIRE_MODELS:
        if re.search(_CABLE_PREFIX + model + r'[\s\-\d]', text):
            return '电线(单根)'

    # 电缆（优先于电线，因为电缆型号更长更具体）
    for model in _CABLE_MODELS:
        if re.search(_CABLE_PREFIX + model + r'[\s\-\d]', text):
            return '电缆'

    # 双绞线型号
    for model in _TWISTED_PAIR_MODELS:
        if re.search(model + r'[\s\-\d]', text):
            return '双绞线'

    # 普通电线（最后匹配，避免 BV 匹配到 KVVR 之类）
    for model in _WIRE_MODELS:
        if re.search(_CABLE_PREFIX + model + r'[\s\-\d]', text):
            return '电线'

    return None
