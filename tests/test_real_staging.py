from jra_engine.store import Store,now
from jra_engine.pipeline import features
import pytest

def test_registration_is_not_a_confirmed_entry(tmp_path):
    s=Store(tmp_path/'real');t=now();r=dict(fact_id='observed-name',target_key='future',horse_name='実名参照用',number=None,rating=None,stage='registered',source='https://www.jra.go.jp/',available_at=t,retrieved_at=t,updated_at=t,availability_evidence='閲覧時に確認')
    b=dict(schema_version=1,dataset='real',tables={'race_registrations':[r]})
    assert s.import_bundle(b)==1;assert s.import_bundle(b)==0
    assert s.records('race_registrations',t)[0]['rating'] is None
    assert s.records('race_entries',t)==[]
    with pytest.raises(ValueError):features(s,'future',t)
    s.close()
