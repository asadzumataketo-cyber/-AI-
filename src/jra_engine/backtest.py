"""Score already-saved forecasts against official results: model vs all-horses-equal vs odds-only (Issue #6).

Never predicts, fetches or writes to a database. A race enters only if its forecast predates the start and
every runner has a result. C = reconstructed; U = live but independently unverified.
Local mode/timestamps/hashes cannot certify A. No attestation verifier is implemented yet,
so this entry point never admits A to the formal validation verdict.
"""
import argparse
import json
from datetime import datetime
import math
from pathlib import Path
import numpy as np
from .baseline import baselines

EXPLORATORY_RACES = {'202608040711'}  # 秋華賞 was used to try out inputs, so it is not independent evidence
LONGSHOT_ODDS = 20
REFUNDED = ('scratched', 'excluded')
METRICS = ('top1_hit', 'top3_overlap', 'exact_top3', 'winner_rank', 'tau_b', 'trio_hit',
           'average_order_top1_hit', 'average_order_top3_overlap', 'average_order_tau_b', 'odds_top1_hit', 'odds_top3_overlap')


def tau_b(predicted, actual):
    # shortcut: assumes predicted ranks are unique (they are by construction); ties are counted on the actual side only
    c = d = ties = 0
    for i in range(len(predicted)):
        for j in range(i + 1, len(predicted)):
            v = (predicted[i] - predicted[j]) * (actual[i] - actual[j])
            c += v > 0; d += v < 0; ties += v == 0
    denom = math.sqrt((c + d) * (c + d + ties))
    return (c - d) / denom if denom else None


def _order_metrics(order, finish, prefix=''):
    """order: runner ids, best first; finish: id -> finishing place or None."""
    runners = [h for h in order if finish[h]]
    ranks = {h: i + 1 for i, h in enumerate(order)}
    top = {h for h in runners if finish[h] <= 3}
    return {prefix + 'top1_hit': int(finish[order[0]] == 1), prefix + 'top3_overlap': sum(h in top for h in order[:3]),
            **({} if prefix == 'odds_' else {prefix + 'tau_b': tau_b([ranks[h] for h in runners], [finish[h] for h in runners])})}


def score_race(race):
    horses, finish, status = race['horses'], race['finish'], race.get('status', {})
    ids = [h['id'] for h in horses]
    order = [h['id'] for h in sorted(horses, key=lambda h: h['predicted_rank'])]
    winners = [h for h in ids if finish[h] == 1]
    top = sorted((h for h in ids if finish[h] and finish[h] <= 3), key=lambda h: finish[h])
    s = _order_metrics(order, finish)
    s['winner_rank'] = min(order.index(w) + 1 for w in winners)
    s['exact_top3'] = int(top == order[:3]) if [finish[h] for h in top] == [1, 2, 3] else None
    if all(h.get('average_rank') is not None for h in horses):
        s.update(_order_metrics([h['id'] for h in sorted(horses, key=lambda h: (h['average_rank'], h['predicted_rank']))], finish, 'average_order_'))
    trifecta = race.get('trifecta_top') or []
    s['trio_hit'] = int(set(trifecta) == set(top)) if trifecta else None

    odds = {h['id']: h.get('odds') for h in horses}
    known = all(isinstance(v, (int, float)) and v >= 1 for v in odds.values())
    # shortcut: uses the odds saved at forecast time, not the final payout; replace once official payouts are stored
    s['win_bet_return'] = (1. if status.get(order[0]) in REFUNDED else odds[order[0]] if order[0] in winners else 0.) if known else None
    s['favourite_in_top3'] = int(min(ids, key=lambda h: odds[h]) in top) if known else None
    s['longshot_in_top3'] = int(any(odds[h] >= LONGSHOT_ODDS for h in top)) if known else None
    if known:
        s.update(_order_metrics(sorted(ids, key=lambda h: odds[h]), finish, 'odds_'))

    s['log_loss'] = s['brier'] = s['top3_brier'] = None
    s['points'] = dict(win=[], top3=[])
    # A withdrawal after the lock changes who could win, so probabilities are not scored (same rule as compare()).
    if not any(status.get(h) in REFUNDED for h in ids):
        b = baselines(horses, winners)
        s['log_loss'] = {k: b[k]['log_loss'] if b[k] else None for k in ('model', 'uniform', 'odds')}
        s['brier'] = {k: b[k]['brier'] if b[k] else None for k in ('model', 'uniform', 'odds')}
        s['top3_brier'] = dict(model=float(np.mean([(h['top3'] - (h['id'] in top)) ** 2 for h in horses])),
                               uniform=float(np.mean([(3 / len(ids) - (h in top)) ** 2 for h in ids])))
        s['points'] = dict(win=[(h['win'], int(h['id'] in winners)) for h in horses], top3=[(h['top3'], int(h['id'] in top)) for h in horses])
    return s


def _skip_reason(race):
    if datetime.fromisoformat(race['as_of']) >= datetime.fromisoformat(race['start_at']):
        return 'as_of_not_before_start'
    finish, status = race['finish'], race.get('status', {})
    if any(finish.get(h['id']) is None and status.get(h['id'], 'finished') == 'finished' for h in race['horses']):
        return 'results_incomplete'
    return None if any(finish.get(h['id']) == 1 for h in race['horses']) else 'no_winner'


def _mean(values):
    values = [v for v in values if v is not None]
    return dict(mean=float(np.mean(values)) if values else None, n=len(values))


def _calibration(points):
    bins = {}
    for point in points:
        bins.setdefault(min(int(point[0] * 10), 9), []).append(point)
    return [dict(range=f'{i / 10:.1f}-{(i + 1) / 10:.1f}', n=len(hit), predicted=float(np.mean([p[0] for p in hit])), observed=float(np.mean([p[1] for p in hit])))
            for i, hit in sorted(bins.items())]


def _group(rows):
    scored = [r for r in rows if r['log_loss']]
    missed = [r for r in rows if r['favourite_in_top3'] == 0]
    long = [r for r in rows if r['longshot_in_top3'] == 1]
    known = [r for r in rows if r['favourite_in_top3'] is not None]
    sub = lambda rs: dict(races=len(rs), model_log_loss=_mean([r['log_loss']['model'] for r in rs if r['log_loss']]), top3_overlap=_mean([r['top3_overlap'] for r in rs]))
    return dict(races=len(rows), days=len({r['day'] for r in rows}), **{m: _mean([r.get(m) for r in rows]) for m in METRICS},
                log_loss={k: _mean([r['log_loss'][k] for r in scored]) for k in ('model', 'uniform', 'odds')},
                brier={k: _mean([r['brier'][k] for r in scored]) for k in ('model', 'uniform', 'odds')},
                top3_brier={k: _mean([r['top3_brier'][k] for r in scored]) for k in ('model', 'uniform')},
                win_bet_return=_mean([r['win_bet_return'] for r in rows]),
                favourite_missed_share=len(missed) / len(known) if known else None, favourite_missed_top3=sub(missed), longshot_in_top3=sub(long),
                calibration=dict(win=_calibration([p for r in rows for p in r['points']['win']]), top3=_calibration([p for r in rows for p in r['points']['top3']])))


def _paired(rows, metric, baseline, draws=2000):
    """Model minus baseline per race day; days are resampled because races on one day share a track and field."""
    by_day = {}
    for r in rows:
        by_day.setdefault(r['day'], []).append(r[metric]['model'] - r[metric][baseline])
    days = np.array([np.mean(v) for v in by_day.values()])
    picks = np.random.default_rng(0).integers(0, len(days), (draws, len(days)))
    means = days[picks].mean(axis=1)
    return dict(difference=float(days.mean()), ci95=[float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))])


def _verdict(rows, min_races, min_days):
    """Pass only if the model beats both baselines on both scores, beyond chance, on locked races in the validation period."""
    usable = [r for r in rows if r['log_loss'] and r['log_loss']['odds'] is not None]
    days = len({r['day'] for r in usable})
    if len(usable) < min_races or days < min_days:  # few days make the day-based interval falsely narrow
        return dict(status='insufficient_races', races=len(usable), required=min_races, days=days, required_days=min_days)
    tests = {f'{m}_vs_{b}': _paired(usable, m, b) for m in ('log_loss', 'brier') for b in ('uniform', 'odds')}
    return dict(status='passed' if all(t['ci95'][1] < 0 for t in tests.values()) else 'failed', races=len(usable), required=min_races, days=days, required_days=min_days, tests=tests)


def evaluate(races, validation_from=None, min_validation_races=50, min_validation_days=10):
    if len({r['race_id'] for r in races}) != len(races):
        raise ValueError('duplicate race')
    rows, skipped = [], []
    for race in sorted(races, key=lambda r: r['start_at']):
        reason = _skip_reason(race)
        if reason:
            skipped.append(dict(race_id=race['race_id'], reason=reason)); continue
        # Do not trust caller-supplied A labels (including direct evaluate() callers).
        # Independent timestamp evidence must bind both the forecast and its input SHA;
        # a local label, URL, hash or self-declared verification flag is not that proof.
        grade = 'C' if race['grade'] == 'C' else 'U'
        rows.append(dict(score_race(race), race_id=race['race_id'], grade=grade,
                         grade_reason='reconstruction' if grade == 'C' else 'independent_prestart_attestation_not_verified',
                         day=race['start_at'][:10], prediction_id=race.get('prediction_id')))
    groups = {}
    for grade in sorted({r['grade'] for r in rows}):
        graded = [r for r in rows if r['grade'] == grade]
        groups[f'all/{grade}'] = _group(graded)
        if validation_from:
            for name, part in (('development', [r for r in graded if r['day'] < validation_from]), ('validation', [r for r in graded if r['day'] >= validation_from])):
                if part:
                    groups[f'{name}/{grade}'] = _group(part)
    verdict = dict(status='no_validation_period')
    if validation_from:
        verdict = _verdict([r for r in rows if r['grade'] == 'A' and r['day'] >= validation_from], min_validation_races, min_validation_days)
    return dict(races_scored=len(rows), skipped=skipped, validation_from=validation_from, groups=groups, verdict=verdict,
                races=[{k: v for k, v in r.items() if k != 'points'} for r in rows])


def load_races(database):
    """One forecast per race (live wins, else the newest reconstruction) plus latest results. The DB is opened read-only."""
    import duckdb  # only this reader needs it
    db = duckdb.connect(str(database), read_only=True)
    try:
        chosen = {}
        for created, payload in db.execute('SELECT created_at, payload FROM predictions ORDER BY created_at').fetchall():
            p = json.loads(payload)
            rid = p['race']['race_id']
            if rid not in EXPLORATORY_RACES and (p['mode'] == 'live' or chosen.get(rid, {}).get('mode') != 'live'):
                chosen[rid] = p
        results = {}
        for payload, in db.execute('''SELECT payload FROM race_results
                QUALIFY row_number() OVER(PARTITION BY record_key ORDER BY available_at DESC)=1''').fetchall():
            r = json.loads(payload)
            results.setdefault(r['race_id'], {})[r['horse_id']] = r
    finally:
        db.close()
    races = []
    for rid, p in chosen.items():
        start = datetime.fromisoformat(p['race']['start_at'])
        # Same acceptance as compare(): only final results published after the start; a non-finisher has no place.
        got = {h: r for h, r in results.get(rid, {}).items() if r.get('final') and datetime.fromisoformat(r['available_at']) > start}
        top = p.get('trifecta_top10') or []
        races.append(dict(race_id=rid, prediction_id=p['id'], grade='U' if p['mode'] == 'live' else 'C', start_at=p['race']['start_at'], as_of=p['as_of'],
                          horses=p['horses'], trifecta_top=top[0]['horses'] if top else [],
                          finish={h: r.get('finish') if r.get('status') == 'finished' else None for h, r in got.items()}, status={h: r.get('status') for h, r in got.items()}))
    return races


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('--validation-from', help='YYYY-MM-DD; earlier races are development only')
    parser.add_argument('--min-validation-races', type=int, default=50)
    parser.add_argument('--min-validation-days', type=int, default=10)
    parser.add_argument('--output', required=True, help='keep outside the public repository')
    args = parser.parse_args()
    report = evaluate(load_races(args.database), args.validation_from, args.min_validation_races, args.min_validation_days)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(dict(races_scored=report['races_scored'], skipped=report['skipped'], verdict=report['verdict']), ensure_ascii=False))


if __name__ == '__main__':
    main()
