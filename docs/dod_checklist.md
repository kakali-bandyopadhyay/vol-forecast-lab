# Definition of done — Step 14 release check (17 Nov 2026)

BUILD_GUIDE Step 14: clean clone → fresh venv → install, tests, quickstart; all-commit history scan;
`git log --all --oneline -- .env` empty; tracked-file check; independent review; About + topics; MIT.

| # | Check | Result | Status |
|---|---|---|---|
| 1 | Clean clone → fresh venv → install | Clone of `c924dde` in a Linux VM, Python 3.12.15, `requirements-dev.txt` installed; `pip check` clean (185 packages). The CPU torch index was not reachable from the VM, so the PyPI build of torch 2.15.0 was used (runs on CPU) | pass (Linux) |
| 2 | Tests | 282 passed, 3 skipped (`-m "not airflow"`). 5 files not collected in the VM because tiktoken downloads `cl100k_base` on first use and the VM has no route to it: `test_collect_10c_evidence`, `test_dag_vol_pipeline`, `test_kafka`, `test_pipeline_publish`, `test_text`. Full count to be measured on the release commit with network | pass at release: `df8e020` on Windows (Python 3.14 venv) 341 passed, 3 skipped (DAG, PySpark, OpenSearch not running); CI green |
| 3 | Quickstart | `scripts/smoke_run.py` on the synthetic fixture: audit passed, panel 2,769 rows, exit 0. `scripts/make_charts.py` rebuilds 4 charts; `tests/test_make_charts.py` 2 passed | pass |
| 4 | `.env` never committed | `git log --all --oneline -- .env` printed nothing | pass |
| 5 | All-commit secret scan | `git log --all -p` searched for OpenAI, AWS access-key, LangSmith, GitHub-token and private-key patterns: 0 hits | pass |
| 6 | Tracked-file check | 242 tracked files, 12 MB; no `.env`, `*.tfstate`, `*.tfvars`, `*.db`, `mlruns/`, raw or snapshot data | pass |
| 7 | Licence boundary (ADR 0003) | Found failing: four tracked per-date series (`results/forecasts.parquet`, `mlp_forecasts.parquet`, `har_inputs.parquet`, `losses.parquet`). Fixed 17 Nov 2026 by author decision: removed from all history with `git filter-repo`, git-ignored, hashes remapped (`docs/history_rewrite.md`); `git log --all` on the four paths prints nothing | pass after rewrite |
| 8 | Independent review | Not yet run | open |
| 9 | GitHub About + topics | Not yet set | open |
| 10 | MIT licence | `LICENSE` added | pass |

## Item 7 — resolution

Author chose option 1 (remove from history, then public). Tests: `test_drift` skips when
`losses.parquet` is absent. Pre-rewrite backup: `.git/prerewrite/` (bundle of all refs, the four files,
commit map) — local only, never pushed.

Minor, same review: `results/agent/agent_eval.parquet` quotes forecast values for the handful of
dates in the 15 agent questions (point values, not a series). Kept.
