# -*- coding: utf-8 -*-
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
"""
南宁信息价导入工具

南宁住建局的信息价以在线翻页电子书（Flip PDF）发布，
每期的 search_config.js 包含全文文本（textForPages变量）。
直接抓JS → 正则解析 → 入库，不需要OCR或PDF解析。

用法：
    # 试运行（只看提取结果，不写库）
    python tools/import_nanning_js.py --period "202512-2" --dry-run

    # 正式导入
    python tools/import_nanning_js.py --period "202512-2"

    # 批量导入多期
    python tools/import_nanning_js.py --batch "202501-1,202501-2,202502-1,202502-2"

    # 导入2025全年（自动生成24期）
    python tools/import_nanning_js.py --year 2025

期号格式：YYYYMM-N（N=1上半月，2下半月）
数据来源：https://livecloud.nnfcxx.com/zjxx_four_up/zjxx{期号}/mobile/javascript/search_config.js

依赖：
    pip install requests  （标准库即可，不需要额外依赖）
"""

import argparse
import json
import re
import urllib.request
from pathlib import Path

# 确保项目根目录在搜索路径中
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.material_db import MaterialDB
from tools.import_price_excel import (
    normalize_unit, normalize_spec, is_junk_material,
    create_import_batch, update_batch_count
)
from tools.pdf_profiles.base_profile import guess_category, clean_price


# ======== 配置 ========

# 电子书JS文本URL模板
JS_URL_TEMPLATE = (
    "https://livecloud.nnfcxx.com/zjxx_four_up/"
    "zjxx{period}/mobile/javascript/search_config.js"
)


# ======== 下载和解析JS ========

def fetch_text_pages(period: str, verbose: bool = False) -> list:
    """
    下载一期电子书的JS文本，返回每页文本列表

    period: 期号，如 "202512-2"
    返回: ["", "第1页文本", "第2页文本", ...]
    """
    url = JS_URL_TEMPLATE.format(period=period)
    if verbose:
        print(f"  下载: {url}")

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8-sig")  # BOM头
    except Exception as e:
        print(f"  下载失败: {e}")
        return []

    # 提取 textForPages 数组
    m = re.search(r'var\s+textForPages\s*=\s*(\[.*\])', content, re.DOTALL)
    if not m:
        print("  未找到 textForPages 变量")
        return []

    try:
        pages = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f"  JSON解析失败: {e}")
        return []

    if verbose:
        print(f"  共{len(pages)}页文本")

    return pages


# ======== 文本解析 ========

# 已知的单位关键词（用于从段落流中识别"单位"字段）
_UNIT_SET = {
    '吨', 't', '㎡', 'm', '米', '100m', 'km', '块', '件', '套',
    '个', '根', '张', '把', '付', '副', '对', '组', '台', '只',
    '桶', '包', '卷', '瓶', '支', '条', '盒', '千克', 'kg',
    '立方米', '平方米', '公斤', '升', '株', '袋',
}

# 单位的正则匹配（处理"m  2"→"m2"、"m  3"→"m3"等OCR拆分情况）
_UNIT_RE = re.compile(r'^(m|mm|cm|㎡|m[²³23]|100m|km|m\s*2|m\s*3)$', re.I)

# 页面标题/表头关键词（跳过这些段落）
_SKIP_SEGS = [
    '价 格 信 息', '价格信息', '南宁建设工程造价信息',
    '序号', '编码', '名称', '名  称', '规格', '规   格', '规  格',
    '单位', '备注', '备 注',
    '市场价格含税', '市场价格除税', '含税（元）', '除税（元）',
    '（元）', '市场价格含税（元）', '市场价格除税（元）',
    '市场含税', '市场除税', '价（元）', '号',
    '规格（mm）', '材料名称',
]


def _is_skip_seg(seg: str) -> bool:
    """判断段落是否为页面标题/表头（应跳过）"""
    s = seg.strip()
    if not s:
        return True
    # 精确匹配已知表头词
    for kw in _SKIP_SEGS:
        if s == kw:
            return True
    # 规格相关的表头（半角/全角括号都要匹配）
    if re.match(r'^规\s*格\s*[（(]?', s) and len(s) <= 10:
        return True
    # 期刊标题
    if '南宁建设工程造价信息' in s or '南宁市建设工程材料' in s:
        return True
    if re.match(r'^2\d{3}年\d{1,2}月', s):
        return True
    # 页码
    if re.match(r'^-\s*\d+\s*-$', s):
        return True
    # 表头组合（如"单 市场价格 市场价格"）
    if s.startswith('单') and '市场价格' in s:
        return True
    if s.startswith('市场价格'):
        return True
    return False


def _is_category_header(seg: str) -> str:
    """
    判断是否为分类小标题，返回分类名或空字符串

    例如："1.钢材、不锈钢及其制品" → "钢材、不锈钢及其制品"
          "10.PP-R管、管件" → "PP-R管、管件"
          "一、土建工程材料" → "" (大类标题不当材料名)
          "二、市政工程材料" → "" (大类标题不当材料名)
    """
    s = seg.strip()
    # 大类标题（一、二、三...）不当材料名
    if re.match(r'^[一二三四五六七八九十]+、', s):
        return ""
    # 小类标题（数字+点/顿号+中文名称）
    # 必须要求点后面紧跟中文字符，避免把"3.5寸直插"、"16.70"误判为分类标题
    m = re.match(r'^\d+[.．、]\s*([\u4e00-\u9fffA-Z].+)$', s)
    if m:
        return m.group(1).strip()
    return ""


def _is_unit(seg: str) -> bool:
    """判断段落是否为单位"""
    s = seg.strip()
    return s in _UNIT_SET or bool(_UNIT_RE.match(s))


def _is_price(seg: str) -> bool:
    """判断段落是否为价格数字"""
    s = seg.strip()
    if not s:
        return False
    # 纯数字或带小数点的数字
    if re.match(r'^\d+(\.\d+)?$', s):
        return True
    # 双价格格式（如"62/55"、"55.0 /48.8"）
    if re.match(r'^\d+(\.\d+)?\s*/\s*\d+(\.\d+)?$', s):
        return True
    return False


def _parse_price_value(s: str) -> float:
    """
    解析价格值，处理各种特殊格式

    '3876' → 3876.0
    '62/55' → 62.0（双价格取高价）
    '55.0 /48.8' → 55.0
    """
    if not s:
        return 0
    s = s.strip()
    # 去掉括号说明
    s = re.sub(r'[（(][^)）]*[)）]', '', s).strip()
    # 双价格取第一个
    if '/' in s:
        s = s.split('/')[0].strip()
    return clean_price(s)


def _is_spec_like(seg: str) -> bool:
    """判断段落是否像规格（φ/Φ/○/DN/×等）"""
    s = seg.strip()
    if re.match(r'^[φΦ○◇]', s):
        return True
    if re.match(r'^DN\s*\d', s, re.I):
        return True
    if re.match(r'^[\d.]+[×xX*][\d.]+', s):
        return True
    if s in ('综合',):
        return True
    # 以数字开头的规格（如"2.5/1.6"、"30×3"）
    if re.match(r'^\d+(\.\d+)?[×xX/]', s):
        return True
    return False


def _normalize_unit(seg: str) -> str:
    """标准化单位（处理OCR拆分如"m  2"→"m2"）"""
    s = seg.strip()
    s = re.sub(r'\s+', '', s)  # 去掉空格
    return s


def parse_page_text(text: str, carry_name: str = "") -> tuple:
    """
    解析一页文本，提取价格记录

    南宁电子书文本是"流式段落"格式：用5+个空格分隔的段落按顺序排列，
    每条记录分散在多个段落中。解析策略：

    1. 把整页文本按5+空格切分成段落
    2. 跳过标题/表头段落
    3. 找"序号+编码"段落作为记录锚点
    4. 锚点后依次消费：[名称] [规格] 单位 含税价 除税价 [备注]
    5. 跟踪"当前材料名"用于名称缺省时继承

    参数：
        text: 一页的完整文本
        carry_name: 上一页遗留的材料名（用于跨页继承）
    返回：(records列表, 最后的carry_name)
    """
    if not text or not text.strip():
        return [], carry_name

    # 按5+空格切分成段落
    segments = re.split(r'\s{5,}', text.strip())
    segments = [s.strip() for s in segments if s.strip()]

    records = []
    current_name = carry_name  # 当前材料名（可继承）
    current_section = ""  # 当前分类小标题

    # 附注行标志（附注后面的内容都不是数据）
    in_footnote = False

    i = 0
    while i < len(segments):
        seg = segments[i]

        # 附注开始后跳过后续所有段落
        if '附注' in seg or seg.startswith('注：') or seg.startswith('说明：'):
            in_footnote = True
        if in_footnote:
            i += 1
            continue

        # 跳过标题/表头
        if _is_skip_seg(seg):
            i += 1
            continue

        # 分类小标题（如"1.钢材"）
        cat = _is_category_header(seg)
        if cat:
            current_section = cat
            # 小标题也可能是材料名（如"10.PP-R管、管件"里的"PP-R管"）
            i += 1
            continue

        # 大类标题（一、土建工程材料）
        if re.match(r'^[一二三四五六七八九十]+、', seg):
            i += 1
            continue

        # 尝试匹配"序号+编码"格式的记录锚点
        # 格式："1    010902005" 或 "33   010304002  冷拔丝碳钢丝"
        # 或 "1   172902001   Ⅰ钢筋混凝土排水管"
        anchor_m = re.match(
            r'^(\d{1,4})\s+(\d{6,12})\s*(.*?)$', seg
        )

        if not anchor_m:
            # 尝试匹配纯序号格式（无编码，如PP-R管页面的"1"、"2"）
            pure_seq_m = re.match(r'^(\d{1,4})$', seg)
            if pure_seq_m:
                seq = pure_seq_m.group(1)
                code = ""
                trailing_name = ""
            else:
                # 不是记录锚点——可能是名称段落（如"圆钢"、"镀锌钢管"）
                # 或规格段落（如"○ 10以内"）或其他
                # 检查是否像材料名（中文，不是规格/单位/价格）
                if (not _is_unit(seg) and not _is_price(seg)
                        and not _is_spec_like(seg)
                        and re.search(r'[\u4e00-\u9fff]', seg)
                        and not re.match(r'^[（(]', seg)):
                    # 可能是材料名，但也可能是备注
                    # 暂存，如果后面紧跟记录锚点就当名称用
                    # 简单策略：如果看起来像常见材料名就更新current_name
                    if len(seg) <= 15 and not any(c in seg for c in '。，：；'):
                        current_name = seg
                i += 1
                continue
        else:
            seq = anchor_m.group(1)
            code = anchor_m.group(2)
            trailing_name = anchor_m.group(3).strip()

        # 找到记录锚点，开始消费后续段落提取字段
        # trailing_name可能包含名称，也可能包含名称+规格
        # 如 "（HPB300）    φ 14" — 这里有名称修饰和规格混在一起

        # 解析trailing部分（可能有名称和规格用空格混在一起）
        inline_name = ""
        inline_spec = ""
        if trailing_name:
            # 用3+空格拆分
            parts = re.split(r'\s{3,}', trailing_name)
            parts = [p.strip() for p in parts if p.strip()]
            if parts:
                # 第一部分通常是名称
                inline_name = parts[0]
                if len(parts) > 1:
                    inline_spec = " ".join(parts[1:])

        # 向前消费段落：[名称] [规格...] 单位 含税价 除税价 [备注]
        j = i + 1
        found_name_segs = []
        found_spec_segs = []
        found_unit = ""
        found_price_incl = 0
        found_price_excl = 0
        found_note_segs = []

        # 阶段：0=找名称/规格/单位, 1=找含税价, 2=找除税价, 3=找备注
        phase = 0

        while j < len(segments) and phase < 4:
            s = segments[j]

            # 遇到下一个记录锚点就停止
            if re.match(r'^\d{1,4}\s+\d{6,12}', s):
                break
            # 遇到纯序号且后面跟着规格/单位模式也停止
            if re.match(r'^\d{1,4}$', s):
                # 看后面一个段落，如果是规格或单位，说明这是新记录
                if j + 1 < len(segments):
                    next_s = segments[j + 1].strip()
                    if _is_spec_like(next_s) or _is_unit(next_s):
                        break
                # 如果已经找到了价格，这个数字是新记录
                if phase >= 2:
                    break

            # 跳过标题/表头
            if _is_skip_seg(s):
                j += 1
                continue

            # 分类小标题
            c = _is_category_header(s)
            if c:
                current_section = c
                j += 1
                continue

            # 大类标题
            if re.match(r'^[一二三四五六七八九十]+、', s):
                j += 1
                continue

            # 附注
            if '附注' in s or s.startswith('注：'):
                in_footnote = True
                break

            if phase == 0:
                # 找名称/规格/单位
                if _is_unit(s):
                    found_unit = _normalize_unit(s)
                    phase = 1
                elif _is_price(s):
                    # 没找到单位就遇到价格——可能单位缺失
                    found_price_incl = _parse_price_value(s)
                    phase = 2
                elif _is_spec_like(s):
                    found_spec_segs.append(s)
                else:
                    # 可能是名称或规格（分不清的先当规格存）
                    # 如果第一个非规格段落且含中文，当名称
                    if (not found_name_segs and not found_spec_segs
                            and re.search(r'[\u4e00-\u9fff]', s)
                            and len(s) <= 20):
                        found_name_segs.append(s)
                    else:
                        found_spec_segs.append(s)

            elif phase == 1:
                # 找含税价
                if _is_price(s):
                    found_price_incl = _parse_price_value(s)
                    phase = 2
                else:
                    # 可能单位段包含了价格（如"m 2   232.00    206.00"的情况）
                    break

            elif phase == 2:
                # 找除税价
                if _is_price(s):
                    found_price_excl = _parse_price_value(s)
                    phase = 3
                else:
                    # 没有除税价
                    phase = 3
                    continue  # 不消费这个段落

            elif phase == 3:
                # 找备注（可选，遇到下一条记录就停）
                if (_is_price(s) or _is_unit(s) or _is_spec_like(s)):
                    break  # 不是备注，是下一条记录的内容
                if re.search(r'[\u4e00-\u9fff]', s) and len(s) <= 15:
                    found_note_segs.append(s)
                    phase = 4  # 只取一个备注段
                else:
                    break

            j += 1

        # 组装记录
        # 确定名称：inline_name > found_name_segs > current_name > current_section
        name = ""
        if inline_name:
            name = inline_name
        elif found_name_segs:
            name = " ".join(found_name_segs)

        # "‖"是同上符号
        if name == "‖" or not name:
            name = current_name
        else:
            current_name = name  # 更新当前名称

        # 确定规格
        spec = ""
        if inline_spec:
            spec = inline_spec
        if found_spec_segs:
            spec = (spec + " " + " ".join(found_spec_segs)).strip()

        unit = found_unit
        price_incl = found_price_incl
        price_excl = found_price_excl
        note = " ".join(found_note_segs).strip()

        # 基本验证
        if name and price_incl > 0:
            records.append({
                "seq": seq,
                "code": code,
                "name": name,
                "spec": spec,
                "unit": unit,
                "price_incl": price_incl,
                "price_excl": price_excl,
                "note": note,
            })

        # 移到消费完的位置
        i = j if j > i + 1 else i + 1

    return records, current_name


def extract_nanning_period(period: str, verbose: bool = False) -> list:
    """
    提取一期南宁信息价的所有记录

    返回：标准记录列表（和import_price_pdf的import_to_db兼容）
    """
    pages = fetch_text_pages(period, verbose=verbose)
    if not pages:
        return []

    all_records = []
    carry_name = ""  # 跨页继承的材料名

    for i, text in enumerate(pages):
        if not text or not text.strip():
            continue

        records, carry_name = parse_page_text(text, carry_name)
        if records and verbose:
            print(f"  第{i+1}页: {len(records)}条记录")

        for rec in records:
            name = rec["name"]
            spec = rec["spec"]
            unit = rec["unit"]
            price = rec["price_incl"]

            # ---- 数据清洗 ----
            # 跳过垃圾名称
            if name in ('‖', '综合', '大厂', '线材', '高速'):
                continue
            # 跳过表头/公式类名称
            if any(kw in name for kw in ('元/', '比例', '用量', '平米含量', '（元')):
                continue
            # 清理规格中的"‖"同上符号
            spec = spec.replace('‖', '').strip()
            # 跳过规格异常长的记录（多半是解析错乱）
            if len(spec) > 50:
                continue
            # 跳过明显不合理的价格（单条>50000的只有钢材按吨计有可能）
            if price > 50000:
                continue
            # 跳过价格为0的记录
            if price <= 0:
                continue
            # 如果单位为空但规格里包含单位，尝试提取
            if not unit and spec:
                for u in ('吨', '块', '㎡', 'm', '个', '根', '套', '张', '条', '株'):
                    # 规格末尾可能有 "块    0.86" 这样的格式
                    m = re.search(r'\s+(' + re.escape(u) + r')\s+[\d.]+$', spec)
                    if m:
                        unit = m.group(1)
                        # 去掉规格里的单位+价格部分
                        spec = spec[:m.start()].strip()
                        break

            # 无单位的记录质量通常很差（解析错位），加强过滤
            if not unit:
                # 价格<50的无单位记录大概率是规格数字被误当价格
                if price < 50:
                    continue
                # 名称太短或看起来像表头/序号的跳过
                if len(name) <= 2:
                    continue

            category = guess_category(name)
            all_records.append({
                "name": name,
                "spec": spec,
                "unit": unit,
                "price": price,
                "category": category,
                "tax_included": True,
                "city": "南宁",
            })

    return all_records


# ======== 导入数据库 ========

def import_to_db(records: list, province: str, period: str,
                 source_file: str, dry_run: bool = False) -> dict:
    """复用现有的导入函数"""
    from tools.import_price_pdf import import_to_db as _import_to_db
    return _import_to_db(records, province, period, source_file, dry_run)


def _period_to_date_range(period: str) -> str:
    """
    期号转日期范围

    '202512-2' → '2025-12'（下半月）
    '202501-1' → '2025-01'（上半月）
    """
    m = re.match(r'(\d{4})(\d{2})-(\d)', period)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return period


def _generate_periods(year: int) -> list:
    """生成一年的全部24期期号"""
    periods = []
    for month in range(1, 13):
        periods.append(f"{year}{month:02d}-1")
        periods.append(f"{year}{month:02d}-2")
    return periods


# ======== 主入口 ========

def main():
    parser = argparse.ArgumentParser(
        description="南宁信息价导入工具 — 从电子书JS提取材料价格导入主材库"
    )
    parser.add_argument("--period", "-p", help="单期期号（如 202512-2）")
    parser.add_argument("--batch", "-b", help="批量期号（逗号分隔，如 202501-1,202501-2）")
    parser.add_argument("--year", "-y", type=int, help="导入整年（自动生成24期，如 2025）")
    parser.add_argument("--dry-run", action="store_true", help="试运行：只看结果，不写库")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")

    args = parser.parse_args()

    if not args.period and not args.batch and not args.year:
        parser.error("请指定 --period、--batch 或 --year")

    # 收集期号
    periods = []
    if args.period:
        periods.append(args.period)
    if args.batch:
        periods.extend(args.batch.split(","))
    if args.year:
        periods.extend(_generate_periods(args.year))

    # 去重
    periods = list(dict.fromkeys(periods))

    print(f"共{len(periods)}期待处理")
    print(f"省份: 广西 | 城市: 南宁")
    print()

    total_imported = 0

    for period in periods:
        period = period.strip()
        date_range = _period_to_date_range(period)
        half = "上半月" if period.endswith("-1") else "下半月"

        print(f"{'='*50}")
        print(f"期号: {period} ({date_range} {half})")
        print(f"{'='*50}")

        # 提取
        records = extract_nanning_period(period, verbose=args.verbose)
        print(f"提取 {len(records)} 条记录")

        if not records:
            print("无记录，跳过")
            print()
            continue

        # 导入
        source_file = f"nanning_{period}.js"
        stats = import_to_db(
            records,
            province="广西",
            period=date_range,
            source_file=source_file,
            dry_run=args.dry_run,
        )

        if not args.dry_run:
            print(f"导入完成: 成功{stats['imported']} "
                  f"跳过{stats['skipped']} "
                  f"过滤{stats['junk_filtered']} "
                  f"失败{stats['errors']}")
            total_imported += stats["imported"]

        print()

    # 最终汇总
    if not args.dry_run and len(periods) > 1:
        print(f"\n{'='*50}")
        print(f"全部完成！共导入 {total_imported} 条")

        db = MaterialDB()
        s = db.stats()
        print(f"\n主材库当前统计:")
        for k, v in s.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
