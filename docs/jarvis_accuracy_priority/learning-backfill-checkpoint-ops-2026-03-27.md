# Learning Backfill Checkpoint Ops

Date: 2026-03-27
Status: active

## Purpose

Stabilize `tools/backfill_learning_from_price_documents.py` for long-running reimport batches.

The key change is that document exclusion now happens in SQL selection, not after `LIMIT/OFFSET`.
This prevents the old `offset + processed ids` interaction from causing duplicate or skipped documents.

## Checkpoint Format

New checkpoint files store three sets:

```json
{
  "succeeded_document_ids": [12262, 12280],
  "failed_document_ids": [12345],
  "skipped_document_ids": [13001]
}
```

Compatibility:

- Old files with only `processed_document_ids` are still accepted.
- Legacy `processed_document_ids` are treated as `succeeded_document_ids`.

Default exclusion behavior:

- Exclude `succeeded_document_ids`
- Exclude `skipped_document_ids`
- Do not exclude `failed_document_ids`
- `--dry-run` does not update checkpoint state

Optional flags:

- `--retry-skipped`: allow skipped documents to be selected again
- `--exclude-failed`: also exclude failed documents
- `--exclude-document-ids-file`: exclude extra ids from a JSON or text file

## Recommended Usage

FJ medium batch, checkpoint-driven:

```powershell
$env:LOGURU_LEVEL='ERROR'
python tools/backfill_learning_from_price_documents.py `
  --region FJ `
  --sort-by-row-count `
  --max-row-count 300 `
  --limit 10 `
  --checkpoint-file output/learning_backfill_fj_le300_checkpoint.json `
  --summary-only
```

Retry skipped documents:

```powershell
$env:LOGURU_LEVEL='ERROR'
python tools/backfill_learning_from_price_documents.py `
  --region FJ `
  --sort-by-row-count `
  --max-row-count 300 `
  --limit 10 `
  --checkpoint-file output/learning_backfill_fj_le300_checkpoint.json `
  --retry-skipped `
  --summary-only
```

Exclude an external id list:

```powershell
$env:LOGURU_LEVEL='ERROR'
python tools/backfill_learning_from_price_documents.py `
  --region FJ `
  --sort-by-row-count `
  --max-row-count 300 `
  --limit 10 `
  --checkpoint-file output/learning_backfill_fj_le300_checkpoint.json `
  --exclude-document-ids-file output/fj_manual_excludes.json `
  --summary-only
```

## Run Rules

1. Prefer checkpoint-driven batches over manual `offset` continuation.
2. Keep one checkpoint file per batch lane, for example `FJ <=300`.
3. Only rebuild FTS after a meaningful amount of new writes.
4. Delay full vector rebuild until enough batches accumulate.

## Next Batch Order

1. Continue `FJ` with `101-300` row documents using checkpoint mode.
2. After `FJ` medium batches stabilize, decide whether to move into larger `FJ` files.
3. Then switch to `ZJ`.
