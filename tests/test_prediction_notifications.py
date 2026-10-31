"""Synthetic forecasts only; test outbox identity, budget and post-start safety."""
import json
from jra_engine.prediction_notifications import build_notice,enqueue,collect,acknowledge,render_notice
from jra_engine.store import Store

def example():
    return dict(id='202605041011:live',mode='live',race=dict(name='TEST ONLY',race_id='202605041011',start_at='2099-01-01T00:00:00+00:00'),
      as_of='2026-01-01T00:00:00+00:00',model_version='test',seed=42,simulations=100000,
      horses=[dict(id=str(i),number=i,name='TEST'+str(i),predicted_rank=i,win=w,top3=t,average_rank=i) for i,w,t in [(1,.5,.6),(2,.3,.9),(3,.2,.5)]])

def test_reference_bets_and_budget():
    n=build_notice(example())
    assert n['axis_number']==2 and sum(b['budget_cap'] for b in n['bets'])+n['reserve_fraction']==1
    assert not n['market_odds_verified'] and n['combination_probabilities'] is None

def test_cached_result_does_not_duplicate(tmp_path):
    a=enqueue(example(),tmp_path);b=enqueue(dict(example(),cached=True),tmp_path)
    assert a==b and len(list(tmp_path.glob('*.json')))==1

def test_poll_ack_and_post_start_bets(tmp_path):
    folder=tmp_path/'real';s=Store(folder);p=example();p['race']['start_at']='2020-01-01T00:00:00+00:00'
    s.save('predictions',p['id'],p);s.close()
    state=tmp_path/'state';r=collect(folder,state)
    assert r['pending'][0]['notice']['bets']==[]
    assert r['pending'][0]['notice']['reserve_fraction']==1
    acknowledge(state,p['id'],r['pending'][0]['snapshot_hash'])
    assert collect(folder,state)['pending']==[]

def test_writer_lock_defers_without_opening_db(tmp_path):
    (tmp_path/'claude-jobs.lock').write_text('owner')
    assert collect(tmp_path,tmp_path/'state')['status']=='writer_busy'

def test_renderer_rechecks_time_for_an_old_queued_notice():
    n=build_notice(example());n['race']['start_at']='2020-01-01T00:00:00+00:00'
    text=render_notice(n)
    assert '採用対象外' in text and 'ワイド' not in text and '3連複' not in text
