---
name: "cli-anything-auto-quota"
description: "Use auto-quota through a structured CLI for Excel matching, service health checks, and quota searches."
---

# cli-anything-auto-quota

Use this skill when you need to call the local auto-quota backend from an agent
workflow.

## Common Commands

```powershell
cli-anything-auto-quota --json status
cli-anything-auto-quota match file input.xlsx --mode search -o output.xlsx
cli-anything-auto-quota match file input.xlsx --dry-run --json
cli-anything-auto-quota server health --json
cli-anything-auto-quota quota search "镀锌钢管" --limit 5 --json
```

## Notes

- `match file` wraps the real `main.py`.
- `server health` and `quota search` call the real `local_match_server.py` API.
- Use `--json` for machine-readable output.
- Use `AUTO_QUOTA_ROOT` or `--root` if the repository path is not auto-detected.
