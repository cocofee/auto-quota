# Production + Goal 40/10 Candidate Budget Shadow Design

## Goal

Test whether a conservative source-balanced cascade budget can admit Goal-only candidates without regressing the reconstructed Production safety baseline.

The experiment remains strictly offline. It does not modify `src/`, production configuration, databases, models, or online state.

## Evidence

The full candidate union recalls 199/261 cases, but only 103/261 remain in the first lifecycle `retrieved` snapshot. All 96 losses are Goal-unique recalled cases and occur before route filtering, parameter validation, and the downstream reranker trace.

The cause is the cascade head limit: the proxy currently returns Production candidates first and Goal-only candidates afterward, while cascade retains `candidates[:limit]`. Production commonly fills all 50 slots before Goal candidates can reach knowledge-prior retention.

## Chosen Experiment

Add an offline-only `production_40_goal_10` budget policy to `GoalUnionSearcherProxy`.

For a search call with a 50-candidate budget, the proxy head contains:

- the first 40 deduplicated Production candidates;
- the first 10 Goal-only candidates by Goal score descending and quota ID ascending.

The remaining Production and Goal candidates stay after the balanced head so raw-union diagnostics remain complete. The unchanged cascade limit consumes the balanced head.

For a non-50 `top_k`, use the same 80/20 ratio: `ceil(top_k * 0.8)` Production slots and the remaining slots for Goal-only candidates. Unused slots from either source are filled from the other source. Duplicate Goal candidates already present in Production do not consume Goal-only slots.

Production ordering is preserved within the Production partition. Goal ordering remains deterministic. The policy does not use oracle labels, final answers, or evaluation outcomes.

## Data Flow

1. Run the standalone Production pass and freeze its case-scoped recall pool.
2. Precompute leakage-safe Goal Top80 hits.
3. Run the wrapped Production search for the current cascade stage.
4. Merge current and frozen Production candidates.
5. Merge and materialize Goal candidates.
6. Build the source-balanced 40/10 head.
7. Pass the reordered pool through the unchanged cascade, route filters, reranker, parameter validation, and final decision chain.
8. Report generic metrics plus budget diagnostics.

## Diagnostics

Each result records:

- budget policy name;
- requested candidate limit;
- Production and Goal-only slot counts;
- Production and Goal-only IDs placed in the balanced head;
- raw union IDs and missing materialization IDs;
- rankable recall and downstream stage metrics.

Diagnostics accumulate across repeated case-scoped cascade calls. Calls without a case token keep existing fallback behavior.

## Tests

Add tests proving:

- a 50-slot call produces a 40/10 head when both sources are available;
- duplicate Goal IDs do not consume Goal-only slots;
- unused slots backfill from the other source;
- non-50 limits preserve the 80/20 ratio;
- no budget policy preserves current proxy ordering;
- runner and CLI pass the selected offline budget policy without affecting existing providers.

## Acceptance Gates

Run the 261-case primary dataset and require:

- 261 valid cases;
- raw union recall remains 199/261;
- missing local Goal candidates remain 0;
- final Top1 is at least 63/261;
- final-stage bad flips are at most 12.

Rankable recall and Top3 are diagnostic improvements, not hard acceptance gates for this conservative arm. If Top1 is below 63 or bad flips exceed 12, reject the 40/10 policy and do not modify production behavior.

## Boundaries

Allowed changes are limited to `eval/accuracy_baseline/`, `tools/run_accuracy_baseline.py`, related tests, documentation, and generated offline reports. No commit, push, or production configuration change is authorized.
