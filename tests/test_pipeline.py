from datetime import datetime,timedelta,timezone
from copy import deepcopy
import pytest
from jra_engine.store import Store,now,digest
from jra_engine.local import demo_bundle
from jra_engine.pipeline import predict,compare,features

@pytest.fixture
def db(tmp_path):
    s=Store(tmp_path/'demo');s.import_bundle(demo_bundle());yield s;s.close()

def results(db,order=None):
    bundle=demo_bundle();meta=bundle['tables']['races'][0].copy();start=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    # Fixture shifts the race start in the sealed snapshot for result evaluation only;
    # a retrospective prediction below uses a historical race built explicitly.
    entries=db.records('race_entries',now());r=[]
    for i,e in enumerate(entries):
        row=dict(e,finish=(order or list(range(1,7)))[i],status='finished',final=True)
        r.append(row)
    return r

def historical(db):
    b=demo_bundle();t=datetime.now(timezone.utc);asof=(t-timedelta(days=2)).isoformat()
    for records in b['tables'].values():
        for r in records:
            r['available_at']=(t-timedelta(days=3)).isoformat()
            if 'race_id' in r and r['race_id']=='DEMO-ONLY' and 'start_at' in r:r['start_at']=(t-timedelta(days=1)).isoformat()
    # Different source publication versions are appended, not updated.
    db.import_bundle(b)
    return asof

def test_end_to_end(db):
    asof=historical(db);p=predict(db,'DEMO-ONLY',asof,mode='retrospective')
    assert p['simulations']==100000 and p['batch_size']==10000
    for field in ['win','second','third']:assert sum(h[field] for h in p['horses'])==pytest.approx(1)
    assert sum(h['top3'] for h in p['horses'])==pytest.approx(3)
    entries=db.records('race_entries',asof);rank={h['id']:h['predicted_rank'] for h in p['horses']}
    rows=[dict(e,available_at=now(),retrieved_at=now(),updated_at=now(),finish=rank[e['horse_id']],status='finished',final=True) for e in entries]
    db.import_bundle(dict(schema_version=1,dataset='demo',tables={'race_results':rows}))
    report=compare(db,p['id']);assert report['metrics']['kendall_tau_b']==1;assert report['metrics']['top1_hit'];assert report['metrics']['top3_overlap']==3
    assert report['metrics']['exact_top3'];assert report['metrics']['log_loss']>=0
    assert predict(db,'DEMO-ONLY',asof,mode='retrospective')['cached']

def test_idempotent_and_rollback(db):
    r=db.records('races',now())[0]
    b=dict(schema_version=1,dataset='demo',tables={'races':[deepcopy(r)]})
    assert db.import_bundle(b)==0
    b['tables']['races'][0]['name']='modified'
    with pytest.raises(ValueError):db.import_bundle(b)
    assert db.records('races',now())[0]['name']==r['name']

def test_future_and_target_results_do_not_leak(db):
    asof=now();_,before,_=features(db,'DEMO-ONLY',asof)
    b=demo_bundle();h=b['tables']['horse_performances'][0];h['race_id']='DEMO-ONLY';h['entry_id']='DEMO-ONLY:'+h['horse_id'];h['finish']=1
    b['tables']={'horse_performances':[h]};db.import_bundle(b)
    assert features(db,'DEMO-ONLY',asof)[1]==before
    future=dict(db.records('race_entries',asof)[0]);future['available_at']=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat();future['retrieved_at']=future['available_at'];future['name']='FUTURE'
    db.import_bundle(dict(schema_version=1,dataset='demo',tables={'race_entries':[future]}))
    assert features(db,'DEMO-ONLY',asof)[1]==before

def test_live_lock(db):
    p=predict(db,'DEMO-ONLY',now());q=predict(db,'DEMO-ONLY',now(),seed=99)
    assert q['cached'] and q['seed']==p['seed']
    bad=deepcopy(p);bad['seed']=99
    with pytest.raises(ValueError):db.save('predictions',p['id'],bad)

def test_no_backdated_live(db):
    asof=historical(db)
    with pytest.raises(ValueError):predict(db,'DEMO-ONLY',asof)

def test_dataset_separation(db):
    b=demo_bundle();b['dataset']='real'
    with pytest.raises(ValueError):db.import_bundle(b)

def test_missing_history_is_visible(db):
    asof=historical(db)
    # All histories in this fixture are published before as_of; original future versions are ignored.
    race,hs,used=features(db,'DEMO-ONLY',asof)
    assert all(x['available_at']<=asof for x in used)
    assert all('missing' in h for h in hs)

def test_incomplete_results_rejected(db):
    p=predict(db,'DEMO-ONLY',now())
    with pytest.raises(ValueError):compare(db,p['id'])
