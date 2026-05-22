"""Structured sleeve query helpers."""

import re


def build_structured_sleeve_query(
    *,
    name: str,
    full_text: str,
    dn: int | str | None = None,
    specialty: str | None = None,
) -> str | None:
    """Return a structured sleeve query when a dedicated rule is available."""
    text = f"{name or ''} {full_text or ''}"
    if "套管" not in text:
        return None

    if any(keyword in text for keyword in ("止水钢板", "止水翼环")):
        parts = ["刚性防水套管制作安装"]
        resolved_dn = dn or _extract_dn(text)
        if resolved_dn:
            parts.append(f"DN{resolved_dn}")
        return " ".join(parts)

    return None


def _extract_dn(text: str) -> str:
    match = re.search(r"\bDN\s*(\d+)\b", text or "", flags=re.IGNORECASE)
    return match.group(1) if match else ""
