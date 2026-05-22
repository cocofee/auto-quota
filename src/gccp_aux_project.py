"""GCCP auxiliary pricing-source workflow primitives.

This module intentionally treats the formal GBQ7 project as read-only.  The
first implementation creates an import package for a separate auxiliary GBQ7
pricing-source project.  A later implementation can replace the updater with a
real GBQ7 writer, but only for the auxiliary project.
"""

from __future__ import annotations

import json
import secrets
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


FORMAL_PROJECT_POLICY = "formal_project_readonly_reuse_only"
AUX_UPDATE_STRATEGY_EXCEL = "aux_gbq7_excel_import_package"
AUX_UPDATE_STRATEGY_DIRECT_PLACEHOLDER = "aux_gbq7_direct_writer_placeholder"


@dataclass
class GccpAuxRunRequest:
    bill_excel: str
    formal_gbq7: str = ""
    aux_gbq7: str = ""
    province: str = ""
    aux_provinces: list[str] = field(default_factory=list)
    mode: str = "agent"
    sheet: str = ""
    limit: int | None = None
    use_experience: bool = True
    run_id: str = ""
    notes: str = ""


@dataclass
class GccpAuxRunManifest:
    schema_version: int
    run_id: str
    created_at: str
    policy: str
    update_strategy: str
    bill_excel: str
    generated_excel: str
    generated_json: str
    formal_gbq7: str
    aux_gbq7: str
    province: str
    aux_provinces: list[str]
    mode: str
    sheet: str
    limit: int | None
    use_experience: bool
    stats: dict[str, Any]
    safety: dict[str, Any]
    next_steps: list[str]
    notes: str = ""


class AuxProjectUpdater(Protocol):
    """Strategy interface for maintaining an auxiliary GCCP pricing source."""

    strategy_name: str

    def build(
        self,
        request: GccpAuxRunRequest,
        *,
        output_dir: Path,
    ) -> GccpAuxRunManifest:
        """Create/update the auxiliary pricing source and return a manifest."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id(prefix: str = "gccp_aux") -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{secrets.token_hex(3)}"


def normalize_path(value: str | Path | None) -> str:
    if not value:
        return ""
    return str(Path(value).expanduser().resolve())


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix=f"{target.stem}.",
            dir=str(target.parent),
            encoding="utf-8",
            delete=False,
        ) as handle:
            tmp_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        Path(tmp_name).replace(target)
    finally:
        if tmp_name and Path(tmp_name).exists():
            try:
                Path(tmp_name).unlink()
            except OSError:
                pass


def write_manifest(path: str | Path, manifest: GccpAuxRunManifest) -> None:
    atomic_write_json(path, asdict(manifest))


def build_safety_report(request: GccpAuxRunRequest, generated_excel: Path) -> dict[str, Any]:
    formal_path = normalize_path(request.formal_gbq7)
    aux_path = normalize_path(request.aux_gbq7)
    output_path = normalize_path(generated_excel)

    violations: list[str] = []
    if formal_path and output_path.lower() == formal_path.lower():
        violations.append("generated output path equals formal GBQ7 path")
    if formal_path and aux_path and formal_path.lower() == aux_path.lower():
        violations.append("formal GBQ7 and auxiliary GBQ7 are the same file")

    return {
        "formal_project_writable_by_autoquota": False,
        "formal_project_policy": FORMAL_PROJECT_POLICY,
        "allowed_formal_operation": "GCCP AutoReuseData/ReuseHistoricalData only",
        "generated_output_is_formal_project": bool(
            formal_path and output_path.lower() == formal_path.lower()
        ),
        "formal_and_aux_same_file": bool(
            formal_path and aux_path and formal_path.lower() == aux_path.lower()
        ),
        "violations": violations,
        "status": "blocked" if violations else "ok",
    }


def default_next_steps() -> list[str]:
    return [
        "Open the auxiliary GBQ7 pricing-source project in GCCP.",
        "Import the generated Excel into the auxiliary GBQ7 only, then adjust quota items and materials there.",
        "Save the auxiliary GBQ7 after manual review.",
        "Open the formal GBQ7 project and run GCCP reuse pricing from the auxiliary GBQ7 source.",
        "Run the before/after validation snapshot and confirm bill identity fields did not change.",
    ]


class ExcelImportPackageUpdater:
    """Current safe updater: AutoQuota creates files for the auxiliary GBQ7."""

    strategy_name = AUX_UPDATE_STRATEGY_EXCEL

    def build(
        self,
        request: GccpAuxRunRequest,
        *,
        output_dir: Path,
    ) -> GccpAuxRunManifest:
        from main import run

        output_dir.mkdir(parents=True, exist_ok=True)
        run_id = request.run_id or new_run_id()
        stem = Path(request.bill_excel).stem[:40]
        generated_excel = output_dir / f"{run_id}_{stem}_aux_import.xlsx"
        generated_json = output_dir / f"{run_id}_{stem}_match.json"

        data = run(
            input_file=request.bill_excel,
            mode=request.mode,
            output=str(generated_excel),
            limit=request.limit,
            province=request.province or None,
            aux_provinces=request.aux_provinces or None,
            no_experience=not request.use_experience,
            sheet=request.sheet or None,
            json_output=str(generated_json),
            interactive=False,
        )

        stats = data.get("stats", {}) if isinstance(data, dict) else {}
        safety = build_safety_report(request, generated_excel)
        return GccpAuxRunManifest(
            schema_version=1,
            run_id=run_id,
            created_at=utc_now_iso(),
            policy=FORMAL_PROJECT_POLICY,
            update_strategy=self.strategy_name,
            bill_excel=normalize_path(request.bill_excel),
            generated_excel=normalize_path(generated_excel),
            generated_json=normalize_path(generated_json),
            formal_gbq7=normalize_path(request.formal_gbq7),
            aux_gbq7=normalize_path(request.aux_gbq7),
            province=request.province,
            aux_provinces=request.aux_provinces,
            mode=request.mode,
            sheet=request.sheet,
            limit=request.limit,
            use_experience=request.use_experience,
            stats=stats,
            safety=safety,
            next_steps=default_next_steps(),
            notes=request.notes,
        )


class DirectGbq7UpdaterPlaceholder:
    """Reserved adapter for future auxiliary-only GBQ7 writing."""

    strategy_name = AUX_UPDATE_STRATEGY_DIRECT_PLACEHOLDER

    def build(
        self,
        request: GccpAuxRunRequest,
        *,
        output_dir: Path,
    ) -> GccpAuxRunManifest:
        raise NotImplementedError(
            "Direct auxiliary GBQ7 writing is not implemented. "
            "Use ExcelImportPackageUpdater until the GBQ7 storage model is proven."
        )
