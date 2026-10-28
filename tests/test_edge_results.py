from datetime import datetime,timedelta,timezone
from jra_engine.local import demo_bundle
from jra_engine.store import Store,now
from jra_engine.pipeline import predict,compare
import pytest

@pytest.mark.parametrize('exception',['scratched','dnf','tie','reverse'])
def test_special_results(tmp_path,exception):
    s=Store(tmp_path/'demo');b=demo_bundle();t=datetime.now(timezone.utc)
    asof=(t-timedelta(days=2)).isoformat()
    for records in b['tables'].values():
        for r in records:
            r['available_at']=(t-timedelta(days=3)).isoformat()
    b['tables']['races'][0]['start_at']=(t-timedelta(days=1)).isoformat()
    s.import_bundle(b);p=predict(s,'DEMO-ONLY',asof,mode='retrospective')
    entries={e['horse_id']:e for e in s.records('race_entries',asof)};rows=[]
    for h in p['horses']:
        rank=h['predicted_rank'];row=dict(entries[h['id']],finish=7-rank if exception=='reverse' else rank,status='finished',final=True,available_at=now(),retrieved_at=now(),updated_at=now())
        if rank==1 and exception in ['scratched','dnf']:row.update(status=exception,finish=None)
        if rank==2 and exception in ['scratched','dnf','tie']:row['finish']=1
        if rank==3 and exception in ['scratched','dnf']:row['finish']=2
        rows.append(row)
    s.import_bundle(dict(schema_version=1,dataset='demo',tables={'race_results':rows}))
    m=compare(s,p['id'])['metrics']
    if exception=='scratched':assert m['log_loss'] is None and m['excluded_from_tau']==1
    if exception=='dnf':assert m['log_loss'] is not None and m['excluded_from_tau']==1
    if exception=='tie':assert m['exact_top3'] is None and m['ties_in_top3']
    if exception=='reverse':assert m['kendall_tau_b']==-1
    s.close()
