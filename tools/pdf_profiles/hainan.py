# -*- coding: utf-8 -*-
"""
海南PDF信息价解析配置

海南省建设标准定额站发布月度信息价PDF，特点：
1. 分4个区域发布（北部/西部/南部/中部），每个区域独立表格
2. 5列格式：序号 | 材料名称 | 规格型号 | 单位 | 除税价（元）
3. 只有除税价（不含税价），无含税价
4. 主材部分（前约150页）格式统一，最后几页是园林苗木（格式不同，跳过）
5. 含"说明"段落混在表格中间（需跳过）
6. 有些价格是百分比加价率（如"8.00%"），不是绝对价格，需跳过

区域对应城市：
- 北部：海口、澄迈、文昌、定安
- 西部：儋州、临高、昌江、东方
- 南部：三亚、陵水、乐东、保亭、五指山
- 中部：琼中、屯昌、万宁、琼海、白沙、五指山
"""

import re
from .base_profile import BasePDFProfile, clean_price, fill_merged_cells, guess_category


# 区域名称到城市的映射
REGION_MAP = {
    "北部区域": "海口",
    "西部区域": "儋州",
    "南部区域": "三亚",
    "中部区域": "琼海",
}


class HainanProfile(BasePDFProfile):
    """
    海南省建设工程主要材料市场参考价解析

    所有区域统一5列，只有除税价。
    跳过园林苗木和施工机具部分（列数≠5）。
    """
    name = "hainan"
    description = "海南省建设工程主要材料市场参考价（月刊，除税价，分4个区域）"

    def classify_page(self, page_num: int, page_text: str) -> str:
        """
        识别页面类型

        - 包含"材料名称"或"除税价"的是价格表 → municipal
        - 包含"园林绿化苗木"或"施工机具"的 → skip（格式不同）
        - "说明"/"编制说明"/"目  录" → skip
        """
        # 先排除园林苗木和施工机具（列数不一样，不解析）
        if "园林绿化苗木" in page_text or "施工机具" in page_text:
            return "skip"
        if "编制说明" in page_text and "市场参考价" not in page_text:
            return "skip"
        if page_text.strip().startswith("目  录") or page_text.strip().startswith("目录"):
            return "skip"

        # 有价格表特征
        if "除税价" in page_text or "材料名称" in page_text:
            return "municipal"
        # 续表页（有序号和数字价格的）
        if re.search(r'^\d+\s', page_text.strip()):
            return "municipal"

        return "skip"

    def get_page_ranges(self) -> dict:
        """不用固定页码，由classify_page动态判断"""
        return {}

    def parse_table(self, rows: list, headers: list,
                    page_type: str, page_num: int) -> list:
        """
        解析海南5列表格

        表头格式：序号 | 材料名称 | 规格型号 | 单位 | 区域+除税价
        数据行：数字序号 | 名称 | 规格 | 单位 | 价格数字
        """
        records = []

        # 确认列结构：必须是5列
        if not headers or len(headers) < 5:
            return []

        # 找列位置（通过表头关键词）
        name_col = None
        spec_col = None
        unit_col = None
        price_col = None

        for i, h in enumerate(headers):
            h_str = str(h).strip().replace('\n', '') if h else ""
            if "材料名称" in h_str:
                name_col = i
            elif "规格" in h_str:
                spec_col = i
            elif "单位" in h_str:
                unit_col = i
            elif "除税价" in h_str or "区域" in h_str or "北部" in h_str or "南部" in h_str or "西部" in h_str or "中部" in h_str:
                price_col = i

        # 如果没有通过关键词找到列，用默认位置
        if name_col is None:
            name_col = 1
        if spec_col is None:
            spec_col = 2
        if unit_col is None:
            unit_col = 3
        if price_col is None:
            price_col = 4  # 最后一列是价格

        # 确定当前区域（从表头提取）
        region = ""
        header_text = " ".join(str(h) for h in headers if h)
        for region_name in REGION_MAP:
            if region_name in header_text:
                region = region_name
                break

        # 填充合并单元格（同名材料不同规格时，名称只出现一次）
        fill_merged_cells(rows, name_col)

        for row in rows:
            if len(row) <= price_col:
                continue

            # 跳过表头行和非数据行
            seq = str(row[0]).strip() if row[0] else ""
            if not seq or not seq.isdigit():
                continue

            name = str(row[name_col]).strip() if row[name_col] else ""
            spec = str(row[spec_col]).strip() if row[spec_col] else ""
            unit = str(row[unit_col]).strip() if row[unit_col] else ""
            price_str = str(row[price_col]).strip() if row[price_col] else ""

            # 跳过空名称
            if not name:
                continue

            # 跳过"说明"行（有时说明文字被识别成数据行）
            if "说明" in name or "执行标准" in name:
                continue

            # 跳过百分比加价率（如"8.00%"，不是绝对价格）
            if "%" in price_str:
                continue

            # 解析价格
            price = clean_price(price_str)
            if price <= 0:
                continue

            # 猜测分类
            category = guess_category(name)

            # 构建记录
            # 海南只有除税价，需要反算含税价（建材13%税率）
            tax_rate = 0.13
            price_incl_tax = round(price * (1 + tax_rate), 2)

            record = {
                "name": name,
                "spec": spec,
                "unit": unit,
                "category": category,
                "price": price_incl_tax,  # import_price_pdf.py 期望的是含税价
                "tax_rate": tax_rate,
                "price_excl_tax": price,  # 额外记录除税价（原始数据）
            }

            # 附加区域信息到spec（方便区分同名材料不同区域价格）
            if region:
                record["region"] = region

            records.append(record)

        return records
