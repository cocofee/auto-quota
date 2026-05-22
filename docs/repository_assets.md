# Repository Asset Boundary

This repository should contain source code, small configuration files, tests, and durable documentation. It should not contain generated runtime output, local indexes, trained model checkpoints, benchmark snapshots, extracted price PDFs, quota databases, or Python bytecode.

Large or reproducible assets must live outside Git:

- model checkpoints and merged model directories: `models/`
- imported quota databases and vector indexes: `db/provinces/`, `db/chroma/`, `db/chroma_cache/`
- local common database materializations: `db/common/*.db`
- source quota spreadsheets and price information PDFs: `data/quota_data/`, `data/pdf_info_price/`
- local project pricing files: `2.计价/`
- local experience databases and imported reference stores: `data/experience/`, `data/reference/`, `data/*.db*`
- generated benchmark, audit, task, and run output: `output/`, `reports/agent_state/`, `reports/attribution/`
- temporary test/runtime folders: `tmp/`, `logs/`, `test_artifacts/`, `.pytest_tmp*/`, `pytest_tmp_*/`

If a workflow needs one of these assets, restore it from local backup or external object storage before running the workflow. Code should fail with a clear missing-asset message or provide a rebuild/import command instead of expecting these assets to be versioned by Git.

Before committing, run:

```powershell
python tools/repo_hygiene_check.py
```

The check fails if generated paths, Python bytecode, or Git-tracked files larger than the configured limit are present.
