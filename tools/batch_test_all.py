"""
批量测试所有清单文件，输出汇总报告
用法: python tools/batch_test_all.py
"""
import subprocess
import json
import glob
import os
import sys
import time

# 测试文件目录
TEST_DIRS = [
    r"D:\广联达临时文件\2025",
    r"D:\广联达临时文件\2026",
]

# 项目内已有的测试文件
PROJECT_FILES = [
    r"output\temp\7#配套楼-小栗AI自动编清单202602072236.xlsx",
    r"output\temp\3A#8#楼-给排水-小栗AI自动编清单2025120818.json",  # 跳过json
]

def find_all_test_files():
    """扫描所有测试文件"""
    files = []
    for d in TEST_DIRS:
        for root, dirs, filenames in os.walk(d):
            for f in filenames:
                if "小栗AI" in f and f.endswith(".xlsx"):
                    files.append(os.path.join(root, f))
    files.sort()
    return files

def run_test(filepath, with_experience=False):
    """运行单个测试文件，返回统计结果"""
    try:
        cmd = [sys.executable, "tools/review_test.py", filepath]
        if with_experience:
            cmd.append("--with-experience")
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=300,
            cwd=r"C:\Users\Administrator\Documents\trae_projects\auto-quota",
            encoding="utf-8", errors="replace"
        )
        # 从输出中提取统计数据
        output = result.stdout + result.stderr
        # 查找JSON输出文件路径
        json_path = None
        for line in output.split('\n'):
            if 'review_' in line and '.json' in line:
                # 提取路径
                parts = line.split()
                for p in parts:
                    if 'review_' in p and '.json' in p:
                        json_path = p.strip()
                        break
        return json_path, result.returncode
    except subprocess.TimeoutExpired:
        return None, -1
    except Exception as e:
        return None, -2

def analyze_results(json_path):
    """分析单个测试结果，返回汇总统计和红色项详情"""
    if not json_path or not os.path.exists(json_path):
        return None
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    results = data.get("results", [])
    if not results:
        return None

    total = len(results)
    green = sum(1 for r in results if r.get("confidence", 0) >= 85)
    yellow = sum(1 for r in results if 60 <= r.get("confidence", 0) < 85)
    red = sum(1 for r in results if r.get("confidence", 0) < 60)
    rule_count = sum(1 for r in results if r.get("match_source") == "rule")

    # 收集红色项详情（置信度<60的），方便后续分析
    red_items = []
    for r in results:
        if r.get("confidence", 0) < 60:
            # review JSON 结构：bill_item 嵌套清单信息，quotas 嵌套定额信息
            bill = r.get("bill_item", {})
            quotas = r.get("quotas", [])
            first_quota = quotas[0] if quotas else {}
            red_items.append({
                "bill_name": bill.get("name", ""),
                "bill_desc": (bill.get("description", "") or "")[:80],
                "quota_id": first_quota.get("quota_id", ""),
                "quota_name": first_quota.get("name", ""),
                "confidence": r.get("confidence", 0),
                "source": r.get("match_source", ""),
            })

    return {
        "total": total, "green": green, "yellow": yellow, "red": red,
        "rule_count": rule_count,
        "green_pct": round(green / total * 100, 1),
        "project_name": data.get("project_name", ""),
        "red_items": red_items,
    }

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-experience", action="store_true",
                        help="使用经验库匹配")
    args = parser.parse_args()

    files = find_all_test_files()
    print(f"找到 {len(files)} 个测试文件")
    if args.with_experience:
        print(">>> 经验库模式：开启")
    print()

    all_stats = []
    total_items = 0
    total_green = 0
    total_yellow = 0
    total_red = 0

    for i, filepath in enumerate(files):
        basename = os.path.basename(filepath)
        short_name = basename.replace("-小栗AI自动编清单", "").replace(".xlsx", "")
        if len(short_name) > 40:
            short_name = short_name[:40] + "..."

        print(f"[{i+1}/{len(files)}] 测试: {short_name} ...", end=" ", flush=True)
        start = time.time()
        json_path, returncode = run_test(filepath, with_experience=args.with_experience)
        elapsed = time.time() - start

        if returncode != 0:
            print(f"失败(code={returncode}) 耗时{elapsed:.0f}秒")
            all_stats.append({"file": short_name, "error": True})
            continue

        # 找到json文件（可能路径解析不对，尝试用glob）
        if not json_path or not os.path.exists(json_path):
            # 尝试在output/review/目录找最新的json
            review_dir = r"C:\Users\Administrator\Documents\trae_projects\auto-quota\output\review"
            json_files = glob.glob(os.path.join(review_dir, "review_*.json"))
            if json_files:
                json_path = max(json_files, key=os.path.getmtime)

        stats = analyze_results(json_path)
        if stats:
            print(f"总{stats['total']:3d}条  绿{stats['green']:3d} 黄{stats['yellow']:3d} 红{stats['red']:3d}  "
                  f"({stats['green_pct']}%) 规则{stats['rule_count']}条  耗时{elapsed:.0f}秒")
            stats["file"] = short_name
            all_stats.append(stats)
            total_items += stats["total"]
            total_green += stats["green"]
            total_yellow += stats["yellow"]
            total_red += stats["red"]
        else:
            print(f"无结果 耗时{elapsed:.0f}秒")
            all_stats.append({"file": short_name, "error": True})

    # 汇总
    print("\n" + "=" * 80)
    print("汇总报告")
    print("=" * 80)
    print(f"{'文件':<45s} {'总':>4s} {'绿':>4s} {'黄':>4s} {'红':>4s} {'绿%':>6s}")
    print("-" * 80)
    for s in all_stats:
        if s.get("error"):
            print(f"{s['file']:<45s}  错误")
        else:
            print(f"{s['file']:<45s} {s['total']:4d} {s['green']:4d} {s['yellow']:4d} {s['red']:4d} {s['green_pct']:5.1f}%")
    print("-" * 80)
    if total_items > 0:
        print(f"{'合计':<45s} {total_items:4d} {total_green:4d} {total_yellow:4d} {total_red:4d} "
              f"{total_green/total_items*100:5.1f}%")

    # 收集并展示所有红色项
    all_red_items = []
    for s in all_stats:
        if not s.get("error") and s.get("red_items"):
            for item in s["red_items"]:
                item["file"] = s["file"]
                all_red_items.append(item)

    if all_red_items:
        print(f"\n{'='*80}")
        print(f"红色项详情 ({len(all_red_items)}条)")
        print(f"{'='*80}")
        for item in all_red_items:
            print(f"  [{item['confidence']:2d}] {item['file'][:25]:<25s} | "
                  f"{item['bill_name'][:20]:<20s} → {item['quota_id']} {item['quota_name'][:30]}")
        # 按名称分组统计
        from collections import Counter
        name_counts = Counter(item['bill_name'] for item in all_red_items)
        if len(name_counts) > 1:
            print(f"\n红色项按名称统计:")
            for name, count in name_counts.most_common(15):
                print(f"  {count:>2}x  {name}")

    # 保存结果到文件（包含红色项详情）
    report_path = os.path.join(
        r"C:\Users\Administrator\Documents\trae_projects\auto-quota\output",
        "batch_test_report.json"
    )
    # 保存时移除 red_items 避免 details 太大，红色项单独存
    details_clean = []
    for s in all_stats:
        s_copy = {k: v for k, v in s.items() if k != "red_items"}
        details_clean.append(s_copy)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_files": len(files),
            "total_items": total_items,
            "total_green": total_green,
            "total_yellow": total_yellow,
            "total_red": total_red,
            "green_pct": round(total_green / total_items * 100, 1) if total_items else 0,
            "details": details_clean,
            "red_items": all_red_items,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n报告已保存: {report_path}")

if __name__ == "__main__":
    main()
