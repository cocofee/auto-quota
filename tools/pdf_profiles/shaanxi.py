# -*- coding: utf-8 -*-
"""
陕西PDF信息价解析配置

陕西省建设工程造价服务中心每月发布《陕西工程造价信息》材料信息价PDF。

特点：
1. 标准6列格式：材料编码 | 材料名称 | 规格型号 | 单位 | 除税价格(元) | 含税价格(元)
2. 同时提供含税和除税价格
3. 前7页是封面/说明/目录（跳过），第8页开始是价格表
4. 后半部分（约第70页起）是企业报价，格式不同（跳过）
5. 适用范围：西安市7个行政区域（新城/碑林/莲湖/灞桥/未央/雁塔/长安）
6. 每期约2000+条材料价格
"""

import re
from .base_profile import BasePDFProfile, clean_price, fill_merged_cells, guess_category


class ShaanxiProfile(BasePDFProfile):
    """
    陕西省材料信息价解析配置

    标准6列，含税+除税双价格，提取简单。
    """
    name = "shaanxi"
    description = "陕西省材料信息价（月刊，含税+除税双价格，适用西安7区）"

    def classify_page(self, page_num: int, page_text: str) -> str:
        """
        识别页面类型

        - 前7页是封面/说明/目录 → skip
        - 包含"材料编码"+"除税价格"的是标准价格表 → municipal
        - 企业报价页（表头不同）→ skip
        """
        # 前7页固定跳过（封面、说明、目录）
        if page_num <= 7:
            return "skip"

        # 标准价格表特征：有"材料编码"和"除税价格"
        if "材料编码" in page_text and "除税价格" in page_text:
            return "municipal"

        # 续表页：有材料编码格式的数字（6-9位纯数字开头的行）
        if re.search(r'\d{6,9}\s', page_text):
            # 检查是否企业报价页（企业报价通常有公司名称）
            if any(kw in page_text for kw in ["有限公司", "股份", "集团"]):
                return "skip"
            return "municipal"

        return "skip"

    def parse_table(self, rows: list, headers: list,
                    page_type: str, page_num: int) -> list:
        """
        解析陕西6列表格

        表头：材料编码 | 材料名称 | 规格型号 | 单位 | 除税价格(元) | 含税价格(元)
        """
        records = []

        # 确认是6列表格
        if not headers or len(headers) < 6:
            return []

        # 检查表头确认是标准格式（排除企业报价等非标准表）
        header_text = " ".join(str(h) for h in headers if h)
        if "材料编码" not in header_text:
            return []

        # 列位置（固定6列）
        code_col = 0   # 材料编码
        name_col = 1   # 材料名称
        spec_col = 2   # 规格型号
        unit_col = 3   # 单位
        excl_col = 4   # 除税价格
        incl_col = 5   # 含税价格

        # 填充合并单元格（同名材料不同规格时，名称只出现一次）
        fill_merged_cells(rows, name_col)

        for row in rows:
            if len(row) < 6:
                continue

            # 跳过表头行
            code = str(row[code_col]).strip() if row[code_col] else ""
            if code == "材料编码" or "编码" in code:
                continue

            # 材料编码必须是数字（6-9位），否则不是数据行
            if not code or not re.match(r'^\d{6,9}$', code):
                continue

            name = str(row[name_col]).strip() if row[name_col] else ""
            spec = str(row[spec_col]).strip() if row[spec_col] else ""
            unit = str(row[unit_col]).strip() if row[unit_col] else ""

            if not name:
                continue

            # 跳过汇总行
            if any(kw in name for kw in ["合计", "小计", "总计", "说明"]):
                continue

            # 解析价格（含税和除税）
            price_excl = clean_price(row[excl_col])
            price_incl = clean_price(row[incl_col])

            # 至少要有一个价格
            if price_incl <= 0 and price_excl <= 0:
                continue

            # 如果只有除税价，按13%算含税价
            if price_incl <= 0 and price_excl > 0:
                price_incl = round(price_excl * 1.13, 2)

            # 如果只有含税价，按13%算除税价
            if price_excl <= 0 and price_incl > 0:
                price_excl = round(price_incl / 1.13, 2)

            # 计算实际税率
            if price_excl > 0:
                tax_rate = round((price_incl / price_excl) - 1, 4)
                if tax_rate < 0 or tax_rate > 0.25:
                    tax_rate = 0.13
            else:
                tax_rate = 0.13

            records.append({
                "name": name,
                "spec": spec,
                "unit": unit,
                "price": price_incl,          # import_price_pdf期望含税价
                "price_excl_tax": price_excl,  # 额外记录除税价
                "tax_rate": tax_rate,
                "city": "西安",
                "category": guess_category(name, spec),
                "tax_included": True,
            })

        return records
