from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import config

from src.query_router import normalize_query_route


DEFAULT_THRESHOLD_PAYLOAD: dict[str, Any] = {
    "confidence": {
        "experience_exact_degrade_cap": 88,
        "same_quota_confirm_boost": 92,
    },
    "fastpath": {
        "reranker_failure_window": 3,
        "missing_primary_param_min_score": 0.70,
        "arbitrated_min_top1_margin": 0.08,
    },
    "pickers": {
        "explicit_hybrid_margin": 0.005,
    },
    "route_policies": {
        "balanced": {
            "rule_direct_confidence": 80,
            "agent_fastpath_score": 0.60,
            "agent_fastpath_score_gap": 0.03,
            "agent_fastpath_min_candidates": 2,
        },
        "installation_spec": {
            "rule_direct_confidence": 82,
            "agent_fastpath_score": 0.72,
            "agent_fastpath_score_gap": 0.05,
            "agent_fastpath_min_candidates": 2,
            "require_param_match": True,
        },
        "material": {
            "rule_direct_confidence": 82,
            "agent_fastpath_score": 0.68,
            "agent_fastpath_score_gap": 0.04,
            "agent_fastpath_min_candidates": 2,
            "require_param_match": True,
        },
        "ambiguous_short": {
            "rule_direct_confidence": 88,
            "agent_fastpath_score": 0.78,
            "agent_fastpath_score_gap": 0.06,
            "agent_fastpath_min_candidates": 3,
            "require_param_match": True,
        },
        "semantic_description": {
            "agent_fastpath_score": 0.58,
            "agent_fastpath_score_gap": 0.02,
            "agent_fastpath_min_candidates": 2,
        },
    },
}

RULE_DIRECT_CONFIDENCE = int(
    DEFAULT_THRESHOLD_PAYLOAD["route_policies"]["balanced"]["rule_direct_confidence"]
)


def _thresholds_path() -> Path:
    raw_path = getattr(config, "POLICY_THRESHOLDS_PATH", "")
    if raw_path:
        return Path(raw_path)
    return Path(config.PROJECT_ROOT) / "policy" / "thresholds.yaml"


def _parse_scalar(value: str) -> Any:
    text = str(value).strip()
    if not text:
        return ""

    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False

    if text[:1] == text[-1:] and text[:1] in {'"', "'"}:
        return text[1:-1]

    try:
        if any(marker in text for marker in (".", "e", "E")):
            return float(text)
        return int(text)
    except ValueError:
        return text


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        key, sep, remainder = stripped.partition(":")
        if not sep:
            continue

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        parent = stack[-1][1]
        key = key.strip()
        value_text = remainder.strip()
        if not value_text:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
            continue
        parent[key] = _parse_scalar(value_text)

    return root


def _load_yaml_payload(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        payload = _parse_simple_yaml(text)
        return payload if isinstance(payload, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
            continue
        merged[key] = value
    return merged


@lru_cache(maxsize=1)
def _load_threshold_payload() -> dict[str, Any]:
    path = _thresholds_path()
    if not path.exists():
        return DEFAULT_THRESHOLD_PAYLOAD
    try:
        payload = _load_yaml_payload(path)
        return _deep_merge(DEFAULT_THRESHOLD_PAYLOAD, payload)
    except Exception:
        return DEFAULT_THRESHOLD_PAYLOAD


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
    @classmethod
    def clear_caches(cls) -> None:
        _load_threshold_payload.cache_clear()

    @classmethod
    def get_threshold_payload(cls) -> dict[str, Any]:
        return _load_threshold_payload()

    @classmethod
    def get_threshold(cls, dotted_key: str, default: Any = None) -> Any:
        current: Any = cls.get_threshold_payload()
        for part in str(dotted_key or "").split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    @classmethod
    def get_confidence_threshold(cls, name: str, default: Any = None) -> Any:
        return cls.get_threshold(f"confidence.{name}", default)

    @classmethod
    def get_fastpath_threshold(cls, name: str, default: Any = None) -> Any:
        return cls.get_threshold(f"fastpath.{name}", default)

    @classmethod
    def get_picker_threshold(cls, name: str, default: Any = None) -> Any:
        return cls.get_threshold(f"pickers.{name}", default)

    @classmethod
    def get_route_policy(cls, route_profile=None) -> RoutePolicy:
        route = normalize_query_route(route_profile)
        thresholds = cls.get_threshold_payload()
        route_policies = dict(thresholds.get("route_policies") or {})
        balanced = dict(route_policies.get("balanced") or {})
        route_override = dict(route_policies.get(route) or {})

        require_param_match = bool(getattr(config, "AGENT_FASTPATH_REQUIRE_PARAM_MATCH", True))
        if "require_param_match" in balanced:
            require_param_match = bool(balanced["require_param_match"])
        if "require_param_match" in route_override:
            require_param_match = bool(route_override["require_param_match"])

        base_policy = RoutePolicy(
            route=route,
            rule_direct_confidence=int(
                max(
                    float(balanced.get("rule_direct_confidence", RULE_DIRECT_CONFIDENCE) or RULE_DIRECT_CONFIDENCE),
                    float(getattr(config, "RULE_DIRECT_CONFIDENCE", RULE_DIRECT_CONFIDENCE)),
                )
            ),
            agent_fastpath_score=max(
                float(balanced.get("agent_fastpath_score", 0.60) or 0.60),
                float(getattr(config, "AGENT_FASTPATH_SCORE", 0.60)),
            ),
            agent_fastpath_score_gap=max(
                float(balanced.get("agent_fastpath_score_gap", 0.03) or 0.03),
                float(getattr(config, "AGENT_FASTPATH_SCORE_GAP", 0.03)),
            ),
            agent_fastpath_min_candidates=int(
                max(float(balanced.get("agent_fastpath_min_candidates", 2) or 2), 2.0)
            ),
            require_param_match=require_param_match,
        )

        if not route_override:
            return base_policy

        return RoutePolicy(
            route=route,
            rule_direct_confidence=int(
                max(
                    float(base_policy.rule_direct_confidence),
                    float(route_override.get("rule_direct_confidence", base_policy.rule_direct_confidence)),
                )
            ),
            agent_fastpath_score=max(
                float(base_policy.agent_fastpath_score),
                float(route_override.get("agent_fastpath_score", base_policy.agent_fastpath_score)),
            ),
            agent_fastpath_score_gap=max(
                float(base_policy.agent_fastpath_score_gap),
                float(route_override.get("agent_fastpath_score_gap", base_policy.agent_fastpath_score_gap)),
            ),
            agent_fastpath_min_candidates=int(
                max(
                    float(base_policy.agent_fastpath_min_candidates),
                    float(route_override.get("agent_fastpath_min_candidates", base_policy.agent_fastpath_min_candidates)),
                )
            ),
            require_param_match=require_param_match,
        )

    @classmethod
    def should_use_rule_direct(cls, confidence: float, route_profile=None) -> tuple[bool, int]:
        policy = cls.get_route_policy(route_profile)
        return float(confidence or 0) >= policy.rule_direct_confidence, policy.rule_direct_confidence
