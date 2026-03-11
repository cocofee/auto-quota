# -*- coding: utf-8 -*-
"""
PDF信息价解析基类

所有省份的PDF解析配置都继承这个基类。
基类提供：
1. 通用清洗函数（箭头去除、合并单元格填充、价格提取）
2. 自动分类（材料名→大类）
3. 默认的表格解析流程

扩展新省份时，只需继承BasePDFProfile，覆盖需要定制的方法。
"""

import re


# ======== 材料自动分类（全局唯一定义，其他文件统一import这里）========
# 合并了base_profile + shanghai_api的关键词，扩展到21大类
CATEGORY_KEYWORDS = {
    # --- 管道/阀门类 ---
    "管材": ["钢管", "铸铁管", "PPR", "PE管", "PVC管", "铜管", "复合管",
            "塑料管", "排水管", "给水管", "无缝管", "焊接管", "镀锌管",
            "衬塑管", "涂塑管", "不锈钢管", "波纹管", "方管", "消防管",
            "弯管", "球墨铸铁", "聚乙烯管", "缠绕管", "蜂窝管",
            "螺纹管", "短管", "异径管", "热水管", "采暖管", "地板管",
            "刚性管", "燃气管", "电熔管"],
    "管件": ["弯头", "三通", "四通", "法兰", "管件", "接头", "管箍",
            "补芯", "堵头", "活接", "由任", "卡套", "异径直通",
            "对接", "大小头", "管帽", "管卡", "水管斗"],
    "阀门": ["阀门", "闸阀", "截止阀", "球阀", "止回阀", "蝶阀",
            "减压阀", "安全阀", "过滤器", "水表", "流量计",
            "平衡阀", "排气阀", "切断阀", "逆止阀", "二通阀",
            "疏水阀", "柱塞阀", "旋塞阀", "隔断阀", "调节阀"],
    # --- 电气类 ---
    "电线电缆": ["电线", "电缆", "BV线", "BVR", "BVV", "YJV", "RVV", "VV",
               "护套线", "控制电缆", "电力电缆", "光缆", "网线",
               "双绞线", "电话线", "橡皮线", "绝缘线", "铜芯线"],
    "灯具": ["灯", "灯具", "LED", "荧光灯", "吸顶灯", "筒灯", "射灯",
            "应急灯", "路灯", "投光灯", "探照灯", "照明"],
    "开关插座": ["开关", "插座", "面板", "暗装", "明装"],
    "配电箱": ["配电箱", "配电柜", "动力柜", "控制柜", "强电箱",
             "弱电箱", "照明箱", "断路器", "接触器", "桥架", "线槽"],
    # --- 土建材料类 ---
    "水泥": ["水泥", "硅酸盐", "矿渣水泥"],
    "砂石": ["砂", "石子", "碎石", "卵石", "石料", "砾石", "石粉",
            "机制砂", "黄砂", "河砂", "中砂", "细砂", "粗砂", "片石"],
    "混凝土": ["混凝土", "砂浆", "商品砼", "预拌"],
    "钢材": ["钢筋", "钢板", "角钢", "槽钢", "工字钢", "H型钢",
            "圆钢", "扁钢", "钢丝", "铁丝", "焊条", "螺纹钢",
            "盘圆", "线材", "方钢", "钢带", "钢绞线", "钢纤维",
            "热轧", "冷轧", "不锈钢", "合金钢"],
    "木材": ["木材", "胶合板", "木方", "模板", "竹胶板", "方木",
            "松木", "杉木", "木枋", "板材"],
    # --- 装饰/装修类 ---
    "防水材料": ["防水卷材", "防水涂料", "防水", "卷材", "止水带", "防水胶",
              "自粘", "SBS", "APP", "止水条", "防水板"],
    "保温材料": ["保温", "橡塑", "岩棉", "玻璃棉", "聚苯板", "聚苯",
              "挤塑板", "挤塑", "硅酸铝", "保温管壳", "珍珠岩", "发泡"],
    "涂料": ["涂料", "乳胶漆", "油漆", "底漆", "面漆", "防锈漆",
            "环氧", "聚氨酯", "氟碳漆", "腻子"],
    "装饰材料": ["瓷砖", "石材", "大理石", "花岗岩", "地砖", "墙砖",
              "铝板", "铝塑板", "吊顶", "矿棉板", "石膏板", "龙骨",
              "壁纸", "墙布", "踢脚线"],
    "门窗": ["门", "窗", "门窗", "防火门", "防盗门", "推拉门",
            "铝合金门", "铝合金窗", "塑钢门", "塑钢窗", "百叶窗",
            "纱窗", "门锁", "门把手", "合页", "闭门器"],
    "玻璃": ["玻璃", "钢化玻璃", "中空玻璃", "夹胶玻璃", "浮法玻璃"],
    # --- 五金/设备类 ---
    "五金": ["螺栓", "螺母", "螺钉", "垫片", "膨胀螺栓", "铆钉",
            "射钉", "铁钉", "钢钉", "锚栓", "膨胀管"],
    "消防器材": ["消防", "灭火器", "消火栓", "喷淋头", "报警",
              "烟感", "温感", "手报", "声光"],
    "通风设备": ["风机", "风管", "风口", "风阀", "消声器",
              "空调", "新风"],
    # --- 通用兜底 ---
    "建筑材料": ["砖", "砌块", "加气块"],
    "支架": ["支架", "支吊架", "托架", "挂架", "抗震支架", "管道支架"],
    "机械设备": ["电梯", "起重机", "吊车", "压路机", "挖掘机", "装载机",
              "发电机", "水泵", "潜水泵", "离心泵", "增压泵"],
    "园林苗木": ["苗木", "乔木", "灌木", "草坪", "草皮", "花卉",
              "行道树", "绿篱"],
    "油料": ["柴油", "汽油", "沥青", "润滑油", "机油"],
}


def guess_category(name: str, spec: str = "") -> str:
    """
    根据材料名称（+规格）猜大类

    优先匹配名称，名称没命中再看规格。
    全局唯一定义，其他文件统一import这里的版本。
    """
    # 先按名称匹配
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in name:
                return category
    # 名称没命中，再看规格
    if spec:
        for category, keywords in CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in spec:
                    return category
    return ""  # 无法识别时返回空


def clean_price(value) -> float:
    """
    清洗价格值：去除箭头符号、空格、逗号等

    例如：
    "265.49↓" → 265.49
    "1,200.00" → 1200.0
    "——" → 0 （无价格）
    None → 0
    """
    if value is None:
        return 0
    s = str(value).strip()
    if not s:
        return 0
    # 去掉箭头符号（↓↑→←）
    s = re.sub(r'[↓↑→←▲▼△▽]', '', s)
    # 去掉逗号（千分位）
    s = s.replace(',', '')
    # 去掉空格
    s = s.strip()
    # 特殊符号表示无价格
    if s in ('——', '--', '-', '—', '/', ''):
        return 0
    try:
        return float(s)
    except ValueError:
        return 0


def fill_merged_cells(rows: list, col_index: int = 1) -> list:
    """
    填充合并单元格（向上溯源）

    PDF表格中，合并单元格被提取为None。
    比如同一大类下的多种规格，大类名称只出现一次。
    此函数让每行都有完整的名称。

    col_index: 需要填充的列索引（默认1=材料名称列）
    """
    last_value = ""
    for row in rows:
        if col_index >= len(row):
            continue  # 短行跳过，不崩溃
        if row[col_index] is not None and str(row[col_index]).strip():
            last_value = str(row[col_index]).strip()
        else:
            row[col_index] = last_value
    return rows


class BasePDFProfile:
    """
    PDF解析基类

    每个省份继承此类，可覆盖以下方法：
    - classify_page(page_num, page_text) → 返回页面类型
    - get_page_ranges() → 返回各类型的页码范围（可选，优先用此方法）
    - parse_table(table_df, page_type, page_num) → 解析一个表格为记录列表
    """

    # 子类可覆盖的属性
    name = "base"           # 配置名称（用于CLI --profile参数）
    description = "基础配置"  # 描述

    def classify_page(self, page_num: int, page_text: str) -> str:
        """
        识别页面类型

        返回值：
        - 'building'  = 建筑材料（可能有城市拆分列）
        - 'municipal' = 市政材料（统一价格列）
        - 'install'   = 安装材料
        - 'skip'      = 非价格表（政策文件、目录等），跳过

        子类必须覆盖此方法（或覆盖get_page_ranges）。
        """
        return "skip"

    def get_page_ranges(self) -> dict:
        """
        返回各类型的页码范围（1-indexed，和PDF页码一致）

        例如：
        {
            'building': (63, 67),     # 第63-67页是建筑材料
            'municipal': (68, 74),    # 第68-74页是市政材料
        }

        如果子类实现了此方法，classify_page就不会被调用。
        这对于已知固定页码范围的PDF更方便。
        返回空字典表示不使用页码范围，走classify_page逻辑。
        """
        return {}

    def parse_table(self, rows: list, headers: list,
                    page_type: str, page_num: int) -> list:
        """
        解析一个表格为标准记录列表

        参数：
        - rows: 表格数据（list of list，不含表头）
        - headers: 表头（list of str）
        - page_type: 页面类型（building/municipal/install）
        - page_num: 页码

        返回：标准记录列表，每条记录格式：
        {
            "name": "普通硅酸盐水泥",
            "spec": "P·O 42.5R（散）",
            "unit": "t",
            "price": 265.49,     # 含税价
            "city": "银川市",     # 城市（建筑材料有，市政材料可能为空）
            "category": "水泥",
            "tax_included": True,  # 是否含税价
        }

        默认实现：处理统一价格列（市政/安装类）的表格。
        建筑材料的城市拆分由子类覆盖。
        """
        records = []

        # 找关键列的位置
        col_map = self._detect_columns(headers)
        if col_map.get("name_col") is None:
            return records

        name_col = col_map["name_col"]
        spec_col = col_map.get("spec_col")
        unit_col = col_map.get("unit_col")
        price_col = col_map.get("price_col")

        # 填充合并单元格
        rows = fill_merged_cells(rows, name_col)

        def _safe_get(row, idx):
            """安全取列值（短行不崩溃）"""
            if idx is not None and idx < len(row):
                return row[idx]
            return None

        for row in rows:
            name_val = _safe_get(row, name_col)
            name = str(name_val).strip() if name_val else ""
            if not name:
                continue

            spec_val = _safe_get(row, spec_col)
            spec = str(spec_val).strip() if spec_val else ""
            unit_val = _safe_get(row, unit_col)
            unit = str(unit_val).strip() if unit_val else ""

            # 统一价格列
            if price_col is not None:
                price = clean_price(_safe_get(row, price_col))
                if price > 0:
                    records.append({
                        "name": name,
                        "spec": spec,
                        "unit": unit,
                        "price": price,
                        "city": "",
                        "category": guess_category(name),
                        "tax_included": True,
                    })

        return records

    def _detect_columns(self, headers: list) -> dict:
        """
        自动检测表头列映射

        返回：{"name_col": 1, "spec_col": 2, "unit_col": 3, "price_col": 4, ...}
        """
        col_map = {}
        for i, h in enumerate(headers):
            h_clean = str(h).strip() if h else ""
            if "名称" in h_clean and "name_col" not in col_map:
                col_map["name_col"] = i
            elif "规格" in h_clean or "型号" in h_clean:
                col_map["spec_col"] = i
            elif h_clean == "单位" or h_clean == "计量单位":
                col_map["unit_col"] = i
            elif "价格" in h_clean or "单价" in h_clean:
                col_map["price_col"] = i
            elif h_clean == "序号":
                col_map["seq_col"] = i
        return col_map

    def is_header_row(self, row: list) -> bool:
        """判断是否为表头行（跨页时表头会重复出现）"""
        row_text = " ".join(str(c) for c in row if c)
        return "序号" in row_text and "材料名称" in row_text

    def is_data_row(self, row: list) -> bool:
        """判断是否为有效数据行（排除空行、汇总行等）"""
        non_empty = [c for c in row if c is not None and str(c).strip()]
        if len(non_empty) < 2:
            return False
        # 排除"合计"、"小计"等汇总行
        first_cells = " ".join(str(c) for c in row[:3] if c)
        if any(kw in first_cells for kw in ["合计", "小计", "总计", "注：", "备注", "说明"]):
            return False
        return True
