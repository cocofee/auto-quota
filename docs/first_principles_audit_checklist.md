# First-Principles Audit Checklist

## 1. Goal Function
Project decisions must optimize this order:

1. Correctness: output quota is right for real-world bill intent.
2. Safety: no silent data corruption, no unsafe auto-write behavior.
3. Reliability: no crash under dirty input or partial dependency failure.
4. Observability: failure reason is traceable in logs/artifacts.
5. Efficiency: latency and cost are controlled after 1-4 are satisfied.

## 2. Non-Negotiable Facts
These are treated as physical laws of the system:

1. Input is always dirty: missing fields, merged cells, multi-sheet quirks, mixed types.
2. External dependencies will fail: LLM timeout, model load fail, DB lock, vector query empty.
3. Concurrency creates races unless explicitly guarded.
4. Every automated correction can be wrong and needs review boundary.
5. Rules and quota versions drift over time; stale knowledge exists.

## 3. Audit Questions By Layer

### A. Input Layer
1. Can every parser path survive malformed/partial Excel rows?
2. Are numeric/text mixed formats normalized before logic branches?
3. Do multi-sheet locators work when active sheet is non-data?
4. Is there a deterministic fallback when row mapping fails?

Pass condition: no unhandled exception for malformed fixture set.

### B. Decision Layer
1. Does each branch produce valid semantic output or explicit no-match?
2. Are invalid placeholders blocked from becoming real quota IDs?
3. Are low-confidence and fallback sources explicitly marked?
4. Are strategy chains deterministic and not prematurely terminated?

Pass condition: no fake quota ID, no ambiguous success state.

### C. Concurrency Layer
1. Are shared dict/state reads and writes lock-protected?
2. Is lazy initialization single-flight safe?
3. Do cache lookups avoid duplicate compute under contention?

Pass condition: no race exception under threaded stress.

### D. Persistence Layer
1. Are DB/file writes atomic or rollback-safe?
2. Are connections closed on success and failure paths?
3. Are stale records downgraded instead of silently trusted?

Pass condition: no resource leak, no stale-as-authority bypass.

### E. Review/Correction Layer
1. Can correction rules tolerate partial/missing rule config?
2. Are reminders/statistical annotations separated from real item counts?
3. Is auto-correction output reversible and explainable?

Pass condition: correction never crashes; stats reflect real rows only.

### F. Metrics Layer
1. Are degradation/error sources fully counted (not partially)?
2. Are metrics robust to dirty types (str/None confidence)?
3. Does benchmark logic match runtime semantics?

Pass condition: metric values are stable and explainable.

## 4. Release Gates (Go/No-Go)
Release is blocked if any gate fails:

1. Full test suite green.
2. No high-severity unchecked fallback path.
3. No known race in shared mutable state.
4. No fake/placeholder IDs can enter persisted learning data.
5. No unclosed DB/file handle path in critical modules.
6. Benchmark degradation counters include all degraded sources.
7. Review statistics separate actual item rows from reminders.

## 5. Severity Rubric
Use this when triaging findings:

1. P0: wrong quota persisted as trusted knowledge, data corruption, or crash on common input.
2. P1: deterministic miscount/misclassification that misleads operations.
3. P2: robustness gap requiring rare edge case to trigger.
4. P3: maintainability or readability issue without runtime impact.

## 6. Iteration Workflow
Run this loop each change batch:

1. Identify one violated fact from Section 2.
2. Patch smallest surface to restore invariant.
3. Add regression test proving the exact failure mode.
4. Run targeted tests, then full suite.
5. Record finding and fix mapping in changelog/review note.

