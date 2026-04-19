from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import cached_property
from types import MappingProxyType
from typing import Any, Iterator, Mapping


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return {_thaw(item) for item in value}
    return value


@dataclass(frozen=True)
class BillItemContext(Mapping[str, Any]):
    raw_name: str
    raw_desc: str
    section: str = ""
    sheet_name: str = ""
    unit: Any = None
    quantity: Any = None
    original_name: str = ""
    specialty: str = ""
    province: str = ""
    params: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    canonical_features: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    context_prior: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    classification: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    primary_query_profile: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    canonical_query: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    plugin_hints: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    unified_plan: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    query_route: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    input_gate: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    item: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_legacy_dict(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        item: dict[str, Any] | None = None,
    ) -> "BillItemContext":
        payload = dict(payload or {})
        canonical_query = dict(payload.get("canonical_query") or {})
        primary_query_profile = dict(
            payload.get("primary_query_profile")
            or canonical_query.get("primary_query_profile")
            or {}
        )
        return cls(
            raw_name=str(payload.get("name") or ""),
            raw_desc=str(payload.get("desc") or ""),
            section=str(payload.get("section") or ""),
            sheet_name=str(payload.get("sheet_name") or ""),
            unit=payload.get("unit"),
            quantity=payload.get("quantity"),
            original_name=str(payload.get("original_name") or payload.get("name") or ""),
            specialty=str(payload.get("specialty") or ""),
            province=str(payload.get("province") or ""),
            params=payload.get("params") or {},
            canonical_features=payload.get("canonical_features") or {},
            context_prior=payload.get("context_prior") or {},
            classification=payload.get("classification") or {},
            primary_query_profile=primary_query_profile,
            canonical_query=canonical_query,
            plugin_hints=payload.get("plugin_hints") or {},
            unified_plan=payload.get("unified_plan") or {},
            query_route=payload.get("query_route") or {},
            input_gate=payload.get("input_gate") or {},
            item=item if item is not None else payload.get("item"),
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", _freeze(self.params or {}))
        object.__setattr__(self, "canonical_features", _freeze(self.canonical_features or {}))
        object.__setattr__(self, "context_prior", _freeze(self.context_prior or {}))
        object.__setattr__(self, "classification", _freeze(self.classification or {}))
        object.__setattr__(self, "primary_query_profile", _freeze(self.primary_query_profile or {}))
        object.__setattr__(self, "canonical_query", _freeze(self.canonical_query or {}))
        object.__setattr__(self, "plugin_hints", _freeze(self.plugin_hints or {}))
        object.__setattr__(self, "unified_plan", _freeze(self.unified_plan or {}))
        object.__setattr__(self, "query_route", _freeze(self.query_route or {}))
        object.__setattr__(self, "input_gate", _freeze(self.input_gate or {}))
        if not object.__getattribute__(self, "original_name"):
            object.__setattr__(self, "original_name", self.raw_name)

    @property
    def name(self) -> str:
        return self.raw_name

    @property
    def desc(self) -> str:
        return self.raw_desc

    @cached_property
    def search_query(self) -> str:
        return str(self.canonical_query.get("search_query") or "").strip()

    @cached_property
    def full_query(self) -> str:
        return str(self.canonical_query.get("validation_query") or "").strip()

    @cached_property
    def normalized_query(self) -> str:
        return str(self.canonical_query.get("normalized_query") or "").strip()

    def mutable_params(self) -> dict[str, Any]:
        return _thaw(self.params)

    def mutable_canonical_features(self) -> dict[str, Any]:
        return _thaw(self.canonical_features)

    def mutable_context_prior(self) -> dict[str, Any]:
        return _thaw(self.context_prior)

    def mutable_classification(self) -> dict[str, Any]:
        return _thaw(self.classification)

    def with_updates(self, **changes: Any) -> "BillItemContext":
        return replace(self, **changes)

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "desc": self.desc,
            "section": self.section,
            "sheet_name": self.sheet_name,
            "unit": self.unit,
            "quantity": self.quantity,
            "canonical_query": _thaw(self.canonical_query),
            "full_query": self.full_query,
            "normalized_query": self.normalized_query,
            "search_query": self.search_query,
            "params": self.mutable_params(),
            "canonical_features": self.mutable_canonical_features(),
            "context_prior": self.mutable_context_prior(),
            "classification": self.mutable_classification(),
            "primary_query_profile": _thaw(self.primary_query_profile),
            "plugin_hints": _thaw(self.plugin_hints),
            "unified_plan": _thaw(self.unified_plan),
            "query_route": _thaw(self.query_route),
            "item": self.item,
            "input_gate": _thaw(self.input_gate),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_legacy_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_legacy_dict())

    def __len__(self) -> int:
        return len(self.to_legacy_dict())

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_legacy_dict().get(key, default)
