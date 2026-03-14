"""
清单编码匹配器：根据清单名称+描述，匹配9位国标清单编码。

核心逻辑：从清单库(41.6万条)学习"名称→编码"映射，再从项目特征库补全模板。
1. 在清单库名称索引中精确/模糊匹配
2. 匹配到9位编码后，从项目特征库补全features/unit/work_content
3. 批量匹配时自动编后3位序号，组成完整12位编码

使用位置：bill_compiler.py 中调用，在编清单编译阶段自动补全编码
"""

import json
import re
from pathlib import Path
from loguru import logger

# 套定额的专业分类器（共享消歧逻辑，编清单和套定额用同一套规则）
from src.specialty_classifier import classify as classify_specialty


# ============================================================
# 常量：分部编码前4位 → 附录字母
# ============================================================

# 安装工程（03xx）的分部→附录字母
CODE_PREFIX_TO_APPENDIX = {
    "0301": "A", "0302": "B", "0303": "C", "0304": "D",
    "0305": "E", "0306": "F", "0307": "G", "0308": "H",
    "0309": "J", "0310": "K", "0311": "L", "0312": "M",
    "0313": "N", "0314": "P",
}

# 编码前2位 → 专业大类名称（GB/T 50500 体系）
MAJOR_CATEGORY = {
    "01": "房建",    # 房屋建筑与装饰工程
    "02": "装饰",    # 装饰装修工程（部分标准合并到01）
    "03": "安装",    # 通用安装工程
    "04": "市政",    # 市政工程
    "05": "园林",    # 园林绿化工程
    "06": "矿山",    # 矿山工程
    "07": "修缮",    # 修缮工程
    "08": "轨道",    # 城市轨道交通工程
    "09": "仿古",    # 仿古建筑工程
}

# 册号 → 清单库的附录字母/大类编码
# specialty_classifier 返回 "C10"，清单库索引用的是 "K"（附录字母）或 "01"（大类编码）
# 这个表做转换，让两边能对上
BOOK_TO_INDEX_LABEL = {
    # 安装12册 → 附录字母（和 CODE_PREFIX_TO_APPENDIX 反过来）
    "C1": "A", "C2": "B", "C3": "C", "C4": "D", "C5": "E",
    "C6": "F", "C7": "G", "C8": "H", "C9": "J", "C10": "K",
    "C11": "L", "C12": "M", "C13": "N",
    # 非安装专业 → 大类编码
    "A": "01",   # 房建（specialty_classifier用A，清单库索引用01）
    "D": "04",   # 市政
    "E": "05",   # 园林
}


# ============================================================
# 数据加载（懒加载，只读一次）
# ============================================================

_features_db = None    # 项目特征数据库缓存（1182条，有features/unit/work_content）
_bill_lib_index = None  # 清单库名称索引（从41.6万条全专业清单构建）


def _load_features_db() -> dict:
    """加载项目特征数据库（1182条，GB/T 50856-2024）。"""
    global _features_db
    if _features_db is not None:
        return _features_db

    db_path = Path(__file__).resolve().parent.parent / "data" / "bill_features_2024.json"
    if not db_path.exists():
        logger.warning(f"项目特征库不存在: {db_path}")
        _features_db = {"items": [], "total_items": 0}
        return _features_db

    with open(db_path, "r", encoding="utf-8") as f:
        _features_db = json.load(f)
    logger.info(f"项目特征库已加载: {_features_db['total_items']}条")
    return _features_db


def _extract_core_name(name: str) -> str:
    """从清单名称中提取核心名称，去掉规格参数。

    例如：
      "中压管道 不锈钢管(氩弧焊) 公称直径(mm以内) 100" → "中压管道"
      "室内消火栓钢管(沟槽连接) 外径(mm以内) 110" → "室内消火栓钢管"
      "配电箱墙上(柱上)明装 规格(回路以内) 8" → "配电箱"
      "镀锌钢管" → "镀锌钢管"
    """
    # 去掉括号内容（但保留名称中有意义的括号如"(软管)"）
    # 策略：取第一个空格前的部分作为核心名称
    core = name.strip()

    # 如果名称有空格，取第一段
    if " " in core:
        core = core.split()[0]

    # 去掉尾部的括号内容（如"钢管(沟槽连接)"→"钢管"）
    core = re.sub(r"[（(][^）)]*[）)]$", "", core)

    return core.strip()


def _strip_model_prefix(name: str) -> str:
    """去掉名称中的型号前缀和后缀，提取纯中文部分。

    很多清单名称前面带型号编号（如"LT04四头格栅灯"、"LED灯带LT11"），
    这些型号在清单库索引中不存在，导致匹配失败。
    去掉型号后（"四头格栅灯"、"灯带"），更容易在索引中找到。

    例如：
      "LT04四头格栅灯" → "四头格栅灯"
      "LED灯带LT11" → "灯带"
      "LED防水灯带LT12" → "防水灯带"
      "信号线RVV2*0.5" → "信号线"
      "布线管cat6-4UTP" → "布线管"
      "不锈钢水箱溢流DN50" → "不锈钢水箱溢流"
      "镀锌钢管" → "镀锌钢管"（没有型号，不变）
    """
    # 去掉开头的英文+数字前缀（如"LT04"、"LED"、"PVC"等）
    cleaned = re.sub(r'^[A-Za-z0-9\-]+', '', name)

    # 去掉尾部的英文+数字后缀（如"LT11"、"DN50"、"RVV2*0.5"等）
    cleaned = re.sub(r'[A-Za-z][A-Za-z0-9\-\*/\.]*$', '', cleaned)
    # 也去掉尾部纯数字（如"DN50"去掉字母后剩"50"）
    cleaned = re.sub(r'[\d\*\/\.]+$', '', cleaned)

    cleaned = cleaned.strip()

    # 清洗后太短（<2字）则不用清洗结果
    if len(cleaned) < 2:
        return name

    return cleaned


def _load_bill_library_index() -> dict:
    """从清单库构建名称→编码索引。

    从41.6万条全专业清单中按核心名称分组，同名不同编码取出现次数最多的。
    支持安装(03)、房建(01)、装饰(02)、市政(04)、园林(05)等全部专业。

    返回:
        {
            "镀锌钢管": [{"code9": "031001002", "appendix": "K", "major": "03", "count": 58}, ...],
            "瓷砖地面": [{"code9": "010401xxx", "appendix": "", "major": "01", "count": 30}, ...],
            ...
        }
        每个名称对应一个候选列表（按count降序排列），通常只有1~3个候选。
    """
    global _bill_lib_index
    if _bill_lib_index is not None:
        return _bill_lib_index

    lib_path = Path(__file__).resolve().parent.parent / "data" / "bill_library_all.json"
    if not lib_path.exists():
        logger.warning(f"清单库不存在: {lib_path}")
        _bill_lib_index = {}
        return _bill_lib_index

    with open(lib_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 统计：(核心名称, 9位编码) → 出现次数
    from collections import Counter
    name_code_counter = Counter()

    for lib_name, lib_data in data.get("libraries", {}).items():
        # 2024版权重×3（优先使用最新标准的编码）
        weight = 3 if "2024" in lib_name else 1

        for item in lib_data.get("items", []):
            code = item.get("code", "")
            name = item.get("name", "")
            if len(code) < 9 or not name:
                continue
            # 只收标准编码（0开头的9位数字编码，如01/02/03/04/05...）
            if not re.match(r"^0[1-9]\d{7}", code):
                continue

            code9 = code[:9]
            core = _extract_core_name(name)
            if not core or len(core) < 2:
                continue

            name_code_counter[(core, code9)] += weight

    # 构建索引：核心名称 → [{code9, appendix, major, count}, ...]
    index = {}
    for (core_name, code9), count in name_code_counter.items():
        major = code9[:2]  # 专业大类：01房建/03安装/04市政...
        prefix4 = code9[:4]
        # 安装工程(03)有附录字母A-N，其他专业暂不细分
        appendix = CODE_PREFIX_TO_APPENDIX.get(prefix4, "")

        if core_name not in index:
            index[core_name] = []
        index[core_name].append({
            "code9": code9,
            "appendix": appendix,
            "major": major,
            "count": count,
        })

    # 每个名称的候选按优先级排列：安装(03)优先，同专业内按出现次数降序
    # 原因：系统主业务是安装工程，同名时安装条目应排在前面
    for name in index:
        index[name].sort(key=lambda x: (0 if x["major"] == "03" else 1, -x["count"]))

    _bill_lib_index = index
    logger.info(f"清单库索引已构建: {len(index)}个不同名称, "
                f"来自{sum(len(v) for v in index.values())}个名称-编码对")
    return _bill_lib_index


# ============================================================
# 同义词表（输入别名 → 标准名称）
# ============================================================

SYNONYMS = {
    # 管材（注意：PPR管→塑料管，不是PPR→塑料管，否则会变成"塑料管管"）
    "PPR管": "塑料管", "PPR": "塑料管", "PP-R管": "塑料管",
    "PP-R": "塑料管", "PE管": "塑料管", "PVC-U管": "塑料管",
    "薄壁不锈钢": "不锈钢管",
    # 卫生器具（清单库用"大便器""小便器"不用"坐便器""蹲便器"）
    "坐便器": "大便器", "蹲便器": "大便器", "蹲便": "大便器",
    "坐便": "大便器", "马桶": "大便器", "座便器": "大便器",
    "洗手盆": "洗脸盆", "面盆": "洗脸盆",
    # 消防探测器
    "烟感": "感烟探测器", "温感": "感温探测器",
    "手报": "手动报警按钮", "声光": "声光报警",
    # 灯具
    "LED灯": "灯具", "日光灯": "灯具", "吸顶灯": "灯具",
    "筒灯": "灯具", "射灯": "灯具", "平板灯": "灯具",
    # 电气
    "空开": "断路器", "漏保": "断路器",
    "控制箱": "配电箱", "动力箱": "配电箱",
    # 通风
    "换气扇": "风机", "排气扇": "风机",
    # 其他
    "铝扣板": "金属吊顶",
}

# 同义词按key长度降序排列（长的先匹配，避免"PPR"先于"PPR管"匹配）
_SYNONYMS_SORTED = sorted(SYNONYMS.items(), key=lambda x: -len(x[0]))

# 从清单库自动挖掘的同义词（bill_synonyms.json），懒加载
_bill_synonyms_cache = None


def _load_bill_synonyms() -> list:
    """加载清单库自动挖掘的同义词，和硬编码同义词合并。

    硬编码优先：如果 SYNONYMS 里已有某个key，不会被 bill_synonyms 覆盖。
    返回合并后的 (key, value) 列表，按key长度降序排列。
    """
    global _bill_synonyms_cache
    if _bill_synonyms_cache is not None:
        return _bill_synonyms_cache

    # 先放硬编码的（优先级高）
    merged = dict(SYNONYMS)

    # 加载自动挖掘的同义词
    syn_path = Path(__file__).resolve().parent.parent / "data" / "bill_synonyms.json"
    if syn_path.exists():
        try:
            with open(syn_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            auto_syns = data.get("synonyms", {})
            added = 0
            for key, value in auto_syns.items():
                # 跳过太长的名称（超过10个字的key通常是带参数的，不适合做同义词替换）
                if len(key) > 10:
                    continue
                # 硬编码优先，不覆盖
                if key not in merged:
                    merged[key] = value
                    added += 1
            logger.info(f"清单同义词已加载: 硬编码{len(SYNONYMS)}条 + "
                        f"自动挖掘{added}条 = 共{len(merged)}条")
        except Exception as e:
            logger.warning(f"清单同义词加载失败: {e}")

    # 按key长度降序排列（长词先匹配）
    _bill_synonyms_cache = sorted(merged.items(), key=lambda x: -len(x[0]))
    return _bill_synonyms_cache


# ============================================================
# 消歧函数（调用共享的专业分类器）
# ============================================================

# 路由模型缓存（轻量文本分类器，从清单库训练）
_route_model_cache = None
_route_model_loaded = False  # 区分"没加载"和"加载失败"


def _load_route_model():
    """加载路由分类模型（TF-IDF + LinearSVC）。

    从清单库41.6万条数据训练的轻量分类器，
    用于消歧时替代/补充规则路由，特别是非安装专业路由更准确。
    """
    global _route_model_cache, _route_model_loaded
    if _route_model_loaded:
        return _route_model_cache

    _route_model_loaded = True
    model_path = Path(__file__).resolve().parent.parent / "data" / "route_model.pkl"
    if not model_path.exists():
        return None

    try:
        import pickle
        with open(model_path, "rb") as f:
            _route_model_cache = pickle.load(f)
        logger.info("路由模型已加载")
        return _route_model_cache
    except Exception as e:
        logger.warning(f"路由模型加载失败: {e}")
        return None


def _predict_route(name: str) -> str:
    """用路由模型预测专业标签。

    返回索引标签（如"K"、"D"、"01"等），失败返回空字符串。
    """
    model = _load_route_model()
    if not model:
        return ""

    try:
        core = _extract_core_name(name)
        if not core or len(core) < 2:
            return ""
        vectorizer = model["vectorizer"]
        clf = model["classifier"]
        X = vectorizer.transform([core])
        return clf.predict(X)[0]
    except Exception:
        return ""


# 描述文本中的专业暗示词 → 索引标签
# 这些词出现在描述中时，几乎100%说明属于对应专业
# （关键词, 对应附录/大类标签）
_DESC_SPECIALTY_HINTS = [
    # 消防(J)
    ("消防", "J"), ("喷淋", "J"), ("灭火", "J"), ("火灾", "J"),
    ("烟感", "J"), ("温感", "J"),
    # 给排水(K)
    ("给水", "K"), ("排水", "K"), ("热水", "K"), ("采暖", "K"),
    ("暖气", "K"),
    # 通风空调(G)
    ("通风", "G"), ("空调", "G"), ("新风", "G"),
    # 电气(D)
    ("灯具", "D"), ("配电", "D"), ("电缆", "D"), ("桥架", "D"),
    # 智能化(E)
    ("智能", "E"), ("弱电", "E"), ("监控", "E"), ("网络", "E"),
    # 刷油防腐(M)
    ("保温", "M"), ("防腐", "M"), ("刷油", "M"), ("绝热", "M"),
    # 仪表(F)
    ("仪表", "F"),
]


def _desc_specialty_hint(description: str, candidates: list = None) -> str:
    """从描述文本中提取专业暗示词，辅助消歧。

    只看描述（不看名称），如果描述中出现了明确的专业关键词
    （如"消防"、"给水"、"灯具"），就返回对应专业标签。
    有候选列表时只返回匹配候选的标签，无候选时直接返回。

    参数:
        description: 项目特征描述
        candidates: 候选列表（可选）

    返回:
        匹配的索引标签，无匹配返回空字符串
    """
    if not description:
        return ""

    # 候选中有哪些专业标签（没有候选时不做过滤）
    candidate_labels = None
    if candidates:
        candidate_labels = set(
            c["appendix"] or c["major"] for c in candidates
        )

    for keyword, label in _DESC_SPECIALTY_HINTS:
        if keyword in description:
            if candidate_labels is None or label in candidate_labels:
                return label

    return ""


def _disambiguate(name: str, description: str = "",
                  section_title: str = "",
                  candidates: list = None) -> str:
    """用规则+模型做同名多义消歧。

    分工策略（按优先级）：
    1. 描述文本暗示词（如描述含"消防"→J）
    2. 跨大类消歧（安装 vs 房建 vs 市政）→ 用路由模型
    3. 同大类消歧（安装内K vs J vs D）→ 用规则分类器

    参数:
        name: 清单名称
        description: 项目特征描述
        section_title: 分部标题
        candidates: 候选列表

    返回:
        索引标签（如"K"、"D"、"01"等），无法判断返回空字符串
    """
    if candidates:
        majors = set(c["major"] for c in candidates)
        is_cross_major = len(majors) > 1
        all_non_install = "03" not in majors  # 全部是非安装候选
    else:
        is_cross_major = False
        all_non_install = False

    # 第1优先级：描述文本中的专业暗示词
    # 描述里明确写了"消防"、"灯具"等词的，几乎不会错
    if description:
        desc_hint = _desc_specialty_hint(description, candidates)
        if desc_hint:
            return desc_hint

    # 第2优先级：跨大类 且 候选中有非安装：用路由模型
    # （模型对非安装分类更准确，安装内部用规则更好）
    if is_cross_major and all_non_install:
        # 纯非安装跨大类（如房建01 vs 市政04 vs 园林05）：用模型
        model_label = _predict_route(name)
        if model_label and candidates:
            candidate_labels = set(
                c["appendix"] or c["major"] for c in candidates
            )
            if model_label in candidate_labels:
                return model_label

    # 第3优先级：用规则分类器
    try:
        result = classify_specialty(
            bill_name=name,
            bill_desc=description,
            section_title=section_title,
        )
        book = result.get("primary")
        if not book:
            return ""
        return BOOK_TO_INDEX_LABEL.get(book, "")
    except Exception as e:
        logger.debug(f"消歧失败: {e}")
        return ""


# ============================================================
# Sheet名 → 附录字母映射（从Excel的sheet名推断专业）
# ============================================================

# (关键词, 附录字母) — 按优先级排列，先匹配更具体的
_SHEET_NAME_ROUTES = [
    ("消防", "J"), ("消火栓", "J"), ("喷淋", "J"), ("报警", "J"),
    ("通风", "G"), ("空调", "G"), ("暖通", "G"), ("新风", "G"),
    ("电气", "D"), ("强电", "D"), ("照明", "D"), ("配电", "D"), ("动力", "D"),
    ("弱电", "E"), ("智能", "E"), ("监控", "E"), ("综合布线", "E"),
    ("给排水", "K"), ("给水", "K"), ("排水", "K"), ("水暖", "K"),
    ("采暖", "K"), ("燃气", "K"),
    ("工业管道", "H"), ("工艺管道", "H"),
    ("仪表", "F"),
    ("通信", "L"),
    ("刷油", "M"), ("防腐", "M"), ("保温", "M"), ("绝热", "M"),
    ("机械", "A"), ("设备", "A"),
    ("热力", "B"),
]


def _sheet_name_to_appendix(sheet_name: str) -> str:
    """从sheet名推断所属附录。"""
    if not sheet_name:
        return ""
    text = sheet_name.lower()
    for keyword, appendix in _SHEET_NAME_ROUTES:
        if keyword in text:
            return appendix
    return ""


# ============================================================
# 项目特征库索引（按9位编码查找特征模板）
# ============================================================

_features_by_code = None  # 9位编码 → 项目特征数据


def _get_features_by_code() -> dict:
    """构建 9位编码→项目特征 的索引。"""
    global _features_by_code
    if _features_by_code is not None:
        return _features_by_code

    db = _load_features_db()
    _features_by_code = {}
    for item in db.get("items", []):
        code = item.get("code", "")
        if code and code not in _features_by_code:
            _features_by_code[code] = item
    return _features_by_code


def _lookup_features(code9: str) -> dict | None:
    """根据9位编码查找项目特征模板。"""
    fbc = _get_features_by_code()
    return fbc.get(code9)


# ============================================================
# 匹配核心逻辑
# ============================================================

def _route_appendix(name: str, description: str = "",
                    hint_major: str = "",
                    section_title: str = "") -> str:
    """根据名称和描述判断所属附录/专业大类。

    参数:
        name: 清单名称
        description: 描述
        hint_major: 专业大类提示（"01"房建/"03"安装/"04"市政/"05"园林）
        section_title: 分部标题提示（如"给排水工程"）

    返回值:
      - 安装工程: 附录字母 A-N（如 "K"=给排水、"D"=电气）
      - 非安装工程: 2位大类编码（如 "01"=房建、"04"=市政）
      - 未匹配: 空字符串
    """
    index = _load_bill_library_index()
    core = _extract_core_name(name)

    def _pick_best(candidates: list) -> str:
        """从候选列表中选最佳结果。

        消歧优先级：
        1. hint_major（来自sheet名）
        2. specialty_classifier（共享的专业分类器，看名称+描述的上下文）
        3. 默认取第一个（安装优先排序）
        """
        # 只有1个候选，不需要消歧
        if len(candidates) == 1:
            return candidates[0]["appendix"] or candidates[0]["major"]

        # 有大类提示（来自sheet名）：优先匹配对应大类
        if hint_major:
            for c in candidates:
                if c["major"] == hint_major:
                    return c["appendix"] or c["major"]

        # 多个候选且无提示 → 调用共享专业分类器消歧
        # 用清单的名称+描述做上下文判断，和套定额用同一套规则
        disambig_label = _disambiguate(name, description, section_title,
                                       candidates=candidates)
        if disambig_label:
            for c in candidates:
                label = c["appendix"] or c["major"]
                if label == disambig_label:
                    return label

        # 兜底：取第一个（安装优先排序）
        best = candidates[0]
        return best["appendix"] or best["major"]

    # 精确匹配
    if core in index:
        return _pick_best(index[core])

    # 同义词展开
    expanded = core
    for alias, standard in _load_bill_synonyms():
        if alias in core:
            expanded = core.replace(alias, standard)
            break
    if expanded != core and expanded in index:
        return _pick_best(index[expanded])

    # 去掉型号后重试精确+同义词
    stripped = _strip_model_prefix(core)
    if stripped != core:
        if stripped in index:
            return _pick_best(index[stripped])
        # 同义词展开
        expanded2 = stripped
        for alias, standard in _load_bill_synonyms():
            if alias in stripped:
                expanded2 = stripped.replace(alias, standard)
                break
        if expanded2 != stripped and expanded2 in index:
            return _pick_best(index[expanded2])

    # 模糊：检查输入名称是否包含在某个索引名称中，或反过来
    text = f"{name} {description}"
    best_result = ""
    best_len = 0
    for idx_name, candidates in index.items():
        if len(idx_name) < 2:
            continue
        if idx_name in text and len(idx_name) > best_len:
            best_result = _pick_best(candidates)
            best_len = len(idx_name)

    if best_result:
        return best_result

    # 兜底：所有索引搜索都没找到时，用分类器给出路由
    # 分类器用的是品类词路由表+TF-IDF+关键词，不依赖清单库索引
    fallback_label = _disambiguate(name, description, section_title)
    if fallback_label:
        return fallback_label

    return ""


def match_bill_code(name: str, description: str = "",
                    hint_appendix: str = "",
                    section_title: str = "") -> dict | None:
    """匹配清单编码。

    参数:
        name: 项目名称（如"镀锌钢管"、"消火栓钢管"）
        description: 项目特征描述（如"DN100 螺纹连接 室内"）
        hint_appendix: 提示附录字母（可选，如已知专业可直接指定）
        section_title: 分部标题（可选，用于消歧）

    返回:
        匹配结果dict，包含:
        {
            "code": "031001002",          # 9位国标编码（前9位，标准规定不能自编）
            "code_12": "031001002001",    # 完整12位编码（批量匹配时自动编后3位序号）
            "name": "镀锌钢管",            # 标准名称（项目特征库里的名称）
            "features": [...],            # 项目特征模板
            "unit": "m",                  # 计量单位
            "calc_rule": "...",           # 计算规则
            "work_content": [...],        # 工作内容
            "appendix": "K",             # 附录
            "section": "K.1",            # 节
            "match_score": 85.0,         # 匹配分数
            "match_method": "library",   # 匹配方式：library=清单库 / features=特征库
        }
        未匹配到返回 None
    """
    if not name:
        return None

    index = _load_bill_library_index()
    core = _extract_core_name(name)
    text = f"{name} {description}"

    # ---- 第1步：精确匹配 ----
    match = _find_in_index(index, core, text, hint_appendix,
                           bill_name=name, bill_desc=description,
                           section_title=section_title)

    # ---- 第2步：同义词展开后匹配 ----
    if not match:
        expanded = core
        for alias, standard in _load_bill_synonyms():
            if alias in core:
                expanded = core.replace(alias, standard)
                break
        if expanded != core:
            match = _find_in_index(index, expanded, text, hint_appendix,
                                   bill_name=name, bill_desc=description,
                                   section_title=section_title)

    # ---- 第3步：模糊匹配（子串搜索） ----
    if not match:
        match = _fuzzy_search(index, name, text, hint_appendix,
                              bill_desc=description,
                              section_title=section_title)

    # ---- 第4步：去掉型号后重试 ----
    # 名称带型号前缀（如"LT04四头格栅灯"），去掉后用纯中文部分重试
    # 放在模糊搜索之后，只在前3步全部失败时才用
    if not match:
        stripped = _strip_model_prefix(core)
        if stripped != core:
            match = _find_in_index(index, stripped, text, hint_appendix,
                                   bill_name=name, bill_desc=description,
                                   section_title=section_title)
            if not match:
                expanded2 = stripped
                for alias, standard in _load_bill_synonyms():
                    if alias in stripped:
                        expanded2 = stripped.replace(alias, standard)
                        break
                if expanded2 != stripped:
                    match = _find_in_index(index, expanded2, text, hint_appendix,
                                           bill_name=name, bill_desc=description,
                                           section_title=section_title)
            if not match:
                match = _fuzzy_search(index, stripped, text, hint_appendix,
                                      bill_desc=description,
                                      section_title=section_title)

    if not match:
        return None

    code9 = match["code9"]
    appendix = match["appendix"]
    major = match.get("major", code9[:2])
    score = match["score"]
    method = match["method"]

    # ---- 从项目特征库补全特征模板 ----
    feat = _lookup_features(code9)
    if feat:
        return {
            "code": code9,
            "name": feat.get("name", ""),
            "features": feat.get("features", []),
            "unit": feat.get("unit", ""),
            "calc_rule": feat.get("calc_rule", ""),
            "work_content": feat.get("work_content", []),
            "appendix": feat.get("appendix", appendix),
            "appendix_name": feat.get("appendix_name", ""),
            "section": feat.get("section", ""),
            "section_name": feat.get("section_name", ""),
            "major": major,
            "match_score": round(score, 1),
            "match_method": method,
        }
    else:
        # 清单库有但项目特征库没有（非安装编码、2013版编码等）
        return {
            "code": code9,
            "name": name,
            "features": [],
            "unit": "",
            "calc_rule": "",
            "work_content": [],
            "appendix": appendix,
            "appendix_name": "",
            "section": "",
            "section_name": "",
            "major": major,
            "match_score": round(score, 1),
            "match_method": method,
        }


def _find_in_index(index: dict, core: str, text: str,
                   hint_appendix: str = "",
                   bill_name: str = "", bill_desc: str = "",
                   section_title: str = "") -> dict | None:
    """在清单库索引中查找核心名称。

    返回: {"code9": ..., "appendix": ..., "major": ..., "score": ..., "method": ...} 或 None
    """
    if core not in index:
        return None

    candidates = index[core]

    # 如果有附录提示，优先匹配对应附录
    if hint_appendix:
        for c in candidates:
            if c["appendix"] == hint_appendix:
                return {"code9": c["code9"], "appendix": c["appendix"],
                        "major": c["major"], "score": 100.0, "method": "library_exact"}

    # 多候选时用专业分类器消歧（和套定额共享同一套规则）
    if len(candidates) > 1:
        disambig_label = _disambiguate(bill_name or core, bill_desc,
                                       section_title, candidates=candidates)
        if disambig_label:
            for c in candidates:
                label = c["appendix"] or c["major"]
                if label == disambig_label:
                    return {"code9": c["code9"], "appendix": c["appendix"],
                            "major": c["major"], "score": 98.0,
                            "method": "library_exact_disambig"}

    # 兜底：取出现次数最多的
    best = candidates[0]
    return {"code9": best["code9"], "appendix": best["appendix"],
            "major": best["major"], "score": 95.0, "method": "library_exact"}


def _fuzzy_search(index: dict, name: str, text: str,
                  hint_appendix: str = "",
                  bill_desc: str = "",
                  section_title: str = "") -> dict | None:
    """模糊搜索：用子串包含关系在索引中查找。

    策略：
    1. 索引名称包含在输入名称中（如索引有"钢管"，输入"镀锌钢管"）→ 取最长匹配
    2. 输入名称包含在索引名称中（如输入"配电箱"，索引有"成套配电箱"）→ 取最短索引名
    """
    best = None
    best_score = 0

    # 预先算一次消歧结果（避免在循环内反复调用）
    # 注意：这里不传candidates，因为每次循环的candidates不同
    # 在循环内使用时会检查当前candidates是否同大类内
    disambig_label = ""
    # 单独提取描述暗示词（比规则消歧更可靠，可跨大类使用）
    desc_hint_label = ""
    if not hint_appendix:
        disambig_label = _disambiguate(name, bill_desc, section_title)
        if bill_desc:
            desc_hint_label = _desc_specialty_hint(bill_desc)

    for idx_name, candidates in index.items():
        if len(idx_name) < 2:
            continue

        score = 0
        # 索引名称 在 输入名称中（部分匹配）
        if idx_name in name:
            # 匹配长度越长越好（"消火栓钢管"比"钢管"更精确）
            score = 60 + len(idx_name) * 3
        # 输入名称 在 索引名称中
        elif name in idx_name:
            score = 50 + len(name) * 2
        # 索引名称 在 完整文本中（含描述）
        elif idx_name in text:
            score = 30 + len(idx_name) * 2

        if score <= best_score:
            continue

        # 选候选：优先附录提示，其次描述暗示词，再次规则消歧，最后默认
        pick = candidates[0]
        if hint_appendix:
            for c in candidates:
                if c["appendix"] == hint_appendix:
                    pick = c
                    score += 10
                    break
        elif desc_hint_label and len(candidates) > 1:
            # 描述暗示词比规则消歧更可靠，不受同大类限制
            for c in candidates:
                label = c["appendix"] or c["major"]
                if label == desc_hint_label:
                    pick = c
                    score += 7  # 比规则消歧高，比hint_appendix低
                    break
        elif disambig_label and len(candidates) > 1:
            # 规则消歧：同大类和跨大类都尝试，但跨大类加分更低
            majors = set(c["major"] for c in candidates)
            bonus = 5 if len(majors) == 1 else 3  # 跨大类加分低（不太确定）
            for c in candidates:
                label = c["appendix"] or c["major"]
                if label == disambig_label:
                    pick = c
                    score += bonus
                    break

        best_score = score
        best = {"code9": pick["code9"], "appendix": pick["appendix"],
                "major": pick["major"], "score": score, "method": "library_fuzzy"}

    # 分数太低的不返回（避免乱匹配）
    if best and best_score < 40:
        return None

    return best


# ============================================================
# 邻居投票（batch匹配的第二遍扫描）
# ============================================================

def _neighbor_vote_pass(items: list[dict]):
    """邻居投票校正：用同section/sheet内邻居的匹配结果，校验消歧项。

    逻辑：
    - 第一遍匹配后，有些项目是通过"消歧"选出来的（method含"disambig"）
    - 这些项目的名称在多个专业都有，消歧可能选错
    - 看前后各5条邻居（同section或同sheet）匹配到了什么专业
    - 如果邻居大多数是同一个专业，且和当前不一致，就用邻居的专业重新匹配
    - 类似于"旁边都是给排水的，这个钢管大概率也是给排水"
    """
    corrected = 0

    for i, item in enumerate(items):
        bm = item.get("bill_match")
        if not bm:
            continue

        # 只对消歧结果做校验（精确匹配/模糊匹配的不动）
        method = bm.get("match_method", "")
        if "disambig" not in method:
            continue

        current_appendix = bm.get("appendix", "")
        if not current_appendix:
            continue

        section = item.get("section", "")
        sheet = item.get("sheet_name", "")

        # 收集邻居的专业投票（前后各5条）
        votes = {}  # appendix → 投票数
        for offset in range(-5, 6):
            if offset == 0:
                continue
            j = i + offset
            if j < 0 or j >= len(items):
                continue
            neighbor = items[j]
            n_bm = neighbor.get("bill_match")
            if not n_bm:
                continue

            # 同section/sheet检查（不同分部的邻居不参考）
            n_section = neighbor.get("section", "")
            n_sheet = neighbor.get("sheet_name", "")
            if section and n_section != section:
                continue
            if not section and sheet and n_sheet != sheet:
                continue

            # 只统计非消歧结果的投票（这些结果更可靠）
            n_method = n_bm.get("match_method", "")
            if "disambig" in n_method:
                continue

            n_app = n_bm.get("appendix", "")
            if n_app:
                votes[n_app] = votes.get(n_app, 0) + 1

        if not votes:
            continue

        # 投票最多的专业
        dominant = max(votes, key=votes.get)
        dominant_count = votes[dominant]
        total_votes = sum(votes.values())

        # 当前结果和邻居多数不一致，且邻居投票够强（>=3票 且 占比>=70%）
        if (dominant != current_appendix
                and dominant_count >= 3
                and dominant_count / total_votes >= 0.7):
            # 用邻居的专业作为hint重新匹配
            name = item.get("name", "").strip()
            desc = item.get("description", "").strip()
            new_result = match_bill_code(name, desc, hint_appendix=dominant)
            if new_result:
                new_result["match_method"] += "_neighbor"  # 标记来源
                item["bill_match"] = new_result
                corrected += 1
                logger.debug(f"邻居投票校正: {name} {current_appendix}→{dominant}")

    if corrected:
        logger.info(f"  邻居投票校正: {corrected}条消歧结果被邻居投票修正")


# ============================================================
# 批量匹配（供 bill_compiler 调用）
# ============================================================

def match_bill_codes(items: list[dict]) -> list[dict]:
    """批量匹配清单编码。

    对每条清单item，如果没有编码或编码不完整，尝试自动匹配。
    匹配结果写入 item["bill_match"] 字段。
    同一个9位国标编码下的多条清单项，自动编后3位序号（001、002…），
    组成完整12位清单编码。

    参数:
        items: 清单项列表（bill_reader输出的dict列表）

    返回:
        原列表（原地修改）
    """
    if not items:
        return items

    # 确保数据库已加载
    _load_features_db()
    _load_bill_library_index()

    matched = 0
    skipped = 0

    for item in items:
        code = item.get("code", "").strip()
        name = item.get("name", "").strip()
        desc = item.get("description", "").strip()

        # 已有完整12位编码 → 跳过（尊重用户输入）
        if code and re.match(r"^0[1-9]\d{10}$", code):
            skipped += 1
            continue
        # 已有完整9位编码（用户只填了前9位）→ 也跳过
        if code and re.match(r"^0[1-9]\d{7}$", code):
            skipped += 1
            continue

        # 没有名称 → 跳过
        if not name:
            continue

        # 从sheet名提取专业提示（如"电气"→D，"给排水"→K）
        sheet_name = item.get("sheet_name", "")
        hint = _sheet_name_to_appendix(sheet_name)

        # 分部标题（从Excel结构来的，用于消歧）
        # 注意：bill_reader存的字段名是"section"，不是"section_title"
        section = item.get("section", "") or item.get("section_title", "")

        # 匹配（传入分部标题，用于多义名称消歧）
        result = match_bill_code(name, desc, hint_appendix=hint,
                                 section_title=section)
        if result:
            item["bill_match"] = result
            matched += 1

    # ---- 邻居投票校正（第二遍扫描） ----
    # 对消歧决定的匹配结果，用邻居的匹配结果做投票校验
    _neighbor_vote_pass(items)

    # 给匹配结果编后3位序号，组成完整12位编码
    # 规则：同一个9位编码按出现顺序编001、002、003…
    code_counter = {}  # 9位编码 → 当前序号
    for item in items:
        bm = item.get("bill_match")
        if not bm:
            continue
        code9 = bm["code"]
        seq = code_counter.get(code9, 0) + 1
        code_counter[code9] = seq
        bm["code_12"] = f"{code9}{seq:03d}"  # 完整12位编码

    if matched > 0 or skipped > 0:
        logger.info(f"  清单编码匹配: {matched}条匹配成功, "
                    f"{skipped}条已有编码跳过, "
                    f"{len(items) - matched - skipped}条未匹配")

    return items
