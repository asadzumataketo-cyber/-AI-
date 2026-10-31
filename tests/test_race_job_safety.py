"""Race-day corruption/locking regressions. All inputs below are synthetic."""
import json
import os
import time
import pytest
from jra_engine import jra_card as jc, race_jobs as jobs, race_scheduler as scheduler
from jra_engine.baseline import baselines

RID = '202605041011'
URL = 'https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1005202604101120261031/AB'

def result_html(date='2026年10月31日', venue='東京', race=11):
    return (f'<div class="cell date">{date} 4回{venue}10日</div><img alt="{race}レース">'
            '<tr><td class="place">1</td><td class="num">1</td>'
            '<a href="?CNAME=pw01dud102022100001/AA">馬</a></tr>').encode('cp932')

@pytest.mark.parametrize('change',[{'date':'2026年10月30日'},{'venue':'京都'},{'race':12}])
def test_wrong_result_page_rejected(change):
    with pytest.raises(ValueError):jc.parse_result(result_html(**change),RID,URL)

def test_exact_result_identity_is_checked():
    rows=jc.parse_result(result_html(),RID,URL)
    jc.validate_result_entries(rows,[dict(horse_id='2022100001',number=1)])
    for entries in [[dict(horse_id='2022100001',number=2)],
                    [dict(horse_id='2022100002',number=1)],[]]:
        with pytest.raises(ValueError):jc.validate_result_entries(rows,entries)

def test_no_winner_is_rejected():
    with pytest.raises(ValueError):
        jc.validate_result_entries([dict(horse_id='2022100001',number=1,status='dnf',finish=None)],
                                   [dict(horse_id='2022100001',number=1)])

def test_old_writer_lock_is_never_stolen(tmp_path,monkeypatch):
    path=tmp_path/'claude-jobs.lock';path.write_text('old active owner')
    os.utime(path,(time.time()-86400,)*2);monkeypatch.setattr(jobs,'LOCK',path)
    with pytest.raises(RuntimeError):
        with jobs.db_lock():pass
    assert path.read_text()=='old active owner'

def test_lock_released_on_error_and_only_by_owner(tmp_path,monkeypatch):
    path=tmp_path/'claude-jobs.lock';monkeypatch.setattr(jobs,'LOCK',path)
    with pytest.raises(ValueError):
        with jobs.db_lock():raise ValueError('work failed')
    assert not path.exists()
    with jobs.db_lock():path.write_text(json.dumps({'token':'another-owner'}))
    assert path.exists()

def test_refused_fetch_does_not_retry_or_write(tmp_path,monkeypatch):
    calls=[]
    class Response:returncode=0;stdout=b'Forbidden403'
    monkeypatch.setattr(jobs,'W',tmp_path)
    monkeypatch.setattr(jobs.subprocess,'run',lambda *a,**k:calls.append(a) or Response())
    with pytest.raises(RuntimeError,match='403'):jobs.fetch('accessD.html?CNAME=pw01dde0105202604101120261031/C6')
    assert len(calls)==1 and not (tmp_path/'raw').exists()

def test_scheduler_passes_explicit_paths(tmp_path,monkeypatch):
    calls=[];monkeypatch.setattr(scheduler,'W',tmp_path/"operator's files")
    monkeypatch.setattr(scheduler.subprocess,'run',lambda *a,**k:calls.append(a))
    from datetime import datetime,timezone
    scheduler.register(dict(cmd='result',key='race',name='race-result',at=datetime(2026,11,1,tzinfo=timezone.utc)))
    script=calls[0][0][-1]
    assert '--workdir' in script and '--data-dir' in script and 'operator\'\'s files' in script and '+09:00' in script

@pytest.mark.parametrize('odds',[float('nan'),float('inf')])
def test_nonfinite_odds_skipped(odds):
    r=baselines([dict(id='a',win=.5,odds=odds),dict(id='b',win=.5,odds=2.)],['a'])
    assert r['odds'] is None and r['odds_missing']==1


@pytest.mark.parametrize('bad_number',[False,True])
def test_result_job_validates_before_import_and_uses_packaged_baseline(tmp_path,monkeypatch,bad_number):
    from datetime import datetime,timezone
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,10,31,8,0,tzinfo=timezone.utc)
    monkeypatch.setattr(jobs,'datetime',Clock)
    monkeypatch.setattr(jobs,'W',tmp_path)
    monkeypatch.setattr(jobs,'LOCK',tmp_path/'writer.lock')
    monkeypatch.setattr(jobs,'RACES',{'race':dict(cname='pw01dde0105202604101120261031/C6',start_at='2026-10-31T06:30:00+00:00')})
    (tmp_path/'outputs').mkdir()
    body=result_html().decode('cp932')
    body+='<tr><td class="place">2</td><td class="num">2</td><a href="?CNAME=pw01dud102022100002/AA">馬</a></tr>'
    card=b'<a href="?CNAME=pw01sde1005202604101120261031/AB">result</a>'
    monkeypatch.setattr(jobs,'fetch',lambda p: (card if 'accessD' in p else body.encode('cp932'),'a'*64,'2026-10-31T08:00:00+00:00',URL))
    imports=[]
    class FakeStore:
        def __init__(self,*a):pass
        def load(self,*a):return dict(id=RID+':live',as_of='2026-10-31T01:00:00+00:00',horses=[dict(id='2022100001',win=.6,odds=2.),dict(id='2022100002',win=.4,odds=3.)])
        def records(self,*a):return [dict(race_id=RID,horse_id='2022100001',number=3 if bad_number else 1),dict(race_id=RID,horse_id='2022100002',number=2)]
        def import_bundle(self,b):imports.append(b)
        def close(self):pass
    monkeypatch.setattr(jobs,'Store',FakeStore)
    monkeypatch.setattr(jobs,'compare',lambda *a:dict(rows=[dict(horse_id='2022100001',actual_rank=1),dict(horse_id='2022100002',actual_rank=2)],metrics=dict(post_lock_withdrawals=0,top1_hit=True,top3_overlap=2,kendall_tau_b=1.,winner_predicted_ranks=[1])))
    if bad_number:
        with pytest.raises(ValueError):jobs.result('race')
        assert not imports and not (tmp_path/'outputs/result-race.json').exists()
    else:
        assert jobs.result('race')==0
        saved=json.loads((tmp_path/'outputs/result-race.json').read_text(encoding='utf-8'))
        assert saved['baselines']['odds'] is not None
        assert imports[0]['tables']['race_results'][0]['data_grade']=='confirmed-after-event'
        assert jobs.result('race')==0 and len(imports)==1
