# Race-day jobs and integration

`python -m jra_engine.race_jobs lock TARGET --workdir PRIVATE_WORK --data-dir PRIVATE_DB`

`python -m jra_engine.race_jobs result TARGET --workdir PRIVATE_WORK --data-dir PRIVATE_DB`

`python -m jra_engine.race_jobs report TARGET --workdir PRIVATE_WORK --data-dir PRIVATE_DB`

`python -m jra_engine.race_jobs odds TARGET --workdir PRIVATE_WORK --data-dir PRIVATE_DB`

The private work folder contains `races.json` with target_key, cname and timezone-aware start_at. HTML captures, results and logs remain under that folder and are not committed. `JRA_JOB_WORKDIR` and `JRA_DATA_DIR` can supply the paths. The scheduler is preview-only by default; `python -m jra_engine.race_scheduler apply` registers tasks only when the operator explicitly invokes it. Keep the PC running. Existing operator task registrations are not modified by this PR.

Future new freezes are accepted only 1-2 hours before start; scheduled attempts are T-120/T-90/T-60. Already-frozen forecasts remain no-ops regardless of age. T-30 observes singles odds only within a +/-5 minute window, with the actual completed retrieval time, source URL, SHA-256 and horse identity/status checks. It writes an immutable `outputs/odds-TARGET-t30.json` separate from the forecast and DB. Missing odds stay missing; a missed window is logged, never backdated or replaced with final odds. This is not yet an automatic importer of ticket odds for article EV.

After comparison the result job resumes the daily PDF step. `report TARGET` retries PDF generation without refetching/reimporting results. Human `reports/DATE/notes.json` is preserved; absent notes receive a deterministic summary of completed races only. The report validates the frozen prediction hash, stored comparison and recalculated frozen-odds baselines using a read-only DB. Headless Edge (or `JRA_PDF_BROWSER`) renders the reused slide layout with a private browser profile. Generated PDFs live in `reports/DATE/generated/INPUT_HASH/` with a hash manifest, so manual slides and older versions remain untouched. A PDF failure leaves the completed result JSON available for retry. Matching inputs and PDF hashes reuse the output. Windows/Edge and actual task registration still require operator verification; a successful synthetic test does not prove a scheduled real observation.

The shared lock is `PRIVATE_DB/claude-jobs.lock`. All operators must acquire the same exclusive lock before writing. An old lock is never automatically deleted: inspect the owner/process and resolve it explicitly. DuckDB's own file locking is an additional protection. Result URLs, dates, venue/meeting/day/race number and every horse ID/number are checked before writing any result. Unsupported or missing results fail closed. A 403 is reported after one attempt without bypass or retry.

Results and a model/uniform/odds baseline comparison are saved atomically. Only odds from the frozen prediction are used. Withdrawals skip the probability comparison. Missing result links are an explicit pending state. Current-race result links use `pw01sde01`; historical links also use `pw01sde10`. Both are matched against the full race ID and date. The race number comes from the main heading image, not the first navigation image. On October 31, saved official Tokyo/Kyoto 11R captures (27 horses) passed parsing, full identity validation and result-job integration on a private backup copy, including duplicate-run suppression. Production results were imported separately by the operator's one-off adapter and independently checked read-only; this does not establish scheduled-task execution or future HTML compatibility.

This PR is stacked on `feature/weekend-local-e2e` (PR #13) and carries the existing batch/readiness/UI work plus the reviewed Claude adapters. It does not modify prediction coefficients or simulation logic, merge main or PR #16, register scheduled tasks, or include databases/raw captures/runtime outputs. The baseline helper already on main is included as a runtime dependency, with nonfinite odds treated as unavailable.

## Prediction notifications

New real live forecasts enqueue a local notice containing the frozen ranking and reference betting combinations. The packet is keyed by prediction ID plus snapshot hash; repeated cached predictions do not duplicate it. Demo and retrospective runs are excluded from the automatic chat queue. A failed queue write does not undo a saved forecast; the adapter returns a notification warning.

`python -m jra_engine.prediction_notifications poll --data-dir PRIVATE_DB --state-dir PRIVATE_NOTIFICATION_STATE` also discovers forecasts saved by other processes using a read-only DB connection. An active `claude-jobs.lock` defers the poll. Acknowledge each ID/hash only after presenting it in chat, via the `ack` command with `--prediction-id` and `--snapshot-hash`. The outbox is not a delivery transport or a guarantee that the recipient read the message. An assistant-run prediction can be reported at completion; external predictions are discovered at the next scheduled check. No additional frequent AI wakeups are installed.

The default candidates use the highest Top3 probability as an axis and two other horses in fixed win-probability order: three wide pairs at 20/15/10%, trio at 5%, and 50% reserve. This transparent reference rule is distinct from a five-role council's qualitative assessment. Joint hit probabilities and the relevant ticket odds are unknown, so adoption remains pending; these are caps, not optimized stakes. Renderers suppress betting suggestions after the start. No wager is placed.
