"""Synthetic report/odds fixtures only. Never run a real browser or network request."""
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
import duckdb
import pytest
from jra_engine import race_reports as rr, report_slides as slides, race_jobs as jobs, race_scheduler as scheduler
from jra_engine.baseline import baselines
from jra_engine.store import digest,encoded


@pytest.fixture
def reporting(tmp_path):
    work=tmp_path/'work';(work/'outputs').mkdir(parents=True);db=tmp_path/'db.duckdb'
    key='2026-10-31-tokyo-11';rid='202605041011'
    horses=[dict(id=str(i),number=i,name=f'Synthetic{i}',predicted_rank=i,win=p,odds=i+1.) for i,p in enumerate([.5,.3,.2],1)]
    p=dict(id=rid+':live',race=dict(race_id=rid),horses=horses)
    report=dict(prediction_id=p['id'],race_id=rid,rows=[dict(horse_id=h['id'],name=h['name'],number=h['number'],predicted_rank=h['predicted_rank'],actual_rank=h['predicted_rank'],status='finished') for h in horses],
                metrics=dict(top1_hit=True,top3_overlap=3,winner_predicted_ranks=[1],kendall_tau_b=1.,post_lock_withdrawals=0),result_hash='synthetic')
    with duckdb.connect(str(db)) as c:
        for table in ['predictions','backtest_results']:c.execute(f'create table {table}(id varchar,payload varchar,hash varchar)')
        c.execute('insert into predictions values (?,?,?)',[p['id'],encoded(p),digest(p)])
        c.execute('insert into backtest_results values (?,?,?)',[p['id']+':synthetic',encoded(report),digest(report)])
    report['baselines']=baselines(horses,['1'])
    out=work/'outputs'/f'result-{key}.json';out.write_text(json.dumps(report),encoding='utf-8')
    race=dict(target_key=key,name='Synthetic race',start_at='2026-10-31T15:30:00+09:00')
    return work,db,race,out


def fake_browser(args,**kwargs):
    dest=next(x.split('=',1)[1] for x in args if x.startswith('--print-to-pdf='))
    Path(dest).write_bytes(b'%PDF-1.7\nsynthetic-browser-output')


def test_result_to_report_cache_and_manual_outputs_preserved(reporting,monkeypatch):
    work,db,race,out=reporting;folder=work/'reports/2026-10-31';folder.mkdir(parents=True)
    manual=folder/'slides.pdf';manual.write_bytes(b'manual report')
    calls=[]
    def browser(args,**kwargs):calls.append(args);fake_browser(args,**kwargs)
    monkeypatch.setattr(slides.subprocess,'run',browser)
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    first=rr.ensure(work,db,race,[race]);assert first['status']=='completed' and not first['cached']
    second=rr.ensure(work,db,race,[race]);assert second['cached'] and len(calls)==1
    assert '--headless=new' in calls[0] and any(x.startswith('--user-data-dir=') for x in calls[0])
    assert manual.read_bytes()==b'manual report' and hashlib.sha256(db.read_bytes()).hexdigest()==before
    assert Path(first['pdf']).read_bytes().startswith(b'%PDF-')


def test_failed_pdf_is_resumed_without_result_reimport(reporting,monkeypatch):
    work,db,race,out=reporting
    def fail(*args,**kwargs):raise RuntimeError('browser unavailable')
    monkeypatch.setattr(slides.subprocess,'run',fail)
    with pytest.raises(RuntimeError,match='browser unavailable'):rr.ensure(work,db,race,[race])
    assert out.exists()
    monkeypatch.setattr(slides.subprocess,'run',fake_browser)
    assert rr.ensure(work,db,race,[race])['status']=='completed'


@pytest.mark.parametrize('which',['core','baseline'])
def test_modified_comparison_or_market_baseline_rejected(reporting,which):
    work,db,race,out=reporting;r=json.loads(out.read_text())
    if which=='core':r['metrics']['top1_hit']=False
    else:r['baselines']['model']['log_loss']=99
    out.write_text(json.dumps(r))
    with pytest.raises(ValueError):rr.ensure(work,db,race,[race])


def test_human_notes_not_overwritten_when_second_race_pending(reporting):
    work,db,race,out=reporting;notes=rr.make_notes([race],{race['target_key']:json.loads(out.read_text())},'2026-10-31')
    notes.pop('generated_by');notes['races'].append(dict(target_key='2026-10-31-kyoto-11'))
    folder=work/'reports/2026-10-31';folder.mkdir(parents=True);path=folder/'notes.json'
    path.write_text(json.dumps(notes));before=path.read_bytes()
    assert rr.ensure(work,db,race,[race])['status']=='pending' and path.read_bytes()==before


def test_existing_result_invokes_report_without_fetching(tmp_path,monkeypatch):
    (tmp_path/'outputs').mkdir();(tmp_path/'outputs/result-key.json').write_text('{}')
    monkeypatch.setattr(jobs,'W',tmp_path);monkeypatch.setattr(jobs,'race_info',lambda k:{})
    monkeypatch.setattr(jobs,'fetch',lambda *a:pytest.fail('Do not refetch results'))
    calls=[];monkeypatch.setattr(jobs,'build_report',lambda k:calls.append(k) or 0)
    assert jobs.result('key')==0 and calls==['key']


def odds_setup(tmp_path,monkeypatch,minute=0):
    race=dict(target_key='2026-10-31-tokyo-11',cname='pw01dde0105202604101120261031/C6',start_at='2026-10-31T15:30:00+09:00')
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,10,31,6,minute,tzinfo=timezone.utc)
    monkeypatch.setattr(jobs,'datetime',Clock);monkeypatch.setattr(jobs,'W',tmp_path)
    monkeypatch.setattr(jobs,'race_info',lambda k:race)
    from jra_engine import article_analysis
    p=dict(id='202605041011:live',horses=[dict(id='2022100001',number=1),dict(id='2022100002',number=2)])
    monkeypatch.setattr(article_analysis,'load_snapshot',lambda *a:p)
    card=dict(race_id='202605041011',start_at=race['start_at'],entries=[dict(horse_id=h['id'],number=h['number'],status='active',odds=2.5 if h['number']==1 else None) for h in p['horses']])
    monkeypatch.setattr(jobs.jc,'parse_card',lambda *a:card)
    calls=[]
    monkeypatch.setattr(jobs,'fetch',lambda path:(calls.append(path) or b'raw','a'*64,'2026-10-31T06:00:00+00:00','https://official-test-only'))
    return race,card,calls


def test_t30_odds_recorded_once_separately_with_real_capture_metadata(tmp_path,monkeypatch):
    race,card,calls=odds_setup(tmp_path,monkeypatch)
    assert jobs.odds('key')==0
    path=tmp_path/'outputs/odds-key-t30.json';obj=json.loads(path.read_text(encoding='utf-8'))
    assert digest(obj['observation'])==obj['sha256']
    assert obj['observation']['missing']==1 and obj['observation']['retrieved_at']=='2026-10-31T06:00:00+00:00'
    assert jobs.odds('key')==0 and len(calls)==1


def test_missed_t30_window_does_not_fetch_or_backdate(tmp_path,monkeypatch):
    race,card,calls=odds_setup(tmp_path,monkeypatch,minute=15)
    assert jobs.odds('key')==3 and not calls


@pytest.mark.parametrize('change',['runner','nan','late_response'])
def test_wrong_odds_capture_rejected_before_write(tmp_path,monkeypatch,change):
    race,card,calls=odds_setup(tmp_path,monkeypatch)
    if change=='runner':card['entries'][0]['number']=3
    elif change=='nan':card['entries'][0]['odds']=float('nan')
    else:monkeypatch.setattr(jobs,'fetch',lambda *a:(b'raw','a'*64,'2026-10-31T06:10:00+00:00','source'))
    with pytest.raises(ValueError):jobs.odds('key')
    assert not (tmp_path/'outputs/odds-key-t30.json').exists()


@pytest.mark.parametrize('already_locked',[False,True])
def test_new_forecast_window_preserves_existing_locks(tmp_path,monkeypatch,already_locked):
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,10,31,2,0,tzinfo=timezone.utc)
    with duckdb.connect(str(tmp_path/'research.duckdb')) as c:
        c.execute('create table predictions(id varchar)')
        if already_locked:c.execute("insert into predictions values ('202605041011:live')")
    monkeypatch.setattr(jobs,'DB',tmp_path);monkeypatch.setattr(jobs,'W',tmp_path);monkeypatch.setattr(jobs,'datetime',Clock)
    monkeypatch.setattr(jobs,'race_info',lambda k:dict(cname='pw01dde0105202604101120261031/C6',start_at='2026-10-31T15:30:00+09:00'))
    monkeypatch.setattr(jobs,'fetch',lambda *a:pytest.fail('Early fetch is forbidden'))
    assert jobs.lock('key')==(0 if already_locked else 3)
