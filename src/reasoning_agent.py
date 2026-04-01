from __future__ import annotations

from dataclasses import asdict, dataclass

from src.ambiguity_gate import analyze_ambiguity
from src.utils import safe_float


def _normalize_text(value) -> str:
    return str(value or "").strip()


def _normalize_list(values) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    cleaned = []
    seen = set()
    for value in values:
        text = _normalize_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _normalize_dict_values(values) -> dict[str, str]:
    if not isinstance(values, dict):
        return {}
    normalized = {}
    for key, value in values.items():
        text = _normalize_text(value)
        if text:
            normalized[str(key)] = text
    return normalized


def _candidate_features(candidate: dict) -> dict:
    return (
        candidate.get("candidate_canonical_features")
        or candidate.get("canonical_features")
        or {}
    )


@dataclass(frozen=True)
class CandidateReasoningView:
    rank: int
    quota_id: str
    name: str
    unit: str
    param_score: float
    rerank_score: float
    logic_score: float
    entity: str
    canonical_name: str
    system: str
    material: str
    connection: str
    install_method: str
    traits: list[str]
    specs: dict[str, str]
    numeric_params: dict[str, str]

    def as_dict(self) -> dict:
        return asdict(self)


class ReasoningAgent:
    """对歧义候选做结构化仲裁摘要，供 Agent Prompt 使用。"""

    _COMPARE_FIELDS = (
        ("entity", "构件"),
        ("canonical_name", "标准名"),
        ("system", "系统"),
        ("material", "材质"),
        ("connection", "连接"),
        ("install_method", "安装方式"),
    )

    def build_packet(self,
                     bill_item: dict,
                     candidates: list[dict],
                     *,
                     route_profile=None,
                     exp_backup: dict | None = None,
                     rule_backup: dict | None = None,
                     top_n: int = 5) -> dict:
        focus = self._build_candidate_views(candidates, top_n=top_n)
        decision = analyze_ambiguity(
            candidates,
            exp_backup=exp_backup,
            rule_backup=rule_backup,
            route_profile=route_profile,
        )
        conflict_fields, conflict_summaries = self._collect_conflicts(focus)

        return {
            "engaged": bool(focus) and (decision.is_ambiguous or len(conflict_fields) > 0),
            "decision": decision.as_dict(),
            "focus_candidates": [view.as_dict() for view in focus],
            "conflict_fields": conflict_fields,
            "conflict_summaries": conflict_summaries,
            "compare_points": self._build_compare_points(conflict_fields, decision.reason),
        }

    def _build_candidate_views(self,
                               candidates: list[dict],
                               *,
                               top_n: int = 5) -> list[CandidateReasoningView]:
        views = []
        for idx, candidate in enumerate(candidates[:top_n], start=1):
            features = _candidate_features(candidate)
            views.append(CandidateReasoningView(
                rank=idx,
                quota_id=_normalize_text(candidate.get("quota_id")),
                name=_normalize_text(candidate.get("name")),
                unit=_normalize_text(candidate.get("unit")),
                param_score=safe_float(candidate.get("param_score"), 0.0),
                rerank_score=safe_float(
                    candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)), 0.0),
                logic_score=safe_float(candidate.get("logic_score"), 0.0),
                entity=_normalize_text(features.get("entity")),
                canonical_name=_normalize_text(features.get("canonical_name")),
                system=_normalize_text(features.get("system")),
                material=_normalize_text(features.get("material")),
                connection=_normalize_text(features.get("connection")),
                install_method=_normalize_text(features.get("install_method")),
                traits=_normalize_list(features.get("traits")),
                specs=_normalize_dict_values(features.get("specs")),
                numeric_params=_normalize_dict_values(features.get("numeric_params")),
            ))
        return views

    def _collect_conflicts(self,
                           views: list[CandidateReasoningView]) -> tuple[list[str], list[str]]:
        if len(views) <= 1:
            return [], []

        conflict_fields: list[str] = []
        conflict_summaries: list[str] = []

        for field, label in self._COMPARE_FIELDS:
            values = sorted({
                _normalize_text(getattr(view, field))
                for view in views
                if _normalize_text(getattr(view, field))
            })
            if len(values) > 1:
                conflict_fields.append(field)
                conflict_summaries.append(f"{label}冲突: {' / '.join(values)}")

        trait_values = sorted({trait for view in views for trait in view.traits})
        if len(trait_values) > 1:
            conflict_fields.append("traits")
            conflict_summaries.append(f"特征差异: {' / '.join(trait_values[:6])}")

        spec_keys = sorted({key for view in views for key in view.specs.keys()})
        for key in spec_keys:
            values = sorted({
                view.specs.get(key, "")
                for view in views
                if view.specs.get(key, "")
            })
            if len(values) > 1:
                if "specs" not in conflict_fields:
                    conflict_fields.append("specs")
                conflict_summaries.append(f"规格差异[{key}]: {' / '.join(values)}")

        numeric_keys = sorted({key for view in views for key in view.numeric_params.keys()})
        for key in numeric_keys:
            values = sorted({
                view.numeric_params.get(key, "")
                for view in views
                if view.numeric_params.get(key, "")
            })
            if len(values) > 1:
                if "numeric_params" not in conflict_fields:
                    conflict_fields.append("numeric_params")
                conflict_summaries.append(f"数值差异[{key}]: {' / '.join(values)}")

        return conflict_fields, conflict_summaries

    @staticmethod
    def _build_compare_points(conflict_fields: list[str], ambiguity_reason: str) -> list[str]:
        points = []
        if ambiguity_reason == "small_score_gap":
            points.append("重点比较前两名候选的核心差异，不要只看排序分数。")
        if ambiguity_reason == "backup_conflict":
            points.append("搜索结果与经验/规则备选冲突，必须给出排除理由。")
        if "material" in conflict_fields:
            points.append("优先核对材质是否一致，材质不一致直接排除。")
        if "connection" in conflict_fields:
            points.append("优先核对连接方式，丝接/沟槽/法兰/焊接不能混套。")
        if "numeric_params" in conflict_fields or "specs" in conflict_fields:
            points.append("优先核对规格和数值档位，按向上取档原则仲裁。")
        if "entity" in conflict_fields or "canonical_name" in conflict_fields:
            points.append("优先核对工作对象是否同类，避免把配套件当主定额。")
        if not points:
            points.append("请对前几名候选逐一说明保留与排除理由。")
        return points
