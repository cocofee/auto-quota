"""批量循环匹配脚本：按优先级轮转跑
用法:
    python tools/batch_loop.py --mode day    # 白天：只跑安装类标准清单
    python tools/batch_loop.py --mode night  # 晚上：全部都跑
特性:
    - 专业之间轮转（消防跑一批→电气跑一批→给排水跑一批→...→回到消防）
    - 同一专业内省份也轮转（北京跑一批→上海跑一批→...）
    - 这样每个省的数据都能比较均匀地积累
    - 低优先级运行，断点续跑
"""
import subprocess, time, sqlite3, sys, os, argparse, psutil
from collections import defaultdict

DB_PATH = 'output/batch/batch.db'

# 安装核心专业（白天只跑这些）
INSTALL_SPECS = ['消防', '电气', '给排水', '通风空调', '智能化']

# 全部专业（晚上跑）
ALL_SPECS = [
    '消防', '电气', '给排水', '通风空调', '智能化',
    '电力', '钢结构幕墙',
    '市政', '园林景观',
]

BATCH_SIZE = 5
SLEEP_SEC = 5

# 省份列表（用于平均分配未知省份 + 轮转顺序）
PROVINCES = [
    '北京', '上海', '天津', '重庆',
    '广东', '江苏', '浙江', '山东', '河南', '河北',
    '湖北', '湖南', '四川', '福建', '安徽', '江西',
    '辽宁', '吉林', '黑龙江', '山西', '陕西', '甘肃',
    '云南', '贵州', '广西', '海南', '内蒙古', '西藏',
    '宁夏', '青海', '新疆',
]


def assign_unknown_provinces():
    """把未知省份的scanned文件平均分配到各省。"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT file_path FROM file_registry "
        "WHERE (province IS NULL OR province = '') AND status = 'scanned'"
    ).fetchall()
    if not rows:
        conn.close()
        return 0

    count = len(rows)
    n = len(PROVINCES)
    for i, row in enumerate(rows):
        conn.execute(
            "UPDATE file_registry SET province = ? WHERE file_path = ?",
            (PROVINCES[i % n], row[0])
        )
    conn.commit()
    conn.close()
    print(f'已将 {count} 个未知省份文件平均分配到 {n} 个省', flush=True)
    return count


def get_remaining_by_province(specialties, format_filter=None):
    """查询每个(专业, 省份)的剩余数量。
    返回: {专业: {省份: 数量}}
    """
    conn = sqlite3.connect(DB_PATH)
    placeholders = ','.join(['?'] * len(specialties))

    if format_filter == 'standard_bill':
        fmt_clause = "AND format = 'standard_bill'"
    elif format_filter == 'non_standard':
        fmt_clause = "AND format IN ('work_list', 'equipment_list')"
    else:
        fmt_clause = ""

    rows = conn.execute(
        f'SELECT specialty, province, COUNT(*) as cnt FROM file_registry '
        f'WHERE status = "scanned" AND specialty IN ({placeholders}) '
        f'{fmt_clause} '
        f'GROUP BY specialty, province',
        specialties
    ).fetchall()
    conn.close()

    result = defaultdict(dict)
    for r in rows:
        spec, prov, cnt = r
        if prov:  # 跳过省份为空的
            result[spec][prov] = cnt
    return dict(result)


def run_batch_round(round_name, specialties, format_filter=None):
    """跑一轮匹配，专业和省份都轮转，直到全部跑完。"""
    batch_no = 0
    skip_keys = set()  # (专业, 省份) 超时的跳过

    print(f'\n{"="*60}', flush=True)
    print(f'  {round_name}', flush=True)
    print(f'{"="*60}', flush=True)

    # 专业轮转指针
    spec_idx = 0

    while True:
        remaining = get_remaining_by_province(specialties, format_filter)
        total_left = sum(cnt for provs in remaining.values() for cnt in provs.values())

        if total_left == 0:
            print(f'\n  {round_name} 完成!（共跑{batch_no}批）', flush=True)
            break

        # 找下一个有活干的专业（轮转）
        found = False
        for _ in range(len(specialties)):
            spec = specialties[spec_idx % len(specialties)]
            spec_idx += 1

            if spec not in remaining:
                continue

            # 在这个专业内，找有活干的省份（按PROVINCES顺序轮转）
            prov_remaining = remaining[spec]
            for prov in PROVINCES:
                if prov in prov_remaining and (spec, prov) not in skip_keys:
                    found = True
                    break

            if not found:
                # 这个专业所有省都跳过了或没活了，试下一个专业
                continue
            break

        if not found:
            # 所有都超时过，重置
            if skip_keys:
                print(f'\n  重置超时列表，继续...', flush=True)
                skip_keys.clear()
                continue
            else:
                break

        batch_no += 1
        prov_count = prov_remaining[prov]
        print(f'\n--- 第{batch_no}批 | 剩余{total_left}个 | {spec}/{prov}({prov_count}个) ---', flush=True)

        # 构建命令
        cmd = [sys.executable, 'tools/batch_runner.py',
               '--specialty', spec, '--province', prov,
               '--limit', str(BATCH_SIZE)]
        if format_filter == 'standard_bill':
            cmd += ['--format', 'standard_bill']
        elif format_filter == 'non_standard':
            cmd += ['--format', 'work_list']

        try:
            creation_flags = 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
            result = subprocess.run(
                cmd, timeout=600,
                creationflags=creation_flags,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
            )

            # 非标准格式补跑equipment_list
            if format_filter == 'non_standard':
                cmd2 = cmd.copy()
                cmd2[cmd2.index('work_list')] = 'equipment_list'
                subprocess.run(
                    cmd2, timeout=600,
                    creationflags=creation_flags,
                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
                )

            skip_keys.discard((spec, prov))

        except subprocess.TimeoutExpired:
            print(f'  超时! 跳过{spec}/{prov}', flush=True)
            skip_keys.add((spec, prov))
        except Exception as e:
            print(f'  异常: {e}', flush=True)

        time.sleep(SLEEP_SEC)


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='批量匹配')
    parser.add_argument('--mode', choices=['day', 'night'], default='night',
                        help='day=白天(安装类标准清单) night=全部')
    args = parser.parse_args()

    is_day = (args.mode == 'day')

    # 低优先级
    try:
        p = psutil.Process()
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        print('已设为低优先级运行', flush=True)
    except Exception:
        pass

    # 自动分配未知省份
    assign_unknown_provinces()

    # 显示总览
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM file_registry WHERE status='scanned'").fetchone()[0]

    if is_day:
        specs = INSTALL_SPECS
        placeholders = ','.join(['?'] * len(specs))
        count = conn.execute(
            f"SELECT COUNT(*) FROM file_registry WHERE status='scanned' "
            f"AND format='standard_bill' AND specialty IN ({placeholders})", specs
        ).fetchone()[0]
        conn.close()

        print(f'\n白天模式：安装类标准清单 {count}个（总{total}个）', flush=True)
        print(f'专业轮转: {" → ".join(specs)}，省份也轮转', flush=True)
        run_batch_round('安装类标准清单', specs, format_filter='standard_bill')

    else:
        specs = ALL_SPECS
        conn.close()

        print(f'\n晚上模式：全部 {total}个文件', flush=True)
        print(f'专业轮转: {" → ".join(specs)}，省份也轮转', flush=True)
        run_batch_round('第1轮：标准清单', specs, format_filter='standard_bill')
        run_batch_round('第2轮：非标准清单', specs, format_filter='non_standard')

        remaining = get_remaining_by_province(specs)
        if sum(cnt for provs in remaining.values() for cnt in provs.values()) > 0:
            run_batch_round('第3轮：剩余', specs, format_filter=None)

    print(f'\n{"="*60}', flush=True)
    print(f'  全部完成!', flush=True)
    print(f'{"="*60}', flush=True)


if __name__ == '__main__':
    main()
