"""Frozen-model batch evaluation; never acquire data or silently fill missing races."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from .pipeline import predict, compare, MODEL
from .store import Store, now, stamp, digest

def model_hash():
    return hashlib.sha256(b''.join((Path(__file__).parent/f).read_bytes() for f in ['pipeline.py','simulation.py'])).hexdigest()

class EvaluationView:
    """Reuse the existing evaluator without writing a changed prediction/report."""
    def __init__(self,store,snapshot):self.store=store;self.snapshot=snapshot
    def records(self,*a,**kw):return self.store.records(*a,**kw)
    def load(self,table,key):return self.snapshot if table=='predictions' and key==self.snapshot['id'] else self.store.load(table,key)
    def save(self,table,key,value):return value

def compare_order(store,snapshot,method):
    p=copy.deepcopy(snapshot)
    if method=='average':p['horses'].sort(key=lambda h:(h['average_rank'],h['number']))
    elif method!='win':raise ValueError('ranking method')
    for i,h in enumerate(p['horses']):h['predicted_rank']=i+1
    return compare(EvaluationView(store,p),p['id'])

def audit(store,item,min_history=1):
    rid=item['race_id'];cutoff=stamp(item['as_of']);errors=[]
    races=store.records('races',cutoff,[rid])
    if not races:return ['race_card_missing'],[]
    race=races[0]
    if cutoff>=race['start_at']:errors.append('cutoff_not_before_start')
    entries=[e for e in store.records('race_entries',cutoff) if e['race_id']==rid]
    if len(entries)!=race['declared_entries']:errors.append('roster_incomplete')
    if len({e['horse_id'] for e in entries})!=len(entries):errors.append('duplicate_horse')
    history=store.records('horse_performances',cutoff)
    used=[race,*entries]
    for e in entries:
        if e.get('status')!='active':continue
        past=[h for h in history if h['horse_id']==e['horse_id'] and h['race_id']!=rid and stamp(h['start_at'])<cutoff and h.get('status')=='finished']
        if len(past)<min_history:errors.append('history_missing:'+e['horse_id'])
        used.extend(past)
    results=[r for r in store.records('race_results',now()) if r['race_id']==rid]
    if {r['horse_id'] for r in results}!={e['horse_id'] for e in entries}:errors.append('results_incomplete')
    if not results or any(not r.get('final') or r['available_at']<=race['start_at'] for r in results):errors.append('final_results_unverified')
    return errors,used

def grade(records,cutoff):
    # The old B-reconstructed rows belong to C; a date assertion alone is not proof.
    for r in records:
        if r.get('data_grade')!='A-verified' or r.get('availability_verified') is not True or not r.get('published_at'):return 'C-reconstruction'
        if stamp(r['published_at'])>stamp(cutoff) or not r.get('availability_evidence'):return 'C-reconstruction'
    return 'verified-as-of' if records else 'C-reconstruction'

def aggregate(rows):
    groups={}
    for row in rows:
        for method,report in row['rankings'].items():
            key=row['grade']+'/'+method;groups.setdefault(key,[]).append(report['metrics'])
    out={}
    for key,values in groups.items():
        out[key]={'races':len(values),'metrics':{}}
        for field in ['top1_hit','top3_overlap','kendall_tau_b','log_loss','brier_score']:
            valid=[float(v[field]) for v in values if v.get(field) is not None and math.isfinite(float(v[field]))]
            out[key]['metrics'][field]={'n':len(valid),'mean':sum(valid)/len(valid) if valid else None}
    return out

def run_batch(store,manifest):
    fingerprint=model_hash();items=manifest['races']
    if len({r['race_id'] for r in items})!=len(items):raise ValueError('duplicate evaluation race')
    if any(r['race_id']=='202608040711' for r in items):raise ValueError('秋華賞は独立評価から除外してください')
    rows=[];skipped=[]
    for item in items:
        errors,used=audit(store,item,manifest.get('minimum_past_runs',1))
        if errors:skipped.append(dict(race_id=item['race_id'],errors=errors));continue
        try:
            p=predict(store,item['race_id'],item['as_of'],manifest.get('seed',42),mode='retrospective')
            rankings={method:compare_order(store,p,method) for method in ['win','average']}
            rows.append(dict(race_id=item['race_id'],prediction_id=p['id'],grade=grade(used,item['as_of']),rankings=rankings,cached=p['cached']))
        except Exception as e:skipped.append(dict(race_id=item['race_id'],errors=[type(e).__name__+': '+str(e)]))
    if model_hash()!=fingerprint:raise RuntimeError('model changed during batch')
    report=dict(created_at=now(),model_version=MODEL,model_sha256=fingerprint,seed=manifest.get('seed',42),runs_per_race=100000,requested=len(items),completed=len(rows),skipped=skipped,summary=aggregate(rows),races=rows,manifest_hash=digest(manifest))
    store.save('backtest_results','batch:'+digest(report),report)
    return report

def main():
    p=argparse.ArgumentParser();p.add_argument('manifest');p.add_argument('--database-folder',default='data/real');p.add_argument('--output',required=True);a=p.parse_args()
    s=Store(a.database_folder)
    try:
        report=run_batch(s,json.loads(Path(a.manifest).read_text(encoding='utf-8-sig')))
        Path(a.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:report[k] for k in ['requested','completed','summary']}))
    finally:s.close()
if __name__=='__main__':main()
