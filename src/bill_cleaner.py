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

from loguru import logger

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

# 多芯软导线（R开头，按芯数和线径套"软导线"定额）
_SOFT_WIRE_MODELS = ['RVSP', 'RYJS', 'RVS', 'RVV', 'RVVP', 'RYJ']

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

    返回: "电线" / "软导线" / "电缆" / "光缆" / "双绞线" / None
    """
    text = f"{name} {desc}".upper()  # 统一大写匹配型号
    text_cn = f"{name} {desc}"       # 保留中文原文匹配关键词

    # 第1步：中文关键词匹配（光缆、双绞线等）
    for keywords, cable_type in _KEYWORD_MAP:
        for kw in keywords:
            if kw in text_cn:
                return cable_type

    # 第2步：型号前缀匹配（处理阻燃前缀如 WDZ-BYJ、WDZN-YJV 等）
    # 多芯软导线（优先于普通电线，因为 RVV 不能被 BV 误匹配）
    for model in _SOFT_WIRE_MODELS:
        if re.search(_CABLE_PREFIX + model + r'[\s\-\d]', text):
            return '软导线'

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


# ============================================================
# 项目上下文分析（L2：项目级感知）
# ============================================================

# 专业册号 → 中文简称（用于生成概览文本）
_SPECIALTY_SHORT_NAMES = {
    "C1": "机械设备", "C2": "热力设备", "C3": "静置设备",
    "C4": "电气", "C5": "智能化", "C6": "仪表",
    "C7": "通风空调", "C8": "工业管道", "C9": "消防",
    "C10": "给排水", "C11": "通信", "C12": "刷油防腐保温",
    "A": "土建", "D": "市政", "E": "园林",
}


def analyze_project_context(items: list[dict]) -> dict:
    """分析清单整体情况，生成项目级上下文

    在 clean_bill_items() 之后调用，利用已打好的专业标签和提取的参数，
    汇总成项目级信息。纯 Python 统计，不调 API。

    参数:
        items: clean_bill_items() 处理后的清单项列表

    返回:
        项目上下文字典，包含专业分布、参数范围、关键内容摘要等
    """
    from collections import Counter

    if not items:
        return {"total_items": 0}

    # 统计专业分布
    specialty_counter = Counter()
    for item in items:
        sp = item.get("specialty")
        if sp:
            specialty_counter[sp] += 1

    # 主专业（数量最多的）
    primary_sp = specialty_counter.most_common(1)[0][0] if specialty_counter else ""
    primary_sp_name = _SPECIALTY_SHORT_NAMES.get(primary_sp, "")

    # 收集参数分布
    dn_values = []    # 管径
    cable_sections = []  # 电缆截面
    for item in items:
        params = item.get("params", {})
        if not params:
            continue
        dn = params.get("dn")
        if dn:
            try:
                dn_values.append(int(float(dn)))
            except (ValueError, TypeError):
                pass
        cs = params.get("cable_section")
        if cs:
            try:
                cable_sections.append(float(cs))
            except (ValueError, TypeError):
                pass

    # 按名称关键词分组统计（生成"主要内容"摘要）
    key_items = _summarize_key_items(items)

    context = {
        "total_items": len(items),
        "primary_specialty": primary_sp,
        "primary_specialty_name": primary_sp_name,
        "specialty_distribution": dict(specialty_counter.most_common()),
        "dn_range": sorted(set(dn_values)) if dn_values else [],
        "cable_sections": sorted(set(cable_sections)) if cable_sections else [],
        "key_items": key_items,
    }

    logger.info(f"项目分析: {len(items)}条清单, "
                f"主专业={primary_sp_name}({primary_sp}), "
                f"涉及{len(specialty_counter)}个专业")

    return context


def _summarize_key_items(items: list[dict], max_groups: int = 8) -> list[str]:
    """按关键词分组统计清单内容，生成摘要列表

    把清单按常见造价类别分组，输出如：
    ["给水管道(DN25~DN100): 15条", "阀门: 8条", "电缆敷设: 12条"]
    """
    from collections import Counter

    # 分组关键词（按优先级匹配，命中第一个即停）
    group_patterns = [
        ("给水管道", ["给水管", "给水钢管", "给水塑料管"]),
        ("排水管道", ["排水管", "排水铸铁", "排水塑料"]),
        ("消防管道", ["消防管", "消火栓管", "喷淋管"]),
        ("采暖管道", ["采暖管", "供暖管", "地暖管"]),
        ("通风风管", ["风管", "风道", "风口"]),
        ("阀门", ["阀门", "闸阀", "蝶阀", "球阀", "止回阀", "截止阀", "减压阀"]),
        ("水泵", ["水泵", "循环泵", "加压泵", "排污泵"]),
        ("配电箱", ["配电箱", "配电柜", "开关柜", "控制箱"]),
        ("灯具", ["灯", "灯具", "照明"]),
        ("开关插座", ["开关", "插座", "面板"]),
        ("电缆敷设", ["电缆", "电力电缆"]),
        ("电线穿管", ["电线", "穿线", "导线", "BV", "BYJ"]),
        ("桥架", ["桥架", "线槽"]),
        ("消火栓", ["消火栓", "消防栓"]),
        ("灭火器", ["灭火器"]),
        ("喷淋头", ["喷淋头", "喷头", "洒水喷头"]),
        ("卫生器具", ["洗脸盆", "坐便器", "蹲便器", "小便器", "洗涤盆", "拖布池"]),
        ("管件", ["管件", "弯头", "三通", "法兰"]),
        ("保温", ["保温", "绝热"]),
        ("刷油防腐", ["刷油", "防腐", "油漆"]),
        ("电梯", ["电梯", "扶梯"]),
        ("套管", ["套管", "穿墙管"]),
        ("支架", ["支架", "吊架", "管卡"]),
    ]

    group_counter = Counter()
    group_dn_range = {}  # 记录每组的DN范围

    for item in items:
        name = item.get("name", "")
        desc = item.get("description", "") or ""
        text = f"{name} {desc}"

        matched_group = None
        for group_name, keywords in group_patterns:
            for kw in keywords:
                if kw in text:
                    matched_group = group_name
                    break
            if matched_group:
                break

        if matched_group:
            group_counter[matched_group] += 1
            # 记录该组的DN范围
            dn = (item.get("params") or {}).get("dn")
            if dn:
                try:
                    dn_val = int(float(dn))
                    if matched_group not in group_dn_range:
                        group_dn_range[matched_group] = [dn_val, dn_val]
                    else:
                        r = group_dn_range[matched_group]
                        r[0] = min(r[0], dn_val)
                        r[1] = max(r[1], dn_val)
                except (ValueError, TypeError):
                    pass

    # 生成摘要（按数量降序，取前 max_groups 个）
    summaries = []
    for group_name, count in group_counter.most_common(max_groups):
        dn_r = group_dn_range.get(group_name)
        if dn_r and dn_r[0] != dn_r[1]:
            summaries.append(f"{group_name}(DN{dn_r[0]}~DN{dn_r[1]}): {count}条")
        elif dn_r:
            summaries.append(f"{group_name}(DN{dn_r[0]}): {count}条")
        else:
            summaries.append(f"{group_name}: {count}条")

    return summaries


def format_project_overview(context: dict) -> str:
    """把项目上下文格式化成大模型可读的文本

    参数:
        context: analyze_project_context() 返回的字典

    返回:
        项目概览文本，用于注入到 Agent Prompt 中
    """
    total = context.get("total_items", 0)
    if total == 0:
        return ""

    primary = context.get("primary_specialty_name", "")
    primary_code = context.get("primary_specialty", "")
    dist = context.get("specialty_distribution", {})
    dn_range = context.get("dn_range", [])
    cable_secs = context.get("cable_sections", [])
    key_items = context.get("key_items", [])

    parts = []

    # 基本信息
    if primary:
        parts.append(f"本项目共{total}条清单，主专业为{primary}({primary_code})。")
    else:
        parts.append(f"本项目共{total}条清单。")

    # 专业分布
    if dist:
        dist_parts = []
        for code, count in dist.items():
            name = _SPECIALTY_SHORT_NAMES.get(code, code)
            dist_parts.append(f"{name}{count}条")
        parts.append("专业分布：" + "、".join(dist_parts) + "。")

    # 参数范围
    if dn_range:
        parts.append(f"管径范围：DN{dn_range[0]}~DN{dn_range[-1]}。")
    if cable_secs:
        parts.append(f"电缆截面：{cable_secs[0]}~{cable_secs[-1]}mm²。")

    # 主要内容
    if key_items:
        parts.append("主要内容：" + "、".join(key_items) + "。")

    # 一致性提示
    parts.append("同类清单请保持套定额一致。")

    return "\n".join(parts)
