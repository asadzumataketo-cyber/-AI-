"""Local-only feature extraction, prediction sealing and result comparison."""
import math
from pathlib import Path
import numpy as np
from .store import now, stamp, digest, encoded
from .simulation import simulate

MODEL='jra-weekend-1.0.0-baseline'
def features(store,race_id,as_of):
    as_of=stamp(as_of)
    races=store.records('races',as_of,[race_id])
    if not races:raise ValueError('予測時点のレース情報がありません')
    race=races[0]
    if as_of>=race['start_at']:raise ValueError('as_ofは発走前にしてください')
    entries=[r for r in store.records('race_entries',as_of) if r['race_id']==race_id]
    if not entries:raise ValueError('出走表未登録')
    if len({r['number'] for r in entries})!=len(entries) or len({r['horse_id'] for r in entries})!=len(entries):raise ValueError('馬番・馬IDが重複しています')
    if len(entries)!=race.get('declared_entries'):raise ValueError('公式の出走頭数と入力件数が一致しません')
    horse_ids={e['horse_id'] for e in entries}
    # SQL extracts only the participating horses' history, with a strict pre-race cutoff.
    rows=store.db.execute('''SELECT payload FROM horse_performances WHERE available_at<=?
      AND json_extract_string(payload,'$.horse_id') IN (SELECT unnest(?))
      QUALIFY row_number() OVER(PARTITION BY record_key ORDER BY available_at DESC)=1 ORDER BY record_key''',[as_of,sorted(horse_ids)]).fetchall()
    import json
    histories=[json.loads(r[0]) for r in rows]
    histories=[h for h in histories if h['race_id']!=race_id and stamp(h['start_at'])<as_of and stamp(h['start_at'])<race['start_at'] and h.get('status','finished')=='finished']
    tracks=[x for x in store.records('track_conditions',as_of,[race_id])]
    track=tracks[0] if tracks else {}
    paddocks={x['entry_id']:x for x in store.records('paddock_evaluations',as_of) if x['race_id']==race_id}
    odds={x['entry_id']:x for x in store.records('odds_snapshots',as_of) if x['race_id']==race_id}
    out=[]; used=[race,*entries,*tracks]
    for e in entries:
        if e.get('status','active')!='active':continue
        history=sorted([h for h in histories if h['horse_id']==e['horse_id']],key=lambda h:h['start_at'],reverse=True)[:20]
        missing=[]
        # Relative finishing performance, never compare raw times across courses/classes.
        def score(h):
            n=float(h['field_size']);p=float(h['finish'])
            if n<2 or not 1<=p<=n:raise ValueError('過去走の頭数・着順が不正です')
            return 70+15*(n-p)/(n-1)
        scores=[score(h) for h in history];recent=scores[:3]
        base=float(np.average(recent,weights=[.5,.3,.2][:len(recent)])) if recent else 77.5
        if len(recent)<3:missing.append('直近3走不足：中立事前値と大きな分散を使用')
        def fit(test):
            vals=[score(h) for h in history if test(h)]
            return (float(np.mean(vals))-base)*len(vals)/(len(vals)+5) if vals else 0.
        distance=fit(lambda h:abs(float(h['distance'])-float(race['distance']))<=200)
        course=fit(lambda h:h['venue']==race['venue'] and h['surface']==race['surface'])
        surface=fit(lambda h:h['surface']==race['surface'])
        going=fit(lambda h:h.get('going')==track.get('going')) if track.get('going') else 0
        closing=[h.get('closing_percentile') for h in history[:3] if h.get('closing_percentile') is not None]
        end_speed=(float(np.mean(closing))-.5)*1.5 if closing else 0.
        if not closing:missing.append('末脚指数なし')
        style=e.get('style');pace={'逃げ':-1.,'先行':-.5,'差し':.5,'追込':1.}.get(style,0.)
        if not style:missing.append('脚質なし')
        bias=float(track.get('front_bias',0))*min(float(track.get('sample_count',0))/5,1)*(-pace)
        if track.get('sample_count',0)<5:missing.append('当日バイアス実測不足：縮小補正')
        pad=paddocks.get(e['entry_id']);condition=float(pad['score']) if pad else 0.
        if not pad:missing.append('パドック未入力')
        weight=e.get('carried_weight');jockey=-(float(weight)-57)*.15 if weight else 0.
        missing.append('騎手固有効果未校正')
        std=max(2.,min(6.,float(np.std(scores[:10])) if len(scores)>=3 else 4.5))
        rating=base+.20*course+.25*distance+.20*surface+.15*going+end_speed+bias+jockey+condition
        observed=odds.get(e['entry_id']);used+=history+([pad] if pad else [])+([observed] if observed else [])
        out.append(dict(id=e['horse_id'],number=e['number'],name=e['name'],rating=rating,std=std,
          pace_sensitivity=pace,mean=base,recent_form=base,course_fit=course,distance_fit=distance,surface_fit=surface,
          pace_fit=pace,track_bias=bias,jockey_factor=jockey,condition=condition,closing_factor=end_speed,
          last3=history[:3],missing=missing,odds=observed.get('odds') if observed else None))
    if not 3<=len(out)<=18:raise ValueError('有効出走馬は3〜18頭必要です')
    return race,out,used

def predict(store,race_id,as_of,seed=42,mode='live'):
    if mode not in ['live','retrospective']:raise ValueError('mode')
    current=stamp(now());as_of=stamp(as_of)
    race,horses,used=features(store,race_id,as_of)
    if mode=='live' and (current>=race['start_at'] or as_of>current):raise ValueError('発走後の事前予測保存・未来時点指定は禁止です。過去検証はretrospectiveを使用')
    identity=f'{race_id}:live' if mode=='live' else race_id+':replay:'+digest([as_of,seed,used,MODEL])[:20]
    old=store.load('predictions',identity)
    if old:return {**old,'cached':True}
    calculation=simulate(horses,runs=100000,batch_size=10000,seed=int(seed),pace_sigma=1.)
    if mode=='live' and stamp(now())>=race['start_at']:raise ValueError('計算中に発走時刻に達したため事前予測を保存しません')
    by_id={h['id']:h for h in horses}
    ranked=sorted(calculation['horses'],key=lambda h:(-h['win'],by_id[h['id']]['number']))
    for i,h in enumerate(ranked):h.update(by_id[h['id']]);h['predicted_rank']=i+1
    snapshot=dict(id=identity,race=race,as_of=as_of,created_at=current,mode=mode,model_version=MODEL,
      seed=int(seed),simulations=100000,batch_size=10000,numpy_version=np.__version__,horses=ranked,
      trifecta_top10=calculation['trifecta_top10'],input_hash=digest(used),used_sources=[{k:r.get(k) for k in ['source','available_at','retrieved_at','availability_evidence']} for r in used],
      warning='未校正の基準モデル。時系列評価前。確率の標本誤差はモデル誤差を含まない。')
    store.db.execute('BEGIN')
    try:
        store.save('model_versions',MODEL,dict(version=MODEL,calibrated=False,training_end=None))
        store.save('predictions',identity,snapshot)
        store.save('prediction_probabilities',identity,ranked)
        store.save('simulation_summaries',identity,dict(runs=100000,seed=int(seed),trifecta=calculation['trifecta_top10']))
        store.db.execute('COMMIT')
    except Exception:store.db.execute('ROLLBACK');raise
    folder=store.folder/'snapshots';folder.mkdir(exist_ok=True)
    path=folder/(digest(identity)+'.json')
    with path.open('x',encoding='utf-8') as f:f.write(encoded(dict(snapshot=snapshot,sha256=digest(snapshot))))
    return {**snapshot,'cached':False}

def compare(store,prediction_id):
    p=store.load('predictions',prediction_id)
    if not p:raise ValueError('予測がありません')
    results=[r for r in store.records('race_results',now()) if r['race_id']==p['race']['race_id']]
    expected={e['horse_id'] for e in store.records('race_entries',p['as_of']) if e['race_id']==p['race']['race_id']}
    if {r['horse_id'] for r in results}!=expected:raise ValueError('確定結果は取消・中止を含む全頭分が必要です')
    if any(not r.get('final') or r['available_at']<=p['race']['start_at'] for r in results):raise ValueError('発走後に公開された確定結果のみ取り込めます')
    by={r['horse_id']:r for r in results};rows=[]
    for h in p['horses']:
        r=by[h['id']];finish=r.get('finish') if r['status']=='finished' else None
        rows.append(dict(horse_id=h['id'],name=h['name'],number=h['number'],predicted_rank=h['predicted_rank'],actual_rank=finish,status=r['status'],difference=h['predicted_rank']-finish if finish else None))
    valid=[r for r in rows if r['actual_rank'] is not None];c=d=ties=0
    for i,a in enumerate(valid):
        for b in valid[i+1:]:
            v=(a['predicted_rank']-b['predicted_rank'])*(a['actual_rank']-b['actual_rank'])
            c+=v>0;d+=v<0;ties+=v==0
    denom=math.sqrt((c+d)*(c+d+ties));tau=(c-d)/denom if denom else None
    actual_top={r['horse_id'] for r in valid if r['actual_rank']<=3};winner=[r['horse_id'] for r in valid if r['actual_rank']==1]
    if not winner:raise ValueError('確定勝者がありません')
    ties_in_top=len(actual_top)!=3 or any(sum(r['actual_rank']==k for r in valid)!=1 for k in [1,2,3])
    withdrawn=[r for r in rows if r['status'] in ['scratched','excluded']]
    probs={h['id']:h['win'] for h in p['horses']};target={h:1/len(winner) if h in winner else 0 for h in probs}
    # A post-lock withdrawal changes the sample space: do not silently renormalize.
    logloss=None if withdrawn else -sum(target[h]*math.log(max(probs[h],1e-12)) for h in probs)
    brier=None if withdrawn else sum((probs[h]-target[h])**2 for h in probs)
    metrics=dict(top1_hit=p['horses'][0]['id'] in winner,top3_overlap=sum(h['id'] in actual_top for h in p['horses'][:3]),
      exact_top3=None if ties_in_top else all(by[h['id']].get('finish')==i+1 and by[h['id']]['status']=='finished' for i,h in enumerate(p['horses'][:3])),
      winner_predicted_ranks=[r['predicted_rank'] for r in rows if r['horse_id'] in winner],kendall_tau_b=tau,valid_finishers=len(valid),excluded_from_tau=len(rows)-len(valid),
      log_loss=logloss,brier_score=brier,post_lock_withdrawals=len(withdrawn),ties_in_top3=ties_in_top)
    report=dict(prediction_id=prediction_id,model_version=p['model_version'],mode=p['mode'],race_id=p['race']['race_id'],metrics=metrics,rows=rows,result_hash=digest(results))
    store.save('backtest_results',prediction_id+':'+digest(results)[:16],report)
    return report
