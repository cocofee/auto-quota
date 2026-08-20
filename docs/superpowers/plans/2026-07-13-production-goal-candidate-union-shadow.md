# Production + Goal Candidate Union Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a strictly offline provider that merges Production and Goal candidates at the search boundary, runs the merged pool through the unchanged production ranking chain, and reports recall and Top1 safety gates.

**Architecture:** Create an evaluation-only `GoalUnionSearcherProxy` that delegates to `HybridSearcher`, merges precomputed Goal hits by quota ID, and returns production-compatible candidate dictionaries. A new provider initializes normal production components, precomputes Goal Top80 per case with leakage exclusions, runs `match_search_only()` through the proxy, and exposes union diagnostics through the existing immutable provider contract. No `src/` file or production configuration is modified.

**Tech Stack:** Python 3.11+, dataclasses, existing `GoalSearcher`, `HybridSearcher`, `match_search_only`, accuracy-baseline contracts and metrics, pytest.

**Design Reference:** `docs/superpowers/specs/2026-07-13-production-goal-candidate-union-shadow-design.md`

**Repository Constraints:** Do not modify `src/`, `config.py`, databases, models, or online state. Do not add dependencies. Do not commit unless the user explicitly authorizes it.

---

## File Structure

- Create `eval/accuracy_baseline/union_shadow.py`: Goal hit serialization, candidate merge contract, searcher proxy, provider executor, provider adapter, and union diagnostic aggregation.
- Modify `eval/accuracy_baseline/runner.py`: add provider-specific union metrics without changing existing provider metrics.
- Modify `tools/run_accuracy_baseline.py`: expose `production_goal_union_shadow` as an offline-only provider choice.
- Create `tests/test_accuracy_baseline_union_shadow.py`: proxy, merge, provider, leakage, error-isolation, and aggregate tests.
- Modify `tests/test_accuracy_baseline_runner.py`: union diagnostic summary test.
- Modify `tests/test_accuracy_baseline_providers.py`: confirm existing providers remain unchanged.

### Task 1: Define Goal Hit and Merge Contracts

**Files:**
- Create: `eval/accuracy_baseline/union_shadow.py`
- Test: `tests/test_accuracy_baseline_union_shadow.py`

- [ ] **Step 1: Write failing duplicate and Goal-only merge tests**

Create tests using simple dictionaries and a fake materializer:

```python
def test_merge_goal_candidates_preserves_production_fields_and_adds_diagnostics():
    production = [{"quota_id": "Q-1", "name": "Production", "hybrid_score": 0.8, "match_source": "hybrid"}]
    goal_hits = [SerializedGoalHit("Q-1", "Goal", "set", 0.9, 90.0, ("goal",), {"bm25": 0.7})]

    merged, diagnostics = merge_goal_candidates(production, goal_hits, materialize=lambda *_: None)

    assert merged[0]["name"] == "Production"
    assert merged[0]["hybrid_score"] == 0.8
    assert merged[0]["goal_shadow_score"] == 0.9
    assert merged[0]["candidate_sources"] == ["hybrid", "goal_shadow"]
    assert diagnostics.goal_unique_ids == ()


def test_merge_goal_candidates_materializes_goal_only_candidate_with_median_score():
    production = [
        {"quota_id": "Q-1", "name": "A", "hybrid_score": 0.2, "rerank_score": 0.3},
        {"quota_id": "Q-2", "name": "B", "hybrid_score": 0.8, "rerank_score": 0.9},
    ]
    goal_hits = [SerializedGoalHit("Q-3", "Goal", "set", 0.95, 95.0, (), {})]

    merged, diagnostics = merge_goal_candidates(
        production,
        goal_hits,
        materialize=lambda quota_id, **_: {"quota_id": quota_id, "name": "C", "unit": "set"},
    )

    goal = next(candidate for candidate in merged if candidate["quota_id"] == "Q-3")
    assert goal["hybrid_score"] == 0.8
    assert goal["rerank_score"] == 0.9
    assert goal["knowledge_prior_sources"] == ["goal_shadow_union"]
    assert diagnostics.goal_unique_ids == ("Q-3",)
```

- [ ] **Step 2: Run tests and verify the missing module fails**

Run:

```powershell
python -m pytest tests/test_accuracy_baseline_union_shadow.py -q -p no:cacheprovider
```

Expected: collection fails because `eval.accuracy_baseline.union_shadow` does not exist.

- [ ] **Step 3: Implement immutable contracts and deterministic merge**

Add:

```python
@dataclass(frozen=True, slots=True)
class SerializedGoalHit:
    quota_id: str
    name: str
    unit: str
    score: float
    confidence: float
    reasons: tuple[str, ...]
    source_scores: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class UnionMergeDiagnostics:
    production_ids: tuple[str, ...]
    goal_ids: tuple[str, ...]
    raw_union_ids: tuple[str, ...]
    goal_unique_ids: tuple[str, ...]
    materialized_goal_ids: tuple[str, ...]
    missing_local_goal_ids: tuple[str, ...]
```

Implement `merge_goal_candidates(production, goal_hits, materialize)` with these rules:

- clone every input candidate;
- deduplicate by `(quota_id, _source_province)`;
- preserve all production values for duplicates;
- append deterministic Goal diagnostics and sources;
- materialize Goal-only candidates and reject missing local quota rows;
- initialize Goal-only `hybrid_score` and `rerank_score` with the upper median of production scores;
- place production candidates first, then Goal-only candidates ordered by Goal score descending and quota ID ascending.

- [ ] **Step 4: Run focused merge tests**

Run: `python -m pytest tests/test_accuracy_baseline_union_shadow.py -q -p no:cacheprovider`

Expected: merge tests pass.

### Task 2: Implement the Offline Searcher Proxy

**Files:**
- Modify: `eval/accuracy_baseline/union_shadow.py`
- Test: `tests/test_accuracy_baseline_union_shadow.py`

- [ ] **Step 1: Write failing proxy delegation and diagnostics tests**

Use a fake base searcher with `search()`, `_materialize_quota_candidate()`, and an arbitrary delegated attribute. Assert:

```python
def test_proxy_delegates_and_merges_case_scoped_goal_hits():
    proxy = GoalUnionSearcherProxy(base, {"case-1": goal_hits})
    result = proxy.search("query", top_k=20, item={"_accuracy_case_id": "case-1"})

    assert proxy.province == base.province
    assert [candidate["quota_id"] for candidate in result] == ["Q-1", "Q-2"]
    assert proxy.diagnostics["case-1"].raw_union_ids == ("Q-1", "Q-2")


def test_proxy_without_case_lookup_returns_production_candidates_unchanged():
    result = proxy.search("query", top_k=20, item={"_accuracy_case_id": "missing"})
    assert result == base.production_candidates
```

- [ ] **Step 2: Run the proxy tests and verify failure**

Expected: FAIL because `GoalUnionSearcherProxy` is missing.

- [ ] **Step 3: Implement the proxy**

The proxy must:

- use `__getattr__` for all non-search behavior;
- call the wrapped `search()` with original positional and keyword arguments;
- read only `_accuracy_case_id` from the offline bill item;
- merge with the case-scoped precomputed hits;
- union diagnostics across repeated cascade calls for the same case;
- never mutate the wrapped searcher's returned list or dictionaries;
- return the wrapped result unchanged when no lookup exists.

- [ ] **Step 4: Run all proxy and merge tests**

Run: `python -m pytest tests/test_accuracy_baseline_union_shadow.py -q -p no:cacheprovider`

Expected: PASS.

### Task 3: Implement the Union Shadow Provider

**Files:**
- Modify: `eval/accuracy_baseline/union_shadow.py`
- Test: `tests/test_accuracy_baseline_union_shadow.py`

- [ ] **Step 1: Write failing provider tests with injected factories**

Create fake Goal and production components. Assert that the provider:

- groups cases by province;
- creates one Goal searcher and production bundle per province;
- sends `goal_no_answer_priors=True`;
- excludes the current `sample_id`, source, and project;
- adds `_accuracy_case_id` only to offline bill items;
- returns provider name `production_goal_union_shadow`;
- places union diagnostics in `runtime_metadata`;
- isolates one province failure without affecting another.

- [ ] **Step 2: Run provider tests and verify failure**

Expected: FAIL because `GoalUnionShadowProvider` is missing.

- [ ] **Step 3: Implement the injectable executor**

Add:

```python
def evaluate_union_province_records(
    province: str,
    records: list[dict[str, Any]],
    *,
    goal_top_k: int = 80,
    init_components=None,
    goal_searcher_factory=None,
    matcher=None,
) -> dict[str, Any]:
    ...
```

Default dependencies are imported lazily from existing production and Goal modules. Build bill items with the existing real-evaluation converter, attach case IDs, precompute serialized Goal hits, run `match_search_only()` with the proxy and experience disabled, then add proxy diagnostics to each detail.

- [ ] **Step 4: Implement `GoalUnionShadowProvider`**

Follow `ProductionProvider` grouping and error isolation. Normalize each detail through `normalize_production_detail()`, then use `dataclasses.replace()` to set:

```python
provider_name="production_goal_union_shadow"
runtime_metadata={
    "experiment": "production_goal_candidate_union_shadow_v1",
    "production_retrieved_ids": [...],
    "goal_retrieved_ids": [...],
    "raw_union_ids": [...],
    "goal_unique_ids": [...],
    "materialized_goal_ids": [...],
    "missing_local_goal_ids": [...],
}
```

- [ ] **Step 5: Run provider tests**

Run: `python -m pytest tests/test_accuracy_baseline_union_shadow.py -q -p no:cacheprovider`

Expected: PASS.

### Task 4: Add Union Metrics and CLI Exposure

**Files:**
- Modify: `eval/accuracy_baseline/union_shadow.py`
- Modify: `eval/accuracy_baseline/runner.py`
- Modify: `tools/run_accuracy_baseline.py`
- Modify: `tests/test_accuracy_baseline_runner.py`
- Test: `tests/test_accuracy_baseline_union_shadow.py`

- [ ] **Step 1: Write failing aggregate-metric test**

Build two cases and union provider results with runtime metadata. Assert `aggregate_union_shadow_metrics()` reports raw production, Goal, and union recall counts, Goal-unique gain, rankable recall, and missing materialization count.

- [ ] **Step 2: Implement union diagnostic aggregation**

Add:

```python
def aggregate_union_shadow_metrics(
    cases: Sequence[EvalCase],
    results: Sequence[ProviderResult],
) -> dict[str, Any]:
    ...
```

Only statuses `OK` and `TRACE_INCOMPLETE` enter the denominator. Compare canonical oracle sets with runtime ID lists and `ProviderResult.retrieved_ids`.

- [ ] **Step 3: Write failing runner and CLI tests**

Assert the runner adds `union_shadow_diagnostics` under the provider summary when provider name is `production_goal_union_shadow`. Assert CLI accepts `--providers production_goal_union_shadow` and constructs only the offline provider.

- [ ] **Step 4: Wire metrics and CLI**

In `runner.py`, preserve all generic metrics and append provider-specific diagnostics. In `tools/run_accuracy_baseline.py`, add the provider name to the allowed set and instantiate `GoalUnionShadowProvider(goal_top_k=args.goal_top_k)`.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m pytest tests/test_accuracy_baseline_union_shadow.py tests/test_accuracy_baseline_runner.py tests/test_accuracy_baseline_providers.py -q -p no:cacheprovider
python tools/run_accuracy_baseline.py --help
```

Expected: PASS and CLI help exits 0.

### Task 5: Run the Real 261-Case Shadow Experiment

**Files:**
- Output only: `output/accuracy_baseline/baseline_v0/production_goal_union_shadow_v1/`

- [ ] **Step 1: Run the union provider**

Run:

```powershell
python tools/run_accuracy_baseline.py --primary output/accuracy_baseline/datasets/primary_v0.jsonl --providers production_goal_union_shadow --goal-top-k 80 --provinces-db-dir output/accuracy_baseline/reconstructed_assets/provinces --output-dir output/accuracy_baseline/baseline_v0/production_goal_union_shadow_v1
```

- [ ] **Step 2: Check hard acceptance gates**

Read `summary.json` and require:

- raw union recalled cases = 199;
- final Top1 correct cases >= 63;
- final-stage bad flips <= 12;
- missing local Goal candidate count = 0;
- all 261 cases are evaluable.

If raw union is below 199, stop at merge debugging. If raw union passes but rankable recall falls, report filtering loss. If Top1 is below 63, reject the experiment without changing production behavior.

- [ ] **Step 3: Record the measured decision**

Append the exact result and accept/reject decision to the trusted-baseline plan. Do not modify production configuration.

### Task 6: Regression and Boundary Verification

**Files:**
- Test: `tests/test_accuracy_baseline_*.py`
- Test: `tests/test_real_eval_tools.py`
- Verify: working tree and protected paths

- [ ] **Step 1: Run all accuracy-baseline and adjacent tests**

Run:

```powershell
$files = @(Get-ChildItem tests -Filter 'test_accuracy_baseline_*.py' | Select-Object -ExpandProperty FullName)
python -m pytest @files tests/test_real_eval_tools.py -q -p no:cacheprovider
```

- [ ] **Step 2: Run compile and CLI checks**

Run:

```powershell
python -m compileall -q eval/accuracy_baseline tools/run_accuracy_baseline.py
python tools/run_accuracy_baseline.py --help
```

- [ ] **Step 3: Verify protected boundaries**

Run:

```powershell
git diff --check
git status --short -- src config.py db/provinces
```

Expected: no new changes under protected paths. Existing unrelated user changes are preserved and reported separately.

- [ ] **Step 4: Stop for checkpoint review**

Report raw union recall, rankable recall, final Top1/Top3, stage bad flips, materialization gaps, runtime, acceptance decision, tests, and remaining data risks. Do not commit unless explicitly authorized.
