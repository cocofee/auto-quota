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

import re
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from loguru import logger


class TextParser:
    """从工程文本中提取结构化参数"""

    def __init__(self):
        # 手动维护的基础材质列表（核心词汇，保证覆盖常见材质）
        self._base_materials = [
            # 复合管材
            "钢塑复合管", "铝塑复合管", "衬塑钢管", "涂塑钢管",
            "涂覆碳钢管", "涂覆钢管",  # 涂覆=涂塑，实际借用镀锌钢管定额换主材
            "钢丝网骨架管", "孔网钢带管", "塑铝稳态管", "铝合金衬塑管",
            "PPR复合管",  # PPR复合管（铝合金衬塑PPR等）
            # 金属管材
            "镀锌钢管", "焊接钢管", "无缝钢管", "不锈钢管",
            "薄壁不锈钢管", "铜管", "铝管",
            "铸铁管", "球墨铸铁管", "柔性铸铁管",
            # 塑料管材
            "PPR冷水管", "PPR热水管",  # PPR细分（出现在清单描述中）
            "PPR管", "PE管", "PVC管", "UPVC管", "HDPE管",
            "PP管", "ABS管", "CPVC管",
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
        ]

        # 最终使用的列表（基础 + 从定额库自动提取的，按长度降序）
        self._materials = None  # 延迟初始化
        self._connections = None  # 延迟初始化

        # 解析结果缓存（热点文本会被多次解析：规则校验/经验校验/主流程）
        self._parse_cache = OrderedDict()
        self._parse_cache_max = 4096

    def _ensure_vocab_loaded(self):
        """确保词汇列表已加载（合并基础列表 + 定额库提取的词汇）"""
        if self._materials is not None:
            return  # 已加载

        # 先用基础列表
        mat_set = set(self._base_materials)
        conn_set = set(self._base_connections)

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

        # 按长度降序排列（长词优先匹配，避免"钢塑复合管"被"钢管"截断）
        self._materials = sorted(cleaned_materials, key=len, reverse=True)
        self._connections = sorted(conn_set, key=len, reverse=True)

    def _get_parse_cache(self, text: str) -> Optional[dict]:
        """读取解析缓存（LRU 命中后刷新活跃度）。"""
        cached = self._parse_cache.get(text)
        if cached is None:
            return None
        self._parse_cache.move_to_end(text)
        return dict(cached)

    def _set_parse_cache(self, text: str, result: dict):
        """写入解析缓存并维护 LRU 上限。"""
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

        # 提取管径（DN）
        dn = self._extract_dn(text)
        if dn is not None:
            result["dn"] = dn

        # 提取电缆截面（mm²）
        section = self._extract_cable_section(text)
        if section is not None:
            result["cable_section"] = section

        # 提取容量（kVA）
        kva = self._extract_kva(text)
        if kva is not None:
            result["kva"] = kva

        # 提取电压等级（kV）
        kv = self._extract_kv(text)
        if kv is not None:
            result["kv"] = kv

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
        if material:
            result["material"] = material

        # 提取连接方式
        connection = self._extract_connection(text)
        if connection:
            result["connection"] = connection

        # 提取回路数（配电箱按回路分档：4/8/16/24/32/48）
        circuits = self._extract_circuits(text)
        if circuits is not None:
            result["circuits"] = circuits

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

        # 提取大边长（弯头导流叶片、矩形风管等按大边长取档）
        large_side = self._extract_large_side(text)
        if large_side is not None:
            result["large_side"] = large_side

        # 提取电梯停靠站数（从"停靠层数:-2~24层"计算）
        elevator_stops = self._extract_elevator_stops(text)
        if elevator_stops is not None:
            result["elevator_stops"] = elevator_stops

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

    # De外径→DN公称直径转换表（塑料管常用De标记外径，定额用DN标记公称直径）
    # 标准对照：GB/T 1047, ISO 4065
    DE_TO_DN = {
        20: 15, 25: 20, 32: 25, 40: 32, 50: 40,
        63: 50, 75: 65, 90: 80, 110: 100, 125: 100,
        140: 125, 160: 150, 200: 200, 225: 200,
        250: 250, 315: 300, 355: 350, 400: 400,
    }

    def _extract_dn(self, text: str) -> Optional[int]:
        """
        提取管径DN值，统一返回整数（毫米）

        支持格式:
        - DN150, DN-150, dn150 → 直接取值
        - De110, de110 → 通过De→DN转换表转换（塑料管外径→公称直径）
        - Φ150, φ150 → 直接取值（Φ是直径符号，工程中常等同于DN）
        - 公称直径150, 公称直径(mm以内) 150 → 直接取值
        - 管径150 → 直接取值
        - 规格：65 → 当值为合理DN范围(10-600)且非尺寸格式时，视为DN
        """
        # 先匹配DN格式（精确）
        dn_patterns = [
            r'[Dd][Nn]\s*[-_]?\s*(\d+)',                         # DN150, DN-150
            r'[ΦφΦ]\s*(\d+)',                                     # Φ150, φ150（直径符号）
            r'公称直径\s*(?:\(mm(?:以内)?\))?\s*(\d+)',            # 公称直径150
            r'管径\s*(\d+)',                                       # 管径150
        ]
        for pattern in dn_patterns:
            match = re.search(pattern, text)
            if match:
                return int(match.group(1))

        # 匹配De格式（塑料管外径标记），转换为DN
        de_match = re.search(r'[Dd][Ee]\s*(\d+)', text)
        if de_match:
            de_value = int(de_match.group(1))
            # 查转换表，找不到则取最接近的DN值
            if de_value in self.DE_TO_DN:
                return self.DE_TO_DN[de_value]
            else:
                # 找最接近的De值
                closest_de = min(self.DE_TO_DN.keys(), key=lambda x: abs(x - de_value))
                return self.DE_TO_DN[closest_de]

        # 匹配"规格：65"格式（清单描述中常见，管道项的规格通常就是DN值）
        # 安全条件：
        # 1. 值为合理DN范围(10-600)
        # 2. 不是尺寸格式(NxN或N*N)
        # 3. 文本中包含管道相关关键词（材质或连接方式），排除电气/弱电项误提取
        # 用具体管材关键词判断，不用"管"——"管"太宽泛，会匹配"管道"（如"碳钢通风管道"是风管不是水管）
        pipe_keywords = ["钢管", "铸铁管", "铜管", "不锈钢管", "塑料管",
                         "PE管", "PPR管", "PVC管", "HDPE管",
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

        return None

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

        # 格式: 截面(mm²以内) 数值
        match = re.search(r'截面\s*(?:\(mm[²2]?(?:以内)?\))?\s*(\d+(?:\.\d+)?)', text)
        if match:
            return float(match.group(1))

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
        spec_match = re.search(
            r'规格[：:]\s*(\d+)\s*[*×xX]\s*(\d+)(?:\s*[*×xX]\s*\d+)?',
            text)
        if not spec_match:
            return None

        w = float(spec_match.group(1))
        h = float(spec_match.group(2))
        if w <= 10 or h <= 10:
            return None
        return w, h

    @staticmethod
    def _format_number_for_query(value: float) -> str:
        return str(int(value)) if value == int(value) else str(value)

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
        self._ensure_vocab_loaded()
        for conn in self._connections:
            if conn in text:
                return conn
        return None

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
        ])

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
        2. 清单规格："规格：800*320" → 周长 = (800+320)*2 = 2240
        3. 三维规格："规格：400*120*1000" → 周长 = (400+120)*2 = 1040（忽略长度）
        """
        return self._extract_named_mm_or_spec(text, "周长", use_perimeter=True)

    def _extract_large_side(self, text: str) -> Optional[float]:
        """
        提取大边长参数（弯头导流叶片、矩形风管等按大边长取档）

        来源：
        1. 定额名称："大边长(mm以内) 630" → 630
        2. 清单规格："规格：800*500" → 大边长 = max(800, 500) = 800
        """
        return self._extract_named_mm_or_spec(text, "大边长", use_perimeter=False)

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

    def build_quota_query(self, name: str, description: str = "") -> str:
        """
        构建定额搜索query（模仿定额命名风格）

        管道类定额命名格式：
          {安装部位}{介质}{材质}({连接方式}) 公称直径(mm以内) {DN值}
        电气设备类定额命名格式：
          配电箱墙上(柱上)明装 规格(回路以内) 8
          电力电缆敷设 沿桥架敷设 截面(mm²以内) 70

        参数:
            name: 清单项目名称（如"复合管"、"成套配电箱"）
            description: 清单项目特征描述

        返回:
            构建好的搜索query
        """
        full_text = f"{name} {description}".strip()
        params = self.parse(full_text)

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
        is_electrical = any(kw in name for kw in ("电缆", "配管", "穿线", "配线", "桥架", "线槽"))
        if (material or dn) and not is_electrical:
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

            return " ".join(query_parts)

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
        normalized_name = self._normalize_bill_name(name)
        query_parts = [normalized_name]

        if description:
            # 从 "1.标签:值\n2.标签:值" 格式的描述中提取关键字段
            fields = self._extract_description_fields(description)

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
                    section_str = self._format_number_for_query(cable_section)
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
                    section_str = self._format_number_for_query(cable_section)
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

        return " ".join(query_parts)

    @staticmethod
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

        # 灯具类：去掉"LED"前缀（定额不按光源分类，按灯具类型分类）
        if "灯" in name:
            cleaned = re.sub(r'LED\s*', '', name, flags=re.IGNORECASE)
            # 吸顶灯 → 普通灯具安装 吸顶灯
            if "吸顶灯" in cleaned:
                return "普通灯具安装 吸顶灯"
            # 直管灯/灯管 → LED灯带 灯管式（LED直管灯套LED灯带定额）
            if re.search(r'直管|灯管', cleaned):
                return "LED灯带 灯管式"
            return cleaned

        # 接线盒（86mm的小接线盒，不是通信用的大接线箱）
        if name == "接线盒":
            return "接线盒安装"

        return name

    def _extract_description_fields(self, description: str) -> dict:
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
            if re.match(r'^(压力试验|安装部位|安装位置|介质|'
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
