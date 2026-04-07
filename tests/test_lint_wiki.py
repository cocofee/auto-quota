from __future__ import annotations

import json

from tools.lint_wiki import lint_wiki


def test_lint_wiki_reports_missing_source_pack_and_bad_related(tmp_path):
    project_root = tmp_path / "project"
    wiki_root = project_root / "knowledge_wiki"
    (project_root / "data" / "source_packs" / "packs").mkdir(parents=True, exist_ok=True)
    (wiki_root / "rules").mkdir(parents=True, exist_ok=True)

    good_page = wiki_root / "rules" / "rule-good.md"
    good_page.write_text(
        """---
title: "Good Rule"
type: "rule"
status: "reviewed"
province: ""
specialty: ""
source_refs:
  - "source_pack:good-pack"
source_kind: "doc"
created_at: "2026-04-07"
updated_at: "2026-04-07"
confidence: 90
owner: "tester"
tags:
  - "rule"
related: []
---

# Good Rule

Body.
""",
        encoding="utf-8",
    )
    ((project_root / "data" / "source_packs" / "packs") / "good-pack.json").write_text("{}", encoding="utf-8")

    bad_page = wiki_root / "rules" / "rule-bad.md"
    bad_page.write_text(
        """---
title: ""
type: "method"
status: "mystery"
province: ""
specialty: ""
source_refs:
  - "source_pack:missing-pack"
source_kind: "doc"
created_at: "2026-04-07"
updated_at: "2026-04-07"
confidence: 120
owner: "tester"
tags:
  - "rule"
related:
  - "missing-page"
---

# Bad Rule

See [[missing-page]].
""",
        encoding="utf-8",
    )

    (wiki_root / ".generated_manifest.json").write_text(
        json.dumps({"files": [{"relative_path": "rules/rule-missing.md"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    report = lint_wiki(wiki_root=wiki_root, project_root=project_root)

    assert report["page_count"] == 2
    assert report["error_count"] >= 3
    codes = {item["code"] for item in report["issues"]}
    assert "type_mismatch" in codes
    assert "invalid_confidence" in codes
    assert "missing_source_ref_target" in codes
    assert "missing_related_target" in codes
    assert "manifest_missing_file" in codes
