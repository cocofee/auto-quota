"""
定额规则自动提取脚本

功能：扫描定额数据库的所有章节，自动识别"定额家族"和匹配规则，
      生成结构化JSON规则文件。

核心算法：
1. 逐章节读取定额（按编号排序，保证连续性）
2. 正则提取每条定额名称末尾的参数值（数字、φ值、复合参数等）
3. 相同前缀的连续定额 → 归为同一家族
4. 正则提取失败的 → 用最长公共前缀判断是否同家族
5. 独立定额（前后都不匹配）→ 标记为单条规则

输出：
- JSON规则文件: data/quota_rules/北京2024_安装定额规则.json
- 人可读摘要: data/quota_rules/北京2024_安装定额规则_摘要.txt
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.quota_db import QuotaDB


def natural_sort_key(quota_id: str):
    """
    自然排序键：让 "C4-1-2" 排在 "C4-1-10" 前面

    原理：把编号中的数字部分转为整数，非数字部分保持字符串
    "C4-1-10" → ["C", 4, "-", 1, "-", 10]
    "C4-1-2"  → ["C", 4, "-", 1, "-", 2]
    这样 2 < 10，排序正确
    """
    parts = re.split(r'(\d+)', quota_id)
    result = []
    for p in parts:
        if p.isdigit():
            result.append(int(p))
        else:
            result.append(p)
    return result


# ============ 参数提取正则 ============
# 从定额名称末尾提取参数值，按优先级从高到低尝试

PARAM_PATTERNS = [
    # 格式1：复合参数 — 如 "4×70", "50/57", "3×2.5", "70+35"
    (r'^(.+?)\s+([\d.]+[×xX*/+][\d.]+(?:[×xX*/+][\d.]+)*)$', 'compound'),

    # 格式2：带φ/Φ的参数 — 如 "φ350", "Φ100"
    (r'^(.+?)\s+([φΦ][\d.]+)$', 'phi'),

    # 格式3：带范围的参数 — 如 "2~4", "10~20"
    (r'^(.+?)\s+([\d.]+[~\-][\d.]+)$', 'range'),

    # 格式4：纯数字参数（最常见）— 如 "630", "2.5", "100"
    (r'^(.+?)\s+([\d.]+)$', 'numeric'),
]

# 参数名称提取正则 — 从前缀中提取参数说明
# 如 "容量(kVA以内)" → param_name="容量", param_unit="kVA", param_rule="以内"
PARAM_DESC_PATTERN = re.compile(
    r'(.+?)\s*[\(（]([^\)）]*?)[\)）]\s*$'
)

# 参数单位和规则提取
# 如 "kVA以内" → unit="kVA", rule="以内"
PARAM_UNIT_RULE = re.compile(
    r'^([A-Za-zΩ²³㎡㎥]+|mm|cm|m|kg|t|kW|kVA|kV|A|VA)?'
    r'\s*(以内|以上|以下)?$'
)


def extract_tail_param(name: str) -> dict:
    """
    从定额名称中提取末尾参数

    参数:
        name: 定额完整名称，如 "油浸电力变压器安装 容量(kVA以内) 630"

    返回:
        {
            "prefix": "油浸电力变压器安装 容量(kVA以内)",
            "value": "630",
            "value_type": "numeric",
            "success": True
        }
        或 {"prefix": name, "value": None, "value_type": None, "success": False}
    """
    name = name.strip()

    for pattern, vtype in PARAM_PATTERNS:
        m = re.match(pattern, name)
        if m:
            prefix = m.group(1).strip()
            value = m.group(2).strip()

            # 防止误匹配：前缀太短（<2个字符）或纯数字前缀
            if len(prefix) < 2:
                continue

            return {
                "prefix": prefix,
                "value": value,
                "value_type": vtype,
                "success": True,
            }

    # 所有正则都没匹配上 → 可能是文字参数或独立定额
    return {
        "prefix": name,
        "value": None,
        "value_type": None,
        "success": False,
    }


def extract_param_info(prefix: str) -> dict:
    """
    从家族前缀中提取参数名称和单位

    参数:
        prefix: 如 "油浸电力变压器安装 容量(kVA以内)"

    返回:
        {"param_name": "容量", "param_unit": "kVA", "param_rule": "以内"}
        或 {"param_name": None, "param_unit": None, "param_rule": None}
    """
    m = PARAM_DESC_PATTERN.search(prefix)
    if not m:
        return {"param_name": None, "param_unit": None, "param_rule": None}

    base = m.group(1).strip()  # noqa: F841 — 暂时不用，但保留以备扩展
    desc = m.group(2).strip()  # 如 "kVA以内", "mm以内", "回路以内"

    # 从描述中分离单位和规则
    m2 = PARAM_UNIT_RULE.match(desc)
    if m2:
        unit = m2.group(1) or ""
        rule = m2.group(2) or ""
    else:
        # 尝试更宽泛的匹配
        # 如 "回路以内" → param_name可能包含在desc中
        if "以内" in desc:
            parts = desc.split("以内")
            unit = parts[0].strip()
            rule = "以内"
        elif "以上" in desc:
            parts = desc.split("以上")
            unit = parts[0].strip()
            rule = "以上"
        else:
            unit = desc
            rule = ""

    # 提取参数名称 — 从前缀末尾的括号前面找
    # "油浸电力变压器安装 容量(kVA以内)" → "容量"
    # 找括号前最后一个中文词
    prefix_before_paren = prefix[:prefix.rfind('(') if '(' in prefix else
                                  prefix.rfind('（') if '（' in prefix else
                                  len(prefix)]
    prefix_before_paren = prefix_before_paren.strip()
    # 取最后一个空格后的内容
    parts = prefix_before_paren.rsplit(None, 1)
    param_name = parts[-1] if len(parts) > 1 else None

    return {
        "param_name": param_name,
        "param_unit": unit if unit else None,
        "param_rule": rule if rule else None,
    }


def longest_common_prefix(s1: str, s2: str) -> str:
    """计算两个字符串的最长公共前缀"""
    min_len = min(len(s1), len(s2))
    i = 0
    while i < min_len and s1[i] == s2[i]:
        i += 1
    return s1[:i]


def is_same_family_by_prefix(name1: str, name2: str, threshold: float = 0.5) -> bool:
    """
    用最长公共前缀判断两条定额是否属于同一家族

    判断标准：公共前缀 ≥ 较短名称长度的 threshold 比例

    参数:
        name1, name2: 两条定额名称
        threshold: 相似度阈值（默认0.5，即50%）

    返回:
        True/False
    """
    lcp = longest_common_prefix(name1, name2)
    shorter = min(len(name1), len(name2))
    if shorter == 0:
        return False
    return len(lcp) / shorter >= threshold


def extract_keywords(name: str) -> list:
    """
    从定额名称中提取关键词

    简单实现：按标点和空格分词，过滤掉纯数字和太短的词
    """
    # 去掉括号内容和数字参数
    clean = re.sub(r'[\(（][^\)）]*[\)）]', '', name)
    clean = re.sub(r'\s+[\d.×xX*/+φΦ]+$', '', clean)

    # 中文分词（简单版：按常见分隔符切分）
    tokens = re.split(r'[\s,，、/]', clean)
    keywords = []
    for t in tokens:
        t = t.strip()
        if len(t) >= 2 and not re.match(r'^[\d.]+$', t):
            keywords.append(t)

    return keywords


def group_quotas_into_families(quotas: list) -> list:
    """
    将一个章节的定额分组为家族

    算法：
    1. 对每条定额提取末尾参数
    2. 相同prefix的连续定额 → 同一家族
    3. 提取失败的 → 用最长公共前缀判断
    4. 独立定额 → 标记为standalone

    参数:
        quotas: 定额列表（已按quota_id排序），每项包含 quota_id, name, unit 等字段

    返回:
        家族列表，每个家族是一个dict
    """
    if not quotas:
        return []

    # 第1步：对每条定额提取参数
    parsed = []
    for q in quotas:
        p = extract_tail_param(q["name"])
        parsed.append({
            "quota_id": q["quota_id"],
            "name": q["name"],
            "unit": q.get("unit", ""),
            "prefix": p["prefix"],
            "value": p["value"],
            "value_type": p["value_type"],
            "param_extracted": p["success"],
        })

    # 第2步：分组 — 相邻行的prefix相同则归为同一组
    groups = []
    current_group = [parsed[0]]

    for i in range(1, len(parsed)):
        prev = parsed[i - 1]
        curr = parsed[i]

        same_family = False

        # 情况A：两条都提取到了参数，且前缀完全相同
        if prev["param_extracted"] and curr["param_extracted"]:
            if prev["prefix"] == curr["prefix"]:
                same_family = True

        # 情况B：至少有一条没提取到参数 → 用最长公共前缀判断
        if not same_family:
            # 特殊情况：如果当前行提取到参数，但前一行没有（或反过来），
            # 可能是"文字子类型"变化，如 "开关 单联" → "开关 双联"
            # 用前缀比较判断
            if is_same_family_by_prefix(prev["name"], curr["name"], threshold=0.5):
                # 额外检查：单位必须相同（不同单位通常不是同一家族）
                if prev["unit"] == curr["unit"]:
                    same_family = True

        if same_family:
            current_group.append(curr)
        else:
            groups.append(current_group)
            current_group = [curr]

    # 别忘了最后一组
    groups.append(current_group)

    # 第3步：将分组转换为家族结构
    families = []
    for group in groups:
        family = build_family(group)
        families.append(family)

    return families


def build_family(group: list) -> dict:
    """
    将一组定额构建为家族结构

    参数:
        group: 同一家族的定额列表

    返回:
        家族dict
    """
    if len(group) == 1:
        # 独立定额
        q = group[0]
        result = {
            "type": "standalone",
            "name": q["name"],
            "quota_id": q["quota_id"],
            "unit": q["unit"],
            "keywords": extract_keywords(q["name"]),
        }
        # 提取结构化属性（连接方式、材质、是否保温/人防/防爆）
        attrs = extract_family_attrs(q["name"])
        if any(v for v in attrs.values()):  # 有非空/非False属性才写入
            result["attrs"] = attrs
        return result

    # 多条定额的家族
    # 找出共同前缀
    first = group[0]

    # 如果所有成员都提取到了参数且prefix相同 → 用提取的prefix
    all_extracted = all(g["param_extracted"] for g in group)
    if all_extracted and len(set(g["prefix"] for g in group)) == 1:
        prefix = first["prefix"]
        values = [g["value"] for g in group]
        value_type = first["value_type"]
    else:
        # 用最长公共前缀
        prefix = group[0]["name"]
        for g in group[1:]:
            prefix = longest_common_prefix(prefix, g["name"])
        prefix = prefix.rstrip()  # 去掉末尾空格
        # 值就是去掉公共前缀后的部分
        values = [g["name"][len(prefix):].strip() for g in group]
        value_type = "text"

    # 提取参数信息
    param_info = extract_param_info(prefix)

    # 尝试将值转为数字（用于排序和档位分析）
    numeric_tiers = []
    for v in values:
        try:
            numeric_tiers.append(float(v))
        except (ValueError, TypeError):
            pass

    # 构建家族名称（去掉参数描述部分）
    family_name = prefix
    # 去掉末尾的参数说明，如 " 容量(kVA以内)"
    m = re.search(r'\s+\S*[\(（][^\)）]*[\)）]\s*$', family_name)
    if m:
        family_name = family_name[:m.start()].strip()

    # 定额编号范围
    quota_ids = [g["quota_id"] for g in group]

    result = {
        "type": "family",
        "name": family_name,
        "prefix": prefix,
        "quota_range": f"{quota_ids[0]} ~ {quota_ids[-1]}",
        "count": len(group),
        "unit": first["unit"],
        "keywords": extract_keywords(prefix),
        "values": values,
        "value_type": value_type,
        "quotas": [
            {"id": g["quota_id"], "value": g["value"] or g["name"][len(prefix):].strip()}
            for g in group
        ],
    }

    # 添加参数信息（如果提取到了）
    if param_info["param_name"]:
        result["param_name"] = param_info["param_name"]
    if param_info["param_unit"]:
        result["param_unit"] = param_info["param_unit"]
    if param_info["param_rule"]:
        result["param_rule"] = param_info["param_rule"]

    # 添加数值档位（如果有）
    if len(numeric_tiers) == len(values) and numeric_tiers:
        result["tiers"] = numeric_tiers

    # 提取结构化属性（连接方式、材质、是否保温/人防/防爆）
    attrs = extract_family_attrs(family_name)
    if any(v for v in attrs.values()):  # 有非空/非False属性才写入
        result["attrs"] = attrs

    return result


# ============ 家族属性自动提取 ============
# 从家族名称中提取结构化属性（连接方式、材质、是否保温/人防/防爆）
# 用于规则匹配时的多维过滤

# 连接方式关键词（按长度降序排列，优先匹配更具体的）
CONNECTION_PATTERNS = [
    ("焊接对夹式法兰", "焊接对夹式法兰"),
    ("螺纹法兰", "螺纹法兰"),
    ("焊接法兰", "焊接法兰"),
    ("螺纹连接", "螺纹"),
    ("沟槽连接", "沟槽"),
    ("卡压连接", "卡压"),
    ("承插连接", "承插"),
    ("热熔连接", "热熔"),
    ("电熔连接", "电熔"),      # 钢骨架塑料复合管用电熔连接
    ("热熔焊", "热熔"),
    ("熔接", "热熔"),
    ("粘接", "粘接"),
    ("卡箍", "卡箍"),
    ("电熔", "电熔"),          # 单独的"电熔"放在后面
    ("热熔", "热熔"),          # 如"水平地埋塑料管热熔安装"
    # 单独的"法兰"要放在后面，且排除"法兰液压式"等阀门名称
    ("法兰", "法兰"),
    # 名称中直接出现的连接方式前缀（如"螺纹阀门"、"外螺纹阀门"）
    ("螺纹", "螺纹"),
    ("焊接", "焊接"),          # 如"室内钢管(焊接)"
]

# 材质关键词（按长度降序排列，优先匹配更具体的）
MATERIAL_PATTERNS = [
    ("薄壁不锈钢管", "不锈钢管"),
    ("钢塑复合管", "钢塑"),
    ("铝塑复合管", "铝塑"),
    ("塑铝稳态管", "塑铝"),        # 塑铝稳态管（PPR-AL-PPR）
    ("塑料复合管", "塑料复合管"),  # 钢骨架塑料复合管、塑铝稳态管等
    ("镀锌钢管", "镀锌钢管"),
    ("焊接钢管", "焊接钢管"),
    ("无缝钢管", "无缝钢管"),
    ("不锈钢管", "不锈钢管"),
    ("不锈钢", "不锈钢"),
    ("球墨铸铁", "铸铁"),
    ("铸铁管", "铸铁"),
    ("铸铁", "铸铁"),
    ("玻璃钢", "玻璃钢"),
    ("塑料管", "塑料管"),
    ("钢塑", "钢塑"),
    ("铝塑", "铝塑"),
    ("铜管", "铜"),
    ("铜制", "铜"),
    ("碳钢", "碳钢"),
    ("镀锌", "镀锌钢管"),
    ("钢管", "钢管"),          # 通用钢管（放在最后，作为兜底）
]


def extract_family_attrs(name: str) -> dict:
    """
    从家族名称中自动提取结构化属性

    这些属性用于规则匹配时的多维过滤：
    - 清单写"沟槽连接"，家族是"螺纹连接" → 跳过
    - 清单没写"保温"，家族是"绝热" → 跳过
    - 清单没写"人防"，家族是"人防套管" → 跳过

    参数:
        name: 家族名称，如 "室内镀锌钢管(螺纹连接)" 或 "通风管道绝热 岩棉制品安装"

    返回:
        {
            "connection": "螺纹",      # 连接方式，None表示未识别
            "material": "镀锌钢管",     # 材质，None表示未识别
            "is_insulation": False,    # 是否保温/绝热类定额
            "is_civil_defense": False, # 是否人防类定额
            "is_explosion_proof": False, # 是否防爆类定额
            "is_outdoor": False,       # 是否室外类定额
        }
    """
    attrs = {
        "connection": None,
        "material": None,
        "is_insulation": False,
        "is_civil_defense": False,
        "is_explosion_proof": False,
        "is_outdoor": False,        # 是否室外定额（清单默认室内）
    }

    # === 1. 提取连接方式 ===
    for keyword, conn_type in CONNECTION_PATTERNS:
        if keyword in name:
            # 特殊处理："法兰"在阀门名称中可能只是描述阀门类型而非连接方式
            # 但"焊接法兰阀门"、"螺纹法兰阀门"的连接方式是明确的
            # "法兰浮球阀门"、"法兰液压式水位控制阀门" → 连接方式是"法兰"
            attrs["connection"] = conn_type
            break

    # === 2. 提取材质 ===
    for keyword, mat_type in MATERIAL_PATTERNS:
        if keyword in name:
            attrs["material"] = mat_type
            break

    # === 3. 布尔属性 ===
    # 保温/绝热
    if "绝热" in name or "保温" in name:
        attrs["is_insulation"] = True

    # 人防
    if "人防" in name:
        attrs["is_civil_defense"] = True

    # 防爆
    if "防爆" in name:
        attrs["is_explosion_proof"] = True

    # 室外（造价行业惯例：清单没写室内/室外时默认室内）
    if "室外" in name:
        attrs["is_outdoor"] = True

    # 去掉全None的attrs（所有值都是默认值时不输出，节省空间）
    # 但为了一致性，始终返回完整结构
    return attrs


def extract_book_from_chapter(chapter: str, quotas: list) -> str:
    """
    从章节的定额编号中提取所属大册

    参数:
        chapter: 章节名称
        quotas: 该章节的定额列表

    返回:
        大册编号，如 "C4", "C10"
    """
    if not quotas:
        return ""
    # 从第一条定额编号提取
    qid = quotas[0].get("quota_id", "")
    # 通用格式：C10-5-41→C10, A-1-5→A, D-3-8→D, 1-2-3→1
    m = re.match(r'^([A-Za-z]\d{0,2})-', qid)
    if not m:
        m = re.match(r'^(\d{1,2})-', qid)
    return m.group(1).upper() if m else ""


def process_all_chapters(db: QuotaDB, specialty: str = None) -> dict:
    """
    处理所有章节，提取规则

    参数:
        db: 定额数据库实例
        specialty: 限定专业（如"安装"、"土建"），为None时处理所有

    返回:
        完整的规则字典
    """
    if specialty:
        chapters = db.get_chapters_by_specialty(specialty)
    else:
        chapters = db.get_chapters()
    print(f"共找到 {len(chapters)} 个章节" + (f" (specialty={specialty})" if specialty else ""))

    all_chapters = {}
    total_quotas = 0
    total_families = 0
    total_standalone = 0

    for i, chapter in enumerate(chapters):
        # 读取该章节的所有定额
        quotas = db.get_quotas_by_chapter(chapter, limit=1000)
        if not quotas:
            continue

        # 按编号自然排序（确保 C4-1-2 在 C4-1-10 前面）
        quotas.sort(key=lambda q: natural_sort_key(q["quota_id"]))

        total_quotas += len(quotas)
        book = extract_book_from_chapter(chapter, quotas)

        # 分组为家族
        families = group_quotas_into_families(quotas)

        # 统计
        family_count = sum(1 for f in families if f["type"] == "family")
        standalone_count = sum(1 for f in families if f["type"] == "standalone")
        total_families += family_count
        total_standalone += standalone_count

        # 定额编号范围
        quota_ids = [q["quota_id"] for q in quotas]

        all_chapters[chapter] = {
            "book": book,
            "quota_count": len(quotas),
            "quota_range": f"{quota_ids[0]} ~ {quota_ids[-1]}",
            "family_count": family_count,
            "standalone_count": standalone_count,
            "families": families,
        }

        # 进度显示
        if (i + 1) % 10 == 0 or i == len(chapters) - 1:
            print(f"  已处理 {i + 1}/{len(chapters)} 章节...")

    result = {
        "meta": {
            "source": specialty or "全部",
            "province": db.province,
            "specialty": specialty or "全部",
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_chapters": len(chapters),
            "total_quotas": total_quotas,
            "total_families": total_families,
            "total_standalone": total_standalone,
        },
        "chapters": all_chapters,
    }

    return result


def generate_summary(rules: dict) -> str:
    """
    生成人可读的规则摘要

    参数:
        rules: 完整的规则字典

    返回:
        摘要文本
    """
    meta = rules["meta"]
    lines = []
    lines.append("=" * 70)
    lines.append(f"定额规则自动提取摘要")
    lines.append(f"=" * 70)
    lines.append(f"数据来源: {meta['source']}")
    lines.append(f"省份版本: {meta['province']}")
    lines.append(f"生成时间: {meta['generated']}")
    lines.append(f"总章节数: {meta['total_chapters']}")
    lines.append(f"总定额数: {meta['total_quotas']}")
    lines.append(f"定额家族: {meta['total_families']} 个")
    lines.append(f"独立定额: {meta['total_standalone']} 个")
    lines.append("")

    for chapter_name, chapter_data in rules["chapters"].items():
        lines.append("-" * 60)
        lines.append(f"【{chapter_name}】 {chapter_data['book']}")
        lines.append(f"  定额范围: {chapter_data['quota_range']}")
        lines.append(f"  共 {chapter_data['quota_count']} 条 | "
                      f"{chapter_data['family_count']} 个家族 + "
                      f"{chapter_data['standalone_count']} 个独立定额")

        for family in chapter_data["families"]:
            if family["type"] == "family":
                lines.append(f"")
                lines.append(f"  ▸ {family['name']}")
                lines.append(f"    前缀: {family['prefix']}")
                lines.append(f"    范围: {family['quota_range']}（{family['count']}条）")
                lines.append(f"    单位: {family['unit']}")

                # 参数信息
                param_parts = []
                if family.get("param_name"):
                    param_parts.append(f"参数={family['param_name']}")
                if family.get("param_unit"):
                    param_parts.append(f"单位={family['param_unit']}")
                if family.get("param_rule"):
                    param_parts.append(f"规则={family['param_rule']}")
                if param_parts:
                    lines.append(f"    {', '.join(param_parts)}")

                # 档位
                if "tiers" in family:
                    tiers_str = " → ".join(str(int(t) if t == int(t) else t)
                                           for t in family["tiers"])
                    lines.append(f"    档位: {tiers_str}")
                else:
                    values_str = " | ".join(family["values"][:10])
                    if len(family["values"]) > 10:
                        values_str += f" ... (共{len(family['values'])}个)"
                    lines.append(f"    取值: {values_str}")

            else:
                # 独立定额，简略显示
                lines.append(f"  · [{family['quota_id']}] {family['name']} ({family['unit']})")

        lines.append("")

    return "\n".join(lines)


def main():
    """主函数"""
    import argparse
    parser = argparse.ArgumentParser(description="定额规则自动提取工具")
    parser.add_argument("--specialty", type=str, default=None,
                        help="限定专业（如 安装、土建），不指定则处理所有")
    parser.add_argument("--province", type=str, default=None,
                        help="省份版本（如 北京2024），不指定使用默认配置")
    args = parser.parse_args()

    print("=" * 50)
    print("定额规则自动提取工具")
    print("=" * 50)
    print()

    # 连接数据库
    db = QuotaDB(province=args.province)
    print(f"省份: {db.province}")
    print(f"数据库: {db.db_path}")
    if args.specialty:
        print(f"专业: {args.specialty}")
    print()

    # 如果未指定specialty，自动提取所有已有专业
    if args.specialty:
        specialties = [args.specialty]
    else:
        specialties = db.get_specialties()
        if not specialties:
            print("数据库为空，请先导入定额数据")
            return
        print(f"发现 {len(specialties)} 个专业: {', '.join(specialties)}")

    # 逐个specialty生成规则文件
    for specialty in specialties:
        print(f"\n{'='*40}")
        print(f"处理专业: {specialty}")
        print(f"{'='*40}")

        rules = process_all_chapters(db, specialty=specialty)

        # 输出路径（按省份分子目录）
        province = db.province
        output_dir = PROJECT_ROOT / "data" / "quota_rules" / province
        output_dir.mkdir(parents=True, exist_ok=True)

        # 文件名格式: {专业}定额规则.json
        json_path = output_dir / f"{specialty}定额规则.json"
        summary_path = output_dir / f"{specialty}定额规则_摘要.txt"

        # 写入JSON（原子替换）
        json_tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json",
                prefix=f"{json_path.stem}_tmp_",
                dir=str(json_path.parent),
                encoding="utf-8", delete=False,
            ) as f:
                json_tmp = f.name
                json.dump(rules, f, ensure_ascii=False, indent=2)
            os.replace(json_tmp, json_path)
        finally:
            if json_tmp and Path(json_tmp).exists():
                try:
                    os.remove(json_tmp)
                except OSError:
                    pass
        print(f"JSON规则文件: {json_path}")

        # 写入摘要
        summary = generate_summary(rules)
        summary_tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt",
                prefix=f"{summary_path.stem}_tmp_",
                dir=str(summary_path.parent),
                encoding="utf-8", delete=False,
            ) as f:
                summary_tmp = f.name
                f.write(summary)
            os.replace(summary_tmp, summary_path)
        finally:
            if summary_tmp and Path(summary_tmp).exists():
                try:
                    os.remove(summary_tmp)
                except OSError:
                    pass
        print(f"摘要文件: {summary_path}")

        # 打印统计
        meta = rules["meta"]
        print(f"  章节: {meta['total_chapters']}")
        print(f"  定额: {meta['total_quotas']}")
        print(f"  家族: {meta['total_families']}")
        print(f"  独立: {meta['total_standalone']}")

    print(f"\n全部完成!")


if __name__ == "__main__":
    main()
