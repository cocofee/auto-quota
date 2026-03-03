"""
全局配置文件
集中管理所有路径、参数、常量
"""

import contextvars
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载.env文件中的API密钥等敏感配置
load_dotenv()

# ============================================================
# 路径配置
# ============================================================

# 项目根目录
PROJECT_ROOT = Path(__file__).parent

# 数据目录
DATA_DIR = PROJECT_ROOT / "data"
QUOTA_DATA_DIR = DATA_DIR / "quota_data"      # 定额Excel源文件
EXPERIENCE_DIR = DATA_DIR / "experience"       # 已完成项目（训练数据）
DICT_DIR = DATA_DIR / "dict"                   # jieba专业词典

# 数据库目录
DB_DIR = PROJECT_ROOT / "db"
COMMON_DB_DIR = DB_DIR / "common"              # 通用数据（经验库等）
PROVINCES_DB_DIR = DB_DIR / "provinces"         # 省份数据（定额库等）

# 输出目录
OUTPUT_DIR = PROJECT_ROOT / "output"

# 知识库目录（第二阶段）
KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"

# ============================================================
# 当前省份/版本配置（默认北京2024）
# ============================================================

# 默认省份常量（命令行和旧代码的回退值）
CURRENT_PROVINCE = "北京市建设工程施工消耗量标准(2024)"

# 运行时省份（用 contextvars 实现线程安全，支持多请求并发场景）
_runtime_province: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    '_runtime_province', default=None)


def set_current_province(name: str):
    """设置运行时的当前省份（Web界面切换时调用）"""
    _runtime_province.set(name)


def get_current_province() -> str:
    """获取当前生效的省份名称

    优先级：运行时设置 > 硬编码默认值
    Web界面通过 set_current_province() 设置，命令行通过 --province 参数传递
    """
    return _runtime_province.get() or CURRENT_PROVINCE


def get_province_db_dir(province=None):
    """获取指定省份的数据库目录"""
    province = province or get_current_province()
    return PROVINCES_DB_DIR / province

def get_quota_db_path(province=None):
    """获取定额SQLite数据库路径"""
    return get_province_db_dir(province) / "quota.db"

def _safe_dir_name(name: str) -> str:
    """将含中文的目录名转为ASCII安全名（ChromaDB在Windows上不支持中文路径）"""
    import hashlib
    # 保留字母数字，其余用哈希替代
    ascii_part = "".join(c for c in name if c.isascii() and c.isalnum())
    if ascii_part == name:
        return name  # 纯ASCII无需处理
    # 用原名的哈希前8位保证唯一性
    hash_suffix = hashlib.md5(name.encode()).hexdigest()[:8]
    return f"{ascii_part}_{hash_suffix}" if ascii_part else f"p_{hash_suffix}"

def get_chroma_quota_dir(province=None):
    """获取定额向量数据库目录（使用ASCII安全路径，避免Windows中文路径问题）"""
    province = province or get_current_province()
    safe_name = _safe_dir_name(province)
    new_path = DB_DIR / "chroma" / f"{safe_name}_quota"

    # 兼容迁移：如果旧路径存在而新路径不存在，自动迁移
    old_path = get_province_db_dir(province) / "chroma_quota"
    if old_path.exists() and not new_path.exists():
        try:
            import shutil
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(old_path), str(new_path))
        except Exception as e:
            # 迁移失败时回退到旧路径，不中断系统启动
            import logging
            logging.getLogger(__name__).warning(f"向量库迁移失败({e})，继续使用旧路径: {old_path}")
            return old_path

    return new_path

def get_experience_db_path():
    """获取经验库SQLite数据库路径"""
    return COMMON_DB_DIR / "experience.db"

def get_universal_kb_path():
    """获取通用知识库SQLite数据库路径"""
    return COMMON_DB_DIR / "universal_kb.db"

def get_chroma_experience_dir():
    """获取经验库向量数据库目录（使用ASCII安全路径）"""
    new_path = DB_DIR / "chroma" / "common_experience"

    # 兼容迁移：旧路径存在而新路径不存在时自动迁移
    old_path = COMMON_DB_DIR / "chroma_experience"
    if old_path.exists() and not new_path.exists():
        try:
            import shutil
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(old_path), str(new_path))
        except Exception as e:
            # 迁移失败时回退到旧路径，不中断系统启动
            import logging
            logging.getLogger(__name__).warning(f"经验库向量迁移失败({e})，继续使用旧路径: {old_path}")
            return old_path

    return new_path

def get_chroma_universal_kb_dir():
    """获取通用知识库向量数据库目录"""
    return DB_DIR / "chroma" / "common_universal_kb"

def get_current_quota_version(province=None):
    """获取当前定额库的版本号（供经验库写入时绑定）

    经验库每次写入时会带上这个版本号，
    这样当定额库重新导入后，旧经验能被识别出"基于旧版定额"并降级处理。
    """
    from src.quota_db import QuotaDB
    try:
        db = QuotaDB(province)
        return db.get_version()
    except Exception:
        return ""

# ============================================================
# 定额Excel文件配置
# ============================================================

# 定额数据按省份分目录存放：data/quota_data/{province}/
# 每个目录下放该省份的定额Excel文件（广联达导出格式）
# specialty（专业）从Excel的D列自动识别，不需要手动配置

def get_quota_data_dir(province=None):
    """获取指定省份的定额Excel源文件目录

    支持两种目录结构：
    1. 扁平: data/quota_data/北京2024/
    2. 嵌套: data/quota_data/北京/北京2024/
    优先匹配扁平结构，找不到再扫描子目录
    """
    province = province or get_current_province()
    # 扁平结构：直接匹配
    flat_path = QUOTA_DATA_DIR / province
    if flat_path.exists() and flat_path.is_dir():
        return flat_path
    # 嵌套结构：在所有子目录中查找
    if QUOTA_DATA_DIR.exists():
        for parent in QUOTA_DATA_DIR.iterdir():
            if parent.is_dir():
                nested = parent / province
                if nested.exists() and nested.is_dir():
                    return nested
    # 都没找到，返回扁平路径（让调用方报错）
    return flat_path


def list_db_provinces():
    """扫描 db/provinces/ 下所有已构建的省份数据库目录

    返回省份名称列表（即目录名），如 ['北京2024消耗量', '北京2021消耗量', ...]
    跳过空目录和测试目录（无 quota.db 或定额条数为0的目录）。
    """
    provinces = []
    if not PROVINCES_DB_DIR.exists():
        return provinces
    for item in sorted(PROVINCES_DB_DIR.iterdir()):
        if item.is_dir():
            quota_db = item / "quota.db"
            if not quota_db.exists():
                continue  # 无数据库文件，跳过（测试残留的空目录）
            # 检查是否有实际定额数据
            try:
                import sqlite3
                conn = sqlite3.connect(str(quota_db))
                count = conn.execute("SELECT COUNT(*) FROM quotas").fetchone()[0]
                conn.close()
                if count == 0:
                    continue  # 空数据库，跳过
            except Exception:
                continue  # 表不存在或损坏，跳过
            provinces.append(item.name)
    return provinces


def get_sibling_provinces(main_province: str) -> list[str]:
    """查找与主定额库同省份、同年份的兄弟库（自动挂载用）

    例如：输入"宁夏安装工程计价定额(2019)"
    返回：["宁夏市政工程计价定额(2019)", "宁夏房屋建筑装饰工程计价定额(2019)", ...]

    分组规则：提取省份名（前2-3字）+ 年份（括号中4位数字），两者都相同才算同批。
    """
    import re

    def _extract_region_year(name: str):
        """从定额库名提取(省份, 年份)"""
        # 年份：括号中的4位数字
        year_match = re.search(r'[（(](\d{4})[)）]', name)
        year = year_match.group(1) if year_match else ''
        # 省份：前2-3个汉字（遇到省/市/回族等截断）
        region_match = re.match(
            r'^([\u4e00-\u9fff]{2,3}?)(省|市|回族|壮族|维吾尔)', name)
        region = region_match.group(1) if region_match else name[:2]
        return region, year

    main_region, main_year = _extract_region_year(main_province)
    if not main_year:
        return []  # 无法提取年份，不做自动挂载

    siblings = []
    for p in list_db_provinces():
        if p == main_province:
            continue  # 跳过自己
        r, y = _extract_region_year(p)
        if r == main_region and y == main_year:
            siblings.append(p)

    return siblings


def _split_keywords(text: str) -> list[str]:
    """将用户输入拆成关键词列表（按中文/数字/字母的自然边界拆分）

    省份名通常由"地区(2字)+专业关键词(2字)+年份(4位)"组成，
    按字符类型边界拆分后，对连续中文再按2字一组细分。

    例如:
        "北京2024"    → ["北京", "2024"]
        "广东安装"    → ["广东", "安装"]
        "湖北市政2024" → ["湖北", "市政", "2024"]
        "修缮"       → ["修缮"]
    """
    import re
    # 按字符类型边界拆分：连续中文、连续数字、连续字母各为一组
    tokens = re.findall(r'[\u4e00-\u9fff]+|[0-9]+|[a-zA-Z]+', text)

    keywords = []
    for token in tokens:
        # 纯中文且超过2字：按2字一组拆分（"广东安装" → "广东"+"安装"）
        if len(token) > 2 and all('\u4e00' <= c <= '\u9fff' for c in token):
            for i in range(0, len(token), 2):
                part = token[i:i+2]
                if len(part) >= 2:
                    keywords.append(part)
        else:
            keywords.append(token)

    return keywords if keywords else [text]


def resolve_province(name: str = None, interactive: bool = False,
                     scope: str = "db") -> str:
    """将用户输入的省份简称解析为完整的省份目录名

    解析优先级:
    1. 精确匹配
    2. 子串匹配：输入是某个目录名的子串，且唯一匹配
    3. 多关键词匹配：按中文/数字边界拆分输入，全部命中才算匹配
    4. 多个匹配：交互模式下让用户选择，否则报错
    5. 无匹配：报错并列出所有可用省份

    参数:
        name: 用户输入的省份名称/简称，None 表示使用默认值或交互选择
        interactive: 是否允许交互式选择（命令行场景为True）
        scope: "db" 搜索已构建的数据库（匹配用），"data" 搜索源文件目录（导入用）

    返回: 完整的省份目录名

    示例:
        resolve_province("北京2024")  → "北京市建设工程施工消耗量标准(2024)"
        resolve_province("广东安装")  → "广东省通用安装工程综合定额(2018)"
        resolve_province("修缮")      → "北京市房屋修工程预算消耗量标准(2021)"
        resolve_province("湖北市政2024") → "湖北省市政工程消耗量定额及全费用基价表(2024)"
    """
    available = list_db_provinces() if scope == "db" else list_all_provinces()

    if not available:
        # 导入场景下无源文件目录时，保留原始输入做兼容（旧版扁平结构）
        if scope == "data":
            # 有名称则原样返回；无名称则回退当前默认省份（兼容旧版目录）
            return name if name else get_current_province()
        raise ValueError("没有找到任何省份数据库，请先导入定额数据")

    # 未指定省份：交互模式下让用户选，否则优先当前默认，找不到则回退第一个可用省份
    if not name:
        if interactive and len(available) > 1:
            return _interactive_select(available)
        current = get_current_province()
        if current in available:
            return current
        return available[0]

    # 1. 精确匹配
    if name in available:
        return name

    # 2. 子串匹配（输入是目录名的子串）
    matches = [p for p in available if name in p]

    # 3. 多关键词匹配（按中文/数字/字母的自然边界拆分，全部命中才算）
    # 例如 "广东安装" → ["广东", "安装"]，匹配 "广东省通用安装工程综合定额(2018)"
    if not matches:
        keywords = _split_keywords(name)
        if len(keywords) > 1:
            matches = [p for p in available if all(kw in p for kw in keywords)]

    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        if interactive:
            print(f"\n'{name}' 匹配到多个省份：")
            return _interactive_select(matches)
        # 非交互模式，返回详细错误
        options = "\n  ".join(matches)
        raise ValueError(f"'{name}' 匹配到多个省份:\n  {options}\n请输入更精确的名称")
    else:
        options = "\n  ".join(available)
        raise ValueError(f"找不到省份 '{name}'，可用省份:\n  {options}")


def _interactive_select(provinces: list) -> str:
    """交互式选择省份（命令行菜单）"""
    # 默认省份排在最前面
    if CURRENT_PROVINCE in provinces:
        provinces = [CURRENT_PROVINCE] + [p for p in provinces if p != CURRENT_PROVINCE]

    print("\n请选择省份定额库:")
    for i, p in enumerate(provinces):
        default_mark = " (默认)" if p == CURRENT_PROVINCE else ""
        print(f"  [{i + 1}] {p}{default_mark}")

    while True:
        try:
            choice = input(f"\n输入编号 [1-{len(provinces)}]，直接回车选默认: ").strip()
            if not choice:
                # 回车 = 选默认
                result = CURRENT_PROVINCE if CURRENT_PROVINCE in provinces else provinces[0]
                print(f"  → 已选择: {result}")
                return result
            idx = int(choice) - 1
            if 0 <= idx < len(provinces):
                print(f"  → 已选择: {provinces[idx]}")
                return provinces[idx]
            print(f"  无效编号，请输入 1-{len(provinces)}")
        except (ValueError, EOFError):
            print(f"  无效输入，请输入 1-{len(provinces)}")


def list_all_provinces():
    """扫描所有可用的省份/版本

    支持扁平和嵌套两种目录结构，返回省份名称列表。
    """
    provinces = []
    if not QUOTA_DATA_DIR.exists():
        return provinces
    for item in sorted(QUOTA_DATA_DIR.iterdir()):
        if not item.is_dir():
            continue
        # 检查是否直接含xlsx（扁平结构）
        has_xlsx = any(item.glob("*.xlsx"))
        if has_xlsx:
            provinces.append(item.name)
        else:
            # 检查子目录（嵌套结构）
            for sub in sorted(item.iterdir()):
                if sub.is_dir() and any(sub.glob("*.xlsx")):
                    provinces.append(sub.name)
    return provinces


def get_province_groups() -> dict[str, str]:
    """获取每个定额库的分组名（来自 data/quota_data/ 的父文件夹名）

    返回 {定额库名: 分组名} 的映射，如 {"石化安装预算2019": "石油", "石油预算2022": "石油"}
    嵌套结构下父文件夹名即分组名；扁平结构（无父文件夹）用定额库名前2字作为分组。
    特殊处理：新疆-* 文件夹统一归入"新疆"分组（地区信息通过 get_province_subgroups 获取）。
    同时扫描 db/provinces/ 下已有但不在 data/ 中的定额库，用前2字兜底。
    """
    groups = {}
    # 从 data/quota_data/ 读取真实文件夹分组
    if QUOTA_DATA_DIR.exists():
        for item in sorted(QUOTA_DATA_DIR.iterdir()):
            if not item.is_dir():
                continue
            has_xlsx = any(item.glob("*.xlsx"))
            if has_xlsx:
                # 扁平结构：无父文件夹，用名称前2字
                groups[item.name] = item.name[:2]
            else:
                # 嵌套结构：父文件夹名即分组
                # 新疆-乌鲁木齐 → 分组为"新疆"（不是"新疆-乌鲁木齐"）
                group_name = "新疆" if item.name.startswith("新疆-") else item.name
                for sub in sorted(item.iterdir()):
                    if sub.is_dir():
                        groups[sub.name] = group_name

    # 补充 db/provinces/ 下已构建但不在 data/ 中的定额库
    for name in list_db_provinces():
        if name not in groups:
            groups[name] = name[:2]

    return groups


def get_province_subgroups() -> dict[str, str]:
    """获取新疆等省份的子分组（地区名）

    返回 {定额库名: 地区名} 的映射，仅包含有子分组的定额库。
    例如 {"全统安装工程消耗量定额乌鲁木齐估价汇总表(2020)": "乌鲁木齐"}
    """
    subgroups = {}
    if QUOTA_DATA_DIR.exists():
        for item in sorted(QUOTA_DATA_DIR.iterdir()):
            if not item.is_dir():
                continue
            # 只处理 新疆-{地区名} 格式的文件夹
            if item.name.startswith("新疆-"):
                region = item.name[3:]  # "新疆-乌鲁木齐" → "乌鲁木齐"
                for sub in sorted(item.iterdir()):
                    if sub.is_dir():
                        subgroups[sub.name] = region
    return subgroups

# 兼容旧代码：保留QUOTA_EXCEL_FILES但标记为废弃
QUOTA_EXCEL_FILES = {
    "安装": "C 通用安装工程_全部.xlsx",
}

# 定额Excel列映射（A=编号, B=名称+参数, C=单位, D=工作类型）
QUOTA_EXCEL_COLUMNS = {
    "id_col": 0,       # A列：定额编号
    "name_col": 1,     # B列：名称+特征参数
    "unit_col": 2,     # C列：计量单位
    "type_col": 3,     # D列：工作类型（"安装"/"土建"/"市政"等，用于自动识别specialty）
}

# ============================================================
# 搜索引擎配置
# ============================================================

# 向量搜索配置
VECTOR_MODEL_NAME = "BAAI/bge-large-zh-v1.5"  # BGE中文向量模型
VECTOR_TOP_K = 20                               # 向量搜索返回Top K
VECTOR_WEIGHT = 0.7                             # 混合搜索中向量的权重（参考OpenClaw的70/30配比）

# BM25搜索配置
BM25_TOP_K = 20                                 # BM25搜索返回Top K
BM25_WEIGHT = 0.3                               # 混合搜索中BM25的权重

# 混合搜索配置
HYBRID_TOP_K = 20                               # 混合搜索最终返回Top K
RRF_K = 60                                      # RRF融合排序的常数k（标准值60）

# 自适应融合与多查询增强（训练无关，适合快速演进）
HYBRID_ADAPTIVE_FUSION = True                   # 根据query特征动态调整BM25/向量权重
HYBRID_MULTI_QUERY_FUSION = True                # 使用多查询变体进行RRF融合
HYBRID_QUERY_VARIANTS = 4                       # 最多使用几个query变体（含核心名词变体）
HYBRID_VARIANT_WEIGHTS = [1.0, 0.75, 0.60, 0.50] # 各query变体在RRF中的权重
HYBRID_ADAPTIVE_BOOST = 0.18                    # 动态权重偏移幅度（0~0.4更稳妥）

# 级联搜索质量门槛：主搜候选较少时，检查top分差是否足够大
# 值含义：top1与top3的hybrid_score分差比例，低于此值则继续全库搜索
# 0.3表示top1至少比top3高30%的分数才认为搜索结果确定
CASCADE_QUALITY_THRESHOLD = 0.3
HYBRID_FEEDBACK_ADAPTIVE_BIAS = True            # 从用户修正/确认数据学习全局权重偏置
HYBRID_FEEDBACK_BIAS_MAX = 0.08                 # 反馈偏置最大幅度（避免震荡）
HYBRID_FEEDBACK_BIAS_REFRESH_SEC = 300          # 偏置缓存刷新周期（秒）
HYBRID_FEEDBACK_MIN_SAMPLES = 60                # 启用偏置的最小样本数

# Reranker重排配置（交叉编码器，精度远高于向量搜索）
RERANKER_MODEL_NAME = "BAAI/bge-reranker-v2-m3" # 中文重排模型（568M参数，FP16约2GB显存）
RERANKER_TOP_K = 20                              # 重排后保留的候选数（不截断，让param_validator精确筛选）

# ============================================================
# 大模型API配置
# ============================================================

# 默认使用的模型
DEFAULT_LLM = os.getenv("DEFAULT_LLM", "deepseek")

# DeepSeek配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = "deepseek-chat"

# Claude配置（支持中转服务）
# 注意：用CLAUDE_前缀而非ANTHROPIC_前缀，避免和Claude Code自身环境变量冲突
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
CLAUDE_BASE_URL = os.getenv("CLAUDE_BASE_URL", "")  # 中转地址，留空=官方API
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")

# OpenAI配置
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = "gpt-4o"

# 通义千问(Qwen)配置 —— 阿里云DashScope
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")

# Kimi配置 —— 通过阿里云DashScope代理访问
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-k2.5")

# API调用配置
LLM_MAX_RETRIES = 3           # API调用最大重试次数
LLM_TIMEOUT = 90              # API调用超时（秒）
LLM_CONCURRENT = 5            # 并发调用数（批量匹配时）

# ============================================================
# 多Agent动态路由配置（渐进式纠偏）
# ============================================================

# 第1层：经验库直通的置信度阈值
EXPERIENCE_DIRECT_THRESHOLD = 90   # 经验库命中且置信度>90%时直接返回

# 第2层：单Agent匹配后，跳过多Agent的置信度阈值
SINGLE_AGENT_THRESHOLD = 85        # 单Agent置信度>85%时仅做参数快速验证

# 第3层：触发多Agent纠偏的阈值
MULTI_AGENT_THRESHOLD = 85         # 低于此值启动多Agent（参数审核+规则审核+裁判）

# 最终置信度标记
CONFIDENCE_GREEN = 85     # 绿色：自动确认
CONFIDENCE_YELLOW = 60    # 黄色：建议人工确认
# 低于60%：红色，需人工处理

# ============================================================
# Agent（造价员贾维斯）配置
# ============================================================

# Agent默认使用的大模型（开发阶段用Claude/DeepSeek，生产阶段可切换）
AGENT_LLM = os.getenv("AGENT_LLM", "deepseek")

# Agent大模型温度（低温度=更确定性，高温度=更创造性）
AGENT_TEMPERATURE = 0.1

# Agent快速通道（高置信候选时跳过LLM，显著提速）
# 默认值说明：
#   SCORE=0.60 → param_score≥0.6即可走快通道（绝大多数条目跳过LLM）
#     - 1.0="无参数可验证/完美匹配" → 走快通道
#     - 0.6-0.7="部分匹配/有档位未确认" → 走快通道（如质量不达标可调高到0.85）
#     - <0.6="参数不匹配" → 走LLM
#   SCORE_GAP=0.03 → top1和top2的reranker分差至少0.03才走快通道
#     - reranker分数是0~1归一化的（bge-reranker-v2-m3经sigmoid）
#     - 分差<0表示reranker和参数验证器意见不一致 → 必须走LLM
#     - 分差<0.03表示基本平局 → 走LLM
#     - 分差≥0.03表示reranker明确偏好top1 → 可走快通道
#   AUDIT_RATE=0.15 → 15%抽检率，监控快通道质量（不一致时以LLM为准）
#   REQUIRE_PARAM_MATCH → 清单有参数但top1无参数时强制走LLM（避免无参数候选盲区）
AGENT_FASTPATH_ENABLED = os.getenv("AGENT_FASTPATH_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "off", ""
)
AGENT_FASTPATH_SCORE = float(os.getenv("AGENT_FASTPATH_SCORE", "0.60"))
AGENT_FASTPATH_MARGIN = float(os.getenv("AGENT_FASTPATH_MARGIN", "0.03"))
AGENT_FASTPATH_SCORE_GAP = float(os.getenv("AGENT_FASTPATH_SCORE_GAP", "0.03"))
AGENT_FASTPATH_AUDIT_RATE = max(0.0, min(
    1.0, float(os.getenv("AGENT_FASTPATH_AUDIT_RATE", "0.15"))
))
# 清单有参数但top1无参数时，强制走LLM（避免无参数候选因语义得分高而盲通）
AGENT_FASTPATH_REQUIRE_PARAM_MATCH = True

# 低置信度重试：Agent返回confidence低于此值时，自动用AI建议的搜索词重试
# 或当AI推荐的定额不在候选列表中时也触发重试
LOW_CONFIDENCE_RETRY_THRESHOLD = 70

# ============================================================
# L3 一致性反思（同类清单定额一致性检查）
# ============================================================
# 匹配全部完成后，检查同类清单是否套了同一个定额，不一致时用加权投票纠正。
# 纯Python后处理，不调LLM，耗时可忽略。

# 总开关（True=启用，False=跳过）
REFLECTION_ENABLED = True

# 投票决策阈值：winner票权至少是runner-up的多少倍才纠正
# 低于此比例的只标记冲突，不强制纠正
REFLECTION_MIN_VOTE_RATIO = 1.5

# 高置信度保护：置信度>=此值的结果不被反思纠正
# 经验库精确匹配通常>=90，不应被搜索结果投票推翻
REFLECTION_SKIP_HIGH_CONFIDENCE = 90

# 纠正后的置信度扣分（提示用户关注）
REFLECTION_CONFIDENCE_PENALTY = 5

# ============================================================
# L5 跨省迁移学习
# ============================================================

# 通用知识库自动同步（用户修正时自动同步定额名称到全国通用知识库）
UNIVERSAL_KB_SYNC_ENABLED = True

# 跨省置信度预热（新省份无经验时，查其他省份经验作为搜索参考）
CROSS_PROVINCE_WARMUP_ENABLED = True

# 跨省搜索的最低相似度门槛（比省内0.75高，降低误匹配风险）
CROSS_PROVINCE_MIN_SIMILARITY = 0.80

# 跨省搜索的最低置信度（只用高置信的权威数据）
CROSS_PROVINCE_MIN_CONFIDENCE = 85


# ============================================================
# L6 Agent瘦身
# ============================================================

# 是否在Agent prompt中注入规则知识（关闭后由代码校验替代，节省~300-500 tokens/条）
AGENT_RULES_IN_PROMPT = False

# 是否在Agent prompt中注入方法卡片（关闭后策略已融入固定提示词，节省~200-400 tokens/条）
AGENT_METHOD_CARDS_IN_PROMPT = False

# ============================================================
# LLM后验证（匹配后逐条审核纠正）
# ============================================================

# 是否启用LLM后验证（启用后每条匹配结果都会经过LLM审核）
LLM_VERIFY_ENABLED = os.getenv("LLM_VERIFY_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "off", ""
)

# 验证使用的大模型（空=沿用AGENT_LLM，支持匹配和验证用不同模型）
# 例如：匹配用千问(AGENT_LLM=qwen)，验证用Kimi(VERIFY_LLM=kimi)
VERIFY_LLM = os.getenv("VERIFY_LLM", "")

# 验证模型的具体型号（空=使用该厂商的默认型号）
# 例如：VERIFY_LLM=kimi + VERIFY_MODEL=kimi-k2.5
# 不填则自动读取对应厂商的默认型号（如KIMI_MODEL）
VERIFY_MODEL = os.getenv("VERIFY_MODEL", "")

# 跳过验证的置信度阈值（高于此值不验证，节省API费用）
# 88=只验证中低置信度项（推荐），100=全部验证
VERIFY_SKIP_THRESHOLD = int(os.getenv("VERIFY_SKIP_THRESHOLD", "88"))

# 验证并发数（并行验证多条，加速验证阶段）
VERIFY_CONCURRENT = int(os.getenv("VERIFY_CONCURRENT", "8"))

# 验证任务的max_tokens（验证输出很短，不需要1500）
VERIFY_MAX_TOKENS = int(os.getenv("VERIFY_MAX_TOKENS", "200"))

# 验证任务的超时时间（秒，验证比匹配简单，不需要90秒）
VERIFY_TIMEOUT = int(os.getenv("VERIFY_TIMEOUT", "30"))

# 绿灯抽检率（置信度>=阈值的也随机抽检一部分，保底质量监控）
VERIFY_SPOT_CHECK_RATE = float(os.getenv("VERIFY_SPOT_CHECK_RATE", "0.05"))

# 批量审核模式（中置信度项打包一次LLM调用，减少API调用次数）
AGENT_BATCH_ENABLED = True              # 是否启用批量审核
AGENT_BATCH_SIZE = 8                     # 每批最多几条
AGENT_BATCH_MIN_SCORE = 0.45             # 候选top1 param_score >= 此值才走批量（低于走逐条）

# ============================================================
# L7 搜索召回率优化
# ============================================================

# 经验库模糊匹配（通过 normalized_text 字段，容忍空格/标点/DN格式差异）
EXPERIENCE_FUZZY_MATCH_ENABLED = True

# 自动同义词表（从经验库挖掘的同义词，由 tools/synonym_miner.py 生成）
AUTO_SYNONYMS_ENABLED = True

# BM25 同义词扩展变体（在混合搜索中增加同义词反向替换变体）
BM25_SYNONYM_EXPANSION_ENABLED = True

# 学习笔记数据库路径
def get_learning_notes_db_path():
    return COMMON_DB_DIR / "learning_notes.db"

# 进化规则数据库路径（第二期实现）
def get_evolved_rules_db_path():
    return COMMON_DB_DIR / "evolved_rules.db"

# ============================================================
# jieba分词配置
# ============================================================

# 工程造价专业词典路径
ENGINEERING_DICT_PATH = DICT_DIR / "engineering_dict.txt"

# ============================================================
# 日志配置
# ============================================================

LOG_DIR = PROJECT_ROOT / "logs"
LOG_LEVEL = "INFO"

# 上传安全限制（Web页面）
UPLOAD_MAX_MB = 30

# ============================================================
# 初始化：确保必要目录存在
# ============================================================

def ensure_dirs():
    """创建所有必要的目录（不含省份目录，省份目录在导入时按需创建）"""
    dirs = [
        DATA_DIR, QUOTA_DATA_DIR, EXPERIENCE_DIR, DICT_DIR,
        DB_DIR, COMMON_DB_DIR, PROVINCES_DB_DIR,
        OUTPUT_DIR, KNOWLEDGE_DIR, LOG_DIR,
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

# 导入时自动创建目录
ensure_dirs()
