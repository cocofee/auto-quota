"""
全局配置文件
集中管理所有路径、参数、常量
"""

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

CURRENT_PROVINCE = "北京2024"

def get_province_db_dir(province=None):
    """获取指定省份的数据库目录"""
    province = province or CURRENT_PROVINCE
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
    province = province or CURRENT_PROVINCE
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

# 安装定额文件名（放在 data/quota_data/ 下）
QUOTA_EXCEL_FILES = {
    "安装": "C 通用安装工程_全部.xlsx",
    # "土建": "待导入.xlsx",    # 用户从广联达导出后添加
    # "市政": "待导入.xlsx",
}

# 定额Excel列映射（A=编号, B=名称+参数, C=单位, D=工作类型）
QUOTA_EXCEL_COLUMNS = {
    "id_col": 0,       # A列：定额编号
    "name_col": 1,     # B列：名称+特征参数
    "unit_col": 2,     # C列：计量单位
    "type_col": 3,     # D列：工作类型
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

# Claude配置
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# OpenAI配置
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
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
LLM_TIMEOUT = 30              # API调用超时（秒）
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

# ============================================================
# 初始化：确保必要目录存在
# ============================================================

def ensure_dirs():
    """创建所有必要的目录"""
    dirs = [
        DATA_DIR, QUOTA_DATA_DIR, EXPERIENCE_DIR, DICT_DIR,
        DB_DIR, COMMON_DB_DIR, PROVINCES_DB_DIR,
        get_province_db_dir(),  # 当前省份目录
        OUTPUT_DIR, KNOWLEDGE_DIR, LOG_DIR,
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

# 导入时自动创建目录
ensure_dirs()
