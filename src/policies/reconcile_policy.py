from dataclasses import dataclass

import config

from src.policy_engine import PolicyEngine


@dataclass(frozen=True)
class ReconcilePolicy:
    """Decision policy for search/experience reconciliation."""

    same_quota_min_confidence: int = 92
    experience_exact_degrade_cap: int = 88
    experience_similar_override_margin: int = 0
    similar_threshold: float = 0.80
    exact_min_confirm_count: int = 2

    @classmethod
    def from_runtime(cls) -> "ReconcilePolicy":
        return cls(
            same_quota_min_confidence=int(
                PolicyEngine.get_confidence_threshold(
                    "same_quota_confirm_boost",
                    getattr(config, "RECONCILE_SAME_QUOTA_MIN_CONF", 92),
                )
            ),
            experience_exact_degrade_cap=int(
                PolicyEngine.get_confidence_threshold(
                    "experience_exact_degrade_cap",
                    getattr(config, "RECONCILE_EXP_EXACT_CAP", 88),
                )
            ),
            experience_similar_override_margin=int(
                PolicyEngine.get_confidence_threshold(
                    "experience_similar_override_margin",
                    getattr(config, "RECONCILE_EXP_SIMILAR_MARGIN", 0),
                )
            ),
            similar_threshold=float(
                PolicyEngine.get_confidence_threshold(
                    "experience_similar_threshold",
                    getattr(config, "EXPERIENCE_SIMILAR_THRESHOLD", 0.80),
                )
            ),
            exact_min_confirm_count=int(
                PolicyEngine.get_confidence_threshold(
                    "experience_exact_min_confirm",
                    getattr(config, "EXPERIENCE_EXACT_MIN_CONFIRM", 2),
                )
            ),
        )


def get_reconcile_policy() -> ReconcilePolicy:
    return ReconcilePolicy.from_runtime()
