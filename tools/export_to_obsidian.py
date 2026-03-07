"""
知识库导出到 Obsidian
将同义词表按专业分类，导出为 Obsidian 笔记（md文件）
放到 D:\Obsidian\工程造价\系统更新\ 目录下

用法：
    python tools/export_to_obsidian.py          # 导出全部
    python tools/export_to_obsidian.py --force   # 强制覆盖已有文件
"""

import json
import os
import sys
from datetime import datetime

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# OB 输出目录
OB_OUTPUT_DIR = r"D:\Obsidian\工程造价\系统更新\知识库"

# 同义词表路径
SYNONYMS_PATH = os.path.join(PROJECT_ROOT, "data", "engineering_synonyms.json")

# 按关键词归类的规则
CATEGORY_RULES = {
    "消防水": {
        "keywords": ["消火栓", "消防栓", "消防泵", "喷淋", "报警阀", "水喷",
                     "灭火", "SNW", "SNZW", "卷盘", "水龙", "水鹤",
                     "消防模块", "消防广播", "消防喇叭", "消防电源",
                     "消防柜", "智能消防", "消防水炮", "消火栓按钮",
                     "消防应急", "消防软管", "喷头", "水流指示器",
                     "手报", "手动报警", "防火封堵", "防火槽盒", "防火板",
                     "消防", "七氟丙烷", "气溶胶"],
        "icon": "🔥",
    },
    "给排水管道": {
        "keywords": ["给水管", "排水管", "铸铁管", "PPR", "PVC", "PE管",
                     "复合管", "骨架", "PSP", "钢塑", "衬塑",
                     "不锈钢管", "薄壁", "卡压", "环压", "ABS管",
                     "排水立管", "通气管", "虹吸", "紫铜管", "铜管"],
        "icon": "🚰",
    },
    "管道通用": {
        "keywords": ["钢管", "镀锌", "焊接钢管", "无缝钢管", "管道安装",
                     "管道支架", "管卡", "管箍", "法兰", "套管",
                     "柔性接口", "伸缩节", "补偿器", "管件",
                     "弯头", "三通", "四通", "大小头", "沟槽",
                     "卡箍", "热熔连接", "电熔连接", "伸缩器",
                     "膨胀节", "伸缩接头", "分歧器", "分歧管"],
        "icon": "🔧",
    },
    "阀门": {
        "keywords": ["阀", "止回", "截止", "闸阀", "蝶阀", "球阀",
                     "减压阀", "安全阀", "电磁阀", "浮球阀",
                     "过滤器", "倒流防止器"],
        "icon": "🔶",
    },
    "水泵与设备": {
        "keywords": ["泵", "水箱", "水池", "水表", "流量计",
                     "稳压罐", "气压罐", "水处理", "软化",
                     "换热器", "集水器", "分水器", "定压罐",
                     "定压补水"],
        "icon": "⚙️",
    },
    "电缆与导线": {
        "keywords": ["电缆", "导线", "BV", "YJV", "RVS", "WDZN",
                     "NH-", "ZR-", "铜芯", "穿线", "线缆",
                     "网线", "双绞线", "配线", "电气配线"],
        "icon": "⚡",
    },
    "配管与线槽": {
        "keywords": ["桥架", "线槽", "穿管", "配管", "钢管敷设",
                     "JDG", "KBG", "SC管", "电线管", "金属软管",
                     "波纹管", "接线盒", "梯架", "阻燃槽盒",
                     "凿槽", "剔槽", "压槽", "刨沟"],
        "icon": "📦",
    },
    "配电设备": {
        "keywords": ["配电", "开关", "插座", "断路器", "接触器",
                     "控制柜", "动力柜", "照明箱", "计量箱",
                     "母线", "电源", "动力箱", "控制箱",
                     "干式变压器", "箱式变压器", "箱变",
                     "UPS", "不间断电源", "地插", "地面插座",
                     "线控器"],
        "icon": "🔌",
    },
    "灯具与照明": {
        "keywords": ["灯", "照明", "光源", "筒灯", "射灯",
                     "诱导灯", "应急灯", "日光灯", "LED"],
        "icon": "💡",
    },
    "弱电与智能化": {
        "keywords": ["探测器", "烟感", "温感", "摄像", "监控",
                     "门禁", "对讲", "广播", "网络", "光纤",
                     "信号", "传感器", "声光"],
        "icon": "📡",
    },
    "通风空调": {
        "keywords": ["风管", "风口", "风机", "排烟", "空调",
                     "风阀", "消声器", "静压箱", "新风",
                     "散流器", "百叶", "格栅", "风盘", "冷媒",
                     "球形喷口", "冷水机", "冷却塔", "多联机",
                     "室内机", "室外机", "排气扇", "换气扇",
                     "浴霸", "凉霸", "通风器", "全空气",
                     "油网除尘"],
        "icon": "🌀",
    },
    "防雷接地": {
        "keywords": ["接地", "防雷", "避雷", "等电位",
                     "扁钢", "圆钢", "热镀锌", "均压环"],
        "icon": "⚡",
    },
    "刷油防腐保温": {
        "keywords": ["防腐", "刷油", "保温", "绝热",
                     "油漆", "底漆", "面漆", "防锈"],
        "icon": "🎨",
    },
}


def load_synonyms():
    """加载同义词表"""
    with open(SYNONYMS_PATH, "r", encoding="utf-8") as f:
        syns = json.load(f)
    # 过滤掉元数据键（以_开头的）
    return {k: v for k, v in syns.items() if not k.startswith("_")}


def categorize(synonyms: dict) -> dict:
    """按专业分类同义词"""
    result = {cat: {} for cat in CATEGORY_RULES}
    result["其他"] = {}  # 兜底分类

    for term, mappings in synonyms.items():
        found = False
        for cat, rule in CATEGORY_RULES.items():
            if any(kw in term for kw in rule["keywords"]):
                result[cat][term] = mappings
                found = True
                break
        if not found:
            result["其他"][term] = mappings

    return result


def generate_md(category: str, items: dict, icon: str = "📋") -> str:
    """生成一个分类的 Obsidian 笔记内容"""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        "---",
        f"topic: 同义词表-{category}",
        "specialty: 通用",
        "status: 自动生成",
        "source: auto-quota知识库",
        f"date: {today}",
        f"tags: [同义词, {category}, Jarvis]",
        "---",
        "",
        f"# {icon} 同义词表 — {category}",
        "",
        f"> 自动导出自 auto-quota 知识库（{today}）",
        f"> 共 **{len(items)}** 条映射",
        "",
        "| 清单写法 | 定额搜索词 |",
        "|---------|----------|",
    ]

    # 按清单写法排序
    for term in sorted(items.keys()):
        mappings = items[term]
        if isinstance(mappings, list):
            mapping_str = " / ".join(mappings)
        else:
            mapping_str = str(mappings)
        # 转义表格中的竖线
        term_safe = term.replace("|", "\\|")
        mapping_safe = mapping_str.replace("|", "\\|")
        lines.append(f"| {term_safe} | {mapping_safe} |")

    lines.append("")
    lines.append("---")
    lines.append(f"> 由 `tools/export_to_obsidian.py` 自动生成，请勿手动编辑")
    lines.append("")

    return "\n".join(lines)


def generate_index(categorized: dict) -> str:
    """生成汇总索引页"""
    today = datetime.now().strftime("%Y-%m-%d")
    total = sum(len(items) for items in categorized.values())

    lines = [
        "---",
        "topic: 知识库总览",
        "specialty: 通用",
        "status: 自动生成",
        "source: auto-quota知识库",
        f"date: {today}",
        "tags: [知识库, 同义词, Jarvis]",
        "---",
        "",
        "# Jarvis 知识库总览",
        "",
        f"> 自动导出自 auto-quota 知识库（{today}）",
        f"> 同义词总数：**{total}** 条",
        "",
        "## 分类索引",
        "",
        "| 分类 | 条数 | 链接 |",
        "|------|------|------|",
    ]

    for cat, items in categorized.items():
        if not items:
            continue
        icon = CATEGORY_RULES.get(cat, {}).get("icon", "📋")
        lines.append(f"| {icon} {cat} | {len(items)} | [[同义词表_{cat}]] |")

    lines.append("")
    lines.append("## 说明")
    lines.append("")
    lines.append("- **清单写法**：工程量清单中常见的名称/型号/俗称")
    lines.append("- **定额搜索词**：在定额库中搜索时使用的关键词")
    lines.append("- Jarvis 匹配时会自动将清单写法替换为定额搜索词，提高命中率")
    lines.append("- 新增同义词请在 Claude Code 中使用 `/note` 命令")
    lines.append("")
    lines.append("---")
    lines.append(f"> 由 `tools/export_to_obsidian.py` 自动生成")
    lines.append("")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="知识库导出到 Obsidian")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有文件")
    args = parser.parse_args()

    # 确保输出目录存在
    os.makedirs(OB_OUTPUT_DIR, exist_ok=True)

    # 加载和分类
    synonyms = load_synonyms()
    print(f"加载同义词表：{len(synonyms)} 条")

    categorized = categorize(synonyms)

    # 统计
    for cat, items in categorized.items():
        if items:
            icon = CATEGORY_RULES.get(cat, {}).get("icon", "📋")
            print(f"  {icon} {cat}: {len(items)} 条")

    # 生成各分类文件
    written = 0
    for cat, items in categorized.items():
        if not items:
            continue
        icon = CATEGORY_RULES.get(cat, {}).get("icon", "📋")
        filename = f"同义词表_{cat}.md"
        filepath = os.path.join(OB_OUTPUT_DIR, filename)

        if os.path.exists(filepath) and not args.force:
            print(f"  跳过（已存在）: {filename}")
            continue

        content = generate_md(cat, items, icon)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  写入: {filename}")
        written += 1

    # 生成索引页
    index_path = os.path.join(OB_OUTPUT_DIR, "知识库总览.md")
    if not os.path.exists(index_path) or args.force:
        content = generate_index(categorized)
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  写入: 知识库总览.md")
        written += 1

    print(f"\n完成！共写入 {written} 个文件到 {OB_OUTPUT_DIR}")
    if not args.force and written == 0:
        print("提示：文件已存在，使用 --force 强制覆盖")


if __name__ == "__main__":
    main()
