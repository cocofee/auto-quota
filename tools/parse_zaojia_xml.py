# -*- coding: utf-8 -*-
"""
外部造价软件XML文件解析器

支持三种格式：
1. .13jk (JingJiBiao) — 江苏新点格式
2. .XML (浙江省数据标准) — 浙江省建设工程计价成果文件
3. .XML (GCZJWJ) — 福建等省工程造价文件

核心功能：从XML中提取 (清单名称, 项目特征, 定额编号, 定额名称) 对照数据
"""

import os
import sys
import json
import xml.etree.ElementTree as ET
from collections import defaultdict


def parse_13jk(tree):
    """解析 .13jk (JingJiBiao) 格式

    结构：JingJiBiao > Dxgcxx > Dwgcxx > Qdxm > QdBt > Qdmx > Qdxdezj > QdxdezjMx
    """
    root = tree.getroot()
    if root.tag != 'JingJiBiao':
        return None

    result = {
        'format': '13jk',
        'project_name': root.get('Xmmc', ''),
        'version': root.get('Version', ''),
        'unit_projects': [],  # 单位工程列表
        'pairs': [],  # (清单, 定额) 对
    }

    for dwgc in root.findall('.//Dwgcxx'):
        unit = {
            'name': dwgc.get('Dwgcmc', ''),
            'code': dwgc.get('Dwgcbh', ''),
            'specialty_code': dwgc.get('Zylb', ''),  # 专业类别
            'software': dwgc.get('SoftName', ''),
        }
        result['unit_projects'].append(unit)

        # 遍历清单项
        for qdmx in dwgc.findall('.//Qdmx'):
            bill = {
                'code': qdmx.get('Qdbm', ''),
                'name': qdmx.get('Mc', ''),
                'feature': qdmx.get('Xmtz', ''),
                'unit': qdmx.get('Dw', ''),
                'quantity': qdmx.get('Sl', ''),
            }

            # 提取定额子目
            quotas = []
            for dezj in qdmx.findall('.//QdxdezjMx'):
                quotas.append({
                    'code': dezj.get('Debm', ''),
                    'name': dezj.get('Mc', ''),
                    'unit': dezj.get('Dw', ''),
                    'quantity': dezj.get('DwQdSl', ''),
                })

            if quotas:
                result['pairs'].append({
                    'bill': bill,
                    'quotas': quotas,
                    'unit_project': unit['name'],
                    'specialty_code': unit.get('specialty_code', ''),
                })

    return result


def parse_zhejiang(tree):
    """解析浙江省建设工程计价成果文件数据标准

    结构：浙江省... > 项目数据信息 > 单位工程列表 > 专业工程列表
         > 分部分项工程量清单表 > 分部分项工程量清单表标题 > 分部分项工程量清单表记录
         > 分部分项综合单价分析表
    """
    root = tree.getroot()
    if '浙江' not in root.tag and '计价成果' not in root.tag:
        return None

    result = {
        'format': 'zhejiang',
        'project_name': '',
        'unit_projects': [],
        'pairs': [],
    }

    # 项目信息
    proj_info = root.find('建设项目信息表')
    if proj_info is not None:
        result['project_name'] = proj_info.get('项目名称', '')

    # 遍历单位工程
    for unit_elem in root.findall('.//单位工程列表'):
        unit_name = unit_elem.get('单位工程名称', '')

        # 遍历专业工程
        for spec_elem in unit_elem.findall('专业工程列表'):
            spec_name = spec_elem.get('专业工程名称', '')
            spec_type = spec_elem.get('专业类型', '')

            result['unit_projects'].append({
                'name': f'{unit_name}-{spec_name}',
                'specialty_name': spec_name,
                'specialty_type': spec_type,
            })

            # 遍历清单表
            for qd_table in spec_elem.findall('分部分项工程量清单表'):
                # 清单记录是标题的子元素（标题→记录→定额）
                for title_elem in qd_table.findall('分部分项工程量清单表标题'):
                    section_name = title_elem.get('名称', '')

                    for record in title_elem.findall('分部分项工程量清单表记录'):
                        bill = {
                            'code': record.get('项目编码', ''),
                            'name': record.get('项目名称', ''),
                            'feature': record.get('项目特征', ''),
                            'unit': record.get('计量单位', ''),
                            'quantity': record.get('工程量', ''),
                        }

                        # 提取定额（综合单价分析表）
                        quotas = []
                        for de in record.findall('分部分项综合单价分析表'):
                            q_name = de.get('名称', '')
                            # 去掉 \x7f换为【...】 这种替换标记，保留原始名称
                            if '\x7f' in q_name:
                                q_name = q_name.split('\x7f')[0].strip()
                            quotas.append({
                                'code': de.get('编码', ''),
                                'name': q_name,
                                'unit': de.get('单位', ''),
                                'quantity': de.get('数量', ''),
                            })

                        if quotas:
                            result['pairs'].append({
                                'bill': bill,
                                'quotas': quotas,
                                'unit_project': unit_name,
                                'specialty_name': spec_name,
                                'section': section_name,
                            })

    return result


def parse_gczjwj(tree):
    """解析 GCZJWJ（工程造价文件）格式 — 广联达导出的标准XML

    结构：GCZJWJ > GCZJZC > DXGC > DWGC > FBFX > FBGC > QDXM > DEZM
    标签说明：
      QDXM = 清单项目（属性：XMBM编码, XMMC名称, XMTZ特征, JLDW单位, GCSL数量）
      DEZM = 定额子目（属性：DEBH编号, XMMC名称, JLDW单位, GCSL数量）
    """
    root = tree.getroot()
    if root.tag != 'GCZJWJ':
        return None

    result = {
        'format': 'gczjwj',
        'project_name': root.get('GCMC', ''),
        'unit_projects': [],
        'pairs': [],
    }

    # 专业类别（市政/安装/房建等）
    zylb = root.get('ZYLB', '')

    # 遍历单位工程
    for dwgc in root.findall('.//DWGC'):
        unit_name = dwgc.get('DWGCMC', '')
        unit_zylb = dwgc.get('ZYLB', '') or zylb  # 单位工程级别的专业类别
        result['unit_projects'].append({
            'name': unit_name,
            'specialty': unit_zylb,
        })

        # 清单项目在 FBFX > FBGC > QDXM 下
        for qd in dwgc.findall('.//QDXM'):
            bill = {
                'code': qd.get('XMBM', ''),
                'name': qd.get('XMMC', ''),
                'feature': qd.get('XMTZ', ''),
                'unit': qd.get('JLDW', '') or qd.get('DW', ''),
                'quantity': qd.get('GCSL', '') or qd.get('SL', ''),
            }

            # 定额子目在 QDXM > DEZM 下
            quotas = []
            for de in qd.findall('.//DEZM'):
                quotas.append({
                    'code': de.get('DEBH', '') or de.get('DEBM', ''),
                    'name': de.get('XMMC', '') or de.get('DEMC', ''),
                    'unit': de.get('JLDW', '') or de.get('DW', ''),
                    'quantity': de.get('GCSL', '') or de.get('SL', ''),
                })

            if quotas and bill['name']:
                result['pairs'].append({
                    'bill': bill,
                    'quotas': quotas,
                    'unit_project': unit_name,
                })

    return result


def parse_zaojia_home_xml(tree):
    """解析外部造价软件XML格式（mergedRoot结构）

    结构：root > mergedRoot > fileContents > 单位工程 > 分部分项清单 > 清单分部 > 清单项目
         > 组价内容 > 定额子目
    """
    root = tree.getroot()
    merged = root.find('mergedRoot')
    if merged is None:
        return None

    result = {
        'format': 'zaojia_home',
        'project_name': '',
        'unit_projects': [],
        'pairs': [],
    }

    for unit in merged.findall('.//单位工程'):
        unit_name = unit.get('工程名称', '')
        spec_code = unit.get('工程专业', '')

        result['unit_projects'].append({
            'name': unit_name,
            'specialty_code': spec_code,
        })

        for qd in unit.findall('.//清单项目'):
            # 提取项目特征
            features = []
            for f in qd.findall('.//特征明细'):
                content = f.get('内容', '')
                if content:
                    features.append(content)

            bill = {
                'code': qd.get('项目编码', ''),
                'name': qd.get('项目名称', ''),
                'feature': '\n'.join(features),
                'unit': qd.get('计量单位', ''),
                'quantity': qd.get('工程量', ''),
            }

            quotas = []
            for de in qd.findall('.//定额子目'):
                quotas.append({
                    'code': de.get('定额编号', ''),
                    'name': de.get('项目名称', ''),
                    'unit': de.get('计量单位', ''),
                    'quantity': de.get('工程量', ''),
                    'specialty': de.get('定额专业类别', ''),
                })

            if quotas and bill['name']:
                result['pairs'].append({
                    'bill': bill,
                    'quotas': quotas,
                    'unit_project': unit_name,
                    'specialty_code': spec_code,
                })

    return result


def parse_file(filepath):
    """自动识别格式并解析文件"""
    try:
        tree = ET.parse(filepath)
    except ET.ParseError as e:
        return None, f'XML解析错误: {e}'

    root = tree.getroot()
    tag = root.tag

    # 按根元素标签判断格式
    if tag == 'JingJiBiao':
        return parse_13jk(tree), None
    elif '浙江' in tag or '计价成果' in tag:
        return parse_zhejiang(tree), None
    elif tag == 'GCZJWJ':
        return parse_gczjwj(tree), None
    elif tag == 'root':
        merged = root.find('mergedRoot')
        if merged is not None:
            return parse_zaojia_home_xml(tree), None

    return None, f'未知格式: <{tag}>'


def extract_bill_quota_pairs(result):
    """从解析结果中提取干净的 (清单, 定额) 对照数据"""
    if not result or not result.get('pairs'):
        return []

    pairs = []
    for p in result['pairs']:
        bill = p['bill']
        bill_name = bill.get('name', '').strip()
        bill_feature = bill.get('feature', '').strip()
        bill_code = bill.get('code', '').strip()
        bill_unit = bill.get('unit', '').strip()

        if not bill_name:
            continue

        quota_list = []
        for q in p.get('quotas', []):
            q_code = q.get('code', '').strip()
            q_name = q.get('name', '').strip()
            if q_name and q_code != '市场价':  # 跳过市场价（不是定额）
                quota_list.append({
                    'code': q_code,
                    'name': q_name,
                    'unit': q.get('unit', ''),
                })

        if quota_list:
            pairs.append({
                'bill_name': bill_name,
                'bill_feature': bill_feature,
                'bill_code': bill_code,
                'bill_unit': bill_unit,
                'quotas': quota_list,
                'unit_project': p.get('unit_project', ''),
                'specialty': p.get('specialty_name', '') or p.get('specialty_code', ''),
            })

    return pairs


def main():
    """测试解析样本文件"""
    import glob

    samples_dir = 'output/temp/zaojia_samples'
    if not os.path.exists(samples_dir):
        print(f'样本目录不存在: {samples_dir}')
        return

    total_pairs = 0
    for fpath in sorted(glob.glob(f'{samples_dir}/*')):
        fname = os.path.basename(fpath)
        result, error = parse_file(fpath)

        if error:
            print(f'{fname}: {error}')
            continue

        if result is None:
            print(f'{fname}: 解析返回空')
            continue

        pairs = extract_bill_quota_pairs(result)
        total_pairs += len(pairs)

        print(f'\n{fname}: [{result["format"]}] {result["project_name"][:40]}')
        print(f'  单位工程: {len(result["unit_projects"])} | 清单-定额对: {len(pairs)}')

        # 打印前3条样例
        for p in pairs[:3]:
            q_names = ' + '.join(q['name'][:25] for q in p['quotas'])
            print(f'  {p["bill_name"][:25]} → {q_names}')

    print(f'\n总计: {total_pairs} 条清单-定额对')


if __name__ == '__main__':
    main()
