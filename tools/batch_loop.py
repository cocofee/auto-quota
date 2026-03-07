"""批量循环匹配脚本：20个一批，休息10秒，直到安装类全部处理完
用法: python tools/batch_loop.py
特性: 低优先级运行，超时自动跳过换下一个专业
"""
import subprocess, time, sqlite3, sys, os, psutil

DB_PATH = 'output/batch/batch.db'
SPECIALTIES = ['消防', '电气', '给排水', '智能化', '通风空调', '电力', '市政', '园林景观', '钢结构幕墙']
BATCH_SIZE = 20
SLEEP_SEC = 10

# 把自己设为低优先级（不抢CPU）
try:
    p = psutil.Process()
    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    print('已设为低优先级运行', flush=True)
except Exception:
    pass

def get_remaining():
    conn = sqlite3.connect(DB_PATH)
    placeholders = ','.join(['?'] * len(SPECIALTIES))
    rows = conn.execute(
        f'SELECT specialty, COUNT(*) as cnt FROM file_registry '
        f'WHERE status = "scanned" AND specialty IN ({placeholders}) '
        f'GROUP BY specialty ORDER BY cnt DESC',
        SPECIALTIES
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}

batch_no = 0
skip_specs = set()  # 超时的专业暂时跳过

while True:
    remaining = get_remaining()
    # 排除超时的专业
    available = {k: v for k, v in remaining.items() if k not in skip_specs}
    total_left = sum(remaining.values())

    if total_left == 0:
        print(f'\n===== 全部处理完成! 共跑{batch_no}批 =====')
        break

    if not available:
        # 所有专业都超时过，重置跳过列表再试
        print(f'\n所有专业都超时过，重置后继续...', flush=True)
        skip_specs.clear()
        available = remaining

    # 找剩余最多的专业
    top_spec = max(available, key=available.get)
    batch_no += 1
    print(f'\n===== 第{batch_no}批 | 剩余{total_left}个 | 跑 {top_spec}({remaining[top_spec]}个剩余) =====', flush=True)

    try:
        # 子进程也用低优先级
        creation_flags = 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
        result = subprocess.run(
            [sys.executable, 'tools/batch_runner.py', '--specialty', top_spec, '--limit', str(BATCH_SIZE)],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=600,
            creationflags=creation_flags,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
        )

        for line in result.stdout.split('\n'):
            if any(k in line for k in ['成功:', '错误:', '清单总条数', '条 |', '跳过', '批量匹配完成']):
                clean = line.strip()
                if clean:
                    print(f'  {clean}', flush=True)

        if result.returncode != 0:
            for line in result.stderr.split('\n')[-5:]:
                if line.strip():
                    print(f'  ERR: {line.strip()}', flush=True)

        # 成功了就从跳过列表移除
        skip_specs.discard(top_spec)

    except subprocess.TimeoutExpired:
        print(f'  超时! 跳过{top_spec}，先跑别的', flush=True)
        skip_specs.add(top_spec)
    except Exception as e:
        print(f'  异常: {e}', flush=True)

    print(f'  休息{SLEEP_SEC}秒...', flush=True)
    time.sleep(SLEEP_SEC)
