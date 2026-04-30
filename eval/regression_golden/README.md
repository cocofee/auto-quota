# V36 Regression Golden Set

This directory stores fast regression cases created from V36 repair rounds.

It is separate from `eval/golden_set.jsonl`, which is a broader real-sample
evaluation export. A case belongs here only when a V36 repair is kept or
registered as `pending_full_validation`.

Required files:

- `manifest.json`: index of active, rejected, and retired cases.
- `cases.jsonl`: one JSON object per golden case.

Each case should include:

- `case_id`
- `source_repair_id`
- `target_common_issue`
- `mechanism`
- `failing_stage`
- `positive_samples`
- `negative_samples`
- `expected_behavior`
- `validation_command`
- `owner_files`
- `created_at`
- `status`

Every registered fix must include at least one representative positive sample,
one or two same-cluster positive samples when available, and one negative sample
that prevents overfitting.
