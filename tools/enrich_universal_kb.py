# -*- coding: utf-8 -*-
"""
批量补充通用知识库 — 常见品类映射

根据匹配结果审核发现的系统性搜索盲区，
把"清单常见品类 → 正确的定额搜索方向"写入通用知识库权威层。

写入后效果：
- 搜索引擎在搜索前先查通用知识库，获取"应该搜什么"的提示
- 比如搜"无缝钢管 DN125"时，知识库告诉它"应该搜碳钢管道安装"
- 这样搜索引擎就能找到正确的候选定额

运行方式：
    python tools/enrich_universal_kb.py
    python tools/enrich_universal_kb.py --dry-run   # 只预览不写入
"""
import sys
import os
import argparse

# 项目根目录加入path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 知识条目定义
# ============================================================
# 每条知识的含义：
#   bill_pattern = 清单中会出现的描述模式
#   quota_patterns = 应该搜索的定额名称方向（不含编号，全国通用）
#   associated_patterns = 可能需要配套的定额
#   param_hints = 参数变化规则
#   specialty = 所属专业册号
#   bill_keywords = 用于快速匹配的关键词

KNOWLEDGE_ENTRIES = [
    # ============================================================
    # 一、管道安装类（暖通/给排水/消防常见的管材）
    # ============================================================
    {
        "bill_pattern": "无缝钢管安装",
        "quota_patterns": [
            "碳钢管道安装",
            "钢管安装 焊接",
            "管道安装 无缝钢管",
        ],
        "associated_patterns": ["管道支架", "管道试压", "管道冲洗"],
        "param_hints": {"材质": "无缝钢管", "连接方式": "焊接"},
        "bill_keywords": ["无缝钢管", "钢管", "碳钢管"],
        "specialty": "C8",
    },
    {
        "bill_pattern": "焊接钢管安装",
        "quota_patterns": [
            "碳钢管道安装",
            "钢管安装 焊接",
        ],
        "associated_patterns": ["管道支架", "管道试压"],
        "param_hints": {"材质": "焊接钢管", "连接方式": "焊接"},
        "bill_keywords": ["焊接钢管", "钢管"],
        "specialty": "C8",
    },
    {
        "bill_pattern": "镀锌钢管安装",
        "quota_patterns": [
            "管道安装 镀锌钢管",
            "镀锌钢管安装 丝接",
            "镀锌钢管安装 沟槽连接",
        ],
        "associated_patterns": ["管卡安装", "水压试验"],
        "param_hints": {"材质": "镀锌钢管", "连接方式": "丝接或沟槽"},
        "bill_keywords": ["镀锌钢管", "镀锌管"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "PPR给水管安装",
        "quota_patterns": [
            "塑料管道安装 PPR",
            "PPR管道安装 热熔连接",
        ],
        "associated_patterns": ["管卡安装", "水压试验"],
        "param_hints": {"材质": "PPR", "连接方式": "热熔"},
        "bill_keywords": ["PPR", "PPR管", "给水管"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "PE管安装",
        "quota_patterns": [
            "塑料管道安装 PE",
            "PE管道安装 热熔连接",
        ],
        "associated_patterns": ["管卡安装", "水压试验"],
        "param_hints": {"材质": "PE", "连接方式": "热熔或电熔"},
        "bill_keywords": ["PE管", "PE给水管"],
        "specialty": "C10",
    },

    # ============================================================
    # 二、阀门安装类
    # ============================================================
    {
        "bill_pattern": "闸阀安装",
        "quota_patterns": [
            "低压阀门安装 闸阀",
            "阀门安装 闸阀",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "闸阀"},
        "bill_keywords": ["闸阀"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "蝶阀安装 手动",
        "quota_patterns": [
            "低压阀门安装 蝶阀",
            "低压阀门安装 对夹式蝶阀",
            "蝶阀安装",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "手动蝶阀", "说明": "无电动标注时默认手动"},
        "bill_keywords": ["蝶阀"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "电动蝶阀安装",
        "quota_patterns": [
            "电动蝶阀及执行机构",
            "电动阀门安装 蝶阀",
        ],
        "associated_patterns": ["执行机构安装"],
        "param_hints": {"类型": "电动蝶阀", "说明": "必须清单明确标注电动"},
        "bill_keywords": ["电动蝶阀", "电动阀"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "电动阀安装",
        "quota_patterns": [
            "电动阀门安装",
            "低压电动阀门",
            "中压电动阀门",
        ],
        "associated_patterns": ["执行机构安装"],
        "param_hints": {"类型": "电动阀", "说明": "注意区分低压和中压"},
        "bill_keywords": ["电动阀"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "球阀安装",
        "quota_patterns": [
            "低压阀门安装 球阀",
            "阀门安装 球阀",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "球阀"},
        "bill_keywords": ["球阀"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "止回阀安装",
        "quota_patterns": [
            "低压阀门安装 止回阀",
            "阀门安装 止回阀",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "止回阀"},
        "bill_keywords": ["止回阀", "逆止阀"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "截止阀安装",
        "quota_patterns": [
            "低压阀门安装 截止阀",
            "阀门安装 截止阀",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "截止阀"},
        "bill_keywords": ["截止阀"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "安全阀安装",
        "quota_patterns": [
            "安全阀安装",
            "阀门安装 安全阀",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "安全阀"},
        "bill_keywords": ["安全阀"],
        "specialty": "C8",
    },
    {
        "bill_pattern": "减压阀安装",
        "quota_patterns": [
            "减压阀安装",
            "阀门安装 减压阀",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "减压阀"},
        "bill_keywords": ["减压阀"],
        "specialty": "C10",
    },

    # ============================================================
    # 三、管道附件类
    # ============================================================
    {
        "bill_pattern": "Y型过滤器安装",
        "quota_patterns": [
            "管道附件安装 过滤器",
            "除污器安装",
            "过滤器安装",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "Y型过滤器", "说明": "管道附件,非通风过滤器"},
        "bill_keywords": ["Y型过滤器", "过滤器"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "橡胶软接头安装",
        "quota_patterns": [
            "柔性接头安装",
            "橡胶接头安装",
            "可曲挠橡胶接头",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "橡胶软接头/柔性接头"},
        "bill_keywords": ["橡胶软接头", "软接头", "柔性接头"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "盲板安装",
        "quota_patterns": [
            "盲板安装",
            "管道附件 盲板",
            "法兰盲板",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "盲板"},
        "bill_keywords": ["盲板"],
        "specialty": "C8",
    },

    # ============================================================
    # 四、套管类
    # ============================================================
    {
        "bill_pattern": "填料套管制作安装",
        "quota_patterns": [
            "套管制作安装",
            "填料套管制作安装",
            "套管制作",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "填料套管/穿墙套管"},
        "bill_keywords": ["填料套管", "套管", "穿墙套管"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "刚性防水套管安装",
        "quota_patterns": [
            "防水套管安装",
            "刚性防水套管安装",
            "套管制作安装 防水",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "刚性防水套管"},
        "bill_keywords": ["防水套管", "刚性防水套管"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "柔性防水套管安装",
        "quota_patterns": [
            "防水套管安装",
            "柔性防水套管安装",
            "套管制作安装 防水",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "柔性防水套管"},
        "bill_keywords": ["柔性防水套管", "防水套管"],
        "specialty": "C10",
    },

    # ============================================================
    # 五、仪表类（温度计、压力表等管道仪表）
    # ============================================================
    {
        "bill_pattern": "温度计安装 管道",
        "quota_patterns": [
            "温度仪表安装",
            "就地温度计安装",
            "双金属温度计安装",
        ],
        "associated_patterns": ["温度计套管"],
        "param_hints": {"类型": "温度计/温度仪表", "说明": "管道用温度计,非辐射温度计"},
        "bill_keywords": ["温度计", "温度仪表"],
        "specialty": "C5",
    },
    {
        "bill_pattern": "压力表安装 管道",
        "quota_patterns": [
            "压力仪表安装",
            "就地压力表安装",
            "弹簧管压力表安装",
        ],
        "associated_patterns": ["压力表弯制作"],
        "param_hints": {"类型": "压力表/压力仪表", "说明": "压力表本体安装,压力表弯是附件"},
        "bill_keywords": ["压力表", "压力仪表"],
        "specialty": "C5",
    },

    # ============================================================
    # 六、消防/通风阀门类
    # ============================================================
    {
        "bill_pattern": "排烟阀安装",
        "quota_patterns": [
            "防排烟阀安装",
            "排烟阀安装",
            "排烟防火阀安装",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "排烟阀/排烟防火阀"},
        "bill_keywords": ["排烟阀", "排烟防火阀", "防排烟阀"],
        "specialty": "C9",
    },
    {
        "bill_pattern": "防火阀安装",
        "quota_patterns": [
            "防火阀安装",
            "防排烟阀安装",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "防火阀"},
        "bill_keywords": ["防火阀"],
        "specialty": "C7",
    },
    {
        "bill_pattern": "排烟风机安装",
        "quota_patterns": [
            "排烟风机安装",
            "消防排烟风机安装",
            "风机安装 离心式",
        ],
        "associated_patterns": ["风机减振器安装"],
        "param_hints": {"类型": "排烟风机"},
        "bill_keywords": ["排烟风机", "消防风机"],
        "specialty": "C9",
    },

    # ============================================================
    # 七、支架类
    # ============================================================
    {
        "bill_pattern": "成品支架安装",
        "quota_patterns": [
            "成品支吊架安装",
            "管道支架安装",
            "管道支吊架安装",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "成品支架/抗震支架"},
        "bill_keywords": ["成品支架", "成品支吊架", "抗震支架"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "管道支吊架制作安装",
        "quota_patterns": [
            "管道支吊架制作安装",
            "管道支架制作安装",
        ],
        "associated_patterns": ["管道支吊架刷油"],
        "param_hints": {"类型": "管道支吊架", "说明": "按重量分档"},
        "bill_keywords": ["支吊架", "支架", "管道支架", "基础型钢"],
        "specialty": "C10",
    },

    # ============================================================
    # 八、通风系统调试类
    # ============================================================
    {
        "bill_pattern": "通风工程检测调试",
        "quota_patterns": [
            "通风系统调试",
            "风量平衡调试",
            "通风系统测试",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "通风系统调试", "说明": "非环境检测装置"},
        "bill_keywords": ["通风调试", "通风检测", "风量调试"],
        "specialty": "C7",
    },

    # ============================================================
    # 九、设备安装类
    # ============================================================
    {
        "bill_pattern": "水箱安装",
        "quota_patterns": [
            "水箱安装",
            "不锈钢水箱安装",
        ],
        "associated_patterns": ["水箱基础"],
        "param_hints": {"类型": "水箱", "说明": "按容积分档"},
        "bill_keywords": ["水箱", "软化水箱", "消防水箱", "生活水箱"],
        "specialty": "C10",
    },
    {
        "bill_pattern": "风机盘管安装",
        "quota_patterns": [
            "风机盘管安装 吊顶式",
            "风机盘管安装 壁挂式",
            "风机盘管安装 落地式",
        ],
        "associated_patterns": ["风机盘管接管"],
        "param_hints": {"类型": "风机盘管", "说明": "区分吊顶/壁挂/落地式"},
        "bill_keywords": ["风机盘管", "FCU"],
        "specialty": "C7",
    },
    {
        "bill_pattern": "空气源热泵安装",
        "quota_patterns": [
            "空气源热泵机组安装",
            "热泵机组安装",
            "制冷机组安装",
        ],
        "associated_patterns": ["设备基础", "减振器安装"],
        "param_hints": {"类型": "空气源热泵"},
        "bill_keywords": ["空气源热泵", "热泵", "热泵机组"],
        "specialty": "C7",
    },
    {
        "bill_pattern": "循环泵安装",
        "quota_patterns": [
            "水泵安装",
            "离心泵安装",
            "循环泵安装",
        ],
        "associated_patterns": ["泵基础", "减振器安装"],
        "param_hints": {"类型": "循环泵/水泵"},
        "bill_keywords": ["循环泵", "冷冻水泵", "冷却水泵", "热水循环泵"],
        "specialty": "C1",
    },

    # ============================================================
    # 十、管道绝热/刷油类
    # ============================================================
    {
        "bill_pattern": "管道绝热 橡塑保温",
        "quota_patterns": [
            "管道绝热 橡塑制品安装",
            "管道保温 橡塑",
        ],
        "associated_patterns": [],
        "param_hints": {"材质": "橡塑保温", "说明": "按管径分档"},
        "bill_keywords": ["管道绝热", "保温", "橡塑保温", "闭孔橡塑"],
        "specialty": "C12",
    },
    {
        "bill_pattern": "设备与矩形管道刷油",
        "quota_patterns": [
            "设备与矩形管道刷油",
            "管道刷油 防锈漆",
        ],
        "associated_patterns": [],
        "param_hints": {"说明": "注意漆种和遍数"},
        "bill_keywords": ["刷油", "防锈漆", "管道刷油"],
        "specialty": "C12",
    },

    # ============================================================
    # 十一、开孔打洞类
    # ============================================================
    {
        "bill_pattern": "水钻开孔",
        "quota_patterns": [
            "水钻开孔",
            "钻孔",
        ],
        "associated_patterns": [],
        "param_hints": {"说明": "按孔径和结构类型分档"},
        "bill_keywords": ["开孔", "打洞", "水钻", "钻孔"],
        "specialty": "C10",
    },

    # ============================================================
    # 十二、分集水器
    # ============================================================
    {
        "bill_pattern": "分集水器安装",
        "quota_patterns": [
            "分集水器安装",
            "集分水器安装",
        ],
        "associated_patterns": [],
        "param_hints": {"类型": "分集水器"},
        "bill_keywords": ["分集水器", "集分水器", "热媒集配"],
        "specialty": "C7",
    },
]


def main():
    parser = argparse.ArgumentParser(description="批量补充通用知识库")
    parser.add_argument("--dry-run", action="store_true",
                        help="只预览不写入")
    args = parser.parse_args()

    from src.universal_kb import UniversalKB
    kb = UniversalKB()

    # 先看当前状态
    stats = kb.get_stats()
    print(f"当前知识库状态: 总{stats['total']}条 "
          f"(权威层{stats['authority']}条, 候选层{stats['candidate']}条)")
    print()

    added = 0
    updated = 0
    failed = 0

    for entry in KNOWLEDGE_ENTRIES:
        bill_pattern = entry["bill_pattern"]
        quota_patterns = entry["quota_patterns"]

        if args.dry_run:
            print(f"  [预览] {bill_pattern} → {quota_patterns[0]}...")
            added += 1
            continue

        try:
            record_id = kb.add_knowledge(
                bill_pattern=bill_pattern,
                quota_patterns=quota_patterns,
                associated_patterns=entry.get("associated_patterns"),
                param_hints=entry.get("param_hints"),
                bill_keywords=entry.get("bill_keywords"),
                layer="authority",      # 写入权威层（人工验证的知识）
                confidence=90,          # 高置信度
                source_province=None,   # 全国通用，不绑定省份
                source_project="审核补充_v1",
                specialty=entry.get("specialty"),
            )
            print(f"  [OK] {bill_pattern} → {quota_patterns[0]} (id={record_id})")
            added += 1
        except Exception as e:
            print(f"  [失败] {bill_pattern}: {e}")
            failed += 1

    print()
    if args.dry_run:
        print(f"预览完成: 将添加 {added} 条知识")
    else:
        # 再看写入后状态
        stats_after = kb.get_stats()
        print(f"写入完成: 成功{added}条, 失败{failed}条")
        print(f"知识库状态: 总{stats_after['total']}条 "
              f"(权威层{stats_after['authority']}条, 候选层{stats_after['candidate']}条)")
        print(f"新增: {stats_after['total'] - stats['total']}条")


if __name__ == "__main__":
    main()
