# Learning Backfill Status

Date: 2026-03-29
Status: active

## Scope

Continue importing historical priced bill documents into the learning layer.

Rule-extraction work remains blocked until import reaches completion.

## Current Totals

Snapshot taken after the latest completed batches:

```json
{
  "exp_total": 701985,
  "exp_completed_project": 258712,
  "exp_verified_completed_project": 176780,
  "exp_candidate_completed_project": 81932,
  "fts_rows": 491849
}
```

Notes:

- `experience_fts` is intentionally behind `experiences` during batch import.
- Full FTS rebuild stays deferred until the remaining document lanes are finished.

## Remaining Documents

Current checkpoint-based remaining count:

```json
{
  "fj_remaining": 251,
  "zj_remaining": 0,
  "js_remaining": 16,
  "bj_remaining": 1,
  "unknown_region_remaining": 1,
  "fj_done": 513,
  "zj_done": 449,
  "zj_skipped": 2,
  "all_remaining_from_now": 269
}
```

## Current Active Lane

Current lane after the latest completed work:

- `ZJ` is finished.
- Import has switched to `FJ`.
- `FJ` was advanced by 16 additional `limit=10` batches after `ZJ` closed.

Current stable `FJ` mode:

```powershell
$env:LOGURU_LEVEL='ERROR'
python tools/backfill_learning_from_price_documents.py `
  --region FJ `
  --sort-by-row-count `
  --limit 10 `
  --checkpoint-file output/learning_backfill_fj_le300_checkpoint.json `
  --summary-only
```

## ZJ Closure

Final `ZJ` outcome:

- `zj_done = 449`
- `zj_skipped = 2`
- Remaining `ZJ` documents: `0`

Operational note:

- The final heavy `ZJ` documents had to be finished in direct single-document or small-batch mode because full remaining-run sessions exceeded terminal timeout windows.

## FJ Front Queue

The front of the current `FJ` queue was in the low-300-row band and processed stably:

| document_id | row_count | source_file_name |
| --- | ---: | --- |
| 12105 | 301 | `ca667352-c9d7-4683-b4af-adb48e43a84b.XML` |
| 11315 | 303 | `2f9fd534-1409-4be0-b967-b17f54a60659.XML` |
| 11280 | 303 | `280367ee-2871-4557-961c-89a484b11240.XML` |
| 11108 | 303 | `0a6302d7-bc8a-4a7a-8f45-2e2f87e35b44.XML` |
| 11982 | 305 | `a36e64fe-93d7-40ea-bc68-443b0dd58115.XML` |
| 11243 | 309 | `21f33f74-a24c-4c21-994b-4387a1ede915.XML` |

Current implication:

- `FJ` remains the main remaining volume.
- `FJ` dropped from `411` remaining to `251` remaining during this run.
- The current front of queue is stable enough for continued `limit=10` checkpoint-driven batches.

## Operating Rules

- Keep import work strictly serial. Do not overlap backfill and FTS rebuild.
- Do not start rule extraction before the remaining import count reaches zero.
- After all remaining lanes finish:
  - rebuild `experience_fts`
  - rebuild vector index
  - recompute final totals
