# -*- coding: utf-8 -*-
"""
搜索 query 构建器 — 从 text_parser.py 拆分

功能：将清单名称+特征描述 转换为 定额搜索 query
核心函数：build_quota_query(parser, name, description)

设计：通过参数接收 parser 实例（调用 parser.parse()），不导入 text_parser，
避免循环依赖。
"""

import json
import re
from pathlib import Path

from loguru import logger

_LAMP_RULE_EXCLUDE_PATTERN = r"灯杆|灯塔|路灯基础|灯槽|灯箱|灯带槽"

# 清单编码过滤正则（WMSGCS001001、050402001001 等编码会污染搜索词）
_BILL_CODE_PATTERN = re.compile(
    r'[A-Za-z]{4,}\d{6,}'   # 字母前缀+数字后缀（WMSGCS001001）
    r'|\b\d{10,12}\b'       # 10-12位纯数字编码（050402001001）
)

# ===== 工程同义词表（清单常用名 → 定额库常用名） =====
# 只加载一次，后续复用缓存
_SYNONYMS_CACHE = None


def _load_synonyms() -> dict:
    """加载工程同义词表（手工表 + 自动挖掘表合并，惰性加载）

    合并规则：手工表优先覆盖自动表（人工审核的更可靠）。
    自动表由 tools/synonym_miner.py 生成，开关由 config.AUTO_SYNONYMS_ENABLED 控制。
    """
    global _SYNONYMS_CACHE
    if _SYNONYMS_CACHE is not None:
        return _SYNONYMS_CACHE

    base_path = Path(__file__).parent.parent / "data"

    # 1. 加载手工同义词表（必须存在）
    manual = _load_synonym_file(base_path / "engineering_synonyms.json")

    # 2. 加载自动挖掘的同义词表（可选，开关控制）
    auto = {}
    try:
        import config as _cfg
        auto_enabled = getattr(_cfg, 'AUTO_SYNONYMS_ENABLED', True)
    except ImportError:
        auto_enabled = True

    if auto_enabled:
        auto = _load_synonym_file(base_path / "auto_synonyms.json")

    # 3. 合并：自动的先放，手工的覆盖（手工优先）
    merged = {}
    merged.update(auto)
    merged.update(manual)

    # 按key长度降序排列，优先匹配长词（避免"PE管"先于"HDPE管"匹配）
    _SYNONYMS_CACHE = dict(
        sorted(merged.items(), key=lambda x: len(x[0]), reverse=True)
    )
    return _SYNONYMS_CACHE


def _load_synonym_file(path: Path) -> dict:
    """从单个JSON文件加载同义词映射（内部工具函数）"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # 过滤掉说明字段和空值，只保留有效的同义词映射
        return {
            k: v[0] for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, list) and v
        }
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.debug(f"同义词表加载失败 {path.name}（不影响基础搜索）: {e}")
        return {}


_SPECIALTY_SCOPE_CACHE = None  # 专业适用范围缓存


def _load_specialty_scope() -> dict:
    """加载同义词的专业适用范围（带缓存，只读一次文件）

    从 engineering_synonyms.json 的 _specialty_scope 字段读取。
    格式: {"镀锌钢管": ["C10", "C8"], "砖基础": ["A"]}
    没有记录的同义词 = 全专业通用（向后兼容）。
    """
    global _SPECIALTY_SCOPE_CACHE
    if _SPECIALTY_SCOPE_CACHE is not None:
        return _SPECIALTY_SCOPE_CACHE

    base_path = Path(__file__).parent.parent / "data"
    try:
        with open(base_path / "engineering_synonyms.json", "r", encoding="utf-8") as f:
            raw = json.load(f)
        scope = raw.get("_specialty_scope", {})
        # 兼容旧格式（_specialty_scope 是字符串 "install" 而不是字典）
        if not isinstance(scope, dict):
            _SPECIALTY_SCOPE_CACHE = {}
        else:
            _SPECIALTY_SCOPE_CACHE = scope
    except Exception:
        _SPECIALTY_SCOPE_CACHE = {}
    return _SPECIALTY_SCOPE_CACHE


def _is_synonym_applicable(key: str, specialty: str, scope: dict) -> bool:
    """判断某条同义词是否适用于当前专业

    规则：
    - scope 中没有该 key → 全专业通用，返回 True
    - scope 中有该 key → 只在指定专业列表中生效
    - specialty 为空（无分类信息）→ 全部适用（兼容旧调用）
    """
    if not specialty:
        return True  # 无专业信息时全部适用
    if key not in scope:
        return True  # 没打标签 = 全专业通用
    # 有标签，检查当前专业是否在列表中（精确匹配，避免C1和C10混淆）
    allowed = scope[key]
    spec_upper = specialty.upper()
    for allowed_spec in allowed:
        if spec_upper == allowed_spec.upper():
            return True
    return False


def _apply_synonyms(query: str, specialty: str = "") -> str:
    """应用工程同义词扩展：在原词基础上追加定额常用名

    策略：**追加而非替换**，保留原词不丢失信息。
    BM25对短文本敏感，替换会丢掉原词导致召回下降。

    例如：
      "镀锌钢管 DN25" → "镀锌钢管 焊接钢管 镀锌 DN25"（原词保留）
      "PPR管 热熔连接" → "PPR管 PP-R管 热熔连接"（原词保留）

    特殊情况（自映射）：key==value的条目不追加（无意义），
    但仍通过break阻止更短的key误匹配长词子串（"保护伞"机制）。

    参数:
        query: 搜索query字符串
        specialty: 清单所属专业册号（如"C10"、"A"等）
            按 _specialty_scope 过滤：有标签的只对指定专业生效，
            没标签的全专业通用。
    """
    synonyms = _load_synonyms()
    if not synonyms:
        return query

    scope = _load_specialty_scope()

    # 按key长度从长到短排序，确保"水泵接合器"优先于"水泵"匹配
    # 避免短key误匹配长词的子串（如"水泵"匹配"水泵接合器"中的子串）
    sorted_items = sorted(synonyms.items(), key=lambda x: len(x[0]), reverse=True)

    for key, replacement in sorted_items:
        if key in query:
            # 防止重复：如果扩展目标已经出现在query中，停止搜索
            # 例如：query="消防水泵接合器"，key="水泵接合器"→已包含，跳过
            # 用break而非continue：继续搜索可能命中更短的key（如"水泵"→"离心泵"），
            # 误替换长词的子串（已排序保证长key优先，此处命中说明概念已被覆盖）
            if replacement in query:
                break
            if _is_synonym_applicable(key, specialty, scope):
                # 自映射（key==value）是"保护伞"，不追加内容，只通过break阻断
                if key != replacement:
                    # 追加扩展词到query末尾，保留原词不丢信息
                    query = f"{query} {replacement}"
                break  # 只做一次扩展，避免连锁追加导致query过长

    return query
_SPECIAL_LAMP_PATTERN = r"紫外|杀菌|消毒|舞台|投光|泛光|景观|水下|地埋|航空障碍|手术|无影|植物|补光|洗墙|轨道"


def _get_desc_field(fields: dict, target: str) -> str:
    """从描述字段字典中模糊查找目标字段值

    extract_description_fields 的 key 经常含有清单名碎片前缀，
    如 "钢阀门 名称" 而非 "名称"。这里用后缀/子串匹配来容错。
    """
    # 精确匹配
    if target in fields:
        return fields[target]
    # 后缀/子串匹配（key 可能含前缀噪声）
    for k, v in fields.items():
        if k.endswith(target) or target in k:
            return v
    return ""


def _extract_desc_equipment_type(fields: dict, bill_name: str) -> str:
    """从描述字段提取设备具体类型，追加到搜索query帮助BM25精准命中

    清单名称经常是泛称（碳钢阀门、消声器、管道绝热、桥架），
    而描述的"名称"/"类型"等字段包含具体设备名（风管防火阀、片式消声器）。
    提取这些关键词追加到query，让BM25能搜到正确定额。

    例：
      名称="成品风管防火阀" → "风管防火阀"
      名称="XZP100片式消声器" → "片式消声器"
      类型="槽式" → "槽式"
      绝热材料品种="B1级闭孔橡塑管壳" → "橡塑管壳"
      安装形式="沿砖混结构明敷（屋面）" → "沿砖混结构明敷"
    """
    # 按优先级遍历候选字段
    for field_key in ("名称", "类型", "绝热材料品种", "安装形式"):
        value = _get_desc_field(fields, field_key)
        if not value or len(value) < 2:
            continue

        # 截断后续字段：单行格式时value经常包含"名称:XX 规格:YY 阀体代号:ZZ"
        # 只取第一段（在分号或"标签:"处截断）
        cleaned = re.split(r'[;；,，]|\s+\S{2,6}[：:]', value)[0].strip()

        # 去掉前缀修饰词（成品/成套等）、型号代号、括号内容
        cleaned = re.sub(r'^(成品|成套|配套)\s*', '', cleaned)
        cleaned = re.sub(r'[A-Z][A-Z0-9]{2,}[-]?\d*\s*', '', cleaned).strip()
        cleaned = re.sub(r'[（(][^)）]*[)）]', '', cleaned).strip()
        # 去掉等级前缀（如"B1级"）和修饰词（如"闭孔"）
        cleaned = re.sub(r'[A-Z]\d+级', '', cleaned).strip()
        cleaned = re.sub(r'闭孔|开孔', '', cleaned).strip()

        if len(cleaned) < 2:
            continue

        # 避免重复/噪声：精细判断desc_type和bill_name的关系
        if cleaned == bill_name:
            continue
        # bill_name是cleaned的子串 → 提取差异部分作为修饰词
        # 例：bill_name="消声器", cleaned="片式消声器" → 提取"片式"
        if bill_name in cleaned:
            diff = cleaned.replace(bill_name, "").strip()
            if len(diff) >= 2:
                cleaned = diff
            else:
                continue
        # cleaned是bill_name的子串 → 完全包含则跳过
        elif cleaned in bill_name:
            continue
        else:
            # 两者无子串关系 → 用Jaccard字符相似度判断是否同一设备的不同叫法
            # 相似度高（如"报警联动一体机"≈"火灾自动报警系统控制主机"）→ 跳过
            # 相似度低（如"碳钢阀门"≠"风管防火阀"）→ 有用，保留
            bill_chars = {c for c in bill_name if '\u4e00' <= c <= '\u9fff'}
            desc_chars = {c for c in cleaned if '\u4e00' <= c <= '\u9fff'}
            if bill_chars and desc_chars:
                jaccard = len(bill_chars & desc_chars) / len(bill_chars | desc_chars)
                if jaccard >= 0.25:
                    continue

        # 截断过长的值（避免噪声污染query）
        return cleaned[:15]

    return ""


def _format_number_for_query(value: float) -> str:
    """数值格式化：整数去小数点，小数保留原样。"""
    # 用 modulo 避免浮点精度问题（如 25.0 == int(25.0) 在某些情况可能不成立）
    return str(int(value)) if value % 1 == 0 else str(value)


def extract_description_fields(description: str) -> dict:
    """
    从清单特征描述中提取标签-值字段

    清单描述格式通常是：
      1.名称:APE-Z
      2.回路数:7回路
      3.安装方式:底距地1.3m安装

    返回:
        字典 {"名称": "APE-Z", "回路数": "7回路", ...}
    """
    fields = {}
    # 匹配 "数字.标签:值" 或 "数字.标签：值" 格式
    for match in re.finditer(r'\d+[.、．]\s*([^:：\n]+)[：:]\s*([^\n]*)', description):
        label = match.group(1).strip()
        value = match.group(2).strip()
        if value and value != "详见图纸" and not value.startswith("详见"):
            fields[label] = value
    # 也匹配没有序号的 "标签:值" 格式
    if not fields:
        for match in re.finditer(r'([^:：\n]{2,6})[：:]\s*([^\n]+)', description):
            label = match.group(1).strip()
            value = match.group(2).strip()
            if value:
                fields[label] = value
    return fields


def _extract_distribution_box_fields(description: str) -> dict:
    """提取配电箱/配电柜描述中的关键标签，兼容 // 风格。"""
    if not description:
        return {}

    fields = {}
    for label, value in re.findall(
        r'(名称|型号规格|规格型号|型号|规格|安装方式)[：:]\s*(.*?)(?=(?://|//|；|;|\n|$))',
        description,
    ):
        cleaned = value.strip().strip("/；;，,")
        if cleaned and cleaned != "详见图纸" and not cleaned.startswith("详见"):
            fields.setdefault(label, cleaned)
    return fields


def _clean_distribution_box_text(value: str) -> str:
    if not value:
        return ""

    cleaned = value.strip().strip("/；;，,")
    cleaned = re.sub(r'[（(][^)）]*(只计安装|详见|图纸|自带)[^)）]*[)）]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    if cleaned in {"详见图纸", "详见设计图纸", "根据设计图纸综合考虑", "综合考虑", "非标"}:
        return ""
    return cleaned


def _extract_distribution_box_model(value: str) -> str:
    cleaned = _clean_distribution_box_text(value)
    if not cleaned:
        return ""

    candidates = re.findall(r'[A-Za-z0-9#/_\-.]{2,}', cleaned)
    for token in candidates:
        upper = token.upper()
        if upper.startswith("IP") and any(ch.isdigit() for ch in upper[2:]):
            continue
        return token
    return ""


def _normalize_distribution_box_name(value: str) -> str:
    cleaned = _clean_distribution_box_text(value)
    if not cleaned:
        return ""

    cleaned = re.sub(r'^成套配电箱(?!安装)', '成套配电箱安装 ', cleaned)
    cleaned = re.sub(r'^成套配电柜(?!安装)', '成套配电柜安装 ', cleaned)
    cleaned = re.sub(
        r'((?:配电箱|配电柜|控制箱|电表箱))([A-Za-z0-9#/_\-.]{2,})$',
        r'\1 \2',
        cleaned,
    )
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _extract_distribution_box_half_perimeter_mm(full_text: str,
                                                spec_text: str,
                                                params: dict) -> float | None:
    """配电箱半周长只在显式半周长或规格尺寸存在时使用，避免误吃安装高度。"""
    if "半周长" in full_text:
        value = params.get("half_perimeter")
        if value:
            return float(value)

    spec_source = spec_text or full_text
    size_match = re.search(r'(\d+(?:\.\d+)?)\s*[*×xX]\s*(\d+(?:\.\d+)?)', spec_source)
    if size_match:
        width = float(size_match.group(1))
        height = float(size_match.group(2))
        return width + height

    return None


def _bucket_distribution_box_half_perimeter(half_perimeter_mm: float | None) -> str:
    if not half_perimeter_mm:
        return ""

    buckets = (
        (500, "0.5m"),
        (1000, "1.0m"),
        (1500, "1.5m"),
        (2500, "2.5m"),
        (3000, "3.0m"),
    )
    for upper, label in buckets:
        if half_perimeter_mm <= upper:
            return label
    return buckets[-1][1]


def _build_distribution_box_query(name: str,
                                  description: str,
                                  full_text: str,
                                  fields: dict,
                                  params: dict,
                                  specialty: str = "") -> str | None:
    """配电箱/配电柜对象模板：优先具体箱名/型号，其次安装方式+半周长模板。"""
    is_box_item = (
        ("配电箱" in name and "杆上" not in name)
        or name.strip() in {"配电柜", "成套配电柜", "成套配电柜安装"}
    )
    if not is_box_item:
        return None

    box_fields = dict(fields)
    box_fields.update(_extract_distribution_box_fields(description))

    explicit_family = re.search(r'((?:高压|低压)?成套配电[箱柜](?:安装)?)\s*([A-Za-z0-9#/_\-.]{2,})?', description)
    if explicit_family:
        family = explicit_family.group(1)
        if not family.endswith("安装"):
            family += "安装"
        model = _extract_distribution_box_model(explicit_family.group(2) or "")
        if model or "配电柜" in family or family.startswith(("高压", "低压")):
            return f"{family} {model}".strip()

    box_name = _normalize_distribution_box_name(
        box_fields.get("名称", "") or box_fields.get("规格型号", "") or box_fields.get("型号规格", "")
    )
    model = _extract_distribution_box_model(box_fields.get("型号", ""))
    spec_text = _clean_distribution_box_text(box_fields.get("规格", ""))

    if not model:
        loose_model_match = re.search(
            r'^(?:配电箱|配电柜|成套配电箱安装|成套配电柜安装)\s+([A-Za-z0-9#/_\-.]{2,})\b',
            description,
        )
        if loose_model_match:
            model = loose_model_match.group(1)

    generic_names = {
        "配电箱",
        "配电柜",
        "成套配电箱",
        "成套配电箱安装",
        "成套配电柜",
        "成套配电柜安装",
    }
    if box_name:
        query = box_name
        if model and model not in query:
            query = f"{query} {model}"
        if query not in generic_names:
            return query

    if model:
        base = "配电柜" if "配电柜" in name else "配电箱"
        return f"{base} {model}"

    install_text = _clean_distribution_box_text(box_fields.get("安装方式", ""))
    if not install_text:
        for candidate in ("明装", "暗装", "落地", "落地式", "嵌入", "嵌入式", "壁挂", "挂墙", "悬挂"):
            if candidate in full_text:
                install_text = candidate
                break

    if "落地" in install_text:
        return "成套配电箱安装 落地式"

    half_perimeter_mm = _extract_distribution_box_half_perimeter_mm(full_text, spec_text, params)
    bucket = _bucket_distribution_box_half_perimeter(half_perimeter_mm)
    if bucket:
        return f"成套配电箱安装 悬挂、嵌入式 半周长{bucket}"

    if any(keyword in install_text for keyword in ("明装", "暗装", "嵌入", "悬挂", "壁挂", "挂墙")):
        return "成套配电箱安装 悬挂、嵌入式"

    return "成套配电箱安装"



def _build_garden_plant_query(name: str, full_text: str, specialty: str = "") -> str | None:
    """园林苗木类 query 构建：优先用土球/裸根等分档特征，避免误走 DN 管道路由。"""
    if not any(keyword in name for keyword in ("栽植乔木", "起挖乔木", "栽植灌木", "起挖灌木")):
        return None

    normalized_name = _normalize_bill_name(name)

    soil_ball_match = re.search(r'土球(?:直径)?[^\d]{0,4}(\d+)', full_text)
    if soil_ball_match:
        size = soil_ball_match.group(1)
        return _apply_synonyms(f"{normalized_name} 土球直径{size}cm以内", specialty)

    if "裸根" in full_text:
        if "乔木" in name:
            diameter_match = re.search(r'(?:米径|胸径|干径)[^\d]{0,4}(\d+)', full_text)
            if diameter_match:
                size = diameter_match.group(1)
                return _apply_synonyms(f"{normalized_name} 裸根 米径{size}cm以内", specialty)
            return _apply_synonyms(f"{normalized_name} 裸根", specialty)

        if "灌木" in name:
            crown_match = re.search(r'冠丛高[^\d]{0,4}(\d+)', full_text)
            if crown_match:
                size = crown_match.group(1)
                return _apply_synonyms(f"{normalized_name} 裸根 冠丛高{size}cm以内", specialty)
            return _apply_synonyms(f"{normalized_name} 裸根", specialty)

    return _apply_synonyms(normalized_name, specialty)


def _build_cable_head_query(name: str, full_text: str, params: dict,
                            specialty: str = "") -> str | None:
    """电缆头对象模板：拦截电缆终端头/中间头类清单，构建精确搜索词。

    处理的对象类型：
    1. 压铜接线端子（清单名"电力电缆头"但描述含"压铜接线端子"）
    2. 矿物绝缘电缆头（BTLY/YTTW/BTTRZ/BTTZ/BBTRZ型号）
    3. 控制电缆终端头（按芯数分档 ≤6/14/24/37/48）
    4. 电力电缆终端头（默认1kV干包式铜芯，按截面分档）
    5. 中间头（电力电缆中间头，按截面分档）

    为什么需要模板：
    - "电力电缆头"被_normalize_bill_name转成"电缆终端头"后直接搜索，
      缺少电压等级/工艺/材质等关键限定词，BM25容易匹配到10kV或热缩式
    - 控制电缆头需要按芯数分档，和电力电缆按截面分档完全不同
    - 矿物绝缘电缆头有专用定额，不能走普通电缆头路由

    注意：直接返回query，不经过_apply_synonyms。
    原因：同义词表有"电缆终端头→电力电缆终端头制作安装"，会给控制电缆头
    追加"电力电缆"关键词导致BM25偏向电力电缆定额。模板已构建精确搜索词，
    不需要同义词扩展。
    """
    # 触发条件：清单名含"电缆头"或"终端头"或"中间头"
    is_cable_head = any(kw in name for kw in ("电缆头", "终端头", "中间头"))
    if not is_cable_head:
        return None

    upper_text = full_text.upper()

    # --- 步骤1：压铜接线端子（浙江特例） ---
    # 清单名"电力电缆头"但描述里明确写"压铜接线端子"
    if "压铜接线端子" in full_text:
        # 提取截面：从"规格:16mm2"或"NxN"格式
        section = params.get("cable_section")
        if section:
            section_str = _format_number_for_query(section)
            return f"压铜接线端子 导线截面 {section_str}"
        return "压铜接线端子"

    # --- 步骤2：矿物绝缘电缆头 ---
    # 型号含BTLY/BTTRZ/BTTZ/YTTW/BBTRZ/NG-A → 矿物绝缘电缆专用定额
    _MINERAL_MODELS = ("BTTRZ", "BTLY", "BTTZ", "YTTW", "BBTRZ", "NG-A")
    is_mineral = any(m in upper_text for m in _MINERAL_MODELS)
    if is_mineral:
        # 矿物绝缘分控制/电力两种
        if "控制" in full_text:
            # 提取芯数
            core_match = re.search(r'(\d+)\s*[×xX*]\s*\d+', full_text)
            core_count = int(core_match.group(1)) if core_match else None
            query = "矿物绝缘控制电缆终端头"
            if core_count:
                query += f" 芯数 {core_count}"
            return query
        else:
            # 矿物绝缘电力电缆头
            section = params.get("cable_section")
            query = "矿物绝缘电力电缆终端头"
            if section:
                section_str = _format_number_for_query(section)
                query += f" 截面 {section_str}"
            return query

    # --- 步骤3：控制电缆头 ---
    # 清单名含"控制"，或描述中含"控制电缆"
    is_control = "控制" in name or "控制" in full_text
    # 信号电缆也按控制电缆计
    if not is_control:
        is_control = "信号" in name
    if is_control:
        # 提取芯数：从"规格:6芯以下"、"14芯内"、"4*1.5"等格式
        core_count = None
        # 格式1：直接写"N芯"
        core_direct = re.search(r'(\d+)\s*芯', full_text)
        if core_direct:
            core_count = int(core_direct.group(1))
        else:
            # 格式2：从"NxN"提取（第一个数字是芯数）
            core_match = re.search(r'(\d+)\s*[×xX*]\s*\d+(?:\.\d+)?', full_text)
            if core_match:
                core_count = int(core_match.group(1))

        # 中间头 vs 终端头
        if "中间" in full_text:
            query = "控制电缆中间头"
        else:
            query = "控制电缆终端头"
        if core_count:
            query += f" 芯数 {core_count}"
        return query

    # --- 步骤4/5：电力电缆终端头/中间头 ---
    # 提取电压等级（默认1kV）
    voltage = "1kV"
    if any(kw in full_text for kw in ("10kV", "10KV", "10kv")):
        voltage = "10kV"
    elif any(kw in full_text for kw in ("35kV", "35KV", "35kv")):
        voltage = "35kV"
    # "0.6/1KV"、"1KV以下"、"1kv"都是1kV

    # 提取工艺类型（干包/热缩/浇注）
    if "热缩" in full_text or "冷缩" in full_text or "热(冷)缩" in full_text or "热（冷）缩" in full_text:
        craft = "热(冷)缩式"
    elif "浇注" in full_text:
        craft = "浇注式"
    else:
        # 1kV默认干包式（最常见），10kV默认热缩式
        craft = "干包式" if voltage == "1kV" else "热(冷)缩式"

    # 提取室内/室外（默认室内）
    location = "室内"
    if "室外" in full_text or "户外" in full_text:
        location = "室外"

    # 提取截面
    section = params.get("cable_section")

    # 中间头 vs 终端头
    if "中间" in name or "中间" in full_text:
        query = f"{voltage}以下{location}电力电缆中间头"
        if section:
            section_str = _format_number_for_query(section)
            query += f" 截面 {section_str}"
        return query

    # 电力电缆终端头（最常见的case）
    # 搜索词格式："1kV以下室内干包式铜芯电力电缆终端头 截面 N"
    query = f"{voltage}以下{location}{craft}铜芯电力电缆终端头"
    if section:
        section_str = _format_number_for_query(section)
        query += f" 截面 {section_str}"
    return query


def _build_valve_query(name: str, full_text: str, params: dict,
                       specialty: str = "") -> str | None:
    """阀门族对象模板：拦截需要特殊路由的阀门类清单。

    处理的对象类型：
    1. 通风类阀门（防火阀/调节阀/排烟阀）→ 周长路由（不走管道DN路由）
    2. 特殊设备（倒流防止器/自动排气阀/减压孔板）→ 专用搜索词
    3. 过滤器 → 螺纹阀门（定额按螺纹阀门计）
    4. 软接头 → 按连接方式分流（法兰/螺纹）
    5. 电热熔法兰套件 → 塑料法兰
    6. 管道阀门（闸阀/蝶阀/球阀等泛称）→ 按连接方式+DN分流

    为什么需要模板：
    - "碳钢阀门 名称：280℃防火阀"不拦截会被管道路由误改为"法兰阀门安装"
    - 闸阀/蝶阀等泛称在定额库中搜不到，必须规范化为法兰/螺纹阀门
    - 过滤器/软接头/倒流防止器等有独立的定额体系
    """
    # --- 前置检查：是否含阀门相关关键词 ---
    # 消声百叶有独立定额"消声百叶安装"，不走阀门路由
    if "消声百叶" in name:
        return None
    if not any(kw in name for kw in ("阀门", "阀", "过滤器", "软接头", "倒流防止")):
        return None

    # --- 提取真实设备名（清单常在"名称："或"类型："后给具体设备名） ---
    # 例如 "碳钢阀门 名称：280℃防火阀" → real_type = "280℃防火阀"
    # 例如 "螺纹阀门 类型:截止阀 规格:DN32" → real_type = "截止阀"
    real_type = ""
    rt_match = re.search(
        r'(?:名称[、,]?类型|名称|类型)[：:]\s*(.+?)(?:\s+(?:规格|压力|名称|类型)|$)',
        full_text)
    if rt_match:
        real_type = rt_match.group(1).strip()
        real_type = re.sub(r'-超高$', '', real_type).strip()  # 去掉超高后缀

    dn = params.get("dn")
    connection = params.get("connection", "")

    # 清单名的基础部分（去掉"名称：xxx"/"类型：xxx"后缀）
    # "碳钢阀门 名称：280℃防火阀" → "碳钢阀门"
    _name_base = re.split(r'\s+(?:名称|类型|规格)', name)[0].strip()

    # === 1. 通风类阀门拦截（防火阀/调节阀/排烟阀） ===
    # 这些走周长分档（WxH→周长），不走DN分档
    # 不拦截会被管道路由覆盖成"法兰阀门安装"，导致搜索完全偏离
    _vent_kw = ("防火阀", "排烟阀", "调节阀", "排烟口", "排烟防火", "风量调节")
    _vent_check = real_type if real_type else _name_base
    # 排除水系统调节阀（动态平衡阀/电动调节阀等），这些按管道阀门处理
    _not_vent = ("动态平衡", "静态平衡", "压差", "温控", "恒温", "比例")
    if any(kw in _vent_check for kw in _vent_kw) and not any(ex in _vent_check for ex in _not_vent):
        # 清理真实设备名：去温度（280℃）、去型号前缀（MEE-、FVD-）
        vent_name = real_type or _name_base
        vent_name = re.sub(r'\d+℃', '', vent_name)
        vent_name = re.sub(r'^[A-Za-z]+-', '', vent_name).strip()
        # 风量调节阀/多叶调节阀 → 多叶调节阀安装
        if "多叶" in vent_name or "对开" in vent_name or "风量调节" in vent_name:
            return _apply_synonyms("多叶调节阀安装 周长", specialty)
        return _apply_synonyms("防火调节阀安装 周长", specialty)

    # 通风止回阀（有周长参数的止回阀→风管止回阀，与管道止回阀用DN的不同）
    _stop_check = real_type if real_type else _name_base
    if "止回阀" in _stop_check:
        perimeter = params.get("perimeter")
        if perimeter or specialty == "C7":
            return _apply_synonyms("风管止回阀安装 周长", specialty)
        # 无周长且不是C7专业 → 不拦截，走后续管道阀门路由

    # C7通风空调的"碳钢阀门"/"阀门"无具体名称时，通常是防火阀/调节阀
    if specialty == "C7" and _name_base in ("碳钢阀门", "阀门", "金属阀门"):
        return _apply_synonyms("防火调节阀安装 周长", specialty)

    # === 2. 特殊设备（有专用定额，不走通用阀门路由） ===
    _check = real_type or _name_base

    # 倒流防止器 → "倒流防止器组成与安装(连接方式)"
    if "倒流防止" in _check:
        _dn_val = int(dn) if dn else 50
        if "螺纹" in connection:
            conn = "螺纹连接"
        elif "法兰" in connection:
            conn = "法兰连接"
        else:
            conn = "螺纹连接" if _dn_val < 50 else "法兰连接"
        return _apply_synonyms(f"倒流防止器组成与安装({conn})", specialty)

    # 自动排气阀/快速排气阀 → "自动排气阀"
    if "排气阀" in _check:
        return _apply_synonyms("自动排气阀", specialty)

    # 减压孔板 → "减压孔板"
    if "减压孔板" in _check:
        return _apply_synonyms("减压孔板", specialty)

    # === 3. 过滤器 → 按类型和DN分流 ===
    if "过滤器" in _check:
        # 空气过滤器/油过滤器等设备类有专用定额，不按阀门处理
        if any(prefix in _check for prefix in ("空气", "油", "活性炭", "初效", "中效", "高效")):
            return None  # 交给后续逻辑保留原名搜索
        # Y形/管道过滤器：小口径按螺纹阀门，大口径按法兰阀门
        _dn_val = int(dn) if dn else 25  # 过滤器默认小口径
        if _dn_val >= 50:
            return _apply_synonyms("法兰阀门安装", specialty)
        return _apply_synonyms("螺纹阀门", specialty)

    # === 4. 软接头 → 按连接方式分流 ===
    if "软接头" in name:
        _dn_val = int(dn) if dn else 50
        if "法兰" in connection:
            conn_type = "法兰连接"
        elif "螺纹" in connection:
            conn_type = "螺纹连接"
        else:
            conn_type = "法兰连接" if _dn_val >= 50 else "螺纹连接"
        return _apply_synonyms(f"软接头({conn_type})", specialty)

    # === 5. 电热熔法兰套件 → 塑料法兰 ===
    if "法兰套件" in _check or "电热熔法兰" in _check:
        return _apply_synonyms("塑料法兰(带短管)安装(热熔连接)", specialty)

    # === 6. 管道阀门 → 不在模板中处理，交给后续管道路由 ===
    # 管道路由会保留 location/usage/connection 等上下文（如"室内消防法兰阀门安装"），
    # 模板直接返回会丢失这些修饰词导致搜索不够精准。
    # 管道阀门（闸阀/蝶阀/碳钢阀门等）的规范化由 build_quota_query 中
    # 原有的管道路由代码（lines 925+）处理。
    return None


def _normalize_bill_name(name: str) -> str:
    """
    清单名称 → 定额搜索名称的规范化

    清单用通俗名称，定额用专业术语，两者经常不同。
    例如：
      清单"LED圆形吸顶灯" → 定额"普通灯具安装 吸顶灯"
      清单"电力电缆头"     → 定额"电缆终端头"
      清单"直管LED灯"      → 定额"荧光灯安装"（LED直管灯套荧光灯定额）
    """
    # 电缆头 → 电缆终端头（定额中叫"终端头"，不是"电缆头"）
    if "电缆头" in name and "终端" not in name:
        return name.replace("电力电缆头", "电缆终端头").replace("电缆头", "电缆终端头")

    # 注意：焊接法兰阀门/螺纹法兰阀门的处理已移至 build_quota_query 管道路由中
    # 那里先执行，所以这里不再重复处理

    # 灯具类：去掉"LED"前缀和瓦数/电压等噪声（定额不按光源和瓦数分类）
    if "灯" in name and not re.search(_LAMP_RULE_EXCLUDE_PATTERN, name):
        cleaned = re.sub(r'LED\s*', '', name, flags=re.IGNORECASE)
        # 去掉瓦数（12W、2×28W、1*28W等）和电压（220V等）—— 定额不按这些分类
        cleaned = re.sub(r'\d+[×*]\d+\s*W', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\d+\s*W\b', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\d+\s*V\b', '', cleaned, flags=re.IGNORECASE)
        # 去掉"T5"等灯管型号前缀
        cleaned = re.sub(r'\bT\d+\s*', '', cleaned)
        # 去掉"A型"消防等级标记（如"A型1*3W"中的"A型"，不影响定额选择）
        cleaned = re.sub(r'[A-Z]型', '', cleaned)
        # 去掉反斜杠及后续电气参数（如"\5W,DC≤36V,lm≥500"）
        cleaned = re.sub(r'\\[^\\]*$', '', cleaned)
        # 去掉流明参数（如"2400lm"、"lm≥500"）
        cleaned = re.sub(r'\d*lm[≥>=\d]*', '', cleaned, flags=re.IGNORECASE)
        # 去掉直流电压参数（如"DC≤36V"）
        cleaned = re.sub(r'DC[≤<>=]*\d+V?', '', cleaned, flags=re.IGNORECASE)
        # 清理多余空格、逗号和括号
        cleaned = re.sub(r'[,，]+', ' ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        cleaned = re.sub(r'\(\s*\)', '', cleaned).strip()
        cleaned = re.sub(r'\[\s*\]', '', cleaned).strip()

        # 特殊灯具优先保留原始语义，避免被通用灯具规则过度归类
        if re.search(_SPECIAL_LAMP_PATTERN, cleaned):
            return cleaned

        # ===== 按灯具类型映射到定额搜索名称 =====

        # 格栅灯 → 嵌入式灯具安装（不是荧光灯！"五头格栅灯"的"头"是光源数不是管数）
        if "格栅灯" in cleaned:
            return "嵌入式灯具安装"

        # 吸顶灯 → 普通灯具安装 吸顶灯（含防水式吸顶灯）
        if "吸顶灯" in cleaned:
            if "防水" in cleaned or "防尘" in cleaned:
                return "防水防尘灯安装 吸顶式"
            return "普通灯具安装 吸顶灯"

        # 壁灯 → 壁灯安装
        if "壁灯" in cleaned:
            if "防水" in cleaned or "防尘" in cleaned:
                return "防水防尘灯安装 壁灯"
            return "壁灯安装 小型壁灯"

        # 防爆灯 → 仅"防爆密闭"走密闭灯，其他防爆灯走荧光灯安装
        # 原因：很多省份"防爆荧光灯"走荧光灯定额，不走密闭灯定额
        if "防爆" in cleaned and "灯" in cleaned:
            if "密闭" in cleaned or "密封" in cleaned:
                return "密闭灯安装 防爆灯"
            # 防爆荧光灯 → 荧光灯具安装（带防爆修饰词帮BM25区分）
            return "荧光灯具安装 防爆"

        # 防水防尘灯：当同时有管数信息时，管数优先走荧光灯安装
        # 原因："防水型单管灯(管吊)"的正确定额是"荧光灯具安装 吊管式 单管"
        # 防水只是附加属性，安装工艺和普通荧光灯一样
        if ("防水" in cleaned or "防尘" in cleaned or "防潮" in cleaned) and "灯" in cleaned:
            tube_match = re.search(r'(单管|双管|三管)', cleaned)
            if tube_match:
                # 有管数 → 走荧光灯安装（管数优先于防水属性）
                install = "吸顶式"  # 默认
                if "吊链" in cleaned:
                    install = "吊链式"
                elif "吊管" in cleaned or "吊杆" in cleaned or "管吊" in cleaned or "吊装" in cleaned:
                    install = "吊管式"
                elif "嵌入" in cleaned:
                    install = "嵌入式"
                elif "壁装" in cleaned:
                    install = "壁装式"
                return f"荧光灯具安装 {install} {tube_match.group(1)}"
            return "防水防尘灯安装"

        # 线槽灯 → LED灯带 灯管式（线槽灯安装在线槽内，安装工艺接近LED灯带）
        if "线槽灯" in cleaned:
            return "LED灯带 灯管式"

        # 井道灯 → 密闭灯安装（井道用密闭灯）
        if "井道灯" in cleaned:
            return "密闭灯安装 防潮灯"

        # 荧光灯具：提取安装方式和管数（必须在"直管|灯管"之前，否则"荧光灯管"会被抢先匹配）
        if "荧光灯" in cleaned:
            # 提取管数
            tube_count = ""
            if "三管" in cleaned or "3管" in cleaned:
                tube_count = "三管"
            elif "双管" in cleaned or "2管" in cleaned:
                tube_count = "双管"
            else:
                tube_count = "单管"  # 默认单管
            # 提取安装方式
            install = "吸顶式"  # 默认
            if "吊链" in cleaned:
                install = "吊链式"
            elif "吊管" in cleaned or "吊杆" in cleaned or "吊装" in cleaned:
                install = "吊管式"
            elif "嵌入" in cleaned:
                install = "嵌入式"
            elif "壁装" in cleaned:
                install = "壁装式"
            elif "吸顶" in cleaned:
                install = "吸顶式"
            return f"荧光灯具安装 {install} {tube_count}"

        # 直管灯/灯管 → 荧光灯具安装（放在荧光灯之后，避免"荧光灯管"被抢先匹配丢失细节）
        if re.search(r'直管|灯管', cleaned):
            return "荧光灯具安装 单管"

        # 壁装/管吊/吊装的灯 → 根据安装方式推断荧光灯安装
        if re.search(r'壁装.*灯|灯.*壁装', cleaned):
            tube = "双管" if "双管" in cleaned else "单管"
            return f"荧光灯具安装 壁装式 {tube}"
        if re.search(r'管吊|吊装|吊链', cleaned) and "灯" in cleaned:
            tube = "双管" if "双管" in cleaned else ("三管" if "三管" in cleaned else "单管")
            install = "吊链式" if "吊链" in cleaned else "吊管式"
            return f"荧光灯具安装 {install} {tube}"

        # 感应灯/声光控灯 → 普通灯具安装
        if re.search(r'感应灯|声光控|光控', cleaned):
            return "普通灯具安装 吸顶灯"

        # 灯头座/灯头 → 座灯头安装
        if re.search(r'灯头座|座灯头', cleaned):
            return "其他普通灯具安装 座灯头"

        # 集中电源灯 → 智能应急灯具安装（需在疏散/标志灯之前判断）
        if "集中电源" in cleaned:
            if "指示" in cleaned or "标志" in cleaned:
                return "智能应急灯具及标志灯具安装 标志灯"
            # 集中电源疏散照明灯 → 吸顶式应急灯（不是嵌入式）
            if "疏散照明" in cleaned or "照明" in cleaned:
                return "智能应急灯具及标志灯具安装 应急灯 吸顶"
            return "智能应急灯具及标志灯具安装"

        # 应急灯/应急照明灯
        if "应急" in cleaned:
            # 消防应急照明灯 → 标志/诱导灯安装（不是荧光灯！）
            # 消防应急照明灯是消防系统的一部分，套标志灯定额
            if "消防" in cleaned:
                if "双面" in cleaned or "吊杆" in cleaned:
                    return "标志、诱导灯安装 吊杆式"
                if "壁" in cleaned or "单面" in cleaned:
                    return "标志、诱导灯安装 壁式"
                return "标志、诱导灯安装"
            # 应急+指示灯 → 标志灯（不是荧光灯）
            # 例如"应急疏散指示灯"是标志灯，不是照明灯
            if "指示" in cleaned:
                if "嵌入" in cleaned or "地面" in cleaned:
                    return "标志、诱导灯安装 地面嵌入式"
                if "吸顶" in cleaned:
                    return "标志、诱导灯安装 吸顶式"
                if "双面" in cleaned or "吊杆" in cleaned:
                    return "标志、诱导灯安装 吊杆式"
                if "壁" in cleaned or "单面" in cleaned:
                    return "标志、诱导灯安装 壁式"
                return "标志、诱导灯安装"
            if "吸顶" in cleaned:
                return "普通灯具安装 吸顶灯"
            if "疏散" in cleaned:
                return "标志、诱导灯安装"
            # 应急照明灯/其他应急灯 → 荧光灯具安装
            return "荧光灯具安装"

        # 疏散指示灯/标志灯/出口指示灯 → 标志、诱导灯安装
        if re.search(r'疏散|指示灯|标志灯|诱导灯|出口.*灯|楼层.*灯', cleaned):
            if "嵌入" in cleaned or "地面" in cleaned:
                return "标志、诱导灯安装 地面嵌入式"
            if "吸顶" in cleaned:
                return "标志、诱导灯安装 吸顶式"
            # 双面灯物理上必须悬挂展示，走吊杆式
            if "双面" in cleaned or "吊杆" in cleaned or "吊装" in cleaned:
                return "标志、诱导灯安装 吊杆式"
            if "壁" in cleaned or "单面" in cleaned:
                return "标志、诱导灯安装 壁式"
            return "标志、诱导灯安装 壁式"

        # 单管灯/双管灯/三管灯（不含"荧光"字样的简称）→ 荧光灯具安装
        tube_match = re.search(r'(单管|双管|三管)灯', cleaned)
        if tube_match:
            return f"荧光灯具安装 吸顶式 {tube_match.group(1)}"

        # 坡道灯/过渡照明灯/照明灯 → 普通灯具安装
        if re.search(r'照明灯|过渡灯|坡道.*灯', cleaned):
            return "普通灯具安装 吸顶灯"

        # 通用灯具兜底：保留cleaned（已去除LED/瓦数/电压噪声）
        return cleaned

    # 风口类归一化：防止"防雨百叶"被BM25搜到建筑"钢百叶窗"
    # "防雨百叶风口"/"单层百叶"/"格栅风口"→"百叶风口"
    if any(kw in name for kw in ("风口", "散流器", "喷口")):
        cleaned = name
        # 去掉尺寸噪声（如"800*150"、"φ200"）
        cleaned = re.sub(r'\d+\s*[*×xX]\s*\d+', '', cleaned).strip()
        cleaned = re.sub(r'[φΦ]\d+', '', cleaned).strip()
        # 防雨百叶/单层百叶/双层百叶 → 百叶风口
        if re.search(r'防雨.*百叶|百叶.*风口|单层.*百叶|双层.*百叶', cleaned):
            return "百叶风口"
        # 格栅风口 → 百叶风口（定额中按同一类计）
        if "格栅" in cleaned and "风口" in cleaned:
            return "百叶风口"
        return cleaned

    # 开关盒/插座盒 → 暗装开关(插座)盒（BM25容易把"开关盒"拆词搜到"刀型开关"）
    if re.search(r'开关盒|插座盒', name):
        if "连体" in name:
            return "暗装开关(插座)盒 连体"
        return "暗装开关(插座)盒"

    # N连体智能面板 → 多联组合开关插座 暗装
    # 广东清单格式："智能插座面板N连体：xx+xx+xx"
    if "连体" in name and ("插座" in name or "开关" in name or "面板" in name):
        return "多联组合开关插座 暗装"

    # 接线盒（86mm的小接线盒，不是通信用的大接线箱）
    if name == "接线盒":
        return "接线盒安装"

    # 配电箱：去掉编号噪声（AL、SGAT、7-AT-BDS1等），只保留"配电箱"
    # 不加"安装"后缀——加了会导致BM25匹配到"杆上配电设备安装 配电箱"
    # （因为"安装"+"配电箱"两词同时出现在该定额名中，BM25给了高分）
    # 只用"配电箱"反而更好，BM25会优先匹配以"配电箱"开头的定额
    if "配电箱" in name and "杆上" not in name:
        return "配电箱"

    return name


def build_quota_query(parser, name: str, description: str = "",
                      specialty: str = "",
                      bill_params: dict = None) -> str:
    """
    构建定额搜索query（模仿定额命名风格）

    管道类定额命名格式：
      {安装部位}{介质}{材质}({连接方式}) 公称直径(mm以内) {DN值}
    电气设备类定额命名格式：
      配电箱墙上(柱上)明装 规格(回路以内) 8
      电力电缆敷设 沿桥架敷设 截面(mm²以内) 70

    参数:
        parser: TextParser 实例（用于调用 parser.parse()）
        name: 清单项目名称（如"复合管"、"成套配电箱"）
        description: 清单项目特征描述
        specialty: 清单所属专业册号（如"C10"），用于同义词范围限定
        bill_params: 清单已清洗的参数字典（来自bill_cleaner）。
                     如果提供，优先使用；否则从文本重新提取。

    返回:
        构建好的搜索query
    """
    # 过滤清单编码（如 WMSGCS001001、HJBHCS001001、050402001001 等）
    # 这些编码混在特征描述中会污染搜索词，导致BM25搜不到正确定额
    original_name = name
    name = _BILL_CODE_PATTERN.sub('', name or '').strip() or original_name
    if description:
        description = _BILL_CODE_PATTERN.sub('', description).strip()
    description = description or ""

    full_text = f"{name} {description}".strip()
    # 优先使用清单清洗阶段已清洗的参数（如卫生器具已剔除DN）
    params = bill_params if bill_params is not None else parser.parse(full_text)

    # 提前提取描述字段（管道路由和通用路由都需要用）
    fields = extract_description_fields(description) if description else {}

    # 提取安装部位（室内/室外）
    location = ""
    loc_match = re.search(r'安装部位[：:]\s*(室内|室外|户内|户外)', full_text)
    if loc_match:
        location = loc_match.group(1)
        location = location.replace("户内", "室内").replace("户外", "室外")

    # 提取用途/介质（给水/排水/热水/消防/采暖等）
    usage = ""
    usage_match = re.search(r'介质[：:]\s*(给水|排水|热水|冷水|消防|蒸汽|采暖|通风|空调)', full_text)
    if usage_match:
        usage = usage_match.group(1)

    # 材质和连接方式从已提取的参数获取
    material = params.get("material", "")
    connection = params.get("connection", "")
    dn = params.get("dn")
    cable_section = params.get("cable_section")
    shape = params.get("shape", "")  # 风管形状：矩形/圆形

    # 补充提取连接方式：text_parser有时漏提取描述中的"连接方式:xxx"
    # 例如"连接方式:卡压式连接"在parser中未被识别，导致query丢失关键区分词
    if not connection:
        _conn_match = re.search(
            r'连接方式[：:]\s*'
            r'(卡压式?连接|环压式?连接|焊接|螺纹连接|法兰连接'
            r'|热熔连接|粘接|承插连接|沟槽连接|卡箍连接'
            r'|对接电弧焊|承插氩弧焊|对焊连接)',
            full_text)
        if _conn_match:
            connection = _conn_match.group(1)

    # ===== 管道类：有材质或DN参数，且不是电气类（电缆/配管/穿线） =====
    # 电气类即使有material/dn也应走下面的电气专用query构建
    # 灯具类也不走管道路由（描述中"保护管"等配件词会被误提取为材质）
    garden_plant_query = _build_garden_plant_query(name, full_text, specialty)
    if garden_plant_query:
        return garden_plant_query

    # 阀门族对象模板：在管道路由之前拦截，避免被管道路由误覆盖
    valve_query = _build_valve_query(name, full_text, params, specialty)
    if valve_query:
        return valve_query

    is_electrical = any(kw in name for kw in ("电缆", "配管", "穿线", "配线", "桥架", "线槽"))
    is_lamp = "灯" in name  # 灯具类走专用的_normalize_bill_name处理
    # 风口/喷口/散流器的φ值是开口直径，不是管道DN，不走管道路由
    is_wind_outlet = any(kw in name for kw in ("风口", "喷口", "散流器"))
    is_window_item = "窗" in name or "百叶" in name
    if (material or dn) and not is_electrical and not is_lamp and not is_wind_outlet and not is_window_item:
        # 阀门类清单名称规范化：清单常写"碳钢阀门"/"不锈钢阀门"等材质+阀门泛称，
        # 但定额名统一叫"法兰阀门安装"/"螺纹阀门安装"。直接在路由中替换，
        # 避免依赖_apply_synonyms（可能被其他同义词抢先匹配导致失效）
        _valve_materials = ("碳钢", "不锈钢", "铸铁", "铸钢", "合金钢", "铜")
        if "阀门" in name and any(m in name for m in _valve_materials):
            name = "法兰阀门安装"
            material = ""  # 材质已融入名称，不再单独拼接

        # 焊接法兰阀门/螺纹法兰阀门 → 焊接法兰阀安装/螺纹法兰阀安装
        # 清单写"焊接法兰阀门"，但很多省定额叫"焊接法兰阀安装"（无"门"字）
        # 不做此替换时，"焊接"+"法兰"会被BM25匹配到"碳钢法兰安装(焊接)"
        if "焊接法兰阀门" in name:
            name = name.replace("焊接法兰阀门", "焊接法兰阀安装")
            material = ""
        elif "螺纹法兰阀门" in name:
            name = name.replace("螺纹法兰阀门", "螺纹法兰阀安装")
            material = ""

        # 泛称阀门（闸阀/蝶阀/止回阀/球阀等）→ 按DN分流到法兰/螺纹阀门安装
        # 定额不按阀门类型（闸阀/蝶阀）分类，而是按连接方式（法兰/螺纹）分类
        # 大口径(DN≥50)通常法兰连接，小口径(DN<50)通常螺纹连接
        # "闸阀""蝶阀"在定额库中完全搜不到，必须替换
        _generic_valves = ("闸阀", "蝶阀", "止回阀", "球阀", "截止阀",
                           "防护闸阀", "涡流蝶阀", "浮球阀", "电磁阀",
                           "信号蝶阀", "减压阀", "安全阀", "平衡阀",
                           "排气阀", "放气阀", "放空阀")
        if any(v in name for v in _generic_valves) and "法兰" not in name and "螺纹" not in name:
            # 优先从描述中提取连接方式（覆盖清单名中的隐含连接方式）
            _conn = params.get("connection", "")
            if "法兰" in _conn:
                name = "法兰阀门安装"
            elif "螺纹" in _conn or "丝扣" in _conn:
                name = "螺纹阀门安装"
            elif "焊接" in _conn:
                name = "焊接法兰阀安装"
            elif "卡箍" in _conn or "沟槽" in _conn:
                name = "法兰阀门安装"  # 卡箍/沟槽连接按法兰计
            else:
                # 无明确连接方式时，按DN分流
                _dn_val = int(dn) if dn else 50
                if _dn_val >= 50:
                    name = "法兰阀门安装"
                else:
                    name = "螺纹阀门安装"
            material = ""

        # 连接方式矛盾修复：清单名写"螺纹阀门"但描述中实际是"法兰连接"
        # 例如："螺纹阀门 类型:蝶阀 连接方式:法兰" → 应走法兰阀门
        if "阀门" in name:
            _conn = params.get("connection", "")
            if "螺纹" in name and "法兰" in _conn:
                name = name.replace("螺纹阀门", "法兰阀门")
                name = name.replace("螺纹阀", "法兰阀门")
            elif "法兰" in name and ("螺纹" in _conn or "丝扣" in _conn):
                name = name.replace("法兰阀门", "螺纹阀门")

        # PPR/PP-R管 → 定额标准名称：
        # 清单写"PPR冷水管"/"PP-R管"，定额叫"室内塑料给水管(热熔连接)"或"采暖管道 室内塑料管(热熔连接)"
        # 直接替换为定额名，避免BM25因"PPR"匹配不到"塑料给水管"
        _mat_upper = material.upper() if material else ""
        if "PPR" in _mat_upper or "PP-R" in _mat_upper:
            _full = f"{name} {description}".upper()
            if "采暖" in _full or "热水" in _full or "暖" in _full:
                material = "室内塑料管(热熔连接)"
                if not usage:
                    usage = "采暖管道"
            else:
                # 冷水/给水/未指定用途 → 默认给水（PPR最常见用途）
                material = "室内塑料给水管(热熔连接)"

        # 给水不锈钢管（卡压/环压连接）→ C10定额标准名称：
        # 清单写"304薄壁不锈钢管"/"不锈钢管"，定额叫"给排水管道 室内薄壁不锈钢管(卡压连接)"
        # 不做此替换时，"不锈钢管"会被BM25匹配到C8工业管道"低压 不锈钢管件(电弧焊)"
        # 或C6仪表"不锈钢管缆"，因为它们名称更短、词频密度更高
        # 窄触发条件：不锈钢 + 卡压/环压 + 管类（不含阀门/管件/软管）
        _is_stainless = "不锈钢" in (material or "") or "不锈钢" in name
        _is_press_fit = connection and ("卡压" in connection or "环压" in connection)
        _is_pipe = "管" in name and not any(
            kw in name for kw in ("阀", "管件", "软管", "管缆"))
        if _is_stainless and _is_press_fit and _is_pipe:
            material = "室内薄壁不锈钢管(卡压连接)"
            connection = ""  # 连接方式已融入material
            if not usage or usage in ("给水", "热水", "冷水"):
                usage = "给排水管道"

        if material and "管" in material:
            core = f"{location}{usage}{material}"
        elif material:
            # 避免重复：如果name已包含材质词，不再拼接材质前缀
            if material in name:
                core = f"{location}{usage}{name}"
            else:
                core = f"{location}{usage}{material}{name}"
        else:
            core = f"{location}{usage}{name}"

        query_parts = []
        if connection:
            core += f"({connection})"
        query_parts.append(core)

        # 风管形状：加入"矩形风管"或"圆形风管"帮助BM25区分
        if shape and "风管" in name:
            query_parts.append(f"{shape}风管")

        if dn:
            query_parts.append(f"DN{dn}")

        if material and "管" in material and name and name != material:
            query_parts.append(name)

        # 从描述字段补充设备具体类型（清单名泛称时帮助BM25精准命中）
        desc_type = _extract_desc_equipment_type(fields, name)
        if desc_type:
            query_parts.append(desc_type)

        return _apply_synonyms(" ".join(query_parts), specialty)
    # ===== 电梯类：有电梯参数时构建专用搜索query =====
    # 例如 "6#客梯(高区)" 速度2.5m/s 26站 → "曳引式电梯 运行速度2m/s以上 层数站数"
    # 例如 "载货电梯" 速度1.0m/s 10站 → "载货电梯 运行速度2m/s以下 层数 站数"
    # 优先用提取的 elevator_type，不同类型对应不同定额家族
    elevator_speed = params.get("elevator_speed")
    elevator_stops = params.get("elevator_stops")
    if elevator_speed is not None and elevator_stops is not None:
        # 用提取的电梯类型（载货/液压/杂物等），避免全部误导到"自动电梯"家族
        elevator_type = params.get("elevator_type", "曳引式电梯")
        # 按速度分类构建搜索query，不带具体站数（BM25无法模糊匹配数字）
        # 让param_validator通过向上取档选择正确的站数档位
        speed_class = "运行速度2m/s以上" if elevator_speed > 2.0 else "运行速度2m/s以下"
        return f"{elevator_type}({speed_class}) 层数、站数"

    # ===== 周长类：风口/散流器/阀门等按周长取档的设备 =====
    # 这类设备没有材质/DN参数，但有周长参数（从规格如1200*100计算得来）
    # 在搜索词中加入"安装"和"周长"关键词，引导BM25匹配定额名中含
    # "XX安装 XX周长(mm) ≤XXXX"的子目，避免被无关的制作/材料定额干扰
    # 具体周长值的取档由param_validator完成
    perimeter = params.get("perimeter")
    # 按周长取档的设备：风口类 + 通风空调阀门类（防火阀/止回阀/调节阀等）
    # 注意：管道阀门（球阀/蝶阀/闸阀）用DN取档，不走此路由
    # 区分方式：管道阀门有DN参数无perimeter，通风阀门有perimeter（从WxH算来）
    is_perimeter_device = is_wind_outlet or any(kw in name for kw in (
        "防火阀", "止回阀", "调节阀", "排烟阀", "排烟口",
        "消声器", "消声", "出风口"))
    if perimeter and is_perimeter_device:
        normalized_name = _normalize_bill_name(name)
        # 从描述补充风口具体类型（如"旋流风口"→帮BM25区分散流器/旋转吹风口）
        desc_type = _extract_desc_equipment_type(fields, name)
        if desc_type:
            return _apply_synonyms(f"{normalized_name} {desc_type} 安装 周长", specialty)
        return _apply_synonyms(f"{normalized_name} 安装 周长", specialty)

    # ===== 非管道类：从描述中提取关键信息构建query =====
    # 电气设备、灯具、电缆、配管、配线等

    distribution_box_query = _build_distribution_box_query(
        name=name,
        description=description,
        full_text=full_text,
        fields=fields,
        params=params,
        specialty=specialty,
    )
    if distribution_box_query:
        return distribution_box_query

    # 电缆头模板：在_normalize_bill_name之前拦截，
    # 因为normalize会把"电缆头"→"电缆终端头"丢失原始信息
    cable_head_query = _build_cable_head_query(
        name=name,
        full_text=full_text,
        params=params,
        specialty=specialty,
    )
    if cable_head_query:
        return cable_head_query

    # 清单名称 → 定额搜索名称的规范化映射
    # 清单用的名称和定额用的名称经常不一样
    normalized_name = _normalize_bill_name(name)
    query_parts = [normalized_name]

    # --- 门窗类：将“金属（塑钢、断桥）窗/门”归一到定额常用的铝合金/塑钢窗门名称 ---
    # 典型题面只给泛称，具体材质与开启方式藏在描述里；不归一时 BM25 容易误打到“塑钢固定窗”。
    if ("金属（塑钢" in name or "金属(塑钢" in name) and ("窗" in name or "门" in name):
        material_text = f"{name} {description}"
        if "铝合金" in material_text or "断桥" in material_text:
            frame_material = "铝合金"
        elif "塑钢" in material_text:
            frame_material = "塑钢"
        else:
            frame_material = ""

        opening_type = ""
        if "窗" in name:
            for candidate in ("平开窗", "推拉窗", "固定窗", "百叶窗"):
                if candidate in material_text:
                    opening_type = candidate
                    break
            if not opening_type and frame_material:
                opening_type = "窗"
        elif "门" in name:
            for candidate in ("平开门", "推拉门", "地弹门"):
                if candidate in material_text:
                    opening_type = candidate
                    break
            if not opening_type and frame_material:
                opening_type = "门"

        if frame_material and opening_type:
            query_parts[0] = f"{frame_material}{opening_type}"
            if "附框" in material_text:
                query_parts.append("有附框")
            return _apply_synonyms(" ".join(query_parts), specialty)
    # --- 桥架类：清理尺寸噪声，构建桥架安装搜索词 ---
    # 清单写"热镀锌桥架100*50"，定额叫"钢制槽式桥架(宽+高)(mm以下) 200"
    # 100*50 的数字噪声会让 BM25 匹配到含"100×140"的混凝土结构定额
    if "桥架" in name and "配线" not in name and "穿线" not in name and "电缆" not in name:
        # 去掉尺寸数字（如"100*50"、"200*100"）和尾部连字符
        clean = re.sub(r'\d+\s*[*×xX]\s*\d+', '', name).strip()
        clean = re.sub(r'[-—_]+$', '', clean).strip()
        if not clean:
            clean = "桥架"
        query_parts[0] = clean + " 安装"
        return _apply_synonyms(" ".join(query_parts), specialty)

    if description:
        # fields 已在函数开头提取，这里直接使用

        # --- 配管类：材质代号→中文名称，配置形式→敷设方式 ---
        # 配管材质代号 → 定额库中的实际名称（必须与定额名完全一致）
        conduit_map = {
            "PC":  "PVC阻燃塑料管",    # C4-11-168~181
            "PVC": "PVC阻燃塑料管",    # 同PC
            "SC":  "焊接钢管",          # C4-11-23~70
            "G":   "镀锌钢管",          # C4-11-71~118
            "DG":  "镀锌钢管",          # 同G（电镀锌钢管）
            "RC":  "镀锌电线管",        # C4-11-1~22（水煤气管）
            "MT":  "镀锌电线管",        # 金属电线管
            "JDG": "紧定式薄壁钢管",    # C4-11-119~140
            "KBG": "紧定式薄壁钢管",    # 同JDG（扣压式）
            "FPC": "半硬质阻燃管",      # 半硬质PVC管
            "CT":  "桥架",              # 桥架归类另算
        }
        # 识别配管：清单名含"配管"、或含材质型号（SC管/JDG管等）
        conduit_keywords = ("配管", "SC管", "JDG管", "KBG管", "PVC管", "导管")
        is_conduit = (any(kw in name for kw in conduit_keywords)
                      and "穿线" not in name and "电缆" not in name)

        # --- 配管材质+配置形式+管径：fields.get经常失败，统一用正则从全文提取 ---
        if is_conduit:
            full_text = f"{name} {description}"
            normalized_conduit_text = full_text.upper().replace("KJG", "KBG")

            # 1. 材质型号：从全文提取SC/JDG/KBG/PC等代号
            conduit_code = None
            # 匹配配管材质代号（按长度降序，避免短代号抢先匹配）
            # G/RC/MT 是单/双字母代号，用\b边界防止从JDG/DG中误提取
            mat_match = re.search(
                r'(JDG|KBG|FPC|PVC|SC|PC|DG|RC|MT|G)(?:管)?\s*\d*',
                normalized_conduit_text)
            if mat_match:
                conduit_code = mat_match.group(1)

            if "金属软管" in full_text:
                query_parts[0] = "金属软管敷设"
            elif "可挠金属套管" in full_text:
                query_parts[0] = "可挠金属套管"
            # JDG/KBG是紧定式钢导管，和普通镀锌钢管是不同定额子目
            # 替换query_parts[0]让BM25能匹配"套接紧定式镀锌钢导管(JDG)"
            # 加"套接"关键词提升JDG条目的BM25分数（区别于普通镀锌钢管）
            elif conduit_code in ("JDG", "KBG"):
                guide_code = conduit_code
                query_parts[0] = f"套接紧定式钢导管{guide_code} 镀锌电线管 敷设"
            elif conduit_code in ("PC", "PVC"):
                query_parts[0] = "PVC阻燃塑料管敷设"
            elif conduit_code == "FPC":
                query_parts[0] = "半硬质阻燃管敷设"
            else:
                # SC=焊接钢管, G/DG=镀锌钢管, 分开写让BM25能精准命中
                if conduit_code == "SC":
                    query_parts[0] = "焊接钢管敷设"
                elif conduit_code in ("G", "DG"):
                    query_parts[0] = "镀锌钢管敷设"
                elif conduit_code in ("RC", "MT"):
                    query_parts[0] = "镀锌电线管敷设"
                else:
                    # 无材质代号时用通用"钢管敷设"
                    query_parts[0] = "钢管敷设"

            # 2. 配置形式：暗配/明配（加"砖混凝土结构"限定，避免匹配到"钢模板暗配"）
            config_match = re.search(
                r'配置形式[：:]\s*(.*?)(?:\s|含|工作|其他|$)',
                full_text)
            if config_match:
                config_raw = config_match.group(1)
                if "暗" in config_raw:
                    query_parts.append("砖混凝土结构暗配")
                elif "明" in config_raw:
                    query_parts.append("砖混凝土结构明配")

            # 3. 管径：从"SC25"、"JDG32"、"Φ20"或"规格:25"提取
            query_str = " ".join(query_parts)
            if "公称直径" not in query_str and "外径" not in query_str:
                # 先匹配材质代号后直接跟数字（SC25, JDG32, RC20, MT16, G20）或DN前缀（DN100）
                size_match = re.search(
                    r'(?:SC|JDG|KJG|KBG|PC|RC|MT|G|Φ|φ|DN|D)\s*(\d+)',
                    normalized_conduit_text, re.IGNORECASE)
                # 再匹配规格字段中的数字（规格:25, 规格:Φ20, 规格:DN25）
                if not size_match:
                    size_match = re.search(
                        r'规格[：:]\s*(?:Φ|φ|DN)?\s*(\d+)',
                        full_text)
                if size_match:
                    query_parts.append(f"公称直径 {size_match.group(1)}")

            # 配管query已构建完整（含材质+配置+管径），直接返回
            # 不走末尾的_apply_synonyms，避免"焊接钢管敷设"被同义词再加一次"敷设"
            # 但先补充名称/描述中的明配/暗配（配置形式字段已在上方处理，这里兜底关键词）
            if "明配" in full_text and "明配" not in " ".join(query_parts):
                query_parts.append("砖混凝土结构明配")
            elif "暗配" in full_text and "暗配" not in " ".join(query_parts):
                query_parts.append("砖混凝土结构暗配")
            return " ".join(query_parts)
        else:
            # 非配管类的材质提取（原逻辑保留）
            conduit_mat = fields.get("材质", "")
            if conduit_mat:
                for code in sorted(conduit_map, key=len, reverse=True):
                    if code in conduit_mat.upper():
                        query_parts.append(conduit_map[code])
                        break
            # 配置形式/敷设方式（原逻辑保留）
            config_form = fields.get("配置形式", "")
            if config_form:
                if "暗" in config_form:
                    query_parts.append("暗配")
                elif "明" in config_form:
                    query_parts.append("明配")

        # --- 配线类：导线型号→定额名称 ---
        wire_spec = fields.get("规格", "")
        if "穿线" in name or "配线" in name:
            # fields提取规格经常失败或带垃圾文字，统一用正则从全文提取电线型号
            # 匹配常见电线型号：WDZN-BYJ2.5, RVV2*1.0, BYJ-B1-4 等
            spec_match = re.search(
                r'((?:WDZ[A-Z0-9]*-|ZR[A-Z]?-|NH-)?'
                r'(?:BYJ|BV[R]?|BLV|RVV[P]?|RVS|RYJS[P]?)'
                r'[A-Z0-9.*×xX\-]*)',
                full_text.upper())
            if spec_match:
                wire_spec = spec_match.group(1)

            # 单芯线材质映射（BV/BYJ→铜芯，BLV→铝芯）
            single_core_map = {
                "BYJ": "铜芯", "BV": "铜芯", "BVR": "铜芯", "BLV": "铝芯",
            }
            # 多芯线型号（RVV/RYJS等→穿多芯软导线）
            multi_core_types = ("RVVP", "RVV", "RVS", "RYJS", "RYJSP")

            # 先剥离阻燃/耐火等前缀修饰符，暴露基础型号
            # 如 WDZCN-BYJ-B1-4 → BYJ-B1-4, ZR-BV2.5 → BV2.5
            wire_prefixes = [
                "WDZCB1N-", "WDZB1N-", "WDZCN-", "WDZBN-",
                "WDZC-", "WDZN-", "WDZ-",
                "ZRC-", "ZRB-", "ZR-", "NH-",
            ]
            wire_base = wire_spec.upper()
            for prefix in wire_prefixes:
                if wire_base.startswith(prefix):
                    wire_base = wire_base[len(prefix):]
                    break
            # 剥离耐火等级标识（如 BYJ-B1-4 → BYJ-4，B1是耐火等级不是截面）
            wire_base = re.sub(r'-?B[12](?=-|$)', '', wire_base).strip('-')

            # 判断线型：多芯 or 单芯
            is_multi_core = False
            wire_type_known = False  # 是否识别出已知线型
            core_material = "铜芯"
            for mtype in multi_core_types:
                if wire_base.startswith(mtype):
                    is_multi_core = True
                    wire_type_known = True
                    break
            if not is_multi_core:
                for code, material in single_core_map.items():
                    if wire_base.startswith(code):
                        core_material = material
                        wire_type_known = True
                        break

            # 导线截面提取
            wire_text = wire_base or wire_spec
            section = None
            # 优先匹配多芯格式：2×2.5 → 取单芯截面2.5
            wire_sec = re.search(r'(\d+)\s*[×xX*]\s*(\d+(?:\.\d+)?)', wire_text)
            if wire_sec:
                core_count = int(wire_sec.group(1))
                section = float(wire_sec.group(2))
                # 1*6 表示"1芯×6mm²"，是单芯线，不是多芯线
                # 只有芯数≥2才是真正的多芯线（如 2*2.5、4*1.5）
                if core_count >= 2:
                    is_multi_core = True
                    wire_type_known = True
            else:
                # 单芯：从型号尾部提取（如 BYJ4 → 4, BV2.5 → 2.5）
                wire_sec = re.search(r'(\d+(?:\.\d+)?)\s*$', wire_text)
                if wire_sec:
                    section = float(wire_sec.group(1))
            if section:
                query_parts.append(f"导线截面 {section:g}")

            # 构建搜索词：桥架配线 / 多芯软导线 / 照明线 / 动力线
            # 只有识别出已知线型(BYJ/BV/RVV等)才特化，否则保持原名（如UTP双绞线等弱电）
            if "桥架" in name or "线槽" in name or "桥架" in description or "线槽" in description:
                query_parts[0] = "线槽配线"
            elif wire_type_known and is_multi_core:
                query_parts[0] = "穿多芯软导线"
            elif wire_type_known and section and section > 6:
                query_parts[0] = f"穿动力线 {core_material}"
            elif wire_type_known:
                query_parts[0] = f"穿照明线 {core_material}"
            # else: 未识别的线型（如UTP/STP等弱电），保持原名不改

        # --- 电缆类：根据敷设方式构建query ---
        # 北京2024定额按敷设方式命名：电缆埋地/沿墙面/沿桥架/穿导管敷设
        cable_model = fields.get("规格", "") or fields.get("型号", "")
        # fields提取经常失败，从全文正则提取电缆型号和敷设方式
        if not cable_model:
            model_match = re.search(
                r'(?:型号|规格)[：:,]*\s*'
                r'((?:WDZ[A-Z0-9]*-|ZR[A-Z]?-|NH-|ZB[N]?-)?'
                r'(?:YJV|YJY|VV|BTTRZ|BTLY|BTTZ|YTTW|BBTRZ|KYJY|KVV|KVVP)'
                r'[A-Z0-9.*×xX/\-]*)',
                full_text.upper())
            if model_match:
                cable_model = model_match.group(1)
        is_cable = ("电缆" in name and "终端头" not in name
                    and "电缆头" not in name and "保护管" not in name)
        is_control_cable = ("控制" in name or "信号" in name
                            or "控制" in cable_model.upper())
        if is_cable:
            # 敷设方式：先从fields取，再从全文正则提取
            laying_raw = fields.get("敷设方式", "") or fields.get("敷设方式、部位", "")
            if not laying_raw:
                lay_match = re.search(r'敷设方式[、部位]*[：:]\s*(.+?)(?:\s|电压|$)', full_text)
                if lay_match:
                    laying_raw = lay_match.group(1)

            # 从电缆型号推断敷设方式（行业惯例）
            if not laying_raw and cable_model:
                model_upper = cable_model.upper()
                if "22" in model_upper or "23" in model_upper:
                    laying_raw = "埋地"  # YJV22/VV22=钢带铠装→埋地

            # 控制电缆：按敷设方式+芯数构建query
            # 控制电缆按芯数分档（6/14/24/37/48芯），和电力电缆按截面分档不同
            if is_control_cable:
                if "桥架" in laying_raw or "线槽" in laying_raw:
                    query_parts[0] = "控制电缆沿桥架敷设"
                elif "支架" in laying_raw:
                    query_parts[0] = "控制电缆沿支架敷设"
                elif "埋地" in laying_raw or "直埋" in laying_raw:
                    query_parts[0] = "控制电缆埋地敷设"
                elif "管" in laying_raw:
                    query_parts[0] = "控制电缆穿管敷设"
                else:
                    query_parts[0] = "控制电缆敷设"
                # 提取芯数：从"5x1.5"、"14*1.5"等格式中取第一个数字（芯数）
                core_match = re.search(r'(\d+)\s*[×xX*]\s*\d+(?:\.\d+)?', full_text)
                if core_match:
                    core_count = int(core_match.group(1))
                    query_parts.append(f"电缆芯数 {core_count}")
            # 矿物绝缘电缆：BTTRZ/BTLY/BTTZ/YTTW/BBTRZ
            elif cable_model and any(m in cable_model.upper()
                                     for m in ("BTTRZ", "BTLY", "BTTZ", "YTTW", "BBTRZ")):
                query_parts[0] = "矿物绝缘电缆"
            # 普通电力电缆按敷设方式
            elif "桥架" in laying_raw or "线槽" in laying_raw:
                # 不用"线槽"避免BM25误匹配"金属线槽敷设"（线槽是另一品类）
                # "室内敷设电力电缆 沿桥架"兼容两种命名风格：
                #   北京: "电缆沿桥架、线槽敷设"（BM25匹配"电缆""桥架""敷设"）
                #   江西: "室内敷设电力电缆"（BM25匹配"室内""敷设""电力电缆"）
                query_parts[0] = "室内敷设电力电缆 沿桥架"
            elif "排管" in laying_raw:
                query_parts[0] = "排管内电力电缆敷设"
            elif "管" in laying_raw:
                query_parts[0] = "电缆穿导管敷设"
            elif "埋地" in laying_raw or "直埋" in laying_raw:
                query_parts[0] = "电缆埋地敷设"
            elif "墙" in laying_raw or "支架" in laying_raw:
                query_parts[0] = "电缆沿墙面、支架敷设"
            elif "室内" in laying_raw:
                query_parts[0] = "室内敷设电力电缆"
            else:
                query_parts[0] = "室内敷设电力电缆"  # 默认室内（最常见）

            # 电缆截面
            if cable_section:
                # 保留小数（如2.5mm²不能截断为2）
                section_str = _format_number_for_query(cable_section)
                query_parts.append(f"电缆截面 {section_str}")
            # 不再添加 laying（已融入名称）
        else:
            # --- 非电缆类的敷设方式（配管等） ---
            laying = fields.get("敷设方式", "") or fields.get("敷设方式、部位", "")
            if laying and laying != "综合考虑":
                if "桥架" in laying:
                    query_parts.append("沿桥架敷设")
                elif "管" in laying or "管道" in laying:
                    query_parts.append("管道内敷设")
                elif "直埋" in laying:
                    query_parts.append("直埋敷设")
                elif "沟" in laying:
                    query_parts.append("电缆沟敷设")
                else:
                    query_parts.append(laying)

            # 电缆截面（非电缆类一般用不到，但保留兼容；配线类已单独处理截面）
            if cable_section and "穿线" not in name and "配线" not in name:
                section_str = _format_number_for_query(cable_section)
                query_parts.append(f"截面{section_str}")

        # --- 安装方式（配电箱、灯具、插座等通用） ---
        install = fields.get("安装方式", "")
        # 描述中没安装方式时，从清单名称提取（如"明装配电箱"、"暗装风机盘管"）
        if not install:
            if "明装" in name or "明配" in name:
                install = "明装"
            elif "暗装" in name or "暗配" in name:
                install = "暗装"
            elif "落地式" in name or "落地" in name:
                install = "落地"
            elif "嵌入式" in name or "嵌入" in name:
                install = "嵌入"
            elif "吸顶式" in name or "吸顶" in name:
                install = "吸顶"
            elif "挂墙" in name or "壁挂" in name:
                install = "挂墙"
        if install:
            if "底距地" in install or "墙上" in install:
                query_parts.append("明装")
            elif "落地" in install:
                query_parts.append("落地安装")
            elif "嵌入" in install or "暗装" in install:
                query_parts.append("嵌入安装")
            elif "吸顶" in install:
                query_parts.append("吸顶式")
            else:
                query_parts.append(install)

        # 回路数（配电箱按回路分档）
        circuits = fields.get("回路数", "")
        if circuits:
            query_parts.append(circuits)

    # 从描述字段补充设备具体类型（清单名泛称时帮助BM25精准命中）
    desc_type = _extract_desc_equipment_type(fields, name)
    if desc_type:
        query_parts.append(desc_type)

    # 插座默认单相（建筑工程中绝大多数插座是单相，三相插座会在清单中明确标注）
    # 排除信息/电视/网络等弱电插座（不区分相数）
    if "插座" in name and "三相" not in full_text:
        _weak_current_outlet = ("信息", "电视", "网络", "电话", "光纤", "智能")
        if not any(kw in name for kw in _weak_current_outlet):
            query_parts.append("单相")

    # 开关/插座默认暗装（建筑工程中90%+是暗装，明装会在清单中明确标注）
    # 条件：名称含"开关"或"插座"，且没有已提取的安装方式，且没有"明装"关键词
    if ("开关" in name or "插座" in name) and "连体" not in name:
        has_install = any(kw in " ".join(query_parts) for kw in
                         ("明装", "暗装", "嵌入", "落地", "吸顶", "挂墙"))
        if not has_install and "明装" not in full_text and "明配" not in full_text:
            query_parts.append("暗装")

    return _apply_synonyms(" ".join(query_parts), specialty)
