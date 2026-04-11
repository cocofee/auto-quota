"""
Minimal LLM rerank validation over benchmark error cases.

Usage:
    export OPENAI_API_KEY="sk-..."
    python tools/llm_rerank_test.py

PowerShell:
    $env:OPENAI_API_KEY="sk-..."
    python tools/llm_rerank_test.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


DEFAULT_BENCHMARK_ERRORS = "output/benchmark_assets/20260324_152948/all_errors.jsonl"
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_MAX_CASES = 20
DEFAULT_MAX_CANDIDATES = 10
DEFAULT_PROVINCE_FILTER = "北京"
DEFAULT_OUTPUT = "output/llm_rerank_test_results.json"
ALLOWED_CAUSES = {"wrong_tier", "synonym_gap", "wrong_book"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM reranker 最小验证脚本")
    parser.add_argument("--benchmark-errors", default=DEFAULT_BENCHMARK_ERRORS, help="benchmark all_errors.jsonl 路径")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI 模型名，默认 gpt-5.4")
    parser.add_argument("--province", default=DEFAULT_PROVINCE_FILTER, help="省份过滤关键词，默认 北京")
    parser.add_argument("--max-cases", type=int, default=DEFAULT_MAX_CASES, help="最多测试多少条案例")
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES, help="每题给 LLM 的候选数")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="详细结果输出 JSON 路径")
    parser.add_argument("--sleep-sec", type=float, default=0.5, help="每次请求后的限速秒数")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", ""),
        help="可选的 OpenAI 兼容 base_url，默认读取 OPENAI_BASE_URL",
    )
    return parser.parse_args()


def get_correct_ids(case: dict) -> set[str]:
    ids = case.get("stored_ids") or case.get("expected_quota_ids") or []
    return {str(item).strip() for item in ids if str(item).strip()}


def get_algo_choice(case: dict) -> tuple[str, str]:
    quota_id = str(case.get("algo_id") or case.get("predicted_quota_id") or "").strip()
    name = str(case.get("algo_name") or case.get("predicted_quota_name") or "").strip()
    return quota_id, name


def load_rerank_cases(
    path: str,
    province_filter: str = DEFAULT_PROVINCE_FILTER,
    max_cases: int = DEFAULT_MAX_CASES,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> list[dict]:
    cases: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            case = json.loads(line)
            province = str(case.get("province", ""))
            if province_filter and province_filter not in province:
                continue
            if not case.get("oracle_in_candidates"):
                continue
            if str(case.get("cause", "")) not in ALLOWED_CAUSES:
                continue
            if not get_correct_ids(case):
                continue
            if not extract_candidates(case, max_candidates=max_candidates):
                continue
            cases.append(case)
            if len(cases) >= max_cases:
                break
    return cases


def extract_candidates(case: dict, max_candidates: int) -> list[dict]:
    candidates: list[dict] = []
    seen_ids: set[str] = set()

    def _append_candidate(
        quota_id: str,
        name: str,
        *,
        selected: bool = False,
        reason: str = "",
        param_detail: str = "",
    ) -> None:
        qid = str(quota_id or "").strip()
        qname = str(name or "").strip()
        if not qid or qid in seen_ids:
            return
        candidates.append(
            {
                "quota_id": qid,
                "name": qname,
                "is_selected": bool(selected),
                "reason": str(reason or "").strip(),
                "param_detail": str(param_detail or "").strip(),
            }
        )
        seen_ids.add(qid)

    retrieved = case.get("retrieved_candidates") or []
    if isinstance(retrieved, list) and retrieved:
        selected_rows = []
        other_rows = []
        for row in retrieved:
            if not isinstance(row, dict):
                continue
            if row.get("is_selected"):
                selected_rows.append(row)
            else:
                other_rows.append(row)
        for row in selected_rows + other_rows:
            reasoning = row.get("reasoning") or {}
            _append_candidate(
                row.get("quota_id", ""),
                row.get("name", ""),
                selected=bool(row.get("is_selected")),
                reason=row.get("reason", ""),
                param_detail=reasoning.get("detail", "") or row.get("param_detail", ""),
            )
            if len(candidates) >= max_candidates:
                return candidates

    algo_id, algo_name = get_algo_choice(case)
    _append_candidate(algo_id, algo_name, selected=True)
    if len(candidates) >= max_candidates:
        return candidates

    for alt in case.get("alternatives") or []:
        if not isinstance(alt, dict):
            continue
        reasoning = alt.get("reasoning") or {}
        _append_candidate(
            alt.get("quota_id", ""),
            alt.get("name", ""),
            reason=alt.get("reason", ""),
            param_detail=alt.get("param_detail", "") or reasoning.get("detail", ""),
        )
        if len(candidates) >= max_candidates:
            break
    return candidates


def extract_bill_params(bill_text: str) -> dict[str, str]:
    text = str(bill_text or "").strip()
    patterns = {
        "name": r"(?:名称|项目名称)[:：]\s*([^\n，,；;]+)",
        "spec": r"(?:规格|规格型号)[:：]\s*([^\n，,；;]+)",
        "material": r"材质[:：]\s*([^\n，,；;]+)",
        "connection": r"(?:连接方式|连接)[:：]\s*([^\n，,；;]+)",
        "install_method": r"(?:安装方式|敷设方式|敷设|安装)[:：]\s*([^\n，,；;]+)",
        "model": r"(?:型号|型号规格)[:：]\s*([^\n，,；;]+)",
        "type": r"类型[:：]\s*([^\n，,；;]+)",
        "structure": r"(?:结构|构造)[:：]\s*([^\n，,；;]+)",
        "voltage": r"(?:电压等级|电压)[:：]\s*([^\n，,；;]+)",
    }
    params: dict[str, str] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            params[key] = match.group(1).strip()

    dn_match = re.search(r"\bDN\s*([0-9]+)\b", text, flags=re.IGNORECASE)
    if dn_match:
        params["dn"] = dn_match.group(1)

    section_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:mm2|mm²|平方毫米)", text, flags=re.IGNORECASE)
    if section_match:
        params["section"] = section_match.group(1)

    size_match = re.search(r"([0-9]+)\s*[*xX×]\s*([0-9]+)", text)
    if size_match:
        params["size"] = f"{size_match.group(1)}x{size_match.group(2)}"

    return params


def format_bill_params(params: dict[str, str]) -> str:
    ordered_keys = [
        "name",
        "dn",
        "spec",
        "section",
        "size",
        "material",
        "connection",
        "install_method",
        "structure",
        "type",
        "model",
        "voltage",
    ]
    parts = []
    for key in ordered_keys:
        value = str(params.get(key, "")).strip()
        if value:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def build_prompt(bill_text: str, candidates: list[dict]) -> tuple[str, dict[str, str]]:
    candidate_lines: list[str] = []
    candidate_map: dict[str, str] = {}

    for idx, candidate in enumerate(candidates, start=1):
        quota_id = str(candidate.get("quota_id", "")).strip()
        name = str(candidate.get("name", "")).strip()
        param_detail = str(candidate.get("param_detail", "")).strip()
        reason = str(candidate.get("reason", "")).strip()
        detail = param_detail or reason
        if detail:
            candidate_lines.append(f"{idx}. [{quota_id}] {name} | {detail}")
        else:
            candidate_lines.append(f"{idx}. [{quota_id}] {name}")
        candidate_map[str(idx)] = quota_id

    candidate_text = "\n".join(candidate_lines)
    bill_param_text = format_bill_params(extract_bill_params(bill_text)) or "无"

    prompt = f"""你是中国工程造价定额匹配专家。

任务：给定一条工程清单项，从候选定额中选出最匹配的一条。

选择原则：
1. 品类必须一致（管道配管道，阀门配阀门，灯具配灯具）
2. 规格参数（DN、截面、半周长等）应该匹配，或按“向上取最近档”原则选择
3. 材质、连接方式、安装方式应尽量一致
4. 优先选专用定额，不选泛化/通用定额
5. 不要选明显跨专业、跨品类的定额

清单项：
{bill_text}
提取参数：{bill_param_text}

候选定额：
{candidate_text}

请只输出最匹配的候选编号（例如 1 或 2 或 3），不要输出任何其他内容。"""
    return prompt, candidate_map


def create_client(api_key: str, base_url: str = ""):
    from openai import OpenAI

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def call_llm(client, prompt: str, model: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=5,
        temperature=0,
    )
    return str(response.choices[0].message.content or "").strip()


def parse_choice(llm_response: str, candidate_map: dict[str, str]) -> tuple[str, str]:
    nums = re.findall(r"\d+", llm_response or "")
    if not nums:
        return "", ""
    idx = nums[0]
    return idx, candidate_map.get(idx, "")


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if not api_key:
        print("请设置 OPENAI_API_KEY 环境变量")
        print('  export OPENAI_API_KEY="sk-..."')
        print('  或 PowerShell: $env:OPENAI_API_KEY="sk-..."')
        return 1

    benchmark_path = Path(args.benchmark_errors)
    if not benchmark_path.exists():
        print(f"找不到 benchmark 文件: {benchmark_path}")
        print("请确认路径是否正确")
        return 1

    print("加载 benchmark 案例...")
    cases = load_rerank_cases(
        str(benchmark_path),
        province_filter=args.province,
        max_cases=args.max_cases,
        max_candidates=args.max_candidates,
    )
    print(f"找到 {len(cases)} 条可测案例\n")

    if not cases:
        print("没有找到符合条件的案例")
        return 0

    client = create_client(api_key=api_key, base_url=args.base_url)

    fixed_count = 0
    failed_count = 0
    skipped_pool_count = 0
    results: list[dict] = []

    for index, case in enumerate(cases, start=1):
        bill_text = str(case.get("bill_text", "")).strip()
        cause = str(case.get("cause", "")).strip()
        correct_ids = get_correct_ids(case)
        algo_id, algo_name = get_algo_choice(case)
        candidates = extract_candidates(case, max_candidates=args.max_candidates)

        if not candidates:
            print(f"[{index}] 跳过：没有可用候选")
            continue

        candidate_ids = {str(item.get("quota_id", "")).strip() for item in candidates}
        if not (correct_ids & candidate_ids):
            print(f"[{index}] 跳过：正确答案不在给 LLM 的 top{len(candidates)} 候选中")
            skipped_pool_count += 1
            continue

        prompt, candidate_map = build_prompt(bill_text, candidates)

        try:
            llm_response = call_llm(client, prompt, model=args.model)
            chosen_idx, chosen_id = parse_choice(llm_response, candidate_map)
            if not chosen_idx or not chosen_id:
                print(f"[{index}] LLM 返回无法解析: {llm_response!r}")
                failed_count += 1
                continue
        except Exception as exc:
            print(f"[{index}] API 调用失败: {exc}")
            failed_count += 1
            time.sleep(2)
            continue

        is_correct = chosen_id in correct_ids
        if is_correct:
            fixed_count += 1

        result = {
            "bill": bill_text[:80],
            "cause": cause,
            "algo_chose": {"quota_id": algo_id, "name": algo_name},
            "llm_chose": {"quota_id": chosen_id, "candidate_index": chosen_idx},
            "correct_ids": sorted(correct_ids),
            "llm_correct": is_correct,
            "candidate_pool": candidates,
        }
        results.append(result)

        status = "[FIXED]" if is_correct else "[WRONG]"
        print(f"[{index}] {status} | {cause} | {bill_text[:40]}")
        print(f"     原选: {algo_id} {algo_name[:35]}")
        print(f"     LLM:  {chosen_id} (选了第{chosen_idx}个)")
        if is_correct:
            print("     命中正确答案")
        else:
            print(f"     正确: {sorted(correct_ids)}")
        print()

        if args.sleep_sec > 0:
            time.sleep(args.sleep_sec)

    total_tested = len(results)
    fix_rate = fixed_count / max(total_tested, 1)

    print("=" * 60)
    print(f"测试总数: {total_tested}")
    print(f"LLM 修正: {fixed_count} ({fix_rate * 100:.0f}%)")
    print(f"LLM 仍错: {total_tested - fixed_count} ({(1 - fix_rate) * 100:.0f}%)")
    print(f"候选池跳过: {skipped_pool_count}")
    print(f"API 失败: {failed_count}")
    print()

    if fix_rate >= 0.7:
        print("结论：LLM reranker 有效（修正率 ≥ 70%），值得做成自动化 pipeline。")
        print()
        print("下一步：")
        print("  1. 在 match_pipeline._build_search_result_from_candidates() 里")
        print("     对 small_score_gap / param_mismatch 等高歧义题调用 LLM reranker")
        print("  2. 高置信题继续走现有快通道，不调 LLM")
        print("  3. 跑全量 benchmark 验证端到端效果")
    elif fix_rate >= 0.5:
        print("结论：LLM reranker 有一定效果（修正率 50%~70%），可以继续优化 prompt 后再验证。")
    else:
        print("结论：LLM reranker 效果不足（修正率 < 50%），需要重新设计 prompt 或换方案。")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "model": args.model,
                "benchmark_errors": str(benchmark_path),
                "province": args.province,
                "max_cases": args.max_cases,
                "max_candidates": args.max_candidates,
                "total_loaded": len(cases),
                "total_tested": total_tested,
                "fixed": fixed_count,
                "fix_rate": fix_rate,
                "skipped_pool": skipped_pool_count,
                "failed_api": failed_count,
                "results": results,
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n详细结果已保存: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
