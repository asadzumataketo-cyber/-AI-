from copy import deepcopy
from datetime import datetime,timedelta,timezone
import pytest
from jra_engine.batch import grade,compare_order,run_batch,aggregate
from jra_engine.local import demo_bundle
from jra_engine.store import Store,now
from jra_engine.pipeline import predict

def test_grade_requires_independent_evidence():
    r=dict(data_grade='B-reconstructed',available_at=now(),availability_evidence='reconstructed')
    assert grade([r],now())=='C-reconstruction'
    r.update(data_grade='A-verified',availability_verified=True,published_at='2020-01-01T00:00:00+00:00')
    assert grade([r],now())=='verified-as-of'

def test_both_orderings_do_not_modify_snapshot(tmp_path):
    s=Store(tmp_path/'demo');b=demo_bundle();t=datetime.now(timezone.utc)
    for records in b['tables'].values():
        for r in records:r['available_at']=(t-timedelta(days=3)).isoformat()
    b['tables']['races'][0]['start_at']=(t-timedelta(days=1)).isoformat();s.import_bundle(b)
    cutoff=(t-timedelta(days=2)).isoformat();p=predict(s,'DEMO-ONLY',cutoff,mode='retrospective');old=deepcopy(s.load('predictions',p['id']))
    entries=s.records('race_entries',cutoff)
    results=[dict(e,status='finished',finish=i+1,final=True,available_at=now(),retrieved_at=now(),updated_at=now()) for i,e in enumerate(entries)]
    s.import_bundle(dict(schema_version=1,dataset='demo',tables={'race_results':results}))
    win=compare_order(s,p,'win');avg=compare_order(s,p,'average')
    assert win['metrics']['log_loss']==avg['metrics']['log_loss']
    assert win['metrics']['brier_score']==avg['metrics']['brier_score']
    assert s.load('predictions',p['id'])==old
    batch=run_batch(s,dict(races=[dict(race_id='DEMO-ONLY',as_of=cutoff)]))
    assert batch['completed']==1 and set(batch['summary'])=={'C-reconstruction/win','C-reconstruction/average'}
    assert run_batch(s,dict(races=[dict(race_id='MISSING',as_of=cutoff)]))['completed']==0
    with pytest.raises(ValueError):run_batch(s,dict(races=[dict(race_id='202608040711',as_of=cutoff)]))
    s.close()

def test_aggregation_separates_grades_and_missing():
    row=lambda g,v:dict(grade=g,rankings=dict(win=dict(metrics=dict(top1_hit=True,top3_overlap=2,kendall_tau_b=None,log_loss=v,brier_score=.5))))
    r=aggregate([row('verified-as-of',1),row('C-reconstruction',5)])
    assert r['verified-as-of/win']['metrics']['log_loss']['mean']==1
    assert r['C-reconstruction/win']['metrics']['log_loss']['mean']==5
    assert r['verified-as-of/win']['metrics']['kendall_tau_b']['n']==0
