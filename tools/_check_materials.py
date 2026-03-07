# -*- coding: utf-8 -*-
"""临时脚本：查看经验库主材数据样例"""
import sqlite3, json, sys
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('db/common/experience.db')

# 随机看8条有主材的样例
rows = conn.execute(
    "SELECT quota_ids, quota_names, materials FROM experiences "
    "WHERE layer='authority' AND materials IS NOT NULL "
    "AND materials != '' AND materials != '[]' "
    "ORDER BY RANDOM() LIMIT 8"
).fetchall()

for r in rows:
    mats = json.loads(r[2])
    print(f"定额: {r[0]} -> {r[1]}")
    for m in mats[:3]:
        name = m.get('name', '?')
        unit = m.get('unit', '?')
        price = m.get('price', '')
        print(f"  - {name} | 单位:{unit} | 价格:{price}")
    if len(mats) > 3:
        print(f"  ...共{len(mats)}项")
    print()

# 统计：平均每条经验有几项主材
avg = conn.execute(
    "SELECT AVG(json_array_length(materials)) FROM experiences "
    "WHERE layer='authority' AND materials IS NOT NULL "
    "AND materials != '' AND materials != '[]'"
).fetchone()[0]
print(f"平均每条经验的主材项数: {avg:.1f}")

conn.close()
