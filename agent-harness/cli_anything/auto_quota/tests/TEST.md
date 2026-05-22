# Test Plan

## Inventory

- `test_core.py`: unit tests for root resolution, command building, and JSON output.
- `test_full_e2e.py`: subprocess tests for help, status, and dry-run command output.

## Unit Plan

- `auto_quota_backend.resolve_auto_quota_root`
  - resolves explicit root
  - rejects invalid root
- `auto_quota_backend.build_match_command`
  - includes mode, output, province, sheet, limit, no-experience, JSON output

## E2E Plan

- Run the module help command.
- Run `status --json` from the source tree.
- Run `match file --dry-run --json` against a temporary placeholder `.xlsx`.

The dry-run path verifies command composition without starting the heavy matching
pipeline.

## Test Results

Command:

```powershell
python -m pytest cli_anything\auto_quota\tests -q
```

Result:

```text
......                                                                   [100%]
6 passed in 2.71s
```

Additional smoke checks:

```powershell
cli-anything-auto-quota --help
cli-anything-auto-quota --json status
cli-anything-auto-quota --json match file README.md --dry-run --mode search
```

The real match pipeline was intentionally not run during this smoke test because
it can load indexes/models and requires a real Excel input.
