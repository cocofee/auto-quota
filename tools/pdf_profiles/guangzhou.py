# -*- coding: utf-8 -*-
"""
广州PDF信息价解析配置

广州市建设工程造价管理站每月发布《人材机价格信息》PDF。

特点：
- 价格是"税前综合价格"（不含税）
- 左右双栏布局（一行数据=左半+右半，各自独立）
- 中间有一列None或空串分隔左右两栏
- 部分材料有"区间值+均值"两列价格，取均值
- 表头列数变化大（5-18列都有），但核心是"材料编码+名称+规格+单位+价格"
- 有材料编码（18位），暂不使用
- 前6页是封面/通知/说明/目录，从第7页开始是价格表
- 多行表头：行2是主列名，行3可能是"区间值/均值"子表头
"""

import re
from .base_profile import BasePDFProfile, clean_price, fill_merged_cells, guess_category


class GuangzhouProfile(BasePDFProfile):
    """
    广州《人材机价格信息》月刊解析配置
    """
    name = "guangzhou"
    description = "广州市建设工程人材机价格信息（月刊，税前综合价格）"

    def classify_page(self, page_num: int, page_text: str) -> str:
        """
        广州PDF前6页是封面/通知/说明/目录，从第7页开始是价格表。
        """
        if page_num <= 6:
            return "skip"
        if "材料编码" in page_text or "税前综合价" in page_text:
            return "municipal"
        return "skip"

    def parse_table(self, rows: list, headers: list,
                    page_type: str, page_num: int) -> list:
        """
        解析广州双栏表格

        核心策略：不依赖复杂的表头解析，直接按数据行结构提取。
        广州的数据行特征：第一列是18位材料编码（纯数字），后面跟名称、规格、单位、价格。
        双栏布局中间有一列None/空串分隔，右栏也是以材料编码开头。
        """
        records = []

        # 先合并多行表头信息，找出均值列的位置
        mean_cols = set()  # 记录哪些列是"均值"列
        # 检查headers和rows前几行有没有"均值"/"区间值"标记
        all_header_rows = [headers] + rows[:3]
        for hr in all_header_rows:
            for ci, cell in enumerate(hr):
                cell_str = str(cell).strip() if cell else ""
                if cell_str == "均值":
                    mean_cols.add(ci)

        # 找出"材料编码"列的位置（用于分割左右栏）
        code_cols = []
        for ci, cell in enumerate(headers):
            cell_str = str(cell).strip().replace('\n', '') if cell else ""
            if cell_str == "材料编码":
                code_cols.append(ci)

        # 安全取列值
        def _safe(row, idx):
            if idx is not None and idx < len(row):
                return row[idx]
            return None

        def _is_material_code(val):
            """判断是否为材料编码（12-18位数字）"""
            if val is None:
                return False
            s = str(val).strip()
            return len(s) >= 12 and s.isdigit()

        for row in rows:
            # 跳过表头行
            row_text = " ".join(str(c) for c in row if c)
            if "材料编码" in row_text and "名称" in row_text:
                continue
            if "区间值" in row_text or "均值" in row_text:
                continue

            # 找这一行中所有材料编码的位置，以此分割左右栏
            code_positions = []
            for ci in range(len(row)):
                if _is_material_code(_safe(row, ci)):
                    code_positions.append(ci)

            if not code_positions:
                continue

            # 把每个编码位置作为一条记录的起点
            for idx, code_pos in enumerate(code_positions):
                # 确定这条记录的范围（到下一个编码或行尾）
                if idx + 1 < len(code_positions):
                    end_pos = code_positions[idx + 1]
                else:
                    end_pos = len(row)

                # 在 [code_pos, end_pos) 范围内提取字段
                segment = row[code_pos:end_pos]
                record = self._parse_segment(segment, code_pos, end_pos, mean_cols)
                if record:
                    records.append(record)

        return records

    def _parse_segment(self, segment: list, start: int, end: int, mean_cols: set) -> dict:
        """
        从一段连续列中提取一条记录

        segment格式（通常）：
        [材料编码, 名称, 规格, 单位, 价格, ...]
        或
        [材料编码, 名称, 规格, 单位, 区间值, 均值, ...]
        或
        [材料编码, DN, 英寸, 壁厚, 单位, 价格, ...]
        """
        if len(segment) < 3:
            return None

        # 第一列是编码（跳过）
        # 后面的列逐个检查
        name = ""
        spec_parts = []
        unit = ""
        price = 0

        for i in range(1, len(segment)):
            cell = segment[i]
            cell_str = str(cell).strip().replace('\n', ' ') if cell else ""
            abs_col = start + i  # 绝对列索引

            if not cell_str:
                continue

            # 如果这列是"均值"列，优先用它的值作为价格
            if abs_col in mean_cols:
                p = clean_price(cell_str)
                if p > 0:
                    price = p
                continue

            # 尝试判断这个值是什么
            p = self._try_price(cell_str)

            if cell_str in ("m", "m²", "m³", "m2", "m3", "t", "kg",
                            "千块", "百块", "块", "个", "套", "根", "张",
                            "㎡", "只", "米", "台", "组", "把", "副",
                            "对", "条", "卷", "桶", "支"):
                unit = cell_str
            elif p > 0 and not name:
                # 还没有名称就遇到数字了，可能是DN这种
                spec_parts.append(cell_str)
            elif p > 0:
                # 有名称了，这是价格
                if price <= 0:
                    price = p
            elif "～" in cell_str or "~" in cell_str:
                # 区间值，取平均
                avg = self._parse_range_price(cell_str)
                if avg > 0 and price <= 0:
                    price = avg
            elif not name:
                # 第一个非数字、非单位的文本就是名称
                name = cell_str
            elif cell_str not in ("说明", "备注", "适用范围", "执行标准",
                                   "加价说明", "单面", "双面"):
                # 后续文本当规格
                spec_parts.append(cell_str)

        if not name or price <= 0:
            return None

        # 跳过标题行残留
        if any(kw in name for kw in ["合计", "小计", "总计", "税前综合"]):
            return None

        # === 名称清洗 ===

        # 1. 去除名称中多余空格（"圆 钢"→"圆钢"，"镀 锌 钢 管"→"镀锌钢管"）
        #    只去掉中文字符之间的空格，保留英文/数字间的空格
        name = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', name)

        # 2. "千米"被当名称 → 移到单位，名称留空（后面会跳过）
        if name in ("千米", "百米", "km"):
            if not unit:
                unit = name
            name = ""

        # 3. Φ/φ/DN开头的规格串跑到名称 → 移到规格
        if re.match(r'^[Φφ]\d', name) or re.match(r'^DN\d', name):
            spec_parts.insert(0, name)
            name = ""

        # 4. 名称中含"加价说明"、"执行标准"等非材料文字 → 跳过
        if any(kw in name for kw in ["加价说明", "执行标准", "适用范围"]):
            return None

        if not name:
            return None

        spec = " ".join(spec_parts)

        return {
            "name": name,
            "spec": spec,
            "unit": unit,
            "price": price,
            "city": "广州",
            "category": guess_category(name),
            "tax_included": False,  # 广州信息价是"税前综合价格"
        }

    def _try_price(self, s: str) -> float:
        """尝试把字符串解析为价格数字"""
        if not s:
            return 0
        # 排除材料编码（长数字串）
        clean = s.replace(',', '').strip()
        if len(clean) > 10 and clean.replace('.', '').isdigit():
            return 0
        # 排除区间值
        if "～" in s or "~" in s:
            return 0
        return clean_price(s)

    def _parse_range_price(self, s: str) -> float:
        """解析区间价格取平均值。如："3172～3395" → 3283.5"""
        parts = re.split(r'[～~]', s)
        if len(parts) == 2:
            low = clean_price(parts[0])
            high = clean_price(parts[1])
            if low > 0 and high > 0:
                return round((low + high) / 2, 2)
        return 0

    def is_header_row(self, row: list) -> bool:
        """判断是否为表头行"""
        row_text = " ".join(str(c) for c in row if c)
        return "材料编码" in row_text and ("名称" in row_text or "DN" in row_text)
