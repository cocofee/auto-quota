from collections.abc import Mapping, Sequence
from typing import Any


def apply_rule_constraints(
    candidates: Sequence[Mapping[str, Any]] | None,
    rules: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Normalize rule definitions and apply optional per-rule overrides."""
    overrides = dict(rules or {})
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    for candidate in candidates or []:
        if not isinstance(candidate, Mapping):
            continue
        name = str(candidate.get("name") or "").strip()
        builder = candidate.get("builder")
        if not name or not callable(builder) or name in seen:
            continue

        override = overrides.get(name)
        if override is False:
            continue

        rule = dict(candidate)
        if isinstance(override, Mapping):
            rule.update(override)

        enabled = rule.get("enabled", True)
        if callable(enabled):
            enabled = enabled()
        if not enabled:
            continue

        normalized.append(rule)
        seen.add(name)

    return normalized


def try_rule_match(
    item: Mapping[str, Any] | None,
    rules: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    """Run ordered pre-match rules and return the first successful hit."""
    item = item or {}
    constraints = item.get("rule_constraints")
    if not isinstance(constraints, Mapping):
        constraints = None

    for rule in apply_rule_constraints(rules, constraints):
        query = rule["builder"](item)
        if not query:
            continue
        return {
            "name": str(rule.get("name") or "").strip(),
            "query": str(query).strip(),
            "apply_synonyms": bool(rule.get("apply_synonyms", True)),
        }
    return None
