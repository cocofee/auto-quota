# Production + Goal Candidate Union Shadow Design

## 1. Goal

Build a strictly offline shadow experiment that merges production retrieval candidates with GoalSearcher candidates, then sends the merged pool through the existing production filtering, reranking, parameter validation, and decision chain.

The experiment must answer one question: can Goal recall gains improve final accuracy without changing any online code path or allowing Goal scores to select Top1 directly?

## 2. Baseline Evidence

The trusted 安徽 human-correction baseline contains 261 evaluable cases.

| Provider | Recalled cases | Recall | Final Top1 |
| --- | ---: | ---: | ---: |
| Production | 102 | 39.08% | 63 / 261 (24.14%) |
| Goal Shadow | 176 | 67.43% | 30 / 261 (11.49%) |
| Deduplicated candidate union | 199 | 76.25% | Not yet measured |

Goal contributes 97 recalled cases that production misses. Production ranks its recalled cases substantially better than Goal, so Goal must remain a candidate source rather than a final decision owner.

## 3. Scope

### Included

- Offline-only evaluation under `eval/accuracy_baseline/` and `tools/`.
- A proxy around the production `HybridSearcher` instance.
- Goal Top80 generation with answer priors disabled and same-source exclusions enabled.
- Candidate materialization from the evaluation-only province `quota.db`.
- Candidate deduplication, source attribution, and score initialization.
- Execution through the unchanged production candidate preparation and decision pipeline.
- Separate reports for raw union coverage, rankable-pool coverage, final Top1, stage flips, and latency.

### Excluded

- No production configuration flag.
- No online request field or hidden item key that can activate the experiment.
- No changes to `src/match_core.py`, `src/match_engine.py`, `src/hybrid_searcher.py`, or `src/match_pipeline/`.
- No model training, model replacement, database mutation, or experience writes.
- No Goal Top1 override and no Goal score as a final-answer rule.
- No combined OSS headline metric while province asset gates remain incomplete.

## 4. Chosen Architecture

### 4.1 `GoalUnionSearcherProxy`

The proxy lives in the offline evaluation package and wraps an initialized production `HybridSearcher`.

It delegates all attributes and methods to the wrapped searcher except `search()`. Its `search()` implementation:

1. Calls the wrapped production searcher with the original arguments.
2. Adds the frozen standalone Production recall pool for the stable case token. This preserves candidates found only by Production retry/cascade paths that Goal enrichment can otherwise suppress.
3. Reads precomputed Goal hits attached to an evaluation-only in-memory lookup keyed by the same token.
4. Materializes every missing Production or Goal quota through the wrapped searcher's `_materialize_quota_candidate()` method.
5. Merges frozen Production, current Production, and Goal candidates by quota ID.
6. Returns the merged list to the unchanged production candidate-preparation flow.

The proxy is instantiated only by the offline provider. Normal production constructors never see it.

### 4.2 `GoalUnionShadowProvider`

The provider follows the existing `CandidateProvider` contract.

For each province group it:

1. Initializes the normal production search components.
2. Initializes one GoalSearcher for the same province.
3. Converts each `EvalCase` to the production bill-item record.
4. Runs GoalSearcher with `goal_no_answer_priors=True` and excludes the current case, source file, and project.
5. Stores serialized Goal hits in an in-memory lookup owned by the proxy.
6. Runs `match_search_only()` once with the unwrapped Production searcher and experience disabled, then freezes its case-scoped recall pool.
7. Runs `match_search_only()` again with the proxy, the frozen Production pool, the normal validator, and experience disabled.
8. Converts results through the existing real-evaluation detail and lifecycle normalizers.

Provider failures remain isolated per province and use the existing provider status contract.

## 5. Candidate Contract

### 5.1 Duplicate Candidate

When a Goal quota already exists in production candidates:

- Preserve every production field and score.
- Add `goal_shadow_score`, `goal_shadow_confidence`, and `goal_shadow_reasons`.
- Add `goal_shadow` to `candidate_sources` without replacing the production source.
- Never lower or replace production retrieval, rerank, or parameter scores.

### 5.2 Goal-Only Candidate

Materialized Goal-only candidates contain:

- `quota_id`, `name`, `unit`, and local province identity;
- canonical features from the wrapped production materializer;
- `match_source="goal_shadow_union"`;
- `candidate_sources=["goal_shadow"]`;
- Goal score, confidence, reasons, and source-score diagnostics;
- `knowledge_prior_sources=["goal_shadow_union"]` so the existing retention contract does not silently drop the candidate after semantic reranking.

The initial `hybrid_score` and `rerank_score` use the median of current production candidate scores. This makes Goal candidates eligible for production reranking without allowing incomparable Goal scores to dominate the initial order. The original Goal score remains diagnostic-only.

### 5.3 Ordering and Limits

- Production candidate relative order is preserved before the production reranker runs.
- Goal-only candidates follow production candidates before reranking, ordered by Goal score descending and quota ID ascending.
- Goal collection is capped at 80 hits per case.
- Deduplication is stable and deterministic.

## 6. Data Flow

Before Goal enrichment, the provider runs a standalone Production pass and freezes each case's complete recall pool. The proxy then combines that frozen pool with the current Production search result and Goal Top80 before the unchanged downstream stages. This two-pass boundary is required because Goal candidates can change Production retry/cascade control flow.

```text
EvalCase
  ├─ Production bill-item conversion
  ├─ GoalSearcher Top80 (no answer priors, same-source excluded)
  └─ GoalUnionSearcherProxy
       ├─ HybridSearcher.search()
       ├─ materialize Goal-only quotas
       └─ deduplicate production ∪ Goal
            ↓
       existing route filters
            ↓
       existing semantic reranker
            ↓
       existing parameter validator
            ↓
       existing LTR / structural / arbiter / final stages
            ↓
       offline ProviderResult + union diagnostics
```

## 7. Diagnostics and Reporting

Each case records:

- `production_retrieved_ids`;
- `goal_retrieved_ids`;
- `raw_union_ids`;
- `goal_unique_ids`;
- `rankable_union_ids` from the production trace;
- `goal_candidates_materialized` and `goal_candidates_missing_local_db`;
- production and union final Top1 IDs;
- whether the oracle was production-only, Goal-only, both, or neither;
- stage flips and final bad-flip attribution;
- production, Goal, merge, and total elapsed time.

Aggregate reporting includes raw union Recall@25/80, rankable Recall@25/80, final Top1/Top3, conditional Top1, MRR, stage flips, and per-project slices.

## 8. Error Handling

- Missing local Goal quota: record it in `goal_candidates_missing_local_db`; do not create a name-only candidate.
- Goal failure for one case: keep production candidates and mark Goal enrichment failure in diagnostics.
- Production search failure for a province: return provider errors for that province using the existing isolation behavior.
- Empty production pool: materialized Goal candidates may form the pool, but still pass through every existing production filter and decision stage.
- Missing case token or lookup entry: delegate to production search unchanged.

No error path may write a database, model, production configuration, or online task state.

## 9. Test Strategy

### Unit tests

- Proxy delegates non-search attributes and calls.
- Duplicate candidates preserve production fields and gain Goal diagnostics.
- Goal-only candidates materialize complete rankable fields.
- Median score initialization is deterministic.
- Missing local quota is reported and excluded.
- Goal candidates survive reranker truncation through the knowledge-prior retention contract.
- No Goal lookup produces byte-for-byte equivalent candidate dictionaries to production search output.

### Provider tests

- Same-source exclusions are passed to GoalSearcher.
- Province grouping reuses one production and one Goal searcher per province.
- Provider errors remain isolated.
- Runtime metadata identifies `production_goal_candidate_union_shadow_v1` and reconstructed assets.

### Integration tests

- Synthetic union recall exceeds production recall.
- Goal score alone cannot replace a stronger production final candidate.
- CLI exposes the provider only in the offline accuracy-baseline tool.
- Existing production and Goal providers remain unchanged.

## 10. Acceptance Gates

Run on the same 261-case primary dataset and reconstructed 安徽 asset used by baseline v0.

Required gates:

1. Raw union recall equals the measured ceiling: 199 / 261 (76.25%).
2. Rankable-pool Recall@80 is reported separately; any loss from 199 is attributed to materialization or production filtering.
3. Final Top1 is at least 63 / 261 (24.14%), the reconstructed Production baseline.
4. Final-stage bad flips do not exceed 12.
5. Every Goal-selected final candidate exists in the evaluation province database and passed production validation.
6. Existing accuracy-baseline and adjacent production-evaluation tests pass.
7. No file under `db/provinces`, no production config, no source database, and no online state changes.

If raw union recall is below 199, stop and fix the merge contract. If raw union passes but rankable recall falls, diagnose filtering before tuning ranking. If Top1 regresses, keep the experiment rejected and inspect ranking features; do not weaken the safety gate.
