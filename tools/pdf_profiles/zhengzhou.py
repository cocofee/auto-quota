# -*- coding: utf-8 -*-
"""
郑州PDF信息价解析配置

郑州市城乡建设局发布两种信息价：
1. 月刊《主要材料价格信息》— 统一6列格式，含税+不含税双价格
2. 季刊《材料价格信息》— 更全面的材料列表，同样6列格式

特点：
- 格式极其标准：序号|材料名称|规格型号|单位|含税单价|不含税单价
- 全部页面都是价格表（无封面、无目录、无政策文件）
- 每页约40行数据，跨页续表时表头重复出现
- 含税和不含税价格同时提供
"""

from .base_profile import BasePDFProfile, clean_price, fill_merged_cells, guess_category


class ZhengzhouProfile(BasePDFProfile):
    """
    郑州月刊+季刊解析配置

    月刊和季刊格式完全一样，统一6列，用同一个profile处理。
    """
    name = "zhengzhou"
    description = "郑州市建设工程材料价格信息（月刊+季刊，含税/不含税双价格）"

    def classify_page(self, page_num: int, page_text: str) -> str:
        """
        郑州PDF特征：所有页面都是价格表

        识别依据：有"材料名称"或"规格型号"或"含税单价"关键词
        """
        # 有表格特征的才处理
        if "材料名称" in page_text or "规格型号" in page_text or "含税单价" in page_text:
            return "municipal"  # 复用municipal类型（统一价格列）
        # 可能是纯数据续表页（没有表头但有价格数据）
        if "不含税单价" in page_text or "单位" in page_text:
            return "municipal"
        return "skip"

    def parse_table(self, rows: list, headers: list,
                    page_type: str, page_num: int) -> list:
        """
        解析郑州统一6列表格

        表头格式：序号 | 材料名称 | 规格型号 | 单位 | 含税单价 | 不含税单价
        两个价格都提取，以含税价为主（写入price_incl_tax），同时记录不含税价。
        """
        records = []

        # 找列位置
        name_col = None
        spec_col = None
        unit_col = None
        price_incl_col = None   # 含税单价列
        price_excl_col = None   # 不含税单价列

        for i, h in enumerate(headers):
            h_str = str(h).strip().replace('\n', '') if h else ""
            if "材料名称" in h_str or "名称" in h_str:
                if name_col is None:
                    name_col = i
            elif "规格" in h_str or "型号" in h_str:
                if spec_col is None:
                    spec_col = i
            elif h_str == "单位":
                if unit_col is None:
                    unit_col = i
            elif "含税" in h_str and "不含税" not in h_str:
                # "含税单价"列（排除"不含税"）
                if price_incl_col is None:
                    price_incl_col = i
            elif "不含税" in h_str:
                # "不含税单价"列
                if price_excl_col is None:
                    price_excl_col = i

        if name_col is None:
            return records

        # 安全取列值
        def _safe(row, idx):
            if idx is not None and idx < len(row):
                return row[idx]
            return None

        # 填充合并单元格（名称列）
        rows = fill_merged_cells(rows, name_col)

        for row in rows:
            name_val = _safe(row, name_col)
            name = str(name_val).strip() if name_val else ""
            if not name:
                continue

            # 跳过汇总行
            if any(kw in name for kw in ["合计", "小计", "总计", "注：", "备注", "说明"]):
                continue

            spec_val = _safe(row, spec_col)
            spec = str(spec_val).strip() if spec_val else ""
            unit_val = _safe(row, unit_col)
            unit = str(unit_val).strip() if unit_val else ""

            # 优先取含税价
            price_incl = clean_price(_safe(row, price_incl_col))
            price_excl = clean_price(_safe(row, price_excl_col))

            # 至少要有一个价格
            if price_incl <= 0 and price_excl <= 0:
                continue

            records.append({
                "name": name,
                "spec": spec,
                "unit": unit,
                "price": price_incl if price_incl > 0 else price_excl,
                "city": "郑州",
                "category": guess_category(name),
                "tax_included": price_incl > 0,  # 有含税价就标记为含税
            })

        return records
