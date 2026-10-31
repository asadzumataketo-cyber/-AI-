"""Synthetic fixtures only; real source captures and DBs are never committed."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import sys

import duckdb
import numpy as np
import pytest
from jra_engine import article_analysis as a
from jra_engine.simulation import simulate
from jra_engine.store import digest, encoded

AT = '2026-10-31T12:00:00+00:00'


def snapshot(deterministic=False, runs=12000):
    horses = [dict(id=str(i), number=i, name=f'Test{i}', rating=100-i*10 if deterministic else 0,
                   std=.001 if deterministic else 1., pace_sensitivity=0., odds=i+1., missing=['test missing']) for i in range(1,9)]
    result = simulate(horses, runs=runs, batch_size=1000, seed=42, pace_sigma=1.)
    by = {h['id']: h for h in horses}
    ranked = sorted([dict(h, **by[h['id']]) for h in result['horses']], key=lambda h: (-h['win'], h['number']))
    for i,h in enumerate(ranked):h['predicted_rank'] = i+1
    return dict(id='202605041111:live', race=dict(race_id='202605041111', name='Synthetic race', start_at='2026-11-01T06:40:00+00:00'),
                as_of='2026-10-30T00:00:00+00:00', created_at='2026-10-30T00:00:01+00:00',
                model_version=a.MODEL, numpy_version=np.__version__, simulations=runs,
                batch_size=1000, seed=42, horses=ranked, trifecta_top10=result['trifecta_top10'])


def quote(p, kind, numbers, odds=2.):
    return dict(type=kind, numbers=numbers, race_id=p['race']['race_id'], odds_low=odds,
                observed_at='2026-10-31T10:00:00+00:00', retrieved_at='2026-10-31T10:01:00+00:00',
                source='synthetic-test-not-real', source_sha256='a'*64)


def test_joint_probability_is_not_product_of_marginals():
    p = snapshot()
    original = digest(p)
    report = a.analyze(p, decision_at=AT)
    assert digest(p) == original and report['replay_marginals_exact']
    by = {h['number']:h for h in p['horses']}
    for ticket in report['tickets']:
        expected = 3/28 if ticket['type'] == 'ワイド' else 1/56
        assert ticket['probability'] == pytest.approx(expected, abs=.018)
        assert ticket['mc_interval95'][0] <= ticket['probability'] <= ticket['mc_interval95'][1]
        assert ticket['break_even_decimal_odds'] == 1/ticket['probability']
    pair = report['tickets'][0]
    product = math.prod(by[n]['top3'] for n in pair['numbers'])
    assert abs(pair['probability']-product) > .015
    portfolio = report['portfolio']
    assert portfolio['any_ticket_hit']['probability']+portfolio['all_tickets_miss']['probability'] == 1
    assert portfolio['expected_net_budget_fraction_low'] is None
    assert report['missing_ticket_quotes'] == 4


def test_correlated_portfolio_payoff_uses_joint_outcomes_and_lower_odds():
    p = snapshot(deterministic=True, runs=100)
    quotes = [quote(p,'ワイド',[1,2],2),quote(p,'ワイド',[1,3],3),quote(p,'ワイド',[2,3],4),quote(p,'3連複',[1,2,3],10)]
    for q in quotes:q['odds_high'] = q['odds_low']+5
    report = a.analyze(p,quotes,AT)
    assert all(t['probability'] == 1 for t in report['tickets'])
    assert report['portfolio']['expected_net_budget_fraction_low'] == pytest.approx(1.25)
    assert report['portfolio']['probability_profit_low'] == 1
    assert report['portfolio']['all_tickets_miss']['probability'] == 0
    assert report['portfolio']['allocation_status'] == 'reference_caps_not_optimized_adoption_pending'


@pytest.mark.parametrize('field,value', [('win',.123456),('top3',.123456),('average_rank',99.)])
def test_changed_frozen_probabilities_fail_closed(field,value):
    p = snapshot(runs=100)
    p['horses'][0][field] = value
    with pytest.raises(ValueError, match='mismatch'):a.analyze(p, decision_at=AT)


@pytest.mark.parametrize('field,value', [('model_version','other'),('numpy_version','0.0')])
def test_unknown_replay_version_rejected(field,value):
    p = snapshot(runs=100);p[field] = value
    with pytest.raises(ValueError,match='version'):a.analyze(p,decision_at=AT)


@pytest.mark.parametrize('updates', [
    {'observed_at':'2026-11-02T00:00:00+00:00'},
    {'retrieved_at':'2026-11-01T07:00:00+00:00'},
    {'retrieved_at':'2026-10-31T12:01:00+00:00'},
    {'odds_low':float('nan')}, {'odds_low':float('inf')}, {'odds_low':.5},
    {'source_sha256':'invented'}, {'source':''}, {'race_id':'202608041111'},
    {'numbers':[1,1]}, {'numbers':[1,9]}, {'observed_at':'2026-10-31T10:00:00'}])
def test_invalid_future_or_unproven_quotes_rejected(updates):
    p = snapshot(runs=100);q = quote(p,'ワイド',[1,2]);q.update(updates)
    with pytest.raises(ValueError):a.analyze(p,[q],AT)


def test_duplicate_quotes_rejected():
    p = snapshot(runs=100);q = quote(p,'ワイド',[1,2])
    with pytest.raises(ValueError,match='Duplicate'):a.analyze(p,[q,deepcopy(q)],AT)


def test_upset_metrics_have_explicit_events_and_missing_tied_market_is_not_filled():
    p = snapshot(deterministic=True,runs=100)
    r = a.analyze(p,decision_at=AT)
    assert r['upset']['market_top3_numbers'] == [1,2,3]
    assert [e['probability'] for e in r['upset']['events']] == [0,0,0]
    p['horses'][0]['odds'] = None
    assert a.analyze(p,decision_at=AT)['upset'] == dict(status='missing_frozen_single_odds',events=[])
    for h in p['horses']:h['odds'] = 2.
    assert a.analyze(p,decision_at=AT)['upset']['status'] == 'odds_tie_at_top3_boundary'


def test_article_suppresses_bets_after_start():
    report = a.analyze(snapshot(runs=100),decision_at=AT)
    assert '買い目候補' in a.render(report,AT)
    after = a.render(report,'2026-11-01T07:00:00+00:00')
    assert '買い目候補' not in after and 'ワイド:' not in after and '発走後の研究用' in after


def test_readonly_cli_cache_is_reused_and_tampering_detected(tmp_path,monkeypatch,capsys):
    p = snapshot(runs=100)
    dbdir = tmp_path/'real';dbdir.mkdir()
    path = dbdir/'research.duckdb'
    with duckdb.connect(str(path)) as db:
        db.execute('create table predictions(id varchar,payload varchar,hash varchar)')
        db.execute('insert into predictions values (?,?,?)',[p['id'],encoded(p),digest(p)])
    import hashlib
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(sys,'argv',['analysis','--data-dir',str(dbdir),'--prediction-id',p['id'],'--output-dir',str(tmp_path/'out')])
    a.main();first = json.loads(capsys.readouterr().out)
    output = __import__('pathlib').Path(first['path']);payload = output.read_bytes()
    monkeypatch.setattr(a,'replay',lambda *args:pytest.fail('Cache should avoid replay'))
    a.main();assert json.loads(capsys.readouterr().out)['cached']
    assert output.read_bytes() == payload and hashlib.sha256(path.read_bytes()).hexdigest() == before
    envelope = json.loads(payload);envelope['analysis']['seed'] = -1
    output.write_text(json.dumps(envelope),encoding='utf-8')
    with pytest.raises(ValueError,match='Cached analysis hash'):a.main()
    (dbdir/'claude-jobs.lock').write_text('another writer')
    with pytest.raises(RuntimeError,match='Writer busy'):a.load_snapshot(dbdir,p['id'])
