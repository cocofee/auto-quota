# -*- coding: utf-8 -*-
"""
江西PDF信息价解析配置

江西省住房和城乡建设厅每月发布《江西省材料价格参考信息》PDF。
来源：https://zjt.jiangxi.gov.cn/jxszfhcxjst/gqcyc/pc/list.html

特点：
1. 一期PDF约97页，包含多种表格格式
2. 省级汇总表：17列 = 序号+类别+名称+规格+单位+11个设区市价格+税率
3. 省级统一价格表：6-7列 = 序号+名称+规格+单位+统一价+税率(+备注)
4. 各设区市下辖区县表：14-17列 = 序号+名称+规格+单位+N个区县价格+税率
5. 苗木表：9列 = 序号+名称+胸径+地径+高度+冠幅+单位+价格+税率
6. 所有价格均为含税价（标注增值税税率13%或3%/9%）
7. 前4页是封面/说明/目录（跳过）

城市列表（11个设区市）：南昌/九江/上饶/抚州/宜春/吉安/赣州/景德镇/萍乡/新余/鹰潭
"""

import re
from .base_profile import BasePDFProfile, clean_price, fill_merged_cells, guess_category


# 江西11个设区市名称（用于识别城市列）
JIANGXI_CITIES = {
    "南昌", "九江", "上饶", "抚州", "宜春", "吉安", "赣州",
    "景德镇", "萍乡", "新余", "鹰潭",
}

# 江西所有区县名称关键词（用于识别区县分列表头）
# 不需要穷举，只要表头中含有"县""区""市"且不是"增值税"就认为是城市列
SKIP_HEADER_KEYWORDS = ["序号", "序 号", "材料", "规格", "型号", "单位",
                        "单 位", "增值税", "税率", "备注", "信息参考",
                        "苗木", "胸径", "地径", "高度", "冠幅", "类型",
                        "名称"]


class JiangxiProfile(BasePDFProfile):
    """
    江西省材料信息价解析配置

    核心思路：自动检测表头结构，区分以下情况——
    - 表头有城市/区县列 → 拆成N条记录（每个城市一条）
    - 表头只有统一价格列 → 一行一条记录，city填省名
    """
    name = "jiangxi"
    description = "江西省材料价格参考信息（月刊，11设区市+区县分列，含税价）"

    def classify_page(self, page_num: int, page_text: str) -> str:
        """
        识别页面类型

        - 前4页是封面/说明/目录 → skip
        - 包含"材料"+"价格"或"信息参考"的是价格表 → municipal
        - 包含"苗木"的是苗木表 → municipal（统一处理）
        """
        # 前4页固定跳过（封面、编委、说明）
        if page_num <= 4:
            return "skip"

        # 有表格数据的页面都处理
        # 通过表头关键词判断
        if any(kw in page_text for kw in ["材料名称", "材料类型", "苗木名称",
                                           "信息参考价", "规格型号", "规格及型号"]):
            return "municipal"

        # 有数字行的续表也处理
        if re.search(r'^\d+\s', page_text, re.MULTILINE):
            # 排除政策文件等
            if any(kw in page_text for kw in ["文件", "通知", "办法", "条例"]):
                return "skip"
            return "municipal"

        return "skip"

    def parse_table(self, rows: list, headers: list,
                    page_type: str, page_num: int) -> list:
        """
        解析江西表格（自动适配多种列数格式）

        核心逻辑：
        1. 扫描表头，找到 名称列、规格列、单位列、税率列
        2. 剩余列如果是城市/区县名 → 每个城市拆一条记录
        3. 如果有统一价格列（"信息参考价"） → 一行一条记录
        """
        records = []

        if not headers or len(headers) < 4:
            return []

        # 清洗表头（去掉换行和多余空格）
        clean_headers = []
        for h in headers:
            ch = str(h).replace('\n', ' ').strip() if h else ''
            # 去掉中文字符间的多余空格（"共青城 市" → "共青城市"）
            ch = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', ch)
            clean_headers.append(ch)

        # 检测各列角色
        name_col = None       # 材料名称
        type_col = None       # 材料类别（有的表有，有的没有）
        spec_col = None       # 规格型号
        unit_col = None       # 单位
        tax_col = None        # 增值税税率
        note_col = None       # 备注
        price_col = None      # 统一价格列（"信息参考价"）
        city_cols = []        # 城市/区县列 [(col_idx, city_name), ...]

        for i, h in enumerate(clean_headers):
            h_lower = h.lower()
            if "材料名称" in h or "苗木名称" in h:
                name_col = i
            elif "材料类型" in h or "材料类别" in h:
                type_col = i
            elif "规格" in h or "型号" in h:
                # "规格型号"或"型号及规格"或"规格及型号"都算
                if spec_col is None:
                    spec_col = i
            elif h in ("单位", "单 位", "计量单位"):
                unit_col = i
            elif "增值税" in h or "税率" in h:
                tax_col = i
            elif h == "备注" or h == "备 注":
                note_col = i
            elif "信息参考价" in h or "信息参考" in h:
                price_col = i
            elif h in ("序号", "序 号"):
                pass  # 序号列，跳过
            elif "胸径" in h or "地径" in h or "高度" in h or "冠幅" in h:
                pass  # 苗木规格列，作为规格信息但不作为价格列
            else:
                # 剩余列可能是城市/区县名
                # 排除空列头和非地名
                if h and not any(kw in h for kw in SKIP_HEADER_KEYWORDS):
                    city_cols.append((i, h))

        # 如果没找到名称列，尝试宽松匹配
        if name_col is None:
            for i, h in enumerate(clean_headers):
                if "名称" in h:
                    name_col = i
                    break

        if name_col is None:
            return []

        # 苗木表特殊处理：把胸径/地径/高度/冠幅拼接成规格
        is_nursery = any("苗木" in h or "胸径" in h for h in clean_headers)
        nursery_cols = []  # 苗木规格列
        if is_nursery:
            for i, h in enumerate(clean_headers):
                if any(kw in h for kw in ["胸径", "地径", "高度", "冠幅"]):
                    nursery_cols.append((i, h.replace('（', '(').replace('）', ')')))

        # 填充合并单元格（名称列和类别列）
        fill_merged_cells(rows, name_col)
        if type_col is not None:
            fill_merged_cells(rows, type_col)

        for row in rows:
            if len(row) < 4:
                continue

            # 跳过表头行（跨页重复）
            row_text = ' '.join(str(c) for c in row[:3] if c)
            if '序号' in row_text or '序 号' in row_text:
                continue
            if '材料名称' in row_text or '苗木名称' in row_text:
                continue

            # 获取名称
            name = str(row[name_col]).strip() if name_col < len(row) and row[name_col] else ""
            if not name:
                continue

            # 跳过汇总行
            if any(kw in name for kw in ["合计", "小计", "总计", "说明", "注："]):
                continue

            # 如果有类别列，追加到名称前面（方便后续识别）
            mat_type = ""
            if type_col is not None and type_col < len(row) and row[type_col]:
                mat_type = str(row[type_col]).strip()

            # 获取规格
            if is_nursery and nursery_cols:
                # 苗木：拼接胸径/地径/高度/冠幅
                spec_parts = []
                for ci, ch in nursery_cols:
                    if ci < len(row) and row[ci] and str(row[ci]).strip() not in ('/', ''):
                        spec_parts.append(f"{ch}{str(row[ci]).strip()}")
                spec = ' '.join(spec_parts)
            elif spec_col is not None and spec_col < len(row) and row[spec_col]:
                spec = str(row[spec_col]).strip()
            else:
                spec = ""

            # 清洗规格中的换行
            spec = spec.replace('\n', ' ').strip()

            # 获取单位
            unit = ""
            if unit_col is not None and unit_col < len(row) and row[unit_col]:
                unit = str(row[unit_col]).strip()

            # 获取税率
            tax_rate = 0.13  # 默认13%
            if tax_col is not None and tax_col < len(row) and row[tax_col]:
                tax_str = str(row[tax_col]).strip().replace('%', '').replace('％', '')
                try:
                    tax_val = float(tax_str)
                    if tax_val > 1:  # "13" → 0.13
                        tax_rate = tax_val / 100
                    elif 0 < tax_val <= 1:  # "0.13"
                        tax_rate = tax_val
                except ValueError:
                    pass

            # 构建完整名称（类别+名称）
            full_name = name
            if mat_type and mat_type != name:
                # 如果类别列和名称不同，且类别不在名称中
                if mat_type not in name:
                    full_name = f"{mat_type} {name}"

            # 分类
            category = guess_category(full_name, spec)

            # === 根据是否有城市列，决定拆分策略 ===

            if city_cols:
                # 多城市分列：每个有价格的城市拆一条
                for ci, city_name in city_cols:
                    if ci >= len(row):
                        continue
                    price = clean_price(row[ci])
                    if price <= 0:
                        continue

                    # 处理"150/189/216"这种多价格（取第一个）
                    price_str = str(row[ci]).strip() if row[ci] else ""
                    if '/' in price_str and price <= 0:
                        parts = price_str.split('/')
                        for p in parts:
                            pv = clean_price(p)
                            if pv > 0:
                                price = pv
                                break

                    records.append({
                        "name": full_name,
                        "spec": spec,
                        "unit": unit,
                        "price": price,
                        "city": city_name.strip(),
                        "category": category,
                        "tax_included": True,
                        "tax_rate": tax_rate,
                    })

            elif price_col is not None:
                # 统一价格列
                if price_col >= len(row):
                    continue
                price_raw = str(row[price_col]).strip() if row[price_col] else ""

                # 处理"150/189/216"多规格价格
                if '/' in price_raw:
                    prices = [clean_price(p) for p in price_raw.split('/')]
                    prices = [p for p in prices if p > 0]
                    if prices:
                        # 取中间值
                        price = prices[len(prices) // 2]
                    else:
                        continue
                else:
                    price = clean_price(price_raw)

                if price <= 0:
                    continue

                records.append({
                    "name": full_name,
                    "spec": spec,
                    "unit": unit,
                    "price": price,
                    "city": "",  # 统一价格不区分城市
                    "category": category,
                    "tax_included": True,
                    "tax_rate": tax_rate,
                })

        return records
