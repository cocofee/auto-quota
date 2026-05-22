# cli-anything-auto-quota

CLI-Anything harness for the existing `auto-quota` repository.

It is a thin command layer over the real backend:

- `main.py` for Excel matching.
- `local_match_server.py` for HTTP service workflows.

## Install

```powershell
cd C:\Users\Administrator\Documents\trae_projects\auto-quota\agent-harness
pip install -e .
```

## Commands

```powershell
cli-anything-auto-quota --help
cli-anything-auto-quota --json status
cli-anything-auto-quota match file input.xlsx --mode search -o output.xlsx
cli-anything-auto-quota match file input.xlsx --dry-run --json
cli-anything-auto-quota server health --json
cli-anything-auto-quota quota search "镀锌钢管" --limit 5 --json
```

## Backend root

By default, the harness resolves the auto-quota repo root from its installed
source location. You can override it:

```powershell
$env:AUTO_QUOTA_ROOT = "C:\Users\Administrator\Documents\trae_projects\auto-quota"
cli-anything-auto-quota status
```

## Service commands

`server health` and `quota search` require `local_match_server.py` to be running
and may require `LOCAL_MATCH_API_KEY`.
