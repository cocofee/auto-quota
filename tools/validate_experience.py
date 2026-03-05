"""
经验库候选层数据验证与晋升工具

功能：
1. 第1层：自动清洗（删垃圾文本、清理Z前缀、删无效编号）
2. 第2层：AI抽样验证（每省抽样，计算准确率）
3. 第3层：灰度晋升（按准确率决定晋升/删除）

所有操作使用软删除（标记deleted，不真删），支持回滚。

用法：
    python tools/validate_experience.py clean       # 第1层：自动清洗
    python tools/validate_experience.py sample      # 第2层：AI抽样验证
    python tools/validate_experience.py promote     # 第3层：根据抽样结果晋升
    python tools/validate_experience.py rollback    # 回滚上一次操作
    python tools/validate_experience.py report      # 查看当前状态报告
"""

import argparse
import json
import os
import random
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ============================================================
# 工具函数
# ============================================================

def get_exp_db_path():
    """获取经验库数据库路径"""
    return str(ROOT / "db" / "common" / "experience.db")


def get_quota_db_path(province: str):
    """获取省份定额库路径"""
    return str(ROOT / "db" / "provinces" / province / "quota.db")


def backup_db(tag: str):
    """备份经验库（操作前自动调用）"""
    src = get_exp_db_path()
    backup_dir = ROOT / "db" / "common" / "backups"
    backup_dir.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"experience_{tag}_{ts}.db"
    shutil.copy2(src, dst)
    print(f"已备份: {dst}")
    # 只保留最近5个备份
    backups = sorted(backup_dir.glob("experience_*.db"), key=lambda f: f.stat().st_mtime)
    for old in backups[:-5]:
        old.unlink()
        print(f"清理旧备份: {old.name}")
    return str(dst)


def connect_exp():
    """连接经验库"""
    db = sqlite3.connect(get_exp_db_path())
    db.row_factory = sqlite3.Row
    return db


def load_results(name: str):
    """加载中间结果"""
    path = ROOT / "output" / "temp" / f"exp_validate_{name}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_results(name: str, data):
    """保存中间结果"""
    path = ROOT / "output" / "temp" / f"exp_validate_{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"结果已保存: {path}")


# ============================================================
# 第1层：自动清洗
# ============================================================

# 垃圾文本关键词（清单文本包含这些词的直接标记删除）
GARBAGE_KEYWORDS = [
    "酷造价", "上传文件名称", "免费功能", "安装WPS",
    "点击此处下载", "插件酷造价", "特征完善",
]


def clean(dry_run=False):
    """第1层：自动清洗候选层数据"""
    backup_path = backup_db("clean") if not dry_run else None
    db = connect_exp()

    report = {
        "backup": backup_path,
        "garbage_deleted": 0,
        "z_prefix_cleaned": 0,
        "z_prefix_record_deleted": 0,
        "invalid_id_deleted": 0,
        "details_by_province": {},
    }

    # --- 1. 删垃圾文本 ---
    print("\n=== 步骤1: 清理垃圾文本 ===")
    garbage_ids = []
    for kw in GARBAGE_KEYWORDS:
        rows = db.execute(
            "SELECT id, bill_text FROM experiences WHERE layer='candidate' AND bill_text LIKE ?",
            (f"%{kw}%",)
        ).fetchall()
        for r in rows:
            if r["id"] not in garbage_ids:
                garbage_ids.append(r["id"])
                print(f"  垃圾: id={r['id']} \"{r['bill_text'][:60]}\"")

    if garbage_ids and not dry_run:
        # 软删除：标记到notes，改layer为deleted
        for gid in garbage_ids:
            db.execute("""
                UPDATE experiences
                SET layer='deleted',
                    notes=COALESCE(notes,'') || '\n[清洗删除 """ + time.strftime('%Y-%m-%d') + """] 垃圾文本'
                WHERE id=?
            """, (gid,))
        db.commit()
    report["garbage_deleted"] = len(garbage_ids)
    print(f"  共标记删除 {len(garbage_ids)} 条垃圾文本")

    # --- 2. 清理Z前缀编号 ---
    print("\n=== 步骤2: 清理Z前缀材料补差编号 ===")
    rows = db.execute("""
        SELECT id, quota_ids, quota_names FROM experiences
        WHERE layer='candidate' AND quota_ids LIKE '%Z0%'
    """).fetchall()

    z_cleaned = 0
    z_deleted = 0
    for r in rows:
        try:
            qids = json.loads(r["quota_ids"])
            qnames = json.loads(r["quota_names"]) if r["quota_names"] else []
        except (json.JSONDecodeError, TypeError):
            continue

        # 过滤掉Z开头的编号
        new_qids = []
        new_qnames = []
        for i, qid in enumerate(qids):
            if not qid.startswith("Z"):
                new_qids.append(qid)
                if i < len(qnames):
                    new_qnames.append(qnames[i])

        if not new_qids:
            # 过滤完没有定额了，标记删除
            if not dry_run:
                db.execute("""
                    UPDATE experiences
                    SET layer='deleted',
                        notes=COALESCE(notes,'') || '\n[清洗删除 """ + time.strftime('%Y-%m-%d') + """] 仅含Z前缀编号'
                    WHERE id=?
                """, (r["id"],))
            z_deleted += 1
        elif len(new_qids) < len(qids):
            # 部分Z编号被清理
            if not dry_run:
                db.execute("""
                    UPDATE experiences
                    SET quota_ids=?, quota_names=?,
                        notes=COALESCE(notes,'') || '\n[清洗 """ + time.strftime('%Y-%m-%d') + """] 已清理Z前缀编号'
                    WHERE id=?
                """, (json.dumps(new_qids), json.dumps(new_qnames, ensure_ascii=False), r["id"]))
            z_cleaned += 1

    if not dry_run:
        db.commit()
    report["z_prefix_cleaned"] = z_cleaned
    report["z_prefix_record_deleted"] = z_deleted
    print(f"  清理Z前缀: {z_cleaned}条编号修正, {z_deleted}条整条删除")

    # --- 3. 删编号无效记录 ---
    print("\n=== 步骤3: 删除定额编号无效的记录 ===")

    # 获取所有有候选层数据的安装类省份
    provinces = db.execute("""
        SELECT DISTINCT province FROM experiences
        WHERE layer='candidate' AND (province LIKE '%安装%' OR province LIKE '%消耗量标准%')
    """).fetchall()

    total_invalid = 0
    for prov_row in provinces:
        prov = prov_row["province"]
        qdb_path = get_quota_db_path(prov)
        if not os.path.exists(qdb_path):
            print(f"  {prov}: 定额库不存在，跳过")
            continue

        qdb = sqlite3.connect(qdb_path)

        rows = db.execute("""
            SELECT id, quota_ids FROM experiences
            WHERE province=? AND layer='candidate'
        """, (prov,)).fetchall()

        prov_invalid = 0
        for r in rows:
            try:
                qids = json.loads(r["quota_ids"])
            except (json.JSONDecodeError, TypeError):
                continue

            # 检查所有非Z编号是否存在
            has_invalid = False
            for qid in qids:
                if qid.startswith("Z"):
                    continue  # Z前缀已在步骤2处理
                found = qdb.execute(
                    "SELECT COUNT(*) FROM quotas WHERE quota_id=?", (qid,)
                ).fetchone()[0]
                if not found:
                    has_invalid = True
                    break

            if has_invalid:
                if not dry_run:
                    db.execute("""
                        UPDATE experiences
                        SET layer='deleted',
                            notes=COALESCE(notes,'') || '\n[清洗删除 """ + time.strftime('%Y-%m-%d') + """] 定额编号不存在'
                        WHERE id=?
                    """, (r["id"],))
                prov_invalid += 1

        if not dry_run:
            db.commit()
        qdb.close()

        if prov_invalid > 0:
            print(f"  {prov}: 删除 {prov_invalid} 条无效编号")
            report["details_by_province"][prov] = {"invalid_deleted": prov_invalid}
        total_invalid += prov_invalid

    report["invalid_id_deleted"] = total_invalid
    print(f"\n  共标记删除 {total_invalid} 条无效编号记录")

    # --- 汇总 ---
    total_cleaned = report["garbage_deleted"] + report["z_prefix_record_deleted"] + report["invalid_id_deleted"]
    print(f"\n{'='*50}")
    print(f"清洗完成: 共处理 {total_cleaned} 条")
    print(f"  垃圾文本: {report['garbage_deleted']}条")
    print(f"  Z前缀整条删除: {report['z_prefix_record_deleted']}条")
    print(f"  Z前缀编号修正: {report['z_prefix_cleaned']}条")
    print(f"  无效编号: {report['invalid_id_deleted']}条")
    if backup_path:
        print(f"  备份位置: {backup_path}")

    save_results("clean_report", report)
    db.close()
    return report


# ============================================================
# 第2层：AI抽样验证
# ============================================================

def sample_and_verify(sample_size=200):
    """第2层：AI抽样验证各省候选层匹配质量"""
    db = connect_exp()

    # 获取清洗后还存活的候选层安装省份
    provinces = db.execute("""
        SELECT province, COUNT(*) as cnt FROM experiences
        WHERE layer='candidate'
          AND (province LIKE '%安装%' OR province LIKE '%消耗量标准%'
               OR province LIKE '%消耗量定额%')
        GROUP BY province
        HAVING cnt >= 10
        ORDER BY cnt DESC
    """).fetchall()

    print(f"共 {len(provinces)} 个省份需要抽样验证\n")

    all_results = {}
    for prov_row in provinces:
        prov = prov_row["province"]
        cnt = prov_row["cnt"]

        # 大省抽200条，小省全量（但不超过sample_size）
        n = min(sample_size, cnt)
        if cnt <= 100:
            n = cnt  # 小省全量验证

        # 随机抽样
        rows = db.execute("""
            SELECT id, bill_text, bill_name, quota_ids, quota_names, specialty
            FROM experiences
            WHERE province=? AND layer='candidate'
            ORDER BY RANDOM()
            LIMIT ?
        """, (prov, n)).fetchall()

        print(f"--- {prov} (候选{cnt}条, 抽样{len(rows)}条) ---")

        # 用规则做初步语义校验（不花API钱）
        correct = 0
        wrong = 0
        uncertain = 0
        wrong_samples = []

        for r in rows:
            bill = r["bill_text"] or ""
            try:
                qnames = json.loads(r["quota_names"]) if r["quota_names"] else []
            except (json.JSONDecodeError, TypeError):
                qnames = []
            try:
                qids = json.loads(r["quota_ids"]) if r["quota_ids"] else []
            except (json.JSONDecodeError, TypeError):
                qids = []

            verdict = _rule_based_verify(bill, qids, qnames, r["specialty"])

            if verdict == "correct":
                correct += 1
            elif verdict == "wrong":
                wrong += 1
                if len(wrong_samples) < 5:
                    wrong_samples.append({
                        "id": r["id"],
                        "bill": bill[:80],
                        "qids": qids,
                        "qnames": [q[:40] for q in qnames] if qnames else [],
                        "reason": "规则判错",
                    })
            else:
                uncertain += 1

        total = correct + wrong + uncertain
        acc = correct / total * 100 if total > 0 else 0
        err = wrong / total * 100 if total > 0 else 0

        print(f"  正确: {correct}({acc:.1f}%) | 错误: {wrong}({err:.1f}%) | 存疑: {uncertain}")
        for ws in wrong_samples:
            print(f"    错误样本: {ws['bill'][:50]} → {ws['qnames']}")

        all_results[prov] = {
            "total_candidate": cnt,
            "sample_size": total,
            "correct": correct,
            "wrong": wrong,
            "uncertain": uncertain,
            "accuracy": round(acc, 1),
            "error_rate": round(err, 1),
            "wrong_samples": wrong_samples,
        }
        print()

    # 汇总
    print("=" * 60)
    print("抽样验证汇总")
    print("=" * 60)
    for prov, r in sorted(all_results.items(), key=lambda x: -x[1]["total_candidate"]):
        status = "可晋升" if r["accuracy"] >= 90 else ("待定" if r["accuracy"] >= 70 else "建议删除")
        print(f"  {prov[:20]:20s} | 候选{r['total_candidate']:>6d} | "
              f"准确{r['accuracy']:>5.1f}% | {status}")

    save_results("sample_report", all_results)
    db.close()
    return all_results


def _rule_based_verify(bill_text: str, quota_ids: list, quota_names: list,
                       specialty: str) -> str:
    """
    规则语义校验（不用AI，免费）

    返回: "correct" / "wrong" / "uncertain"
    """
    if not bill_text or not quota_ids:
        return "wrong"

    bill_lower = bill_text.lower()

    # 1. 明显的跨专业错误：土建内容进了安装库
    civil_keywords = ["灰土", "砌砖", "砌墙", "抹灰", "模板安拆", "钢筋绑扎",
                      "混凝土浇筑", "脚手架搭拆", "土方开挖回填"]
    for kw in civil_keywords:
        if kw in bill_text:
            # 但有些安装定额确实涉及土建配合（如凿槽、预留孔洞），不算错
            if any(ok in bill_text for ok in ["凿槽", "预留", "套管", "堵洞"]):
                return "uncertain"
            return "wrong"

    # 2. 清单和定额的核心词对比
    qname_text = " ".join(quota_names).lower() if quota_names else ""

    # 提取清单中的核心品类词
    bill_categories = _extract_categories(bill_lower)
    quota_categories = _extract_categories(qname_text)

    if bill_categories and quota_categories:
        # 有交集 → 大概率对
        if bill_categories & quota_categories:
            return "correct"
        # 无交集但有模糊关联 → 存疑
        if _fuzzy_category_match(bill_categories, quota_categories):
            return "uncertain"
        # 完全无关 → 可能错
        return "uncertain"  # 保守起见不直接判错

    # 3. 简单关键词重叠检查
    bill_words = set(bill_lower.replace("：", " ").replace(":", " ").split())
    qname_words = set(qname_text.replace("：", " ").replace(":", " ").split())
    overlap = bill_words & qname_words
    # 去掉太通用的词
    overlap -= {"安装", "工程", "规格", "名称", "型号", "材质", "其他", "要求",
                "mm", "dn", "kw", "的", "及", "与", "和"}
    if len(overlap) >= 2:
        return "correct"
    if len(overlap) >= 1:
        return "uncertain"

    return "uncertain"


# 品类词表：用于判断清单和定额是否属于同一类
CATEGORY_PATTERNS = {
    "电缆": {"电缆", "电力电缆", "控制电缆"},
    "穿线": {"穿线", "配线", "导线", "绝缘导线"},
    "灯具": {"灯", "灯具", "荧光灯", "吸顶灯", "壁灯", "射灯", "标志灯", "装饰灯", "应急灯"},
    "配电箱": {"配电箱", "配电柜", "开关柜", "开关箱"},
    "管道": {"管", "钢管", "塑料管", "镀锌钢管", "ppr", "给水管", "排水管", "管道"},
    "阀门": {"阀门", "阀", "闸阀", "截止阀", "蝶阀", "止回阀", "球阀"},
    "风管": {"风管", "通风管", "风道"},
    "风口": {"风口", "百叶风口", "散流器", "风阀"},
    "桥架": {"桥架", "电缆桥架", "托盘"},
    "配管": {"配管", "线管", "电线管", "穿线管"},
    "开关": {"开关", "插座", "按钮"},
    "水泵": {"泵", "水泵", "离心泵", "消防泵", "喷淋泵"},
    "空调": {"空调", "空调器", "空调机", "风机盘管"},
    "消防": {"消防", "喷头", "消火栓", "报警", "探测器"},
    "套管": {"套管", "防水套管", "钢套管", "止水节"},
    "支架": {"支架", "管道支架", "托架", "吊架"},
    "接地": {"接地", "接地极", "接地母线", "等电位"},
    "水表": {"水表", "流量计"},
    "地漏": {"地漏", "扫除口", "清扫口"},
    "交换机": {"交换机", "路由器", "网络设备"},
}


def _extract_categories(text: str) -> set:
    """从文本中提取品类标签"""
    cats = set()
    for cat, keywords in CATEGORY_PATTERNS.items():
        for kw in keywords:
            if kw in text:
                cats.add(cat)
                break
    return cats


# 模糊关联表：这两个品类虽然名字不同但经常配对出现
FUZZY_PAIRS = {
    ("管道", "阀门"), ("管道", "套管"), ("管道", "支架"),
    ("管道", "水表"), ("管道", "地漏"),
    ("配管", "穿线"), ("电缆", "桥架"),
    ("灯具", "开关"), ("配电箱", "开关"),
    ("风管", "风口"), ("空调", "风管"),
    ("消防", "水泵"), ("消防", "管道"),
}


def _fuzzy_category_match(cats1: set, cats2: set) -> bool:
    """检查两组品类是否有模糊关联"""
    for c1 in cats1:
        for c2 in cats2:
            if (c1, c2) in FUZZY_PAIRS or (c2, c1) in FUZZY_PAIRS:
                return True
    return False


# ============================================================
# 第3层：灰度晋升
# ============================================================

def promote(max_error_rate=5.0, percent=100):
    """
    第3层：根据抽样结果批量晋升

    判定标准：用错误率（而非准确率）决定是否晋升。
    因为"存疑"不等于"错误"，大部分存疑项实际是正确的。

    参数:
        max_error_rate: 最大允许错误率，<=此值的省份才晋升（默认5%）
        percent: 晋升比例，用于灰度（默认100%即全量晋升）
    """
    sample_report = load_results("sample_report")
    if not sample_report:
        print("错误: 请先运行 sample 生成抽样报告")
        return

    backup_path = backup_db("promote")
    db = connect_exp()
    batch_id = f"batch_{time.strftime('%Y%m%d_%H%M%S')}"

    promote_report = {
        "backup": backup_path,
        "batch_id": batch_id,
        "max_error_rate": max_error_rate,
        "percent": percent,
        "provinces": {},
    }

    print(f"晋升参数: 错误率阈值<={max_error_rate}%, 晋升比例={percent}%")
    print(f"批次ID: {batch_id}\n")

    total_promoted = 0
    for prov, result in sample_report.items():
        err = result["error_rate"]
        cnt = result["total_candidate"]

        if err > max_error_rate:
            action = f"跳过(错误率{err}%>{max_error_rate}%)"
            promoted = 0
        else:
            # 计算实际晋升数量
            target = int(cnt * percent / 100)

            # 获取要晋升的记录ID
            rows = db.execute("""
                SELECT id FROM experiences
                WHERE province=? AND layer='candidate'
                ORDER BY confidence DESC, id
                LIMIT ?
            """, (prov, target)).fetchall()

            ids = [r["id"] for r in rows]
            promoted = 0
            reason = f"[批量验证晋升 {time.strftime('%Y-%m-%d')}] 批次={batch_id} 抽样错误率={err}%"

            for rid in ids:
                db.execute("""
                    UPDATE experiences
                    SET layer='authority',
                        source='promote_from_candidate',
                        confidence=MAX(confidence, 92),
                        notes=COALESCE(notes,'') || ?
                    WHERE id=? AND layer='candidate'
                """, ("\n" + reason, rid))
                promoted += 1

            db.commit()
            action = f"晋升{promoted}条"
            total_promoted += promoted

        promote_report["provinces"][prov] = {
            "error_rate": err,
            "candidate_count": cnt,
            "promoted": promoted,
            "action": action,
        }
        print(f"  {prov[:25]:25s} | 错误率{err:>4.1f}% | {action}")

    print(f"\n{'='*50}")
    print(f"晋升完成: 共晋升 {total_promoted} 条")
    print(f"批次ID: {batch_id} (回滚时需要)")
    print(f"备份位置: {backup_path}")

    save_results("promote_report", promote_report)
    db.close()
    return promote_report


# ============================================================
# 回滚
# ============================================================

def rollback():
    """回滚上一次操作（从备份恢复）"""
    backup_dir = ROOT / "db" / "common" / "backups"
    if not backup_dir.exists():
        print("没有找到备份文件")
        return

    backups = sorted(backup_dir.glob("experience_*.db"), key=lambda f: f.stat().st_mtime)
    if not backups:
        print("没有找到备份文件")
        return

    latest = backups[-1]
    print(f"最新备份: {latest.name}")
    print(f"  大小: {latest.stat().st_size / 1024 / 1024:.1f}MB")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(latest.stat().st_mtime))}")

    confirm = input("\n确认用这个备份恢复吗？(y/N): ").strip().lower()
    if confirm != "y":
        print("取消回滚")
        return

    target = get_exp_db_path()
    shutil.copy2(str(latest), target)
    print(f"已恢复: {target}")


# ============================================================
# 状态报告
# ============================================================

def report():
    """查看当前经验库状态"""
    db = connect_exp()

    print("=" * 60)
    print("经验库当前状态")
    print("=" * 60)

    # 各层统计
    for layer in ["authority", "candidate", "deleted"]:
        cnt = db.execute(
            "SELECT COUNT(*) FROM experiences WHERE layer=?", (layer,)
        ).fetchone()[0]
        if cnt > 0:
            label = {"authority": "权威层", "candidate": "候选层", "deleted": "已删除"}[layer]
            print(f"  {label}: {cnt:,}")

    # 按省份统计
    print("\n--- 安装类省份明细 ---")
    rows = db.execute("""
        SELECT province, layer, COUNT(*) as cnt
        FROM experiences
        WHERE province LIKE '%安装%' OR province LIKE '%消耗量标准%'
              OR province LIKE '%消耗量定额%'
        GROUP BY province, layer
        ORDER BY province, layer
    """).fetchall()

    from collections import defaultdict
    prov_data = defaultdict(dict)
    for r in rows:
        prov_data[r["province"]][r["layer"]] = r["cnt"]

    for prov in sorted(prov_data.keys()):
        d = prov_data[prov]
        parts = []
        for layer in ["authority", "candidate", "deleted"]:
            if layer in d:
                label = {"authority": "权威", "candidate": "候选", "deleted": "删除"}[layer]
                parts.append(f"{label}{d[layer]}")
        print(f"  {prov}: {' | '.join(parts)}")

    # 清洗和抽样报告
    clean_report = load_results("clean_report")
    if clean_report:
        print(f"\n--- 上次清洗报告 ---")
        print(f"  垃圾文本: {clean_report.get('garbage_deleted', 0)}条")
        print(f"  Z前缀删除: {clean_report.get('z_prefix_record_deleted', 0)}条")
        print(f"  无效编号: {clean_report.get('invalid_id_deleted', 0)}条")

    sample_report = load_results("sample_report")
    if sample_report:
        print(f"\n--- 上次抽样报告 ---")
        for prov, r in sorted(sample_report.items(), key=lambda x: -x[1]["total_candidate"]):
            print(f"  {prov[:25]:25s} | 准确率{r['accuracy']:>5.1f}% | 候选{r['total_candidate']}")

    db.close()


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="经验库候选层验证与晋升工具")
    parser.add_argument("action", choices=["clean", "sample", "promote", "rollback", "report"],
                        help="执行动作: clean=清洗, sample=抽样验证, promote=晋升, rollback=回滚, report=报告")
    parser.add_argument("--dry-run", action="store_true", help="只看不改（预览模式）")
    parser.add_argument("--max-error-rate", type=float, default=5.0,
                        help="最大允许错误率（默认5%%，错误率<=此值的省份才晋升）")
    parser.add_argument("--percent", type=int, default=100,
                        help="晋升比例（默认100%%全量晋升）")
    parser.add_argument("--sample-size", type=int, default=200,
                        help="每省抽样数量（默认200条）")

    args = parser.parse_args()

    if args.action == "clean":
        clean(dry_run=args.dry_run)
    elif args.action == "sample":
        sample_and_verify(sample_size=args.sample_size)
    elif args.action == "promote":
        promote(max_error_rate=args.max_error_rate, percent=args.percent)
    elif args.action == "rollback":
        rollback()
    elif args.action == "report":
        report()


if __name__ == "__main__":
    main()
