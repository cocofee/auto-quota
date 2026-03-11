# -*- coding: utf-8 -*-
"""
宁夏PDF信息价解析配置

宁夏有两种PDF：
1. 《宁夏工程造价》（双月刊）— 建筑材料(城市拆分) + 市政材料(综合价格)
2. 《安装工程材料价格信息》（年刊）— 统一6列格式，不含税价格

特点：
- 建筑材料表头有多个城市列（银川/灵武/大武口/平罗等），一行拆N条记录
- 市政材料只有"综合价格"一列
- 安装材料价格是不含税的，需要特殊处理
- 价格数字后面可能有↓↑箭头符号
"""

from .base_profile import BasePDFProfile, clean_price, fill_merged_cells, guess_category


class NingxiaProfile(BasePDFProfile):
    """
    宁夏《工程造价》双月刊解析配置

    包含建筑材料（城市拆分）和市政材料（综合价格）。
    """
    name = "ningxia"
    description = "宁夏《工程造价》双月刊（建筑+市政材料信息价）"

    def get_page_ranges(self) -> dict:
        """
        宁夏第6期的页码范围（1-indexed）

        注意：不同期次的页码可能不同。
        返回空字典时会走classify_page自动识别。
        """
        # 不硬编码页码，走自动识别
        return {}

    def classify_page(self, page_num: int, page_text: str) -> str:
        """
        根据页面文字内容自动识别类型

        宁夏PDF特征：
        - 建筑材料页：表头有城市名（银川市、大武口区、沙坡头区等）
        - 市政材料页：有"市政工程"、"综合价格"
        - 其他页：政策文件、目录等跳过
        """

        # 先排除"采集编制说明"页（有税率转换表但不是价格表）
        if "采集编制说明" in page_text or "不含增值税价格" in page_text:
            return "skip"

        # 建筑材料：表头有多个城市列名
        # 只匹配"xx市"、"xx区"、"xx县"这样的行政区划名称
        nx_city_patterns = [
            "银川市", "灵武市", "青铜峡市",
            "大武口区", "惠农区", "利通区", "沙坡头区", "原州区", "红寺堡区",
            "平罗县", "盐池县", "同心县", "中宁县", "海原县",
            "隆德县", "西吉县", "泾源县", "彭阳县",
        ]
        # 用完整地名匹配（避免"银北矿区"被"银"误匹配）
        city_count = sum(1 for c in nx_city_patterns if c in page_text)
        if city_count >= 2 and ("材料名称" in page_text or "规格型号" in page_text):
            return "building"

        # 市政/安装统一价格：有"综合价格"或标准表头
        if ("综合价格" in page_text or "材料名称" in page_text) and \
           ("规格型号" in page_text or "单位" in page_text):
            # 排除没有价格数据的页面（如目录页）
            if "综合价格" in page_text or "单价" in page_text or "价格" in page_text:
                return "municipal"

        return "skip"

    def parse_table(self, rows: list, headers: list,
                    page_type: str, page_num: int) -> list:
        """
        解析宁夏表格

        建筑材料：城市拆分（一行→N条记录）
        市政材料：综合价格（一行→1条记录）
        """
        if page_type == "building":
            return self._parse_building(rows, headers)
        elif page_type == "municipal":
            return self._parse_municipal(rows, headers)
        else:
            # 其他类型走基类默认逻辑
            return super().parse_table(rows, headers, page_type, page_num)

    def _parse_building(self, rows: list, headers: list) -> list:
        """
        解析建筑材料表格（城市拆分）

        表头格式：序号 | 材料名称 | 规格型号 | 单位 | 银川市 | 灵武市 | ...
        一行数据拆成N条记录（每个城市一条）
        """
        records = []

        # 找固定列
        name_col = None
        spec_col = None
        unit_col = None
        city_cols = {}  # {列索引: 城市名}

        for i, h in enumerate(headers):
            h_str = str(h).strip() if h else ""
            if h_str == "序号":
                pass  # 序号列不用
            elif "名称" in h_str:
                name_col = i
            elif "规格" in h_str or "型号" in h_str:
                spec_col = i
            elif h_str == "单位":
                unit_col = i
            elif h_str and h_str not in ("序号", "单位") and "名称" not in h_str and "规格" not in h_str and "型号" not in h_str and "价格" not in h_str and "单价" not in h_str:
                # 剩余的列都是城市列（排除"综合价格"等非城市列）
                # 清洗城市名
                city_name = h_str.replace("单位", "").replace("元", "").replace("￥", "").replace("：", "").replace(":", "").replace(".", "").replace("。", "").strip()
                # 去掉特殊unicode字符
                city_name = ''.join(c for c in city_name if ord(c) < 0x10000 or c.isalnum())
                city_name = city_name.strip()
                if city_name:
                    city_cols[i] = city_name

        if name_col is None:
            return records

        # 安全取列值（短行不崩溃）
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

            spec_val = _safe(row, spec_col)
            spec = str(spec_val).strip() if spec_val else ""
            unit_val = _safe(row, unit_col)
            unit = str(unit_val).strip() if unit_val else ""

            # 跳过"合计"等非数据行
            if any(kw in name for kw in ["合计", "小计", "总计", "注：", "备注"]):
                continue

            # 每个城市一条记录
            for col_idx, city_name in city_cols.items():
                price = clean_price(row[col_idx]) if col_idx < len(row) else 0
                if price > 0:
                    records.append({
                        "name": name,
                        "spec": spec,
                        "unit": unit,
                        "price": price,
                        "city": city_name,
                        "category": guess_category(name),
                        "tax_included": False,  # 宁夏信息价"材料价格均为不含税价格"
                    })

        return records

    def _parse_municipal(self, rows: list, headers: list) -> list:
        """
        解析市政材料表格（综合价格）

        表头格式：序号 | 材料名称 | 规格型号 | 单位 | 综合价格
        可能还有"绿色建材价格"列（一星/二星/三星），只取第一个价格列。
        """
        records = []

        # 找列（只取第一个匹配的，避免"绿色建材价格"覆盖"综合价格"）
        name_col = None
        spec_col = None
        unit_col = None
        price_col = None

        for i, h in enumerate(headers):
            h_str = str(h).strip() if h else ""
            if "名称" in h_str and name_col is None:
                name_col = i
            elif ("规格" in h_str or "型号" in h_str) and spec_col is None:
                spec_col = i
            elif h_str == "单位" and unit_col is None:
                unit_col = i
            elif ("价格" in h_str or "单价" in h_str) and price_col is None:
                price_col = i

        if name_col is None or price_col is None:
            return records

        # 安全取列值
        def _safe(row, idx):
            if idx is not None and idx < len(row):
                return row[idx]
            return None

        # 填充合并单元格
        rows = fill_merged_cells(rows, name_col)

        for row in rows:
            name_val = _safe(row, name_col)
            name = str(name_val).strip() if name_val else ""
            if not name:
                continue

            spec_val = _safe(row, spec_col)
            spec = str(spec_val).strip() if spec_val else ""
            unit_val = _safe(row, unit_col)
            unit = str(unit_val).strip() if unit_val else ""
            price = clean_price(_safe(row, price_col))

            if any(kw in name for kw in ["合计", "小计", "总计", "注：", "备注"]):
                continue

            if price > 0:
                records.append({
                    "name": name,
                    "spec": spec,
                    "unit": unit,
                    "price": price,
                    "city": "",  # 市政材料不分城市
                    "category": guess_category(name),
                    "tax_included": False,  # 宁夏信息价"材料价格均为不含税价格"
                })

        return records


class NingxiaInstallProfile(BasePDFProfile):
    """
    宁夏《安装工程材料价格信息》年刊解析配置

    特点：
    - 统一6列格式：序号|材料名称|规格型号|单位|不含税价格（元）|备注
    - 价格是【不含税】的（和工程造价的含税价不同！）
    - 有大类标题行（如"1.给水用外层熔接型铝塑复合管材、管件"）
    - 全部页面都是价格表（第9页开始到最后）
    """
    name = "ningxia_install"
    description = "宁夏《安装工程材料价格信息》年刊（不含税价格）"

    # 安装材料从第9页开始（1-indexed）
    start_page = 9

    def classify_page(self, page_num: int, page_text: str) -> str:
        """安装材料PDF：第9页开始都是价格表"""
        if page_num >= self.start_page:
            # 检查是否有表格特征
            if "材料名称" in page_text or "规格型号" in page_text or "不含税" in page_text:
                return "install"
        return "skip"

    def parse_table(self, rows: list, headers: list,
                    page_type: str, page_num: int) -> list:
        """
        解析安装材料表格

        关键区别：价格是不含税的！
        大类标题行（如"1.给水用..."）在序号列，需要识别并用作分类。
        """
        records = []

        # 表头可能直接在columns里（"序号", "材料名称", ...）
        # 也可能在第一行数据里
        name_col = None
        spec_col = None
        unit_col = None
        price_col = None

        for i, h in enumerate(headers):
            h_str = str(h).strip() if h else ""
            if "名称" in h_str and name_col is None:
                name_col = i
            elif ("规格" in h_str or "型号" in h_str) and spec_col is None:
                spec_col = i
            elif h_str == "单位" and unit_col is None:
                unit_col = i
            elif ("价格" in h_str or "单价" in h_str) and price_col is None:
                price_col = i

        if name_col is None:
            return records

        if price_col is None:
            # 价格列未识别到时输出警告，方便排查
            print(f"  警告: 安装材料表格未找到价格列，表头={headers}")
            return records

        # 安全取列值
        def _safe(row, idx):
            if idx is not None and idx < len(row):
                return row[idx]
            return None

        # 当前大类（从标题行提取）
        current_subcategory = ""

        # 填充合并单元格
        rows = fill_merged_cells(rows, name_col)

        for row in rows:
            # 检查是否为大类标题行
            # 特征：第一列是 "1.给水用..." 这种带序号的大类
            first_cell = str(_safe(row, 0) or "").strip()
            if first_cell and '.' in first_cell and len(first_cell) > 5:
                # 可能是大类标题（如 "1.给水用外层熔接型铝塑复合管材、管件"）
                # 检查其余列是否都是None
                other_cols = [_safe(row, j) for j in range(1, len(row))]
                if all(c is None or str(c).strip() == '' for c in other_cols):
                    # 提取大类名称（去掉序号前缀）
                    current_subcategory = first_cell.split('.', 1)[-1].strip() if '.' in first_cell else first_cell
                    continue

            name_val = _safe(row, name_col)
            name = str(name_val).strip() if name_val else ""
            if not name:
                continue

            spec_val = _safe(row, spec_col)
            spec = str(spec_val).strip() if spec_val else ""
            unit_val = _safe(row, unit_col)
            unit = str(unit_val).strip() if unit_val else ""
            price = clean_price(_safe(row, price_col))

            if any(kw in name for kw in ["合计", "小计", "总计", "注：", "备注", "说明"]):
                continue

            if price > 0:
                records.append({
                    "name": name,
                    "spec": spec,
                    "unit": unit,
                    "price": price,
                    "city": "",
                    "category": guess_category(name),
                    "subcategory": current_subcategory,
                    "tax_included": False,  # 安装材料是不含税价格！
                })

        return records
