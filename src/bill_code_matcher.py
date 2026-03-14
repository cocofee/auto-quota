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
                    hint_major: str = "") -> str:
    """根据名称和描述判断所属附录/专业大类。

    参数:
        name: 清单名称
        description: 描述
        hint_major: 专业大类提示（"01"房建/"03"安装/"04"市政/"05"园林）

    返回值:
      - 安装工程: 附录字母 A-N（如 "K"=给排水、"D"=电气）
      - 非安装工程: 2位大类编码（如 "01"=房建、"04"=市政）
      - 未匹配: 空字符串
    """
    index = _load_bill_library_index()
    core = _extract_core_name(name)

    def _pick_best(candidates: list) -> str:
        """从候选列表中选最佳结果，考虑hint_major。"""
        if hint_major:
            # 有大类提示：优先匹配对应大类
            for c in candidates:
                if c["major"] == hint_major:
                    return c["appendix"] or c["major"]
        # 无提示或提示无匹配：取第一个（安装优先排序）
        best = candidates[0]
        return best["appendix"] or best["major"]

    # 精确匹配
    if core in index:
        return _pick_best(index[core])

    # 同义词展开
    expanded = core
    for alias, standard in _SYNONYMS_SORTED:
        if alias in core:
            expanded = core.replace(alias, standard)
            break
    if expanded != core and expanded in index:
        return _pick_best(index[expanded])

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

    return best_result


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
    match = _find_in_index(index, core, text, hint_appendix)

    # ---- 第2步：同义词展开后匹配 ----
    if not match:
        expanded = core
        for alias, standard in _SYNONYMS_SORTED:
            if alias in core:
                expanded = core.replace(alias, standard)
                break
        if expanded != core:
            match = _find_in_index(index, expanded, text, hint_appendix)

    # ---- 第3步：模糊匹配（子串搜索） ----
    if not match:
        match = _fuzzy_search(index, name, text, hint_appendix)

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
                   hint_appendix: str = "") -> dict | None:
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
    # 无提示 → 取出现次数最多的
    best = candidates[0]
    return {"code9": best["code9"], "appendix": best["appendix"],
            "major": best["major"], "score": 95.0, "method": "library_exact"}


def _fuzzy_search(index: dict, name: str, text: str,
                  hint_appendix: str = "") -> dict | None:
    """模糊搜索：用子串包含关系在索引中查找。

    策略：
    1. 索引名称包含在输入名称中（如索引有"钢管"，输入"镀锌钢管"）→ 取最长匹配
    2. 输入名称包含在索引名称中（如输入"配电箱"，索引有"成套配电箱"）→ 取最短索引名
    """
    best = None
    best_score = 0

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

        # 如果有附录提示，匹配对应附录的候选加分
        pick = candidates[0]
        if hint_appendix:
            for c in candidates:
                if c["appendix"] == hint_appendix:
                    pick = c
                    score += 10
                    break

        best_score = score
        best = {"code9": pick["code9"], "appendix": pick["appendix"],
                "major": pick["major"], "score": score, "method": "library_fuzzy"}

    # 分数太低的不返回（避免乱匹配）
    if best and best_score < 40:
        return None

    return best


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

        # 匹配
        result = match_bill_code(name, desc, hint_appendix=hint)
        if result:
            item["bill_match"] = result
            matched += 1

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
