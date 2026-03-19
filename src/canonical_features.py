from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.canonical_dictionary import (
    build_numeric_params,
    build_specs,
    collect_traits,
    detect_entity,
    detect_family,
    detect_system,
    normalize_connection,
    normalize_install_method,
    normalize_material,
    normalize_text,
    resolve_canonical_name,
)


@dataclass
class CanonicalFeatureSet:
    raw_text: str
    normalized_text: str
    canonical_name: str = ""
    entity: str = ""
    family: str = ""
    specialty: str = ""
    system: str = ""
    material: str = ""
    connection: str = ""
    install_method: str = ""
    laying_method: str = ""
    voltage_level: str = ""
    numeric_params: dict[str, Any] = field(default_factory=dict)
    specs: dict[str, Any] = field(default_factory=dict)
    dn: int | None = None
    cable_section: float | None = None
    bridge_wh_sum: float | None = None
    cable_bundle: list[dict[str, Any]] = field(default_factory=list)
    kva: float | None = None
    kw: float | None = None
    ampere: float | None = None
    circuits: int | None = None
    port_count: int | None = None
    traits: list[str] = field(default_factory=list)
    context_prior: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_canonical_features(raw_text: str,
                             params: dict[str, Any] | None = None,
                             specialty: str = "",
                             context_prior: dict[str, Any] | None = None) -> CanonicalFeatureSet:
    params = params or {}
    context_prior = dict(context_prior or {})

    normalized_text = normalize_text(raw_text)
    material = normalize_material(str(params.get("material") or ""), normalized_text)
    connection = normalize_connection(str(params.get("connection") or ""), normalized_text)
    install_method = normalize_install_method(str(params.get("install_method") or ""), normalized_text)
    entity = detect_entity(normalized_text)
    system = detect_system(
        normalized_text,
        specialty=specialty,
        context_prior=context_prior,
        entity=entity,
    )
    traits = collect_traits(params, context_prior=context_prior, raw_text=normalized_text)
    family = detect_family(
        normalized_text,
        entity=entity,
        system=system,
        material=material,
        install_method=install_method,
        traits=traits,
        context_prior=context_prior,
    )
    canonical_name = resolve_canonical_name(
        normalized_text,
        entity=entity,
        material=material,
    )

    return CanonicalFeatureSet(
        raw_text=raw_text or "",
        normalized_text=normalized_text,
        canonical_name=canonical_name,
        entity=entity,
        family=family,
        specialty=specialty or "",
        system=system,
        material=material,
        connection=connection,
        install_method=install_method,
        laying_method=str(params.get("laying_method") or ""),
        voltage_level=str(params.get("voltage_level") or ""),
        numeric_params=build_numeric_params(params),
        specs=build_specs({**params, "install_method": install_method}),
        dn=params.get("dn"),
        cable_section=params.get("cable_section"),
        bridge_wh_sum=params.get("bridge_wh_sum"),
        cable_bundle=list(params.get("cable_bundle") or []),
        kva=params.get("kva"),
        kw=params.get("kw"),
        ampere=params.get("ampere"),
        circuits=params.get("circuits"),
        port_count=params.get("port_count"),
        traits=traits,
        context_prior=context_prior,
    )
