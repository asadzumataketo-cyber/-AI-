# Supplemental analysis for prediction articles

The internal system supports a future prediction-article workflow: full ranking, ticket combinations and explicitly defined upset events. Articles are drafts; this command does not publish, place bets or call any AI/API. The model remains uncalibrated. Positive model EV does not establish a real betting edge.

```powershell
$env:PYTHONPATH='PATH_TO_CHECKOUT/src'
python -m jra_engine.article_analysis --data-dir PRIVATE_DATA --prediction-id 202605041111:live --output-dir PRIVATE_OUTPUTS
```

The command opens DuckDB read-only, respects `claude-jobs.lock`, validates the stored prediction hash, and reconstructs the baseline's original random draws from frozen ratings. It compares every horse's first/second/third/Top3 probability and average rank exactly with the original before using any joint counts. Only this baseline/NumPy version is supported. It never runs the prediction pipeline, recalculates abilities from newer history, changes coefficients or writes to the production DB. These supplemental draws are analysis, not a new prestart prediction. Cache identity includes analysis version, full snapshot hash and quote contents. The JSON result and its hash are immutable; Markdown is rerendered so a cached prestart article cannot expose ticket advice after start.

## Tickets, EV and reference allocation

The existing highest-Top3 axis / two highest-win partners rule is reused. Three wide pairs and one unordered trio are measured jointly from the same top-three outcomes, never by multiplying marginal probabilities. The renderer displays model hit probability, break-even decimal odds (1/p), and optional observed odds/EV. Decimal odds include stake repayment: expected net per stake = p * odds - 1. Use the lower end of a wide odds range. A zero observed hit count does not imply an impossible event; the Wilson sampling interval is retained. MC intervals omit model uncertainty.

An optional `--quotes PRIVATE_QUOTES.json` supplies a JSON list. Every quote requires `type` (`ワイド` or `3連複`), distinct `numbers`, matching `race_id`, `odds_low` (optional `odds_high`), timezone-aware `observed_at` and `retrieved_at`, `source`, and a 64-digit `source_sha256`. Observation <= retrieval <= analysis time; retrieval must be before race start. Invalid, duplicate, nonfinite and future/post-start observations fail closed. This validates the input metadata, not the truth or licensing of the supplied source; the operator must preserve the actual lawful source capture. Missing quotes stay missing. Final/post-start odds cannot be passed off as prestart quotes.

The 20/15/10/5% caps with 50% reserve are the earlier transparent reference rule, not an optimized allocation. Any-hit/all-miss probability accounts for overlapping tickets. When every quote exists, portfolio EV and probability of profit/loss use joint outcomes and the odds lower bounds. No automatic adoption is made.

## Upset definitions

Using only complete singles odds already present in the frozen forecast, report these model-based events: (1) winner outside the three lowest-odds horses, (2) none of those three finishing in the top three, (3) at least two other horses finishing in the top three. They are not high-payout probabilities or calibrated upset forecasts. Missing singles odds or a tie across the third/fourth boundary yields an explicit unavailable status, not an invented ranking. Frozen popularity may differ from current popularity; later market snapshots need a separate future extension.

## Validation and boundaries

Synthetic tests cover joint probabilities against symmetric-runner combinatorics, correlated portfolio payouts, unchanged frozen marginals, unknown model versions, odds provenance/timing, missing/tied popularity, post-start suppression, read-only loading and cache reuse/tampering. Private real-data acceptance uses all four existing fixed snapshots (100,000 draws each); DB SHA-256 and the 1,085 history rows remain unchanged. Real ticket quotes are not present, so actual-market EV and allocation optimality remain unvalidated. Private originals and generated reports are excluded from Git.
