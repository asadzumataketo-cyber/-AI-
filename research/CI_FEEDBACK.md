# Phase 1: pytest feedback (Issue #15)

`.github/workflows/keiba-feedback-loop.yml` runs Python 3.11, `pip install -e '.[test]'`,
and `pytest -q` with a JUnit report. It needs no AI API key, race input, local DB,
Supabase, Windows PC or paid API. It does not acquire race data or modify source code.
Only tests committed to the selected revision run. PR #13's local E2E and private
research results are not present on main until that PR is separately reviewed and merged.

## Execution and status

- Schedule: every two hours at :20, JST even hours (00:20, 02:20, ... 22:20).
  The UTC cron is `20 1-23/2 * * *`. GitHub may delay scheduled runs.
- The workflow also runs on pull requests targeting main, with a read-only token.
- After this PR is reviewed and merged, open **Actions → Keiba feedback loop →
  Run workflow → main** for a manual `workflow_dispatch` run. CLI equivalent:
  `gh workflow run keiba-feedback-loop.yml --ref main`.
- Scheduled execution requires the workflow on the default branch. The initial
  manual dispatch may also be unavailable before default-branch registration.
  A successful PR run does not prove a scheduled or manual run happened.
- Confirm at least two scheduled executions after merge to finish the two-hour
  cycle acceptance criterion. This PR does not merge itself or change main.

Actions and the PR check display success/failure. Each run's Summary includes
counts and blockers. Download its `keiba-feedback-<run_id>-<attempt>` artifact
(7-day retention): `feedback-latest.json`, `install.log`, `pytest.log`, `pytest.xml`.
Logs are capped at 64 KiB each. There is no artifact upload of the working tree,
environment, credentials, local CSV/JSON race input or database.

The JSON includes run ID/attempt, commit SHA, UTC start/end, status, counts,
failed test IDs (up to 30), summary, blockers and process exit codes. Zero tests,
dependency failure, collection/test errors, missing/invalid JUnit and subprocess
timeouts produce a failure. A final `always()` step also reports Python setup
failures or interrupted execution when the runner remains available. A runner
loss, hard cancellation or checkout failure can prevent artifacts; Actions itself
remains the record of that infrastructure failure. Artifacts are never committed.

## Issue feedback and permissions

Default permission is `contents: read`, and checkout does not persist credentials.
Only a separate main-branch schedule/dispatch job gets `issues: write` and
`actions: read` to download the report and comment on Issue #15. That job does
not check out or execute repository code. Pull requests cannot invoke it.
It posts only when the latest bot report's status or commit SHA differs, preventing
identical two-hour comments. Test logs, test data and secrets are not in comments.
Notion/council updates and Codex auto-fixes are outside this phase.

## Local verification

```powershell
python -m pip install -e ".[test]"
python -m pytest -q
python scripts/ci_feedback.py init
python scripts/ci_feedback.py run
python scripts/ci_feedback.py finalize
```

Local JSON uses `local` for GitHub-specific fields, not invented Actions IDs.
The regression tests cover success, individual failure, zero tests, invalid JUnit,
install failure, Python setup failure and timeout without accessing external APIs.

References: [GitHub workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows),
[workflow permissions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions).
