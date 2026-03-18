from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any


_ENTITY_RULES = [
    ("电缆", ("电缆", "电线", "导线", "配线", "电力电缆", "控制电缆")),
    ("配管", ("配管", "钢管敷设", "JDG", "KBG", "SC", "PC", "PVC管", "线管")),
    ("桥架", ("桥架", "线槽", "电缆桥架")),
    ("风管", ("风管", "风阀", "风口", "散流器", "消声器")),
    ("阀门", ("阀门", "蝶阀", "闸阀", "截止阀", "止回阀", "过滤器")),
    ("管道", ("管道", "钢管", "塑料管", "复合管", "喷淋管", "给水管", "排水管")),
    ("配电箱", ("配电箱", "配电柜", "控制箱", "控制柜")),
    ("开关插座", ("开关", "插座", "按钮")),
]

_SYSTEM_RULES = [
    ("消防", ("消防", "喷淋", "消火栓", "火灾报警", "灭火")),
    ("给排水", ("给水", "排水", "污水", "雨水", "中水")),
    ("电气", ("电气", "桥架", "电缆", "配线", "配管", "照明", "动力")),
    ("通风空调", ("通风", "空调", "风管", "风阀", "风口", "散流器")),
]

_ALIAS_RULES = [
    (re.compile(r"白铁管"), "镀锌钢板风管"),
    (re.compile(r"喷淋管"), "喷淋钢管"),
    (re.compile(r"镀锌管"), "镀锌钢管"),
]


@dataclass
class CanonicalFeatureSet:
    raw_text: str
    normalized_text: str
    canonical_name: str = ""
    entity: str = ""
    specialty: str = ""
    system: str = ""
    material: str = ""
    connection: str = ""
    install_method: str = ""
    dn: int | None = None
    cable_section: float | None = None
    cable_bundle: list[dict[str, Any]] = field(default_factory=list)
    kva: float | None = None
    kw: float | None = None
    ampere: float | None = None
    circuits: int | None = None
    traits: list[str] = field(default_factory=list)
    context_prior: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pick_by_rules(text: str, rules: list[tuple[str, tuple[str, ...]]]) -> str:
    for canonical, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return canonical
    return ""


def _normalize_text(text: str) -> str:
    text = text or ""
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _resolve_canonical_name(text: str, entity: str, material: str) -> str:
    for pattern, canonical in _ALIAS_RULES:
        if pattern.search(text):
            return canonical
    if entity and material:
        return f"{material}{entity}"
    return entity or material or ""


def build_canonical_features(raw_text: str,
                             params: dict[str, Any] | None = None,
                             specialty: str = "",
                             context_prior: dict[str, Any] | None = None) -> CanonicalFeatureSet:
    params = params or {}
    normalized_text = _normalize_text(raw_text)
    entity = _pick_by_rules(normalized_text, _ENTITY_RULES)
    system = _pick_by_rules(normalized_text, _SYSTEM_RULES)
    material = str(params.get("material") or "")
    connection = str(params.get("connection") or "")
    install_method = str(params.get("install_method") or "")

    traits: list[str] = []
    for key in ("shape", "elevator_type", "cable_type"):
        value = params.get(key)
        if value:
            traits.append(str(value))

    canonical_name = _resolve_canonical_name(normalized_text, entity, material)

    return CanonicalFeatureSet(
        raw_text=raw_text or "",
        normalized_text=normalized_text,
        canonical_name=canonical_name,
        entity=entity,
        specialty=specialty or "",
        system=system,
        material=material,
        connection=connection,
        install_method=install_method,
        dn=params.get("dn"),
        cable_section=params.get("cable_section"),
        cable_bundle=list(params.get("cable_bundle") or []),
        kva=params.get("kva"),
        kw=params.get("kw"),
        ampere=params.get("ampere"),
        circuits=params.get("circuits"),
        traits=traits,
        context_prior=dict(context_prior or {}),
    )
