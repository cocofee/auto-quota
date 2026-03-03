# -*- coding: utf-8 -*-
"""跨省定额术语搜索工具 — 同一个关键词在各省的BM25搜索结果对比"""
import subprocess
import sys
import re

# 要搜索的关键词
keyword = sys.argv[1] if len(sys.argv) > 1 else "桥架"

# 安装类定额库列表（去掉石化/轨道/拆除等特殊库，24个省/市）
INSTALL_PROVINCES = [
    "北京市建设工程施工消耗量标准(2024)",
    "上海市安装工程预算定额(2016)",
    "宁夏安装工程计价定额(2019)",
    "广东省通用安装工程综合定额(2018)",
    "广西安装工程消耗量定额(2023)",
    "江苏省安装工程计价定额(2014)",
    "江西省通用安装工程消耗量定额及统一基价表(2017)",
    "河南省通用安装工程预算定额(2016)",
    "浙江省通用安装工程预算定额(2018)",
    "湖北省通用安装工程消耗量定额及全费用基价表(2024)",
    "湖南省安装工程消耗量标准(2020)",
    "四川省2020序列定额",
    "山东省安装工程消耗量定额(2025)",
    "西藏自治区通用安装工程预算定额(2016)",
    "辽宁省通用安装工程定额(2024)",
    "重庆市通用安装工程计价定额(2018)",
    "陕西省通用安装工程基价表(2025)",
    "黑龙江省通用安装工程消耗量定额(2019)",
    "云南省通用安装工程计价标准(2020)",
    "内蒙古通用安装工程预算定额(2017)",
    "甘肃省安装工程预算定额(2013)",
    "深圳市安装工程消耗量标准(2025)",
    "福建省通用安装工程预算定额(2017)",
]

def extract_family(name):
    """从定额全名提取家族名（去掉参数后缀）"""
    # 去掉 (宽+高)(mm以下) 200、公称直径(mm以内) 50 等参数部分
    # 策略：找到第一个数字+单位的参数部分，截断
    fam = re.sub(r'\s*[\(（][^)）]*[\)）]\s*[≤≥<>]?\s*\d+.*$', '', name)
    # 如果还有尾部纯数字，也去掉
    fam = re.sub(r'\s+\d+(\.\d+)?$', '', fam)
    return fam.strip()

def search_province(keyword, province):
    """在指定省份搜索关键词"""
    cmd = [
        sys.executable, "tools/jarvis_lookup.py", keyword,
        "--province", province
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8',
            timeout=30, env={**__import__('os').environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
        )
        lines = result.stdout.strip().split('\n')
        # 解析结果行：  C4-11-249\t钢制槽式桥架(宽+高)(mm以下) 200\tm
        entries = []
        for line in lines:
            line = line.strip()
            if line.startswith('C') or line.startswith('D') or re.match(r'\d+-\d+', line):
                parts = line.split('\t')
                if len(parts) >= 2:
                    entries.append({'id': parts[0].strip(), 'name': parts[1].strip()})
        return entries
    except Exception as e:
        return [{'id': 'ERR', 'name': str(e)}]

# 简称映射（24个省/市）
SHORT_NAMES = {
    '北京': '北京市建设工程施工消耗量标准(2024)',
    '上海': '上海市安装工程预算定额(2016)',
    '宁夏': '宁夏安装工程计价定额(2019)',
    '广东': '广东省通用安装工程综合定额(2018)',
    '广西': '广西安装工程消耗量定额(2023)',
    '江苏': '江苏省安装工程计价定额(2014)',
    '江西': '江西省通用安装工程消耗量定额及统一基价表(2017)',
    '河南': '河南省通用安装工程预算定额(2016)',
    '浙江': '浙江省通用安装工程预算定额(2018)',
    '湖北': '湖北省通用安装工程消耗量定额及全费用基价表(2024)',
    '湖南': '湖南省安装工程消耗量标准(2020)',
    '四川': '四川省2020序列定额',
    '山东': '山东省安装工程消耗量定额(2025)',
    '西藏': '西藏自治区通用安装工程预算定额(2016)',
    '辽宁': '辽宁省通用安装工程定额(2024)',
    '重庆': '重庆市通用安装工程计价定额(2018)',
    '陕西': '陕西省通用安装工程基价表(2025)',
    '黑龙江': '黑龙江省通用安装工程消耗量定额(2019)',
    '云南': '云南省通用安装工程计价标准(2020)',
    '内蒙古': '内蒙古通用安装工程预算定额(2017)',
    '甘肃': '甘肃省安装工程预算定额(2013)',
    '深圳': '深圳市安装工程消耗量标准(2025)',
    '福建': '福建省通用安装工程预算定额(2017)',
}

print(f"{'='*80}")
print(f"跨省术语搜索：\"{keyword}\"")
print(f"{'='*80}")

for short, full in SHORT_NAMES.items():
    entries = search_province(keyword, full)
    if not entries:
        print(f"  {short:4s} │ (无结果)")
        continue

    # 提取家族名并去重
    families = {}
    for e in entries:
        fam = extract_family(e['name'])
        if fam not in families:
            families[fam] = e['id']

    fam_strs = [f"{fam}" for fam, eid in families.items()]
    print(f"  {short:4s} │ {' | '.join(fam_strs[:5])}")

print(f"{'='*80}")
