"""
编清单特征组装引擎。

核心职责：根据工程量原料数据 + 匹配到的清单编码，自动生成标准格式的项目特征描述。

两层架构：
1. 结构化中间层：从各种来源（GQI/汇总表/标准清单）提取统一的 feature_context
2. 渲染层：按特征模板逐项填值，输出编号格式的特征描述文本

设计原则：
- 没把握的字段留空，不硬猜
- 新增字段（compiled_features/standard_name/standard_unit），不覆盖原字段
- 复用现有 params（text_parser已解析的结构化参数）
"""

import re
from loguru import logger


# ============================================================
# 笼统名称 → 需要用材质辅助匹配的名称
# ============================================================

# 这些名称太笼统，单独拿去匹配清单库会命中错误结果
# 需要把描述中的材质信息提取出来，替换或拼接到名称中
_GENERIC_NAMES = {"管道", "管件", "通头管件", "管道附件", "设备"}

# 材质名称 → 标准清单名称的映射
# 工程量汇总表的材质字段（如"PPR冷水管"）不是清单规范名称，需要翻译
_MATERIAL_TO_BILL_NAME = {
    # PPR系列 → 塑料管
    "PPR": "塑料管", "PPR管": "塑料管", "PPR冷水管": "塑料管",
    "PPR热水管": "塑料管", "PPR给水管": "塑料管",
    # UPVC系列 → 塑料管
    "UPVC": "塑料管", "UPVC排水": "塑料管", "UPVC排水管": "塑料管",
    "UPVC管": "塑料管", "PVC-U": "塑料管", "PVC-U管": "塑料管",
    # PE系列 → 塑料管
    "PE": "塑料管", "PE管": "塑料管", "HDPE": "塑料管",
    "HDPE管": "塑料管", "HDPE双壁波纹管": "塑料管",
    # 复合管系列
    "PSP钢塑复合管": "复合管", "钢塑复合管": "复合管",
    "铝塑复合管": "复合管", "衬塑钢管": "复合管",
    # 钢管系列
    "镀锌钢管": "镀锌钢管", "焊接钢管": "焊接钢管",
    "无缝钢管": "无缝钢管", "不锈钢管": "不锈钢管",
    "薄壁不锈钢管": "不锈钢管",
    # 铸铁管
    "铸铁管": "铸铁管", "柔性铸铁管": "铸铁管", "球墨铸铁管": "铸铁管",
    # 铜管
    "铜管": "铜管", "紫铜管": "铜管",
}


def enrich_name_from_desc(items: list[dict]):
    """对名称太笼统的清单项，用描述中的材质信息丰富名称。

    例如：name="管道", desc="材质:PPR冷水管" → name改成"PPR冷水管"
    这样去匹配清单库时就能命中"塑料管"而不是乱匹配。

    只影响 name 字段（用于后续匹配），不影响原始数据的审计。
    原始名称保存在 original_generic_name 字段。
    """
    enriched = 0
    for item in items:
        name = item.get("name", "").strip()
        if name not in _GENERIC_NAMES:
            continue

        desc = item.get("description", "")
        material = _extract_from_desc(desc, "材质")
        item_type = _extract_from_desc(desc, "类型")

        # 阀门类：用"类型"来丰富（如"截止阀"、"闸阀"）
        if item_type:
            item["original_generic_name"] = name
            item["name"] = item_type
            enriched += 1
        # 管道/管件类：用"材质"来丰富，优先翻译成标准清单名称
        elif material:
            item["original_generic_name"] = name
            # 材质翻译：如"PPR冷水管"→"塑料管"，直接用清单库能匹配的名称
            bill_name = _MATERIAL_TO_BILL_NAME.get(material)
            if not bill_name:
                # 模糊匹配：材质可能带额外信息（如"PPR冷水管(S5)"）
                for mat_key, std_name in _MATERIAL_TO_BILL_NAME.items():
                    if mat_key in material:
                        bill_name = std_name
                        break
            item["name"] = bill_name or material
            enriched += 1

    if enriched > 0:
        logger.info(f"  名称丰富: {enriched}条笼统名称已用材质/类型替换")


# ============================================================
# 系统类型 → 介质 翻译表
# ============================================================

SYSTEM_TO_MEDIUM = {
    "给水系统": "给水",
    "生活给水": "给水",
    "热水系统": "给水-热水",
    "生活热水": "给水-热水",
    "冷水系统": "给水-冷水",
    "废水系统": "污水",
    "排水系统": "排水",
    "污水系统": "污水",
    "雨水系统": "雨水",
    "虹吸雨水": "雨水",
    "消防系统": "消防",
    "消火栓系统": "消防",
    "自动喷淋": "消防-喷淋",
    "喷淋系统": "消防-喷淋",
    "采暖系统": "采暖",
    "供暖系统": "采暖",
    "燃气系统": "燃气",
    "通风系统": "通风",
    "空调系统": "空调",
    "冷凝水": "冷凝水",
    "中水系统": "中水",
}


# ============================================================
# 第1层：提取统一的 feature_context（从各种来源标准化）
# ============================================================

def extract_feature_context(item: dict) -> dict:
    """从清单项中提取统一的特征上下文。

    不管来源是GQI导出、汇总表还是标准清单，都输出同一个结构化字典。
    优先用结构化字段（GQI/汇总表已解析的），降级用 params（text_parser解析的）。

    返回:
        {
            "system_type": "给水系统",    # 系统类型（原始值）
            "medium": "给水",             # 介质（翻译后的值）
            "material": "PPR冷水管",      # 材质
            "spec": "DN25",               # 规格型号
            "connection": "热熔连接",     # 连接方式
            "install_location": "",       # 安装部位（室内/室外/埋地等）
            "calc_item": "管道",          # 计算项目
            "item_type": "",              # 类型（阀门/设备的子类型）
            "item_name": "",              # 额外名称（如配电箱型号）
            "original_name": "PPR冷水管", # 原始名称
        }
    """
    ctx = {
        "system_type": "",
        "medium": "",
        "material": "",
        "spec": "",
        "connection": "",
        "install_location": "",
        "calc_item": "",
        "item_type": "",
        "item_name": "",
        "original_name": item.get("name", ""),
    }

    desc = item.get("description", "")
    params = item.get("params", {})
    section = item.get("section", "")

    # --- 系统类型和介质 ---
    # GQI/汇总表：description里有"系统:给水系统"
    system_type = _extract_from_desc(desc, "系统") or section
    ctx["system_type"] = system_type
    ctx["medium"] = _translate_medium(system_type)

    # --- 材质 ---
    # GQI/汇总表：description里有"材质:PPR冷水管"
    ctx["material"] = _extract_from_desc(desc, "材质") or ""
    # 降级：从params中取
    if not ctx["material"] and params:
        mat = params.get("material", "")
        if mat:
            ctx["material"] = mat

    # --- 规格 ---
    ctx["spec"] = _extract_from_desc(desc, "规格") or ""
    if not ctx["spec"] and params:
        # 从params取DN/De
        dn = params.get("DN")
        de = params.get("De")
        section_val = params.get("section")  # 截面积
        if dn:
            ctx["spec"] = f"DN{dn}"
        elif de:
            ctx["spec"] = f"De{de}"
        elif section_val:
            ctx["spec"] = f"{section_val}mm²"

    # --- 连接方式 ---
    # "敷设/连接" 标签含斜杠，需要特殊处理
    ctx["connection"] = (
        _extract_from_desc(desc, "敷设/连接")
        or _extract_from_desc(desc, "连接方式")
        or _extract_from_desc(desc, "连接")
        or _extract_from_desc(desc, "安装方式")
        or ""
    )
    if not ctx["connection"] and params:
        conn = params.get("connection", "")
        if conn:
            ctx["connection"] = conn

    # --- 计算项目 ---
    ctx["calc_item"] = _extract_from_desc(desc, "计算项目") or ""

    # --- 类型/名称 ---
    ctx["item_type"] = _extract_from_desc(desc, "类型") or ""
    ctx["item_name"] = _extract_from_desc(desc, "名称") or ""

    return ctx


def _extract_from_desc(desc: str, label: str) -> str:
    """从描述字符串中提取指定标签的值。

    描述格式通常是 "系统:给水系统 / 材质:PPR冷水管 / 规格:DN25"
    分隔符是 " / "（两边有空格），注意标签本身可能含"/"（如"敷设/连接"）。
    """
    if not desc:
        return ""
    # 先尝试精确匹配标签（不转义标签中的/，因为标签可能含/）
    # 值到下一个 " / "（两边有空格的斜杠）或行尾
    pattern = rf'{re.escape(label)}\s*[:：]\s*(.+?)(?:\s+/\s+|$)'
    m = re.search(pattern, desc)
    if m:
        return m.group(1).strip()
    return ""


def _translate_medium(system_type: str) -> str:
    """把系统类型翻译成介质。"""
    if not system_type:
        return ""
    # 精确匹配
    if system_type in SYSTEM_TO_MEDIUM:
        return SYSTEM_TO_MEDIUM[system_type]
    # 模糊匹配（系统类型可能带前缀，如"1F-给水系统"）
    for key, medium in SYSTEM_TO_MEDIUM.items():
        if key in system_type:
            return medium
    return ""


# ============================================================
# 第2层：按特征模板填值 + 渲染
# ============================================================

# 特征字段名 → 从feature_context取值的规则
# 返回值：字符串（有值）或 空字符串（没把握，留空）
def _fill_安装部位(ctx: dict) -> str:
    return ctx.get("install_location", "")

def _fill_介质(ctx: dict) -> str:
    return ctx.get("medium", "")

def _fill_材质(ctx: dict) -> str:
    return ctx.get("material", "")

def _fill_规格(ctx: dict) -> str:
    return ctx.get("spec", "")

def _fill_材质_规格(ctx: dict) -> str:
    """合并材质和规格（清单规范里经常把这两个合在一起写）"""
    mat = ctx.get("material", "")
    spec = ctx.get("spec", "")
    if mat and spec:
        return f"{mat} {spec}"
    return mat or spec or ""

def _fill_连接形式(ctx: dict) -> str:
    return ctx.get("connection", "")

def _fill_类型(ctx: dict) -> str:
    return ctx.get("item_type", "")

def _fill_名称(ctx: dict) -> str:
    # 对于设备/阀门类，"名称"特征指的是子类型名称
    return ctx.get("item_type") or ctx.get("item_name", "")

def _fill_安装方式(ctx: dict) -> str:
    return ctx.get("connection", "")

def _fill_型号(ctx: dict) -> str:
    return ctx.get("spec", "")

def _fill_型号_规格(ctx: dict) -> str:
    return ctx.get("spec", "")


# 特征字段名 → 填值函数
# 只覆盖能可靠填写的字段，其他留空
FEATURE_FILL_RULES = {
    "安装部位": _fill_安装部位,
    "介质": _fill_介质,
    "材质": _fill_材质,
    "规格": _fill_规格,
    "材质、规格": _fill_材质_规格,
    "材质、压力等级": _fill_材质_规格,
    "规格、压力等级": _fill_规格,
    "连接形式": _fill_连接形式,
    "连接方式": _fill_连接形式,
    "连接形式、焊接方法": _fill_连接形式,
    "类型": _fill_类型,
    "名称": _fill_名称,
    "安装方式": _fill_安装方式,
    "型号": _fill_型号,
    "型号、规格": _fill_型号_规格,
    "规格、型号": _fill_型号_规格,
    "材质、规格": _fill_材质_规格,
    "材质、类型": _fill_材质,
    "敷设方式": _fill_连接形式,
    "接口形式": _fill_连接形式,
}


def build_compiled_features(feature_template: list[str],
                            feature_context: dict) -> list[dict]:
    """按特征模板逐项填值，返回结构化结果。

    参数:
        feature_template: 特征字段名列表（来自 bill_features_2024.json 的 features）
        feature_context: 统一的特征上下文（extract_feature_context 的输出）

    返回:
        [
            {"name": "安装部位", "value": "室内", "source": "context"},
            {"name": "介质", "value": "给水", "source": "context"},
            {"name": "材质、规格", "value": "PPR冷水管 DN25", "source": "context"},
            {"name": "连接形式", "value": "热熔连接", "source": "context"},
            {"name": "管卡材质", "value": "", "source": "blank"},
            ...
        ]
    """
    result = []
    for field_name in feature_template:
        fill_func = FEATURE_FILL_RULES.get(field_name)
        if fill_func:
            value = fill_func(feature_context)
            source = "context" if value else "blank"
        else:
            value = ""
            source = "blank"

        result.append({
            "name": field_name,
            "value": value,
            "source": source,
        })

    return result


def render_features_text(compiled_features: list[dict]) -> str:
    """把结构化特征渲染成标准格式文本。

    输出格式（和清单规范一致）：
        1.安装部位:室内
        2.介质:给水
        3.材质、规格:PPR冷水管 DN25
        4.连接形式:热熔连接

    没有值的字段也保留（方便用户手动补充），显示为"5.管卡材质:"
    """
    lines = []
    for i, feat in enumerate(compiled_features, 1):
        name = feat["name"]
        value = feat.get("value", "")
        lines.append(f"{i}.{name}:{value}")
    return "\n".join(lines)


# ============================================================
# 批量处理入口（供 bill_compiler 调用）
# ============================================================

def build_features_batch(items: list[dict]):
    """批量为清单项生成标准项目特征描述。

    对每条item，如果有 bill_match（匹配到了清单编码），
    就根据特征模板 + 原料数据生成标准格式的项目特征。

    结果写入 item 的新字段：
    - item["compiled_features"]: 标准格式的项目特征文本
    - item["compiled_feature_items"]: 结构化特征列表
    - item["standard_name"]: 标准项目名称
    - item["standard_unit"]: 标准计量单位

    不覆盖原有字段（name/description/unit保持不变）。
    """
    if not items:
        return

    built = 0
    for item in items:
        bill_match = item.get("bill_match")
        if not bill_match:
            continue

        # 特征模板（骨架）
        feature_template = bill_match.get("features", [])

        # 标准名称和标准单位
        std_name = bill_match.get("name", "")
        std_unit = bill_match.get("unit", "")
        if std_name:
            item["standard_name"] = std_name
        if std_unit:
            item["standard_unit"] = std_unit

        # 没有特征模板的（非安装编码/2013版），跳过特征生成
        if not feature_template:
            continue

        # 提取统一的特征上下文
        ctx = extract_feature_context(item)

        # 按模板填值
        compiled = build_compiled_features(feature_template, ctx)
        item["compiled_feature_items"] = compiled

        # 渲染成文本
        item["compiled_features"] = render_features_text(compiled)
        built += 1

    if built > 0:
        logger.info(f"  特征组装: {built}条清单项已生成标准项目特征")


# ============================================================
# 分部分项分组（供导出Excel时插入标题行）
# ============================================================

# 计算项目 → 二级分部标题的映射
_CALC_ITEM_TO_SECTION = {
    "管道": "管道",
    "管件": "管道附件",
    "通头管件": "管道附件",
    "阀门": "管道附件",
    "套管": "支架及其他",
    "支吊架": "支架及其他",
    "管道附件": "管道附件",
    "设备": "设备",
}


def group_items_by_section(items: list[dict]) -> list[dict]:
    """按系统类型+计算项目分组，插入分部标题行。

    返回一个新列表，其中穿插了标题行（带 _is_title 标记）：
    [
        {"_is_title": True, "_level": 1, "_title": "给水系统"},
        {"_is_title": True, "_level": 2, "_title": "管道"},
        {正常清单项...},
        {正常清单项...},
        {"_is_title": True, "_level": 1, "_title": "排水系统"},
        ...
    ]
    """
    if not items:
        return items

    result = []
    current_system = None   # 当前一级分部（系统类型）
    current_sub = None      # 当前二级分部（管道/管道附件/支架等）
    seq = 0                 # 清单项全局序号

    for item in items:
        # 确定一级分部（系统类型）
        system = item.get("section", "") or ""

        # 确定二级分部（从原始笼统名称或计算项目推断）
        orig_name = item.get("original_generic_name", "")
        calc_item = ""
        desc = item.get("description", "")
        if orig_name:
            calc_item = _CALC_ITEM_TO_SECTION.get(orig_name, orig_name)
        elif desc:
            ci = _extract_from_desc(desc, "计算项目")
            if ci:
                calc_item = _CALC_ITEM_TO_SECTION.get(ci, ci)

        # 插入一级分部标题（系统类型变了）
        if system and system != current_system:
            current_system = system
            current_sub = None  # 重置二级
            result.append({"_is_title": True, "_level": 1, "_title": system})

        # 插入二级分部标题（计算项目变了）
        if calc_item and calc_item != current_sub:
            current_sub = calc_item
            result.append({"_is_title": True, "_level": 2, "_title": calc_item})

        # 正常清单项
        seq += 1
        item["_seq"] = seq  # 全局序号
        result.append(item)

    return result
