"""Input gates, immutable live snapshots and registration/roster separation."""
from datetime import datetime,timedelta,timezone
import pytest
from jra_engine.store import Store,now,digest
from jra_engine.readiness import audit_live_race,guarded_predict,preparation_status


@pytest.fixture
def ready_store(tmp_path):
    s=Store(tmp_path/'real');t=datetime.now(timezone.utc)
    meta=dict(source='https://www.jra.go.jp/keiba/card.pdf',source_sha256='a'*64,
              available_at=(t-timedelta(hours=1)).isoformat(),retrieved_at=(t-timedelta(minutes=50)).isoformat(),updated_at=now(),availability_evidence='test fixture; no real race or performance claim')
    rid='202605041011';race=dict(meta,race_id=rid,name='検証用',venue='東京',surface='ダート',distance=1600,
                              start_at=(t+timedelta(days=2)).isoformat(),declared_entries=3,confirmed=True,target_key='test-target')
    entries=[];past=[]
    for n in range(1,4):
        hid='202010000'+str(n)
        entries.append(dict(meta,entry_id=rid+':'+hid,race_id=rid,horse_id=hid,number=n,name='検証用'+str(n),status='active',frame=n,jockey='検証用騎手',carried_weight=57,official_confirmed=True,identity_verified=True,identity_source='https://www.jra.go.jp/horse/'+hid))
        past.append(dict(meta,entry_id='202505010101:'+hid,race_id='202505010101',horse_id=hid,start_at=(t-timedelta(days=30)).isoformat(),venue='東京',surface='ダート',distance=1600,field_size=10,finish=n,status='finished'))
    s.import_bundle(dict(schema_version=1,dataset='real',tables=dict(races=[race],race_entries=entries,horse_performances=past)))
    yield s,race,entries,past
    s.close()


def append_version(s,table,row,**changes):
    copy=dict(row,**changes,available_at=now(),retrieved_at=now(),updated_at=now())
    s.import_bundle(dict(schema_version=1,dataset='real',tables={table:[copy]}))


def test_ready_live_e2e_and_cached_lock(ready_store):
    s,race,entries,_=ready_store
    gate=audit_live_race(s,race['race_id']);assert gate['ready'] and gate['warnings']
    snapshot=guarded_predict(s,race['race_id'],now(),42)
    assert snapshot['simulations']==100000 and snapshot['mode']=='live'
    original=digest(s.load('predictions',snapshot['id']))
    append_version(s,'race_entries',entries[0],status='scratched')
    cached=guarded_predict(s,race['race_id'],now(),99)
    assert cached['cached'] and cached['seed']==42
    assert digest(s.load('predictions',snapshot['id']))==original


@pytest.mark.parametrize('change',[{'confirmed':False},{'source':'https://example.com/card'},{'source_sha256':''}])
def test_unconfirmed_or_unproven_card_cannot_freeze(ready_store,change):
    s,race,_,_=ready_store;append_version(s,'races',race,**change)
    with pytest.raises(ValueError,match='準備未完了'):guarded_predict(s,race['race_id'],now())
    assert s.db.execute('SELECT count(*) FROM predictions').fetchone()[0]==0


def test_missing_identity_and_rider_are_visible(ready_store):
    s,race,entries,_=ready_store
    append_version(s,'race_entries',entries[0],identity_verified=False,jockey=None,frame=None)
    gate=audit_live_race(s,race['race_id'])
    assert not gate['ready']
    assert {'馬IDの照合が未記録','枠順が未確認','騎手が未確認'}<=set(gate['horses'][0]['errors'])


def test_future_retrieval_cannot_be_backdated(ready_store):
    s,race,entries,_=ready_store;t=now()
    row=dict(entries[0],available_at=t,retrieved_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),updated_at=t)
    s.import_bundle(dict(schema_version=1,dataset='real',tables={'race_entries':[row]}))
    assert not audit_live_race(s,race['race_id'])['ready']


def test_future_and_target_history_are_not_usable(ready_store):
    s,race,_,past=ready_store
    first=past[0]
    append_version(s,'horse_performances',first,start_at=race['start_at'])
    gate=audit_live_race(s,race['race_id'])
    assert not gate['ready'] and gate['horses'][0]['past_runs']==0


def test_registration_dashboard_reads_normalized_history(ready_store):
    s,race,entries,past=ready_store
    meta={k:race[k] for k in ['source','source_sha256','available_at','retrieved_at','updated_at','availability_evidence']}
    target=dict(meta,target_key='test-target',name='検証用',date='2026-10-31',official_race_id=None)
    regs=[dict(meta,fact_id='reg-'+e['horse_id'],target_key='test-target',horse_id=e['horse_id'],horse_name=e['name']) for e in entries]
    s.import_bundle(dict(schema_version=1,dataset='real',tables={'race_targets':[target],'race_registrations':regs}))
    report=preparation_status(s)
    assert report['total_history_records']==3 and report['unresolved_ids']==0
    assert report['targets'][0]['race_id']==race['race_id']
    assert report['targets'][0]['missing_to_three']==6
    assert s.db.execute('SELECT count(*) FROM predictions').fetchone()[0]==0


def test_registration_alone_does_not_become_confirmed(ready_store):
    s,race,entries,_=ready_store
    meta={k:race[k] for k in ['source','available_at','retrieved_at','updated_at','availability_evidence']}
    s.import_bundle(dict(schema_version=1,dataset='real',tables={'race_targets':[dict(meta,target_key='unconfirmed',name='登録のみ',date='2026-11-01')]}))
    report=preparation_status(s)
    target=next(t for t in report['targets'] if t['target_key']=='unconfirmed')
    assert not target['ready'] and target['race_id'] is None


def test_older_fit_history_with_future_retrieval_is_blocked(ready_store):
    s,race,_,past=ready_store;t=datetime.now(timezone.utc)
    for day in [10,20,40]:
        rid='2025050101'+str(day)
        row=dict(past[0],entry_id=rid+':'+past[0]['horse_id'],race_id=rid,start_at=(t-timedelta(days=day)).isoformat())
        if day==40:row['retrieved_at']=(t+timedelta(days=1)).isoformat()
        s.import_bundle(dict(schema_version=1,dataset='real',tables={'horse_performances':[row]}))
    gate=audit_live_race(s,race['race_id'])
    assert not gate['ready'] and any('retrieved_at' in e for e in gate['errors'])


def test_scratched_runner_does_not_need_rider_or_history(ready_store):
    s,race,entries,_=ready_store
    append_version(s,'races',race,declared_entries=4)
    withdrawn=dict(entries[0],entry_id=race['race_id']+':2020100004',horse_id='2020100004',number=4,status='scratched',frame=None,jockey=None,carried_weight=None)
    s.import_bundle(dict(schema_version=1,dataset='real',tables={'race_entries':[withdrawn]}))
    assert audit_live_race(s,race['race_id'])['ready']
