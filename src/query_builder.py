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
    """加载工程同义词表（惰性加载，只读一次文件）"""
    global _SYNONYMS_CACHE
    if _SYNONYMS_CACHE is not None:
        return _SYNONYMS_CACHE

    synonyms_path = Path(__file__).parent.parent / "data" / "engineering_synonyms.json"
    try:
        with open(synonyms_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # 过滤掉说明字段和空值，只保留有效的同义词映射
        _SYNONYMS_CACHE = {
            k: v[0] for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, list) and v
        }
        # 按key长度降序排列，优先匹配长词（避免"PE管"先于"HDPE管"匹配）
        _SYNONYMS_CACHE = dict(
            sorted(_SYNONYMS_CACHE.items(), key=lambda x: len(x[0]), reverse=True)
        )
    except Exception as e:
        logger.debug(f"工程同义词表加载失败（不影响基础搜索）: {e}")
        _SYNONYMS_CACHE = {}

    return _SYNONYMS_CACHE


def _apply_synonyms(query: str, specialty: str = "") -> str:
    """应用工程同义词替换：把清单常用名替换为定额常用名

    例如：
      "镀锌钢管 DN25" → "焊接钢管 镀锌 DN25"
      "PPR管 热熔连接" → "PP-R管 热熔连接"

    参数:
        query: 搜索query字符串
        specialty: 清单所属专业册号（如"C10"、"A"等）
            当前同义词表仅覆盖安装专业（C1~C12），
            非安装专业时跳过同义词替换，避免误替换。
    """
    # 当前同义词表仅适用于安装专业（C开头的册号）
    # 没有 specialty 时也应用（兼容旧调用、无分类信息的场景）
    if specialty and not specialty.upper().startswith("C"):
        return query

    synonyms = _load_synonyms()
    if not synonyms:
        return query

    for key, replacement in synonyms.items():
        if key in query:
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
                      specialty: str = "") -> str:
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

    返回:
        构建好的搜索query
    """
    full_text = f"{name} {description}".strip()
    params = parser.parse(full_text)

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
    if (material or dn) and not is_electrical and not is_lamp:
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
        conduit_mat = fields.get("材质", "")
        if conduit_mat:
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
            # 按代码长度降序匹配（避免"G"先于"JDG"匹配）
            for code in sorted(conduit_map, key=len, reverse=True):
                if code in conduit_mat.upper():
                    query_parts.append(conduit_map[code])
                    break

        # 配管规格（公称直径）—— 仅配管类，不含穿线/电缆
        conduit_spec = fields.get("规格", "") or fields.get("规格型号", "")
        is_conduit = "配管" in name and "穿线" not in name and "电缆" not in name
        if conduit_spec and is_conduit:
            spec_match = re.search(r'(\d+)', conduit_spec)
            if spec_match:
                query_parts.append(f"公称直径 {spec_match.group(1)}")

        # 配置形式/敷设方式
        config_form = fields.get("配置形式", "")
        if config_form:
            if "暗" in config_form:
                query_parts.append("暗配")
            elif "明" in config_form:
                query_parts.append("明配")

        # --- 配线类：导线型号→定额名称 ---
        wire_spec = fields.get("规格", "")
        if "穿线" in name or "配线" in name:
            # 电线型号 → 定额搜索关键词
            # BV/BYJ等都是铜芯电线，定额叫"管内穿铜芯线照明/动力线路"
            wire_map = {
                "BYJ":  "管内穿铜芯线",    # 交联聚乙烯绝缘电线
                "BV":   "管内穿铜芯线",    # 聚氯乙烯绝缘电线
                "BVR":  "管内穿铜芯线",    # 铜芯软电线
                "BLV":  "管内穿铝芯线",    # 铝芯电线
                "RVS":  "管内穿铜芯线",    # 铜芯绞线
                "RVVP": "管内穿铜芯线",    # 屏蔽软线
                "RVV":  "管内穿铜芯线",    # 铜芯软护套线
            }
            # 先剥离阻燃/耐火等前缀修饰符，暴露基础型号
            # 如 WDZB1N-BYJ4 → BYJ4, ZR-BV2.5 → BV2.5
            wire_prefixes = [
                "WDZB1N-", "WDZBN-", "WDZN-", "WDZ-",
                "ZRC-", "ZRB-", "ZR-", "NH-",
            ]
            wire_base = wire_spec.upper()
            for prefix in wire_prefixes:
                if wire_base.startswith(prefix):
                    wire_base = wire_base[len(prefix):]
                    break
            # 用剥离后的基础型号匹配
            matched_wire = False
            for code, wname in wire_map.items():
                if wire_base.startswith(code):
                    query_parts[0] = wname  # 替换名称
                    matched_wire = True
                    break
            # 降级：如果剥离后没匹配，用原文尝试（兼容旧逻辑）
            if not matched_wire:
                for code, wname in wire_map.items():
                    if code in wire_spec.upper() or code in full_text.upper():
                        query_parts[0] = wname
                        break
            # 导线截面（从基础型号中提取数字，如 BYJ4 → 4）
            wire_sec = re.search(r'[A-Za-z]+\s*(\d+(?:\.\d+)?)', wire_base or wire_spec)
            if wire_sec:
                query_parts.append(f"导线截面 {wire_sec.group(1)}")

        # --- 电缆类：根据敷设方式构建query ---
        # 北京2024定额按敷设方式命名：电缆埋地/沿墙面/沿桥架/穿导管敷设
        cable_model = fields.get("规格", "") or fields.get("型号", "")
        if "电缆" in name and not "终端头" in name and not "电缆头" in name:
            # 敷设方式决定定额名称
            laying_raw = fields.get("敷设方式", "") or fields.get("敷设方式、部位", "")
            if "桥架" in laying_raw or "线槽" in laying_raw:
                query_parts[0] = "电缆沿桥架、线槽敷设"
            elif "管" in laying_raw:
                query_parts[0] = "电缆穿导管敷设"
            elif "埋地" in laying_raw or "直埋" in laying_raw:
                query_parts[0] = "电缆埋地敷设"
            elif "墙" in laying_raw or "支架" in laying_raw:
                query_parts[0] = "电缆沿墙面、支架敷设"
            else:
                query_parts[0] = "电缆敷设"  # 通用

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

            # 电缆截面（非电缆类一般用不到，但保留兼容）
            if cable_section:
                section_str = _format_number_for_query(cable_section)
                query_parts.append(f"截面{section_str}")

        # --- 安装方式（配电箱、灯具、插座等通用） ---
        install = fields.get("安装方式", "")
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