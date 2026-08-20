# Production + Goal 40/10 Candidate Budget Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add and evaluate a strictly offline 80/20 source-balanced cascade head that yields Production 40 / Goal-only 10 candidates for a 50-slot search budget.

**Architecture:** Extend the existing evaluation-only union proxy with a pure deterministic budget-ordering helper. The helper reorders the already materialized union before the unchanged production cascade truncates it; it never changes candidate fields, scores, or production configuration. Expose the policy through the offline provider and CLI, record per-call diagnostics, and evaluate the same 261-case primary dataset.

**Tech Stack:** Python 3.12, dataclasses, existing accuracy-baseline provider contracts, pytest, argparse.

**Design Reference:** `docs/superpowers/specs/2026-07-13-production-goal-40-10-budget-shadow-design.md`

**Repository Constraints:** Do not modify `src/`, `config.py`, databases, models, or online state. Do not add dependencies. Do not commit, stage, or push without explicit user authorization.

---

## File Structure

- Modify `eval/accuracy_baseline/union_shadow.py`: budget diagnostics contract, pure ordering helper, proxy integration, provider option, and runtime metadata.
- Modify `tools/run_accuracy_baseline.py`: add the offline `--union-budget-policy` option.
- Modify `tests/test_accuracy_baseline_union_shadow.py`: helper, proxy, provider, and metadata tests.
- Modify `tests/test_accuracy_baseline_runner.py`: CLI construction test for the selected policy.
- Update `docs/superpowers/plans/2026-07-12-trusted-baseline-data-recovery.md`: record measured 40/10 decision.
- Generate `output/accuracy_baseline/baseline_v0/production_goal_union_shadow_40_10_v1/`: offline reports only.

### Task 1: Define the Pure 40/10 Budget Contract

**Files:**
- Modify: `eval/accuracy_baseline/union_shadow.py`
- Test: `tests/test_accuracy_baseline_union_shadow.py`

- [ ] **Step 1: Write failing 50-slot ordering test**

Add a test that creates 45 Production and 20 Goal-only candidates, then calls the wished-for API:

```python
def test_reorder_union_candidates_for_40_10_budget_builds_balanced_head():
    production = [
        {"quota_id": f"P-{index}", "name": f"Production {index}"}
        for index in range(45)
    ]
    goal = [
        {"quota_id": f"G-{index}", "name": f"Goal {index}"}
        for index in range(20)
    ]

    reordered, diagnostics = reorder_union_candidates_for_budget(
        [*production, *goal],
        production_ids=tuple(candidate["quota_id"] for candidate in production),
        limit=50,
        policy="production_40_goal_10",
    )

    assert [candidate["quota_id"] for candidate in reordered[:40]] == [
        f"P-{index}" for index in range(40)
    ]
    assert [candidate["quota_id"] for candidate in reordered[40:50]] == [
        f"G-{index}" for index in range(10)
    ]
    assert diagnostics.production_slots == 40
    assert diagnostics.goal_only_slots == 10
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/test_accuracy_baseline_union_shadow.py::test_reorder_union_candidates_for_40_10_budget_builds_balanced_head -q -p no:cacheprovider
```

Expected: collection or import failure because `reorder_union_candidates_for_budget` does not exist.

- [ ] **Step 3: Implement immutable diagnostics and minimal ordering**

Add:

```python
@dataclass(frozen=True, slots=True)
class UnionBudgetDiagnostics:
    policy: str
    requested_limit: int
    production_slots: int
    goal_only_slots: int
    head_production_ids: tuple[str, ...]
    head_goal_only_ids: tuple[str, ...]


def reorder_union_candidates_for_budget(
    candidates: Sequence[dict[str, Any]],
    *,
    production_ids: Sequence[str],
    limit: int,
    policy: str,
) -> tuple[list[dict[str, Any]], UnionBudgetDiagnostics | None]:
    working = [deepcopy(candidate) for candidate in candidates or []]
    normalized_policy = str(policy or "none").strip().lower()
    if normalized_policy == "none":
        return working, None
    if normalized_policy != "production_40_goal_10":
        raise ValueError(f"unknown candidate budget policy: {policy}")

    requested_limit = max(0, int(limit or 0))
    production_set = set(_ordered_unique(production_ids))
    production = [
        candidate for candidate in working
        if str(candidate.get("quota_id") or "").strip() in production_set
    ]
    goal_only = [
        candidate for candidate in working
        if str(candidate.get("quota_id") or "").strip() not in production_set
    ]
    production_slots = math.ceil(requested_limit * 0.8)
    goal_only_slots = requested_limit - production_slots
    head_production = production[:production_slots]
    head_goal_only = goal_only[:goal_only_slots]
    head = [*head_production, *head_goal_only]
    remaining = [
        *production[len(head_production):],
        *goal_only[len(head_goal_only):],
    ]
    if len(head) < requested_limit:
        fill_count = requested_limit - len(head)
        head.extend(remaining[:fill_count])
        remaining = remaining[fill_count:]

    diagnostics = UnionBudgetDiagnostics(
        policy=normalized_policy,
        requested_limit=requested_limit,
        production_slots=production_slots,
        goal_only_slots=goal_only_slots,
        head_production_ids=_ordered_unique(
            [candidate.get("quota_id", "") for candidate in head if str(candidate.get("quota_id") or "").strip() in production_set]
        ),
        head_goal_only_ids=_ordered_unique(
            [candidate.get("quota_id", "") for candidate in head if str(candidate.get("quota_id") or "").strip() not in production_set]
        ),
    )
    return [*head, *remaining], diagnostics
```

Implementation rules:

- return cloned candidates and `None` when policy is empty or `none`;
- reject unknown policies with `ValueError`;
- compute `production_slots = ceil(limit * 0.8)` and `goal_only_slots = limit - production_slots`;
- partition by membership in `production_ids`, so Goal duplicates remain Production candidates;
- build the head from Production then Goal-only partitions;
- backfill unused slots from the non-exhausted partition;
- append every unselected candidate exactly once after the head.

- [ ] **Step 4: Write failing edge-case tests**

Add separate tests for:

```python
def test_budget_duplicate_goal_id_does_not_consume_goal_only_slot():
    candidates = [
        {"quota_id": "P-1", "name": "Production with Goal diagnostics"},
        *({"quota_id": f"G-{index}", "name": f"Goal {index}"} for index in range(12)),
    ]
    reordered, diagnostics = reorder_union_candidates_for_budget(
        candidates,
        production_ids=("P-1",),
        limit=10,
        policy="production_40_goal_10",
    )
    assert diagnostics.head_production_ids == ("P-1",)
    assert diagnostics.head_goal_only_ids == tuple(f"G-{index}" for index in range(9))
    assert len(reordered[:10]) == 10


def test_budget_backfills_unused_production_slots_from_goal_only_candidates():
    candidates = [
        *({"quota_id": f"P-{index}", "name": "Production"} for index in range(3)),
        *({"quota_id": f"G-{index}", "name": "Goal"} for index in range(12)),
    ]
    reordered, diagnostics = reorder_union_candidates_for_budget(
        candidates,
        production_ids=("P-0", "P-1", "P-2"),
        limit=10,
        policy="production_40_goal_10",
    )
    assert len(diagnostics.head_production_ids) == 3
    assert len(diagnostics.head_goal_only_ids) == 7
    assert len(reordered[:10]) == 10


def test_budget_scales_non_50_limit_to_80_20_ratio():
    candidates = [
        *({"quota_id": f"P-{index}", "name": "Production"} for index in range(30)),
        *({"quota_id": f"G-{index}", "name": "Goal"} for index in range(10)),
    ]
    reordered, diagnostics = reorder_union_candidates_for_budget(
        candidates,
        production_ids=tuple(f"P-{index}" for index in range(30)),
        limit=25,
        policy="production_40_goal_10",
    )
    assert diagnostics.production_slots == 20
    assert diagnostics.goal_only_slots == 5
    assert [candidate["quota_id"] for candidate in reordered[20:25]] == [
        f"G-{index}" for index in range(5)
    ]


def test_budget_none_preserves_candidate_order():
    candidates = [
        {"quota_id": "P-1", "name": "Production"},
        {"quota_id": "G-1", "name": "Goal"},
    ]
    reordered, diagnostics = reorder_union_candidates_for_budget(
        candidates,
        production_ids=("P-1",),
        limit=1,
        policy="none",
    )
    assert reordered == candidates
    assert diagnostics is None
```

The non-50 test uses `limit=25` and requires 20 Production plus 5 Goal-only candidates in the head.

- [ ] **Step 5: Run helper tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_accuracy_baseline_union_shadow.py -k budget -q -p no:cacheprovider
```

Expected: all budget helper tests pass.

### Task 2: Apply the Budget at the Proxy Boundary

**Files:**
- Modify: `eval/accuracy_baseline/union_shadow.py`
- Test: `tests/test_accuracy_baseline_union_shadow.py`

- [ ] **Step 1: Write failing proxy integration test**

Construct a fake base searcher returning 45 Production candidates and 20 Goal hits. Instantiate:

```python
proxy = GoalUnionSearcherProxy(
    base,
    {"case-1": goal_hits},
    candidate_budget_policy="production_40_goal_10",
)
result = proxy.search("query", top_k=50, item={"_accuracy_case_id": "case-1"})
```

Assert the first 50 IDs are Production 0-39 followed by Goal 0-9, all remaining candidates still exist after index 49, and `proxy.budget_diagnostics["case-1"]` records one call.

- [ ] **Step 2: Run the proxy test and verify RED**

Run:

```powershell
python -m pytest tests/test_accuracy_baseline_union_shadow.py::test_proxy_applies_40_10_budget_before_cascade_truncation -q -p no:cacheprovider
```

Expected: FAIL because the proxy does not accept or apply `candidate_budget_policy`.

- [ ] **Step 3: Implement proxy integration**

Extend the constructor with:

```python
candidate_budget_policy: str = "none"
```

After `merge_goal_candidates()` and before returning, resolve the call limit from keyword `top_k`, falling back to the first positional argument when it is an integer. Call `reorder_union_candidates_for_budget()` and append non-null diagnostics to a case-scoped list.

Calls without a case token or Goal lookup continue returning the wrapped search output unchanged.

- [ ] **Step 4: Run proxy and union tests**

Run:

```powershell
python -m pytest tests/test_accuracy_baseline_union_shadow.py -q -p no:cacheprovider
```

Expected: all tests pass.

### Task 3: Expose Policy Through Provider, Metadata, and CLI

**Files:**
- Modify: `eval/accuracy_baseline/union_shadow.py`
- Modify: `tools/run_accuracy_baseline.py`
- Modify: `tests/test_accuracy_baseline_union_shadow.py`
- Modify: `tests/test_accuracy_baseline_runner.py`

- [ ] **Step 1: Write failing provider metadata test**

Extend the injected executor test so `GoalUnionShadowProvider(candidate_budget_policy="production_40_goal_10")` passes the policy into `evaluate_union_province_records()` and normalized runtime metadata contains:

```python
{
    "candidate_budget_policy": "production_40_goal_10",
    "candidate_budget_calls": [
        {
            "policy": "production_40_goal_10",
            "requested_limit": 50,
            "production_slots": 40,
            "goal_only_slots": 10,
            "head_production_ids": ["P-1"],
            "head_goal_only_ids": ["G-1"],
        }
    ],
}
```

- [ ] **Step 2: Write failing CLI construction test**

Invoke CLI arguments:

```text
--providers production_goal_union_shadow
--union-budget-policy production_40_goal_10
```

Monkeypatch `GoalUnionShadowProvider` and assert its constructor receives both `goal_top_k` and `candidate_budget_policy`.

- [ ] **Step 3: Run provider and CLI tests and verify RED**

Run:

```powershell
python -m pytest tests/test_accuracy_baseline_union_shadow.py tests/test_accuracy_baseline_runner.py -k "budget or cli_constructs_union" -q -p no:cacheprovider
```

Expected: FAIL because provider/executor/CLI do not expose the policy.

- [ ] **Step 4: Implement provider and CLI wiring**

Add `candidate_budget_policy: str = "none"` to `evaluate_union_province_records()` and `GoalUnionShadowProvider.__init__()`. Pass it into the proxy and serialize each `UnionBudgetDiagnostics` with `dataclasses.asdict()` into detail diagnostics.

Add:

```python
parser.add_argument(
    "--union-budget-policy",
    choices=("none", "production_40_goal_10"),
    default="none",
)
```

Construct the provider with:

```python
GoalUnionShadowProvider(
    goal_top_k=args.goal_top_k,
    candidate_budget_policy=args.union_budget_policy,
)
```

- [ ] **Step 5: Run focused tests and CLI help**

Run:

```powershell
python -m pytest tests/test_accuracy_baseline_union_shadow.py tests/test_accuracy_baseline_runner.py tests/test_accuracy_baseline_providers.py -q -p no:cacheprovider
python tools/run_accuracy_baseline.py --help
```

Expected: tests pass and help lists `--union-budget-policy`.

### Task 4: Run the 261-Case 40/10 Shadow Experiment

**Files:**
- Output only: `output/accuracy_baseline/baseline_v0/production_goal_union_shadow_40_10_v1/`

- [ ] **Step 1: Run the experiment**

Run:

```powershell
python tools/run_accuracy_baseline.py --primary output/accuracy_baseline/datasets/primary_v0.jsonl --providers production_goal_union_shadow --goal-top-k 80 --union-budget-policy production_40_goal_10 --provinces-db-dir output/accuracy_baseline/reconstructed_assets/provinces --output-dir output/accuracy_baseline/baseline_v0/production_goal_union_shadow_40_10_v1
```

- [ ] **Step 2: Check hard gates**

Read `summary.json` and require:

- accepted and valid cases = 261;
- raw union recalled count = 199;
- missing local Goal candidate count = 0;
- final Top1 correct count >= 63;
- final-stage bad flips <= 12.

Also report rankable recall, Top3, runtime, and budget-call coverage.

- [ ] **Step 3: Record accept or reject decision**

Append the exact measured values to `docs/superpowers/plans/2026-07-12-trusted-baseline-data-recovery.md`. Keep the policy offline-only regardless of the result; no production enablement is authorized.

### Task 5: Regression and Boundary Verification

**Files:**
- Test: `tests/test_accuracy_baseline_*.py`
- Test: `tests/test_real_eval_tools.py`
- Verify: protected paths and working tree

- [ ] **Step 1: Run all related tests**

```powershell
$files = @(Get-ChildItem tests -Filter 'test_accuracy_baseline_*.py' | Select-Object -ExpandProperty FullName)
python -m pytest @files tests/test_real_eval_tools.py -q -p no:cacheprovider
```

- [ ] **Step 2: Run compile and CLI checks**

```powershell
python -m compileall -q eval/accuracy_baseline tools/run_accuracy_baseline.py
python tools/run_accuracy_baseline.py --help
```

- [ ] **Step 3: Verify boundaries**

```powershell
git diff --check
git status --short -- src config.py db/provinces
```

Expected: no new protected-path changes. Existing unrelated user changes remain untouched.

- [ ] **Step 4: Stop for checkpoint review**

Report raw union recall, rankable recall, final Top1/Top3, bad flips, materialization gaps, runtime, test counts, acceptance decision, and the next recommended experiment. Do not commit, stage, push, merge, or alter production configuration.
