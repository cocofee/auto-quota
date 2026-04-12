# -*- coding: utf-8 -*-
"""
搜索 query 构建器 — 从 text_parser.py 拆分

功能：将清单名称+特征描述 转换为 定额搜索 query
核心函数：build_quota_query(parser, name, description)

设计：通过参数接收 parser 实例（调用 parser.parse()），不导入 text_parser，
避免循环依赖。
"""

import json
import math
import re
from pathlib import Path

from loguru import logger
from src.pipe_rule_utils import (
    PIPE_ACCESSORY_WORDS,
    normalize_pipe_material_hint as shared_normalize_pipe_material_hint,
)
from src.query_builder_specialized_rules import (
    _bucket_distribution_box_half_perimeter,
    _build_distribution_box_query,
    _build_fire_door_or_metal_opening_query,
    _build_metal_opening_query_parts,
    _clean_distribution_box_text,
    _extract_distribution_box_half_perimeter_mm,
    _extract_distribution_box_model,
    _extract_distribution_box_fields,
    _normalize_distribution_box_name,
)
from src.rule_matcher import try_rule_match
from src.subject_family_guard import (
    is_family_hint_term,
    resolve_primary_subject_hint,
    should_suppress_family_hint,
)

_LAMP_RULE_EXCLUDE_PATTERN = r"灯杆|灯塔|路灯基础|灯槽|灯箱|灯带槽"

# 清单编码过滤正则（WMSGCS001001、050402001001 等编码会污染搜索词）
_BILL_CODE_PATTERN = re.compile(
    r'[A-Za-z]{4,}\d{6,}'   # 字母前缀+数字后缀（WMSGCS001001）
    r'|\b\d{10,12}\b'       # 10-12位纯数字编码（050402001001）
)

# 装修材料代号正则（CT-03、MT-01、ST-02、M-03、MR-07、WD-02、WC-01、PT-01等）
# 室内设计图纸上的材料编号，混在搜索词里会严重干扰BM25匹配
_DECO_CODE_PATTERN = re.compile(
    r'[（(]\s*[A-Z]{1,3}-?\d{1,3}\s*[）)]'  # 括号包裹的编号：（CT-03）(MT-01)
    r'|(?<=[^\x00-\x7f])\s*[A-Z]{1,2}R?-\d{1,3}\b'  # 中文后跟的编号：成品门M-03、门MR-07
)

# ===== 工程同义词表（清单常用名 → 定额库常用名） =====
# 只加载一次，后续复用缓存
_SYNONYMS_CACHE = None

_PIPE_USAGE_TOKENS = (
    "给水",
    "排水",
    "雨水",
    "污水",
    "废水",
    "冷水",
    "热水",
    "消防",
    "采暖",
    "空调",
    "凝结水",
    "燃气",
)
_GENERIC_PIPE_SYNONYM_KEYS = {
    "塑料管",
    "钢管",
    "复合管",
}


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

    # 3. 加载清单库挖掘的同义词（优先级最低，由 mine_bill_synonyms.py 生成）
    bill = _load_synonym_file(base_path / "bill_synonyms.json")

    # 4. 合并：清单库先放 → 自动表覆盖 → 手工表最终覆盖（手工优先级最高）
    merged = {}
    merged.update(bill)
    merged.update(auto)
    merged.update(manual)

    # 按key长度降序排列，优先匹配长词（避免"PE管"先于"HDPE管"匹配）
    _SYNONYMS_CACHE = dict(
        sorted(merged.items(), key=lambda x: len(x[0]), reverse=True)
    )
    return _SYNONYMS_CACHE


def _load_synonym_file(path: Path) -> dict:
    """从单个JSON文件加载同义词映射（内部工具函数）

    支持两种格式：
    1. engineering/auto格式：{"key": ["value", ...], ...}
    2. bill_synonyms格式：{"_meta": {...}, "synonyms": {"key": "value", ...}}
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # 格式2：bill_synonyms.json（嵌套在 "synonyms" 字段里，值是字符串不是列表）
        if "synonyms" in raw and isinstance(raw["synonyms"], dict):
            return {k: v for k, v in raw["synonyms"].items() if k and v}

        # 格式1：engineering/auto（值是列表，取第一个元素）
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


def _extract_pipe_usage_tokens(text: str) -> set[str]:
    raw = str(text or "")
    return {token for token in _PIPE_USAGE_TOKENS if token in raw}


def _should_skip_conflicting_synonym(key: str, replacement: str, query: str) -> bool:
    if key not in _GENERIC_PIPE_SYNONYM_KEYS:
        return False

    replacement_usage = _extract_pipe_usage_tokens(replacement)
    if not replacement_usage:
        return False

    query_usage = _extract_pipe_usage_tokens(query)
    if not query_usage:
        return True

    return not replacement_usage.issubset(query_usage)


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
                if _should_skip_conflicting_synonym(key, replacement, query):
                    continue
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


def _strip_cable_accessory_noise(text: str) -> str:
    """移除电缆本体描述里的电缆头/接线端子噪声，避免检索词误漂到附件定额。"""
    cleaned = str(text or "")
    if not cleaned:
        return ""
    noise_patterns = (
        r"电缆接线端子及电缆中间头制作安装",
        r"电缆中间头制作安装",
        r"电缆终端头制作安装",
        r"电缆头制作安装",
        r"中间头制作安装",
        r"终端头制作安装",
        r"电缆头或电线头[^。；;\n]*?(?:铜鼻子|接线端子)",
        r"(?:焊|压)?铜接线端子[^。；;\n]*",
        r"(?:焊|压)?铝接线端子[^。；;\n]*",
        r"铜鼻子[^。；;\n]*",
        r"接线端子材质[、,，]?(?:规格)?[：:][^。；;\n]*",
    )
    for pattern in noise_patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _infer_cable_conductor(*, text: str, material: str = "", wire_type: str = "") -> str:
    """推断电缆导体材质，用于强化铜芯/铝芯区分。"""
    raw_text = str(text or "")
    material = str(material or "")
    wire_type = str(wire_type or "").upper()
    combined = f"{raw_text} {material}".upper()

    if "铝合金" in raw_text or "铝合金" in material:
        return "铝合金"
    if any(keyword in combined for keyword in ("铝芯", "压铝", "铝电缆")):
        return "铝芯"
    if any(keyword in combined for keyword in ("铜芯", "压铜", "铜电缆")):
        return "铜芯"

    if wire_type.startswith(("YJLV", "VLV", "VLL", "YJHLV")):
        return "铝芯"
    if wire_type.startswith((
        "BPYJV", "YJV", "YJY", "VV", "KYJY", "KVV", "KVVP",
        "BTLY", "BTTRZ", "BTTZ", "YTTW", "BBTRZ",
    )):
        return "铜芯"
    return ""


def _dedupe_terms(terms: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for term in terms:
        clean = str(term or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
    return deduped


_LOW_VALUE_APPEND_TERMS = {
    "成套",
    "成品",
    "配套",
    "综合考虑",
    "含",
    "综合",
    "套",
    "附件",
    "调试",
    "详见图纸",
    "详图",
    "图纸",
    "安装",
}


def _query_text_len(text: str) -> int:
    return len(re.sub(r"\s+", "", str(text or "")))


_LOW_VALUE_APPEND_NORMALIZED_TERMS = {
    "安装",
    "敷设",
    "制作",
    "调试",
    "成套",
    "成品",
    "配套",
    "综合",
    "综合考虑",
    "含",
    "附件",
    "套",
    "组",
    "台",
    "详见图纸",
    "详图",
    "图纸",
}

_LOW_VALUE_APPEND_PREFIXES = (
    "工作内容",
    "安装方式",
    "敷设方式",
    "详见",
    "包含",
    "包括",
    "不含",
    "含",
    "主材",
    "辅材",
    "附件",
    "配套",
    "综合考虑",
)

_APPEND_TERM_STRONG_HINTS = (
    "DN",
    "DE",
    "Φ",
    "φ",
    "MM",
    "KV",
    "KVA",
    "KW",
    "W",
    "回路",
    "联",
    "明装",
    "暗装",
    "嵌入",
    "悬挂",
    "落地",
    "壁挂",
    "壁装",
    "吸顶",
    "吊链",
    "吊管",
    "桥架",
    "穿管",
    "沟槽",
    "法兰",
    "螺纹",
    "焊接",
)


def _normalize_append_term(term: str) -> str:
    return re.sub(r"[\s:：,，;；、()（）【】\[\]<>《》]+", "", str(term or "")).strip()


def _has_append_term_anchor(term: str) -> bool:
    raw = str(term or "").strip()
    normalized = _normalize_append_term(raw).upper()
    if not normalized:
        return False
    if any(ch.isdigit() for ch in normalized):
        return True
    return any(hint in normalized for hint in _APPEND_TERM_STRONG_HINTS)


def _is_low_value_append_term(term: str) -> bool:
    normalized = _normalize_append_term(term)
    if not normalized:
        return True
    if normalized in _LOW_VALUE_APPEND_NORMALIZED_TERMS:
        return True
    if normalized.endswith(("安装", "敷设", "制作", "调试")) and len(normalized) <= 4:
        return True
    if any(normalized.startswith(prefix) for prefix in _LOW_VALUE_APPEND_PREFIXES):
        return not _has_append_term_anchor(term)
    if any(marker in normalized for marker in ("不含", "主材", "辅材", "附件", "图纸")):
        return not _has_append_term_anchor(term)
    return term in _LOW_VALUE_APPEND_TERMS


def _append_terms_with_budget(base_query: str,
                              terms: list[str],
                              *,
                              budget_chars: int) -> str:
    if budget_chars <= 0:
        return re.sub(r"\s+", " ", str(base_query or "")).strip()

    remaining = budget_chars
    appended: list[str] = []
    normalized_base = f" {str(base_query or '').strip()} "
    for raw_term in terms:
        term = str(raw_term or "").strip()
        if (
            not term
            or _is_low_value_append_term(term)
            or term in str(base_query or "")
            or f" {term} " in normalized_base
            or term in appended
        ):
            continue
        term_len = _query_text_len(term)
        if term_len <= 0 or term_len > remaining:
            continue
        appended.append(term)
        remaining -= term_len
        if remaining <= 0:
            break

    merged = str(base_query or "").strip()
    if appended:
        merged = f"{merged} {' '.join(appended)}".strip()
    return re.sub(r"\s+", " ", merged).strip()


_CONDUIT_PLUGIN_FAMILY_KEYWORDS = (
    "波纹电线管",
    "镀锌电线管",
    "镀锌钢导管",
    "电线管",
    "钢导管",
)


def _is_explicit_electrical_conduit_context(name: str,
                                            full_text: str,
                                            specialty: str = "",
                                            canonical_features: dict | None = None,
                                            context_prior: dict | None = None) -> bool:
    text = f"{name or ''} {full_text or ''}".strip()
    if not text:
        return False

    if any(keyword in text for keyword in ("电气配管", "电线管", "穿线管", "金属软管", "可挠金属套管")):
        return True
    if "导管" in text and "电缆" not in text:
        return True

    canonical_features = canonical_features or {}
    context_prior = context_prior or {}
    system_hint = str(canonical_features.get("system") or context_prior.get("system_hint") or "").strip()
    entity_hint = str(canonical_features.get("entity") or "").strip()
    specialty_hint = str(
        specialty
        or canonical_features.get("specialty")
        or context_prior.get("specialty")
        or ""
    ).strip().upper()

    return "配管" in text and (
        system_hint == "电气"
        or entity_hint == "配管"
        or specialty_hint == "C4"
    )


def _extract_conduit_plugin_family_terms(context_prior: dict | None = None) -> list[str]:
    plugin_hints = dict((context_prior or {}).get("plugin_hints") or {})
    raw_terms = list(plugin_hints.get("preferred_quota_names") or [])[:2]
    raw_terms.extend(list(plugin_hints.get("synonym_aliases") or [])[:2])

    family_terms: list[str] = []
    for text in raw_terms:
        clean = str(text or "").strip()
        if not clean:
            continue
        for keyword in _CONDUIT_PLUGIN_FAMILY_KEYWORDS:
            if keyword in clean:
                family_terms.append(keyword)
                break
    return _dedupe_terms(family_terms)[:2]


def _build_ambiguous_electrical_conduit_query(conduit_code: str = "",
                                              context_prior: dict | None = None) -> str:
    material_hints = {
        "RC": "镀锌电线管",
        "MT": "金属电线管",
    }
    parts = ["电线管敷设", "配管", "导管"]
    if conduit_code:
        parts.append(conduit_code)
        material_hint = material_hints.get(conduit_code)
        if material_hint:
            parts.append(material_hint)
    parts.extend(_extract_conduit_plugin_family_terms(context_prior))
    return " ".join(_dedupe_terms(parts))


def _build_decor_finish_query(name: str, full_text: str) -> str:
    text = str(full_text or "")
    clean_name = str(name or "")

    if "窗帘盒" in clean_name:
        if any(keyword in text for keyword in ("胶合板", "夹板", "玻镁板", "石膏板", "阻燃板")):
            return "窗帘盒 胶合板 单轨"

    if "窗帘" in clean_name and "窗帘盒" not in clean_name and "轨道" not in clean_name:
        if any(keyword in text for keyword in ("不透光", "遮光布", "装饰布帘")):
            return "成品窗帘安装 装饰布帘（不透光）"
        if "纱" in text:
            return "成品窗帘安装 装饰布帘（纱布）"

    floor_tile_hint = any(keyword in text for keyword in ("抛光砖", "防滑砖", "耐磨砖", "仿石砖", "陶瓷地砖"))
    floor_context = any(keyword in text for keyword in ("楼面", "楼地面", "地面"))
    if floor_tile_hint and floor_context:
        size_match = re.search(r'(\d+)\s*[*×xX]\s*(\d+)', text)
        if size_match:
            width = int(size_match.group(1))
            height = int(size_match.group(2))
            perimeter = (width + height) * 2
            if perimeter <= 2400:
                return "楼地面陶瓷地砖(每块周长mm) 2400以内 水泥砂浆"
            if perimeter <= 3200:
                return "楼地面陶瓷地砖(每块周长mm) 3200以内 水泥砂浆"
            return "楼地面陶瓷地砖(每块周长mm) 3200以外 水泥砂浆"
        return "楼地面陶瓷地砖 水泥砂浆"

    wall_tile_hint = any(keyword in text for keyword in ("面砖内墙面", "瓷砖墙面", "釉面砖", "陶瓷面砖"))
    decor_board_tile_hint = "墙面装饰板" in clean_name and any(
        keyword in text for keyword in ("瓷砖专用粘贴剂", "建筑胶粘剂", "岩板", "瓷砖", "釉面砖")
    )
    if wall_tile_hint or decor_board_tile_hint:
        if any(keyword in text for keyword in ("建筑胶粘剂", "瓷砖专用粘贴剂", "强力胶粉泥")):
            return "镶贴陶瓷面砖密缝 墙面 建筑胶粘剂粘贴"
        return "镶贴陶瓷面砖密缝 墙面"

    return ""


def _build_feature_alignment_terms(canonical_features: dict | None = None,
                                   context_prior: dict | None = None) -> list[str]:
    canonical_features = canonical_features or {}
    context_prior = context_prior or {}

    family = str(canonical_features.get("family") or "").strip()
    entity = str(canonical_features.get("entity") or "").strip()
    system = str(canonical_features.get("system") or "").strip()
    conduit_type = str(canonical_features.get("conduit_type") or "").strip().upper()
    canonical_name = str(canonical_features.get("canonical_name") or "").strip()
    suppress_conduit_canonical_name = (
        family == "conduit_raceway"
        and entity == "配管"
        and system == "电气"
        and conduit_type in {"SC", "G", "DG", "RC", "MT"}
        and any(token in canonical_name for token in ("钢管", "电线管"))
    )

    suppress_family_alignment = should_suppress_family_hint(family, context_prior)
    terms: list[str] = []
    for key in (
        "canonical_name",
        "system",
        "entity",
        "install_method",
        "laying_method",
        "cable_type",
        "cable_head_type",
        "conduit_type",
        "wire_type",
        "box_mount_mode",
        "bridge_type",
        "valve_connection_family",
        "support_scope",
        "support_action",
        "sanitary_mount_mode",
        "sanitary_flush_mode",
        "sanitary_water_mode",
        "sanitary_nozzle_mode",
        "sanitary_tank_mode",
        "lamp_type",
        "voltage_level",
    ):
        value = canonical_features.get(key)
        if value:
            clean_value = str(value).strip()
            if not clean_value or _is_low_value_append_term(clean_value):
                continue
            if key == "canonical_name" and suppress_conduit_canonical_name:
                continue
            if suppress_family_alignment and is_family_hint_term(clean_value, family):
                continue
            terms.append(clean_value)

    cable_bundle = canonical_features.get("cable_bundle") or []
    for spec in cable_bundle[:2]:
        cores = spec.get("cores")
        section = spec.get("section")
        if cores and section:
            terms.append(f"{cores}x{_format_number_for_query(float(section))}")

    for hint in (context_prior.get("context_hints") or [])[:2]:
        hint_text = str(hint)
        if _is_low_value_append_term(hint_text):
            continue
        if suppress_family_alignment and is_family_hint_term(hint_text, family):
            continue
        terms.append(hint_text)

    prior_family = context_prior.get("prior_family")
    if prior_family:
        prior_family_text = str(prior_family).strip()
        if prior_family_text and not _is_low_value_append_term(prior_family_text):
            if not (suppress_family_alignment and prior_family_text == family):
                terms.append(prior_family_text)

    terms = _dedupe_terms(terms)
    terms = [t for t in terms if not re.match(r'^[A-Z]?\d+[A-Za-z]?$', t)]
    return terms


def _finalize_query(query: str,
                    specialty: str = "",
                    canonical_features: dict | None = None,
                    context_prior: dict | None = None,
                    apply_synonyms: bool = True) -> str:
    final_query = _apply_synonyms(query, specialty) if apply_synonyms else (query or "")
    feature_terms = _build_feature_alignment_terms(canonical_features, context_prior)
    budget_chars = max(0, _query_text_len(query) // 2)
    extras = [term for term in feature_terms if term and term not in final_query]
    if extras and budget_chars > 0:
        final_query = _append_terms_with_budget(
            final_query,
            extras,
            budget_chars=budget_chars,
        )
    family = str((canonical_features or {}).get("family") or "").strip()
    if family and should_suppress_family_hint(family, context_prior):
        family_noise_terms = {
            "pipe_support": ("支架", "吊架", "支吊架", "支撑架", "管架", "给排水", "管道"),
            "bridge_support": ("支架", "吊架", "支吊架", "桥架", "电缆桥架", "线槽", "电气"),
            "bridge_raceway": ("桥架", "电缆桥架", "线槽", "母线槽", "电气"),
            "pipe_sleeve": ("套管", "防水套管", "刚性防水套管", "柔性防水套管", "给排水", "电气"),
        }.get(family, ())
        base_terms = {term for term in str(query or "").split() if term}
        cleaned_terms: list[str] = []
        for term in str(final_query or "").split():
            if term in base_terms:
                cleaned_terms.append(term)
                continue
            if family_noise_terms and any(marker in term for marker in family_noise_terms):
                continue
            if not is_family_hint_term(term, family):
                cleaned_terms.append(term)
        final_query = " ".join(cleaned_terms)
    primary_subject_hint = resolve_primary_subject_hint(context_prior)
    if primary_subject_hint and not _looks_like_pipe_or_support_subject(primary_subject_hint):
        equipment_hints = ("器", "机", "机组", "终端", "交换器", "加热器", "冷却器", "避雷器", "服务器", "控制器")
        if any(hint in primary_subject_hint for hint in equipment_hints):
            base_terms = {term for term in str(query or "").split() if term}
            protected_noise_terms = ("支架", "吊架", "支吊架", "支撑架", "管架", "给排水", "管道")
            cleaned_terms: list[str] = []
            for term in str(final_query or "").split():
                if term in base_terms or not any(marker in term for marker in protected_noise_terms):
                    cleaned_terms.append(term)
            final_query = " ".join(cleaned_terms)
    return re.sub(r"\s+", " ", final_query).strip()


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
    if description:
        inline_pattern = re.compile(
            r'(?P<label>[\u4e00-\u9fffA-Za-z0-9()（）/\-]{2,18})\s*[:：]\s*'
            r'(?P<value>.*?)(?=(?:\s+[\u4e00-\u9fffA-Za-z0-9()（）/\-]{2,18}\s*[:：])|//|$)'
        )
        for match in inline_pattern.finditer(description):
            label = match.group("label").strip()
            value = match.group("value").strip()
            if value:
                fields[label] = value
    return fields


_PRIMARY_HARD_NOISE_MARKERS = (
    "综合单价中含",
    "工作内容：",
    "工作内容:",
    "附件：",
    "附件:",
    "其他说明：",
    "其他说明:",
    "其他：",
    "其他:",
    "未尽事宜",
    "满足规范",
    "满足设计",
    "详见设计",
    "详见图纸",
    "详见招标",
    "详见相关",
    "符合设计",
    "按规范要求",
    "由投标人自行考虑",
    "具体详见",
    "应符合",
    "综合考虑完成该工艺",
    "支架形式",
    "减震措施",
    "减震装置形式",
    "试压要求",
    "配套",
)
_PRIMARY_SOFT_NOISE_MARKERS = ("含", "包含", "包括")
_PRIMARY_BRACKET_PATTERN = re.compile(r'[（(][^（）()]{0,30}[）)]')
_PRIMARY_TAIL_NOISE_PHRASES = (
    "支架制作安装",
    "支吊架制作安装",
    "套管制作及安装",
    "套管制作安装",
    "防火封堵",
    "采购并安装",
)
_PRIMARY_SUBJECT_SPLIT_PATTERN = re.compile(r'[，,；;。/\|]+')
_PRIMARY_SUBJECT_GENERIC_NAMES = {
    "管道",
    "设备",
    "材料",
    "项目",
    "构件",
    "其他构件",
    "其他项目",
    "成品",
    "配件",
    "辅材",
    "零星项目",
}
_PRIMARY_SUBJECT_OVERRIDE_SUFFIXES = (
    "阀门",
    "管道",
    "设备",
    "项目",
    "构件",
    "材料",
)
_PRIMARY_SUBJECT_NOISE_TOKENS = (
    "支架",
    "吊架",
    "支吊架",
    "套管",
    "封堵",
    "堵洞",
    "除锈",
    "刷油",
    "油漆",
    "防锈漆",
    "采购",
    "安装",
    "制作",
)
_PRIMARY_SUBJECT_BREAK_TOKENS = (
    "挡水条",
    "锁具",
    "把手",
    "五金",
    "五金配件",
    "连接片",
    "紧固件",
    "接地编织带",
    "活接头",
    "零部件",
    "配件",
    "附件",
)
_PRIMARY_SPEC_PATTERNS = (
    r'DN\d+(?:\.\d+)?',
    r'De\d+(?:\.\d+)?',
    r'Φ\d+(?:\.\d+)?',
    r'φ\d+(?:\.\d+)?',
    r'\d+(?:\.\d+)?\s*(?:mm|MM)',
    r'\d+(?:\.\d+)?\s*[x×X*]\s*\d+(?:\.\d+)?(?:\s*[x×X*]\s*\d+(?:\.\d+)?)?',
)
_PRIMARY_CONNECTION_TOKENS = (
    "焊接连接",
    "焊接",
    "螺纹连接",
    "法兰连接",
    "热熔连接",
    "电熔连接",
    "卡压连接",
    "沟槽连接",
    "承插连接",
    "粘接",
)


def _normalize_primary_guard_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).strip(" ,，;；。")


def _find_primary_noise_marker(text: str) -> tuple[int, str]:
    best_idx = -1
    best_marker = ""
    for marker in _PRIMARY_HARD_NOISE_MARKERS + _PRIMARY_SOFT_NOISE_MARKERS:
        idx = text.find(marker)
        if idx < 0 or idx <= 6:
            continue
        if marker in _PRIMARY_SOFT_NOISE_MARKERS:
            prev = text[idx - 1] if idx > 0 else ""
            if prev not in {" ", "，", ",", "；", ";", "。", ")", "）"}:
                continue
        if best_idx < 0 or idx < best_idx:
            best_idx = idx
            best_marker = marker
    return best_idx, best_marker


def _build_primary_guard_text(name: str, description: str) -> dict:
    full_text = _normalize_primary_guard_text(f"{name or ''} {description or ''}")
    if not full_text:
        return {"full_text": "", "primary_text": "", "noise_marker": ""}

    noise_idx, noise_marker = _find_primary_noise_marker(full_text)
    primary_text = full_text[:noise_idx] if noise_idx >= 0 else full_text
    primary_text = _normalize_primary_guard_text(primary_text)
    primary_text = _normalize_primary_guard_text(
        _PRIMARY_BRACKET_PATTERN.sub(" ", primary_text)
    )
    for phrase in _PRIMARY_TAIL_NOISE_PHRASES:
        idx = primary_text.find(phrase)
        if idx >= 8:
            primary_text = _normalize_primary_guard_text(primary_text[:idx])
    if not primary_text:
        primary_text = full_text
    return {
        "full_text": full_text,
        "primary_text": primary_text,
        "noise_marker": noise_marker,
    }


def _truncate_subject_phrase(value: str, max_len: int = 28) -> str:
    text = _normalize_primary_guard_text(value)
    if not text:
        return ""
    pieces = [piece.strip() for piece in _PRIMARY_SUBJECT_SPLIT_PATTERN.split(text) if piece.strip()]
    if pieces:
        text = pieces[0]
    tokens = [token.strip() for token in text.split() if token.strip()]
    if len(tokens) > 1:
        kept: list[str] = []
        for token in tokens:
            if not kept:
                kept.append(token)
                continue
            if ":" in token or "：" in token:
                break
            if any(marker in token for marker in _PRIMARY_HARD_NOISE_MARKERS + _PRIMARY_SOFT_NOISE_MARKERS):
                break
            if any(phrase in token for phrase in _PRIMARY_TAIL_NOISE_PHRASES):
                break
            if any(noise in token for noise in _PRIMARY_SUBJECT_BREAK_TOKENS):
                break
            if re.search(r"\d", token):
                break
            next_text = " ".join(kept + [token])
            if len(next_text) > max_len:
                break
            kept.append(token)
        if kept:
            text = " ".join(kept)
    for phrase in _PRIMARY_TAIL_NOISE_PHRASES:
        idx = text.find(phrase)
        if idx >= 2:
            text = text[:idx]
    if any(keyword in text for keyword in ("送风口", "回风口", "风口", "散流器", "喷口")):
        head = text.split(" ", 1)[0].strip()
        if head and any(keyword in head for keyword in ("送风口", "回风口", "风口", "散流器", "喷口")):
            text = head
        for tail_marker in ("检修口", "活动板", "套口"):
            idx = text.find(tail_marker)
            if idx >= 4:
                text = text[:idx]
                break
    text = _normalize_primary_guard_text(text)
    return text[:max_len].strip()


def _extract_primary_specs(text: str) -> list[str]:
    clean = _normalize_primary_guard_text(text)
    specs: list[str] = []
    for value in re.findall(r'\d+(?:\.\d+)?\s*[x×X*脳]\s*\d+(?:\.\d+)?(?:\s*[x×X*脳]\s*\d+(?:\.\d+)?)?', clean):
        token = _normalize_primary_guard_text(value)
        if token and token not in specs:
            specs.append(token)
    for pattern in _PRIMARY_SPEC_PATTERNS:
        for value in re.findall(pattern, clean):
            token = _normalize_primary_guard_text(value)
            if token and token not in specs and not any(token != existing and token in existing for existing in specs):
                specs.append(token)
    for token in _PRIMARY_CONNECTION_TOKENS:
        if token in clean and token not in specs:
            specs.append(token)
    return specs[:4]


def _extract_subject_from_generic_prefix(prefix: str, guarded_text: str) -> str:
    prefix = _normalize_primary_guard_text(prefix)
    guarded_text = _normalize_primary_guard_text(guarded_text)
    if not prefix or not guarded_text or not guarded_text.startswith(prefix):
        return ""

    tail = _normalize_primary_guard_text(guarded_text[len(prefix):])
    if not tail:
        return ""
    if ":" in tail[:16] or "：" in tail[:16]:
        return ""
    while tail.startswith(prefix):
        tail = _normalize_primary_guard_text(tail[len(prefix):])
        if not tail:
            return ""

    tail = re.sub(r'^[A-Za-z]{1,4}-?\d{1,4}', '', tail).strip()
    tail = re.sub(r'^[0-9]+(?:\.[0-9]+)?', '', tail).strip()
    tail = _normalize_primary_guard_text(tail)
    if not tail:
        return ""

    candidate = _truncate_subject_phrase(tail, max_len=20)
    if not candidate or candidate in _PRIMARY_SUBJECT_GENERIC_NAMES:
        return ""
    if any(noise in candidate for noise in ("厚度", "配合比", "工程部位", "部位", "材料种类", "规格型号")):
        return ""
    if len(re.findall(r'[\u4e00-\u9fff]', candidate)) < 3:
        return ""
    return candidate


def _clean_primary_field_value(label: str, value: str) -> str:
    cleaned = _normalize_primary_guard_text(value)
    cleaned = _normalize_primary_guard_text(_PRIMARY_BRACKET_PATTERN.sub(" ", cleaned))
    if label in {
        "宸ヤ綔鍐呭",
        "鏈敖浜嬪疁",
        "鍏朵粬",
        "鍏朵粬璇存槑",
        "澶囨敞",
        "鍘嬪姏璇曢獙鍙婂惞銆佹礂璁捐瑕佹眰",
    }:
        return ""
    return cleaned


def _score_primary_subject(candidate: str, *, source: str, name: str, guarded_text: str) -> float:
    text = _truncate_subject_phrase(candidate)
    if not text:
        return -1e9

    score = 0.0
    if source == "field_name":
        score += 7.0
    elif source == "name":
        score += 4.0
    elif source == "front":
        score += 3.0
    elif source == "field_type":
        score += 2.5

    if text in (name or ""):
        score += 1.5
    if guarded_text.startswith(text):
        score += 1.0
    if len(text) <= 2:
        score -= 2.0
    if len(text) > 20:
        score -= 1.5
    if text in _PRIMARY_SUBJECT_GENERIC_NAMES:
        score -= 2.5
    if any(token in text for token in _PRIMARY_SUBJECT_NOISE_TOKENS):
        score -= 3.0
    if re.search(r'^[A-Za-z]{1,3}-?\d+$', text):
        score -= 4.0
    return score


def _looks_like_generic_install_title(subject: str) -> bool:
    text = _normalize_primary_guard_text(subject)
    if not text:
        return False

    hard_suffixes = (
        "\u7cfb\u7edf\u5b89\u88c5",
        "\u88c5\u7f6e\u5b89\u88c5",
        "\u8bbe\u5907\u5b89\u88c5",
        "\u673a\u7ec4\u5b89\u88c5",
        "\u7cfb\u7edf\u8c03\u8bd5",
        "\u88c5\u7f6e\u8c03\u8bd5",
        "\u8bbe\u5907\u8c03\u8bd5",
        "\u673a\u7ec4\u8c03\u8bd5",
    )
    if any(text.endswith(suffix) for suffix in hard_suffixes):
        return True

    soft_suffixes = ("\u5b89\u88c5", "\u8c03\u8bd5")
    generic_hints = (
        "\u7cfb\u7edf",
        "\u88c5\u7f6e",
        "\u8bbe\u5907",
        "\u673a\u7ec4",
        "\u673a\u623f",
        "\u673a\u68b0",
    )
    return text.endswith(soft_suffixes) and any(hint in text for hint in generic_hints)


def _looks_like_pipe_or_support_subject(text: str) -> bool:
    normalized = _normalize_primary_guard_text(text)
    if not normalized:
        return False
    keywords = (
        "\u7ba1",
        "\u5957\u7ba1",
        "\u9600",
        "\u652f\u67b6",
        "\u540a\u67b6",
        "\u7ba1\u67b6",
        "\u6865\u67b6",
        "\u7ebf\u69fd",
        "\u914d\u7ba1",
        "\u7a7f\u7ebf",
        "\u7535\u7f06",
        "\u5bfc\u7ba1",
    )
    return any(keyword in normalized for keyword in keywords)


def _should_guard_primary_subject_from_route_hijack(raw_input_name: str, subject_info: dict) -> bool:
    raw_name = _normalize_primary_guard_text(raw_input_name)
    primary_subject = _normalize_primary_guard_text(subject_info.get("primary_subject", ""))
    if not primary_subject or primary_subject in _PRIMARY_SUBJECT_GENERIC_NAMES:
        return False
    if _looks_like_pipe_or_support_subject(raw_name) or _looks_like_pipe_or_support_subject(primary_subject):
        return False
    if _looks_like_generic_install_title(raw_name):
        return True

    equipment_hints = (
        "\u5668",
        "\u673a",
        "\u673a\u7ec4",
        "\u7ec8\u7aef",
        "\u4ea4\u6362\u5668",
        "\u52a0\u70ed\u5668",
        "\u51b7\u5374\u5668",
        "\u907f\u96f7\u5668",
        "\u670d\u52a1\u5668",
        "\u63a7\u5236\u5668",
    )
    combined = f"{raw_name} {primary_subject}".strip()
    if any(hint in combined for hint in equipment_hints):
        return True
    return primary_subject != raw_name and len(primary_subject) >= 4


def _sanitize_feature_alignment_for_protected_subject(canonical_features: dict | None = None) -> dict:
    sanitized = dict(canonical_features or {})
    for key in (
        "canonical_name",
        "entity",
        "system",
        "family",
        "conduit_type",
        "box_mount_mode",
        "support_scope",
        "support_action",
        "voltage_level",
    ):
        sanitized[key] = ""
    return sanitized


def discover_primary_subject(name: str, description: str = "", fields: dict | None = None) -> dict:
    fields = dict(fields or {})
    guard = _build_primary_guard_text(name, description)
    guarded_text = str(guard.get("primary_text") or "")

    candidates: list[tuple[str, str]] = []
    if name:
        candidates.append(("name", name))
    field_name = str(fields.get("名称") or fields.get("鍚嶇О") or "")
    if field_name:
        candidates.append(("field_name", field_name))
    field_type = str(fields.get("类型") or fields.get("绫诲瀷") or "")
    if field_type:
        candidates.append(("field_type", field_type))
    if guarded_text:
        candidates.append(("front", guarded_text))

    best_subject = _truncate_subject_phrase(name)
    best_score = -1e9
    subject_aliases: list[str] = []
    for source, raw in candidates:
        subject = _truncate_subject_phrase(raw)
        if not subject:
            continue
        score = _score_primary_subject(subject, source=source, name=name or "", guarded_text=guarded_text)
        if subject not in subject_aliases:
            subject_aliases.append(subject)
        if score > best_score:
            best_subject = subject
            best_score = score

    is_generic_subject = (
        best_subject in _PRIMARY_SUBJECT_GENERIC_NAMES
        or (
            len(best_subject) <= 8
            and any(best_subject.endswith(suffix) for suffix in _PRIMARY_SUBJECT_OVERRIDE_SUFFIXES)
        )
        or _looks_like_generic_install_title(best_subject)
    )
    if is_generic_subject:
        generic_prefix = best_subject
        if " " in generic_prefix:
            lead_prefix = generic_prefix.split(" ", 1)[0].strip()
            if (
                lead_prefix in _PRIMARY_SUBJECT_GENERIC_NAMES
                or _looks_like_generic_install_title(lead_prefix)
            ):
                generic_prefix = lead_prefix
        generic_tail_subject = _extract_subject_from_generic_prefix(generic_prefix, guarded_text)
        if generic_tail_subject:
            if generic_tail_subject not in subject_aliases:
                subject_aliases.append(generic_tail_subject)
            best_subject = generic_tail_subject

    suppressed_terms: list[str] = []
    noise_marker = str(guard.get("noise_marker") or "")
    if noise_marker:
        suppressed_terms.append(noise_marker)
    tail_text = str(guard.get("full_text") or "")
    if guarded_text and tail_text.startswith(guarded_text):
        tail_text = tail_text[len(guarded_text):]
    for phrase in _PRIMARY_TAIL_NOISE_PHRASES:
        if phrase in (description or "") and phrase not in suppressed_terms:
            suppressed_terms.append(phrase)

    return {
        "primary_subject": best_subject or _normalize_primary_guard_text(name),
        "subject_aliases": subject_aliases[:3],
        "key_specs": _extract_primary_specs(guarded_text or description or name),
        "suppressed_terms": suppressed_terms[:6],
        "noise_marker": noise_marker,
        "guarded_text": guarded_text,
    }


def _build_primary_subject_quota_aliases(primary_subject: str,
                                         *,
                                         name: str = "",
                                         description: str = "",
                                         key_specs: list[str] | None = None) -> list[str]:
    subject = str(primary_subject or "").strip()
    combined = " ".join(str(part or "").strip() for part in (subject, name, description) if str(part or "").strip())
    combined_upper = combined.upper()
    specs = _dedupe_terms([str(value).strip() for value in (key_specs or []) if str(value).strip()])[:2]
    if not specs:
        for pattern in (
            r"DN\s*\d+",
            r"\d+(?:\.\d+)?\s*(?:kVA|KVA|kW|KW|kv|KV|A|a)",
            r"φ\s*\d+(?:\.\d+)?",
        ):
            for match in re.finditer(pattern, combined, flags=re.IGNORECASE):
                token = str(match.group(0) or "").strip()
                if token and token not in specs:
                    specs.append(token)
                if len(specs) >= 2:
                    break
            if len(specs) >= 2:
                break

    aliases: list[str] = []

    def _push(alias: str, *, with_specs: bool = False):
        clean = str(alias or "").strip()
        if not clean or clean == subject:
            return
        if with_specs and specs:
            aliases.append(f"{clean} {specs[0]}")
        aliases.append(clean)

    if "逆变器" in combined:
        power_spec = next((spec for spec in specs if re.search(r"(?:kW|KW)$", spec)), "")
        power_value = None
        if power_spec:
            match = re.search(r"(\d+(?:\.\d+)?)\s*(?:kW|KW)$", power_spec)
            if match:
                try:
                    power_value = float(match.group(1))
                except ValueError:
                    power_value = None
        if any(token in combined for token in ("光伏", "组串式")):
            if power_spec:
                _push(f"光伏逆变器安装 功率{power_spec}")
                if power_value is not None:
                    for bucket in (250, 1000):
                        if power_value <= bucket:
                            _push(f"光伏逆变器安装 功率≤{int(bucket)}kW")
            _push("光伏逆变器安装", with_specs=True)
            _push("光伏逆变器")
        if power_spec:
            _push(f"逆变器安装 功率{power_spec}")
        _push("逆变器安装", with_specs=True)

    if "UPS" in combined_upper or "不间断电源" in combined or "不停电装置" in combined:
        kva_spec = next((spec for spec in specs if re.search(r"(?:kVA|KVA)$", spec)), "")
        if any(token in combined for token in ("调试", "试运行", "系统")):
            if kva_spec:
                _push(f"保安电源系统调试 不间断电源容量{kva_spec}")
            _push("保安电源系统调试", with_specs=True)
            if kva_spec:
                _push(f"UPS不停电装置调试 不间断电源容量{kva_spec}")
            _push("UPS不停电装置调试", with_specs=True)
        _push("UPS不停电装置")

    if "清扫口" in combined:
        _push("地面扫除口安装", with_specs=True)
        _push("扫除口")

    if "地漏" in combined:
        if any(token in combined for token in ("防爆", "防爆型")):
            _push("防爆地漏", with_specs=True)
        _push("地漏安装", with_specs=True)
        _push("地漏")

    if "冲洗管" in combined and "大便槽" in combined:
        _push("大便冲洗管", with_specs=True)
        _push("大便冲洗管")

    if "衬塑钢管" in combined:
        _push("钢塑复合管", with_specs=True)

    if any(token in combined for token in ("热镀锌钢管", "热镀锌")):
        dn_spec = next((spec for spec in specs if spec.upper().startswith("DN")), "")
        if any(token in combined for token in ("沟槽", "卡箍")) and dn_spec:
            _push(f"热浸锌镀锌钢管 沟槽连接 {dn_spec}")
        _push("热浸锌镀锌钢管", with_specs=True)

    if "UPVC" in combined_upper and "排水管" in combined:
        outer_diameter = ""
        diameter_match = re.search(r"[φΦ]\s*(\d+(?:\.\d+)?)", combined)
        if diameter_match:
            outer_diameter = diameter_match.group(1)
        if outer_diameter:
            _push(f"硬塑(UPVC)管铺设 外径{outer_diameter}mm")
        _push("硬塑(UPVC)管铺设", with_specs=True)

    return _dedupe_terms(aliases)[:6]


def build_primary_query_profile(name: str, description: str = "", fields: dict | None = None) -> dict:
    normalized_fields = dict(fields or {})
    if not normalized_fields and description:
        normalized_fields = extract_description_fields(description)

    guard = _build_primary_guard_text(name, description)
    subject_info = discover_primary_subject(name, description, normalized_fields)
    primary_subject = _normalize_primary_guard_text(subject_info.get("primary_subject", ""))

    field_aliases = (
        ("名称", ("名称", "鍚嶇О")),
        ("材质", ("材质", "鏉愯川")),
        ("规格", ("规格", "瑙勬牸")),
        ("连接形式", ("连接形式", "杩炴帴褰㈠紡")),
        ("连接方式", ("连接方式", "杩炴帴鏂瑰紡")),
        ("安装部位", ("安装部位", "瀹夎閮ㄤ綅")),
        ("介质", ("介质", "浠嬭川")),
        ("类型", ("类型", "绫诲瀷")),
        ("配置形式", ("配置形式", "閰嶇疆褰㈠紡")),
        ("敷设方式", ("敷设方式", "鏁疯鏂瑰紡")),
        ("管径", ("管径", "绠″緞")),
        ("型号", ("型号", "鍨嬪彿")),
    )
    has_decisive_fields = any(
        any(alias in normalized_fields for alias in aliases)
        for _, aliases in field_aliases
    )

    decisive_terms: list[str] = []
    used_fields: list[str] = []
    if has_decisive_fields:
        name_value = ""
        for alias in ("名称", "鍚嶇О"):
            if normalized_fields.get(alias):
                name_value = normalized_fields.get(alias, "")
                break
        name_term = _clean_primary_field_value("名称", name_value) or primary_subject
        if name_term:
            decisive_terms.append(name_term)
        for label, aliases in field_aliases:
            raw_value = ""
            for alias in aliases:
                if normalized_fields.get(alias):
                    raw_value = normalized_fields.get(alias, "")
                    break
            if not raw_value:
                continue
            value = _clean_primary_field_value(label, raw_value)
            if not value or value == name_term:
                continue
            if value not in decisive_terms:
                decisive_terms.append(value)
                used_fields.append(label)

    quota_aliases = _build_primary_subject_quota_aliases(
        primary_subject,
        name=name,
        description=description,
        key_specs=list(subject_info.get("key_specs") or []),
    )

    return {
        "full_text": str(guard.get("full_text") or ""),
        "primary_text": str(guard.get("primary_text") or ""),
        "noise_marker": str(guard.get("noise_marker") or ""),
        "primary_subject": primary_subject,
        "subject_aliases": list(subject_info.get("subject_aliases") or []),
        "key_specs": list(subject_info.get("key_specs") or []),
        "suppressed_terms": list(subject_info.get("suppressed_terms") or []),
        "guarded_text": str(subject_info.get("guarded_text") or ""),
        "strategy": "fields" if has_decisive_fields else "front_segment",
        "fields": normalized_fields,
        "decisive_terms": decisive_terms[:6],
        "quota_aliases": quota_aliases,
        "used_fields": used_fields[:6],
    }


def _take_primary_query_front_segment(text: str, max_chars: int = 48) -> str:
    clean = _normalize_primary_guard_text(text)
    if len(clean) <= max_chars:
        return clean
    for punct in ("。", "；", ";", "，", ","):
        idx = clean.find(punct)
        if 8 <= idx <= max_chars:
            return clean[:idx]
    return clean[:max_chars]


def _build_query_subject_seed_terms(raw_input_name: str, primary_profile: dict) -> list[str]:
    raw_subject = _normalize_primary_guard_text(raw_input_name)
    primary_subject = _normalize_primary_guard_text(primary_profile.get("primary_subject", ""))
    primary_text = _normalize_primary_guard_text(primary_profile.get("primary_text", ""))
    preferred_subject = primary_subject or raw_subject

    seeds: list[str] = []
    for term in list(primary_profile.get("decisive_terms") or [])[:3]:
        token = _normalize_primary_guard_text(term)
        if token and token not in seeds:
            seeds.append(token)

    if (
        not seeds
        and preferred_subject
        and preferred_subject not in _PRIMARY_SUBJECT_GENERIC_NAMES
        and len(preferred_subject) > 1
    ):
        seeds.append(preferred_subject)

    if not seeds and (not raw_subject or raw_subject in _PRIMARY_SUBJECT_GENERIC_NAMES):
        front_term = _truncate_subject_phrase(primary_text, max_len=28)
        if not front_term:
            front_term = _take_primary_query_front_segment(primary_text)
        if front_term and front_term not in seeds:
            seeds.append(front_term)

    if (
        not seeds
        and primary_subject
        and primary_subject not in _PRIMARY_SUBJECT_GENERIC_NAMES
        and primary_subject != raw_subject
        and primary_subject not in seeds
    ):
        seeds.append(primary_subject)

    for spec in list(primary_profile.get("key_specs") or [])[:2]:
        token = _normalize_primary_guard_text(spec)
        if token and not any(token in existing for existing in seeds):
            seeds.append(token)

    return seeds[:4]


def _build_query_quota_alias_seed_terms(raw_input_name: str, primary_profile: dict) -> list[str]:
    raw_subject = _normalize_primary_guard_text(raw_input_name)
    primary_subject = _normalize_primary_guard_text(primary_profile.get("primary_subject", ""))
    decisive_terms = list(primary_profile.get("decisive_terms") or [])
    quota_aliases = list(primary_profile.get("quota_aliases") or [])
    key_specs = list(primary_profile.get("key_specs") or [])

    if decisive_terms or not quota_aliases:
        return []

    subject = primary_subject or raw_subject
    if not subject or subject in _PRIMARY_SUBJECT_GENERIC_NAMES or len(subject) > 12:
        return []

    strong_spec = any(
        re.search(
            r"(?:^DN\s*\d+|^De\s*\d+|^\d+(?:\.\d+)?\s*(?:kVA|KVA|kW|KW|A|mm|mm2|mm²)$)",
            str(spec or ""),
            flags=re.IGNORECASE,
        )
        for spec in key_specs
    )
    if not strong_spec:
        return []

    selected: list[str] = []
    for alias in quota_aliases:
        token = _normalize_primary_guard_text(alias)
        if not token or token in selected or token == subject or token == raw_subject:
            continue
        selected.append(token)
        if len(selected) >= 2:
            break
    return selected


def _should_apply_discovered_subject(name: str, fields: dict, subject_info: dict) -> bool:
    raw_name = _normalize_primary_guard_text(name)
    primary_subject = _normalize_primary_guard_text(subject_info.get("primary_subject", ""))
    if not primary_subject or primary_subject == raw_name:
        return False
    if not raw_name or len(raw_name) <= 2:
        return True
    if raw_name in _PRIMARY_SUBJECT_GENERIC_NAMES:
        return True
    if _looks_like_generic_install_title(raw_name):
        return True
    if any(raw_name.endswith(suffix) for suffix in _PRIMARY_SUBJECT_OVERRIDE_SUFFIXES):
        return bool(fields.get("名称") or fields.get("鍚嶇О"))
    return False


def _build_switchgear_query(name: str,
                            description: str,
                            full_text: str,
                            params: dict) -> str | None:
    """高低压开关柜/配电柜对象模板。"""
    switchgear_keywords = ("开关柜", "开关屏", "配电柜", "成套配电柜", "高压柜", "低压柜")
    if not any(keyword in full_text for keyword in switchgear_keywords):
        return None
    if "配电箱" in name and "开关柜" not in full_text and "配电柜" not in full_text:
        return None

    voltage_level = str(params.get("voltage_level") or "")
    if re.search(r'10\s*[kK][vV]', full_text):
        return "10kV开关柜安装"
    if voltage_level == "高压":
        return "高压成套配电柜安装"
    if voltage_level == "中压":
        return "中压开关柜安装"
    if voltage_level == "低压" or "低压" in full_text:
        return "低压成套配电柜"
    return None


def _build_sanitary_query(name: str,
                          full_text: str,
                          params: dict) -> str | None:
    """卫生器具标准化查询。"""
    subtype = str(params.get("sanitary_subtype") or "")
    if not subtype and any(token in full_text for token in ("\u62d6\u5e03\u6c60", "\u62d6\u628a\u6c60")):
        subtype = "\u62d6\u5e03\u6c60"
    water_mode = str(params.get("sanitary_water_mode") or "")
    nozzle_mode = str(params.get("sanitary_nozzle_mode") or "")
    tank_mode = str(params.get("sanitary_tank_mode") or "")
    if not subtype and not any(keyword in full_text for keyword in (
        "便器", "洗脸盆", "洗面盆", "洗手盆", "洗涤盆", "水槽", "拖布池", "拖把池", "地漏", "水龙头", "龙头",
    )):
        return None

    install_method = str(params.get("install_method") or "")
    query_parts: list[str] = []

    if subtype == "坐便器":
        query_parts.append("坐式大便器安装")
    elif subtype == "蹲便器":
        query_parts.append("蹲式大便器安装")
    elif subtype == "小便器":
        if any(token in full_text for token in ("立式", "落地式")):
            query_parts.append("立式小便器安装")
        elif any(token in full_text for token in ("壁挂", "挂墙", "挂式")) or install_method == "挂墙":
            query_parts.append("壁挂式小便器安装")
        else:
            query_parts.append("小便器安装")
    elif subtype == "洗脸盆":
        query_parts.append("洗脸盆")
    elif subtype == "洗涤盆":
        query_parts.append("洗涤盆")
    elif subtype == "拖布池":
        query_parts.append("\u5176\u4ed6\u6210\u54c1\u536b\u751f\u5668\u5177")
        query_parts.append("成品拖布池安装")
    elif subtype == "淋浴器":
        query_parts.append("淋浴器安装")
    elif subtype == "地漏":
        query_parts.append("地漏安装")

    if not query_parts:
        return None

    if "感应" in full_text:
        query_parts.append("感应开关")
        if any(token in full_text for token in ("埋入", "暗装", "埋入式")):
            query_parts.append("埋入式")
    if "脚踏" in full_text:
        query_parts.append("脚踏开关")
    if "自闭阀" in full_text:
        query_parts.append("自闭阀")
    if tank_mode:
        query_parts.append(tank_mode)
    if water_mode:
        query_parts.append(water_mode)
    if subtype in {"洗涤盆", "洗脸盆", "水龙头"} and nozzle_mode:
        query_parts.append(nozzle_mode)
    if subtype == "小便器" and "自动冲洗" in full_text:
        query_parts.append("自动冲洗")

    deduped: list[str] = []
    for part in query_parts:
        if part and part not in deduped:
            deduped.append(part)
    return " ".join(deduped)


def _build_surge_protector_query(name: str,
                                 full_text: str,
                                 params: dict) -> str | None:
    del params
    text = f"{name} {full_text}".strip()
    if not any(
        token in text
        for token in (
            "\u6d6a\u6d8c\u4fdd\u62a4\u5668",
            "\u907f\u96f7\u5668",
            "\u9632\u96f7\u5668",
            "SPD",
        )
    ):
        return None

    combo_camera_tokens = (
        "\u6444\u50cf\u673a",
        "\u6444\u50cf\u5934",
        "\u76d1\u63a7",
        "\u4e91\u53f0",
        "\u7535\u89c6",
    )
    combo_spd_tokens = (
        "\u7f51\u7edc+\u7535\u6e90\u9632\u96f7\u5668",
        "\u4e8c\u5408\u4e00\u9632\u96f7\u5668",
        "\u7f51\u7edc\u9632\u96f7",
    )
    if any(token in text for token in combo_camera_tokens) and any(
        token in text for token in combo_spd_tokens
    ):
        return " ".join(
            _dedupe_terms(
                [
                    "\u7535\u5b50\u8bbe\u5907\u9632\u96f7\u63a5\u5730\u88c5\u7f6e\u5b89\u88c5",
                    "\u7535\u89c6\u6444\u50cf\u5934\u907f\u96f7\u5668",
                    "\u7f51\u7edc+\u7535\u6e90\u9632\u96f7\u5668",
                ]
            )
        )

    return None


def _build_garden_plant_query(name: str, full_text: str, specialty: str = "") -> str | None:
    """园林苗木类 query 构建：优先用土球/裸根等分档特征，避免误走 DN 管道路由。"""
    del specialty
    if not any(keyword in name for keyword in ("栽植乔木", "起挖乔木", "栽植灌木", "起挖灌木")):
        return None

    normalized_name = _normalize_bill_name(name)

    soil_ball_match = re.search(r'土球(?:直径)?[^\d]{0,4}(\d+)', full_text)
    if soil_ball_match:
        size = soil_ball_match.group(1)
        return f"{normalized_name} 土球直径{size}cm以内"

    if "裸根" in full_text:
        if "乔木" in name:
            diameter_match = re.search(r'(?:米径|胸径|干径)[^\d]{0,4}(\d+)', full_text)
            if diameter_match:
                size = diameter_match.group(1)
                return f"{normalized_name} 裸根 米径{size}cm以内"
            return f"{normalized_name} 裸根"

        if "灌木" in name:
            crown_match = re.search(r'冠丛高[^\d]{0,4}(\d+)', full_text)
            if crown_match:
                size = crown_match.group(1)
                return f"{normalized_name} 裸根 冠丛高{size}cm以内"
            return f"{normalized_name} 裸根"

    return normalized_name


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
    wire_type = str(params.get("wire_type") or "")
    cable_type = str(params.get("cable_type") or "")
    cable_head_type = str(params.get("cable_head_type") or "")
    conductor = _infer_cable_conductor(
        text=full_text,
        material=str(params.get("material") or ""),
        wire_type=wire_type,
    )

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
        head_word = "中间头" if cable_head_type == "中间头" or "中间" in full_text else "终端头"
        # 矿物绝缘分控制/电力两种
        if "控制" in full_text or cable_type == "控制电缆":
            # 提取芯数
            core_match = re.search(r'(\d+)\s*[×xX*]\s*\d+', full_text)
            core_count = int(core_match.group(1)) if core_match else None
            query = f"矿物绝缘控制电缆{head_word}"
            if core_count:
                query += f" 芯数 {core_count}"
            return query
        else:
            # 矿物绝缘电力电缆头
            section = params.get("cable_section")
            query = f"矿物绝缘电力电缆{head_word}"
            if section:
                section_str = _format_number_for_query(section)
                query += f" 截面 {section_str}"
            return query

    # --- 步骤3：控制电缆头 ---
    # 清单名含"控制"，或描述中含"控制电缆"
    is_control = "控制" in name or "控制" in full_text or cable_type == "控制电缆"
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
        if cable_head_type == "中间头" or "中间" in full_text:
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
    conductor = conductor or "铜芯"

    if cable_head_type == "中间头" or "中间" in name or "中间" in full_text:
        query = f"{voltage}以下{location}{conductor}电力电缆中间头"
        if section:
            section_str = _format_number_for_query(section)
            query += f" 截面 {section_str}"
        return query

    # 电力电缆终端头（最常见的case）
    # 搜索词格式："1kV以下室内干包式铜芯电力电缆终端头 截面 N"
    query = f"{voltage}以下{location}{craft}{conductor}电力电缆终端头"
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
    # --- 提取真实设备名（清单常在"名称："或"类型："后给具体设备名） ---
    # 例如 "碳钢阀门 名称：280℃防火阀" → real_type = "280℃防火阀"
    # 例如 "螺纹阀门 类型:截止阀 规格:DN32" → real_type = "截止阀"
    def _extract_inline_field_value(*labels: str) -> str:
        for label in labels:
            match = re.search(
                rf'(?:^|\s)\d*[.、．]?\s*{label}[：:]\s*(.+?)'
                rf'(?=(?:\s+\d*[.、．]?\s*(?:名称|类型|规格|压力|连接方式|连接形式|安装部位|其它)[：:])|//|$)',
                full_text,
            )
            if match:
                value = match.group(1).strip()
                if value:
                    return value
        return ""

    real_type = _extract_inline_field_value("名称", "类型")
    if real_type:
        real_type = re.sub(r'-超高$', '', real_type).strip()  # 去掉超高后缀

    # --- 前置检查：是否含阀门相关关键词 ---
    # 消声百叶有独立定额"消声百叶安装"，不走阀门路由
    if "消声百叶" in name:
        return None
    valve_like_text = f"{name} {real_type}"
    if not any(kw in valve_like_text for kw in ("阀门", "阀", "过滤器", "软接头", "倒流防止", "除污器")):
        return None

    dn = params.get("dn")
    explicit_connection = _extract_inline_field_value("连接方式", "连接形式")
    if not explicit_connection:
        connection_match = re.search(
            r'连接(?:方式|形式)[：:]\s*([^\s/，,；;]+(?:连接)?)',
            full_text,
        )
        if connection_match:
            explicit_connection = connection_match.group(1).strip()
    connection = explicit_connection or params.get("connection", "")
    if not dn:
        dn_match = re.search(r'\b(?:DN|De)\s*(\d+(?:\.\d+)?)\b', full_text, flags=re.IGNORECASE)
        if dn_match:
            dn = int(float(dn_match.group(1)))

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
        if any(kw in vent_name for kw in ("多叶", "对开", "风量调节", "手动调节")):
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

    # === 3. 过滤器 → 优先走过滤器家族，不落到普通法兰/管道路由 ===
    if any(keyword in _check for keyword in ("过滤器", "除污器")):
        # 空气过滤器/油过滤器等设备类有专用定额，不按阀门处理
        if any(prefix in _check for prefix in ("空气", "油", "活性炭", "初效", "中效", "高效")):
            return None  # 交给后续逻辑保留原名搜索

        if "除污器" in _check:
            base = "法兰除污器"
        elif any(keyword in _check for keyword in ("Y型过滤器", "Y形过滤器", "管道过滤器")):
            base = "Y型过滤器安装"
        else:
            base = "过滤器安装"

        _dn_val = int(dn) if dn else 25
        if "法兰" in connection:
            conn = "法兰连接"
        elif "螺纹" in connection or "丝扣" in connection:
            conn = "螺纹连接"
        else:
            conn = "法兰连接" if _dn_val >= 50 else "螺纹连接"

        if "安装" in base:
            return _apply_synonyms(f"{base}({conn})", specialty)
        return _apply_synonyms(base, specialty)

    # === 4. 软接头 → 按连接方式分流 ===
    if "软接头" in _check:
        _dn_val = int(dn) if dn else 50
        if "法兰" in connection:
            return _apply_synonyms("法兰式软接头安装", specialty)
        elif "螺纹" in connection:
            return _apply_synonyms("螺纹式软接头安装", specialty)
        if _dn_val >= 50:
            return _apply_synonyms("法兰式软接头安装", specialty)
        return _apply_synonyms("螺纹式软接头安装", specialty)

    # === 5. 塑料阀门（PPR/PP-R）→ 阀门家族，不落到塑料管 ===
    _plastic_valve_check = (real_type or _name_base or "").upper()
    if "塑料阀门" in _check or (("PPR" in _plastic_valve_check or "PP-R" in _plastic_valve_check) and "阀" in _check):
        _dn_val = int(dn) if dn else 25
        if any(keyword in connection for keyword in ("热熔", "熔接", "粘接", "电熔")):
            return _apply_synonyms("塑料阀门安装(熔接)", specialty)
        if "法兰" in connection:
            return _apply_synonyms("法兰阀门安装", specialty)
        if any(keyword in connection for keyword in ("螺纹", "丝扣")):
            return _apply_synonyms("螺纹阀门", specialty)
        return _apply_synonyms("塑料阀门安装(熔接)" if _dn_val < 50 else "法兰阀门安装", specialty)

    # === 6. 电热熔法兰套件 → 塑料法兰 ===
    if "法兰套件" in _check or "电热熔法兰" in _check:
        return _apply_synonyms("塑料法兰(带短管)安装(热熔连接)", specialty)

    # === 7. 管道阀门 → 不在模板中处理，交给后续管道路由 ===
    # 管道路由会保留 location/usage/connection 等上下文（如"室内消防法兰阀门安装"），
    # 模板直接返回会丢失这些修饰词导致搜索不够精准。
    # 管道阀门（闸阀/蝶阀/碳钢阀门等）的规范化由 build_quota_query 中
    # 原有的管道路由代码（lines 925+）处理。
    return None


def _build_support_query(name: str, full_text: str, params: dict) -> str | None:
    """支架项优先回到支架家族，避免被除锈/刷油文本带偏。"""
    if not any(keyword in full_text for keyword in ("支架", "吊架", "支吊架", "支撑架")):
        return None

    support_scope = str(params.get("support_scope") or "")
    support_action = str(params.get("support_action") or "")
    surface_process = str(params.get("surface_process") or "")
    prefer_aseismic = support_scope == "抗震支架" or "抗震" in full_text
    prefer_bridge = support_scope == "桥架支架" or any(
        keyword in full_text for keyword in ("桥架支架", "桥架支撑架", "电缆桥架", "桥架")
    )
    prefer_equipment = support_scope == "设备支架" or any(
        keyword in full_text for keyword in ("设备支架", "设备吊架", "设备支吊架")
    )
    name_has_support_anchor = any(
        keyword in (name or "")
        for keyword in ("支架", "吊架", "支吊架", "支撑架")
    )
    has_detail_bucket = any(keyword in full_text for keyword in ("03S402", "单件重量", "每组重量"))
    action = "制作安装" if support_action in {"制作", "安装", "制作安装"} else "制作安装"
    if support_scope == "管道支架" and not has_detail_bucket:
        if surface_process or any(keyword in full_text for keyword in ("按需制作", "一般管架")):
            return f"管道支架{action} 一般管架"
    if prefer_aseismic and name_has_support_anchor:
        support_parts = ["抗震支架", "抗震支吊架", "单向支撑"]
        if prefer_bridge:
            support_parts.extend(["桥架", "桥架(线槽)系统"])
        elif "风管" in full_text:
            support_parts.append("风管系统")
        elif any(keyword in full_text for keyword in ("管道", "水管", "消防管", "给水", "排水")):
            support_parts.extend(["管道", "管道系统"])
        if "侧向" in full_text:
            support_parts.append("侧向")
        elif "纵向" in full_text:
            support_parts.append("纵向")
        elif "门型" in full_text:
            support_parts.append("门型")
        if any(keyword in full_text for keyword in ("多管", "多管道", "双管", "两管", "两管道")):
            support_parts.extend(["多管", "多根"])
        elif any(keyword in full_text for keyword in ("单管", "单管道")):
            support_parts.extend(["单管", "单根"])
        return " ".join(support_parts)
    if prefer_bridge and name_has_support_anchor:
        return f"电缆桥架支撑架{action}"
    if prefer_equipment and name_has_support_anchor:
        return f"设备支架{action}"
    return None


def _build_surface_process_query(name: str, full_text: str, params: dict) -> str | None:
    """Keep standalone coating/marking items out of generic installation routes."""
    text = " ".join(part for part in (name or "", full_text or "") if part)
    name_text = name or ""
    if not any(keyword in text for keyword in ("刷油", "防腐", "油漆", "标识", "色环")):
        return None

    if (
        any(keyword in text for keyword in ("支架", "吊架", "支吊架", "设备"))
        and any(keyword in text for keyword in ("制作", "安装"))
        and not any(keyword in name_text for keyword in ("刷油", "防腐", "标识", "色环"))
    ):
        return None

    if "标识" in text or "色环" in text:
        parts = ["管道标识", "色环"]
    elif "金属结构" in text:
        parts = ["金属结构刷油"]
    elif any(keyword in text for keyword in ("管道", "给水", "排水", "消防", "阀门", "法兰")):
        parts = ["管道刷油"]
    elif "设备" in text:
        parts = ["设备刷油"]
    else:
        return None

    if "红丹" in text or "防锈漆" in text:
        parts.append("红丹防锈漆" if "红丹" in text else "防锈漆")
    elif "调和漆" in text:
        parts.append("调和漆")
    elif "银粉漆" in text:
        parts.append("银粉漆")

    surface_process = str(params.get("surface_process") or "")
    if surface_process:
        for segment in (part.strip() for part in surface_process.split("/") if part.strip()):
            if segment != "刷油" and segment not in parts:
                parts.append(segment)
                break

    return " ".join(parts)


def _build_pipe_insulation_query(name: str, full_text: str, params: dict, specialty: str = "") -> str | None:
    """管道橡塑绝热优先命中管道保温家族，避免被直埋保温管劫持。"""
    if not any(keyword in full_text for keyword in ("绝热", "保温", "保冷")):
        return None

    scope_text = f"{name} {full_text}"
    if not any(keyword in scope_text for keyword in ("管道", "给水", "排水", "采暖", "消防", "风管", "阀门", "法兰")):
        return None
    if any(keyword in full_text for keyword in ("直埋保温管", "聚氨酯直埋", "外护管")):
        return None
    if "橡塑" not in full_text:
        return None

    if any(keyword in scope_text for keyword in ("阀门", "法兰")):
        base = "橡塑板安装(阀门、法兰) 阀门"
    elif "风管" in scope_text:
        base = "橡塑板安装(管道、风管) 风管"
    else:
        base = "橡塑管壳安装(管道) 管道"

    dn = params.get("dn")
    if dn:
        base = f"{base} DN{int(dn)}"
    return base


def _build_pipe_insulation_query_v2(name: str, full_text: str, params: dict, specialty: str = "") -> str | None:
    """Prefer pipe insulation families before generic install routes."""
    if not any(keyword in full_text for keyword in ("绝热", "保温", "保冷", "防结露", "防冻")):
        return None

    scope_text = f"{name} {full_text}"
    has_pipe_context = any(
        keyword in scope_text
        for keyword in ("管道", "给水", "排水", "采暖", "消防", "风管", "阀门", "法兰")
    )
    has_equipment_context = any(
        keyword in scope_text
        for keyword in ("设备", "机组", "容器", "储罐", "水箱", "气压罐", "塔器", "换热器")
    )
    if has_equipment_context:
        return None
    if not has_pipe_context:
        if not specialty.startswith("C10") or not any(keyword in scope_text for keyword in ("防结露", "防冻", "保冷")):
            return None
    if any(keyword in full_text for keyword in ("直埋保温管", "聚氨酯直埋", "外护管")):
        return None

    if any(keyword in scope_text for keyword in ("阀门", "法兰")) and "橡塑" in full_text:
        base = "橡塑板安装(阀门、法兰) 阀门"
    elif "风管" in scope_text:
        base = "橡塑板安装(管道、风管) 风管" if "橡塑" in full_text else "风管绝热"
    elif "橡塑" in full_text:
        base = "橡塑管壳安装(管道) 管道"
    elif any(keyword in scope_text for keyword in ("防结露", "保冷")):
        base = "管道绝热 保冷"
    else:
        base = "管道绝热"

    dn = params.get("dn")
    if dn:
        base = f"{base} DN{int(dn)}"
    return base


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


def _normalize_explicit_pipe_material(text: str, material: str = "") -> str:
    return shared_normalize_pipe_material_hint(text, material)


def _build_explicit_pipe_run_query(name: str,
                                   full_text: str,
                                   location: str,
                                   usage: str,
                                   material: str,
                                   connection: str,
                                   dn: int | None) -> str | None:
    text = full_text or ""
    if not text:
        return None

    if any(word in text for word in ("绝热", "保温", "保冷", "电气配管", "导管", "穿线管", "桥架", "线槽")):
        return None

    if any(word in text for word in PIPE_ACCESSORY_WORDS):
        return None

    if not any(word in text for word in ("复合管", "钢塑", "衬塑", "涂塑", "金属骨架", "钢骨架")):
        return None

    normalized_material = _normalize_explicit_pipe_material(text, material)
    if not normalized_material:
        return None

    route_prefix = "管道"
    if usage in {"给水", "冷水", "热水", "排水"}:
        route_prefix = "给排水管道"
    elif usage == "消防":
        route_prefix = "消防管道"
    elif usage == "采暖":
        route_prefix = "采暖管道"

    parts = [route_prefix]
    if location in {"室内", "室外"}:
        parts.append(location)
    if normalized_material:
        parts.append(normalized_material)
    elif "复合管" in text:
        parts.append("复合管")
    if connection:
        parts.append(connection)
    if dn is not None:
        parts.append(f"DN{int(dn)}")
    if usage in {"给水", "冷水", "热水"} and "给水" not in "".join(parts):
        parts.append("给水")

    return " ".join(dict.fromkeys(part for part in parts if part))


def _finalize_rule_match_query(
    rule_match: dict | None,
    *,
    specialty: str,
    canonical_features: dict,
    context_prior: dict,
) -> str | None:
    if not rule_match:
        return None
    return _finalize_query(
        rule_match["query"],
        specialty=specialty,
        canonical_features=canonical_features,
        context_prior=context_prior,
        apply_synonyms=rule_match.get("apply_synonyms", True),
    )


def build_quota_query(parser, name: str, description: str = "",
                      specialty: str = "",
                      bill_params: dict = None,
                      section_title: str = "",
                      canonical_features: dict = None,
                      context_prior: dict = None) -> str:
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
    # 拆除项标记：清单含"拆除"时，最终query需要去掉"安装"、保留"拆除"
    # 避免同义词追加"安装"导致BM25偏向安装定额而非拆除定额
    _is_demolition = "拆除" in (name or "")

    # 过滤清单编码（如 WMSGCS001001、HJBHCS001001、050402001001 等）
    # 这些编码混在特征描述中会污染搜索词，导致BM25搜不到正确定额
    original_name = name
    raw_input_name = name or ""
    name = _BILL_CODE_PATTERN.sub('', name or '').strip() or original_name
    # 过滤装修材料代号（CT-03、MT-01、M-03 等室内设计图纸编号）
    name = _DECO_CODE_PATTERN.sub('', name).strip() or name
    if description:
        description = _BILL_CODE_PATTERN.sub('', description).strip()
        description = _DECO_CODE_PATTERN.sub('', description).strip()
    description = description or ""

    full_text = f"{name} {description}".strip()
    guarded_full_text = full_text
    subject_description = description
    if "电缆" in (name or "") and not any(token in (name or "") for token in ("终端头", "中间头", "电缆头")):
        subject_description = _strip_cable_accessory_noise(description)
    # 优先使用清单清洗阶段已清洗的参数（如卫生器具已剔除DN）
    params = bill_params if bill_params is not None else parser.parse(full_text)
    use_feature_alignment = bool(canonical_features or context_prior)
    canonical_features = dict(canonical_features or {})
    context_prior = dict(context_prior or {})
    if use_feature_alignment and not canonical_features:
        try:
            canonical_features = parser.parse_canonical(
                full_text,
                specialty=specialty,
                context_prior=context_prior,
                params=params,
            )
        except Exception:
            canonical_features = {}

    # 提前提取描述字段（管道路由和通用路由都需要用）
    fields = extract_description_fields(subject_description) if subject_description else {}
    if fields:
        normalized_fields = dict(fields)
        compound_name = _get_desc_field(fields, "鍚嶇О")
        compound_type = _get_desc_field(fields, "绫诲瀷")
        if compound_name:
            compound_name = re.split(r'[;；]|\s+\S{2,8}[：:]', compound_name)[0].strip()
        if compound_type:
            compound_type = re.split(r'[;；]|\s+\S{2,8}[：:]', compound_type)[0].strip()
        if compound_name and "鍚嶇О" not in normalized_fields:
            normalized_fields["鍚嶇О"] = compound_name
        if compound_type and "绫诲瀷" not in normalized_fields:
            normalized_fields["绫诲瀷"] = compound_type
        fields = normalized_fields
    primary_profile = build_primary_query_profile(name, subject_description, fields)
    guarded_full_text = primary_profile.get("primary_text") or guarded_full_text
    subject_info = primary_profile
    protect_primary_subject = _should_guard_primary_subject_from_route_hijack(
        raw_input_name,
        subject_info,
    )
    if protect_primary_subject:
        canonical_features = _sanitize_feature_alignment_for_protected_subject(canonical_features)
    subject_seed_terms = _build_query_subject_seed_terms(raw_input_name, subject_info)
    quota_alias_seed_terms = _build_query_quota_alias_seed_terms(raw_input_name, subject_info)
    discovered_routing_subject = _normalize_primary_guard_text(subject_info.get("primary_subject", ""))
    if (
        not _normalize_primary_guard_text(raw_input_name)
        and len(discovered_routing_subject) > 2
        and discovered_routing_subject not in _PRIMARY_SUBJECT_GENERIC_NAMES
    ):
        name = discovered_routing_subject

    # --- 防火门/金属门窗硬锚点：在通用路由前先稳定家族召回，避免被编码或配管规则带偏 ---
    pre_route_match = try_rule_match(
        {
            "name": name,
            "description": description,
        },
        [
            {
                "name": "fire_door_or_metal_opening",
                "builder": lambda item: _build_fire_door_or_metal_opening_query(
                    item["name"],
                    item["description"],
                ),
            },
        ],
    )
    finalized_pre_route_match = _finalize_rule_match_query(
        pre_route_match,
        specialty=specialty,
        canonical_features=canonical_features,
        context_prior=context_prior,
    )
    if finalized_pre_route_match:
        return finalized_pre_route_match

    # 提取安装部位（室内/室外）
    location = ""
    loc_match = re.search(r'安装部位[：:]\s*(室内|室外|户内|户外)', full_text)
    if loc_match:
        location = loc_match.group(1)
        location = location.replace("户内", "室内").replace("户外", "室外")

    # 提取用途/介质（给水/排水/热水/消防/采暖等）
    # 优先从清单文本提取，找不到再从分项标题/Sheet名推断
    usage = ""
    usage_match = re.search(r'介质[：:]\s*(给水|排水|热水|冷水|消防|蒸汽|采暖|通风|空调)', full_text)
    if usage_match:
        usage = usage_match.group(1)

    # 从清单名称/描述中直接出现的用途关键词补充
    if not usage:
        for _kw in ("消防", "采暖", "给水", "排水", "热水", "冷水"):
            if _kw in full_text:
                usage = _kw
                break

    # 清单文本没有介质信息时，从分项标题/Sheet名推断
    # 例如分项"给水系统"下的PPR管 → 给水方向；"采暖系统"下 → 采暖方向
    if not usage and section_title:
        _sec = section_title
        # 用途关键词优先级：消防 > 采暖 > 给水 > 排水（从具体到宽泛）
        if "消防" in _sec:
            usage = "消防"
        elif "采暖" in _sec or "供暖" in _sec or "暖通" in _sec:
            usage = "采暖"
        elif "给水" in _sec:
            usage = "给水"
        elif "排水" in _sec or "雨水" in _sec or "污水" in _sec:
            usage = "排水"
        elif "燃气" in _sec or "天然气" in _sec:
            usage = "燃气"

    # 材质和连接方式从已提取的参数获取
    if not usage:
        for _kw in ("污废水", "废污水", "污水", "废水", "雨水"):
            if _kw in full_text:
                usage = "排水"
                break
    if not usage:
        for _kw in ("生活给水", "饮用水", "给水", "冷水", "热水"):
            if _kw in full_text:
                usage = "给水"
                break
    if not usage:
        canonical_system = canonical_features.get("system", "")
        system_usage_map = {
            "消防": "娑堥槻",
            "给排水": "缁欐按",
            "通风空调": "閫氶",
        }
        usage = system_usage_map.get(canonical_system, usage)

    material = params.get("material", "")
    connection = fields.get("连接方式", "") or params.get("connection", "")
    dn = params.get("dn")
    conduit_type = params.get("conduit_type", "")
    conduit_dn = params.get("conduit_dn")
    wire_type = params.get("wire_type", "")
    cable_type = params.get("cable_type", "")
    cable_section = params.get("cable_section")
    shape = params.get("shape", "")  # 风管形状：矩形/圆形
    laying_method = params.get("laying_method", "")
    voltage_level = params.get("voltage_level", "")
    bridge_wh_sum = params.get("bridge_wh_sum")
    valve_type = params.get("valve_type", "")
    support_material = params.get("support_material", "")
    support_scope = params.get("support_scope", "")
    support_action = params.get("support_action", "")
    surface_process = params.get("surface_process", "")
    sanitary_subtype = params.get("sanitary_subtype", "")
    sanitary_mount_mode = params.get("sanitary_mount_mode", "")
    sanitary_flush_mode = params.get("sanitary_flush_mode", "")
    sanitary_water_mode = params.get("sanitary_water_mode", "")
    sanitary_nozzle_mode = params.get("sanitary_nozzle_mode", "")
    sanitary_tank_mode = params.get("sanitary_tank_mode", "")
    lamp_type = params.get("lamp_type", "")

    # 补充提取连接方式：text_parser有时漏提取描述中的"连接方式:xxx"
    # 例如"连接方式:卡压式连接"在parser中未被识别，导致query丢失关键区分词
    if not connection:
        _conn_match = re.search(
            r'连接(?:方式|形式)[：:]\s*'
            r'(卡压式?连接|环压式?连接|焊接|螺纹连接|法兰连接'
            r'|热熔连接|粘接|承插连接|沟槽连接|卡箍连接|丝扣连接'
            r'|对接电弧焊|承插氩弧焊|对焊连接)',
            full_text)
        if _conn_match:
            connection = _conn_match.group(1)

    # ===== 管道类：有材质或DN参数，且不是电气类（电缆/配管/穿线） =====
    # 电气类即使有material/dn也应走下面的电气专用query构建
    # 灯具类也不走管道路由（描述中"保护管"等配件词会被误提取为材质）
    prioritized_rule_match = try_rule_match(
        {
            "name": name,
            "full_text": full_text,
            "specialty": specialty,
        },
        [
            {
                "name": "garden_plant",
                "builder": lambda item: _build_garden_plant_query(
                    item["name"],
                    item["full_text"],
                    item["specialty"],
                ),
                "apply_synonyms": False,
            },
            {
                "name": "valve",
                "builder": lambda item: _build_valve_query(
                    item["name"],
                    item["full_text"],
                    params,
                    item["specialty"],
                ),
                "apply_synonyms": False,
            },
        ],
    )
    finalized_prioritized_rule_match = _finalize_rule_match_query(
        prioritized_rule_match,
        specialty=specialty,
        canonical_features=canonical_features,
        context_prior=context_prior,
    )
    if finalized_prioritized_rule_match:
        return finalized_prioritized_rule_match

    surface_process_query = None if protect_primary_subject else _build_surface_process_query(name, full_text, params)
    if surface_process_query:
        return _finalize_query(
            surface_process_query,
            specialty=specialty,
            canonical_features=canonical_features,
            context_prior=context_prior,
            apply_synonyms=False,
        )

    support_route_text = full_text
    if not any(keyword in (name or "") for keyword in ("支架", "吊架", "支吊架", "支撑架")):
        support_route_text = guarded_full_text
    support_query = None if protect_primary_subject else _build_support_query(name, support_route_text, params)
    if support_query:
        return _finalize_query(
            support_query,
            specialty=specialty,
            canonical_features=canonical_features,
            context_prior=context_prior,
            apply_synonyms=False,
        )

    explicit_pipe_query = None
    if not protect_primary_subject:
        explicit_pipe_query = _build_explicit_pipe_run_query(
            name=name,
            full_text=full_text,
            location=location,
            usage=usage,
            material=material,
            connection=connection,
            dn=dn,
        )
    if explicit_pipe_query:
        return _finalize_query(
            explicit_pipe_query,
            specialty=specialty,
            canonical_features=canonical_features,
            context_prior=context_prior,
            apply_synonyms=False,
        )

    pipe_insulation_query = None if protect_primary_subject else _build_pipe_insulation_query_v2(
        name,
        full_text,
        params,
        specialty,
    )
    if pipe_insulation_query:
        return _finalize_query(
            pipe_insulation_query,
            specialty=specialty,
            canonical_features=canonical_features,
            context_prior=context_prior,
            apply_synonyms=False,
        )

    sleeve_text = guarded_full_text
    name_has_sleeve_anchor = any(keyword in (name or "") for keyword in ("套管", "堵洞", "封堵", "孔洞"))
    sleeve_note_only = (
        any(keyword in sleeve_text for keyword in ("含预留孔洞", "含套管", "综合单价中含", "不再另计"))
        and not name_has_sleeve_anchor
    )
    accessory_boundary_item = any(
        keyword in sleeve_text
        for keyword in (
            "给、排水附",
            "给、排水附件",
            "给、排水附(配)件",
            "给、排水附（配）件",
            "附件",
            "附(配)件",
            "附（配）件",
            "地漏",
            "雨水斗",
            "溢流斗",
            "清扫口",
            "水龙头",
            "坐便器",
            "蹲便器",
            "小便器",
        )
    )
    is_explicit_sleeve = (
        any(keyword in sleeve_text for keyword in ("套管", "堵洞", "封堵"))
        and not any(keyword in sleeve_text for keyword in ("可挠金属套管", "电气配管", "导管", "穿线管"))
        and not sleeve_note_only
        and not (
            accessory_boundary_item
            and any(keyword in sleeve_text for keyword in ("含预留孔洞", "含套管", "综合单价中含", "不再另计"))
        )
    )
    if is_explicit_sleeve and not protect_primary_subject:
        sleeve_parts = []
        if any(keyword in sleeve_text for keyword in ("堵洞", "封堵")):
            sleeve_parts.append("堵洞")
        elif any(keyword in sleeve_text for keyword in ("刚性防水", "刚性防水套管")):
            sleeve_parts.append("刚性防水套管制作安装")
        elif any(keyword in sleeve_text for keyword in ("柔性防水", "柔性防水套管")):
            sleeve_parts.append("柔性防水套管制作安装")
        elif any(keyword in sleeve_text for keyword in ("人防", "防护密闭", "密闭")):
            sleeve_parts.append("密闭套管")
        elif any(keyword in sleeve_text for keyword in ("塑料套管", "PVC套管")):
            sleeve_parts.append("一般塑料套管制作安装")
        else:
            sleeve_parts.append("一般钢套管制作安装")

        if "穿墙" in sleeve_text:
            sleeve_parts.append("穿墙")
        if "穿楼板" in sleeve_text:
            sleeve_parts.append("穿楼板")
        if dn:
            sleeve_parts.append(f"DN{dn}")
        return _finalize_query(
            " ".join(sleeve_parts),
            specialty=specialty,
            canonical_features=canonical_features,
            context_prior=context_prior,
            apply_synonyms=False,
        )

    canonical_entity = canonical_features.get("entity", "")
    is_electrical = (
        any(kw in name for kw in ("电缆", "配管", "穿线", "配线", "桥架", "线槽"))
        or canonical_entity in {"电缆", "配管", "桥架", "配电箱", "开关插座"}
        or specialty in {"C4", "C5", "C11"}
    )
    is_lamp = "灯" in name  # 灯具类走专用的_normalize_bill_name处理
    # 风口/喷口/散流器的φ值是开口直径，不是管道DN，不走管道路由
    is_wind_outlet = any(kw in name for kw in ("风口", "喷口", "散流器"))
    is_window_item = "窗" in name or "百叶" in name
    if (
        (material or dn)
        and not protect_primary_subject
        and not is_electrical
        and not is_lamp
        and not is_wind_outlet
        and not is_window_item
    ):
        reset_query_seed = False

        _inline_subject_match = re.search(
            r'(?:^|\s)\d*[.、．]?\s*(?:名称|类型)[：:]\s*(.+?)'
            r'(?=(?:\s+\d*[.、．]?\s*(?:名称|名称、类型|类型|规格|规格、压力等级|型号|型号、规格|压力|压力等级|连接方式|连接形式|安装部位|其它)[：:])|//|$)',
            full_text,
        )
        _inline_subject = _inline_subject_match.group(1).strip() if _inline_subject_match else ""
        _compact_valve_text = re.sub(r"\s+", "", full_text)
        if not _inline_subject:
            if any(token in _compact_valve_text for token in ("名称:过滤器", "名称：过滤器", "类型:过滤器", "类型：过滤器")):
                _inline_subject = "过滤器"
            elif any(token in _compact_valve_text for token in ("名称:除污器", "名称：除污器", "类型:除污器", "类型：除污器")):
                _inline_subject = "除污器"
            elif any(token in _compact_valve_text for token in ("名称:软接头安装", "名称：软接头安装", "类型:软接头安装", "类型：软接头安装")):
                _inline_subject = "软接头安装"
        _inline_conn = fields.get("连接方式", "") or fields.get("连接形式", "") or params.get("connection", "")
        if not _inline_conn:
            _inline_conn_match = re.search(
                r'连接(?:方式|形式)[：:]\s*([^\s/，,；;]+(?:连接)?)',
                full_text,
            )
            if _inline_conn_match:
                _inline_conn = _inline_conn_match.group(1).strip()
        if not _inline_conn:
            if any(token in _compact_valve_text for token in ("连接方式:法兰连接", "连接方式：法兰连接", "连接形式:法兰连接", "连接形式：法兰连接")):
                _inline_conn = "法兰连接"
            elif any(token in _compact_valve_text for token in ("连接方式:螺纹连接", "连接方式：螺纹连接", "连接形式:螺纹连接", "连接形式：螺纹连接", "连接方式:丝扣连接", "连接方式：丝扣连接", "连接形式:丝扣连接", "连接形式：丝扣连接")):
                _inline_conn = "螺纹连接"

        if any(keyword in _inline_subject for keyword in ("过滤器", "除污器")):
            if "法兰" in _inline_conn:
                name = "过滤器安装(法兰连接)"
            elif "螺纹" in _inline_conn or "丝扣" in _inline_conn:
                name = "过滤器安装(螺纹连接)"
            else:
                _dn_val = int(dn) if dn else 25
                name = "过滤器安装(法兰连接)" if _dn_val >= 50 else "过滤器安装(螺纹连接)"
            material = ""
            reset_query_seed = True
        elif "软接头" in _inline_subject:
            if "法兰" in _inline_conn:
                name = "法兰式软接头安装"
            elif "螺纹" in _inline_conn or "丝扣" in _inline_conn:
                name = "螺纹式软接头安装"
            else:
                _dn_val = int(dn) if dn else 50
                name = "法兰式软接头安装" if _dn_val >= 50 else "螺纹式软接头安装"
            material = ""
            reset_query_seed = True

        # 阀门类清单名称规范化：清单常写"碳钢阀门"/"不锈钢阀门"等材质+阀门泛称，
        # 但定额名统一叫"法兰阀门安装"/"螺纹阀门安装"。直接在路由中替换，
        # 避免依赖_apply_synonyms（可能被其他同义词抢先匹配导致失效）
        _valve_materials = ("碳钢", "不锈钢", "铸铁", "铸钢", "合金钢", "铜")
        if "阀门" in name and any(m in name for m in _valve_materials):
            name = "法兰阀门安装"
            material = ""  # 材质已融入名称，不再单独拼接
            reset_query_seed = True

        # 焊接法兰阀门/螺纹法兰阀门 → 焊接法兰阀安装/螺纹法兰阀安装
        # 清单写"焊接法兰阀门"，但很多省定额叫"焊接法兰阀安装"（无"门"字）
        # 不做此替换时，"焊接"+"法兰"会被BM25匹配到"碳钢法兰安装(焊接)"
        if "焊接法兰阀门" in name:
            name = name.replace("焊接法兰阀门", "焊接法兰阀安装")
            material = ""
            reset_query_seed = True
        elif "螺纹法兰阀门" in name:
            name = name.replace("螺纹法兰阀门", "螺纹法兰阀安装")
            material = ""
            reset_query_seed = True

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
            _conn = fields.get("连接方式", "") or params.get("connection", "")
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
            reset_query_seed = True

        # 连接方式矛盾修复：清单名写"螺纹阀门"但描述中实际是"法兰连接"
        # 例如："螺纹阀门 类型:蝶阀 连接方式:法兰" → 应走法兰阀门
        if "阀门" in name:
            _conn = fields.get("连接方式", "") or params.get("connection", "")
            if "螺纹" in name and "法兰" in _conn:
                name = name.replace("螺纹阀门", "法兰阀门")
                name = name.replace("螺纹阀", "法兰阀门")
                reset_query_seed = True
            elif "法兰" in name and ("螺纹" in _conn or "丝扣" in _conn):
                name = name.replace("法兰阀门", "螺纹阀门")
                reset_query_seed = True

        if reset_query_seed:
            query_parts = []

        # PPR/PP-R管 → 定额标准名称：
        # 清单写"PPR冷水管"/"PP-R管"，定额叫"室内塑料给水管(热熔连接)"或"采暖管道 室内塑料管(热熔连接)"
        # 直接替换为定额名，避免BM25因"PPR"匹配不到"塑料给水管"
        _mat_upper = material.upper() if material else ""
        if ("PPR" in _mat_upper or "PP-R" in _mat_upper) and "阀" not in name:
            _full = f"{name} {description}".upper()
            # 采暖方向：采暖/供暖/地暖/暖气（"热水"属于给水方向，不是采暖）
            if "采暖" in _full or "供暖" in _full or "地暖" in _full or "暖气" in _full or usage == "采暖":
                material = "室内塑料管(热熔连接)"
                if not usage:
                    usage = "采暖管道"
            elif usage == "消防":
                # 消防PPR管 → 走消防方向
                material = "室内塑料管(热熔连接)"
            else:
                # 给水/热水/冷水/未指定 → 给水方向（PPR最常见用途）
                material = "室内塑料给水管(热熔连接)"
            # 连接方式已包含在材质替换中，清空避免重复拼接
            connection = ""

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

        _pipe_text = f"{name} {description} {material}"
        _has_cast_iron_pipe = any(keyword in _pipe_text for keyword in ("铸铁管", "柔性铸铁", "球墨铸铁"))
        _is_drainage_cast_iron = usage == "排水" or any(keyword in _pipe_text for keyword in ("污水", "废水", "排水", "雨水"))
        if _has_cast_iron_pipe and _is_drainage_cast_iron:
            cast_iron_parts = ["给排水管道"]
            if location:
                cast_iron_parts.append(location)
            if "雨水" in _pipe_text:
                family = "柔性铸铁雨水管"
            else:
                family = "柔性铸铁排水管"
            if any(keyword in _pipe_text for keyword in ("机械接口", "机械连接")):
                family += "(机械接口)"
            elif any(keyword in _pipe_text for keyword in ("卡箍", "无承口")):
                family += "(卡箍连接)"
            cast_iron_parts.append(family)
            if dn:
                cast_iron_parts.append(f"DN{dn}")
            return _finalize_query(
                " ".join(cast_iron_parts),
                specialty=specialty,
                canonical_features=canonical_features,
                context_prior=context_prior,
                apply_synonyms=False,
            )

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

        query_parts = [] if reset_query_seed else list(subject_seed_terms[:2])
        if not reset_query_seed:
            for alias_term in quota_alias_seed_terms:
                if alias_term not in query_parts:
                    query_parts.append(alias_term)
        if connection:
            core += f"({connection})"
        if core not in query_parts:
            query_parts.append(core)

        # 风管形状：加入"矩形风管"或"圆形风管"帮助BM25区分
        if shape and "风管" in name:
            query_parts.append(f"{shape}风管")

        if dn:
            query_parts.append(f"DN{dn}")

        if valve_type and valve_type not in "".join(query_parts):
            query_parts.append(valve_type)

        if support_material and any(token in name for token in ("支架", "吊架", "支吊架")):
            query_parts.append(support_material)

        if support_scope and any(token in name for token in ("支架", "吊架", "支吊架")):
            query_parts.append(support_scope)
            if support_scope == "桥架支架":
                query_parts.append("桥架支撑架")
            elif support_scope == "管道支架":
                query_parts.append("一般管架")

        if support_action and any(token in name for token in ("支架", "吊架", "支吊架")):
            query_parts.append(support_action)

        if sanitary_subtype and sanitary_subtype not in "".join(query_parts):
            query_parts.append(sanitary_subtype)

        if sanitary_mount_mode and sanitary_mount_mode not in "".join(query_parts):
            query_parts.append(sanitary_mount_mode)

        if sanitary_flush_mode and sanitary_flush_mode not in "".join(query_parts):
            query_parts.append(sanitary_flush_mode)

        if sanitary_water_mode and sanitary_water_mode not in "".join(query_parts):
            query_parts.append(sanitary_water_mode)

        if sanitary_nozzle_mode and sanitary_nozzle_mode not in "".join(query_parts):
            query_parts.append(sanitary_nozzle_mode)

        if sanitary_tank_mode and sanitary_tank_mode not in "".join(query_parts):
            query_parts.append(sanitary_tank_mode)

        if lamp_type and lamp_type not in "".join(query_parts):
            query_parts.append(lamp_type)

        if surface_process and any(token in full_text for token in ("刷油", "除锈", "油漆", "防锈漆")):
            query_parts.append(surface_process.split("/")[0])

        if material and "管" in material and name and name != material:
            query_parts.append(name)

        # 从描述字段补充设备具体类型（清单名泛称时帮助BM25精准命中）
        desc_type = _extract_desc_equipment_type(fields, name)
        if desc_type:
            query_parts.append(desc_type)

        return _finalize_query(
            " ".join(query_parts),
            specialty=specialty,
            canonical_features=canonical_features,
            context_prior=context_prior,
        )

    switchgear_rule_match = try_rule_match(
        {
            "name": name,
            "description": description,
            "full_text": full_text,
        },
        [
            {
                "name": "switchgear",
                "builder": lambda item: _build_switchgear_query(
                    item["name"],
                    item["description"],
                    item["full_text"],
                    params,
                ),
            },
        ],
    )
    finalized_switchgear_rule_match = _finalize_rule_match_query(
        switchgear_rule_match,
        specialty=specialty,
        canonical_features=canonical_features,
        context_prior=context_prior,
    )
    if finalized_switchgear_rule_match:
        return finalized_switchgear_rule_match
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
            return _finalize_query(
                f"{normalized_name} {desc_type} 安装 周长",
                specialty=specialty,
                canonical_features=canonical_features,
                context_prior=context_prior,
            )
        return _finalize_query(
            f"{normalized_name} 安装 周长",
            specialty=specialty,
            canonical_features=canonical_features,
            context_prior=context_prior,
        )

    # ===== 非管道类：从描述中提取关键信息构建query =====
    # 电气设备、灯具、电缆、配管、配线等

    distribution_box_route_text = guarded_full_text if protect_primary_subject else full_text
    allow_distribution_box_template = (
        not protect_primary_subject
        or any(
            keyword in distribution_box_route_text
            for keyword in (
                "配电箱",
                "配电柜",
                "控制箱",
                "控制柜",
                "程序控制箱",
                "动力箱",
                "照明箱",
                "双电源箱",
                "双电源配电箱",
            )
        )
    )
    late_rule_match = try_rule_match(
        {
            "name": name,
            "description": description,
            "full_text": full_text,
            "distribution_box_route_text": distribution_box_route_text,
            "allow_distribution_box_template": allow_distribution_box_template,
            "specialty": specialty,
        },
        [
            {
                "name": "distribution_box",
                "enabled": lambda: allow_distribution_box_template,
                "builder": lambda item: _build_distribution_box_query(
                    name=item["name"],
                    description=item["description"],
                    full_text=item["distribution_box_route_text"],
                    fields=fields,
                    params=params,
                    specialty=item["specialty"],
                ),
                "apply_synonyms": False,
            },
            {
                "name": "sanitary",
                "builder": lambda item: _build_sanitary_query(
                    name=item["name"],
                    full_text=item["full_text"],
                    params=params,
                ),
                "apply_synonyms": False,
            },
            {
                "name": "surge_protector",
                "builder": lambda item: _build_surge_protector_query(
                    name=item["name"],
                    full_text=item["full_text"],
                    params=params,
                ),
                "apply_synonyms": False,
            },
            {
                "name": "cable_head",
                "builder": lambda item: _build_cable_head_query(
                    name=item["name"],
                    full_text=item["full_text"],
                    params=params,
                    specialty=item["specialty"],
                ),
                "apply_synonyms": False,
            },
        ],
    )
    finalized_late_rule_match = _finalize_rule_match_query(
        late_rule_match,
        specialty=specialty,
        canonical_features=canonical_features,
        context_prior=context_prior,
    )
    if finalized_late_rule_match:
        return finalized_late_rule_match

    # 清单名称 → 定额搜索名称的规范化映射
    # 清单用的名称和定额用的名称经常不一样
    subject_name = str(name or "")
    apply_discovered_subject = _should_apply_discovered_subject(raw_input_name, fields, subject_info)
    if apply_discovered_subject:
        subject_name = str(subject_info.get("primary_subject") or subject_name)
    normalized_name = _normalize_bill_name(subject_name)
    query_parts = list(subject_seed_terms[:2])
    for alias_term in quota_alias_seed_terms:
        if alias_term not in query_parts:
            query_parts.append(alias_term)
    if (
        not list(subject_info.get("decisive_terms") or [])
        and query_parts
        and normalized_name
        and normalized_name not in query_parts[0]
    ):
        query_parts = []
    if normalized_name not in query_parts:
        query_parts.append(normalized_name)
    raw_subject_name = _normalize_primary_guard_text(raw_input_name)
    primary_subject = _normalize_primary_guard_text(subject_info.get("primary_subject", ""))
    should_append_subject_specs = (
        (apply_discovered_subject or not raw_subject_name or raw_subject_name in _PRIMARY_SUBJECT_GENERIC_NAMES)
        and len(primary_subject) > 2
    )
    if should_append_subject_specs:
        for spec in list(subject_info.get("key_specs") or [])[:2]:
            token = _normalize_primary_guard_text(spec)
            if token and token not in query_parts:
                query_parts.append(token)
    if should_append_subject_specs:
        for term in list(subject_info.get("decisive_terms") or [])[:3]:
            token = _normalize_primary_guard_text(term)
            if token and token not in query_parts:
                query_parts.append(token)

    # --- 桥架类：清理尺寸噪声，构建桥架安装搜索词 ---
    # 清单写"热镀锌桥架100*50"，定额叫"钢制槽式桥架(宽+高)(mm以下) 200"
    # 100*50 的数字噪声会让 BM25 匹配到含"100×140"的混凝土结构定额
    canonical_family = str((canonical_features or {}).get("family") or "").strip()
    laying_method_hint = str((canonical_features or {}).get("laying_method") or "").strip()
    bridge_object_name = "桥架" in name or canonical_family == "bridge_raceway"
    explicit_cable_laying_name = any(keyword in name for keyword in ("桥架配线", "桥架敷设", "沿桥架", "桥架内配线"))
    if (
        bridge_object_name
        and "配线" not in name
        and "穿线" not in name
        and not explicit_cable_laying_name
        and not (canonical_family == "cable_family" and "桥架" in laying_method_hint)
    ):
        # 去掉尺寸数字（如"100*50"、"200*100"）和尾部连字符
        clean = re.sub(r'\d+\s*[*×xX]\s*\d+', '', name).strip()
        clean = re.sub(r'[-—_]+$', '', clean).strip()
        if not clean:
            clean = "桥架"
        bridge_type = ""
        for candidate in ("槽式", "托盘式", "梯式", "线槽"):
            if candidate in full_text:
                bridge_type = candidate
                break
        if bridge_type and bridge_type not in clean:
            clean = f"{bridge_type}桥架" if bridge_type != "线槽" else "线槽"
        query_parts[0] = clean + " 安装"
        if bridge_wh_sum:
            query_parts.append(f"宽+高 {bridge_wh_sum:g}")
        return _finalize_query(
            " ".join(query_parts),
            specialty=specialty,
            canonical_features=canonical_features,
            context_prior=context_prior,
        )

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
        conduit_keywords = ("配管", "SC管", "JDG管", "KBG管", "PVC管", "导管", "穿线管", "金属软管", "可挠金属套管")
        is_conduit = (
            (any(kw in name for kw in conduit_keywords) and "电缆" not in name)
            or bool(conduit_type)
        )

        # --- 配管材质+配置形式+管径：fields.get经常失败，统一用正则从全文提取 ---
        if is_conduit and not protect_primary_subject:
            full_text = f"{name} {description}"
            normalized_conduit_text = full_text.upper().replace("KJG", "KBG")
            explicit_electrical_conduit = _is_explicit_electrical_conduit_context(
                name,
                full_text,
                specialty=specialty,
                canonical_features=canonical_features,
                context_prior=context_prior,
            )

            # 1. 材质型号：从全文提取SC/JDG/KBG/PC等代号
            conduit_code = conduit_type or None
            # 匹配配管材质代号（按长度降序，避免短代号抢先匹配）
            # G/RC/MT 是单/双字母代号，用\b边界防止从JDG/DG中误提取
            mat_match = re.search(
                r'(JDG|KBG|FPC|PVC|SC|PC|DG|RC|MT|G)(?:管)?\s*\d*',
                normalized_conduit_text)
            if mat_match and not conduit_code:
                conduit_code = mat_match.group(1)

            if "金属软管" in full_text:
                query_parts = ["金属软管敷设"]
            elif "可挠金属套管" in full_text:
                query_parts = ["可挠金属套管"]
            # JDG/KBG是紧定式钢导管，和普通镀锌钢管是不同定额子目
            # 替换query_parts[0]让BM25能匹配"套接紧定式镀锌钢导管(JDG)"
            # 加"套接"关键词提升JDG条目的BM25分数（区别于普通镀锌钢管）
            elif conduit_code in ("JDG", "KBG"):
                guide_code = conduit_code
                query_parts = [f"套接紧定式钢导管{guide_code} 镀锌电线管 敷设"]
            elif conduit_code in ("PC", "PVC"):
                query_parts = ["PVC阻燃塑料管敷设"]
            elif conduit_code == "FPC":
                query_parts = ["半硬质阻燃管敷设"]
            elif explicit_electrical_conduit and conduit_code in ("SC", "G", "DG", "RC", "MT"):
                query_parts = [_build_ambiguous_electrical_conduit_query(
                    conduit_code,
                    context_prior=context_prior,
                )]
            elif explicit_electrical_conduit and not conduit_code:
                query_parts = [_build_ambiguous_electrical_conduit_query(
                    context_prior=context_prior,
                )]
            else:
                # SC=焊接钢管, G/DG=镀锌钢管, 分开写让BM25能精准命中
                if conduit_code == "SC":
                    query_parts = ["焊接钢管敷设"]
                elif conduit_code in ("G", "DG"):
                    query_parts = ["镀锌钢管敷设"]
                elif conduit_code in ("RC", "MT"):
                    query_parts = ["镀锌电线管敷设"]
                else:
                    # 无材质代号时用通用"钢管敷设"
                    query_parts = ["钢管敷设"]

            # 2. 配置形式：暗配/明配（加"砖混凝土结构"限定，避免匹配到"钢模板暗配"）
            config_match = re.search(
                r'配置形式[：:]\s*(.*?)(?:\s|含|工作|其他|$)',
                full_text)
            layout_hint = ""
            if config_match:
                layout_hint = config_match.group(1)
            if not layout_hint and laying_method:
                layout_hint = laying_method
            if "暗" in layout_hint:
                query_parts.append("砖混凝土结构暗配")
            elif "明" in layout_hint:
                query_parts.append("砖混凝土结构明配")

            # 3. 管径：从"SC25"、"JDG32"、"Φ20"或"规格:25"提取
            query_str = " ".join(query_parts)
            if "公称直径" not in query_str and "外径" not in query_str:
                # 先匹配材质代号后直接跟数字（SC25, JDG32, RC20, MT16, G20）或DN前缀（DN100）
                if conduit_dn is not None:
                    query_parts.append(f"公称直径 {int(conduit_dn)}")
                else:
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
            if ("明配" in full_text or "明敷" in full_text) and "明配" not in " ".join(query_parts):
                query_parts.append("砖混凝土结构明配")
            elif ("暗配" in full_text or "暗敷" in full_text) and "暗配" not in " ".join(query_parts):
                query_parts.append("砖混凝土结构暗配")
            return _finalize_query(
                " ".join(query_parts),
                specialty=specialty,
                canonical_features=canonical_features,
                context_prior=context_prior,
                apply_synonyms=False,
            )
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
            parsed_wire_section = params.get("cable_section")
            normalized_wire_text = re.sub(r'(?i)mm(?:2|²)', '', wire_text).replace("㎡", "")
            # 优先匹配多芯格式：2×2.5 → 取单芯截面2.5
            wire_sec = re.search(r'(\d+)\s*[×xX*]\s*(\d+(?:\.\d+)?)', normalized_wire_text)
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
                wire_sec = re.search(r'(\d+(?:\.\d+)?)\s*$', normalized_wire_text)
                if wire_sec:
                    section = float(wire_sec.group(1))
            if section is None and isinstance(parsed_wire_section, (int, float)) and parsed_wire_section > 0:
                section = float(parsed_wire_section)
            if section:
                query_parts.append(f"导线截面 {_format_number_for_query(section)}")

            # 构建搜索词：桥架配线 / 多芯软导线 / 照明线 / 动力线
            # 只有识别出已知线型(BYJ/BV/RVV等)才特化，否则保持原名（如UTP双绞线等弱电）
            if any(token in laying_method for token in ("桥架", "线槽")) or "桥架" in name or "线槽" in name or "桥架" in description or "线槽" in description:
                query_parts[0] = "线槽配线"
            elif any(token in laying_method for token in ("穿管", "明配", "暗配")) or "管内穿线" in full_text or "管内" in full_text:
                if wire_type_known and is_multi_core:
                    query_parts[0] = "管内穿线 穿多芯软导线"
                elif wire_type_known and section and section > 6:
                    query_parts[0] = f"管内穿线 穿动力线 {core_material}"
                    query_parts.append("\u52a8\u529b\u7ebf\u8def")
                elif wire_type_known:
                    query_parts[0] = f"管内穿线 穿照明线 {core_material}"
                    query_parts.append("\u7167\u660e\u7ebf\u8def")
                else:
                    query_parts[0] = "管内穿线"
            elif wire_type_known and is_multi_core:
                query_parts[0] = "穿多芯软导线"
            elif wire_type_known and section and section > 6:
                query_parts[0] = f"穿动力线 {core_material}"
            elif wire_type_known:
                query_parts[0] = f"穿照明线 {core_material}"
            # else: 未识别的线型（如UTP/STP等弱电），保持原名不改

        # --- 电缆类：根据敷设方式构建query ---
        # 北京2024定额按敷设方式命名：电缆埋地/沿墙面/沿桥架/穿导管敷设
        cable_query_text = full_text
        if "电缆" in name and not any(token in name for token in ("终端头", "中间头", "电缆头")):
            cable_query_text = _strip_cable_accessory_noise(full_text)
        cable_model = fields.get("规格", "") or fields.get("型号", "")
        # fields提取经常失败，从全文正则提取电缆型号和敷设方式
        if not cable_model:
            model_match = re.search(
                r'(?:型号|规格)[：:,]*\s*'
                r'((?:WDZ[A-Z0-9]*-|ZR[A-Z]?-|NH-|ZB[N]?-)?'
                r'(?:YJV|YJY|VV|BTTRZ|BTLY|BTTZ|YTTW|BBTRZ|KYJY|KVV|KVVP)'
                r'[A-Z0-9.*×xX/\-]*)',
                cable_query_text.upper())
            if model_match:
                cable_model = model_match.group(1)
        is_cable = ("电缆" in name and "终端头" not in name
                    and "电缆头" not in name and "保护管" not in name)
        is_control_cable = ("控制" in name or "信号" in name
                            or "控制" in cable_model.upper())
        if is_cable:
            conductor = _infer_cable_conductor(
                text=cable_query_text,
                material=material,
                wire_type=wire_type,
            )
            # 敷设方式：先从fields取，再从全文正则提取
            laying_raw = fields.get("敷设方式", "") or fields.get("敷设方式、部位", "")
            if not laying_raw:
                lay_match = re.search(r'敷设方式[、部位]*[：:]\s*(.+?)(?:\s|电压|$)', cable_query_text)
                if lay_match:
                    laying_raw = lay_match.group(1)

            # 从电缆型号推断敷设方式（行业惯例）
            if not laying_raw and cable_model:
                model_upper = cable_model.upper()
                if "22" in model_upper or "23" in model_upper:
                    laying_raw = "埋地"  # YJV22/VV22=钢带铠装→埋地

            effective_laying = laying_method or laying_raw

            # 控制电缆：按敷设方式+芯数构建query
            # 控制电缆按芯数分档（6/14/24/37/48芯），和电力电缆按截面分档不同
            if is_control_cable:
                if "桥架" in effective_laying and "穿管" in effective_laying:
                    query_parts[0] = "控制电缆敷设"
                    query_parts.extend(["桥架", "穿管"])
                elif "桥架" in effective_laying or "线槽" in effective_laying:
                    query_parts[0] = "控制电缆沿桥架敷设"
                elif "支架" in effective_laying:
                    query_parts[0] = "控制电缆沿支架敷设"
                elif "埋地" in effective_laying or "直埋" in effective_laying:
                    query_parts[0] = "控制电缆埋地敷设"
                elif "排管" in effective_laying:
                    query_parts[0] = "控制电缆排管敷设"
                elif "穿管" in effective_laying or "管" in effective_laying:
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
                query_parts[0] = "矿物绝缘电缆敷设"
            # 普通电力电缆按敷设方式
            elif "桥架" in effective_laying and "穿管" in effective_laying:
                query_parts[0] = "室内敷设电力电缆"
                query_parts.extend(["桥架", "穿管"])
            elif "桥架" in effective_laying or "线槽" in effective_laying:
                # 不用"线槽"避免BM25误匹配"金属线槽敷设"（线槽是另一品类）
                # "室内敷设电力电缆 沿桥架"兼容两种命名风格：
                #   北京: "电缆沿桥架、线槽敷设"（BM25匹配"电缆""桥架""敷设"）
                #   江西: "室内敷设电力电缆"（BM25匹配"室内""敷设""电力电缆"）
                query_parts[0] = "室内敷设电力电缆 沿桥架"
            elif "排管" in effective_laying:
                query_parts[0] = "排管内电力电缆敷设"
            elif "穿管" in effective_laying or "管" in effective_laying:
                query_parts[0] = "电缆穿导管敷设"
            elif "埋地" in effective_laying or "直埋" in effective_laying:
                query_parts[0] = "电缆埋地敷设"
            elif "墙" in effective_laying or "支架" in effective_laying:
                query_parts[0] = "电缆沿墙面、支架敷设"
            elif "室内" in effective_laying:
                query_parts[0] = "室内敷设电力电缆"
            else:
                query_parts[0] = "室内敷设电力电缆"  # 默认室内（最常见）

            if conductor and conductor not in "".join(query_parts):
                if conductor == "铝合金":
                    query_parts.append("铝合金")
                else:
                    query_parts.append(conductor)
            if wire_type == "BPYJV" or "变频" in full_text:
                query_parts.append("变频")

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
    desc_type = _extract_desc_equipment_type(fields, subject_name or name)
    if desc_type:
        query_parts.append(desc_type)

    is_floor_outlet = "\u5730\u9762\u63d2\u5ea7" in full_text or "\u5730\u63d2" in full_text
    if is_floor_outlet:
        if query_parts:
            query_parts[0] = "\u5730\u9762\u63d2\u5ea7"
        else:
            query_parts.append("\u5730\u9762\u63d2\u5ea7")
        query_parts = [part for part in query_parts if part != "\u666e\u901a\u63d2\u5ea7\u5b89\u88c5"]
        if "\u5730\u9762\u63d2\u5ea7\u5b89\u88c5" not in query_parts:
            query_parts.append("\u5730\u9762\u63d2\u5ea7\u5b89\u88c5")

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
        if (
            not is_floor_outlet
            and not has_install
            and "明装" not in full_text
            and "明配" not in full_text
        ):
            query_parts.append("暗装")

    query = _finalize_query(
        " ".join(query_parts),
        specialty=specialty,
        canonical_features=canonical_features,
        context_prior=context_prior,
        apply_synonyms=not is_floor_outlet,
    )

    # 拆除项后处理：去掉同义词追加的"安装"，确保"拆除"在query中
    # 例如 "拆除马桶 坐式大便器安装" → "拆除马桶 坐式大便器 拆除"
    if _is_demolition:
        query = query.replace("安装", "").strip()
        query = re.sub(r'\s+', ' ', query)
        if "拆除" not in query:
            query = f"{query} 拆除"

    return query
