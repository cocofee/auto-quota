"""
广材网材料价格查询工具 v2

改进内容（Phase 1）：
1. 名称拆解：把"热浸锌镀锌钢管 DN70"拆成品名+规格
2. 非材料项拦截：措施费、安装费等自动跳过
3. 结果过滤+打分：单位/规格/品名三维打分，低分不取价
4. 输出新增3列：匹配状态、置信度、广材网可点击链接
5. 搜索缓存去重：同名材料只搜一次
6. JSON主材库缓存：查完自动存，下次先查库

用法：
    python tools/gldjc_price.py "材料清单.xlsx" --cookie "token=bearer xxx"
    python tools/gldjc_price.py "材料清单.xlsx" --cookie "xxx" --no-cache  # 强制重查
"""

import re
import json
import sys
import time
import random
import argparse
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote

import requests
import openpyxl
from openpyxl.styles import PatternFill, Font

# ========== 配置 ==========

# 广材网搜索页URL
SEARCH_URL = "https://www.gldjc.com/scj/so.html"

# 主材缓存文件路径
CACHE_FILE = Path(__file__).parent.parent / "data" / "material_prices.json"

# 缓存有效期（天）
CACHE_EXPIRE_DAYS = 30

# 请求头（模拟浏览器）
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Referer": "https://www.gldjc.com/",
}

# User-Agent池（随机选一个，避免固定UA被标记）
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
]


def _get_headers() -> dict:
    """每次请求随机选一个UA，其余头不变"""
    h = dict(HEADERS)
    h["User-Agent"] = random.choice(_UA_POOL)
    return h

# 非材料项关键词（出现这些词的行自动跳过，不查价）
NON_MATERIAL_KEYWORDS = [
    "措施", "改造", "安装费", "调试", "拆除", "修复", "运输",
    "脚手架", "接入费", "制作", "保护措施", "试压", "冲洗",
    "检测", "验收", "税金", "利润", "管理费", "规费",
    "临时设施", "文明施工", "夜间施工", "冬雨季施工",
    "二次搬运", "已完工程保护", "大型机械进出场",
]

# 单位兼容组（同组内的单位视为兼容）
# 注意：t和kg绝对不能放一组！差1000倍，m和m²也不能放一组
UNIT_COMPAT_GROUPS = [
    {"kg", "千克", "公斤"},                    # 重量-千克级
    {"t", "吨"},                               # 重量-吨级（和kg不兼容！差1000倍）
    {"个", "只", "套", "台", "件", "组", "块"},# 计件（造价里个/台/套常互换）
    {"m", "米"},                               # 长度
    {"m²", "㎡", "平方米"},                    # 面积（和m不兼容！）
    {"m³", "立方米"},                          # 体积
    {"根", "条", "支"},                        # 条状物
    {"桶", "瓶"},                              # 容器
    {"卷", "盘"},                              # 卷状物
    {"副", "付", "对"},                        # 成对计量
]

# 名称中的修饰词（拆解时去掉，只保留核心品名）
NOISE_PREFIXES = [
    # 镀锌/防腐类
    "热浸锌", "热浸镀锌", "热镀锌", "冷镀锌", "电镀锌",
    # 场所/位置
    "给水室外", "给水室内", "排水室外", "排水室内", "室外", "室内",
    "地下", "地上", "屋面", "楼层",
    # 品质/规格类
    "国标", "非标", "加厚", "普通", "优质", "标准", "一级", "二级",
    "A型", "B型", "C型", "I型", "II型",
    # 工艺类
    "焊接", "丝接", "螺纹", "法兰", "卡压", "沟槽", "承插", "热熔",
    "涂塑", "衬塑", "内衬",
    # 其他修饰
    "柔性", "刚性", "单壁", "双壁", "薄壁", "厚壁",
    "无缝", "有缝", "直缝",
    "阻燃", "耐火", "低烟无卤",
]

# 广材网品名映射（清单常见写法 → 广材网能搜到的关键词）
# 这张表解决"清单写A，广材网只认B"的问题
_GLDJC_NAME_MAP = {
    # 管材
    "衬塑PP-R钢管": "PPR管",
    "PP-R管": "PPR管",
    "PP-R给水管": "PPR给水管",
    "衬塑钢管": "衬塑钢管",
    "镀锌焊接钢管": "镀锌钢管",
    "镀锌无缝钢管": "镀锌钢管",
    "给水铸铁管": "铸铁管",
    "排水铸铁管": "铸铁排水管",
    "柔性铸铁管": "柔性铸铁排水管",
    "HDPE双壁波纹管": "HDPE波纹管",
    "HDPE排水管": "HDPE管",
    "UPVC排水管": "PVC排水管",
    "U-PVC排水管": "PVC排水管",
    "CPVC电力管": "CPVC管",
    "PE给水管": "PE管",
    "PE-RT地暖管": "PE-RT管",
    "铝塑复合管": "铝塑管",
    "薄壁不锈钢管": "不锈钢管",
    "焊接钢管": "焊接钢管",
    # 管件
    "弯头": "弯头",
    "三通": "三通",
    "异径管": "大小头",
    "变径": "大小头",
    # 阀门
    "截止阀": "截止阀",
    "闸阀": "闸阀",
    "蝶阀": "蝶阀",
    "止回阀": "止回阀",
    "球阀": "球阀",
    "减压阀": "减压阀",
    "平衡阀": "平衡阀",
    "过滤器": "过滤器",
    "倒流防止器": "倒流防止器",
    # 电气
    "电力电缆": "电力电缆",
    "控制电缆": "控制电缆",
    "BV电线": "BV线",
    "BV线": "BV线",
    "RVS双绞线": "RVS线",
    "金属线槽": "金属线槽",
    "桥架": "电缆桥架",
    "镀锌线管": "镀锌线管",
    "PVC线管": "PVC线管",
    "KBG管": "KBG管",
    "JDG管": "JDG管",
    "配电箱": "配电箱",
    "开关": "开关",
    "插座": "插座",
    # 消防
    "消防喷淋头": "喷淋头",
    "消火栓": "消火栓",
    "灭火器": "灭火器",
    "烟感探测器": "烟感",
    "温感探测器": "温感",
    # 暖通
    "风管": "镀锌风管",
    "镀锌钢板风管": "镀锌风管",
    "保温材料": "保温棉",
    "橡塑保温": "橡塑保温",
    "风口": "风口",
    "风阀": "风阀",
    # 五金/卫浴
    "蹲便器": "蹲便器",
    "坐便器": "坐便器",
    "洗脸盆": "洗脸盆",
    "水龙头": "水龙头",
    "地漏": "地漏",
    "角阀": "角阀",
}

# 规格提取正则（从名称中提取规格信息）
# 注意：顺序很重要，长模式放前面避免被短模式截断
SPEC_PATTERNS = [
    r'DN\d+',                  # 管径 DN25, DN100
    r'De\d+',                  # 外径 De25
    r'Φ\d+(?:mm)?',            # 直径 Φ10, Φ10mm
    r'\d+[×x\*]\d+(?:\.\d+)?(?:mm²?)?',  # 尺寸 100×100, 4x1.5, 2*1.5mm²（支持小数）
    r'\d+(?:\.\d+)?mm²',       # 截面 2.5mm², 50mm²（支持小数）
    r'\d+kV[A]?',              # 电压/容量 10kV, 630kVA
    r'\d+[AW]H?',              # 电流/功率/容量 100A, 500W, 24AH
    r'\d+V',                   # 电压 12V, 220V
    r'Q[0-9]+[A-Z]*',          # 牌号 Q235B
    r'HRB\d+',                 # 钢筋牌号 HRB400
]


# ========== 同义词加载 ==========

_engineering_synonyms: dict | None = None  # 懒加载缓存


def _load_engineering_synonyms() -> dict:
    """加载Jarvis工程同义词表（718条），懒加载只读一次"""
    global _engineering_synonyms
    if _engineering_synonyms is not None:
        return _engineering_synonyms
    syn_path = Path(__file__).parent.parent / "data" / "engineering_synonyms.json"
    if syn_path.exists():
        try:
            _engineering_synonyms = json.load(open(syn_path, encoding="utf-8"))
        except Exception:
            _engineering_synonyms = {}
    else:
        _engineering_synonyms = {}
    return _engineering_synonyms


def _get_gldjc_name(base_name: str, original: str) -> str | None:
    """从广材网品名映射表找到对应的广材网常用名

    先精确匹配base_name，再匹配original，再做包含匹配
    """
    # 精确匹配
    if base_name in _GLDJC_NAME_MAP:
        return _GLDJC_NAME_MAP[base_name]
    if original in _GLDJC_NAME_MAP:
        return _GLDJC_NAME_MAP[original]
    # 包含匹配（清单名包含映射表的key）
    for key, val in _GLDJC_NAME_MAP.items():
        if key in base_name or key in original:
            return val
    return None


def _get_synonym(base_name: str) -> str | None:
    """从Jarvis工程同义词表查一个别名

    同义词表格式: {"清单写法": ["定额写法1", ...]}
    这里取第一个定额写法作为搜索别名
    """
    syns = _load_engineering_synonyms()
    if not syns:
        return None
    # 精确匹配
    if base_name in syns:
        vals = syns[base_name]
        return vals[0] if vals else None
    # 包含匹配（清单名包含同义词key的核心部分）
    for key, vals in syns.items():
        if len(key) >= 3 and key in base_name and vals:
            return vals[0]
    return None


# ========== 名称拆解 ==========

def parse_material(name: str, spec_col: str = "") -> dict:
    """
    把材料名称拆成：基础品名 + 规格列表 + 搜索关键词

    例：
    "热浸锌镀锌钢管 DN70" → base_name="镀锌钢管", specs=["DN70"]
    "HDPE 三通 DN100"     → base_name="HDPE三通", specs=["DN100"]
    "T2纯紫铜 Φ10mm"      → base_name="紫铜", specs=["Φ10mm"]
    """
    original = name.strip()

    # 1. 从名称中提取规格
    specs = []
    clean_name = original
    for pattern in SPEC_PATTERNS:
        found = re.findall(pattern, clean_name, re.IGNORECASE)
        specs.extend(found)
        clean_name = re.sub(pattern, '', clean_name, flags=re.IGNORECASE)

    # 2. 从规格型号列也提取（如果有的话）
    if spec_col:
        for pattern in SPEC_PATTERNS:
            found = re.findall(pattern, spec_col, re.IGNORECASE)
            specs.extend(found)

    # 去重保序
    seen = set()
    unique_specs = []
    for s in specs:
        s_upper = s.upper()
        if s_upper not in seen:
            seen.add(s_upper)
            unique_specs.append(s)
    specs = unique_specs

    # 3. 去掉修饰词
    base_name = clean_name.strip()
    for noise in NOISE_PREFIXES:
        base_name = base_name.replace(noise, "")
    # 清理规格提取后残留的碎片（如"mm"、"-"、"."等）
    base_name = re.sub(r'[\s\-\.]+$', '', base_name)  # 去末尾残留符号
    base_name = re.sub(r'^[\s\-\.]+', '', base_name)  # 去开头残留符号
    base_name = re.sub(r'\s+', '', base_name).strip()  # 去多余空格

    # 4. 构建搜索关键词（分层降级，5层策略）
    # 第1优先：品名+核心规格（最精确）
    # 第2优先：纯品名（去掉规格的）
    # 第3优先：广材网品名映射+规格（清单写法→广材网写法）
    # 第4优先：广材网品名映射（纯映射名）
    # 第5优先：工程同义词+规格
    search_keywords = []

    if specs:
        search_keywords.append(f"{base_name} {specs[0]}")
    search_keywords.append(base_name)

    # 广材网品名映射：检查原名/品名是否有对应的广材网常用名
    mapped_name = _get_gldjc_name(base_name, original)
    if mapped_name and mapped_name != base_name:
        if specs:
            search_keywords.append(f"{mapped_name} {specs[0]}")
        search_keywords.append(mapped_name)

    # 工程同义词：从Jarvis的同义词表查一个别名
    synonym = _get_synonym(base_name)
    if synonym and synonym != base_name and synonym != mapped_name:
        if specs:
            search_keywords.append(f"{synonym} {specs[0]}")
        search_keywords.append(synonym)

    # 最后兜底：如果品名和原始名差异大，也加原始名
    if base_name != original and len(base_name) >= 2:
        search_keywords.append(original)

    # 去重保序
    seen_kw: set[str] = set()
    unique_kw: list[str] = []
    for kw in search_keywords:
        if kw not in seen_kw:
            seen_kw.add(kw)
            unique_kw.append(kw)
    search_keywords = unique_kw

    return {
        "original": original,
        "base_name": base_name,
        "specs": specs,
        "search_keywords": search_keywords,
    }


def is_non_material(name: str) -> bool:
    """
    判断是否为非材料项（措施费、安装费等不需要查价的项目）

    返回 True 表示应该跳过
    """
    for kw in NON_MATERIAL_KEYWORDS:
        if kw in name:
            return True
    return False


# ========== 结果过滤与打分 ==========

def check_unit_compatible(unit_a: str, unit_b: str) -> bool:
    """检查两个单位是否兼容（在同一个兼容组内）"""
    if not unit_a or not unit_b:
        return False
    a = unit_a.strip().lower()
    b = unit_b.strip().lower()
    if a == b:
        return True
    for group in UNIT_COMPAT_GROUPS:
        lower_group = {u.lower() for u in group}
        if a in lower_group and b in lower_group:
            return True
    return False


def check_spec_match(result_spec: str, target_specs: list[str]) -> bool:
    """检查搜索结果的规格描述是否包含目标规格"""
    if not target_specs:
        return True  # 没有目标规格，不做过滤
    spec_text = result_spec.upper()
    for ts in target_specs:
        if ts.upper() in spec_text:
            return True
    return False


def filter_and_score(results: list[dict], target_unit: str,
                     target_specs: list[str], base_name: str) -> list[dict]:
    """
    对广材网搜索结果进行过滤和打分

    打分规则：
    - 单位兼容：+40分（最重要，单位不对差几个数量级）
    - 规格命中：+30分（规格不对差几倍到几十倍）
    - 品名关键词命中：+20分
    - 基础分：+10分（搜到了就有基础分）

    返回：打分后的结果列表（只保留>=40分的），按分数降序
    """
    scored = []
    for r in results:
        score = 10  # 基础分

        # 单位打分（最关键）
        if target_unit and r.get("unit"):
            if check_unit_compatible(target_unit, r["unit"]):
                score += 40
            elif r["unit"].strip():
                # 单位明确不兼容，扣分
                score -= 20

        # 规格打分
        if check_spec_match(r.get("spec", ""), target_specs):
            score += 30

        # 品名关键词打分
        if base_name and len(base_name) >= 2:
            spec_text = r.get("spec", "")
            # 检查品名关键字是否出现在结果的规格描述中
            name_chars = set(base_name)
            match_count = sum(1 for c in name_chars if c in spec_text)
            if match_count >= len(name_chars) * 0.5:
                score += 20

        r["score"] = score
        scored.append(r)

    # 只保留>=40分的（至少单位要兼容）
    filtered = [r for r in scored if r["score"] >= 40]
    filtered.sort(key=lambda x: x["score"], reverse=True)
    return filtered


def determine_confidence(scored_results: list[dict], target_unit: str,
                         target_specs: list[str]) -> str:
    """
    根据过滤后的结果判断置信度

    高：单位一致 + 规格命中 + 多条报价
    中：单位一致但规格未命中，或只有1-2条报价
    低：其他情况
    """
    if not scored_results:
        return "低"

    top = scored_results[0]
    count = len(scored_results)

    if top["score"] >= 80 and count >= 3:
        return "高"
    elif top["score"] >= 50 and count >= 2:
        return "中"
    else:
        return "低"


# ========== 广材网搜索 ==========

def search_material_web(session: requests.Session, keyword: str,
                        province_code: str = "1") -> list[dict]:
    """
    在广材网搜索材料，从SSR渲染的HTML中提取价格数据

    返回: [{"spec": "镀锌焊接钢管|DN25", "brand": "贵盈",
            "market_price": 3663.72, "unit": "t"}, ...]
    """
    params = {"keyword": keyword, "l": province_code}

    try:
        resp = session.get(SEARCH_URL, params=params, headers=_get_headers(), timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [错误] 搜索'{keyword}'失败: {e}")
        return []

    html = resp.text

    # 检测是否被踢出登录（Cookie失效）
    if "请登录" in html or "login" in html.lower() and "token" not in html.lower():
        print("\n  [警告] Cookie可能已失效，建议更换Cookie后重试")

    # 从SSR渲染的HTML中提取结构化数据
    specs_raw = re.findall(r'class="m-detail-content"[^>]*>(.*?)</div>', html, re.DOTALL)
    specs = [re.sub(r'<[^>]+>', '', s).strip() for s in specs_raw]

    brands = re.findall(r'class="brand-box"[^>]*>\s*(\S+)\s*<div', html, re.DOTALL)

    price_matches = re.findall(
        r'class="price-block"[^>]*><span[^>]*class="change-point"[^>]*>([0-9.]+)</span>', html
    )

    units = re.findall(r'class="colspan-cell width-56 pure-text"[^>]*>\s*([^<\s]+)\s*</div>', html)

    results = []
    for i in range(len(price_matches)):
        try:
            price_val = float(price_matches[i])
        except ValueError:
            continue

        item = {
            "keyword": keyword,
            "spec": specs[i] if i < len(specs) else "",
            "brand": brands[i] if i < len(brands) else "",
            "unit": units[i] if i < len(units) else "",
            "market_price": price_val,
        }
        results.append(item)

    return results


def get_median_price(prices: list[dict], price_field: str = "market_price") -> float | None:
    """取中档价格：去掉最高和最低，取中间值"""
    valid_prices = [p[price_field] for p in prices if p.get(price_field)]
    if not valid_prices:
        return None

    valid_prices.sort()

    if len(valid_prices) <= 2:
        return round(sum(valid_prices) / len(valid_prices), 2)

    trimmed = valid_prices[1:-1]
    return round(sum(trimmed) / len(trimmed), 2)


def build_gldjc_url(keyword: str, province_code: str = "1") -> str:
    """构建广材网搜索链接（用户可点击查看）"""
    encoded = quote(keyword, safe='')
    return f"https://www.gldjc.com/scj/so.html?keyword={encoded}&l={province_code}"


# ========== JSON主材缓存 ==========

def load_cache() -> dict:
    """加载主材价格缓存"""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_cache(cache: dict):
    """保存主材价格缓存"""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_cache_key(name: str, unit: str) -> str:
    """生成缓存key（材料名+单位）"""
    return f"{name.strip()}|{unit.strip()}"


def check_cache(cache: dict, name: str, unit: str) -> dict | None:
    """
    查缓存：有且未过期返回缓存数据，否则返回None

    缓存结构：{
        "镀锌钢管 DN25|m": {
            "price_with_tax": 18.5,
            "price_without_tax": 16.37,
            "confidence": "高",
            "match_status": "精确匹配",
            "source": "广材网",
            "query_date": "2026-03-10",
            "result_count": 8,
            "match_detail": "搜到DN25镀锌钢管，单位m，8条报价"
        }
    }
    """
    key = get_cache_key(name, unit)
    if key not in cache:
        return None

    entry = cache[key]
    query_date = entry.get("query_date", "")
    if not query_date:
        return None

    # 检查是否过期
    try:
        cached_time = datetime.strptime(query_date, "%Y-%m-%d")
        if datetime.now() - cached_time > timedelta(days=CACHE_EXPIRE_DAYS):
            return None  # 过期
    except ValueError:
        return None

    return entry


def update_cache(cache: dict, name: str, unit: str, data: dict):
    """更新缓存"""
    key = get_cache_key(name, unit)
    cache[key] = data


# ========== Excel输出 ==========

# Excel背景色
FILL_GREEN = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")   # 高置信度
FILL_YELLOW = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")  # 需核对
FILL_RED = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")      # 搜不到
FILL_BLUE = PatternFill(start_color="BDD7EE", end_color="BDD7EE", fill_type="solid")    # 非材料项
FONT_LINK = Font(color="0563C1", underline="single")  # 超链接样式


def process_excel(input_path: str, cookie_str: str, output_path: str = None,
                  province_code: str = "1", delay: float = 3.0,
                  use_cache: bool = True):
    """
    读取Excel材料清单，逐个查询广材网价格，写回Excel

    v2改进：
    - 名称拆解+规格提取
    - 单位/规格过滤
    - 非材料项拦截
    - 置信度标注
    - 广材网链接
    - JSON缓存
    """
    input_file = Path(input_path)
    if not input_file.exists():
        print(f"文件不存在: {input_path}")
        return

    if not output_path:
        output_path = str(input_file.with_stem(input_file.stem + "_含价格"))

    # 读取Excel
    wb = openpyxl.load_workbook(input_path)
    ws = wb.active

    # 找到列索引（自动识别列名）
    header_row = 1
    col_map = {}
    for col_idx, cell in enumerate(ws[header_row], 1):
        val = str(cell.value or "").strip()
        if val in ("材料名称", "名称", "项目名称"):
            col_map["name"] = col_idx
        elif val in ("规格型号", "规格", "型号"):
            col_map["spec"] = col_idx
        elif val in ("单位", "计量单位"):
            col_map["unit"] = col_idx
        elif val in ("含税市场价", "市场价", "含税价"):
            col_map["price_with_tax"] = col_idx
        elif val in ("不含税市场价", "不含税价", "除税价"):
            col_map["price_without_tax"] = col_idx
        elif val == "税率":
            col_map["tax_rate"] = col_idx

    if "name" not in col_map:
        print("未找到'材料名称'列，请检查Excel表头")
        return

    # 新增输出列：匹配状态、置信度、广材网链接
    max_col = ws.max_column
    if "price_with_tax" not in col_map:
        col_map["price_with_tax"] = max_col + 1
        ws.cell(row=header_row, column=col_map["price_with_tax"], value="含税市场价")
        max_col += 1
    if "price_without_tax" not in col_map:
        col_map["price_without_tax"] = max_col + 1
        ws.cell(row=header_row, column=col_map["price_without_tax"], value="不含税市场价")
        max_col += 1

    # 新增3列
    col_map["match_status"] = max_col + 1
    ws.cell(row=header_row, column=col_map["match_status"], value="匹配状态")
    col_map["confidence"] = max_col + 2
    ws.cell(row=header_row, column=col_map["confidence"], value="置信度")
    col_map["gldjc_link"] = max_col + 3
    ws.cell(row=header_row, column=col_map["gldjc_link"], value="广材网链接")

    # 创建session，注入cookie
    session = requests.Session()
    for part in cookie_str.split("; "):
        if "=" in part:
            key, value = part.split("=", 1)
            session.cookies.set(key.strip(), value.strip())

    # 加载缓存
    cache = load_cache() if use_cache else {}

    # 搜索结果缓存（本次运行内去重，同名材料只搜一次）
    search_cache = {}

    # 统计
    total = ws.max_row - 1
    stats = {"成功": 0, "缓存命中": 0, "未匹配": 0, "非材料项": 0, "需核对": 0, "已有价格": 0}

    print(f"开始查询 {total} 条材料价格（v2：带过滤+置信度+链接）")
    print(f"缓存：{'启用' if use_cache else '禁用'}，缓存条数：{len(cache)}")
    print(f"每次请求间隔 {delay} 秒（防止被封）")
    print()

    for row_idx in range(2, ws.max_row + 1):
        name = str(ws.cell(row=row_idx, column=col_map["name"]).value or "").strip()
        spec_col = str(ws.cell(row=row_idx, column=col_map.get("spec", col_map["name"])).value or "").strip()
        unit = str(ws.cell(row=row_idx, column=col_map.get("unit", col_map["name"])).value or "").strip()

        if not name:
            continue

        current = row_idx - 1
        display_name = name.encode('gbk', errors='replace').decode('gbk')

        # 跳过已经有价格的行
        existing_price = ws.cell(row=row_idx, column=col_map["price_with_tax"]).value
        if existing_price and str(existing_price).strip():
            print(f"  [{current}/{total}] {display_name} → 已有价格，跳过")
            stats["已有价格"] += 1
            continue

        # 非材料项拦截
        if is_non_material(name):
            print(f"  [{current}/{total}] {display_name} → 非材料项，跳过")
            ws.cell(row=row_idx, column=col_map["match_status"], value="非材料项")
            ws.cell(row=row_idx, column=col_map["confidence"], value="-")
            # 蓝色背景
            for col in [col_map["match_status"], col_map["confidence"]]:
                ws.cell(row=row_idx, column=col).fill = FILL_BLUE
            stats["非材料项"] += 1
            continue

        # 查缓存
        if use_cache:
            cached = check_cache(cache, name, unit)
            if cached:
                price_tax = cached.get("price_with_tax")
                price_notax = cached.get("price_without_tax")
                conf = cached.get("confidence", "中")
                status = cached.get("match_status", "缓存")

                if price_tax and conf != "低":
                    ws.cell(row=row_idx, column=col_map["price_with_tax"], value=price_tax)
                    ws.cell(row=row_idx, column=col_map["price_without_tax"], value=price_notax)
                    ws.cell(row=row_idx, column=col_map["match_status"], value=f"{status}(缓存)")
                    ws.cell(row=row_idx, column=col_map["confidence"], value=conf)
                    fill = FILL_GREEN if conf == "高" else FILL_YELLOW
                    for col in [col_map["price_with_tax"], col_map["price_without_tax"],
                                col_map["match_status"], col_map["confidence"]]:
                        ws.cell(row=row_idx, column=col).fill = fill
                    print(f"  [{current}/{total}] {display_name} → 缓存命中 {price_tax}元")
                    stats["缓存命中"] += 1
                    continue

        # 名称拆解
        parsed = parse_material(name, spec_col)
        base_name = parsed["base_name"]
        specs = parsed["specs"]

        # 广材网链接（不管能不能查到价格，都给链接）
        link_keyword = f"{base_name} {specs[0]}" if specs else base_name
        link_url = build_gldjc_url(link_keyword, province_code)

        # 分层搜索：先精确（品名+规格），再宽泛（纯品名）
        all_results = []
        searched_keyword = ""
        for kw in parsed["search_keywords"]:
            if kw in search_cache:
                # 本次运行内去重
                all_results = search_cache[kw]
                searched_keyword = kw
                break

            print(f"  [{current}/{total}] 搜索: {kw.encode('gbk', errors='replace').decode('gbk')}...",
                  end=" ", flush=True)

            results = search_material_web(session, kw, province_code)
            search_cache[kw] = results
            searched_keyword = kw

            if results:
                all_results = results
                break

            # 没搜到，降级到下一个关键词
            print("未找到，降级...", end=" ", flush=True)
            time.sleep(delay * 0.5 + random.uniform(0, 0.5))

        if not all_results:
            # 所有关键词都没搜到
            print("全部未找到")
            ws.cell(row=row_idx, column=col_map["match_status"], value="未匹配")
            ws.cell(row=row_idx, column=col_map["confidence"], value="低")
            # 写入链接
            link_cell = ws.cell(row=row_idx, column=col_map["gldjc_link"])
            link_cell.value = link_url
            link_cell.hyperlink = link_url
            link_cell.font = FONT_LINK
            # 红色背景
            for col in [col_map["match_status"], col_map["confidence"]]:
                ws.cell(row=row_idx, column=col).fill = FILL_RED
            stats["未匹配"] += 1
            # 缓存"未匹配"结果（避免下次再搜）
            update_cache(cache, name, unit, {
                "price_with_tax": None,
                "price_without_tax": None,
                "confidence": "低",
                "match_status": "未匹配",
                "source": "广材网",
                "query_date": datetime.now().strftime("%Y-%m-%d"),
                "result_count": 0,
                "searched_keyword": searched_keyword,
            })
            time.sleep(delay + random.uniform(0, 1))
            continue

        # 过滤+打分
        scored = filter_and_score(all_results, unit, specs, base_name)
        confidence = determine_confidence(scored, unit, specs)

        # 用过滤后的结果取价（如果过滤后没有合格的，用原始结果但降低置信度）
        price_source = scored if scored else all_results
        if not scored:
            confidence = "低"

        median = get_median_price(price_source)

        # 生成匹配说明
        if scored:
            top = scored[0]
            match_detail = f"搜到{top.get('spec', '')[:20]}，单位{top.get('unit', '?')}，{len(scored)}条报价"
        else:
            match_detail = f"搜到{len(all_results)}条但单位/规格不匹配"

        # 根据置信度决定是否填价
        match_status = ""
        if confidence == "高":
            match_status = "精确匹配"
        elif confidence == "中":
            match_status = "模糊匹配"
        else:
            match_status = "低置信度"

        # 高/中置信度填价，低置信度不填价只给链接
        if confidence in ("高", "中") and median:
            tax_rate = 1.13  # 默认13%增值税
            # 如果Excel里有税率列，用Excel里的
            if "tax_rate" in col_map:
                tr = ws.cell(row=row_idx, column=col_map["tax_rate"]).value
                if tr and float(tr) > 0:
                    tax_rate = 1 + float(tr) / 100 if float(tr) > 1 else 1 + float(tr)

            price_notax = round(median / tax_rate, 2)
            ws.cell(row=row_idx, column=col_map["price_with_tax"], value=median)
            ws.cell(row=row_idx, column=col_map["price_without_tax"], value=price_notax)

            fill = FILL_GREEN if confidence == "高" else FILL_YELLOW
            print(f"含税 {median} 元（{confidence}，{len(price_source)}条报价）")
            stats["成功"] += 1

            # 更新缓存
            update_cache(cache, name, unit, {
                "price_with_tax": median,
                "price_without_tax": price_notax,
                "confidence": confidence,
                "match_status": match_status,
                "source": "广材网",
                "query_date": datetime.now().strftime("%Y-%m-%d"),
                "result_count": len(price_source),
                "match_detail": match_detail,
                "searched_keyword": searched_keyword,
            })
        else:
            # 低置信度：不填价格，只给链接
            fill = FILL_RED
            print(f"低置信度，不填价（{match_detail}）")
            stats["需核对"] += 1

            update_cache(cache, name, unit, {
                "price_with_tax": median,  # 存着但不写入Excel
                "price_without_tax": round(median / 1.13, 2) if median else None,
                "confidence": "低",
                "match_status": match_status,
                "source": "广材网",
                "query_date": datetime.now().strftime("%Y-%m-%d"),
                "result_count": len(price_source),
                "match_detail": match_detail,
                "searched_keyword": searched_keyword,
            })

        # 写匹配状态和置信度
        ws.cell(row=row_idx, column=col_map["match_status"], value=match_status)
        ws.cell(row=row_idx, column=col_map["confidence"], value=confidence)

        # 写广材网链接（所有行都给，方便核对）
        link_cell = ws.cell(row=row_idx, column=col_map["gldjc_link"])
        link_cell.value = link_url
        link_cell.hyperlink = link_url
        link_cell.font = FONT_LINK

        # 背景色
        for col in [col_map["match_status"], col_map["confidence"]]:
            ws.cell(row=row_idx, column=col).fill = fill
        if confidence in ("高", "中"):
            for col in [col_map["price_with_tax"], col_map["price_without_tax"]]:
                ws.cell(row=row_idx, column=col).fill = fill

        # 随机延迟（只在真正发了请求时才延迟）
        if searched_keyword not in search_cache or len(search_cache) <= 1:
            time.sleep(delay + random.uniform(0, 1))

        # 每10条自动保存
        done = sum(v for v in stats.values())
        if done % 10 == 0 and done > 0:
            wb.save(output_path)
            if use_cache:
                save_cache(cache)

    # 最终保存
    wb.save(output_path)
    if use_cache:
        save_cache(cache)

    print()
    print("=" * 50)
    print(f"查询完成！结果保存到: {output_path}")
    print(f"  成功（自动填价）：{stats['成功']} 条")
    print(f"  缓存命中：{stats['缓存命中']} 条")
    print(f"  需核对（低置信度，给了链接）：{stats['需核对']} 条")
    print(f"  未匹配（搜不到）：{stats['未匹配']} 条")
    print(f"  非材料项（自动跳过）：{stats['非材料项']} 条")
    print(f"  已有价格（跳过）：{stats['已有价格']} 条")
    print(f"  主材缓存：{len(cache)} 条")
    print()
    print("颜色说明：")
    print("  绿色 = 高置信度，可直接用")
    print("  黄色 = 中置信度，建议点链接核对")
    print("  红色 = 低置信度/未找到，请手动查价")
    print("  蓝色 = 非材料项，已跳过")


def main():
    parser = argparse.ArgumentParser(description="广材网材料价格查询工具 v2")
    parser.add_argument("excel", help="材料清单Excel文件路径")
    parser.add_argument("--cookie", required=True,
                        help="广材网cookie字符串（至少包含token=bearer xxx）")
    parser.add_argument("--output", "-o",
                        help="输出Excel路径（默认原文件名_含价格）")
    parser.add_argument("--province", default="1",
                        help="省份代码（1=全国，默认）")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="请求间隔秒数（默认3秒）")
    parser.add_argument("--no-cache", action="store_true",
                        help="不使用缓存，强制重新查询广材网")

    args = parser.parse_args()
    process_excel(args.excel, args.cookie, args.output,
                  args.province, args.delay, not args.no_cache)


if __name__ == "__main__":
    main()
