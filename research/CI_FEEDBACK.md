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

## Post-merge acceptance checklist (human approval required first)

This is a prepared procedure, not a record of completed main/scheduled runs.
Do not merge, enable auto-merge or change the workflow to bypass this gate.

1. Confirm the owner has approved and merged PR #16. Record the resulting main
   SHA, merge time and the current Issue #15 bot-comment IDs as the baseline.
   Stop if PR #16 is still open. Commands (GitHub CLI must be authenticated):

   ```powershell
   gh pr view 16 --repo asadzumataketo-cyber/-AI- --json state,mergedAt,mergeCommit,headRefOid
   gh api repos/asadzumataketo-cyber/-AI-/branches/main --jq .commit.sha
   gh api repos/asadzumataketo-cyber/-AI-/issues/15/comments --paginate
   ```

2. Dispatch on main and identify the NEW run using its event, main SHA and
   creation time (do not reuse a previous branch run). Run commands sequentially:

   ```powershell
   gh workflow run keiba-feedback-loop.yml --repo asadzumataketo-cyber/-AI- --ref main
   gh run list --repo asadzumataketo-cyber/-AI- --workflow keiba-feedback-loop.yml --branch main --event workflow_dispatch --limit 5 --json databaseId,headSha,status,conclusion,url,createdAt
   gh run watch <RUN_ID> --repo asadzumataketo-cyber/-AI- --exit-status
   gh run download <RUN_ID> --repo asadzumataketo-cyber/-AI- --name keiba-feedback-<RUN_ID>-1 --dir outputs/main-<RUN_ID>
   ```

   Record both jobs' conclusions. Verify JSON run ID/SHA/event, UTC start/end,
   nonzero test count, exit codes, empty blockers and all four artifact files.
   Confirm `issue-feedback` ran rather than skipped and that the matching bot
   notification exists on Issue #15. It may already have been created by a
   scheduled run; inspect its linked run instead of assuming every dispatch
   creates a comment. Failure in the comment job must not be reported as success
   just because pytest passed. Never upload local race inputs to debug it.

3. Without changing main, record the latest matching bot notification and run
   the same dispatch again. Compare the actual JSON SHA/status of both runs.
   If BOTH are identical, require no additional comment with the marker
   `<!-- keiba-feedback-loop:v1 -->` by `github-actions[bot]` and require the log
   `State and commit unchanged; no duplicate comment.`. Count only this bot's
   marked comments, not other discussion. A changed SHA/status or intervening
   run makes the duplicate test inconclusive; repeat with a stable baseline.
   Do not introduce an intentional failure into main. A genuine success/failure
   transition, if observed, must create a new state notification.

4. Collect at least TWO actual schedule-event runs on main. Manual/PR runs and
   reruns are not substitutes for two different scheduled run IDs. Record IDs,
   SHA, event, UTC times, job outcomes, artifacts and notification decisions:

   ```powershell
   gh run list --repo asadzumataketo-cyber/-AI- --workflow keiba-feedback-loop.yml --branch main --event schedule --limit 10 --json databaseId,headSha,event,createdAt,status,conclusion,url
   ```

   Expected schedule: JST even hours at :20, with possible GitHub delays. Query
   Actions even when Issue comments do not change; no new comment does not mean
   no execution. If either scheduled run fails, record the failing job/test and
   blockers rather than calling the rollout complete. These checks remain pending
   until the owner's merge; this document does not execute them.

Pre-merge evidence: commit `e38b69f53e03751fb1b42e083b955dba738df66f` passed
11 tests in both [PR run 40709068972](https://github.com/asadzumataketo-cyber/-AI-/actions/runs/40709068972)
and [manual run 40709083371](https://github.com/asadzumataketo-cyber/-AI-/actions/runs/40709083371).
Artifact creation was confirmed; the manual artifact's JSON/log/JUnit contents
were inspected. Both branch runs correctly skipped Issue notifications. Node.js
20 deprecation warnings are a reviewed non-blocker; pinned-action upgrades are
deferred to a separate tested change.

References: [GitHub workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows),
[workflow permissions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions).
