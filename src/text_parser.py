"""
结构化文本解析器
从定额名称和清单描述中提取结构化参数：
- 管径(DN)、截面(mm²)、电流(A)、重量(t/kg)
- 材质、连接方式、设备类型等文字参数
- 数值统一格式化（DN150→150, 4×185→185等）

材质和连接方式的识别词汇有两个来源：
1. 手动维护的"基础列表"（保证核心材质的正确顺序）
2. 从定额库自动提取的词汇（通过 vocab_extractor 反向学习）
两者合并后按长度降序排列，确保长词优先匹配（避免"钢塑复合管"被"钢管"截断）
"""

import math
import re
from collections import OrderedDict
from pathlib import Path
from typing import Optional
import threading

from loguru import logger

from src.canonical_features import build_canonical_features

_CABLE_PREFIX_PATTERN = r'(?:WDZ[A-Z]*-?|ZA[N]?-?|NH-?|N-?|ZR[A-E]?-?|ZC-?)*'
_ALUMINUM_CORE_MODEL_PATTERNS = (
    "BPYJLV",
    "YJLV",
    "JKLV",
    "VLV",
    "BLV",
    "LGJ",
)
_COPPER_CORE_MODEL_PATTERNS = (
    "BPYJV",
    "BPYJY",
    "YJV",
    "YJY",
    "YJFE",
    "JKYJ",
    "JKV",
    "KYJY",
    "KVV",
    "KVVP",
    "KVVR",
    "VV",
    "VY",
    "BYJ",
    "BV",
    "BVR",
    "RVV",
    "RVS",
    "YTTW",
    "BBTRZ",
    "BTTZ",
    "BTLY",
)
_MINERAL_CABLE_MODEL_PATTERNS = (
    "BTTRZ",
    "BTTVZ",
    "BTLY",
    "BTTZ",
    "TBTRZY",
    "YTTW",
    "BBTRZ",
    "NG-A",
)
_CONTROL_CABLE_MODEL_PATTERNS = (
    "DJYPVP",
    "DJYPV",
    "DJYVP",
    "DJYJV",
    "KYJVP",
    "KYJV",
    "KYJY",
    "KVVP2",
    "KVV22",
    "KVVP",
    "KVVR",
    "KVV",
)
_POWER_CABLE_MODEL_PATTERNS = (
    "BPYJLV",
    "BPYJV",
    "BPYJY",
    "YJLV",
    "YJV",
    "YJY",
    "YJFE",
    "JKLV",
    "JKYJ",
    "VLV",
    "VV",
    "VY",
)
_SOFT_WIRE_MODEL_PATTERNS = (
    "RYJSP",
    "RYJS",
    "RVVP",
    "RVV",
    "RVS",
)
_WIRE_MODEL_PATTERNS = (
    "BYJ",
    "BYJR",
    "BYP",
    "BVR",
    "BLV",
    "BV",
)
_ALL_WIRE_TYPE_PATTERNS = tuple(dict.fromkeys(
    list(_MINERAL_CABLE_MODEL_PATTERNS)
    + list(_CONTROL_CABLE_MODEL_PATTERNS)
    + list(_POWER_CABLE_MODEL_PATTERNS)
    + list(_SOFT_WIRE_MODEL_PATTERNS)
    + list(_WIRE_MODEL_PATTERNS)
))


class TextParser:
    """从工程文本中提取结构化参数"""

    def __init__(self):
        # 手动维护的基础材质列表（核心词汇，保证覆盖常见材质）
        self._base_materials = [
            # 复合管材
            "钢塑复合管", "铝塑复合管", "衬塑钢管", "涂塑钢管",
            "涂覆碳钢管", "涂覆钢管",  # 涂覆=涂塑，实际借用镀锌钢管定额换主材
            "钢丝网骨架管", "孔网钢带管", "塑铝稳态管", "铝合金衬塑管",
            "衬塑PP-R钢管",  # 铝合金衬塑PP-R复合管（长词优先，避免被"衬塑"截断）
            "PPR复合管",  # PPR复合管（铝合金衬塑PPR等）
            # 金属管材
            "镀锌钢管", "焊接钢管", "无缝钢管", "不锈钢管",
            "薄壁不锈钢管", "铜管", "铝管",
            "铸铁管", "球墨铸铁管", "柔性铸铁管",
            # 塑料管材
            "PPR冷水管", "PPR热水管",  # PPR细分（出现在清单描述中）
            "PPR管", "PE管", "PVC管", "UPVC管", "HDPE管",
            "PP管", "ABS管", "CPVC管",
            "PP-R",  # 带连字符形式（清单常写"PP-R给水管"）
            "PPR",   # 不带"管"字形式（清单常写"材质PPR"、"材质:PPR"）
            "PE-RT", # PE-RT管材（铝合金衬塑PE-RT复合管等）
            "塑料管",  # 泛称
            "复合管",  # 泛称
            # 简称（兜底）
            "钢塑", "铝塑", "衬塑", "涂塑",
            "不锈钢", "镀锌", "碳钢", "钢制", "铸铁", "铜制",
            "薄钢板", "镀锌钢板", "不锈钢板", "钢板",  # 板材材质（通风风管用）
            "玻璃钢",  # 玻璃钢桥架/风管
            # 电缆材质
            "高压铝芯电缆", "铝芯电缆", "铜芯电缆",
            "铜芯", "铝芯", "铜导线", "铝导线",
        ]

        # 手动维护的基础连接方式列表
        self._base_connections = [
            "沟槽连接", "螺纹连接", "焊接连接", "法兰连接",
            "热熔连接", "卡压连接", "承插连接", "粘接", "卡箍连接",
            # 清单常见变体写法（parser从自由文本匹配，需覆盖各种写法）
            "卡压式连接", "环压式连接", "环压连接",
            "对接电弧焊", "承插氩弧焊", "电弧焊",
            "对焊连接", "焊接",
        ]

        # 显式补充正确中文词汇，避免历史乱码词表漏掉常见安装管材/连接方式。
        self._supplemental_materials = [
            "PSP钢塑复合管", "钢塑复合压力给水管", "钢塑复合给水管", "钢塑复合管",
            "钢骨架塑料复合管", "金属骨架复合管", "金属骨架塑料复合管",
            "铝塑复合管", "衬塑钢管", "涂塑钢管",
            "PPR复合管", "PP-R管", "PPR管", "PP-R", "PPR",
            "复合管",
        ]
        self._supplemental_connections = [
            "电磁感应热熔", "双热熔连接", "双热熔", "热熔连接", "热熔",
            "电熔连接", "电熔",
            "卡压连接", "卡压", "环压连接", "环压",
            "沟槽连接", "沟槽", "卡箍连接", "卡箍",
            "螺纹连接", "螺纹", "丝扣连接", "丝扣",
            "法兰连接", "法兰",
            "承插连接", "承插",
            "焊接连接", "焊接",
            "粘接",
        ]

        # 最终使用的列表（基础 + 从定额库自动提取的，按长度降序）
        self._materials = None  # 延迟初始化
        self._connections = None  # 延迟初始化

        # 解析结果缓存（热点文本会被多次解析：规则校验/经验校验/主流程）
        self._parse_cache = OrderedDict()
        self._parse_cache_max = 4096
        self._parse_cache_lock = threading.Lock()  # 线程安全锁（Celery多线程场景）

    def _ensure_vocab_loaded(self):
        """确保词汇列表已加载（合并基础列表 + 定额库提取的词汇）"""
        if self._materials is not None:
            return  # 已加载

        # 先用基础列表
        mat_set = set(self._base_materials)
        conn_set = set(self._base_connections)
        mat_set.update(self._supplemental_materials)
        conn_set.update(self._supplemental_connections)

        # 尝试从定额库自动提取的缓存文件加载额外词汇
        cache_path = Path(__file__).parent.parent / "data" / "dict" / "extracted_vocab.txt"
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    section = None
                    for line in f:
                        line = line.strip()
                        if line == "[materials]":
                            section = "mat"
                        elif line == "[connections]":
                            section = "conn"
                        elif line.startswith("["):
                            section = None
                        elif line and section == "mat":
                            mat_set.add(line)
                        elif line and section == "conn":
                            conn_set.add(line)
                logger.debug(f"加载了定额库提取词汇: 材质{len(mat_set)}个, 连接方式{len(conn_set)}个")
            except Exception as e:
                logger.warning(f"加载提取词汇缓存失败: {e}")

        # 过滤材质词汇：去掉含位置/功能前缀的长词
        # 如"室内给水钢塑复合管"应精简为"钢塑复合管"，位置前缀不是材质的一部分
        location_prefixes = (
            "室内", "室外", "户内", "户外",
            "给水", "排水", "热水", "冷水", "消防", "采暖", "通风",
            "高压", "低压", "中压",
        )
        cleaned_materials = set()
        for m in mat_set:
            clean = m
            # 逐层去掉开头的位置前缀
            changed = True
            while changed:
                changed = False
                for prefix in location_prefixes:
                    if clean.startswith(prefix) and len(clean) > len(prefix) + 2:
                        clean = clean[len(prefix):]
                        changed = True
            # 只保留去掉前缀后的核心材质词
            # 不保留含位置前缀的原词（否则"室内给水钢塑复合管"会比"钢塑复合管"先匹配）
            cleaned_materials.add(clean)

        # 额外过滤：去掉包含设备/功能词的材质（不是真正的材质）
        # 即使 extracted_vocab.txt 有问题，这里也能兜底过滤
        equipment_words = (
            "风管", "管道安装", "管制作", "管安装", "配管", "敷设", "布放",
            "穿导管", "穿放", "顶管", "钻孔",  # 施工方式不是材质
            "网管", "风机盘管",  # 网络管理和设备不是材质
            "设备与", "设备接", "接管",  # 不完整的截断词
            "安装",  # 材质名称不应含"安装"
        )
        cleaned_materials = {m for m in cleaned_materials if not any(w in m for w in equipment_words)}
        material_noise_terms = {
            "成品管",
            "成品管卡",
            "管卡",
            "卡箍件",
            "配套成品管卡",
        }
        cleaned_materials = {m for m in cleaned_materials if m not in material_noise_terms}

        # 按长度降序排列（长词优先匹配，避免"钢塑复合管"被"钢管"截断）
        self._materials = sorted(cleaned_materials, key=lambda value: (-len(value), value))
        self._connections = sorted(conn_set, key=lambda value: (-len(value), value))

    def _get_parse_cache(self, text: str) -> Optional[dict]:
        """读取解析缓存（LRU 命中后刷新活跃度）。"""
        with self._parse_cache_lock:
            cached = self._parse_cache.get(text)
            if cached is None:
                return None
            self._parse_cache.move_to_end(text)
            return dict(cached)

    def _set_parse_cache(self, text: str, result: dict):
        """写入解析缓存并维护 LRU 上限。"""
        with self._parse_cache_lock:
            self._parse_cache[text] = dict(result)
            self._parse_cache.move_to_end(text)
            if len(self._parse_cache) > self._parse_cache_max:
                self._parse_cache.popitem(last=False)

    def parse(self, text: str) -> dict:
        """
        解析文本，提取所有可识别的参数

        参数:
            text: 定额名称或清单描述文字

        返回:
            字典，包含提取到的各项参数
        """
        if not text:
            return {}

        # 预处理：换行符替换为空格，避免换行截断参数字段
        text = text.replace("\n", " ").replace("\r", " ")

        cached = self._get_parse_cache(text)
        if cached is not None:
            return cached

        result = {}

        # 提取电气配管管径（SC20、PC25 等管材代号）
        # 单独存放，不参与参数验证的 DN 比较（电气配管定额不按管径分档）
        conduit_dn = self._extract_conduit_dn(text)
        if conduit_dn is not None:
            result["conduit_dn"] = conduit_dn
            # 兼容旧调用链：部分下游和历史测试仍从 dn 读取配管口径。
            result["dn"] = conduit_dn

        # 提取管径（DN）
        # 若已命中电气配管口径，则跳过通用 DN 规则，避免“配管 规格：20”被误判成给排水 DN。
        dn = None if conduit_dn is not None else self._extract_dn(text)
        if dn is not None:
            result["dn"] = dn
        elif re.search(r'水龙头|龙头', text):
            # 未显式标注时，卫生器具中的水龙头通常按 DN15 套档。
            result["dn"] = 15

        # 提取接地扁钢宽度（在电缆截面之前，避免扁钢规格40×4被误识别为截面4）
        ground_bar_width = self._extract_ground_bar_width(text)
        if ground_bar_width is not None:
            result["ground_bar_width"] = ground_bar_width

        # 提取电缆截面（mm²）—— 接地扁钢已提取时跳过
        if "ground_bar_width" not in result:
            cable_bundle = self._extract_cable_bundle_specs(text)
            if cable_bundle:
                result["cable_bundle"] = cable_bundle
            section = self._extract_cable_section(text)
            if section is not None:
                result["cable_section"] = section
            elif cable_bundle:
                result["cable_section"] = max(spec["section"] for spec in cable_bundle)
            cable_cores = self._extract_cable_cores(text, cable_bundle=cable_bundle)
            if cable_cores is not None:
                result["cable_cores"] = cable_cores

        # 提取容量（kVA）
        kva = self._extract_kva(text)
        if kva is not None:
            result["kva"] = kva

        # 提取功率（kW）— 电动机等设备按功率分档
        kw = self._extract_kw(text)
        if kw is not None:
            result["kw"] = kw

        # 提取电压等级（kV）
        kv = self._extract_kv(text)
        if kv is not None:
            result["kv"] = kv

        voltage_level = self._extract_voltage_level(text, kv=kv)
        if voltage_level:
            result["voltage_level"] = voltage_level

        # 提取电流（A）
        ampere = self._extract_ampere(text)
        if ampere is not None:
            result["ampere"] = ampere

        # 提取重量（t或kg）
        weight = self._extract_weight(text)
        if weight is not None:
            result["weight_t"] = weight  # 统一转为吨

        # 提取材质
        material = self._extract_material(text)
        inferred_material = self._infer_cable_material_from_model(text)
        if inferred_material == "矿物绝缘电缆":
            material = inferred_material
        elif not material and inferred_material:
            material = inferred_material
        if material:
            result["material"] = material

        cable_type = self._extract_cable_type(text, material=material)
        if cable_type:
            result["cable_type"] = cable_type

        cable_head_type = self._extract_cable_head_type(text)
        if cable_head_type:
            result["cable_head_type"] = cable_head_type

        conduit_type = self._extract_conduit_type(text)
        if conduit_type:
            result["conduit_type"] = conduit_type

        wire_type = self._extract_wire_type(text)
        if wire_type:
            result["wire_type"] = wire_type

        # 提取连接方式
        connection = self._extract_connection(text)
        if connection:
            result["connection"] = connection

        valve_connection_family = self._extract_valve_connection_family(
            text,
            connection=connection or "",
        )
        if valve_connection_family:
            result["valve_connection_family"] = valve_connection_family

        valve_type = self._extract_valve_type(text)
        if valve_type:
            result["valve_type"] = valve_type

        support_material = self._extract_support_material(text)
        if support_material:
            result["support_material"] = support_material

        support_scope = self._extract_support_scope(text)
        if support_scope:
            result["support_scope"] = support_scope

        support_action = self._extract_support_action(text)
        if support_action:
            result["support_action"] = support_action

        surface_process = self._extract_surface_process(text)
        if surface_process:
            result["surface_process"] = surface_process

        sanitary_subtype = self._extract_sanitary_subtype(text)
        if sanitary_subtype:
            result["sanitary_subtype"] = sanitary_subtype

        sanitary_mount_mode = self._extract_sanitary_mount_mode(text)
        if sanitary_mount_mode:
            result["sanitary_mount_mode"] = sanitary_mount_mode

        sanitary_flush_mode = self._extract_sanitary_flush_mode(text)
        if sanitary_flush_mode:
            result["sanitary_flush_mode"] = sanitary_flush_mode

        sanitary_water_mode = self._extract_sanitary_water_mode(text)
        if sanitary_water_mode:
            result["sanitary_water_mode"] = sanitary_water_mode

        sanitary_nozzle_mode = self._extract_sanitary_nozzle_mode(text)
        if sanitary_nozzle_mode:
            result["sanitary_nozzle_mode"] = sanitary_nozzle_mode

        sanitary_tank_mode = self._extract_sanitary_tank_mode(text)
        if sanitary_tank_mode:
            result["sanitary_tank_mode"] = sanitary_tank_mode

        lamp_type = self._extract_lamp_type(text)
        if lamp_type:
            result["lamp_type"] = lamp_type

        # 提取回路数（配电箱按回路分档：4/8/16/24/32/48）
        circuits = self._extract_circuits(text)
        if circuits is not None:
            result["circuits"] = circuits

        port_count = self._extract_port_count(text)
        if port_count is not None:
            result["port_count"] = port_count

        item_count = self._extract_item_count(text)
        if item_count is not None:
            result["item_count"] = item_count

        item_length = self._extract_item_length(text)
        if item_length is not None:
            result["item_length"] = item_length

        # 提取风管形状（矩形/圆形，通风空调管道的核心分类维度）
        shape = self._extract_shape(text)
        if shape:
            result["shape"] = shape

        # 提取周长（通风空调风口/阀门/散流器/消声器按周长取档）
        perimeter = self._extract_perimeter(text)
        if perimeter is not None:
            result["perimeter"] = perimeter
            # 如果从"规格：W*H"计算出了周长，说明cable_section是误提取（同一个W*H源）
            if "cable_section" in result:
                del result["cable_section"]

        # 提取半周长（配电箱悬挂/嵌入式按半周长取档，全国通用）
        half_perimeter = self._extract_half_perimeter(text)
        if half_perimeter is not None:
            result["half_perimeter"] = half_perimeter

        # 提取大边长（弯头导流叶片、矩形风管等按大边长取档）
        large_side = self._extract_large_side(text)
        if large_side is not None:
            result["large_side"] = large_side

        # 提取电梯停靠站数（从"停靠层数:-2~24层"计算）
        elevator_stops = self._extract_elevator_stops(text)
        if elevator_stops is not None:
            result["elevator_stops"] = elevator_stops

        # 提取开关联数（单联=1、双联=2、三联=3、四联=4）
        switch_gangs = self._extract_switch_gangs(text)
        if switch_gangs is not None:
            result["switch_gangs"] = switch_gangs

        outlet_grounding = self._extract_outlet_grounding(text)
        if outlet_grounding:
            result["outlet_grounding"] = outlet_grounding

        # 提取安装方式（明装/暗装/落地/挂墙/嵌入/吊装/悬挂/明敷/暗敷）
        install_method = self._extract_install_method(text)
        if install_method:
            result["install_method"] = install_method

        box_mount_mode = self._extract_box_mount_mode(text, install_method=install_method)
        if box_mount_mode:
            result["box_mount_mode"] = box_mount_mode

        laying_method = self._extract_laying_method(text)
        if laying_method:
            result["laying_method"] = laying_method

        bridge_type = self._extract_bridge_type(text)
        if bridge_type:
            result["bridge_type"] = bridge_type

        bridge_wh_sum = self._extract_bridge_wh_sum(text)
        if bridge_wh_sum is not None:
            result["bridge_wh_sum"] = bridge_wh_sum
            if "桥架" in text and "cable_section" in result:
                del result["cable_section"]
            if "桥架" in text and "cable_cores" in result:
                del result["cable_cores"]

        # 提取电梯类型（从名称关键词判断：货梯→载货电梯、客梯→曳引式电梯等）
        elevator_type = self._extract_elevator_type(text)
        if elevator_type:
            result["elevator_type"] = elevator_type

        # 提取电梯运行速度（从"速度:2.5m/s"提取数值，用于区分2m/s以上/以下系列）
        elevator_speed = self._extract_elevator_speed(text)
        if elevator_speed is not None:
            result["elevator_speed"] = elevator_speed

        self._set_parse_cache(text, result)
        return dict(result)

    # De外径→DN公称直径转换表（已弃用，保留避免外部引用报错）
    # 塑料管定额按外径(De)值分档（如"公称外径32mm以内"），不需要转换为DN
    # 之前误用此表把De32转成DN25，导致搜索和参数验证都对不上定额的外径分档值
    def parse_canonical(self, text: str, specialty: str = "",
                        context_prior: dict | None = None,
                        params: dict | None = None) -> dict:
        """将原始描述转换为标准化特征字典。"""
        parsed_params = dict(params) if params is not None else self.parse(text)
        return build_canonical_features(
            raw_text=text,
            params=parsed_params,
            specialty=specialty,
            context_prior=context_prior,
        ).to_dict()

    DE_TO_DN = {
        20: 15, 25: 20, 32: 25, 40: 32, 50: 40,
        63: 50, 75: 65, 90: 80, 110: 100, 125: 100,
        140: 125, 160: 150, 200: 200, 225: 200,
        250: 250, 315: 300, 355: 350, 400: 400,
    }

    def _prefer_nominal_dn_for_de(self, text: str) -> bool:
        """Return True when a De-sized description should be normalized to DN."""
        if not text:
            return False
        for keyword in (
            "\u9600\u95e8", "\u95f8\u9600", "\u622a\u6b62\u9600", "\u6b62\u56de\u9600", "\u8776\u9600", "\u7403\u9600",
            "\u6cd5\u5170", "\u5957\u7ba1", "\u963b\u706b\u5708", "\u8fc7\u6ee4\u5668", "\u8865\u507f\u5668",
            "\u5012\u6d41\u9632\u6b62\u5668", "\u6c34\u8868",
        ):
            if keyword in text:
                return True
        return False

    def _extract_dn(self, text: str) -> Optional[int]:
        """
        提取管径DN值，统一返回整数（毫米）

        支持格式:
        - DN150, DN-150, dn150 → 直接取值
        - De110, de110 → 通过De→DN转换表转换（塑料管外径→公称直径）
        - Φ150, φ150 → 直接取值（Φ是直径符号，工程中常等同于DN）
        - 公称直径150, 公称直径(mm以内) 150, 公称直径(DN) ≤20 → 直接取值
        - 外径(mm) 20 → 直接取值（JDG导管等用外径标注的定额）
        - 管径150 → 直接取值
        - 规格：65 → 当值为合理DN范围(10-600)且非尺寸格式时，视为DN
        """
        # 先匹配DN格式（精确）
        # [≤≥<>]? 处理定额名称中的"≤20"格式（如"公称直径(DN) ≤20"）
        # (?:\([^)]*\))? 匹配任何括号内容（DN/mm/mm以内等）
        dn_patterns = [
            r'[Dd][Nn]\s*[-_]?\s*(\d+)',                                        # DN150, DN-150
            r'[ΦφΦ]\s*(\d+)',                                                    # Φ150, φ150（直径符号）
            r'公称(?:口径|直径)\s*(?:[（(][^)）]*[)）])?\s*[≤≥<>]?\s*(\d+)',      # 公称直径（DN）≤20 / 公称口径 15mm以内
            r'(?:外径|内径)\s*(?:[（(][^)）]*[)）])?\s*[≤≥<>]?\s*(\d+)',          # 外径(mm) 20 / 内径≤16mm（波纹管）
            r'(?:管径|直径)\s*(?:[（(][^)）]*[)）])?\s*[≤≥<>]?\s*(\d+)',          # 管径(mm以内) 50、直径(mm) ≤40
        ]
        for pattern in dn_patterns:
            match = re.search(pattern, text)
            if match:
                return int(match.group(1))

        # 匹配De格式（塑料管外径标记），直接返回De原值
        # 塑料管定额按外径(De)分档（如"公称外径32mm以内"），De值=定额分档值
        de_match = re.search(r'[Dd][Ee]\s*(\d+)', text)
        if de_match:
            de_value = int(de_match.group(1))
            if self._prefer_nominal_dn_for_de(text):
                return self.DE_TO_DN.get(de_value, de_value)
            return de_value

        # 匹配"规格：65"格式（清单描述中常见，管道项的规格通常就是DN值）
        # 安全条件：
        # 1. 值为合理DN范围(10-600)
        # 2. 不是尺寸格式(NxN或N*N)
        # 3. 文本中包含管道相关关键词（材质或连接方式），排除电气/弱电项误提取
        # 用具体管材关键词判断，不用"管"——"管"太宽泛，会匹配"管道"（如"碳钢通风管道"是风管不是水管）
        pipe_keywords = ["钢管", "铸铁管", "铜管", "不锈钢管", "塑料管",
                         "PE管", "PPR管", "PVC管", "HDPE管", "JDG", "KBG", "SC", "RC",
                         "配管", "线管", "电线管", "明敷", "暗敷",
                         "涂塑", "涂覆", "衬塑", "碳钢管", "镀锌",
                         "阀", "沟槽", "螺纹", "法兰", "卡压",
                         "热熔", "粘接", "承插", "焊接连接",
                         "管径", "公称直径", "消火栓", "喷淋", "给水", "排水"]
        has_pipe_context = any(kw in text for kw in pipe_keywords)
        if has_pipe_context:
            spec_match = re.search(r'规格[：:]\s*(\d+)(?!\d)(?!\s*[*×xX])', text)
            if spec_match:
                val = int(spec_match.group(1))
                if 10 <= val <= 600:
                    return val

            suffixed_pipe_match = re.search(
                r'(?:钢管|碳钢管|镀锌钢管|涂塑钢管|不锈钢管|铸铁管|球墨铸铁管|'
                r'PE管|PPR管|PP-R管|PVC管|UPVC管|HDPE管|铜管|铝塑复合管)'
                r'\s*[-_：:]?\s*(\d+)(?!\d)',
                text,
            )
            if suffixed_pipe_match:
                val = int(suffixed_pipe_match.group(1))
                if 10 <= val <= 600:
                    return val

        slotting_match = re.search(
            r'(?:凿(?:\(压、切割\))?槽|剔槽|凿槽).*?规格[：:]\s*(\d+)(?:以内|以下)?',
            text,
        )
        if slotting_match:
            val = int(slotting_match.group(1))
            if 10 <= val <= 100:
                return val

        # 人防密闭阀门专用规格：SMF20=直径200mm，D400=直径400mm
        # SMF后面的数字是厘米(×10→毫米)，D后面的数字直接是毫米
        _civil_defense_kw = ("密闭阀", "插板阀", "人防")
        if any(kw in text for kw in _civil_defense_kw):
            smf_match = re.search(r'SMF\s*(\d+)', text, re.IGNORECASE)
            if smf_match:
                return int(smf_match.group(1)) * 10
            d_match = re.search(r'\bD(\d{3,4})\b', text)
            if d_match:
                return int(d_match.group(1))
            wh_match = re.search(r'(\d{2,4})\s*[*×xX]\s*(\d{2,4})', text)
            if wh_match:
                return max(int(wh_match.group(1)), int(wh_match.group(2)))

        sleeve_keywords = ("套管", "填料套管", "防水套管", "刚性防水套管", "柔性防水套管")
        if any(keyword in text for keyword in sleeve_keywords):
            sleeve_match = re.search(r'规格(?:型号)?[：:]\s*(\d{2,4})\s*[*×xX]\s*(\d{2,4})', text)
            if sleeve_match:
                first = int(sleeve_match.group(1))
                second = int(sleeve_match.group(2))
                large = max(first, second)
                small = min(first, second)
                if large >= 1000 and 20 <= small <= 1000:
                    return small

        refrigerant_keywords = ("冷媒分配器", "冷媒分歧管", "分歧器", "分配器")
        if any(keyword in text for keyword in refrigerant_keywords):
            range_match = re.search(
                r'(?:\d+(?:\.\d+)?)\s*[≤<]\s*[Φφ]\s*[≤<]\s*(\d+(?:\.\d+)?)',
                text,
            )
            if range_match:
                upper = float(range_match.group(1))
                if 5 <= upper <= 100:
                    return int(math.ceil(upper))

        return None

    def _extract_conduit_dn(self, text: str) -> Optional[int]:
        """
        提取电气配管的管材代号管径（SC20、PC25、JDG20 等）

        这类管径不同于给排水的 DN：
        - 给排水 DN 用于定额分档（DN100 和 DN150 是不同定额），存为 "dn"
        - 电气配管管径通常不影响定额选择（SC20 和 SC25 同一个定额），存为 "conduit_dn"

        分开存放是为了让参数验证器不误罚：定额没有管材管径不代表匹配错误
        """
        # 如果已经有标准 DN（如 DN20、Φ20），不需要再从管材代号提取
        # 注：这是 parse() 中 "dn" not in result 之外的第二层防护（防御性编程）
        standard_dn = re.search(
            r'[Dd][Nn]\s*[-_]?\s*\d+|[ΦφΦ]\s*\d+|(?:公称直径|管径|直径)\s*\d+',
            text)
        if standard_dn:
            return None

        # PVC放在PC前面，避免PC误匹配PVC的前两个字母
        pipe_code_match = re.search(
            r'(?:SC|PVC|PC|JDG|KBG|RC|MT|FPC)\s*(\d+)', text)
        if pipe_code_match:
            return int(pipe_code_match.group(1))

        conduit_type = self._extract_conduit_type(text)
        conduit_context = any(
            keyword in text
            for keyword in ("配管", "导管", "电线管", "穿线管", "配置形式", "配线形式", "明配", "暗配", "明敷", "暗敷")
        )
        if conduit_type and conduit_context:
            spec_match = re.search(r'规格(?:型号)?[：:]\s*(\d+)(?!\d)(?!\s*[*×xX])', text)
            if spec_match:
                val = int(spec_match.group(1))
                if 10 <= val <= 150:
                    return val
        return None

    def _extract_cable_bundle_specs(self, text: str) -> list[dict]:
        """提取复合电缆规格，如 3x4+2x2.5。"""
        if not re.search(r'(?:BV|BYJ|BYJR|BYP|BVR|BLV|RVS|RVV|YJV|YJY|电缆|电线|导线|配线|穿线)',
                         text, flags=re.IGNORECASE):
            return []
        normalized = text.replace("×", "x").replace("*", "x").replace("X", "x")
        matches = re.findall(r'(\d+)\s*[脳x]\s*(\d+(?:\.\d+)?)', normalized)
        if not matches:
            return []

        specs = []
        for index, (cores, section) in enumerate(matches):
            core_count = int(cores)
            section_value = float(section)
            if core_count > 61 or section_value > 500:
                return []
            specs.append({
                "cores": core_count,
                "section": section_value,
                "role": "main" if index == 0 else "aux",
            })
        return specs

    def _has_cable_section_context(self, text: str) -> bool:
        if not text:
            return False

        if any(
            keyword in text
            for keyword in (
                "电缆", "电线", "导线", "配线", "穿线",
                "电力电缆", "控制电缆", "电缆头", "终端头",
                "接线端子", "压铜接线端子", "铜鼻子", "线鼻子",
                "截面", "平方",
            )
        ):
            return True

        compact_text = re.sub(r"\s+", "", text).upper()
        if re.search(
            rf"{_CABLE_PREFIX_PATTERN}(?:{'|'.join(re.escape(model) for model in _ALL_WIRE_TYPE_PATTERNS)})(?=[\-\d,./()*×Xx]|$)",
            compact_text,
        ):
            return True

        return bool(re.search(r'\d+(?:\.\d+)?\s*mm[²2]', text, re.IGNORECASE))

    def _extract_cable_section(self, text: str) -> Optional[float]:
        """
        提取电缆截面积（mm²），返回主线芯截面

        支持格式:
        - YJV-4*185+1*95 → 185
        - 3×70 → 70
        - 截面(mm²以内) 185 → 185
        - 4X16 → 16

        排除：
        - 600x800x300 这样的外形尺寸（三维尺寸不是截面）
        - 600x600 这样的二维尺寸（面板尺寸不是截面）
        - 15W、28W 这样的功率（瓦数不是截面）
        """
        if not self._has_cable_section_context(text):
            return None

        # 先排除功率格式（NW、NkW），避免把灯具功率误识别为截面
        # 如 "15W"、"28W"、"1.5kW"
        text_clean = re.sub(r'\d+(?:\.\d+)?\s*[kK]?[wW]\b', ' ', text)

        # 排除外形尺寸格式（NxNxN，三个维度）
        # 如 "600x800x300"、"1000×600×300"
        dim3_pattern = r'\d+\s*[*×xX]\s*\d+\s*[*×xX]\s*\d+'
        text_clean = re.sub(dim3_pattern, ' ', text_clean)

        # 排除"规格：W*H"格式的设备尺寸（风口/阀门/散流器等的物理尺寸）
        # 如 "规格：800*320"、"规格：400×120"
        spec_dim_pattern = r'规格[：:]\s*\d+\s*[*×xX]\s*\d+(?:\s*[*×xX]\s*\d+)?'
        text_clean = re.sub(spec_dim_pattern, ' ', text_clean)

        # 排除槽盒/箱体/凿槽类的物理尺寸（不是电缆截面）
        # "防火槽盒100*100"的100*100是槽盒尺寸，"凿槽30×50"的30×50是宽×深
        _ENCLOSURE_KW = ("槽盒", "防火槽", "接线箱", "端子箱", "凿槽", "压槽", "凿(压)槽")
        if any(kw in text for kw in _ENCLOSURE_KW):
            text_clean = re.sub(r'\d{2,4}\s*[*×xX]\s*\d{2,4}', ' ', text_clean)

        # 排除二维面板尺寸（NxN后面跟mm或没有电缆型号前缀）
        # 如 "600x600mm"、"300×300"（面板尺寸）
        # 但保留 "4×185"（电缆截面，前面的数字较小，通常≤5）
        dim2_pattern = r'(?<![A-Za-z\-])\b(\d+)\s*[*×xX]\s*(\d+)\s*(?:mm)?(?!\s*[+＋])'
        def _is_panel_size(m):
            """判断 NxN 是面板尺寸还是电缆截面"""
            n1, n2 = int(m.group(1)), int(m.group(2))
            # 标准电缆截面规格（mm²），如果第二个数字是这些值，大概率是电缆
            standard_cable_sections = {1, 2, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300, 400, 500}
            # 1.5和2.5也是标准截面，但int()后变成了1和2，需要额外处理
            # 这里用原始匹配文本检查小数
            raw_n2 = m.group(2)
            if '.' in raw_n2 or raw_n2 in ('1', '2'):
                # 带小数点的（如1.5, 2.5）或很小的数字，大概率是电缆截面
                return False
            # 电缆格式特征：前面是芯数（通常1-61），后面是标准截面
            if n2 in standard_cable_sections:
                return False  # 是电缆截面（如 10×1.5, 4×185），保留
            # 两个数字都较大（>10），且不是标准截面，是面板尺寸
            if n1 > 10 and n2 > 10:
                return True
            return False  # 不确定时保留，避免误删
        text_clean = re.sub(dim2_pattern, lambda m: ' ' if _is_panel_size(m) else m.group(0), text_clean)

        # 格式: 数字*截面 或 数字×截面（取最大的截面值作为主截面）
        pattern = r'(\d+)\s*[*×xX]\s*(\d+(?:\.\d+)?)'
        matches = re.findall(pattern, text_clean)
        if matches:
            # 取所有截面值中最大的作为主截面
            sections = [float(m[1]) for m in matches]
            return max(sections)

        # 格式: (截面mm2以下) 16 / (截面mm²以内) 185
        # 描述字段中括号包裹"截面"的格式：括号在"截面"前面
        # 如 "规格:(截面mm2以下) 16"
        paren_match = re.search(r'[（(]截面[^)）]*[)）]\s*(\d+(?:\.\d+)?)', text)
        if paren_match:
            return float(paren_match.group(1))

        # 格式: 截面(mm²以内) 数值 / 截面(mm2) ≤2.5（定额名称格式含≤前缀）
        # 支持全角括号（mm2）和半角括号(mm2)，支持"截面积"
        match = re.search(r'截面(?:积)?\s*(?:[（(][^)）]*[)）])?\s*[≤≥<>]?\s*(\d+(?:\.\d+)?)', text)
        if match:
            return float(match.group(1))

        # 规格/型号直接写成 16mm2 / 2.5mm² / BYJ2.5mm2 的场景。
        mm2_match = re.search(r'(\d+(?:\.\d+)?)\s*mm[²2]', text, re.IGNORECASE)
        if mm2_match:
            return float(mm2_match.group(1))

        # 导线型号格式：BV-2.5、BYJ-4、WDZN-BYJ-4、NH-BV-2.5 等
        # 支持前缀：WDZ/WDZN/WDZZ（低烟无卤/阻燃）、NH（耐火）
        # 支持型号：BV/BYJ/BVR/BLV/RVS/RVV
        wire_match = re.search(
            rf'{_CABLE_PREFIX_PATTERN}'       # 可选的阻燃/耐火前缀
            r'(?:BV|BYJ|BYJR|BYP|BVR|BLV|RVS|RVV)'   # 导线型号
            r'\s*-?\s*'                       # 可选分隔符
            r'(\d+(?:\.\d+)?)',              # 截面数值
            text
        )
        if wire_match:
            return float(wire_match.group(1))

        # 兜底：从"规格：N"格式提取截面（导线/电缆类清单常见格式）
        # 场景：清单特征描述中写"规格：4"或"规格：2.5"，不带mm²也不带×号
        # 守卫条件：文本必须包含导线/电缆相关关键词，避免和DN提取冲突
        _cable_keywords = ["导线", "电缆", "穿线", "配线", "BV", "BYJ", "BVR",
                           "YJV", "RVS", "RVV", "管内穿"]
        _standard_sections = {1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95,
                              120, 150, 185, 240, 300, 400, 500}
        if any(kw in text for kw in _cable_keywords):
            spec_match = re.search(r'规格[：:]\s*-?\s*(\d+(?:\.\d+)?)\s*(?:\s|$|[,，/])', text)
            if spec_match:
                val = float(spec_match.group(1))
                if val in _standard_sections:
                    return val

        bundle_specs = self._extract_cable_bundle_specs(text)
        if bundle_specs:
            return max(spec["section"] for spec in bundle_specs)

        return None

    def _extract_cable_cores(self, text: str,
                             cable_bundle: list[dict] | None = None) -> Optional[int]:
        """提取电缆总芯数，复杂规格按主芯和辅助芯求和。"""
        bundle = list(cable_bundle or [])
        if bundle:
            total_cores = sum(
                int(spec.get("cores", 0))
                for spec in bundle
                if spec.get("cores") is not None
            )
            if total_cores > 0:
                return total_cores

        if not text or "芯" not in text:
            return None

        match = re.search(r'(\d+)\s*芯(?:以下|以内|内|及以下)?', text)
        if match:
            return int(match.group(1))

        match = re.search(r'([单双一二三四五六七八九十两])\s*芯', text)
        if match:
            return self._CN_GANG_NUM.get(match.group(1))

        return None

    def _search_first_float(self, text: str, patterns: list[str]) -> Optional[float]:
        """Return first float captured by regex patterns, or None."""
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return float(match.group(1))
        return None

    def _search_first_int(self, text: str, patterns: list[str]) -> Optional[int]:
        """Return first int captured by regex patterns, or None."""
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return int(match.group(1))
        return None

    def _search_first_float_group(self, text: str,
                                  pattern_groups: list[tuple[str, int]]) -> Optional[float]:
        """Return first float captured by (pattern, group_idx) specs."""
        for pattern, group_idx in pattern_groups:
            match = re.search(pattern, text)
            if match:
                return float(match.group(group_idx))
        return None

    def _extract_spec_wh(self, text: str) -> Optional[tuple[float, float]]:
        """Extract W/H from '规格:W*H(*L)' and filter out cable-style small prefixes."""
        # "规格型号:" 和 "规格:" 都要匹配（清单描述两种写法都常见）
        spec_match = re.search(
            r'规格(?:型号)?[：:]\s*(\d+)\s*[*×xX]\s*(\d+)(?:\s*[*×xX]\s*\d+)?',
            text)
        if not spec_match:
            return None

        w = float(spec_match.group(1))
        h = float(spec_match.group(2))
        if w <= 10 or h <= 10:
            return None
        return w, h

    def _extract_named_mm_or_spec(self, text: str, name: str,
                                  use_perimeter: bool) -> Optional[float]:
        """Extract mm-tier value from named field first, fallback to spec W*H."""
        named_value = self._search_first_float(
            text, [rf'{name}\s*[（(]\s*mm以内\s*[)）]\s*(\d+)'])
        if named_value is not None:
            return named_value

        spec_wh = self._extract_spec_wh(text)
        if not spec_wh:
            return None
        w, h = spec_wh
        return (w + h) * 2 if use_perimeter else max(w, h)

    def _extract_kva(self, text: str) -> Optional[float]:
        """
        提取变压器容量（kVA）

        支持格式: 800kva, 800kV·A, 容量(kV·A) 800
        """
        patterns = [
            r'(\d+(?:\.\d+)?)\s*[kK][vV][·.]?[aA]',              # 800kva, 800kV·A
            r'容量\s*(?:\([kK][vV][·.]?[aA](?:以内)?\))?\s*(\d+(?:\.\d+)?)',  # 容量(kV·A) 800
        ]
        return self._search_first_float(text, patterns)

    def _extract_kw(self, text: str) -> Optional[float]:
        """
        提取功率（kW）— 电动机、水泵等设备按功率分档

        支持格式: 18.5kW, 18.5KW, 功率(kW) 18.5, 容量(kW):18.5KW以下
        定额格式: (功率kW以下) 220, 功率(kw) ≤30
        注意区分kW和kVA（变压器用kVA，电机用kW）
        """
        patterns = [
            # 格式: 数字kW/KW（排除kVA/kv·a）
            r'(\d+(?:\.\d+)?)\s*[kK][wW](?![·.]?[aA])',
            # 格式: 功率(kW以内/以下) 数字
            r'功率\s*(?:\([kK][wW](?:以内|以下)?\))?\s*[:\s]*(\d+(?:\.\d+)?)',
            # 格式: 容量(kW):数字
            r'容量\s*\([kK][wW]\)\s*[:\s]*(\d+(?:\.\d+)?)',
            # 定额格式: (功率kW以下) 数字 或 功率(kw) ≤数字
            r'功率[kK][wW](?:以下|以内)\)\s*(\d+(?:\.\d+)?)',
            r'功率\s*\([kK][wW]\)\s*[≤<=]*\s*(\d+(?:\.\d+)?)',
        ]
        return self._search_first_float(text, patterns)

    def _extract_kv(self, text: str) -> Optional[float]:
        """
        提取电压等级（kV）

        支持格式: 10kV, 10KV, 0.6/1kV, 8.5/15kv
        """
        return self._search_first_float_group(text, [
            # 格式: 数字/数字kV（取后面的值作为电压等级）
            (r'(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*[kK][vV]', 2),
            # 格式: 数字kV
            (r'(\d+(?:\.\d+)?)\s*[kK][vV](?![·.aA])', 1),
        ])

    def _extract_voltage_level(self, text: str, kv: Optional[float] = None) -> str:
        """提取高压/中压/低压等级，优先显式词，其次根据kV推断。"""
        if not text:
            return ""
        if "高压" in text:
            return "高压"
        if "低压" in text:
            return "低压"
        if "中压" in text:
            return "中压"

        voltage = kv if kv is not None else self._extract_kv(text)
        if voltage is None:
            if re.search(r'(?:220|380|400|660)\s*[vV]\b', text):
                return "低压"
            return ""
        if voltage >= 10:
            return "高压"
        if voltage >= 3:
            return "中压"
        return "低压"

    def _extract_ampere(self, text: str) -> Optional[float]:
        """
        提取电流值（A）

        支持格式: 100A, 电流(A以内) 100
        """
        patterns = [
            r'(\d+(?:\.\d+)?)\s*[aA](?![a-zA-Z])',               # 100A
            r'电流\s*(?:\([aA](?:以内)?\))?\s*(\d+(?:\.\d+)?)',    # 电流(A以内) 100
        ]
        return self._search_first_float(text, patterns)

    def _extract_weight(self, text: str) -> Optional[float]:
        """
        提取重量，统一转为吨(t)

        支持格式: 30t, 30吨, 设备重量(t以内) 30, 500kg
        """
        # 吨
        patterns_t = [
            r'(\d+(?:\.\d+)?)\s*[tT](?![a-zA-Z])',               # 30t
            r'(\d+(?:\.\d+)?)\s*吨',                               # 30吨
            r'(?:重量|质量)\s*(?:\([tT](?:以内)?\))?\s*(\d+(?:\.\d+)?)',  # 重量(t以内) 30
        ]
        weight_t = self._search_first_float(text, patterns_t)
        if weight_t is not None:
            return weight_t

        named_kg = re.search(
            r'(?:设备)?(?:重量|质量)\s*(?:\([kK][gG](?:以内)?\))?\s*(\d+(?:\.\d+)?)',
            text,
        )
        if named_kg and "kg" in text.lower():
            return float(named_kg.group(1)) / 1000

        # 千克 → 转为吨
        match = re.search(r'(\d+(?:\.\d+)?)\s*[kK][gG]', text)
        if match:
            return float(match.group(1)) / 1000

        return None

    def _extract_material(self, text: str) -> Optional[str]:
        """
        提取材质

        匹配策略（优先级从高到低）：
        1. 先在"材质"字段中查找（如 "3.材质、规格:PPR复合管"）
        2. 再在文本最前面（项目名称部分）查找
        3. 最后在全文中查找

        同一优先级下，长词优先（"钢塑复合管"优先于"钢管"）
        """
        explicit = self._extract_explicit_material(text)
        if explicit:
            return explicit

        self._ensure_vocab_loaded()

        # 先把换行符替换为空格，避免换行截断材质字段
        clean_text = text.replace("\n", " ").replace("\r", " ")

        # 清理材质名中常见的干扰词，让"内外涂覆EP碳钢管"能匹配到"涂覆碳钢管"
        # EP=环氧(epoxy)品牌前缀，"内外"=涂覆方式，不影响材质类型
        clean_text = re.sub(r'(?:内外)?涂覆\s*EP', '涂覆', clean_text)

        # 策略1：尝试从"材质"字段中提取（清单描述常有"3.材质、规格:xxx"格式）
        # 匹配"材质"关键字后到下一个编号字段（如"4.xxx"）之间的内容
        mat_field = re.search(r'材质[、和]?(?:规格)?[：:]\s*(.{2,120}?)(?:\d+\.\s*\S|$)', clean_text)
        if mat_field:
            field_text = mat_field.group(1)
            # 在材质字段中按长度优先匹配
            for mat in self._materials:
                if mat in field_text:
                    return mat

        # 策略2：找到所有匹配项，优先返回位置最靠前 + 最长的匹配
        # （名称在query最前面，所以位置靠前 = 更可能来自名称）
        matches = []  # [(位置, 长度, 材质)]
        for mat in self._materials:
            pos = clean_text.find(mat)
            if pos >= 0:
                matches.append((pos, len(mat), mat))

        if matches:
            # 排序：先按位置升序（越前越好），再按长度降序（越长越精确）
            matches.sort(key=lambda x: (x[0], -x[1]))
            return matches[0][2]

        return None

    @staticmethod
    def _extract_explicit_material(text: str) -> Optional[str]:
        if not text:
            return None

        explicit_rules = (
            (r"PSP钢塑复合管", "钢塑复合管"),
            (r"钢塑复合(?:压力)?(?:给水)?管", "钢塑复合管"),
            (r"钢骨架塑料复合管", "钢骨架塑料复合管"),
            (r"金属骨架(?:塑料)?复合管", "金属骨架复合管"),
            (r"铝塑复合(?:给水)?管", "铝塑复合管"),
            (r"衬塑钢管", "衬塑钢管"),
            (r"涂塑钢管", "涂塑钢管"),
            (r"PPR复合管", "PPR复合管"),
            (r"PP-R管", "PPR管"),
            (r"\bPPR\b", "PPR"),
        )
        for pattern, normalized in explicit_rules:
            if re.search(pattern, text, re.IGNORECASE):
                return normalized

        if "复合管" in text:
            return "复合管"
        return None

    def _infer_cable_material_from_model(self, text: str) -> Optional[str]:
        """从电缆/导线型号推断芯材，并单独识别矿物绝缘电缆。"""
        if not text:
            return None

        if "矿物绝缘" in text or "矿物电缆" in text:
            return "矿物绝缘电缆"

        compact_text = re.sub(r"\s+", "", text).upper()
        if not compact_text:
            return None

        for model in _MINERAL_CABLE_MODEL_PATTERNS:
            if re.search(
                rf"{_CABLE_PREFIX_PATTERN}{re.escape(model)}(?=[\-\d,./()*×Xx]|$)",
                compact_text,
            ):
                return "矿物绝缘电缆"

        for model in _ALUMINUM_CORE_MODEL_PATTERNS:
            if re.search(
                rf"{_CABLE_PREFIX_PATTERN}{re.escape(model)}(?=[\-\d,./()*×Xx]|$)",
                compact_text,
            ):
                return "铝芯"

        for model in _COPPER_CORE_MODEL_PATTERNS:
            if re.search(
                rf"{_CABLE_PREFIX_PATTERN}{re.escape(model)}(?=[\-\d,./()*×Xx]|$)",
                compact_text,
            ):
                return "铜芯"

        return None

    def _extract_wire_type(self, text: str) -> str:
        """提取线缆基础型号，如 BPYJV/KVV/BV/JDG。"""
        if not text:
            return ""

        compact_text = re.sub(r"\s+", "", text).upper()
        if not compact_text:
            return ""

        for model in sorted(_ALL_WIRE_TYPE_PATTERNS, key=len, reverse=True):
            if re.search(
                rf"{_CABLE_PREFIX_PATTERN}{re.escape(model)}(?=[\-\d,./()*×Xx]|$)",
                compact_text,
            ):
                return model
        return ""

    def _extract_cable_type(self, text: str, material: str = "") -> str:
        """提取线缆家族锚点，区分电力/控制/电线/软导线/矿物绝缘。"""
        if not text:
            return ""

        # 电线管/钢导管类定额名称属于配管对象，不能被“电线”字样误判成线缆。
        if any(
            keyword in text
            for keyword in (
                "波纹电线管", "镀锌电线管", "金属电线管", "PVC电线管",
                "电线管", "钢导管", "紧定式钢导管", "扣压式钢导管",
            )
        ):
            return ""

        if "光缆" in text:
            return "光缆"
        if any(keyword in text for keyword in ("双绞线", "网线", "网缆")):
            return "双绞线"
        if "矿物绝缘" in text or "矿物电缆" in text:
            return "矿物绝缘电缆"
        if "控制电缆" in text:
            return "控制电缆"
        if "电力电缆" in text or "变频电力电缆" in text:
            return "电力电缆"
        if any(keyword in text for keyword in ("软导线", "软线")):
            return "软导线"
        if any(keyword in text for keyword in ("电线", "导线", "配线", "穿线")):
            return "电线"

        wire_type = self._extract_wire_type(text)
        if material == "矿物绝缘电缆" or wire_type in _MINERAL_CABLE_MODEL_PATTERNS:
            return "矿物绝缘电缆"
        if "控制电缆" in text or wire_type in _CONTROL_CABLE_MODEL_PATTERNS:
            return "控制电缆"
        if any(keyword in text for keyword in ("软导线", "软线")) or wire_type in _SOFT_WIRE_MODEL_PATTERNS:
            return "软导线"
        if any(keyword in text for keyword in ("电线", "导线", "配线", "穿线")) or wire_type in _WIRE_MODEL_PATTERNS:
            return "电线"
        if "电力电缆" in text or "变频电力电缆" in text or wire_type in _POWER_CABLE_MODEL_PATTERNS:
            return "电力电缆"
        return ""

    def _extract_cable_head_type(self, text: str) -> str:
        """提取电缆头子类锚点。"""
        if not text:
            return ""
        if any(keyword in text for keyword in ("中间头", "中间接头")):
            return "中间头"
        if any(keyword in text for keyword in ("终端头", "终端接头")):
            return "终端头"
        return ""

    def _extract_conduit_type(self, text: str) -> str:
        """提取配管类型锚点，如 JDG/KBG/SC/PC。"""
        if not text:
            return ""

        upper_text = re.sub(r"\s+", "", text).upper().replace("KJG", "KBG")
        conduit_context_tokens = (
            "配管",
            "导管",
            "电气配管",
            "电线管",
            "穿线管",
            "钢导管",
            "镀锌电线管",
            "金属软管",
            "可挠金属套管",
            "明配",
            "暗配",
            "敷设",
        )
        code_rules = (
            ("PVC", ("PVC", "PVC阻燃塑料管", "刚性阻燃管")),
            ("JDG", ("JDG", "套接紧定式镀锌钢导管", "套接紧定式钢导管", "紧定式钢导管")),
            ("KBG", ("KBG", "扣压式薄壁钢导管", "扣压式钢导管")),
            ("FPC", ("FPC", "半硬质阻燃管", "半硬质塑料管")),
            ("SC", ("SC", "焊接钢管")),
            ("PC", ("PC", "PC阻燃塑料管")),
            ("RC", ("RC", "镀锌电线管")),
            ("MT", ("MT", "金属电线管")),
            ("G", ("DG", "G", "镀锌钢管")),
        )
        for canonical, aliases in code_rules:
            for alias in aliases:
                if alias.isascii():
                    match = re.search(
                        rf"(?<![A-Z0-9]){re.escape(alias)}(?=\d|管|(?![A-Z0-9]))",
                        upper_text,
                    )
                    if not match:
                        continue
                    next_char = upper_text[match.end():match.end() + 1]
                    if next_char and not next_char.isdigit() and next_char != "管":
                        if not any(token in text for token in conduit_context_tokens):
                            continue
                    return canonical
                elif alias in text:
                    return canonical
        return ""

    # 材质同义词组（工程实践中等价的材质名称）
    # 每组里的材质互相等价，比如"碳钢通风管道"就是用"薄钢板"做的
    _MATERIAL_SYNONYM_GROUPS = [
        {"碳钢", "薄钢板", "碳钢板", "普通钢板"},  # 通风风管常见
        {"镀锌", "镀锌钢板", "镀锌钢管"},  # 镀锌系列
        {"不锈钢", "不锈钢板", "不锈钢管"},  # 不锈钢系列
        {"铸铁", "铸铁管"},  # 铸铁系列
        {"涂塑", "涂覆碳钢管", "涂塑钢管", "涂覆钢管"},  # 涂覆系列
    ]

    def _materials_equivalent(self, mat1: str, mat2: str) -> bool:
        """判断两个材质是否等价（在同一个同义词组里）"""
        for group in self._MATERIAL_SYNONYM_GROUPS:
            if mat1 in group and mat2 in group:
                return True
        return False

    def _extract_connection(self, text: str) -> Optional[str]:
        """
        提取连接方式

        词汇来源：基础列表 + 定额库自动提取
        """
        explicit = self._extract_explicit_connection(text)
        if explicit:
            return explicit

        self._ensure_vocab_loaded()
        for conn in self._connections:
            if conn in text:
                return conn
        return None

    @staticmethod
    def _extract_explicit_connection(text: str) -> Optional[str]:
        if not text:
            return None

        explicit_rules = (
            (("电磁感应热熔", "双热熔连接", "双热熔", "热熔连接", "热熔"), "热熔连接"),
            (("电熔连接", "电熔"), "电熔连接"),
            (("卡压连接", "卡压"), "卡压连接"),
            (("环压连接", "环压"), "环压连接"),
            (("沟槽连接", "沟槽", "卡箍连接", "卡箍"), "沟槽连接"),
            (("螺纹连接", "螺纹", "丝扣连接", "丝扣"), "螺纹连接"),
            (("法兰连接", "法兰"), "法兰连接"),
            (("承插连接", "承插"), "承插连接"),
            (("焊接连接", "焊接"), "焊接连接"),
            (("粘接",), "粘接"),
        )
        for aliases, normalized in explicit_rules:
            if any(alias in text for alias in aliases):
                return normalized
        return None

    def _extract_valve_connection_family(self, text: str, connection: str = "") -> str:
        """提取管道阀门连接家族锚点。"""
        if not text or not any(keyword in text for keyword in ("阀门", "闸阀", "蝶阀", "球阀", "截止阀", "止回阀")):
            return ""

        if "焊接法兰阀" in text:
            return "焊接法兰阀"
        if "螺纹法兰阀" in text:
            return "螺纹法兰阀"
        if "法兰阀门" in text:
            return "法兰阀门"
        if "螺纹阀门" in text:
            return "螺纹阀门"

        if "焊接" in connection:
            return "焊接法兰阀"
        if "法兰" in connection or "卡箍" in connection or "沟槽" in connection:
            return "法兰阀门"
        if "螺纹" in connection or "丝扣" in connection:
            return "螺纹阀门"
        return ""

    def _extract_valve_type(self, text: str) -> str:
        """提取阀门类型锚点。"""
        if not text:
            return ""
        valve_rules = (
            ("止回阀", ("止回阀", "逆止阀", "回流阀")),
            ("截止阀", ("截止阀", "截断阀")),
            ("闸阀", ("闸阀", "软密封闸阀", "信号闸阀")),
            ("蝶阀", ("蝶阀", "信号蝶阀", "电动蝶阀", "双位蝶阀")),
            ("球阀", ("球阀", "浮球阀")),
            ("安全阀", ("安全阀",)),
            ("减压阀", ("减压阀",)),
            ("电磁阀", ("电磁阀",)),
            ("排气阀", ("排气阀", "自动排气阀", "快速排气阀")),
        )
        for canonical, aliases in valve_rules:
            if any(alias in text for alias in aliases):
                return canonical
        return ""

    def _extract_support_material(self, text: str) -> str:
        """提取支架材质锚点。"""
        if not text:
            return ""
        material_rules = (
            ("C型槽钢", ("C型槽钢", "C槽钢")),
            ("槽钢", ("槽钢",)),
            ("角钢", ("角钢",)),
            ("圆钢", ("圆钢", "镀锌圆钢")),
            ("扁钢", ("扁钢", "镀锌扁钢")),
            ("型钢", ("型钢", "一般型钢")),
        )
        for canonical, aliases in material_rules:
            if any(alias in text for alias in aliases):
                return canonical
        return ""

    def _extract_support_scope(self, text: str) -> str:
        if not text or not any(keyword in text for keyword in ("支架", "吊架", "支吊架", "支撑架")):
            return ""
        if any(keyword in text for keyword in ("抗震支架", "抗震支吊架", "抗震吊架")):
            return "抗震支架"
        if any(keyword in text for keyword in (
            "桥架支撑架", "电缆桥架支撑架", "桥架支架", "桥架侧纵向", "母线槽支架",
        )):
            return "桥架支架"
        if any(keyword in text for keyword in ("设备支架", "设备吊架", "设备支吊架")):
            return "设备支架"
        if any(keyword in text for keyword in ("管道支架", "管架", "一般管架", "03S402", "室内管道")):
            return "管道支架"
        return ""

    def _extract_support_action(self, text: str) -> str:
        if not text or not any(keyword in text for keyword in ("支架", "吊架", "支吊架", "支撑架")):
            return ""
        has_make = any(keyword in text for keyword in ("制作安装", "制安", "制作", "单件重量", "图集", "型钢"))
        has_install = any(keyword in text for keyword in ("制作安装", "制安", "安装"))
        if has_make and has_install:
            return "制作安装"
        if has_make:
            return "制作"
        if has_install:
            return "安装"
        return ""

    def _extract_surface_process(self, text: str) -> str:
        """提取除锈/刷油/漆种等表面处理工艺。"""
        if not text:
            return ""
        process_rules = (
            ("手工除锈", ("手工除锈",)),
            ("机械除锈", ("机械除锈", "动力工具除锈")),
            ("喷砂除锈", ("喷砂除锈",)),
            ("刷油", ("刷油",)),
            ("红丹防锈漆", ("红丹防锈漆",)),
            ("防锈漆", ("防锈漆",)),
            ("调和漆", ("调和漆", "调合漆")),
            ("银粉漆", ("银粉漆",)),
            ("镀锌", ("镀锌", "热镀锌", "热浸锌")),
        )
        hits: list[str] = []
        for canonical, aliases in process_rules:
            if any(alias in text for alias in aliases) and canonical not in hits:
                if canonical == "防锈漆" and "红丹防锈漆" in hits:
                    continue
                hits.append(canonical)
        return "/".join(hits)

    def _extract_sanitary_subtype(self, text: str) -> str:
        """提取卫生器具细类。"""
        if not text:
            return ""
        if any(alias in text for alias in ("坐便器", "座便器", "马桶")):
            return "坐便器"
        if "蹲便器" in text or "蹲式大便器" in text:
            return "蹲便器"
        if "大便器" in text:
            if any(alias in text for alias in ("蹲式", "脚踏", "感应蹲", "蹲便")):
                return "蹲便器"
            if any(alias in text for alias in ("连体水箱", "隐蔽水箱", "高水箱", "低水箱", "自闭阀", "座便", "坐便")):
                return "坐便器"
            return ""

        subtype_rules = (
            ("小便器", ("小便器",)),
            ("洗发盆", ("洗发盆", "洗头盆")),
            ("净身盆", ("净身盆", "妇洗器", "妇洁器")),
            ("洗脸盆", ("洗脸盆", "洗面盆", "洗手盆", "面盆")),
            ("洗涤盆", ("洗涤盆", "水槽", "单孔水槽")),
            ("拖布池", ("拖布池", "拖把池")),
            ("淋浴器", ("淋浴器", "淋浴喷头")),
            ("地漏", ("地漏",)),
            ("阻火圈", ("阻火圈",)),
        )
        for canonical, aliases in subtype_rules:
            if any(alias in text for alias in aliases):
                return canonical
        return ""

    def _extract_sanitary_mount_mode(self, text: str) -> str:
        if not text:
            return ""
        if any(keyword in text for keyword in ("壁挂式", "挂墙式", "挂壁式", "墙挂式")):
            return "挂墙式"
        if any(keyword in text for keyword in ("立式", "落地式", "立柱式")):
            return "立式"
        if any(keyword in text for keyword in ("蹲式", "蹲便器", "蹲位")):
            return "蹲式"
        return ""

    def _extract_sanitary_flush_mode(self, text: str) -> str:
        if not text:
            return ""
        if not any(keyword in text for keyword in (
            "便器", "小便", "大便", "坐便", "蹲便", "冲洗阀", "水箱",
        )):
            return ""
        if "感应" in text:
            return "感应"
        if "脚踏" in text:
            return "脚踏"
        if "自动冲洗" in text:
            return "自动冲洗"
        if "自闭阀" in text or "自闭式冲洗阀" in text:
            return "自闭阀"
        if "普通阀" in text:
            return "普通阀"
        return ""

    def _extract_sanitary_water_mode(self, text: str) -> str:
        if not text:
            return ""
        if any(keyword in text for keyword in ("冷热水", "冷热", "冷热混水", "冷热龙头")):
            return "冷热水"
        if any(keyword in text for keyword in ("冷水", "单冷", "冷水龙头")):
            return "冷水"
        if any(keyword in text for keyword in ("热水", "单热", "热水龙头")):
            return "热水"
        return ""

    def _extract_sanitary_nozzle_mode(self, text: str) -> str:
        if not text:
            return ""
        if any(keyword in text for keyword in ("双嘴", "双孔", "双龙头")):
            return "双嘴"
        if any(keyword in text for keyword in ("单嘴", "单孔", "单龙头")):
            return "单嘴"
        return ""

    def _extract_sanitary_tank_mode(self, text: str) -> str:
        if not text:
            return ""
        if "连体水箱" in text:
            return "连体水箱"
        if any(keyword in text for keyword in ("隐藏水箱", "隐蔽水箱", "暗藏水箱")):
            return "隐藏水箱"
        if "高水箱" in text:
            return "高水箱"
        if "低水箱" in text:
            return "低水箱"
        return ""

    def _extract_lamp_type(self, text: str) -> str:
        if not text or "灯" not in text:
            return ""
        if any(keyword in text for keyword in ("疏散指示", "标志灯", "出口灯")):
            return "标志灯"
        if "应急灯" in text:
            return "应急灯"
        if any(keyword in text for keyword in ("线形灯", "线性灯", "灯带", "荧光灯带")):
            return "灯带"
        if any(keyword in text for keyword in ("筒灯", "防眩筒灯")):
            return "筒灯"
        if any(keyword in text for keyword in ("壁灯", "墙灯", "壁装灯")):
            return "壁灯"
        if any(keyword in text for keyword in ("轮廓灯", "立面轮廓灯")):
            return "轮廓灯"
        if any(keyword in text for keyword in ("投光灯", "泛光灯")):
            return "投光灯"
        if any(keyword in text for keyword in ("平板灯", "面板灯", "格栅灯盘", "长条灯", "条形灯", "吸顶灯")):
            return "吸顶灯"
        if "荧光灯" in text:
            return "荧光灯"
        return ""

    def _extract_circuits(self, text: str) -> Optional[int]:
        """
        提取回路数（配电箱按回路分档）

        支持格式:
        - 7回路 → 7
        - 回路数:7回路 → 7
        - 规格(回路以内) 48 → 48  （定额名称格式，数字在"回路"后面）
        """
        return self._search_first_int(text, [
            # 格式1：明确的"X回路"
            r'(\d+)\s*回路',
            # 格式2：定额名称"回路以内) 48"，数字在括号关闭后
            r'回路[^)）]*[)）]\s*(\d+)',
            # 格式3："X路"（排除"路灯"、"路由"、"路径"等干扰词）
            r'(\d+)\s*路(?!灯|由|径|面)',
        ])

    def _extract_port_count(self, text: str) -> Optional[int]:
        """提取交换机等网络设备口数。"""
        if not text:
            return None
        if not any(keyword in text for keyword in ("交换机", "配线架", "集线器", "路由器", "网络设备")):
            return None

        match = re.search(r'(\d+)\s*口', text)
        if not match:
            return None

        value = int(match.group(1))
        if 1 <= value <= 512:
            return value
        return None

    def _extract_item_count(self, text: str) -> Optional[int]:
        """提取通用数量档位参数，如“扬声器数量≤50台”."""
        if not text:
            return None

        patterns = [
            r'(?:数量|台数|只数|套数|樘数|扬声器数量)\s*(?:[:：]|[（(])?\s*[≤<>=]?\s*(\d+)\s*(?:台|个|只|套|樘)?',
            r'(?:数量|台数|只数|套数|樘数)[^0-9]{0,6}(\d+)\s*(?:台|个|只|套|樘)',
            r'[≤<]\s*(\d+)\s*(?:台|个|只|套|樘)(?![A-Za-z0-9])',
            r'(\d+)\s*(?:台|个|只|套|樘)(?:以下|以内|及以下|及以内)',
        ]
        value = self._search_first_int(text, patterns)
        if value is None or not (1 <= value <= 100000):
            return None
        return value

    def _extract_item_length(self, text: str) -> Optional[float]:
        """提取通用长度档位参数，统一返回米。"""
        if not text:
            return None

        explicit_patterns = [
            r'(?:建筑物)?檐口高度(?:、层数)?[：:]\s*(\d+(?:\.\d+)?)\s*(?:m|M|米)(?![mM2²])',
            r'平均井深\s*(\d+(?:\.\d+)?)\s*(?:m|M|米)(?![mM2²])',
            r'喷(?:洒|射)半径[：:]\s*(?:\d+(?:\.\d+)?\s*[-~至]\s*)?(\d+(?:\.\d+)?)\s*(?:m|M|米)(?![mM2²])',
            r'(?:杆高|灯高)\s*(\d+(?:\.\d+)?)\s*(?:/\s*\d+(?:\.\d+)?)?\s*(?:m|M|米)(?![mM2²])',
            r'柱高\s*(\d+(?:\.\d+)?)\s*mm',
        ]
        for pattern in explicit_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            value = float(match.group(1))
            if "柱高" in pattern:
                value = value / 1000.0
            if 0 < value <= 100000:
                return value

        patterns = [
            r'(?:长度|延长米|平均桩长|管长|桩长|粧长|挡墙高度|墙高|挖土深度|坑深|人力车运|建筑檐高|檐口高度|基础标高|井深|柱高|内周长)\s*(?:[:：]|[（(]|在)?\s*[≤<>=]?(\d+(?:\.\d+)?)\s*(?:m|M|米)(?![mM2²])',
            r'(?:长度|延长米|平均桩长|管长|桩长|檐口高度|基础标高|井深|柱高|内周长)\s*(?:[:：]|[（(]|在)?\s*[≤<>=]?\s*(\d+(?:\.\d+)?)\s*(?:m|米)(?![m2²])',
            r'[≤<]\s*(\d+(?:\.\d+)?)\s*(?:m|米)(?![m2²])',
            r'(\d+(?:\.\d+)?)\s*(?:m|米)(?![m2²])(?:以下|以内|及以下|及以内)',
        ]
        value = self._search_first_float(text, patterns)
        if value is None or not (0 < value <= 100000):
            return None
        return value

    def _extract_shape(self, text: str) -> str:
        """
        提取风管形状（矩形/圆形）

        来源：
        1. 清单描述中的"形状：矩形风管"
        2. 定额名称中的"圆形风管制作"/"矩形风管制作"
        """
        # 优先从"形状：xxx"字段提取（清单描述格式）
        shape_match = re.search(r'形状[：:]\s*(矩形|圆形|方形)', text)
        if shape_match:
            shape = shape_match.group(1)
            return "矩形" if shape == "方形" else shape

        # 从"矩形风管"/"圆形风管"关键词提取（定额名称格式）
        duct_shape = re.search(r'(矩形|圆形)风管', text)
        if duct_shape:
            return duct_shape.group(1)

        return ""

    def _extract_perimeter(self, text: str) -> Optional[float]:
        """
        提取周长参数（通风空调风口/阀门/散流器/消声器的核心取档参数）

        来源：
        1. 定额名称："周长(mm以内) 3200" → 3200
        2. 清单规格（矩形）："规格：800*320" → 周长 = (800+320)*2 = 2240
        3. 三维规格："规格：400*120*1000" → 周长 = (400+120)*2 = 1040（忽略长度）
        4. 清单规格（圆形）："φ400" → 周长 = π×400 = 1257（圆形直径转周长）
        """
        # 先走矩形逻辑（定额名称直接给周长 / W×H算周长）
        result = self._extract_named_mm_or_spec(text, "周长", use_perimeter=True)
        if result is not None:
            return result

        # 清单格式：直接写"周长2000mm"或"周长:2000"（无括号）
        direct_match = re.search(r'周长[：:]*\s*(\d{3,5})\s*(?:mm)?', text)
        if direct_match:
            return float(direct_match.group(1))

        # 矩形没提到，检查有没有圆形直径（φ/Φ），有就算周长 = π × 直径
        diameter_match = re.search(r'[ΦφΦ]\s*(\d+)', text)
        if diameter_match:
            diameter = float(diameter_match.group(1))
            if diameter > 10:  # 过滤掉太小的值（可能是其他参数）
                return round(math.pi * diameter, 1)

        # 兜底：从名称中直接提取 W×H 并计算周长
        # 适用于"防火阀1600x400"、"止回阀-1250*1250"、"风口500*300"等
        # 清单名称中直接嵌入尺寸，没有"规格："前缀，上面的_extract_spec_wh()提取不到
        # 仅在文本含通风空调设备关键词时启用，避免误提取电缆/桥架的WxH
        _PERIMETER_KW = ("阀", "风口", "喷口", "散流器", "消声器", "消声", "出风口", "风管")
        if any(kw in text for kw in _PERIMETER_KW):
            wh_match = re.search(r'(\d{2,4})\s*[*×xX]\s*(\d{2,4})', text)
            if wh_match:
                w = float(wh_match.group(1))
                h = float(wh_match.group(2))
                if w >= 50 and h >= 50:  # 通风空调构件尺寸至少50mm
                    return (w + h) * 2

        return None

    def _extract_half_perimeter(self, text: str) -> Optional[float]:
        """
        提取半周长参数（配电箱悬挂/嵌入式按半周长取档，全国通用）

        来源：
        1. 定额名称："半周长1.0m" → 1000 (mm)
        2. 定额名称："半周长(mm以内) 1000" → 1000
        3. 定额名称："半周长500mm以内" → 500 (mm)
        4. 定额名称："半周长≤1.5m" → 1500 (mm)
        5. 清单规格："规格：420*470*120" → 半周长 = 420+470 = 890 (mm)
        6. 清单无规格但含"配电箱" → 默认1500mm（行业惯例按1.5m套用）

        匹配顺序：mm格式优先于m格式，防止"500mm"被误当"500m"
        """
        # === 1. mm格式优先（不会和m混淆） ===

        # 1a: "半周长(mm以内) 700" / "半周长(mm) ≤1500" / "半周长(mm)≤1500"
        m_mm_paren = re.search(
            r'半周长[^)]*mm[^)]*[)）]\s*[≤≥<>]?\s*(\d+(?:\.\d+)?)', text)
        if m_mm_paren:
            return float(m_mm_paren.group(1))  # 已是mm

        # 1b: "半周长500mm以内" / "半周长1500mm"（无括号，mm直接跟数字后面）
        m_mm_suffix = re.search(
            r'半周长\s*[≤≥<>]?\s*(\d+(?:\.\d+)?)\s*mm', text)
        if m_mm_suffix:
            return float(m_mm_suffix.group(1))  # 已是mm

        # === 2. m格式（需×1000转mm） ===

        # 2a: "(半周长m以内) 1.5" / "(半周长) 1.5m以内" / "嵌入式(半周长m以内) 1.5"
        m_paren = re.search(
            r'半周长[^)]*[)）]\s*[≤≥<>]?\s*(\d+(?:\.\d+)?)', text)
        if m_paren:
            val = float(m_paren.group(1))
            if val < 10:  # 小于10视为米（配电箱半周长一般0.5~5m）
                return val * 1000  # m → mm
            return val  # 大于10视为毫米（兼容旧格式）

        # 2b: "半周长1.5m" / "半周长≤1.5m" / "悬挂式半周长1.0m"
        # m(?!m) 负向前瞻：确保m后面不是m（避免匹配到mm的第一个m）
        m_m_suffix = re.search(
            r'半周长\s*[≤≥<>]?\s*(\d+(?:\.\d+)?)\s*m(?!m)', text)
        if m_m_suffix:
            return float(m_m_suffix.group(1)) * 1000  # m → mm

        # === 3. 从清单规格 W*H 计算 ===
        spec_wh = self._extract_spec_wh(text)
        if spec_wh:
            w, h = spec_wh
            return w + h  # 半周长 = W + H（单位mm）

        box_size_match = re.search(r'箱体尺寸[：:]\s*(\d+(?:\.\d+)?)\s*[*×xX]\s*(\d+(?:\.\d+)?)', text)
        if box_size_match:
            return float(box_size_match.group(1)) + float(box_size_match.group(2))

        # === 4. 默认值 ===
        if re.search(r'配电箱|配电柜|动力箱|照明箱', text):
            install_cues = (
                "明装", "暗装", "距地", "挂墙", "壁挂", "嵌入", "嵌墙", "悬挂", "落地",
            )
            has_box_install_cue = any(cue in text for cue in install_cues)
            has_box_model = bool(re.search(r'\b\d*[A-Z]{1,4}\d+[A-Z0-9-]*\b', text, re.IGNORECASE))
            if has_box_install_cue or has_box_model:
                return 1500.0

        return None

    def _extract_large_side(self, text: str) -> Optional[float]:
        """
        提取大边长参数（弯头导流叶片、矩形风管等按大边长取档）

        来源：
        1. 定额名称："大边长(mm以内) 630" → 630
        2. 清单规格："规格：800*500" → 大边长 = max(800, 500) = 800
        """
        result = self._extract_named_mm_or_spec(text, "大边长", use_perimeter=False)
        if result is not None:
            return result

        direct_match = re.search(r'大边长\s*(?:mm|毫米)?\s*[≤<>=]?\s*(\d+(?:\.\d+)?)', text, re.IGNORECASE)
        if direct_match:
            return float(direct_match.group(1))

        return None

    def _extract_elevator_stops(self, text: str) -> Optional[int]:
        """
        提取电梯停靠站数

        中国建筑楼层规则：没有0层（B1直接到1楼）。
        计算方法：
        - "停靠层数:-2~24层" → abs(-2) + 24 = 26站（跨地下+地上，无0层）
        - "停靠层数:1~10层" → 10 - 1 + 1 = 10站（纯地上）
        - "26站" → 直接取值
        """
        # 模式1：层数范围格式 "停靠层数:-2~24层"（最常见的清单描述格式）
        range_match = re.search(
            r'(?:停靠层数|停靠层站|停靠站数|层数)[：:]\s*(-?\d+)\s*[~～\-至到]\s*(-?\d+)\s*(?:层|站)?',
            text
        )
        if range_match:
            start = int(range_match.group(1))  # 起始层（可能为负，如-2）
            end = int(range_match.group(2))    # 终止层（正数，如24）

            if start < 0 and end > 0:
                # 跨越地下+地上：站数 = |起始层| + 终止层（中国无0层）
                stops = abs(start) + end
            elif start < 0 and end <= 0:
                # 纯地下：站数 = |起始层| - |终止层| + 1
                stops = abs(start) - abs(end) + 1
            else:
                # 纯地上：站数 = 终止层 - 起始层 + 1
                stops = end - start + 1

            if self._is_valid_elevator_stops(stops):  # 合理范围校验（定额最大到120站）
                return stops

        # 模式2/3：直接给出站数（优先“站数/停靠站”，其次“26站”）
        stops = self._search_first_int(text, [
            r'(?:站数|停靠站)[：:\s]*(\d+)',
            r'(\d+)\s*站',
        ])
        if stops is not None and self._is_valid_elevator_stops(stops):
            return stops

        return None

    @staticmethod
    def _is_valid_elevator_stops(stops: int) -> bool:
        """电梯站数合理范围校验。"""
        return 2 <= stops <= 200

    # 中文数字→阿拉伯数字映射（开关联数用）
    _CN_GANG_NUM = {"单": 1, "双": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                     "一": 1, "二": 2}

    def _extract_switch_gangs(self, text: str) -> Optional[int]:
        """
        提取开关联数（单联/双联/三联/四联，开关按联数分档）

        支持格式：
        - 清单名称："单联单控开关" → 1, "双联双控开关" → 2
        - 定额名称："暗装式开关面板(单控) 单联" → 1
        - 定额名称："≤3联" / "联数(联以内) 3" → 3

        只在含"开关"或"按钮"上下文中提取，避免误匹配（如"联动""联锁"等）
        """
        # 守卫条件：只有开关/按钮/强电插座相关的文本才提取联数
        weak_current_outlets = ("信息插座", "电视插座", "电话插座", "网络插座", "光纤插座")
        has_outlet_context = "插座" in text and not any(keyword in text for keyword in weak_current_outlets)
        has_switch_control_phrase = bool(
            re.search(r'([单双三四五六一二两])联(?:单控|双控)', text)
        )
        if not has_outlet_context and not has_switch_control_phrase and not any(kw in text for kw in ["开关", "按钮"]):
            return None

        # 组合插座常写成"两孔加三孔/二三孔"，定额通常按双联处理。
        if has_outlet_context and re.search(r'(?:两|二|2)\s*孔?\s*(?:加|和|及|、)?\s*(?:三|3)\s*孔', text):
            return 2

        # 格式1："单联"、"双联"等中文数字+联
        match = re.search(r'([单双三四五六一二两])联', text)
        if match:
            if match.group(1) == "两":
                return 2
            return self._CN_GANG_NUM.get(match.group(1))

        # 格式2："3联"等数字+联（排除"联动""联锁"等非联数词）
        match = re.search(r'(\d)\s*联(?!动|锁|合|网|系|通)', text)
        if match:
            val = int(match.group(1))
            if 1 <= val <= 6:
                return val

        # 格式3：定额格式"联数(联以内) 3"
        match = re.search(r'联数\s*(?:[（(][^)）]*[)）])?\s*[≤≥<>]?\s*(\d+)', text)
        if match:
            val = int(match.group(1))
            if 1 <= val <= 6:
                return val

        return None

    def _extract_outlet_grounding(self, text: str) -> str:
        """提取插座是否带接地，仅用于强电插座。"""
        if not text or "插座" not in text:
            return ""
        if any(keyword in text for keyword in ("信息插座", "电视插座", "电话插座", "网络插座", "光纤插座")):
            return ""

        grounding_keywords = (
            "带接地", "带保护极", "保护极", "接地极",
            "二三极", "二、三极", "二三孔", "二、三孔",
            "三孔", "五孔", "四孔", "空调插座",
        )
        if any(keyword in text for keyword in grounding_keywords):
            return "带接地"

        if any(keyword in text for keyword in ("两孔", "二孔", "二极")):
            return "不带接地"
        return ""

    # 安装/敷设方式关键词规则（按优先级排列，长词优先避免误匹配）
    # 统一归类为：明装/暗装/落地/挂墙/嵌入/吊装/悬挂/明敷/暗敷
    _INSTALL_METHOD_RULES = [
        # 敷设方式（电缆/接地母线等用"明敷/暗敷"）
        (["明敷设", "明敷"], "明敷"),
        (["暗敷设", "暗敷"], "暗敷"),
        # 安装方式-具体子类（长词优先）
        (["壁挂安装", "壁挂式", "挂墙安装", "挂墙式", "挂墙", "壁式", "壁装", "墙上式"], "挂墙"),
        (["落地安装", "落地式"], "落地"),
        (["嵌入式", "嵌入安装", "嵌顶式"], "嵌入"),
        (["吸顶式", "吸顶安装"], "吸顶"),
        (["吊装", "吊顶内安装", "吊顶安装", "吊顶式", "天花式", "天棚式"], "吊装"),
        (["悬挂式", "悬挂安装", "悬吊"], "悬挂"),
        # 明装/暗装（最通用的二分法，放在最后避免覆盖更具体的类型）
        (["明装"], "明装"),
        (["暗装"], "暗装"),
    ]

    def _extract_install_method(self, text: str) -> str:
        """
        从清单或定额名称提取安装/敷设方式

        覆盖场景：
        - 配电箱：明装/暗装/落地/挂墙/嵌入
        - 开关插座：明装/暗装
        - 接地母线：明敷/暗敷
        - 灯具：吊装/吸顶（吸顶不提取，由其他规则处理）

        返回归一化的安装方式字符串，如"明装""暗敷""落地"等。
        未识别则返回空字符串。
        """
        if not text:
            return ""
        if "紧急呼叫" in text and "扬声器" in text:
            return "挂墙"
        kv_match = re.search(
            r'(?:安装形式|安装方式|配置形式|安装类型)\s*[：:]\s*([^\n\r,，;；]+)',
            text,
        )
        if kv_match:
            raw = kv_match.group(1).strip()
            structured_rules = [
                (("明配",), "明装"),
                (("暗配",), "暗装"),
                (("明敷",), "明敷"),
                (("暗敷",), "暗敷"),
                (("落地",), "落地"),
                (("挂墙", "挂墙式", "壁挂", "挂壁", "壁装", "壁式"), "挂墙"),
                (("嵌入", "嵌入式", "嵌墙", "嵌装"), "嵌入"),
                (("吊装",), "吊装"),
                (("吸顶",), "吸顶"),
                (("悬挂", "悬吊"), "悬挂"),
                (("明装",), "明装"),
                (("暗装",), "暗装"),
            ]
            matched_methods = {
                method
                for keywords, method in structured_rules
                if any(keyword in raw for keyword in keywords)
            }
            if len(matched_methods) == 1:
                return next(iter(matched_methods))
            if len(matched_methods) > 1:
                return ""
        for keywords, method in self._INSTALL_METHOD_RULES:
            if any(kw in text for kw in keywords):
                return method
        return ""

    def _extract_box_mount_mode(self, text: str, install_method: str = "") -> str:
        """提取配电箱/配电柜安装大类锚点。"""
        if not text or not any(keyword in text for keyword in ("配电箱", "配电柜", "控制箱", "控制柜", "动力箱", "照明箱")):
            return ""

        if install_method == "落地" or any(keyword in text for keyword in ("落地", "柜基础", "基础槽钢")):
            return "落地式"
        if (
            install_method in {"挂墙", "嵌入", "明装", "暗装", "悬挂"}
            or any(keyword in text for keyword in ("悬挂", "嵌入", "明装", "暗装", "挂墙", "壁挂", "墙上", "柱上", "距地"))
        ):
            return "悬挂/嵌入式"
        if any(keyword in text for keyword in ("配电柜", "控制柜")):
            return "落地式"
        return ""

    def _extract_laying_method(self, text: str) -> str:
        """提取线缆/桥架/配管场景的敷设方式，支持复合方式。"""
        if not text:
            return ""
        if not any(kw in text for kw in ("敷设", "布放", "穿管", "桥架", "线槽", "直埋", "排管", "支架", "管内", "配线", "配管", "配置形式", "配线形式", "电缆沟", "地沟", "明配", "暗配", "明敷", "暗敷")):
            return ""

        hits: list[str] = []

        if any(kw in text for kw in ("明配", "明敷")):
            hits.append("明配")
        if any(kw in text for kw in ("暗配", "暗敷")):
            hits.append("暗配")
        if any(kw in text for kw in ("直埋", "埋地", "埋设")):
            hits.append("直埋")
        if any(kw in text for kw in ("桥架", "梯架", "托盘", "桥架内", "桥架上", "沿桥架")):
            hits.append("桥架")
        if any(kw in text for kw in ("线槽", "槽盒", "线槽内", "穿线槽")):
            hits.append("线槽")
        if any(kw in text for kw in ("排管", "排管内")):
            hits.append("排管")
        if any(kw in text for kw in ("穿管", "管内", "导管内", "钢管内", "PVC管内", "配管内", "穿保护管")):
            hits.append("穿管")
        if any(kw in text for kw in ("电缆沟", "地沟")):
            hits.append("地沟")
        if any(kw in text for kw in ("沿墙", "沿支架", "支架上", "墙面敷设")):
            hits.append("支架")

        normalized: list[str] = []
        for method in hits:
            if method not in normalized:
                normalized.append(method)
        return "/".join(normalized)

    def _extract_bridge_type(self, text: str) -> str:
        """提取桥架细类锚点。"""
        if not text:
            return ""
        if "槽式" in text:
            return "槽式"
        if "托盘式" in text:
            return "托盘式"
        if "梯式" in text or "梯架" in text:
            return "梯式"
        if "线槽" in text and "桥架" in text:
            return "线槽"
        return ""

    def _extract_bridge_wh_sum(self, text: str) -> Optional[float]:
        """提取桥架宽+高，统一为mm；若明显是cm写法则转为mm。"""
        if not text or "桥架" not in text:
            return None

        named_match = re.search(
            r'宽\+高(?:\s*\([^)]+\)|\s*(?:mm|毫米)?(?:以内|以下|以上))?\)?\s*[≤≥<>=]?\s*(\d+(?:\.\d+)?)',
            text,
            re.IGNORECASE,
        )
        if named_match:
            return float(named_match.group(1))

        spec_match = re.search(
            r'(?:规格(?:型号)?[：:]?\s*|MR-?|CT-?)?(\d{2,4})\s*[*×xX]\s*(\d{2,4})(?:\s*[*×xX]\s*\d{2,4})?',
            text,
            re.IGNORECASE,
        )
        if not spec_match:
            return None

        width = float(spec_match.group(1))
        height = float(spec_match.group(2))
        explicit_mm = bool(re.search(r'(?:mm|毫米)', text, re.IGNORECASE))
        if not explicit_mm and width < 100 and height < 100:
            width *= 10
            height *= 10
        return width + height

    # 电梯类型判断规则（按优先级排列：具体类型优先，泛称在后）
    _ELEVATOR_TYPE_RULES = [
        (["货梯", "载货"], "载货电梯"),
        (["杂物", "餐梯", "杂物梯"], "杂物电梯"),
        (["液压"], "液压电梯"),
        (["扶梯", "自动扶梯"], "自动扶梯"),
        (["客梯", "乘客", "观光电梯", "无障碍电梯", "无机房电梯"], "曳引式电梯"),
    ]

    def _extract_elevator_type(self, text: str) -> str:
        """
        从清单名称提取电梯类型

        优先级：货梯 > 杂物电梯 > 液压电梯 > 扶梯 > 客梯/通用电梯
        如"货梯兼消防电梯"优先匹配"货梯"→载货电梯。
        含"电梯"但没匹配具体类型→默认曳引式电梯（最常见）。
        不含电梯相关词→返回空字符串。
        """
        if not any(kw in text for kw in ["电梯", "扶梯", "货梯", "客梯", "杂物梯", "餐梯"]):
            return ""

        for keywords, elevator_type in self._ELEVATOR_TYPE_RULES:
            if any(kw in text for kw in keywords):
                return elevator_type

        # 兜底：含"电梯"→曳引式电梯
        if "电梯" in text:
            return "曳引式电梯"

        return ""

    def _extract_elevator_speed(self, text: str) -> Optional[float]:
        """
        从清单描述中提取电梯运行速度(m/s)

        支持格式：
        - "速度:2.5m/s" 或 "速度：2.5m/s"
        - "运行速度:1.0m/s"
        - "速度 2.5 m/s"（带空格）

        返回浮点数速度值，如 2.5；未找到返回 None。
        用于区分定额中"运行速度2m/s以上"和"2m/s以下"两个子系列。
        """
        match = re.search(
            r'(?:运行速度|速度)[：:\s]*(\d+(?:\.\d+)?)\s*m/s',
            text, re.IGNORECASE
        )
        if match:
            speed = float(match.group(1))
            if 0.1 <= speed <= 20.0:  # 合理范围校验（电梯速度一般0.25~10m/s）
                return speed
        return None

    def _extract_ground_bar_width(self, text: str) -> Optional[float]:
        """
        提取接地扁钢/母带宽度（如"40*4"→40，"60×6"→60）

        接地母线/母带用扁钢制作，规格用"宽×厚"表示（单位mm）。
        特征：宽度远大于厚度（比值≥3），如40×4、60×6、25×4。
        定额按宽度分档，所以提取宽度作为参数。

        改进（Codex 5.4审核）：用局部上下文正则，N×N必须紧邻接地/扁钢关键词，
        避免电缆清单工作内容中远处的"接地"触发误提取（如"3×185...接地、测绝缘"）
        """
        # 局部上下文匹配：N×N必须在接地/扁钢关键词附近（±15字符内）
        # 模式1：关键词在前 — "接地扁钢 40×4"、"接地母线 25×4"
        ground_ctx = r'(?:接地(?:母线|母带)?|扁钢|扁铁)'
        p1 = rf'{ground_ctx}.{{0,15}}?(\d+)\s*[*×xX]\s*(\d+)'
        # 模式2：关键词在后 — "40×4 扁钢"、"25*4 接地母线"
        p2 = rf'(\d+)\s*[*×xX]\s*(\d+).{{0,10}}?(?:扁钢|扁铁|接地母线|接地母带)'

        for pattern in [p1, p2]:
            match = re.search(pattern, text)
            if match:
                a, b = float(match.group(1)), float(match.group(2))
                # 扁钢"宽×厚"，宽>厚；兼容少量反写（4×40）
                width, thickness = a, b
                if width < thickness:
                    width, thickness = thickness, width
                # 扁钢特征：宽/厚比≥3，且宽度在合理范围（通常20-200mm）
                if thickness > 0 and width / thickness >= 3 and 10 <= width <= 200:
                    return width
        return None

    def build_search_text(self, name: str, description: str = "") -> str:
        """
        构建用于搜索的文本
        合并项目名称和特征描述，去除无用信息

        参数:
            name: 项目名称
            description: 项目特征描述

        返回:
            清洗后的搜索文本
        """
        # 合并名称和描述
        text = f"{name} {description}".strip()

        # 去除序号前缀（如 "1.", "2." 等）
        text = re.sub(r'^\d+\.\s*', '', text)

        # 去除"详见图纸及设计验收规范"等通用废话
        noise_patterns = [
            r'详见图纸.*?规范',
            r'其他[：:]\s*详见.*',
            r'详见设计图纸',
        ]
        for pattern in noise_patterns:
            text = re.sub(pattern, '', text)

        # 去除多余空白
        text = re.sub(r'\s+', ' ', text).strip()

        return text

    def build_quota_query(self, name: str, description: str = "",
                          specialty: str = "",
                          bill_params: dict = None,
                          section_title: str = "",
                          canonical_features: dict = None,
                          context_prior: dict = None) -> str:
        """构建定额搜索query（实际实现在 query_builder.py）"""
        from src.query_builder import build_quota_query as _build
        return _build(self, name, description, specialty=specialty,
                      bill_params=bill_params, section_title=section_title,
                      canonical_features=canonical_features,
                      context_prior=context_prior)

    def params_match(self, bill_params: dict, quota_params: dict) -> tuple[bool, float]:
        """
        检查清单参数和定额参数是否匹配

        参数:
            bill_params: 清单提取的参数
            quota_params: 定额提取的参数

        返回:
            (是否匹配, 匹配分数0-1)
            匹配分数: 1.0=完全匹配, 0.5=部分匹配, 0.0=不匹配
        """
        if not bill_params or not quota_params:
            return True, 0.5  # 没有参数可比较，算部分匹配

        score = 0.0
        total_checks = 0

        # 检查DN（管径）
        if "dn" in bill_params and "dn" in quota_params:
            total_checks += 1
            if bill_params["dn"] == quota_params["dn"]:
                score += 1.0
            else:
                # DN不匹配是硬伤，直接标记不匹配
                return False, 0.0

        # 检查电缆截面
        if "cable_section" in bill_params and "cable_section" in quota_params:
            total_checks += 1
            bill_sec = bill_params["cable_section"]
            quota_sec = quota_params["cable_section"]
            if bill_sec == quota_sec:
                score += 1.0
            elif bill_sec <= quota_sec:
                # 清单截面小于定额档位，可能是向上取档，扣一点分
                score += 0.5
            else:
                return False, 0.0

        # 检查容量（kVA）
        if "kva" in bill_params and "kva" in quota_params:
            total_checks += 1
            if bill_params["kva"] == quota_params["kva"]:
                score += 1.0
            elif bill_params["kva"] <= quota_params["kva"]:
                score += 0.5
            else:
                return False, 0.0

        # 检查材质（软匹配，不匹配只扣分不判死）
        if "material" in bill_params and "material" in quota_params:
            total_checks += 1
            if bill_params["material"] == quota_params["material"]:
                score += 1.0
            elif self._materials_equivalent(bill_params["material"], quota_params["material"]):
                # 同义词（碳钢=薄钢板、镀锌=镀锌钢板等），工程实践中等价
                score += 0.9
            else:
                score += 0.3

        # 检查连接方式（软匹配）
        if "connection" in bill_params and "connection" in quota_params:
            total_checks += 1
            if bill_params["connection"] == quota_params["connection"]:
                score += 1.0
            else:
                score += 0.3

        if total_checks == 0:
            return True, 0.5

        final_score = score / total_checks
        is_match = final_score >= 0.5

        return is_match, final_score


def normalize_bill_text(name: str, description: str = "") -> str:
    """
    规范化清单文本（统一格式，用于经验库存储和查询）

    核心原则：只保留与定额匹配相关的关键信息，去掉所有废话和冗余。
    不同人编清单详细程度不同，有人写得多有人写得少，
    但只要关键参数一样（材质、规格、连接方式等），normalize结果就应该一样。

    处理规则：
    1. 去掉行首序号（如"1."、"2."等）
    2. 统一逗号分隔为换行（不同模板格式统一）
    3. 去掉空值字段（如"绝热厚度：<空>"）
    4. 去掉废话/客套话（如"详见图纸及设计验收规范"）
    5. 去掉与定额匹配无关的字段（如"安装部位"、"压力试验要求"等）
    6. 去掉重复的名称字段（"名称：XXX"如果和清单名一样就去掉）

    参数:
        name: 清单项目名称
        description: 清单特征描述（可能含换行、序号等）

    返回:
        规范化后的纯文本（只含关键信息）
    """
    clean_name = name.strip() if name else ""
    parts = [clean_name] if clean_name else []

    if description:
        # 统一处理不可见空格（Excel导出经常带\xa0即non-breaking space）
        description = description.replace('\xa0', ' ')
        # 统一字段分隔符：不同来源的文本格式不同
        # 1. 有些用中文逗号分隔（如"名称：xxx，规格：yyy"）
        # 2. 有些用空格分隔（如"安装部位:室内 材质:镀锌钢管 规格:DN20"）
        # 3. 有些用换行分隔
        # 统一拆成换行，每个字段单独一行，方便逐行过滤
        description = re.sub(r'，(?=\S+[：:])', '\n', description)  # 中文逗号
        description = re.sub(r'\s+(?=\S+[：:])', '\n', description)  # 空格
        lines = re.split(r'[\r\n]+', description)
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 去掉行首序号（如"1."、"2."、"3、"等）
            line = re.sub(r'^\d+[.、．]\s*', '', line)
            # 去掉"项目特征："等前缀
            line = re.sub(r'^项目特征[：:]\s*', '', line)
            if not line:
                continue

            # --- 跳过空值字段（如"绝热厚度：<空>"或"管道外径："后面没内容的行）---
            if re.match(r'^[^：:]+[：:]\s*(<空>)?\s*$', line):
                continue

            # --- 跳过废话/客套话（与定额匹配完全无关的套话）---
            # "其他：详见图纸及设计验收规范"、"其他：/"、"其他：无"等
            if re.match(r'^其他[：:]\s*(详见|见|按|/|无|—|-)\s*', line):
                continue
            # 整行都是废话（不在字段里的独立废话）
            if re.match(r'^(详见图纸|详见设计|见图纸|按图施工|按设计要求|按规范|'
                        r'符合国家标准|满足设计要求|执行现行规范)', line):
                continue

            # --- 跳过与定额匹配无关的字段 ---
            # 这些字段描述的是施工要求、安装位置、管内介质等，不影响套哪个定额
            if re.match(r'^(压力试验|安装部位|安装位置|'
                        r'施工要求|验收标准|质量要求|安全要求|环保要求)[,、：:]', line):
                continue

            # --- 去掉重复的名称字段 ---
            # "名称：一般填料套管" 如果和清单名一样就跳过（信息冗余）
            m = re.match(r'^名称[：:]\s*(.+)$', line)
            if m and clean_name:
                field_name = m.group(1).strip()
                if field_name == clean_name:
                    continue  # 完全一样，跳过
                # 如果名称字段有额外信息（如"名称：一般填料套管（防水型）"），保留额外部分
                if field_name.startswith(clean_name):
                    extra = field_name[len(clean_name):].strip('()（） ')
                    if extra:
                        line = extra  # 只保留额外信息
                    else:
                        continue

            if line:
                parts.append(line)

    return " ".join(parts)


# 模块级单例，方便直接导入使用
parser = TextParser()
