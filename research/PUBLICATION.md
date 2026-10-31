# Three-edition publication: offline preparation

`jra_engine.publication.plan(race)` returns an 08:30 JST morning edition plus T-60 and T-10 plans. An early race whose morning edition overlaps T-60 is rejected; an earlier morning time can be configured. This is a plan, not registered or executed scheduling.

`draft(race, edition, forecast, pdf_path, https_url)` prepares a LINE text message without making any network request. It requires a distinct `RACE:edition:morning|t60|t10` forecast; an old `RACE:live` snapshot cannot be relabeled as a newly calculated edition. Forecast cutoff/creation must fall in the publication window and before the current time. Draft preparation is allowed within five minutes of the target, always before race start. PDF bytes and forecast content are hashed. Local HTTP/localhost URLs are rejected; external hosting and paid access remain explicitly unverified.

`save_draft(packet, private_folder)` preserves a packet once, deduplicates repeated preparation, and never labels it sent. It contains no LINE token and sends no messages. This module does not calculate/save edition forecasts, generate prediction PDFs, upload files, check subscriptions, register jobs or call LINE. Those are follow-up work. Existing canonical forecasts, the T120..T60 canonical guard and model coefficients remain unchanged.

Validation uses synthetic fixtures for JST times, overlap, stale/future or relabeled forecasts, post-start suppression, URL rejection and idempotent drafts. Real forecasts are not simulated or overwritten by the planning command.
