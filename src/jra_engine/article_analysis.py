"""Supplemental analysis of immutable forecasts. No model changes, DB writes or LLM."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

import duckdb
import numpy as np
from .prediction_notifications import build_notice
from .store import digest

VERSION = 'article-analysis-1.0.0'
MODEL = 'jra-weekend-1.0.0-baseline'


def clock(value):
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('Timezone required')
    return t.astimezone(timezone.utc)


def estimate(count, total):
    """Wilson interval describes simulation sampling, not real-world calibration."""
    p = count / total
    z = 1.959963984540054
    den = 1 + z*z/total
    center = (p + z*z/(2*total)) / den
    half = z*math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / den
    return dict(count=int(count), probability=p, mc_se=math.sqrt(p*(1-p)/total),
                mc_interval95=[max(0., center-half), min(1., center+half)])


def ticket_key(kind, numbers):
    if kind not in ('ワイド', '3連複'):
        raise ValueError('Supported tickets: ワイド, 3連複')
    expected = 2 if kind == 'ワイド' else 3
    if len(numbers) != expected or any(type(n) is not int or not 1 <= n <= 18 for n in numbers) or len(set(numbers)) != expected:
        raise ValueError('Invalid ticket numbers')
    return kind + ':' + '-'.join(map(str, sorted(numbers)))


def replay(snapshot):
    """Reconstruct original draws only to measure joint events; reject any drift."""
    if snapshot['model_version'] != MODEL or snapshot['numpy_version'] != np.__version__:
        raise ValueError('Unsupported model/NumPy version; exact replay not established')
    horses = sorted(snapshot['horses'], key=lambda h: h['id'])
    n = len(horses)
    runs, batch = snapshot['simulations'], snapshot['batch_size']
    if not 3 <= n <= 18 or len({h['id'] for h in horses}) != n or len({h['number'] for h in horses}) != n:
        raise ValueError('Invalid runners')
    if type(runs) is not int or type(batch) is not int or min(runs, batch) < 1:
        raise ValueError('Invalid simulation size')
    ratings = np.array([h['rating'] for h in horses], dtype=float)
    std = np.array([h['std'] for h in horses], dtype=float)
    sensitivity = np.array([h['pace_sensitivity'] for h in horses], dtype=float)
    if not all(np.isfinite(a).all() for a in (ratings, std, sensitivity)) or (std <= 0).any():
        raise ValueError('Invalid ratings')
    numbers = np.array([h['number'] for h in horses])
    rng = np.random.default_rng(snapshot['seed'])
    podium = np.zeros((n, 3), dtype=np.int64)
    rank_sum = np.zeros(n, dtype=np.int64)
    triples = Counter()
    for start in range(0, runs, batch):
        size = min(batch, runs-start)
        pace = rng.normal(0, 1., (size, 1))
        scores = ratings[None, :] + pace*sensitivity[None, :] + rng.normal(size=(size, n))*std
        order = np.argsort(-scores, axis=1, kind='stable')
        for position in range(n):
            rank_sum += np.bincount(order[:, position], minlength=n)*(position+1)
        for position in range(3):
            podium[:, position] += np.bincount(order[:, position], minlength=n)
        triples.update(map(tuple, numbers[order[:, :3]].tolist()))
    for i, h in enumerate(horses):
        for j, field in enumerate(('win', 'second', 'third')):
            if podium[i, j]/runs != h[field]:
                raise ValueError('Frozen marginal mismatch: ' + field)
        if podium[i].sum()/runs != h['top3'] or rank_sum[i]/runs != h['average_rank']:
            raise ValueError('Frozen rank/Top3 mismatch')
    if any(podium[:, j].sum() != runs for j in range(3)) or sum(triples.values()) != runs:
        raise ValueError('Probability invariant failed')
    return triples


def quotes_by_ticket(quotes, snapshot, decision_at):
    out = {}
    for q in quotes:
        key = ticket_key(q['type'], q['numbers'])
        if key in out or q.get('race_id') != snapshot['race']['race_id']:
            raise ValueError('Duplicate quote or wrong race')
        if not set(q['numbers']) <= {h['number'] for h in snapshot['horses']}:
            raise ValueError('Quote contains unknown runner')
        lo, hi = float(q['odds_low']), float(q.get('odds_high', q['odds_low']))
        if not (math.isfinite(lo) and math.isfinite(hi) and 1 <= lo <= hi):
            raise ValueError('Invalid decimal payout odds')
        if not q.get('source') or not re.fullmatch('[0-9a-fA-F]{64}', q.get('source_sha256', '')):
            raise ValueError('Quote source and SHA-256 required')
        observed, retrieved = clock(q['observed_at']), clock(q['retrieved_at'])
        if not observed <= retrieved <= clock(decision_at) or retrieved >= clock(snapshot['race']['start_at']):
            raise ValueError('Future or post-start quote rejected')
        out[key] = dict(q, odds_low=lo, odds_high=hi)
    return out


def upset_events(snapshot, triples):
    horses = snapshot['horses']
    odds = [h.get('odds') for h in horses]
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) or x < 1 for x in odds):
        return dict(status='missing_frozen_single_odds', events=[])
    ranked = sorted(horses, key=lambda h: (h['odds'], h['number']))
    if len(ranked) > 3 and ranked[2]['odds'] == ranked[3]['odds']:
        return dict(status='odds_tie_at_top3_boundary', events=[])
    top = {h['number'] for h in ranked[:3]}
    events = [
        ('単勝人気上位3頭以外が優勝', lambda t: t[0] not in top),
        ('単勝人気上位3頭が全頭3着外', lambda t: not top.intersection(t)),
        ('単勝人気上位3頭以外が2頭以上3着以内', lambda t: sum(x not in top for x in t) >= 2)]
    return dict(status='model_based_unvalidated', market_basis='single odds stored in frozen forecast',
                market_top3_numbers=sorted(top), events=[dict(name=name, **estimate(sum(c for t,c in triples.items() if test(t)), snapshot['simulations'])) for name,test in events])


def analyze(snapshot, quotes=(), decision_at=None):
    decision_at = decision_at or datetime.now(timezone.utc).isoformat()
    clock(decision_at)
    if clock(snapshot['as_of']) >= clock(snapshot['race']['start_at']) or clock(snapshot['created_at']) >= clock(snapshot['race']['start_at']):
        raise ValueError('Not a prestart frozen forecast')
    market = quotes_by_ticket(quotes, snapshot, decision_at)
    triples = replay(snapshot)
    notice = build_notice(snapshot)
    tickets = []
    for b in notice['bets']:
        key = ticket_key(b['type'], b['numbers'])
        hit = sum(c for t,c in triples.items() if set(b['numbers']) <= set(t))
        e = estimate(hit, snapshot['simulations'])
        q = market.get(key)
        p = e['probability']
        tickets.append(dict(b, key=key, **e, break_even_decimal_odds=1/p if p else None,
                            quote=q, expected_net_per_stake_low=p*q['odds_low']-1 if q else None,
                            status='model_EV_only_unvalidated' if q else 'missing_ticket_odds_adoption_pending'))
    matched = lambda t,b: set(b['numbers']) <= set(t)
    missed = sum(c for t,c in triples.items() if not any(matched(t,b) for b in tickets))
    portfolio = dict(reference_budget_fraction=sum(t['budget_cap'] for t in tickets),
                     reserve_fraction=notice['reserve_fraction'], any_ticket_hit=estimate(snapshot['simulations']-missed, snapshot['simulations']),
                     all_tickets_miss=estimate(missed, snapshot['simulations']),
                     expected_net_budget_fraction_low=None, probability_profit_low=None, probability_loss_low=None,
                     allocation_status='reference_caps_not_optimized_adoption_pending')
    if all(t['quote'] for t in tickets):
        outcomes = [(sum(b['budget_cap']*(b['quote']['odds_low'] if matched(t,b) else 0) for b in tickets)-portfolio['reference_budget_fraction'], c) for t,c in triples.items()]
        portfolio['expected_net_budget_fraction_low'] = sum(v*c for v,c in outcomes)/snapshot['simulations']
        portfolio['probability_profit_low'] = sum(c for v,c in outcomes if v > 1e-12)/snapshot['simulations']
        portfolio['probability_loss_low'] = sum(c for v,c in outcomes if v < -1e-12)/snapshot['simulations']
    return dict(analysis_version=VERSION, generated_at=decision_at, snapshot_hash=digest(snapshot),
                prediction_id=snapshot['id'], race=snapshot['race'], as_of=snapshot['as_of'],
                model_version=snapshot['model_version'], runs=snapshot['simulations'], seed=snapshot['seed'],
                replay_marginals_exact=True, ranking=notice['ranking'], tickets=tickets,
                portfolio=portfolio, upset=upset_events(snapshot, triples),
                missing_ticket_quotes=sum(t['quote'] is None for t in tickets),
                warning='未校正モデルの追加分析。MC区間はモデル誤差を含まない。オッズ下限の期待値は利益優位の実証ではなく、配分は未最適化。')


def render(report, at=None):
    started = clock(at or datetime.now(timezone.utc).isoformat()) >= clock(report['race']['start_at'])
    rows = [f"# {report['race']['name']}｜分析原稿（未公開）", '',
            f"固定時点：{report['as_of']}／{report['model_version']}／seed {report['seed']}／{report['runs']:,}回", '',
            report['warning'], '', '発走後の研究用分析。買い目・配分の案内は抑止。' if started else '発走前の参考分析。買い目は採否保留。', '',
            '|予想順位|馬番・馬名|勝率|3着以内率|', '|---:|---|---:|---:|']
    rows += [f"|{h['predicted_rank']}|{h['number']} {h['name']}|{h['win']:.2%}|{h['top3']:.2%}|" for h in report['ranking']]
    if not started:
        rows += ['', '|買い目候補|モデル同時的中率|損益分岐オッズ|取得オッズ下限|モデル期待損益/投資額|参考配分上限・未最適化|', '|---|---:|---:|---:|---:|---:|']
        for t in report['tickets']:
            fair = f"{t['break_even_decimal_odds']:.2f}" if t['break_even_decimal_odds'] else '算出不能'
            odds = f"{t['quote']['odds_low']:.2f}" if t['quote'] else '欠損'
            ev = f"{t['expected_net_per_stake_low']:+.2%}" if t['quote'] else '未算出'
            rows.append(f"|{t['key']}|{t['probability']:.2%}|{fair}|{odds}|{ev}|{t['budget_cap']:.0%}|")
        rows += ['', f"対象券種オッズ欠損：{report['missing_ticket_quotes']}/4。留保50%。配分の最適性は未検証。"]
        rows += [f"- {t['key']} のオッズ出典：{t['quote']['source']}／観測 {t['quote']['observed_at']}／取得 {t['quote']['retrieved_at']}／SHA-256 {t['quote']['source_sha256']}" for t in report['tickets'] if t['quote']]
    rows += ['', '荒れ指標（高配当確率とは異なる）：'+report['upset']['status'],
             '人気の基準は固定時点の単勝オッズ。現在の人気とは限らない。']
    rows += [f"- {e['name']}：モデル内 {e['probability']:.2%}" for e in report['upset']['events']]
    rows += ['', '欠損：'+'／'.join(sorted({m for h in report['ranking'] for m in h.get('missing') or []})),
             '', '固定予測SHA-256：'+report['snapshot_hash']]
    return '\n'.join(rows)+'\n'


def load_snapshot(data_dir, identity):
    folder = Path(data_dir)
    if (folder/'claude-jobs.lock').exists():
        raise RuntimeError('Writer busy; analysis deferred')
    with duckdb.connect(str(folder/'research.duckdb'), read_only=True) as db:
        row = db.execute('SELECT payload,hash FROM predictions WHERE id=?', [identity]).fetchone()
    if not row:
        raise ValueError('Prediction not found')
    p = json.loads(row[0])
    if digest(p) != row[1]:
        raise ValueError('Frozen snapshot hash mismatch')
    return p


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--prediction-id', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--quotes', type=Path)
    args = parser.parse_args()
    p = load_snapshot(args.data_dir, args.prediction_id)
    quotes = json.loads(args.quotes.read_text(encoding='utf-8')) if args.quotes else []
    # Revalidate quote timing on cache reads too. Never use a cached future quote.
    at = datetime.now(timezone.utc).isoformat()
    quotes_by_ticket(quotes, p, at)
    key = digest([VERSION, digest(p), sorted(quotes, key=lambda q: ticket_key(q['type'], q['numbers']))])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir/(key+'.json')
    cached = path.exists()
    if cached:
        envelope = json.loads(path.read_text(encoding='utf-8'))
        report = envelope['analysis']
        if digest(report) != envelope['sha256'] or report['snapshot_hash'] != digest(p):
            raise ValueError('Cached analysis hash mismatch')
    else:
        report = analyze(p, quotes, at)
        with path.open('x', encoding='utf-8') as f:
            json.dump(dict(analysis=report, sha256=digest(report)), f, ensure_ascii=False, indent=2, allow_nan=False)
    article = path.with_suffix('.md')
    article.write_text(render(report, at), encoding='utf-8')
    print(json.dumps(dict(path=str(path), article=str(article), cached=cached, missing_quotes=report['missing_ticket_quotes']), ensure_ascii=False))


if __name__ == '__main__':
    main()
