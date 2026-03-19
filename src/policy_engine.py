from dataclasses import asdict, dataclass

import config

from src.query_router import normalize_query_route


RULE_DIRECT_CONFIDENCE = 80


@dataclass(frozen=True)
class RoutePolicy:
    route: str
    rule_direct_confidence: int
    agent_fastpath_score: float
    agent_fastpath_score_gap: float
    agent_fastpath_min_candidates: int
    require_param_match: bool

    def as_dict(self) -> dict:
        return asdict(self)


class PolicyEngine:
    @staticmethod
    def get_route_policy(route_profile=None) -> RoutePolicy:
        route = normalize_query_route(route_profile)
        policy = RoutePolicy(
            route=route,
            rule_direct_confidence=RULE_DIRECT_CONFIDENCE,
            agent_fastpath_score=float(getattr(config, "AGENT_FASTPATH_SCORE", 0.60)),
            agent_fastpath_score_gap=float(getattr(config, "AGENT_FASTPATH_SCORE_GAP", 0.03)),
            agent_fastpath_min_candidates=2,
            require_param_match=bool(getattr(config, "AGENT_FASTPATH_REQUIRE_PARAM_MATCH", True)),
        )

        if route == "installation_spec":
            return RoutePolicy(
                route=route,
                rule_direct_confidence=max(policy.rule_direct_confidence, 82),
                agent_fastpath_score=max(policy.agent_fastpath_score, 0.72),
                agent_fastpath_score_gap=max(policy.agent_fastpath_score_gap, 0.05),
                agent_fastpath_min_candidates=2,
                require_param_match=True,
            )
        if route == "material":
            return RoutePolicy(
                route=route,
                rule_direct_confidence=max(policy.rule_direct_confidence, 82),
                agent_fastpath_score=max(policy.agent_fastpath_score, 0.68),
                agent_fastpath_score_gap=max(policy.agent_fastpath_score_gap, 0.04),
                agent_fastpath_min_candidates=2,
                require_param_match=True,
            )
        if route == "ambiguous_short":
            return RoutePolicy(
                route=route,
                rule_direct_confidence=max(policy.rule_direct_confidence, 88),
                agent_fastpath_score=max(policy.agent_fastpath_score, 0.78),
                agent_fastpath_score_gap=max(policy.agent_fastpath_score_gap, 0.06),
                agent_fastpath_min_candidates=3,
                require_param_match=True,
            )
        if route == "semantic_description":
            return RoutePolicy(
                route=route,
                rule_direct_confidence=policy.rule_direct_confidence,
                agent_fastpath_score=max(policy.agent_fastpath_score, 0.58),
                agent_fastpath_score_gap=max(policy.agent_fastpath_score_gap, 0.02),
                agent_fastpath_min_candidates=2,
                require_param_match=policy.require_param_match,
            )
        return policy

    @classmethod
    def should_use_rule_direct(cls, confidence: float, route_profile=None) -> tuple[bool, int]:
        policy = cls.get_route_policy(route_profile)
        return float(confidence or 0) >= policy.rule_direct_confidence, policy.rule_direct_confidence
