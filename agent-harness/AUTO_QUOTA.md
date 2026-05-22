# auto-quota CLI-Anything Harness

## Target

Source path: `C:\Users\Administrator\Documents\trae_projects\auto-quota`

This harness wraps the existing auto-quota backend. It does not reimplement quota
matching, material pricing, or experience logic. The real backend remains:

- `main.py` for one-shot Excel matching.
- `local_match_server.py` for HTTP service workflows.
- Existing `src/` modules for matching, output writing, experience, and prices.

## Backend Mapping

| Human workflow | Backend surface | Harness command |
| --- | --- | --- |
| Run auto quota on an Excel file | `python main.py ...` | `match file` |
| Check local API service | `/health` | `server health` |
| Search quota database | `/quota-search` | `quota search` |
| Inspect harness/backend state | file/env probes | `status` |

## GCCP Direction

This harness is deliberately scoped to auto-quota. GCCP integration should stay
in `D:\广联达\GCCP\7.0-X64\Bin\AutoQuotaBridge`, where the bridge can call these
commands after silently exporting from GCCP and before silently importing results.

Planned next command groups for the GCCP flow:

- `gccp match-quota`
- `gccp material-normalize`
- `gccp material-insert`
- `gccp price-lookup`

Those commands should generate GCCP-compatible import Excel files, then let the
bridge handle GCCP UI automation.
