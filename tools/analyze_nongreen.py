"""
统计匹配结果中的非绿色条目（低于阈值的待审核项）。

用法:
  python tools/analyze_nongreen.py output/temp/match_result_xxx.json
  python tools/analyze_nongreen.py output/temp/match_result_xxx.json --threshold 85 --top 20
"""

import argparse
import json
import sys
from pathlib import Path


def _load_results(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"结果文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        results = data.get("results")
        if not isinstance(results, list):
            raise ValueError("JSON结构错误：根节点对象缺少 results 数组")
        return results
    if isinstance(data, list):
        return data
    raise ValueError("JSON结构错误：根节点必须是对象或数组")


def _as_int(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def analyze(results, threshold: int, top_n: int):
    total = len(results)
    nongreen = []
    unmatched = 0
    malformed = 0

    for idx, r in enumerate(results, start=1):
        if not isinstance(r, dict):
            malformed += 1
            continue
        item = r.get("bill_item", {}) if isinstance(r.get("bill_item"), dict) else {}
        confidence = _as_int(r.get("confidence", 0), 0)
        quotas = r.get("quotas")
        has_match = isinstance(quotas, list) and len(quotas) > 0
        if not has_match:
            unmatched += 1
        if confidence < threshold:
            nongreen.append({
                "idx": idx,
                "confidence": confidence,
                "name": str(item.get("name", "")),
                "code": str(item.get("code", "")),
                "reason": str(r.get("no_match_reason") or r.get("explanation") or ""),
                "match_source": str(r.get("match_source", "")),
            })

    nongreen.sort(key=lambda x: x["confidence"])

    print("=" * 60)
    print(f"总条目: {total}")
    print(f"阈值(<{threshold})非绿色: {len(nongreen)} ({len(nongreen) * 100 / max(total, 1):.1f}%)")
    print(f"未匹配: {unmatched}")
    if malformed:
        print(f"结构异常条目(已跳过): {malformed}")
    print("=" * 60)

    if not nongreen:
        print("无非绿色条目。")
        return

    print(f"最低置信度前 {min(top_n, len(nongreen))} 条:")
    for row in nongreen[:top_n]:
        code = f"[{row['code']}]" if row["code"] else ""
        reason = row["reason"].replace("\n", " ").strip()
        if len(reason) > 80:
            reason = reason[:80] + "..."
        print(
            f"- #{row['idx']} {code} {row['name']} | {row['confidence']}% | "
            f"{row['match_source']} | {reason}"
        )


def main():
    parser = argparse.ArgumentParser(description="分析匹配结果中的非绿色条目")
    parser.add_argument("result_json", help="匹配结果JSON路径（main.py --json-output 产物）")
    parser.add_argument("--threshold", type=int, default=85, help="绿色阈值，默认85")
    parser.add_argument("--top", type=int, default=15, help="输出最低置信度TopN，默认15")
    args = parser.parse_args()

    result_path = Path(args.result_json)
    try:
        results = _load_results(result_path)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as e:
        print(f"读取失败: {e}")
        sys.exit(1)

    threshold = max(0, min(100, int(args.threshold)))
    top_n = max(1, int(args.top))
    analyze(results, threshold=threshold, top_n=top_n)


if __name__ == "__main__":
    main()
