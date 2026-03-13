"""
清单编码匹配器：根据清单名称+描述，匹配GB/T 50856-2024的9位国标清单编码。

核心逻辑：规则路由 + 关键词匹配（方案B）
1. 先判专业（电气/给排水/消防/通风...），缩小到对应附录
2. 再用关键词匹配具体清单项
3. 返回匹配的编码 + 项目特征模板 + 工作内容

使用位置：bill_compiler.py 中调用，在编清单编译阶段自动补全编码
"""

import json
import re
from pathlib import Path
from functools import lru_cache
from loguru import logger


# ============================================================
# 数据加载（懒加载，只读一次）
# ============================================================

_features_db = None  # 项目特征数据库缓存


def _load_features_db() -> dict:
    """加载项目特征数据库（1182条）。"""
    global _features_db
    if _features_db is not None:
        return _features_db

    db_path = Path(__file__).resolve().parent.parent / "data" / "bill_features_2024.json"
    if not db_path.exists():
        logger.warning(f"项目特征库不存在: {db_path}")
        _features_db = {"items": []}
        return _features_db

    with open(db_path, "r", encoding="utf-8") as f:
        _features_db = json.load(f)
    logger.info(f"项目特征库已加载: {_features_db['total_items']}条")
    return _features_db


# ============================================================
# 专业路由表：关键词 → 附录字母
# ============================================================

# 路由优先级：先匹配更具体的关键词，再匹配宽泛的
# 每条规则：(关键词列表, 附录字母, 权重加分)
APPENDIX_ROUTES = [
    # J 消防（优先级高，因为消防管道和给排水管道容易混）
    (["消火栓", "喷淋", "灭火", "消防", "火灾报警", "感烟", "感温",
      "防火阀", "排烟", "消防泵", "湿式报警", "水流指示", "末端试水",
      "消防水箱", "消防栓", "烟感", "温感", "手报", "声光报警",
      "防火门监控", "消防电源监控", "气体灭火", "泡沫灭火"], "J"),

    # G 通风空调
    (["通风", "空调", "风管", "风机盘管", "新风", "排风", "送风",
      "风口", "散流器", "风阀", "调节阀", "防火阀门",
      "冷却塔", "冷水机", "制冷", "膨胀水箱", "分集水器",
      "风管保温", "空调水管"], "G"),

    # D 电气
    (["变压器", "配电", "母线", "电缆", "配管", "配线", "灯具",
      "开关", "插座", "防雷", "接地", "桥架", "线槽", "光伏",
      "电机", "滑触线", "照明", "应急灯", "日光灯", "LED",
      "配电箱", "配电柜", "控制柜", "动力柜", "电力电缆",
      "控制电缆", "电线管", "PVC管", "SC管", "KBG", "JDG",
      "穿线", "避雷", "等电位", "断路器", "接触器", "继电器"], "D"),

    # K 给排水/采暖/燃气
    (["给水", "排水", "热水", "冷水", "采暖", "供暖", "暖气",
      "散热器", "地暖", "燃气", "天然气", "煤气", "卫生器具",
      "洗脸盆", "坐便器", "蹲便器", "小便器", "浴缸", "淋浴",
      "水龙头", "角阀", "截止阀", "止回阀", "球阀", "闸阀",
      "蝶阀", "减压阀", "安全阀", "过滤器", "水表", "压力表",
      "地漏", "存水弯", "管卡", "管道支架", "吊架",
      "镀锌钢管", "PPR", "PE管", "铸铁管", "不锈钢管",
      "铜管", "钢塑复合", "给排水"], "K"),

    # H 工业管道
    (["低压管道", "中压管道", "高压管道", "工业管道",
      "工艺管道", "蒸汽管道", "压缩空气管道", "氧气管道",
      "氮气管道", "乙炔管道", "法兰", "管件焊接"], "H"),

    # E 建筑智能化
    (["综合布线", "网络", "弱电", "监控", "门禁", "对讲",
      "广播", "有线电视", "卫星", "智能化", "安防",
      "入侵报警", "巡更", "停车场", "楼宇自控",
      "智能家居", "信息点", "光纤", "网线", "六类线"], "E"),

    # F 仪表
    (["仪表", "温度计", "压力表", "流量计", "液位计",
      "调节阀", "DCS", "PLC", "变送器", "热电偶",
      "热电阻"], "F"),

    # L 通信
    (["通信", "程控交换", "传输设备", "天线", "基站",
      "光缆", "通信管道", "电话", "光纤熔接"], "L"),

    # M 刷油防腐绝热
    (["刷油", "防腐", "绝热", "保温", "防锈",
      "衬里", "喷镀", "阴极保护", "补口"], "M"),

    # B 热力设备
    (["锅炉", "汽轮机", "发电机", "脱硫", "脱硝",
      "除尘", "工业炉"], "B"),

    # A 机械设备
    (["机床", "起重机", "电梯", "泵", "压缩机",
      "输送机", "风机安装"], "A"),

    # C 静置设备
    (["容器", "塔器", "油罐", "球罐", "气柜"], "C"),

    # N 其他
    (["凿槽", "开孔", "套管制作", "支架制作"], "N"),
]


def _route_appendix(name: str, description: str = "") -> str:
    """根据名称和描述判断所属附录。返回附录字母（如'D','J','K'）。"""
    text = f"{name} {description}".lower()
    # 去掉序号前缀（如"1."、"2."）方便匹配
    text_clean = re.sub(r"\d+\.\s*", " ", text)

    for keywords, appendix in APPENDIX_ROUTES:
        for kw in keywords:
            if kw.lower() in text_clean:
                return appendix

    return ""  # 无法判断


# ============================================================
# 清单项匹配（在附录内精确匹配）
# ============================================================

def _build_name_index() -> dict[str, list[dict]]:
    """构建 附录→清单项 的索引，加速查找。"""
    db = _load_features_db()
    index = {}  # appendix_letter → [items]
    for item in db["items"]:
        # 跳过名称为空的条目（数据源缺失）
        if not item.get("name", "").strip():
            continue
        app = item["appendix"]
        if app not in index:
            index[app] = []
        index[app].append(item)
    return index


# 全局索引缓存
_appendix_index = None


def _get_appendix_index() -> dict[str, list[dict]]:
    """获取附录索引（懒加载）。"""
    global _appendix_index
    if _appendix_index is None:
        _appendix_index = _build_name_index()
    return _appendix_index


def _score_match(item: dict, name: str, description: str) -> float:
    """计算一条清单项与输入的匹配分数。

    匹配策略：
    1. 名称完全包含 → 高分
    2. 同义词/别名匹配 → 中高分
    3. 描述中的关键词匹配 → 加分
    """
    score = 0.0
    item_name = item["name"]
    if not item_name:
        return 0.0

    text = f"{name} {description}"

    # 同义词/别名映射（输入名称 → 标准名称中可能的关键词）
    # 帮助模糊输入匹配到标准名称
    SYNONYMS = {
        "PPR": "塑料管", "PPR管": "塑料管", "PP-R": "塑料管",
        "PE管": "塑料管", "PVC-U": "塑料管",
        "蹲便": "蹲便器", "坐便": "坐便器", "马桶": "坐便器",
        "洗手盆": "洗脸盆", "面盆": "洗脸盆",
        "烟感": "感烟探测器", "温感": "感温探测器",
        "手报": "手动报警按钮",
        "声光": "声光报警",
        "LED灯": "灯具", "日光灯": "灯具", "吸顶灯": "灯具",
        "筒灯": "灯具", "射灯": "灯具",
        "空开": "断路器", "漏保": "断路器",
        "铝扣板": "金属吊顶",
        "不锈钢管": "不锈钢管", "薄壁不锈钢": "不锈钢管",
    }

    # 先尝试同义词展开
    expanded_name = name
    for alias, standard in SYNONYMS.items():
        if alias in name:
            expanded_name = name.replace(alias, standard)
            break

    # 名称完全匹配（最高分）
    if item_name == name or item_name == expanded_name:
        score += 100
    # 输入名称包含清单项名称（如"消火栓镀锌钢管"包含"消火栓钢管"→不对，要反过来）
    elif item_name in name or item_name in expanded_name:
        score += 85
    # 清单项名称包含输入名称（如"成套配电箱"包含"配电箱"）
    elif name in item_name or expanded_name in item_name:
        score += 75
    else:
        # 核心词匹配（去掉修饰词后比较）
        # 比如"镀锌钢管"的核心是"钢管"，"碳钢通风管道"的核心是"通风管道"
        name_chars = set(name)
        item_chars = set(item_name)
        common = name_chars & item_chars
        # 去掉太常见的字（"管""线""器""机"等单独出现意义不大）
        common -= {"的", "及", "与", "和", "、"}
        if len(common) >= 2:
            # 用Jaccard相似度
            union = name_chars | item_chars
            jaccard = len(common) / max(len(union), 1)
            score += jaccard * 60

    # 项目特征中的关键词在描述中出现 → 加分
    if description and item.get("features"):
        for feat in item["features"]:
            if feat in text:
                score += 2

    return score


def match_bill_code(name: str, description: str = "",
                    hint_appendix: str = "") -> dict | None:
    """匹配清单编码。

    参数:
        name: 项目名称（如"镀锌钢管"、"消火栓钢管"）
        description: 项目特征描述（如"DN100 螺纹连接 室内"）
        hint_appendix: 提示附录字母（可选，如已知专业可直接指定）

    返回:
        匹配结果dict，包含:
        {
            "code": "031001002",          # 9位编码
            "name": "镀锌钢管",            # 标准名称
            "features": [...],            # 项目特征模板
            "unit": "m",                  # 计量单位
            "calc_rule": "...",           # 计算规则
            "work_content": [...],        # 工作内容
            "appendix": "K",             # 附录
            "section": "K.1",            # 节
            "match_score": 85.0,         # 匹配分数
            "match_method": "keyword",   # 匹配方式
        }
        未匹配到返回 None
    """
    if not name:
        return None

    # 第1步：判断附录（专业路由）
    appendix = hint_appendix or _route_appendix(name, description)

    index = _get_appendix_index()

    # 第2步：在目标附录内匹配
    candidates = []

    if appendix and appendix in index:
        # 优先在判定的附录内搜索（附录内加权20%）
        for item in index[appendix]:
            score = _score_match(item, name, description)
            if score > 15:
                candidates.append((score * 1.2, item))  # 附录内加权

    # 如果目标附录没找到好的结果，扩展到全部附录
    best_in_appendix = max((c[0] for c in candidates), default=0)
    if best_in_appendix < 60:
        for app_letter, app_items in index.items():
            if app_letter == appendix:
                continue
            for item in app_items:
                score = _score_match(item, name, description)
                if score > 40:
                    candidates.append((score * 0.8, item))  # 跨附录降权

    if not candidates:
        return None

    # 取最高分
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_item = candidates[0]

    return {
        "code": best_item["code"],
        "name": best_item["name"],
        "features": best_item["features"],
        "unit": best_item["unit"],
        "calc_rule": best_item.get("calc_rule", ""),
        "work_content": best_item.get("work_content", []),
        "appendix": best_item["appendix"],
        "appendix_name": best_item.get("appendix_name", ""),
        "section": best_item.get("section", ""),
        "section_name": best_item.get("section_name", ""),
        "match_score": round(best_score, 1),
        "match_method": "keyword",
    }


# ============================================================
# 批量匹配（供 bill_compiler 调用）
# ============================================================

def match_bill_codes(items: list[dict]) -> list[dict]:
    """批量匹配清单编码。

    对每条清单item，如果没有编码或编码不完整，尝试自动匹配。
    匹配结果写入 item["bill_match"] 字段。

    参数:
        items: 清单项列表（bill_reader输出的dict列表）

    返回:
        原列表（原地修改）
    """
    if not items:
        return items

    # 确保数据库已加载
    _load_features_db()

    matched = 0
    skipped = 0

    for item in items:
        code = item.get("code", "").strip()
        name = item.get("name", "").strip()
        desc = item.get("description", "").strip()

        # 已有完整9位编码 → 跳过（尊重用户输入）
        if code and re.match(r"^03\d{7}", code):
            skipped += 1
            continue

        # 没有名称 → 跳过
        if not name:
            continue

        # 匹配
        result = match_bill_code(name, desc)
        if result:
            item["bill_match"] = result
            matched += 1

    if matched > 0 or skipped > 0:
        logger.info(f"  清单编码匹配: {matched}条匹配成功, "
                    f"{skipped}条已有编码跳过, "
                    f"{len(items) - matched - skipped}条未匹配")

    return items
