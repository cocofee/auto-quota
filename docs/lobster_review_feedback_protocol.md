# Lobster Review Feedback Protocol

## Purpose

This protocol defines how Lobster Audit writes back the final review result for one
OpenClaw draft. The goal is:

- let external audit tools submit a final quota decision
- let Jarvis learn from structured correction facts instead of free-form prose
- keep OpenClaw draft, human final decision, and staging promotion in one loop

## Write-back Target

Use the OpenClaw confirm endpoint:

```http
POST /api/openclaw/tasks/{task_id}/results/{result_id}/review-confirm
Content-Type: application/json
```

## Request Body

```json
{
  "decision": "approve",
  "review_note": "人工终审通过",
  "human_feedback_payload": {
    "protocol_version": "lobster_review_feedback.v1",
    "source": "lobster_audit",
    "adopt_openclaw": false,
    "final_quota": {
      "quota_id": "C10-8-8",
      "name": "人工修正定额",
      "unit": "m"
    },
    "manual_reason_codes": [
      "manual_override",
      "param_checked",
      "same_family_but_param_conflict"
    ],
    "manual_note": "龙虾审计确认 OpenClaw 候选方向对，但关键参数不符，改为人工终版定额。",
    "promotion_decision": "manual_override"
  }
}
```

## Field Rules

### Top-level request

- `decision`
  - required
  - `approve` or `reject`
- `review_note`
  - optional
  - operator note for this confirmation action
- `human_feedback_payload`
  - optional but strongly recommended for `approve`
  - structured feedback written by Lobster Audit

### `human_feedback_payload`

- `protocol_version`
  - required in Lobster integration
  - fixed value: `lobster_review_feedback.v1`
- `source`
  - required
  - recommended value: `lobster_audit`
- `adopt_openclaw`
  - optional
  - `true`: accept OpenClaw suggested quota
  - `false`: use Lobster final quota instead
- `final_quota`
  - required when `adopt_openclaw = false`
  - one final selected quota object
- `final_quotas`
  - optional alternative to `final_quota`
  - use only when you need multi-quota write-back
- `manual_reason_codes`
  - optional but strongly recommended
  - machine-readable human reason codes
- `manual_note`
  - optional but strongly recommended
  - one concise sentence describing why the final decision was made
- `promotion_decision`
  - optional
  - recommended values:
    - `follow_openclaw`
    - `manual_override`
    - `hold_no_promotion`

### Quota object

```json
{
  "quota_id": "C10-8-8",
  "name": "人工修正定额",
  "unit": "m"
}
```

- `quota_id`
  - required
- `name`
  - strongly recommended
- `unit`
  - strongly recommended

## Behavior

### Case 1: Accept OpenClaw draft

```json
{
  "protocol_version": "lobster_review_feedback.v1",
  "source": "lobster_audit",
  "adopt_openclaw": true,
  "manual_reason_codes": ["openclaw_confirmed", "manual_checked"],
  "manual_note": "龙虾审计复核通过，采纳 OpenClaw 建议。",
  "promotion_decision": "follow_openclaw"
}
```

Effect:

- final corrected quota uses OpenClaw suggestion
- human reason codes still enter Jarvis absorbable report
- staging promotion uses confirmed structured facts

### Case 2: Override OpenClaw draft

```json
{
  "protocol_version": "lobster_review_feedback.v1",
  "source": "lobster_audit",
  "adopt_openclaw": false,
  "final_quota": {
    "quota_id": "C10-8-8",
    "name": "人工修正定额",
    "unit": "m"
  },
  "manual_reason_codes": ["manual_override", "param_checked"],
  "manual_note": "龙虾审计确认 OpenClaw 候选方向对，但参数不一致。",
  "promotion_decision": "manual_override"
}
```

Effect:

- final corrected quota uses Lobster final quota
- OpenClaw absorbable report is rewritten to the human final decision
- Jarvis learns from the human final quota, not the discarded draft quota

### Case 3: Reject OpenClaw draft

```json
{
  "decision": "reject",
  "review_note": "人工驳回",
  "human_feedback_payload": {
    "protocol_version": "lobster_review_feedback.v1",
    "source": "lobster_audit",
    "manual_reason_codes": ["draft_rejected", "needs_reaudit"],
    "manual_note": "当前草稿证据不足，驳回后待重新审核。"
  }
}
```

Effect:

- OpenClaw draft marked rejected
- no formal correction is applied
- rejection reason stays with the review record

## Recommended Reason Codes

- `openclaw_confirmed`
- `manual_checked`
- `manual_override`
- `wrong_family`
- `wrong_param`
- `wrong_book`
- `same_family_but_param_conflict`
- `missing_candidate`
- `needs_reaudit`
- `evidence_insufficient`
- `search_direction_ok_but_final_choice_wrong`

## Compatibility Notes

- old `human_feedback_payload` objects are still accepted
- the backend normalizes Lobster payload into:
  - `protocol_version`
  - `source`
  - `adopt_openclaw`
  - `final_quotas`
  - `manual_reason_codes`
  - `manual_note`
  - `promotion_decision`
- when `adopt_openclaw = false`, the final human quota becomes the learning target

## Minimal Recommendation

If Lobster side wants the smallest valid useful payload, send at least:

```json
{
  "protocol_version": "lobster_review_feedback.v1",
  "source": "lobster_audit",
  "adopt_openclaw": false,
  "final_quota": {
    "quota_id": "C10-8-8",
    "name": "人工修正定额",
    "unit": "m"
  },
  "manual_reason_codes": ["manual_override"],
  "manual_note": "人工终审改判。"
}
```
