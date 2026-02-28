"""
经验库异常检测脚本（最终版）
只做查询分析，不做任何修改

已排除的误报：
- 品类冲突：排除清单文本中附带描述导致的多品类误识别
- 专业交叉：排除风机/泵→C4电动机调试等合理跨专业
- 编号格式：各省不同编号体系（两段、四段、无C前缀）都算合法
- 重复卡片：同bill_name不同规格对应不同定额是正常的
"""
import sqlite3
import json
import sys
import re
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = 'db/common/experience.db'

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute('SELECT id, bill_text, bill_name, quota_ids, quota_names, province, layer, source, specialty FROM experiences')
all_rows = [dict(r) for r in cur.fetchall()]
auth_count = sum(1 for r in all_rows if r['layer'] == 'authority')
cand_count = sum(1 for r in all_rows if r['layer'] == 'candidate')
print(f'总共加载 {len(all_rows)} 条记录 (authority={auth_count}, candidate={cand_count})')


def parse_json_field(val):
    if not val:
        return []
    try:
        result = json.loads(val)
        return result if isinstance(result, list) else [result]
    except Exception:
        return [val]


def get_quota_book(quota_id):
    """从定额编号提取册号"""
    if not quota_id or not isinstance(quota_id, str):
        return None
    qid = quota_id.strip()
    m = re.match(r'(C\d+)', qid)
    if m:
        return m.group(1)
    m = re.match(r'(\d+)-\d+-\d+', qid)
    if m:
        return 'C' + m.group(1)
    return None


BOOK_NAMES = {
    'C1': '机械设备', 'C2': '热力设备', 'C3': '静置设备',
    'C4': '电气', 'C5': '智能化', 'C6': '工业管道',
    'C7': '通风空调', 'C8': '工业管道', 'C9': '消防',
    'C10': '给排水', 'C11': '市政', 'C12': '刷油防腐',
}


# ============================================================
# 检测1: 品类冲突（精确版）
# 只用 bill_name 做品类判断（不用 bill_text，避免附带描述误触发）
# ============================================================
print('\n' + '=' * 60)
print('=== 检测1: 品类冲突（清单名称 vs 定额名称的核心品类不匹配） ===')
print('=' * 60)

# 核心品类关键词（只用于 bill_name 的短文本）
CORE_CATEGORIES = {
    '泵': ['泵', '水泵', '增压泵', '循环泵', '潜水泵', '排污泵', '离心泵', '消防泵'],
    '阀门': ['闸阀', '蝶阀', '止回阀', '截止阀', '球阀', '减压阀', '安全阀', '电磁阀',
             '平衡阀', '排气阀', '泄压阀', '调节阀'],
    '管道': ['钢管', '铸铁管', '铜管', '塑料管', '复合管', '排水管', '给水管'],
    '风管': ['风管'],
    '电缆': ['电缆', '电力电缆', '控制电缆'],
    '灯具': ['灯具', '筒灯', '射灯', '吸顶灯', '日光灯', 'LED灯', '荧光灯', '路灯',
             '应急灯', '标志灯', '疏散灯', '防爆灯', '泛光灯', '壁灯'],
    '开关插座': ['开关面板', '开关插座', '双联开关', '三联开关', '单联开关'],
    '配电箱柜': ['配电箱', '配电柜', '控制箱', '控制柜', '动力箱', '照明箱'],
    '桥架': ['桥架', '线槽'],
    '喷头': ['喷头', '喷淋头', '洒水喷头'],
    '探测器': ['探测器', '感烟探测', '感温探测', '报警按钮', '声光报警'],
    '风机': ['风机', '排烟风机', '送风机', '排风机', '轴流风机'],
    '风口': ['风口', '散流器', '排风口', '送风口', '回风口'],
    '洁具': ['大便器', '小便器', '蹲便器', '坐便器', '洗脸盆', '洗手盆', '浴缸', '淋浴器'],
    '接地': ['接地极', '接地线', '避雷带', '接闪器'],
}


def classify_core(text):
    """只匹配核心品类"""
    if not text:
        return set()
    cats = set()
    for cat, kws in CORE_CATEGORIES.items():
        for kw in kws:
            if kw in text:
                cats.add(cat)
                break
    return cats


# 严格的冲突对：电气大类 vs 水暖大类
ELECTRIC_CATS = {'电缆', '灯具', '开关插座', '配电箱柜', '桥架', '探测器', '接地'}
WATER_CATS = {'泵', '阀门', '管道', '洁具', '喷头'}
HVAC_CATS = {'风管', '风机', '风口'}

# 同大类内的不算冲突，跨大类才算
def is_real_conflict(bill_cat, quota_cat):
    """判断两个品类是否真的冲突"""
    if bill_cat == quota_cat:
        return False
    # 同一大类内不冲突
    for group in [ELECTRIC_CATS, WATER_CATS, HVAC_CATS]:
        if bill_cat in group and quota_cat in group:
            return False
    # 跨大类冲突
    groups = [ELECTRIC_CATS, WATER_CATS, HVAC_CATS]
    bill_group = None
    quota_group = None
    for g in groups:
        if bill_cat in g:
            bill_group = g
        if quota_cat in g:
            quota_group = g
    if bill_group and quota_group and bill_group != quota_group:
        return True
    return False


category_conflicts = []
for row in all_rows:
    # 只用bill_name做品类判断
    bill_name = row['bill_name'] or ''
    bill_cats = classify_core(bill_name)

    quota_names_list = parse_json_field(row['quota_names'])
    # 每个定额名称单独判断品类
    all_quota_cats = set()
    for qn in quota_names_list:
        all_quota_cats |= classify_core(str(qn) if qn else '')

    if not bill_cats or not all_quota_cats:
        continue

    conflicts = []
    for bc in bill_cats:
        for qc in all_quota_cats:
            if is_real_conflict(bc, qc):
                conflicts.append((bc, qc))

    if conflicts:
        # 排除共有品类
        common = bill_cats & all_quota_cats
        real_conflicts = [(bc, qc) for bc, qc in conflicts if bc not in common and qc not in common]
        if real_conflicts:
            category_conflicts.append({
                'row': row,
                'bill_cats': bill_cats,
                'quota_cats': all_quota_cats,
                'conflicts': real_conflicts
            })

category_conflicts.sort(key=lambda x: (0 if x['row']['layer'] == 'authority' else 1, x['row']['id']))
auth_cnt = sum(1 for c in category_conflicts if c['row']['layer'] == 'authority')
cand_cnt = sum(1 for c in category_conflicts if c['row']['layer'] == 'candidate')
print(f'共发现 {len(category_conflicts)} 条可疑 (authority={auth_cnt}, candidate={cand_cnt})')
print()
for i, item in enumerate(category_conflicts[:10]):
    r = item['row']
    qids = parse_json_field(r['quota_ids'])
    qnames = parse_json_field(r['quota_names'])
    conflict_str = ', '.join(f'{bc}->{qc}' for bc, qc in item['conflicts'])
    tag = '★' if r['layer'] == 'authority' else ' '
    print(f'  {tag}[{r["id"]}] [{r["layer"]}] 省份:{r["province"][:30]}')
    print(f'    清单名: {(r["bill_name"] or "")[:50]}')
    print(f'    定额: {", ".join(str(q) for q in qids)} | {", ".join(str(q)[:60] for q in qnames)}')
    print(f'    冲突: {conflict_str}')
    print()
if len(category_conflicts) > 10:
    print(f'  ... 还有 {len(category_conflicts) - 10} 条未显示')


# ============================================================
# 检测2: 专业交叉异常
# ============================================================
print('\n' + '=' * 60)
print('=== 检测2: 专业交叉异常（清单→定额的册号完全不合理） ===')
print('=' * 60)

# 只用bill_name判断专业（更精确）
BILL_SPECIALTY = {
    '给排水': ['给水管', '排水管', '铸铁管', '洁具', '大便器', '小便器',
               '洗脸盆', '洗手盆', '地漏', '水龙头', '淋浴'],
    '电气': ['配电箱', '配电柜', '灯具', '筒灯', '射灯', '应急灯',
             '桥架', '线槽'],
    '通风空调': ['风管', '消声器', '散流器'],
}

# 只报告最不合理的跨专业
CROSS_RULES = [
    ('给排水', ['C4', 'C5'], '给排水清单->电气/智能化定额'),
    ('电气', ['C10'], '电气清单->给排水定额'),
    ('电气', ['C7'], '电气清单->通风空调定额'),
    ('通风空调', ['C10'], '通风空调清单->给排水定额'),
]

cross_issues = []
for row in all_rows:
    bill_name = row['bill_name'] or ''
    quota_ids = parse_json_field(row['quota_ids'])

    # 判断专业
    bill_specs = set()
    for spec, kws in BILL_SPECIALTY.items():
        for kw in kws:
            if kw in bill_name:
                bill_specs.add(spec)
                break

    quota_books = set()
    for qid in quota_ids:
        book = get_quota_book(str(qid))
        if book:
            quota_books.add(book)

    if not bill_specs or not quota_books:
        continue

    for spec, bad_books, desc in CROSS_RULES:
        if spec not in bill_specs:
            continue
        for book in quota_books:
            if book not in bad_books:
                continue
            # 如果同时有正确的主册号，跳过（是补充定额）
            expected = {'给排水': 'C10', '电气': 'C4', '通风空调': 'C7'}
            if expected.get(spec) in quota_books:
                continue
            cross_issues.append({
                'row': row,
                'bill_specs': bill_specs,
                'quota_books': quota_books,
                'desc': desc,
                'bad_book': book,
            })
            break

cross_issues.sort(key=lambda x: (0 if x['row']['layer'] == 'authority' else 1, x['row']['id']))
auth_cnt = sum(1 for c in cross_issues if c['row']['layer'] == 'authority')
cand_cnt = sum(1 for c in cross_issues if c['row']['layer'] == 'candidate')
print(f'共发现 {len(cross_issues)} 条可疑 (authority={auth_cnt}, candidate={cand_cnt})')
print()
for i, item in enumerate(cross_issues[:10]):
    r = item['row']
    qids = parse_json_field(r['quota_ids'])
    qnames = parse_json_field(r['quota_names'])
    tag = '★' if r['layer'] == 'authority' else ' '
    book_name = BOOK_NAMES.get(item['bad_book'], item['bad_book'])
    print(f'  {tag}[{r["id"]}] [{r["layer"]}] 省份:{r["province"][:30]}')
    print(f'    清单名: {(r["bill_name"] or "")[:50]}')
    print(f'    定额: {", ".join(str(q) for q in qids)} | {", ".join(str(q)[:60] for q in qnames)}')
    print(f'    问题: {item["desc"]} (定额册:{item["quota_books"]}->{book_name})')
    print()
if len(cross_issues) > 10:
    print(f'  ... 还有 {len(cross_issues) - 10} 条未显示')


# ============================================================
# 检测3: 定额编号格式异常（只报告真正异常的）
# ============================================================
print('\n' + '=' * 60)
print('=== 检测3: 定额编号格式异常（排除各省合法编号体系） ===')
print('=' * 60)

# 各省编号格式大汇总（全部合法）：
# 标准格式: C10-2-79, A1-1-1
# 无前缀三段: 4-2-78, 10-1-5
# 无前缀两段: 1-42 (某些省的简写)
# 四段: 2-4-17-22 (某些省)
# Z编号: Z00169@6 (补充定额)
# B编号: B001, B002 (补充定额)
# C编号: C00187@1 (补充定额)
#
# 真正异常的：空值、None、纯文字、超长字符串

LOOSE_VALID = [
    re.compile(r'^[A-Z]{0,3}\d{1,3}(-\d{1,5}){1,4}[a-zA-Z]?$'),  # 各种x-x-x格式
    re.compile(r'^[A-Z]\d{3,6}(@\d+)?$'),                          # Z00169@6, B001, C00187@1
]

format_issues = []
for row in all_rows:
    quota_ids = parse_json_field(row['quota_ids'])
    problems = []

    if not quota_ids:
        problems.append('quota_ids为空列表')

    for qid in quota_ids:
        if qid is None:
            problems.append('包含None值')
        elif not isinstance(qid, str):
            problems.append(f'非字符串: {type(qid).__name__}={qid}')
        elif qid.strip() == '':
            problems.append('空字符串')
        else:
            qc = qid.strip()
            if not any(p.match(qc) for p in LOOSE_VALID):
                # 最后一道防线：至少要有数字
                if not re.search(r'\d', qc):
                    problems.append(f'无数字: "{qc}"')
                elif len(qc) > 30:
                    problems.append(f'过长: "{qc[:25]}..."')
                elif ' ' in qc:
                    problems.append(f'含空格: "{qc}"')
                # 其他的可能是特殊但合法的格式，不报告

    if problems:
        format_issues.append({
            'row': row,
            'problems': problems,
            'raw_quota_ids': row['quota_ids'],
        })

format_issues.sort(key=lambda x: (0 if x['row']['layer'] == 'authority' else 1, x['row']['id']))
auth_cnt = sum(1 for c in format_issues if c['row']['layer'] == 'authority')
cand_cnt = sum(1 for c in format_issues if c['row']['layer'] == 'candidate')
print(f'共发现 {len(format_issues)} 条可疑 (authority={auth_cnt}, candidate={cand_cnt})')

if format_issues:
    ptype_counts = defaultdict(int)
    for item in format_issues:
        for p in item['problems']:
            pt = p.split(':')[0] if ':' in p else p
            ptype_counts[pt] += 1
    print('问题类型:')
    for pt, cnt in sorted(ptype_counts.items(), key=lambda x: -x[1]):
        print(f'  {pt}: {cnt}条')
    print()
    for i, item in enumerate(format_issues[:10]):
        r = item['row']
        tag = '★' if r['layer'] == 'authority' else ' '
        print(f'  {tag}[{r["id"]}] [{r["layer"]}] 省份:{r["province"][:30]}')
        print(f'    清单: {(r["bill_name"] or "")[:40]}')
        print(f'    原始quota_ids: {item["raw_quota_ids"][:80]}')
        print(f'    问题: {"; ".join(item["problems"][:3])}')
        print()
    if len(format_issues) > 10:
        print(f'  ... 还有 {len(format_issues) - 10} 条未显示')


# ============================================================
# 检测4: 重复卡片
# ============================================================
print('\n' + '=' * 60)
print('=== 检测4: 重复卡片 ===')
print('=' * 60)

# 4a: 完全相同的bill_text + 相同省份 → 不同定额（真冲突）
groups_text = defaultdict(list)
for row in all_rows:
    key = (row['province'], row['bill_text'])
    groups_text[key].append(row)

conflict_text = []
redundant_text = []
for key, rows in groups_text.items():
    if len(rows) < 2:
        continue
    qsets = set()
    for r in rows:
        qids = tuple(sorted(parse_json_field(r['quota_ids'])))
        qsets.add(qids)
    entry = {
        'province': key[0], 'bill_text': key[1], 'rows': rows,
        'distinct': len(qsets),
        'has_auth': any(r['layer'] == 'authority' for r in rows),
    }
    if len(qsets) > 1:
        conflict_text.append(entry)
    else:
        redundant_text.append(entry)

print(f'同省同bill_text: {len(conflict_text)} 组定额冲突, {len(redundant_text)} 组冗余(同定额多张卡)')

if conflict_text:
    print('\n--- 定额冲突 ---')
    for i, dup in enumerate(conflict_text[:5]):
        tag = '★含authority' if dup['has_auth'] else '  仅candidate'
        print(f'  {tag} 省份:{dup["province"][:30]} | {len(dup["rows"])}张, {dup["distinct"]}种定额')
        print(f'    清单: {(dup["bill_text"] or "")[:80]}')
        for r in dup['rows']:
            qids = parse_json_field(r['quota_ids'])
            qnames = parse_json_field(r['quota_names'])
            print(f'      [{r["id"]}][{r["layer"]}][{r["source"]}] -> {", ".join(str(q) for q in qids)} | {", ".join(str(q)[:50] for q in qnames)}')
        print()

if redundant_text:
    total_cards = sum(len(d['rows']) for d in redundant_text)
    auth_redundant = sum(1 for d in redundant_text for r in d['rows'] if r['layer'] == 'authority')
    cand_redundant = sum(1 for d in redundant_text for r in d['rows'] if r['layer'] == 'candidate')
    print(f'\n--- 冗余卡片(同text同定额) ---')
    print(f'共 {len(redundant_text)} 组 涉及 {total_cards} 张 (authority={auth_redundant}, candidate={cand_redundant})')
    for i, dup in enumerate(redundant_text[:5]):
        print(f'  省份:{dup["province"][:30]} | {len(dup["rows"])}张')
        print(f'    清单: {(dup["bill_text"] or "")[:80]}')
        for r in dup['rows']:
            print(f'      [{r["id"]}][{r["layer"]}][{r["source"]}]')
        print()
    if len(redundant_text) > 5:
        print(f'  ... 还有 {len(redundant_text) - 5} 组未显示')


# ============================================================
# 汇总
# ============================================================
print('\n' + '=' * 60)
print('=== 最终汇总 ===')
print('=' * 60)
print(f'数据库总记录: {len(all_rows)} (authority={auth_count}, candidate={cand_count})')
print()
c1_auth = sum(1 for c in category_conflicts if c['row']['layer'] == 'authority')
c2_auth = sum(1 for c in cross_issues if c['row']['layer'] == 'authority')
c3_auth = sum(1 for c in format_issues if c['row']['layer'] == 'authority')
print(f'  检测1 品类冲突:       {len(category_conflicts):>4} 条 (authority={c1_auth})')
print(f'  检测2 专业交叉异常:   {len(cross_issues):>4} 条 (authority={c2_auth})')
print(f'  检测3 编号格式异常:   {len(format_issues):>4} 条 (authority={c3_auth})')
print(f'  检测4 同text定额冲突: {len(conflict_text):>4} 组')
print(f'  检测4 同text冗余卡片: {len(redundant_text):>4} 组')
print()
print('各检测说明:')
print('  检测1: 清单名和定额名属于完全不同的大品类（电气vs水暖vs暖通）')
print('  检测2: 给排水清单用了电气册定额，或反过来（无主册号补充的情况）')
print('  检测3: 定额编号为空/None/含空格/纯文字等异常格式')
print('  检测4: 完全相同的清单文本在同省份下指向不同定额，或完全重复')
print()
total_real = len(category_conflicts) + len(cross_issues) + len(format_issues) + len(conflict_text)
total_auth = c1_auth + c2_auth + c3_auth + sum(1 for d in conflict_text if d['has_auth'])
print(f'需关注的异常总计: {total_real} 条/组 (其中authority层: {total_auth})')
print()
if total_real == 0:
    print('数据库整体质量良好，未发现严重异常。')
else:
    print('建议优先处理authority层的问题，因为authority层数据直接参与经验库直通匹配。')

conn.close()
