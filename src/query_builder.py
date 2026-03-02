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
    """应用工程同义词替换：把清单常用名替换为定额常用名

    例如：
      "镀锌钢管 DN25" → "焊接钢管 镀锌 DN25"
      "PPR管 热熔连接" → "PP-R管 热熔连接"

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

    for key, replacement in synonyms.items():
        if key in query:
            if _is_synonym_applicable(key, specialty, scope):
                query = query.replace(key, replacement, 1)  # 只替换第一次出现
                break  # 只做一次替换，避免连锁替换引发副作用

    return query
_SPECIAL_LAMP_PATTERN = r"紫外|杀菌|消毒|舞台|投光|泛光|景观|水下|地埋|航空障碍|手术|无影|植物|补光|洗墙|轨道"


def _format_number_for_query(value: float) -> str:
    """数值格式化：整数去小数点，小数保留原样。"""
    return str(int(value)) if value == int(value) else str(value)


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

        # 防爆灯 → 密闭灯安装 防爆灯
        if "防爆" in cleaned and "灯" in cleaned:
            return "密闭灯安装 防爆灯"

        # 防水防尘灯（非壁灯、非吸顶灯的其他防水灯）
        if ("防水" in cleaned or "防尘" in cleaned or "防潮" in cleaned) and "灯" in cleaned:
            return "防水防尘灯安装"

        # 线槽灯 → LED灯带 灯管式（线槽灯安装在线槽内，安装工艺接近LED灯带）
        if "线槽灯" in cleaned:
            return "LED灯带 灯管式"

        # 直管灯/灯管 → 荧光灯具安装（管状灯具不论LED还是荧光，套荧光灯安装定额）
        if re.search(r'直管|灯管', cleaned):
            return "荧光灯具安装 单管"

        # 井道灯 → 密闭灯安装（井道用密闭灯）
        if "井道灯" in cleaned:
            return "密闭灯安装 防潮灯"

        # 荧光灯具：提取安装方式和管数
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
            if "疏散照明" in cleaned:
                return "智能应急灯具及标志灯具安装 应急灯"
            if "指示" in cleaned or "标志" in cleaned:
                return "智能应急灯具及标志灯具安装 标志灯"
            return "智能应急灯具及标志灯具安装"

        # 应急灯/应急照明灯
        if "应急" in cleaned:
            # 应急+指示灯 → 标志灯方向（不是荧光灯）
            # 例如"应急疏散指示灯"是标志灯，不是照明灯
            if "指示" in cleaned:
                if "壁" in cleaned or "单面" in cleaned or "双面" in cleaned:
                    return "标志、诱导灯安装 壁式"
                return "标志、诱导灯安装 壁式"
            if "吸顶" in cleaned:
                return "普通灯具安装 吸顶灯"
            if "疏散" in cleaned or "照明" in cleaned:
                return "荧光灯具安装"
            return "荧光灯具安装"

        # 疏散指示灯/标志灯/出口指示灯 → 标志、诱导灯安装
        if re.search(r'疏散|指示灯|标志灯|诱导灯|出口.*灯|楼层.*灯', cleaned):
            if "壁" in cleaned or "单面" in cleaned or "双面" in cleaned:
                return "标志、诱导灯安装 壁式"
            if "嵌入" in cleaned or "地面" in cleaned:
                return "标志、诱导灯安装 地面嵌入式"
            if "吸顶" in cleaned:
                return "标志、诱导灯安装 吸顶式"
            return "标志、诱导灯安装 壁式"

        # 单管灯/双管灯/三管灯（不含"荧光"字样的简称）→ 荧光灯具安装
        tube_match = re.search(r'(单管|双管|三管)灯', cleaned)
        if tube_match:
            tube_map = {"单管": "单管", "双管": "双管", "三管": "三管"}
            tube = tube_map.get(tube_match.group(1), "单管")
            return f"荧光灯具安装 吸顶式 {tube}"

        # 坡道灯/过渡照明灯/照明灯 → 普通灯具安装
        if re.search(r'照明灯|过渡灯|坡道.*灯', cleaned):
            return "普通灯具安装 吸顶灯"

        # 通用灯具兜底：保留cleaned（已去除LED/瓦数/电压噪声）
        return cleaned

    # 接线盒（86mm的小接线盒，不是通信用的大接线箱）
    if name == "接线盒":
        return "接线盒安装"

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
    full_text = f"{name} {description}".strip()
    # 优先使用清单清洗阶段已清洗的参数（如卫生器具已剔除DN）
    params = bill_params if bill_params is not None else parser.parse(full_text)

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

    # ===== 管道类：有材质或DN参数，且不是电气类（电缆/配管/穿线） =====
    # 电气类即使有material/dn也应走下面的电气专用query构建
    # 灯具类也不走管道路由（描述中"保护管"等配件词会被误提取为材质）
    is_electrical = any(kw in name for kw in ("电缆", "配管", "穿线", "配线", "桥架", "线槽"))
    is_lamp = "灯" in name  # 灯具类走专用的_normalize_bill_name处理
    # 风口/喷口/散流器的φ值是开口直径，不是管道DN，不走管道路由
    is_wind_outlet = any(kw in name for kw in ("风口", "喷口", "散流器"))
    if (material or dn) and not is_electrical and not is_lamp and not is_wind_outlet:
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

    # ===== 周长类：风口/散流器/消声器等按周长取档的设备 =====
    # 这类设备没有材质/DN参数，但有周长参数（从规格如1200*100计算得来）
    # 在搜索词中加入"安装"和"周长"关键词，引导BM25匹配定额名中含
    # "XX安装 XX周长(mm) ≤XXXX"的子目，避免被无关的制作/材料定额干扰
    # 具体周长值的取档由param_validator完成
    perimeter = params.get("perimeter")
    # 只有风口类设备（风口/喷口/散流器）才走周长路由
    # 静压箱等箱体的W*H是箱体尺寸，不是开口周长，不应走此路由
    if perimeter and is_wind_outlet:
        normalized_name = _normalize_bill_name(name)
        return _apply_synonyms(f"{normalized_name} 安装 周长", specialty)

    # ===== 非管道类：从描述中提取关键信息构建query =====
    # 电气设备、灯具、电缆、配管、配线等

    # 清单名称 → 定额搜索名称的规范化映射
    # 清单用的名称和定额用的名称经常不一样
    normalized_name = _normalize_bill_name(name)
    query_parts = [normalized_name]

    if description:
        # 从 "1.标签:值\n2.标签:值" 格式的描述中提取关键字段
        fields = extract_description_fields(description)

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

            # 1. 材质型号：从全文提取SC/JDG/KBG/PC等代号
            conduit_code = None
            mat_match = re.search(
                r'(JDG|KBG|FPC|PVC|SC|PC|DG)\s*\d*',
                full_text.upper())
            if mat_match:
                conduit_code = mat_match.group(1)

            # JDG/KBG是紧定式钢导管，和普通镀锌钢管是不同定额子目
            # 替换query_parts[0]让BM25能匹配"套接紧定式镀锌钢导管(JDG)"
            if conduit_code in ("JDG", "KBG"):
                query_parts[0] = "紧定式钢导管 JDG敷设"
            elif conduit_code in ("PC", "PVC", "FPC"):
                query_parts[0] = "PVC阻燃塑料管敷设"
            # SC/G/DG → 镀锌钢管，synonym "配管"→"镀锌钢管敷设" 已覆盖

            # 2. 配置形式：暗配/明配（关键！决定定额子目）
            config_match = re.search(
                r'配置形式[：:]\s*(.*?)(?:\s|含|工作|其他|$)',
                full_text)
            if config_match:
                config_raw = config_match.group(1)
                if "暗" in config_raw:
                    query_parts.append("暗配")
                elif "明" in config_raw:
                    query_parts.append("明配")

            # 3. 管径：从"SC25"、"JDG32"、"Φ20"或"规格:25"提取
            query_str = " ".join(query_parts)
            if "公称直径" not in query_str and "外径" not in query_str:
                # 先匹配材质代号后直接跟数字（SC25, JDG32）
                size_match = re.search(
                    r'(?:SC|JDG|KBG|PC|Φ|φ)\s*(\d+)',
                    full_text, re.IGNORECASE)
                # 再匹配规格字段中的数字（规格:25, 规格:Φ20, 规格:DN25）
                if not size_match:
                    size_match = re.search(
                        r'规格[：:]\s*(?:Φ|φ|DN)?\s*(\d+)',
                        full_text)
                if size_match:
                    query_parts.append(f"公称直径 {size_match.group(1)}")
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
                section = float(wire_sec.group(2))
                is_multi_core = True  # N×M格式一定是多芯线
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
            if "桥架" in name or "线槽" in name or "桥架" in description:
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
                r'(?:YJV|YJY|VV|BTTRZ|YTTW|BBTRZ|KYJY|KVV|KVVP)'
                r'[A-Z0-9.*×xX/\-]*)',
                full_text.upper())
            if model_match:
                cable_model = model_match.group(1)
        is_cable = "电缆" in name and "终端头" not in name and "电缆头" not in name
        is_control_cable = "控制" in name or "控制" in cable_model.upper()
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

            # 控制电缆：搜"控制电缆"（同义词会自动加"敷设"，不要手动加）
            if is_control_cable:
                query_parts[0] = "控制电缆"
            # 矿物绝缘电缆：BTTRZ/YTTW/BBTRZ
            elif cable_model and any(m in cable_model.upper()
                                     for m in ("BTTRZ", "YTTW", "BBTRZ")):
                query_parts[0] = "矿物绝缘电缆"
            # 普通电力电缆按敷设方式
            elif "桥架" in laying_raw or "线槽" in laying_raw:
                # 用无逗号格式避免"桥架"被同义词替换成"电缆桥架安装"
                query_parts[0] = "电缆沿桥架线槽敷设"
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

    return _apply_synonyms(" ".join(query_parts), specialty)