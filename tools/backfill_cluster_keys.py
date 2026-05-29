"""离线预处理: 把 national_index 的 cluster_key 写入经验库

跑一次即可, 后续 OSS 导入时直接在 import 流程中写入。
"""

import sqlite3, json, time, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXP_DB = PROJECT_ROOT / "db" / "common" / "experience.db"
NAT_DB = PROJECT_ROOT / "data" / "goal_search" / "national_index.sqlite"


def backfill():
    exp = sqlite3.connect(str(EXP_DB))
    nat = sqlite3.connect(str(NAT_DB))

    # 1. 加列
    try:
        exp.execute("ALTER TABLE experiences ADD COLUMN cluster_key TEXT")
        exp.commit()
        print("已添加 cluster_key 列")
    except sqlite3.OperationalError:
        pass  # already exists

    # 2. 预加载 national_index 的 (province_prefix, quota_id) → cluster_key
    print("加载 national_index 索引...")
    t0 = time.time()
    rows = nat.execute(
        "SELECT quota_id, province, cluster_key FROM national_quotas "
        "WHERE cluster_key IS NOT NULL AND cluster_key != ''"
    ).fetchall()

    # Build lookup: (quota_id, province_prefix) → cluster_key
    lookup = {}
    for qid, prov, ck in rows:
        prov_prefix = prov[:8]  # first 8 chars of province name
        key = (qid, prov_prefix)
        if key not in lookup:
            lookup[key] = ck
    print(f"  索引: {len(lookup)} 条目, {time.time()-t0:.1f}s")

    # 3. 遍历经验库，匹配 cluster_key
    exp_rows = exp.execute(
        "SELECT id, quota_ids, province FROM experiences WHERE cluster_key IS NULL"
    ).fetchall()

    updated = 0
    for eid, quota_ids_json, province in exp_rows:
        if not quota_ids_json:
            continue
        try:
            quota_ids = json.loads(quota_ids_json)
        except Exception:
            continue
        prov_prefix = (province or "")[:8]

        ck = None
        for qid in quota_ids:
            ck = lookup.get((qid, prov_prefix))
            if ck:
                break

        if ck:
            exp.execute("UPDATE experiences SET cluster_key=? WHERE id=?", (ck, eid))
            updated += 1
            if updated % 5000 == 0:
                exp.commit()
                print(f"  已更新 {updated}/{len(exp_rows)}")

    exp.commit()
    total_done = exp.execute("SELECT COUNT(*) FROM experiences WHERE cluster_key IS NOT NULL").fetchone()[0]
    print(f"\n完成: {updated} 条更新, 总计 {total_done} 条有 cluster_key "
          f"({total_done*100/max(len(exp_rows),1):.1f}%)")

    exp.close()
    nat.close()


if __name__ == "__main__":
    backfill()
