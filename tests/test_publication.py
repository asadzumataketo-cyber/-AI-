import json
import pytest
from jra_engine.publication import plan,draft,save_draft

R=dict(race_id='202605041111',name='Synthetic',start_at='2026-11-01T15:40:00+09:00')

def packet(tmp_path,**changes):
    e=plan(R)[2]
    p=dict(id=e['edition_id'],race=R,as_of='2026-11-01T15:28:00+09:00',created_at='2026-11-01T15:29:00+09:00')
    p.update(changes);pdf=tmp_path/'synthetic.pdf';pdf.write_bytes(b'%PDF-test-fixture')
    return draft(R,e,p,pdf,'https://example.com/authorized.pdf',at='2026-11-01T15:30:00+09:00')

def test_jst_three_stage_plan():
    assert [x['scheduled_at'][11:16] for x in plan(R)]==['08:30','14:40','15:30']
    with pytest.raises(ValueError):plan(dict(R,start_at='2026-11-01T09:00:00+09:00'))

def test_dry_draft_is_idempotent_and_never_marks_sent(tmp_path):
    p=packet(tmp_path);path=save_draft(p,tmp_path/'out');before=path.read_bytes()
    assert save_draft(p,tmp_path/'out')==path and path.read_bytes()==before
    assert json.loads(before)['delivery_status']=='draft_not_sent'

@pytest.mark.parametrize('changes',[{'id':R['race_id']+':live'}, {'as_of':'2026-10-31T10:00:00+09:00'}, {'created_at':'2026-11-01T16:00:00+09:00'}])
def test_no_relabeling_stale_or_future_forecast(tmp_path,changes):
    with pytest.raises(ValueError):packet(tmp_path,**changes)

def test_late_delivery_and_local_link_rejected(tmp_path):
    e=plan(R)[2];p=dict(id=e['edition_id'],race=R,as_of=e['scheduled_at'],created_at=e['scheduled_at'])
    pdf=tmp_path/'test.pdf';pdf.write_bytes(b'%PDF-test')
    with pytest.raises(ValueError,match='started'):draft(R,e,p,pdf,'https://example.com/a',at=R['start_at'])
    with pytest.raises(ValueError,match='HTTPS'):draft(R,e,p,pdf,'http://127.0.0.1/a',at=e['scheduled_at'])
