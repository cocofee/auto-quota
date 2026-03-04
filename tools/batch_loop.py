"""批量循环匹配脚本：20个一批，休息10秒，直到安装类全部处理完"""
import subprocess, time, sqlite3, sys, os

DB_PATH = 'output/batch/batch.db'
SPECIALTIES = ['消防', '电气', '给排水', '智能化', '通风空调', '电力', '市政', '园林景观', '钢结构幕墙']
BATCH_SIZE = 20
SLEEP_SEC = 10

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

while True:
    remaining = get_remaining()
    total_left = sum(remaining.values())
    if total_left == 0:
        print(f'\n===== 全部处理完成! 共跑{batch_no}批 =====')
        break

    top_spec = max(remaining, key=remaining.get)
    batch_no += 1
    print(f'\n===== 第{batch_no}批 | 剩余{total_left}个 | 跑 {top_spec}({remaining[top_spec]}个剩余) =====', flush=True)

    try:
        result = subprocess.run(
            [sys.executable, 'tools/batch_runner.py', '--specialty', top_spec, '--limit', str(BATCH_SIZE)],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=600,
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

    except subprocess.TimeoutExpired:
        print(f'  超时! 跳过本批', flush=True)
    except Exception as e:
        print(f'  异常: {e}', flush=True)

    print(f'  休息{SLEEP_SEC}秒...', flush=True)
    time.sleep(SLEEP_SEC)
