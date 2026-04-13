"""
专业分类模块
功能：
1. 定义安装工程12大册的映射关系
2. 根据清单名称+特征描述判断属于哪个专业（册）
3. 提供跨专业借用优先级
4. 从定额编号提取册号
5. 从分部标题映射到册号
6. 从清单编码（GB 50500）辅助判断专业

所有需要"区分专业"的地方都调用这个模块，保证全系统用同一套标准。
"""

import json
import re
from pathlib import Path
from loguru import logger

from src.context_builder import detect_system_hint, normalize_system_hint


# ================================================================
# 12大册定义（北京2024安装工程定额）+ 土建等其他专业
# ================================================================

# 每册的信息：名称 + 用于分类的关键词
BOOKS = {
    # ---- 安装工程（C1~C12）----
    "C1": {
        "name": "机械设备安装",
        "keywords": [
            "起重", "起重机", "行车", "天车", "电梯", "扶梯",
            "风机", "轴流风机", "离心风机",
            "泵", "水泵", "潜水泵", "离心泵", "排污泵",
            "压缩机", "空压机",
            "输送", "皮带机", "输送机", "传送带",
        ],
    },
    "C2": {
        "name": "热力设备安装",
        "keywords": [
            "锅炉", "燃油锅炉", "燃气锅炉", "生物质锅炉",
            "水处理设备", "软化水", "除氧器",
            "脱硝", "脱硫",
        ],
    },
    "C3": {
        "name": "静置设备与工艺金属结构",
        "keywords": [
            "储罐", "油罐", "水罐", "气罐",
            "容器", "压力容器", "反应釜",
            "塔器", "塔", "撬块",
            "金属结构", "钢结构", "钢平台", "钢梯",
        ],
    },
    "C4": {
        "name": "电气设备安装",
        "keywords": [
            "变压器", "配电箱", "配电柜", "开关柜",
            "电缆", "电力电缆", "控制电缆",
            "母线", "母线槽", "桥架", "电缆桥架",
            "开关", "插座", "断路器",
            "灯", "灯具", "照明", "应急灯",
            "防雷", "接地", "避雷", "接地极",
            "配管", "配线", "线管", "穿线管", "线槽",
            "电机", "电动机",
        ],
    },
    "C5": {
        "name": "建筑智能化",
        "keywords": [
            "网络", "交换机", "路由器", "服务器机柜",
            "综合布线", "网线", "光纤跳线", "信息插座",
            "监控", "摄像头", "摄像机", "硬盘录像机",
            "电视", "有线电视", "卫星接收",
            "音频", "视频", "音响", "功放", "投影",
            "安防", "门禁", "对讲", "巡更",
            "智能家居",
        ],
    },
    "C6": {
        "name": "自动化控制仪表",
        "keywords": [
            "仪表", "传感器", "变送器",
            "流量计", "温度计", "压力表", "液位计",
            "热电偶", "热电阻",
            "调节阀", "电磁阀",
            "PLC", "DCS", "控制柜",
        ],
    },
    "C7": {
        "name": "通风空调",
        "keywords": [
            "风管", "通风管道", "排风管",
            "空调", "风机盘管", "盘管", "空调机组", "冷水机组",
            "新风", "新风机", "全热交换",
            "风口", "百叶风口", "散流器",
            "风阀", "防火阀", "调节阀",
            "排烟", "排烟风机", "排烟管道",
            "风帽", "消声器",
            "导流叶片",  # 通风管道弯头专属配件
        ],
    },
    "C8": {
        "name": "工业管道",
        "keywords": [
            "工业管道",
            "高压管道", "高压管", "中压管道", "中压管",
            "法兰", "法兰连接", "法兰安装",
            "无缝钢管", "合金钢管", "不锈钢管道",
            "焊接钢管",
            "管件", "弯头", "三通", "异径管",
        ],
    },
    "C9": {
        "name": "消防",
        "keywords": [
            "消防", "消火栓", "消防栓",
            "喷淋", "喷头", "湿式报警阀",
            "灭火", "灭火器", "气体灭火", "泡沫灭火",
            "烟感", "温感", "火灾报警", "手动报警",
            "应急照明", "疏散指示",
        ],
    },
    "C10": {
        "name": "给排水采暖燃气",
        "keywords": [
            "给水", "给水管", "自来水管",
            "排水", "排水管", "污水管", "雨水管",
            "热水", "热水管", "热水器",
            "采暖", "暖气", "散热器", "地暖", "地热",
            "燃气", "燃气管", "燃气表", "天然气管",
            "卫生器具", "洗手盆", "洗脸盆", "坐便器", "蹲便器",
            "小便器", "浴缸", "淋浴", "地漏", "存水弯",
            "水表", "水龙头", "角阀",
            "管道安装", "镀锌钢管", "PPR管", "PE管", "PVC管",
            "管卡", "支架", "套管", "防水套管",
            "水压试验", "冲洗", "消毒",
        ],
    },
    "C11": {
        "name": "通信设备",
        "keywords": [
            "通信", "通信管道", "通信电缆",
            "光缆", "光纤", "光纤熔接",
            "电话", "电话线",
            "通信设备", "基站",
        ],
    },
    "C12": {
        "name": "刷油防腐蚀绝热",
        "keywords": [
            "刷油", "油漆", "涂料",
            "防腐", "防腐蚀", "防锈",
            "保温", "绝热", "保温材料",
            "岩棉", "橡塑", "聚氨酯",
            "补口", "补伤",
        ],
    },
    # ---- 土建工程（北京2024用A前缀，其他省份可能不同）----
    "A": {
        "name": "房屋建筑与装饰工程",
        "keywords": [
            "土方", "挖土", "回填", "碾压", "夯实",
            "地基", "换填", "搅拌桩", "灌注桩", "预制桩", "桩基",
            "砌筑", "砖墙", "砌块", "灰缝",
            "混凝土", "钢筋", "模板", "垫层", "基础", "梁", "柱", "板", "墙体",
            "钢结构", "钢柱", "钢梁", "钢构件",
            "木结构", "木门", "木窗", "木龙骨",
            "门窗", "铝合金门窗", "塑钢窗", "防火门", "卷帘门",
            "屋面", "防水卷材", "SBS", "找平层", "保护层",
            "楼地面", "地砖", "地面砖", "水磨石", "自流平",
            "墙面", "抹灰", "面砖", "石材", "幕墙", "隔断", "隔墙",
            "天棚", "吊顶", "石膏板", "矿棉板",
            "油漆", "涂料", "裱糊", "乳胶漆",
            "脚手架", "模板支架",
        ],
    },
    # ---- 市政工程 ----
    "D": {
        "name": "市政工程",
        "keywords": [
            "检查井", "雨水口", "雨水井", "污水井", "沉泥井",
            "路面", "路基", "路床", "基层", "底基层",
            "沥青", "沥青混凝土",
            "侧石", "路缘石", "道牙", "缘石",
            "排水沟", "边沟", "截水沟",
            "市政管道", "市政排水", "市政给水",
            "顶管", "拖拉管", "定向钻",
            "路灯基础", "井盖", "井座",
        ],
    },
    # ---- 其他及附属工程（C13）----
    # 2025版多省新增，含支架/套管/基础型钢/管道包封等公共项目
    # 关键词只放各省通用的长词，避免"支架""套管"等泛词和C10冲突
    "C13": {
        "name": "其他及附属工程",
        "keywords": [
            "抗震支架", "装配式支架",
            "基础型钢", "预埋铁件",
            "管道包封", "排管包封",
            "孔洞封堵",
        ],
    },
    # ---- 园林绿化工程 ----
    "E": {
        "name": "园林绿化工程",
        "keywords": [
            "乔木", "灌木", "草坪", "花卉", "地被",
            "种植", "栽植", "移植", "假植",
            "园路", "汀步", "景观铺装",
            "花架", "廊架", "亭", "景墙",
            "喷泉", "假山", "叠石", "景观水池",
        ],
    },
}

# ================================================================
# 册号 → 定额库类型映射（辅助定额库路由用）
# ================================================================
# install=安装, civil=土建, municipal=市政, landscape=园林
BOOK_TO_DB_TYPE = {
    **{f"C{i}": "install" for i in range(1, 14)},  # C1~C13（含其他及附属）
    "A": "civil",
    "D": "municipal",
    "E": "landscape",
}


def province_uses_standard_route_books(province_name: str | None) -> bool:
    province_name = str(province_name or "").strip()
    db_type = detect_db_type(province_name)
    if db_type in {"install", "civil", "municipal", "landscape", "comprehensive"}:
        return True
    custom_tokens = ("电力", "火电", "风电", "配网", "电网", "技改", "序列")
    if any(token in province_name for token in custom_tokens):
        return False
    return True


def get_province_route_scope(province_name: str | None) -> list[str]:
    """Return the broad route books compatible with the current province db type."""
    db_type = detect_db_type(province_name or "")
    if db_type == "install":
        return [book for book, kind in BOOK_TO_DB_TYPE.items() if kind == "install"]
    if db_type == "civil":
        return ["A"]
    if db_type == "municipal":
        return ["D"]
    if db_type == "landscape":
        return ["E"]
    return []


def book_matches_province_scope(book: str | None, province_name: str | None) -> bool:
    book = str(book or "").strip()
    if not book:
        return False
    allowed = get_province_route_scope(province_name)
    if not allowed:
        return True
    return book in allowed


def _filter_routing_by_province_scope(
    scores: dict[str, float],
    routing_evidence: dict[str, list[str]],
    hard_constraints: list[str],
    province_name: str | None,
) -> tuple[dict[str, float], dict[str, list[str]], list[str]]:
    if not province_uses_standard_route_books(province_name):
        filtered_scores = {
            book: score for book, score in scores.items()
            if book not in BOOK_TO_DB_TYPE
        }
        filtered_evidence = {
            book: reasons for book, reasons in routing_evidence.items()
            if book not in BOOK_TO_DB_TYPE
        }
        filtered_constraints = [
            book for book in hard_constraints
            if book not in BOOK_TO_DB_TYPE
        ]
        return filtered_scores, filtered_evidence, filtered_constraints

    allowed = set(get_province_route_scope(province_name))
    if not allowed:
        return scores, routing_evidence, hard_constraints

    filtered_scores = {
        book: score for book, score in scores.items()
        if book in allowed
    }
    filtered_evidence = {
        book: reasons for book, reasons in routing_evidence.items()
        if book in allowed
    }
    filtered_constraints = [
        book for book in hard_constraints
        if book in allowed
    ]
    return filtered_scores, filtered_evidence, filtered_constraints


def detect_db_type(province_name: str) -> str:
    """从定额库名称检测其类型

    用于辅助定额库路由：判断一个定额库属于安装/土建/市政/园林哪类。
    综合类定额库（如北京2024建设工程标准）包含全部专业，不需要辅助路由。

    返回: "install" / "civil" / "municipal" / "landscape" /
          "comprehensive"(综合) / "other"(其他)
    """
    if not province_name:
        return "other"
    # 安装
    if "安装" in province_name:
        return "install"
    # 市政（必须在"土建"之前检测，避免"市政"被"建设"误匹配）
    if "市政" in province_name:
        return "municipal"
    # 园林/绿化
    if "园林" in province_name or "绿化" in province_name:
        return "landscape"
    # 土建/房屋建筑/装饰
    if any(kw in province_name for kw in ["房屋建筑", "房屋修", "装饰"]):
        return "civil"
    # 综合类定额库（包含安装+土建+市政等全部专业，不需要辅助路由）
    # 注意：必须排除"海绵城市"等特殊定额库（虽然含"建设工程"但不是综合类）
    if "消耗量标准" in province_name:
        return "comprehensive"
    if "建设工程" in province_name and "海绵" not in province_name:
        return "comprehensive"
    return "other"

# ================================================================
# 跨专业借用优先级
# ================================================================

# 当主专业找不到合适定额时，按顺序去这些专业找
# C12（刷油防腐绝热）是公共册，所有专业都可能借用
# ================================================================
# 项目级覆盖：基础设施项目的定额归属不随系统变化
# ================================================================
# 这些项目不管出现在弱电/消防/暖通/给排水哪个系统的清单中，
# 永远使用指定册的定额。比如"配管"永远用C4电气的配管定额。
# 优先级高于分部标题，是最高优先级的分类规则。
ITEM_BOOK_OVERRIDES = [
    # ---- 核心动作词覆盖（优先级最高，必须在A册"砌体/钢筋/混凝土"前面）----
    # 这些清单的核心动作属于C4电气，但名称中的施工位置词（砌块墙、底板钢筋等）
    # 会误导关键词分类器把它们归到A册土建。放在列表最前面确保优先匹配。
    ("凿槽", "C4"),    # 砌块墙电气管凿槽 → C4-13 凿槽子目
    ("剔槽", "C4"),    # 混凝土墙剔槽 → C4-13 凿槽子目
    ("接地极", "C4"),  # 利用底板钢筋作接地极 → C4-9 接地极子目
    ("引下线", "C4"),  # 利用柱内钢筋作引下线 → C4-9 防雷引下线
    ("避雷带", "C4"),  # 利用结构钢筋作避雷带 → C4-9 避雷带
    ("等电位", "C4"),  # 等电位联结 → C4-9 等电位
    # 电气配管配线（C4-11章） —— 所有系统的管线基础设施
    ("配管", "C4"),
    ("线管", "C4"),
    ("穿线管", "C4"),
    ("管内穿线", "C4"),
    ("管内穿铜", "C4"),
    ("桥架配线", "C4"),
    ("接线盒", "C4"),
    ("金属软管", "C4"),
    # 给排水软接头不能被电气金属软管规则抢走
    ("软接头", "C10"),
    # 桥架安装（C4） —— 电缆桥架是电气基础设施
    ("桥架", "C4"),
    # 防水套管（C10） —— 属于给排水
    ("防水套管", "C10"),
    ("防结露保温", "C12"),
    ("管道绝热", "C12"),
    ("防潮层、保护层", "C12"),
    ("金属结构刷油", "C12"),
    # 土建类（A册） —— 不管出现在哪个Sheet，土方/混凝土/钢筋等永远归土建
    ("土方", "A"),
    ("挖沟槽", "A"),
    ("回填", "A"),
    ("混凝土", "A"),
    ("钢筋", "A"),
    ("砌筑", "A"),
    ("砌体", "A"),
    ("抹灰", "A"),
    ("防水卷材", "A"),
    ("防水涂膜", "A"),
    ("脚手架", "A"),
    # === 全国通用跨册路由（不受省份版本影响） ===
    ("烘手器", "C4"),          # 电器类，所有省份都在C4
    ("医疗气体", "C8"),        # 工业管道，所有省份都在C8
]


BORROW_PRIORITY = {
    "C1":  ["C4", "C13", "C12"],         # 机械设备 → 借电气、其他附属、刷油防腐
    "C2":  ["C8", "C13", "C12"],         # 热力设备 → 借工业管道、其他附属、刷油防腐
    "C3":  ["C8", "C13", "C12"],         # 静置设备 → 借工业管道、其他附属、刷油防腐
    "C4":  ["C5", "C13", "C12"],         # 电气 → 借智能化、其他附属、刷油防腐
    "C5":  ["C4", "C13", "C12"],         # 智能化 → 借电气、其他附属、刷油防腐
    "C6":  ["C4", "C8", "C13", "C12"],   # 仪表 → 借电气、工业管道、其他附属、刷油防腐
    "C7":  ["C13", "C12"],              # 通风空调 → 借其他附属（支架）、刷油防腐
    "C8":  ["C10", "C13", "C12"],        # 工业管道 → 借给排水、其他附属、刷油防腐
    "C9":  ["C10", "C4", "C13", "C12"],  # 消防 → 借给排水、电气、其他附属、刷油防腐
    "C10": ["C9", "C8", "C13", "C12"],   # 给排水 → 借消防、工业管道、其他附属、刷油防腐
    "C11": ["C4", "C13", "C12"],         # 通信 → 借电气、其他附属、刷油防腐
    "C12": [],                          # 刷油防腐 → 公共册，不需要借用
    "C13": ["C12"],                     # 其他附属 → 借刷油防腐
    "A":   [],                          # 土建 → 单册，不需要借用
    "D":   [],                          # 市政 → 单册，不需要借用
    "E":   [],                          # 园林 → 单册，不需要借用
}

SYSTEM_HINT_TO_BOOK = {
    "\u6d88\u9632": "C9",
    "\u7ed9\u6392\u6c34": "C10",
    "\u7535\u6c14": "C4",
    "\u901a\u98ce\u7a7a\u8c03": "C7",
}

FAMILY_ALLOWED_BOOKS = {
    "air_device": ("C7", "C12", "C13"),
    "air_terminal": ("C7", "C12"),
    "air_valve": ("C7", "C12"),
    "bridge_raceway": ("C4", "C11"),
    "bridge_support": ("C4", "C11", "C12", "C13"),
    "cable_family": ("C4", "C11"),
    "conduit_raceway": ("C4",),
    "electrical_box": ("C4",),
    "protection_device": ("C4", "C11"),
    "pipe_support": ("C10", "C9", "C8", "C7", "C12", "C13"),
    "plumbing_accessory": ("C10", "C9", "C12", "C13"),
    "sanitary_fixture": ("C10", "C9", "C12"),
    "valve_accessory": ("C10", "C9", "C8", "C12"),
    "valve_body": ("C10", "C9", "C8", "C12"),
}


# ================================================================
# 分部标题 → 册号映射
# ================================================================

# 清单Excel中的分部/小节标题关键词，用于从标题判断专业
SECTION_KEYWORDS = {
    "C1":  ["机械设备"],
    "C2":  ["热力设备", "锅炉"],
    "C3":  ["静置设备", "工艺金属"],
    # C4 电气：覆盖常见分部标题（如"配管、配线"、"防雷及接地装置"、"电缆安装"等）
    "C4":  ["电气", "强电", "动力", "照明", "配电",
            "电缆", "配管", "配线", "防雷", "接地",
            "低压电器", "控制设备", "开关", "插座"],
    "C5":  ["智能化", "弱电", "安防", "监控工程", "综合布线"],
    "C6":  ["仪表", "自动化", "自控"],
    "C7":  ["通风", "空调", "暖通"],
    "C8":  ["工业管道"],
    # C9 消防：补充"消火栓"、"喷淋"等常见分部标题
    "C9":  ["消防", "消火栓", "火灾报警", "喷淋", "灭火"],
    # C10 给排水：补充"污水"、"雨水"等常见分部标题
    "C10": ["给排水", "给水", "排水", "采暖", "燃气", "卫生", "污水", "雨水"],
    "C11": ["通信"],
    "C12": ["刷油", "防腐", "保温", "绝热"],
    "C13": ["其他", "附属"],
    "A":   ["土建", "建筑", "装饰", "结构", "主体", "土石方", "砌筑",
            "混凝土", "钢筋", "屋面", "门窗", "楼地面", "墙面", "天棚"],
    "D":   ["市政", "道路", "桥梁", "排水管网", "给水管网"],
    "E":   ["园林", "绿化", "景观"],
}


# ================================================================
# 清单编码前缀 → 专业映射（GB 50500 工程量清单计价规范）
# ================================================================
# 清单编码格式：12位数字，如 030801001001
# 前2位 = 大类（01建筑 02装饰 03安装 04市政 05园林）
# 安装工程（03）的前4位 = 专业册号
# 13版(GB50500-2013) 和 24版编码规则一致

# 所有二级编码（前4位）→ 专业册号
# 一张表管全部，优先查4位，查不到再回退2位
BILL_CODE_PREFIX_4 = {
    # ---- 01 建筑工程（附录A）→ A册 ----
    "0101": "A",   # 土石方工程
    "0102": "A",   # 桩基工程
    "0103": "A",   # 砌筑工程
    "0104": "A",   # 混凝土及钢筋混凝土工程
    "0105": "A",   # 厂库房大门、特种门、木结构
    "0106": "A",   # 金属结构工程
    "0107": "A",   # 屋面及防水工程
    "0108": "A",   # 防腐、隔热、保温工程
    # ---- 02 装饰装修工程（附录B）→ A册 ----
    "0201": "A",   # 楼地面装饰工程
    "0202": "A",   # 墙柱面装饰与隔断工程
    "0203": "A",   # 天棚工程
    "0204": "A",   # 门窗工程
    "0205": "A",   # 油漆、涂料、裱糊工程
    "0206": "A",   # 其他装饰工程
    # ---- 03 安装工程（附录C）→ C1~C12 ----
    "0301": "C1",   # 机械设备安装工程
    "0302": "C2",   # 热力设备安装工程
    "0303": "C3",   # 静置设备与工艺金属结构制作安装
    "0304": "C4",   # 电气设备安装工程
    "0305": "C5",   # 建筑智能化系统设备安装
    "0306": "C6",   # 自动化控制仪表安装工程
    "0307": "C7",   # 通风空调工程
    "0308": "C8",   # 工业管道工程
    "0309": "C9",   # 消防工程
    "0310": "C10",  # 给排水、采暖、燃气工程
    "0311": "C11",  # 通信设备及线路工程
    "0312": "C12",  # 刷油、防腐蚀、绝热工程
    # ---- 04 市政工程（附录D）→ D ----
    "0401": "D",   # 土石方工程
    "0402": "D",   # 道路工程
    "0403": "D",   # 桥涵工程
    "0404": "D",   # 隧道工程
    "0405": "D",   # 管网给水工程
    "0406": "D",   # 管网排水工程
    "0407": "D",   # 管网燃气工程
    "0408": "D",   # 管网集中供热工程
    "0409": "D",   # 路灯工程
    "0410": "D",   # 地铁工程
    "0411": "D",   # 管网再生水工程（24版新增）
    # ---- 05 园林绿化工程（附录E）→ E ----
    "0501": "E",   # 绿化工程
    "0502": "E",   # 园路园桥工程
    "0503": "E",   # 园林景观工程
    "0504": "E",   # 措施项目
}

# 大类前缀（前2位）→ 专业，作为4位查不到时的兜底
BILL_CODE_PREFIX_2 = {
    "01": "A",   # 建筑工程
    "02": "A",   # 装饰装修工程
    "03": None,  # 安装工程（必须查4位才能细分，2位不够）
    "04": "D",   # 市政工程
    "05": "E",   # 园林绿化工程
}


def classify_by_bill_code(bill_code: str) -> str | None:
    """根据清单编码（GB 50500）判断专业册号

    清单编码是国标规定的12位数字，前缀直接对应专业方向。
    比 分部标题/关键词匹配 更可靠（结构化信息 > 文字推断）。

    参数:
        bill_code: 清单项目编码（如"030801001001"）

    返回:
        册号（如"C10"），无法判断时返回None
    """
    if not bill_code or not isinstance(bill_code, str):
        return None

    # 清理：去除空格、横杠等非数字字符
    code = re.sub(r'[^0-9]', '', bill_code.strip())
    if len(code) < 4:
        return None

    # 优先检查安装工程细分（前4位）
    prefix4 = code[:4]
    if prefix4 in BILL_CODE_PREFIX_4:
        return BILL_CODE_PREFIX_4[prefix4]

    # 再检查大类（前2位）
    prefix2 = code[:2]
    if prefix2 in BILL_CODE_PREFIX_2:
        return BILL_CODE_PREFIX_2[prefix2]

    return None


# ================================================================
# 品类词路由（从经验库挖掘的品类词→册号映射）
# ================================================================

# 品类词路由表（懒加载，首次调用时读文件）
# tier1: 高置信（90%+集中度），tier2: 中置信（75-90%集中度）
_category_routing_cache: dict | None = None  # tier1路由表
_category_routing_tier2_cache: dict | None = None  # tier2路由表

_AMBIGUOUS_CATEGORY_ROUTE_WORDS = {"弯头", "手动", "软管"}
_INDUSTRIAL_PIPE_ROUTE_HINTS = (
    "工业管道",
    "不锈钢",
    "无缝",
    "焊接",
    "氩弧焊",
    "法兰",
    "介质",
    "DN",
    "PN",
    "HG/T",
    "弯头",
    "三通",
    "大小头",
    "异径",
    "管件",
    "金属编织",
    "过滤器",
    "蝶阀",
    "球阀",
)
_HVAC_AIR_SIDE_HINTS = (
    "风管",
    "通风",
    "风口",
    "散流器",
    "百叶",
    "风阀",
    "防火阀",
    "排烟",
    "新风",
    "风机盘管",
    "导流叶片",
)
_HVAC_VALVE_HINTS = (
    "风阀",
    "防火阀",
    "排烟阀",
    "调节阀",
    "多叶调节阀",
)


def _load_category_routing() -> tuple[dict, dict]:
    """加载品类词路由表（data/category_routing.json）

    返回: (tier1字典, tier2字典)，格式都是 {词: 册号}
    """
    global _category_routing_cache, _category_routing_tier2_cache
    if _category_routing_cache is not None:
        return _category_routing_cache, _category_routing_tier2_cache

    routing_path = Path(__file__).parent.parent / "data" / "category_routing.json"
    if not routing_path.exists():
        logger.warning(f"品类词路由表不存在: {routing_path}")
        _category_routing_cache = {}
        _category_routing_tier2_cache = {}
        return _category_routing_cache, _category_routing_tier2_cache

    try:
        data = json.loads(routing_path.read_text(encoding="utf-8"))
        # tier1: 高置信路由（90%+集中度，≥20次出现）
        tier1 = {}
        for word, info in data.get("tier1", {}).items():
            tier1[word] = info["book"]
        # tier2: 中置信路由（75-90%集中度，≥10次出现）
        tier2 = {}
        for word, info in data.get("tier2", {}).items():
            tier2[word] = info["book"]
        _category_routing_cache = tier1
        _category_routing_tier2_cache = tier2
        logger.debug(f"品类词路由表已加载: tier1={len(tier1)}个, tier2={len(tier2)}个")
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning(f"品类词路由表加载失败: {e}")
        _category_routing_cache = {}
        _category_routing_tier2_cache = {}

    return _category_routing_cache, _category_routing_tier2_cache


def _looks_like_industrial_pipe_context(
    bill_name: str,
    bill_desc: str = "",
    bill_code: str = "",
) -> bool:
    text = f"{bill_name} {bill_desc}".strip()
    if any(token in text for token in _HVAC_AIR_SIDE_HINTS):
        return False
    if any(token in text for token in _INDUSTRIAL_PIPE_ROUTE_HINTS):
        return True
    clean_code = re.sub(r"[^0-9]", "", str(bill_code or ""))
    return clean_code.startswith("0308")



def _should_expand_c8_accessory_search(
    bill_name: str,
    bill_desc: str = "",
    bill_code: str = "",
    *,
    hard_constraints: list[str] | tuple[str, ...] = (),
) -> bool:
    normalized_constraints = _normalize_books(hard_constraints)
    if "C8" not in normalized_constraints:
        return False
    if any(book != "C8" for book in normalized_constraints):
        return False
    if not _looks_like_industrial_pipe_context(
        bill_name,
        bill_desc=bill_desc,
        bill_code=bill_code,
    ):
        return False

    normalized_text = f"{bill_name} {bill_desc}".replace("\u789f\u9600", "\u8776\u9600")
    if any(token in normalized_text for token in _HVAC_AIR_SIDE_HINTS):
        return False

    accessory_hints = (
        "\u9600",
        "\u9600\u95e8",
        "\u8776\u9600",
        "\u6b62\u56de\u9600",
        "\u7403\u9600",
        "\u622a\u6b62\u9600",
        "\u8fc7\u6ee4\u5668",
        "\u9664\u6c61\u5668",
        "\u8f6f\u63a5\u5934",
    )
    return any(token in normalized_text for token in accessory_hints)

def _should_suppress_category_route(
    word: str,
    routed_book: str,
    *,
    bill_name: str,
    bill_desc: str = "",
    bill_code: str = "",
) -> bool:
    if word not in _AMBIGUOUS_CATEGORY_ROUTE_WORDS:
        return False

    text = f"{bill_name} {bill_desc}".strip()
    looks_like_industrial = _looks_like_industrial_pipe_context(
        bill_name,
        bill_desc=bill_desc,
        bill_code=bill_code,
    )

    if word == "弯头" and routed_book == "C7":
        return looks_like_industrial

    if word == "软管" and routed_book == "C10":
        return looks_like_industrial

    if word == "手动" and routed_book == "C7":
        has_valve_signal = any(token in text for token in ("阀", "阀门", "蝶阀", "球阀"))
        has_hvac_valve_signal = any(token in text for token in _HVAC_VALVE_HINTS)
        return has_valve_signal and not has_hvac_valve_signal and looks_like_industrial

    return False


def classify_by_category_words(
    bill_name: str,
    bill_desc: str = "",
    bill_code: str = "",
) -> tuple[str | None, str]:
    """用品类关键词判断专业册号

    从清单名称中提取关键词，查找品类词路由表（从9.8万条经验库挖掘）。
    优先查tier1（90%+集中度），tier1未命中再查tier2（75-90%集中度）。

    选词策略：优先用最长匹配的词（长词比短词更精确）。
    例如"电力电缆"比"电缆"更精确，"消火栓"比"消防"更精确。

    参数:
        bill_name: 清单项目名称

    返回:
        (册号, tier等级)：如 ("C10", "tier1")。无法判断时返回 (None, "")
    """
    if not bill_name:
        return None, ""

    tier1, tier2 = _load_category_routing()

    # 先查tier1（高置信），再查tier2（中置信）
    for tier_name, routing in [("tier1", tier1), ("tier2", tier2)]:
        if not routing:
            continue
        # 按词长降序匹配，优先长词（更精确）
        for word in sorted(routing.keys(), key=len, reverse=True):
            if word in bill_name:
                routed_book = routing[word]
                if _should_suppress_category_route(
                    word,
                    routed_book,
                    bill_name=bill_name,
                    bill_desc=bill_desc,
                    bill_code=bill_code,
                ):
                    logger.debug(
                        f"category route suppressed: '{bill_name}' matched '{word}' -> {routed_book}"
                    )
                    continue
                logger.debug(
                    f"品类词路由({tier_name}): '{bill_name}' 中 '{word}' → {routed_book}"
                )
                return routed_book, tier_name

    return None, ""


def _resolve_code_text_conflict(code_book: str | None,
                                bill_name: str,
                                bill_desc: str = "",
                                bill_code: str = "") -> tuple[str | None, str]:
    """Resolve conflicts between bill-code routing and explicit text signals.

    In practice some uploaded bills carry stale or generic GB codes while the
    item name still clearly states the true installation family, such as
    "给水管道安装" or "排水管道安装". For these cases, a strong text signal
    should override the code-based guess instead of locking the item into a
    wrong specialty.

    Returns:
        (preferred_book, reason). When no strong conflict exists, returns
        (None, "") so the caller can keep the code-based route.
    """
    if not code_book:
        return None, ""

    text = f"{bill_name} {bill_desc}".strip()
    cat_book, cat_tier = classify_by_category_words(
        bill_name,
        bill_desc=bill_desc,
        bill_code=bill_code,
    )
    keyword_book, keyword_score, matched_keyword = _keyword_match(text) if text else (None, 0, "")

    # Strongest case: mined category routing explicitly points to another book.
    if cat_book and cat_book != code_book:
        if cat_tier == "tier1":
            return (
                cat_book,
                f"文本信号覆盖编码: 品类词路由(tier1) '{bill_name}' → {BOOKS[cat_book]['name']}",
            )
        if (
            cat_tier == "tier2"
            and keyword_book == cat_book
            and keyword_score >= 2
        ):
            return (
                cat_book,
                f"文本信号覆盖编码: 品类词路由(tier2)+关键词 '{matched_keyword}' → {BOOKS[cat_book]['name']}",
            )

    # No category route, but multiple explicit keywords consistently point away
    # from the code-based book. This is weaker than tier1 routing, so require a
    # higher keyword score.
    if (
        keyword_book
        and keyword_book != code_book
        and keyword_score >= 3
        and (not cat_book or cat_book == keyword_book)
    ):
        return (
            keyword_book,
            f"文本信号覆盖编码: 关键词匹配 '{matched_keyword}' → {BOOKS[keyword_book]['name']}",
        )

    return None, ""


def _normalize_books(books) -> list[str]:
    if isinstance(books, str):
        raw_items = [books]
    else:
        raw_items = list(books or [])
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _add_book_score(scores: dict[str, float],
                    evidence: dict[str, list[str]],
                    book: str | None,
                    delta: float,
                    reason: str) -> None:
    book = str(book or "").strip()
    if not book or book not in BOOKS:
        return
    scores[book] = scores.get(book, 0.0) + float(delta)
    evidence.setdefault(book, []).append(reason)


def _book_from_system_hint(system_hint: str) -> str | None:
    text = normalize_system_hint(str(system_hint or "").strip())
    if not text:
        return None
    direct = SYSTEM_HINT_TO_BOOK.get(text)
    if direct:
        return direct
    detected = normalize_system_hint(detect_system_hint(text))
    if not detected:
        return None
    return SYSTEM_HINT_TO_BOOK.get(detected)


def _constrain_books_with_family(anchor_book: str | None,
                                 family_allowed: list[str]) -> list[str]:
    anchor_book = str(anchor_book or "").strip()
    family_allowed = _normalize_books(family_allowed)
    if not anchor_book:
        return list(family_allowed)
    if not family_allowed:
        return _normalize_books([anchor_book] + BORROW_PRIORITY.get(anchor_book, [])[:2])

    constrained = [
        book for book in family_allowed
        if book == anchor_book or book in BORROW_PRIORITY.get(anchor_book, [])
    ]
    if anchor_book not in constrained:
        constrained.insert(0, anchor_book)
    return _normalize_books(constrained)


def _derive_hard_book_constraints(section_title: str,
                                  sheet_name: str | None = None,
                                  context_prior: dict | None = None,
                                  canonical_features: dict | None = None) -> list[str]:
    context_prior = dict(context_prior or {})
    canonical_features = dict(canonical_features or {})

    section_book = parse_section_title(section_title) if section_title else None
    sheet_book = parse_section_title(sheet_name) if sheet_name else None
    batch_context = dict(context_prior.get("batch_context") or {})
    system_candidates = [
        detect_system_hint(section_title or ""),
        detect_system_hint(sheet_name or ""),
        batch_context.get("section_system_hint", ""),
        batch_context.get("sheet_system_hint", ""),
    ]
    system_book = next(
        (
            book for book in (
                _book_from_system_hint(candidate)
                for candidate in system_candidates
            )
            if book
        ),
        None,
    )
    family = str(canonical_features.get("family") or context_prior.get("prior_family") or "").strip()
    family_allowed = list(FAMILY_ALLOWED_BOOKS.get(family, ()))

    anchor_book = section_book or sheet_book or system_book
    if anchor_book:
        return _constrain_books_with_family(anchor_book, family_allowed)
    if system_book:
        return _normalize_books([system_book] + BORROW_PRIORITY.get(system_book, [])[:2])
    if family_allowed:
        return _normalize_books(family_allowed)
    return []


def _merge_book_candidates(primary: str | None,
                           scored_books: list[str],
                           hard_constraints: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for book in [primary] + list(scored_books) + list(hard_constraints):
        text = str(book or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered
def _score_to_confidence(route_mode: str,
                         top_score: float,
                         second_score: float,
                         hard_constraints: list[str]) -> str:
    if route_mode == "strict":
        return "high"
    if top_score >= 4.0:
        return "high"
    if hard_constraints and top_score >= 2.0:
        return "medium"
    if top_score - second_score >= 1.2 and top_score >= 1.5:
        return "medium"
    if top_score > 0:
        return "low"
    return "low"


def _render_routing_evidence(reason: str) -> str:
    text = str(reason or "").strip()
    if not text:
        return ""
    if text.startswith("item_override:"):
        return f"项目级覆盖: {text.split(':', 1)[1]}"
    if text.startswith("section:"):
        return f"分部标题: {text.split(':', 1)[1]}"
    if text.startswith("sheet:"):
        return f"工作表标题: {text.split(':', 1)[1]}"
    if text.startswith("bill_title:"):
        return f"清单标题: {text.split(':', 1)[1]}"
    if text.startswith("project_title:"):
        return f"项目标题: {text.split(':', 1)[1]}"
    if text.startswith("category_route:"):
        return f"品类词路由({text.split(':', 1)[1]})"
    if text.startswith("keyword:"):
        return f"关键字匹配: {text.split(':', 1)[1]}"
    if text.startswith("context_specialty:"):
        return f"上下文专业: {text.split(':', 1)[1]}"
    if text.startswith("system_hint:"):
        return f"系统提示: {text.split(':', 1)[1]}"
    if text.startswith("section_system_hint:"):
        return f"分部系统提示: {text.split(':', 1)[1]}"
    if text.startswith("sheet_system_hint:"):
        return f"工作表系统提示: {text.split(':', 1)[1]}"
    if text.startswith("bill_system_hint:"):
        return f"清单标题系统提示: {text.split(':', 1)[1]}"
    if text.startswith("project_title_system_hint:"):
        return f"项目标题系统提示: {text.split(':', 1)[1]}"
    return text


def _finalize_routing_result(book_scores: dict[str, float],
                             routing_evidence: dict[str, list[str]],
                             *,
                             hard_constraints: list[str],
                             route_mode: str,
                             allow_cross_book_escape: bool) -> dict:
    normalized_constraints = _normalize_books(hard_constraints)
    if normalized_constraints:
        scored_items = [
            (book, score)
            for book, score in book_scores.items()
            if book in normalized_constraints
        ]
    else:
        scored_items = list(book_scores.items())
    scored_items.sort(key=lambda item: item[1], reverse=True)

    primary = scored_items[0][0] if scored_items else None
    primary_score = float(scored_items[0][1]) if scored_items else 0.0
    second_score = float(scored_items[1][1]) if len(scored_items) > 1 else 0.0

    if not primary and normalized_constraints:
        primary = normalized_constraints[0]

    scored_books = [book for book, _score in scored_items]
    candidate_books = _merge_book_candidates(primary, scored_books, normalized_constraints)
    search_books = (
        [book for book in candidate_books if book in normalized_constraints]
        if normalized_constraints else
        list(candidate_books)
    )
    if not search_books and normalized_constraints:
        search_books = list(normalized_constraints)

    if primary and primary not in search_books:
        search_books.insert(0, primary)
    search_books = _normalize_books(search_books[:6])

    effective_route_mode = route_mode
    effective_allow_cross_book_escape = bool(allow_cross_book_escape)
    # Province-scope filtering may eliminate every routed book. In that case the
    # router should expose an open search, not a misleading empty strict route.
    if not primary and not search_books and not normalized_constraints:
        effective_route_mode = "open"
        effective_allow_cross_book_escape = True

    hard_search_books = _normalize_books(normalized_constraints)
    advisory_search_books = _normalize_books(
        [book for book in search_books if book not in hard_search_books]
    )

    fallback_books = [
        book for book in _merge_book_candidates(
            None,
            BORROW_PRIORITY.get(primary or "", []),
            candidate_books,
        )
        if book != primary
    ]

    reason_parts = [
        rendered for rendered in (
            _render_routing_evidence(reason)
            for reason in routing_evidence.get(primary or "", [])[:3]
        )
        if rendered
    ]
    if not reason_parts and primary:
        reason_parts = [f"book_score:{primary_score:.2f}"]

    confidence = _score_to_confidence(
        effective_route_mode,
        primary_score,
        second_score,
        normalized_constraints,
    )

    return {
        "primary": primary,
        "primary_name": BOOKS.get(primary or "", {}).get("name") if primary else None,
        "fallbacks": fallback_books,
        "confidence": confidence,
        "reason": " | ".join(reason_parts) if reason_parts else "unclassified",
        "candidate_books": candidate_books,
        "search_books": search_books,
        "hard_book_constraints": normalized_constraints,
        "hard_search_books": hard_search_books,
        "advisory_search_books": advisory_search_books,
        "routing_evidence": {
            book: list(reasons[:5])
            for book, reasons in routing_evidence.items()
            if reasons
        },
        "route_mode": effective_route_mode,
        "allow_cross_book_escape": effective_allow_cross_book_escape,
        "book_scores": {
            book: round(float(score), 4)
            for book, score in sorted(
                book_scores.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        },
    }


def _classify_v2(bill_name: str,
                 bill_desc: str = "",
                 section_title: str = None,
                 province: str = None,
                 bill_code: str = None,
                 context_prior: dict | None = None,
                 canonical_features: dict | None = None,
                 sheet_name: str | None = None) -> dict:
    context_prior = dict(context_prior or {})
    canonical_features = dict(canonical_features or {})
    text = f"{bill_name} {bill_desc}".strip()

    scores: dict[str, float] = {}
    routing_evidence: dict[str, list[str]] = {}

    override_book = _check_item_override(bill_name)
    section_book = parse_section_title(section_title) if section_title else None
    sheet_book = parse_section_title(sheet_name) if sheet_name else None
    bill_title = str(context_prior.get("bill_name") or "").strip()
    project_title = str(context_prior.get("project_name") or "").strip()
    bill_title_book = parse_section_title(bill_title)
    project_title_book = parse_section_title(project_title)
    section_system_hint = detect_system_hint(section_title or "")
    sheet_system_hint = detect_system_hint(sheet_name or "")
    bill_system_hint = detect_system_hint(bill_title)
    project_system_hint = detect_system_hint(project_title)
    desc_system_hint = detect_system_hint(bill_desc or "")
    item_system_hint = detect_system_hint(bill_name or "", bill_desc or "")
    family = str(
        canonical_features.get("family")
        or context_prior.get("prior_family")
        or ""
    ).strip()
    family_allowed = _normalize_books(FAMILY_ALLOWED_BOOKS.get(family, ()))

    hard_constraints = _derive_hard_book_constraints(
        section_title,
        sheet_name=sheet_name,
        context_prior=context_prior,
        canonical_features=canonical_features,
    )
    if _should_expand_c8_accessory_search(
        bill_name,
        bill_desc=bill_desc,
        bill_code=bill_code,
        hard_constraints=hard_constraints,
    ):
        hard_constraints = _normalize_books([*hard_constraints, "C10"])
    if override_book:
        hard_constraints = [override_book]

    if override_book:
        _add_book_score(
            scores,
            routing_evidence,
            override_book,
            6.0,
            f"item_override:{bill_name}",
        )

    if section_book:
        _add_book_score(
            scores,
            routing_evidence,
            section_book,
            4.0,
            f"section:{section_title}",
        )

    if sheet_book:
        _add_book_score(
            scores,
            routing_evidence,
            sheet_book,
            3.2,
            f"sheet:{sheet_name}",
        )

    if bill_code:
        code_book = classify_by_bill_code(bill_code)
        if code_book and code_book in BOOKS:
            preferred_book, preferred_reason = _resolve_code_text_conflict(
                code_book,
                bill_name,
                bill_desc,
                bill_code=bill_code,
            )
            if preferred_book and preferred_book in BOOKS:
                _add_book_score(
                    scores,
                    routing_evidence,
                    preferred_book,
                    3.4,
                    preferred_reason,
                )
                _add_book_score(
                    scores,
                    routing_evidence,
                    code_book,
                    0.6,
                    f"清单编码回退:{bill_code[:4]}",
                )
            else:
                _add_book_score(
                    scores,
                    routing_evidence,
                    code_book,
                    3.0,
                    f"清单编码匹配:{bill_code[:4]}",
                )

    cat_book, cat_tier = classify_by_category_words(
        bill_name,
        bill_desc=bill_desc,
        bill_code=bill_code,
    )
    if cat_book and cat_book in BOOKS:
        cat_delta = 2.8 if cat_tier == "tier1" else 1.6
        _add_book_score(
            scores,
            routing_evidence,
            cat_book,
            cat_delta,
            f"category_route:{cat_tier}",
        )

    if text:
        keyword_book, keyword_score, matched_keyword = _keyword_match(text)
        if keyword_book and keyword_book in BOOKS and keyword_score > 0:
            _add_book_score(
                scores,
                routing_evidence,
                keyword_book,
                min(2.4, 0.55 * float(keyword_score)),
                f"keyword:{matched_keyword or keyword_book}",
            )

        try:
            from src.book_classifier import BookClassifier
            data_result = BookClassifier.get_instance(province).classify(text)
        except Exception as e:
            data_result = None
            logger.debug(f"book classifier routing skipped: {e}")

        if data_result and data_result.get("primary") in BOOKS:
            confidence = str(data_result.get("confidence") or "low").lower()
            delta = {
                "high": 2.6,
                "medium": 1.8,
                "low": 0.7,
            }.get(confidence, 0.7)
            primary_book = str(data_result.get("primary") or "").strip()
            _add_book_score(
                scores,
                routing_evidence,
                primary_book,
                delta,
                f"tfidf:{confidence}",
            )
            for fallback_book in _normalize_books(data_result.get("fallbacks", []))[:2]:
                _add_book_score(
                    scores,
                    routing_evidence,
                    fallback_book,
                    max(delta * 0.25, 0.2),
                    f"tfidf_fallback:{primary_book}",
                )

    if context_prior.get("specialty") in BOOKS:
        _add_book_score(
            scores,
            routing_evidence,
            context_prior.get("specialty"),
            1.2,
            f"context_specialty:{context_prior.get('specialty')}",
        )

    batch_context = dict(context_prior.get("batch_context") or {})
    system_evidence = [
        ("section_system_hint", section_system_hint, 1.9),
        ("sheet_system_hint", sheet_system_hint, 1.7),
        ("bill_system_hint", bill_system_hint, 1.5),
        ("project_title_system_hint", project_system_hint, 1.2),
        ("desc_system_hint", desc_system_hint, 1.4),
        ("item_system_hint", item_system_hint, 0.9),
        ("system_hint", context_prior.get("system_hint"), 2.2),
        ("batch_section_system_hint", batch_context.get("section_system_hint"), 1.3),
        ("batch_sheet_system_hint", batch_context.get("sheet_system_hint"), 1.1),
        ("neighbor_system_hint", batch_context.get("neighbor_system_hint"), 1.1),
        ("project_system_hint", batch_context.get("project_system_hint"), 0.9),
    ]
    for label, hint_value, delta in system_evidence:
        system_book = _book_from_system_hint(str(hint_value or ""))
        if system_book:
            _add_book_score(
                scores,
                routing_evidence,
                system_book,
                delta,
                f"{label}:{hint_value}",
            )

    for hint in _normalize_books(context_prior.get("context_hints", []))[:3]:
        hinted_book = _book_from_system_hint(hint)
        if hinted_book:
            _add_book_score(
                scores,
                routing_evidence,
                hinted_book,
                0.7,
                f"context_hint:{hint}",
            )

    if family_allowed:
        family_bias = 0.8 if len(family_allowed) <= 2 else 0.45
        for book in family_allowed:
            _add_book_score(
                scores,
                routing_evidence,
                book,
                family_bias,
                f"family:{family}",
            )

    scores, routing_evidence, hard_constraints = _filter_routing_by_province_scope(
        scores,
        routing_evidence,
        hard_constraints,
        province,
    )

    route_mode = "open"
    if override_book or section_book or sheet_book:
        route_mode = "strict"
    elif hard_constraints and len(hard_constraints) <= 3:
        route_mode = "strict"
    elif hard_constraints or scores:
        route_mode = "moderate"

    allow_cross_book_escape = route_mode != "strict"

    return _finalize_routing_result(
        scores,
        routing_evidence,
        hard_constraints=hard_constraints,
        route_mode=route_mode,
        allow_cross_book_escape=allow_cross_book_escape,
    )


def classify(bill_name: str, bill_desc: str = "",
             section_title: str = None, province: str = None,
             bill_code: str = None,
             context_prior: dict | None = None,
             canonical_features: dict | None = None,
             sheet_name: str | None = None) -> dict:
    """Route a bill item to candidate books with routing constraints."""
    return _classify_v2(
        bill_name,
        bill_desc=bill_desc,
        section_title=section_title,
        province=province,
        bill_code=bill_code,
        context_prior=context_prior,
        canonical_features=canonical_features,
        sheet_name=sheet_name,
    )


def get_book_from_quota_id(quota_id: str) -> str | None:
    """
    从定额编号中提取册号

    规则：取编号中第一个 '-' 之前的字母+数字部分
    例如：
        C10-1-5  → "C10"
        C4-8-3   → "C4"
        C1-1-100 → "C1"
        C12-3-1  → "C12"

    参数:
        quota_id: 定额编号（如"C10-1-5"）

    返回:
        册号（如"C10"），无法解析时返回None
    """
    if not quota_id:
        return None

    # 通用匹配：字母(1个或多个)+可选数字 在第一个 '-' 之前
    # C10-1-5 → "C10", A-1-1 → "A", SC1-1-1 → "SC1", GY-1 → "GY"
    match = re.match(r'^([A-Za-z]+\d{0,2})-', quota_id)
    if match:
        book = match.group(1).upper()
        if book in BOOKS:
            return book

    # 纯数字前缀：1-2-3 → "C1"（江西/宁夏等省份编码格式，统一加C前缀）
    # 13+ 的册号属于土建/装饰类，保留原始编号（不加C前缀）
    match = re.match(r'^(\d{1,2})-', quota_id)
    if match:
        num = int(match.group(1))
        if 1 <= num <= 12:
            return f"C{num}"
        return str(num)  # 土建/装饰册号，如 13=抹灰, 14=涂料, 15=金属装饰

    return None


def parse_section_title(title: str) -> str | None:
    """
    从分部/小节标题中判断专业

    例如：
        "给排水工程"  → "C10"
        "电气工程"    → "C4"
        "消防工程"    → "C9"
        "通风空调工程" → "C7"
        "第十册 给排水采暖燃气" → "C10"

    参数:
        title: 分部标题文字

    返回:
        册号（如"C10"），无法判断时返回None
    """
    if not title:
        return None

    title = title.strip()

    # 在标题中查找各专业的关键词
    for book_code, keywords in SECTION_KEYWORDS.items():
        for kw in keywords:
            if kw in title:
                return book_code

    return None


def get_book_info(book_code: str) -> dict | None:
    """
    获取指定册的详细信息

    参数:
        book_code: 册号（如"C10"）

    返回:
        {"code": "C10", "name": "给排水采暖燃气", "keywords": [...], "borrow": [...]}
        不存在时返回None
    """
    if book_code not in BOOKS:
        return None

    return {
        "code": book_code,
        "name": BOOKS[book_code]["name"],
        "keywords": BOOKS[book_code]["keywords"],
        "borrow": BORROW_PRIORITY.get(book_code, []),
    }


def get_all_books() -> list[dict]:
    """
    获取所有册的列表

    返回:
        [{"code": "C1", "name": "机械设备安装"}, {"code": "C2", ...}, ...]
    """
    return [
        {"code": code, "name": info["name"]}
        for code, info in BOOKS.items()
    ]


# ================================================================
# 内部辅助函数
# ================================================================

def _check_item_override(bill_name: str) -> str | None:
    """
    检查清单项名称是否命中项目级覆盖规则

    基础设施项目（配管、桥架、穿线等）不管在哪个系统清单中，
    都使用固定的定额册。这个函数检查是否命中这类覆盖规则。

    参数:
        bill_name: 清单项目名称

    返回:
        册号（如"C4"），不命中返回None
    """
    if not bill_name:
        return None
    name = bill_name.strip()
    for keyword, book in ITEM_BOOK_OVERRIDES:
        if keyword in name:
            return book
    return None


def _keyword_match(text: str) -> tuple[str | None, int, str]:
    """
    在文本中查找关键词，返回匹配的册号

    返回:
        (册号, 匹配分数, 匹配到的关键词)
        匹配分数 = 该册匹配到的关键词数量

    策略：
    - 统计每个册匹配到的关键词数量
    - 取匹配数最多的册
    - 如果有多个册匹配数相同，按优先级（更具体的专业优先）
    """
    scores = {}  # {册号: (匹配数, 最后匹配的关键词)}

    for book_code, info in BOOKS.items():
        count = 0
        max_len = 0  # 最长匹配关键词的长度（越长越精确）
        last_matched = ""
        for kw in info["keywords"]:
            if kw in text:
                count += 1
                if len(kw) > max_len:
                    max_len = len(kw)
                    last_matched = kw
        if count > 0:
            scores[book_code] = (count, max_len, last_matched)

    if not scores:
        return None, 0, ""

    # 按(匹配数, 最长关键词长度)降序排列
    # 最长关键词优先：比如"风机盘管"(4字)比"风机"(2字)更精确
    sorted_books = sorted(scores.items(),
                          key=lambda x: (x[1][0], x[1][1]), reverse=True)

    best_book = sorted_books[0][0]
    best_score = sorted_books[0][1][0]
    best_keyword = sorted_books[0][1][2]

    return best_book, best_score, best_keyword


# ================================================================
# 命令行测试
# ================================================================

if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    # 测试分类
    test_cases = [
        ("室内给水管道安装", "材质：镀锌钢管\nDN25\n丝接", None),
        ("电力电缆敷设", "YJV-4x185+1x95\n沿桥架敷设", None),
        ("风机盘管安装", "卧式暗装\n冷量3500W", None),
        ("消火栓安装", "室内消火栓\nDN65", None),
        ("法兰安装", "名称：Y型过滤器\nDN100", None),
        ("无缝钢管", "DN150\n焊接", "给排水工程"),  # 有分部标题
        ("无缝钢管", "DN150\n焊接", "工业管道"),     # 有分部标题
        ("保温", "岩棉\n管道保温", None),
        ("摄像头安装", "枪式摄像机\n200万像素", None),
    ]

    for name, desc, section in test_cases:
        result = classify(name, desc, section)
        primary = result["primary"] or "全库"
        primary_name = result["primary_name"] or ""
        print(f"  {name} | {section or '-'} => {primary} {primary_name} "
              f"({result['confidence']}) | {result['reason']}")

