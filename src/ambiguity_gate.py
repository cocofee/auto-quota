from dataclasses import asdict, dataclass

import config

from src.policy_engine import PolicyEngine
from src.utils import safe_float


def _result_quota_signature(result: dict | None) -> tuple[str, ...]:
    quotas = (result or {}).get("quotas") or []
    return tuple(str(q.get("quota_id", "")).strip() for q in quotas if q.get("quota_id"))


@dataclass(frozen=True)
class AmbiguityDecision:
    can_fastpath: bool
    is_ambiguous: bool
    reason: str
    top_quota_id: str
    top_param_score: float
    top_score_gap: float
    candidates_count: int
    conflict_with_backup: bool
    route: str = ""
    require_final_review: bool = False
    risk_level: str = "low"
    arbitration_applied: bool = False
    audit_recommended: bool = False
    audit_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return asdict(self)


def _has_backup_conflict(candidates: list[dict],
                         exp_backup: dict | None = None,
                         rule_backup: dict | None = None) -> bool:
    if not candidates:
        return True
    top_id = str(candidates[0].get("quota_id", "")).strip()
    if not top_id:
        return True

    for backup in (exp_backup, rule_backup):
        if not backup:
            continue
        backup_conf = safe_float(backup.get("confidence"), 0.0)
        if backup_conf < config.CONFIDENCE_YELLOW:
            continue
        backup_sig = _result_quota_signature(backup)
        if backup_sig and backup_sig[0] != top_id:
            return True
    return False


def analyze_ambiguity(candidates: list[dict],
                      exp_backup: dict | None = None,
                      rule_backup: dict | None = None,
                      route_profile=None,
                      arbitration: dict | None = None) -> AmbiguityDecision:
    policy = PolicyEngine.get_route_policy(route_profile)
    route = policy.route
    arbitration_applied = bool((arbitration or {}).get("applied"))
    if not config.AGENT_FASTPATH_ENABLED:
        return AmbiguityDecision(
            can_fastpath=False,
            is_ambiguous=False,
            reason="fastpath_disabled",
            top_quota_id="",
            top_param_score=0.0,
            top_score_gap=0.0,
            candidates_count=len(candidates or []),
            conflict_with_backup=False,
            route=route,
            require_final_review=True,
            risk_level="high",
            arbitration_applied=arbitration_applied,
        )

    if not candidates:
        return AmbiguityDecision(
            can_fastpath=False,
            is_ambiguous=True,
            reason="no_candidates",
            top_quota_id="",
            top_param_score=0.0,
            top_score_gap=0.0,
            candidates_count=0,
            conflict_with_backup=False,
            route=route,
            require_final_review=True,
            risk_level="high",
            arbitration_applied=arbitration_applied,
        )

    top = candidates[0]
    top_id = str(top.get("quota_id", "")).strip()
    top_score = safe_float(top.get("param_score"), 0.0)
    top_has_hard_conflict = bool(
        top.get("feature_alignment_hard_conflict") or top.get("logic_hard_conflict")
    )

    if not top.get("param_match", True):
        return AmbiguityDecision(
            can_fastpath=False,
            is_ambiguous=True,
            reason="param_mismatch",
            top_quota_id=top_id,
            top_param_score=top_score,
            top_score_gap=0.0,
            candidates_count=len(candidates),
            conflict_with_backup=False,
            route=route,
            require_final_review=True,
            risk_level="high",
            arbitration_applied=arbitration_applied,
        )

    reranker_failure_window = int(
        max(1, PolicyEngine.get_fastpath_threshold("reranker_failure_window", 3))
    )
    if any(c.get("reranker_failed") for c in candidates[:reranker_failure_window]):
        return AmbiguityDecision(
            can_fastpath=False,
            is_ambiguous=True,
            reason="reranker_failed",
            top_quota_id=top_id,
            top_param_score=top_score,
            top_score_gap=0.0,
            candidates_count=len(candidates),
            conflict_with_backup=False,
            route=route,
            require_final_review=True,
            risk_level="high",
            arbitration_applied=arbitration_applied,
        )

    backup_conflict = _has_backup_conflict(
        candidates, exp_backup=exp_backup, rule_backup=rule_backup)
    if backup_conflict:
        return AmbiguityDecision(
            can_fastpath=False,
            is_ambiguous=True,
            reason="backup_conflict",
            top_quota_id=top_id,
            top_param_score=top_score,
            top_score_gap=0.0,
            candidates_count=len(candidates),
            conflict_with_backup=True,
            route=route,
            require_final_review=True,
            risk_level="high",
            arbitration_applied=arbitration_applied,
        )

    if top_has_hard_conflict:
        return AmbiguityDecision(
            can_fastpath=False,
            is_ambiguous=True,
            reason="hard_conflict",
            top_quota_id=top_id,
            top_param_score=top_score,
            top_score_gap=0.0,
            candidates_count=len(candidates),
            conflict_with_backup=False,
            route=route,
            require_final_review=True,
            risk_level="high",
            arbitration_applied=arbitration_applied,
        )

    if (
        config.CGR_ACCEPT_HEAD_ENABLED
        and top.get("cgr_accept_score") is not None
        and top.get("cgr_probability") is not None
    ):
        accept_score = safe_float(top.get("cgr_accept_score"), 0.0)
        top_probability = safe_float(top.get("cgr_probability"), 0.0)
        gap = safe_float(top.get("cgr_prob_gap_top2"), 0.0)
        if (
            bool(top.get("cgr_accept"))
            and accept_score >= config.CGR_ACCEPT_THRESHOLD
            and top_probability >= config.CGR_MIN_TOP1_PROB
        ):
            audit_reasons: list[str] = []
            if arbitration_applied:
                audit_reasons.append("arbitration_applied")
            return AmbiguityDecision(
                can_fastpath=True,
                is_ambiguous=False,
                reason="accept_head_confident",
                top_quota_id=top_id,
                top_param_score=top_score,
                top_score_gap=gap,
                candidates_count=len(candidates),
                conflict_with_backup=False,
                route=route,
                require_final_review=arbitration_applied,
                risk_level="medium" if arbitration_applied else "low",
                arbitration_applied=arbitration_applied,
                audit_recommended=bool(audit_reasons),
                audit_reasons=tuple(audit_reasons),
            )
        return AmbiguityDecision(
            can_fastpath=False,
            is_ambiguous=True,
            reason="accept_head_reject",
            top_quota_id=top_id,
            top_param_score=top_score,
            top_score_gap=gap,
            candidates_count=len(candidates),
            conflict_with_backup=False,
            route=route,
            require_final_review=True,
            risk_level="high",
            arbitration_applied=arbitration_applied,
        )

    if top_score < policy.agent_fastpath_score:
        return AmbiguityDecision(
            can_fastpath=False,
            is_ambiguous=True,
            reason="low_param_score",
            top_quota_id=top_id,
            top_param_score=top_score,
            top_score_gap=0.0,
            candidates_count=len(candidates),
            conflict_with_backup=False,
            route=route,
            require_final_review=True,
            risk_level="high",
            arbitration_applied=arbitration_applied,
        )

    if policy.require_param_match:
        top_detail = str(top.get("param_detail", "") or "")
        missing_primary_param_min_score = float(
            PolicyEngine.get_fastpath_threshold("missing_primary_param_min_score", 0.70)
        )
        if ("定额无" in top_detail or "未指定" in top_detail) and top_score < missing_primary_param_min_score:
            return AmbiguityDecision(
                can_fastpath=False,
                is_ambiguous=True,
                reason="missing_primary_param",
                top_quota_id=top_id,
                top_param_score=top_score,
                top_score_gap=0.0,
                candidates_count=len(candidates),
                conflict_with_backup=False,
                route=route,
                require_final_review=True,
                risk_level="high",
                arbitration_applied=arbitration_applied,
            )

    if len(candidates) < policy.agent_fastpath_min_candidates:
        return AmbiguityDecision(
            can_fastpath=False,
            is_ambiguous=True,
            reason="insufficient_candidates",
            top_quota_id=top_id,
            top_param_score=top_score,
            top_score_gap=0.0,
            candidates_count=len(candidates),
            conflict_with_backup=False,
            route=route,
            require_final_review=True,
            risk_level="high",
            arbitration_applied=arbitration_applied,
        )

    top1_rs = safe_float(
        candidates[0].get("rerank_score", candidates[0].get("hybrid_score", 0.0)), 0.0)
    top2_rs = safe_float(
        candidates[1].get("rerank_score", candidates[1].get("hybrid_score", 0.0)), 0.0)
    gap = top1_rs - top2_rs
    if gap < policy.agent_fastpath_score_gap:
        return AmbiguityDecision(
            can_fastpath=False,
            is_ambiguous=True,
            reason="small_score_gap",
            top_quota_id=top_id,
            top_param_score=top_score,
            top_score_gap=gap,
            candidates_count=len(candidates),
            conflict_with_backup=False,
            route=route,
            require_final_review=True,
            risk_level="high",
            arbitration_applied=arbitration_applied,
        )

    arbitrated_min_gap = float(
        PolicyEngine.get_fastpath_threshold("arbitrated_min_top1_margin", 0.08)
    )
    if arbitration_applied and gap < max(policy.agent_fastpath_score_gap, arbitrated_min_gap):
        return AmbiguityDecision(
            can_fastpath=False,
            is_ambiguous=True,
            reason="arbitrated_small_gap",
            top_quota_id=top_id,
            top_param_score=top_score,
            top_score_gap=gap,
            candidates_count=len(candidates),
            conflict_with_backup=False,
            route=route,
            require_final_review=True,
            risk_level="high",
            arbitration_applied=True,
        )

    audit_reasons: list[str] = []
    fastpath_margin = max(safe_float(getattr(config, "AGENT_FASTPATH_MARGIN", 0.0), 0.0), 0.0)
    if top_score < (policy.agent_fastpath_score + fastpath_margin):
        audit_reasons.append("borderline_param_score")
    if gap < (policy.agent_fastpath_score_gap + fastpath_margin):
        audit_reasons.append("borderline_score_gap")
    if arbitration_applied:
        audit_reasons.append("arbitration_applied")

    return AmbiguityDecision(
        can_fastpath=True,
        is_ambiguous=False,
        reason="high_confidence",
        top_quota_id=top_id,
        top_param_score=top_score,
        top_score_gap=gap,
        candidates_count=len(candidates),
        conflict_with_backup=False,
        route=route,
        require_final_review=arbitration_applied,
        risk_level="medium" if arbitration_applied else "low",
        arbitration_applied=arbitration_applied,
        audit_recommended=bool(audit_reasons),
        audit_reasons=tuple(audit_reasons),
    )
