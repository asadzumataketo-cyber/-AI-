"""Read-only race preparation checks and a guard around the unchanged model."""
import json
import re
from collections import Counter
from urllib.parse import urlparse
from .store import now, stamp
from .pipeline import predict as engine_predict


def _official(url):
    try:
        parsed=urlparse(url or '')
        host=parsed.hostname or ''
    except (ValueError,TypeError):return False
    return parsed.scheme=='https' and any(host==d or host.endswith('.'+d) for d in ['jra.go.jp','jra.jp','japanracing.jp'])


def _provenance(row,cutoff,official=False):
    errors=[]
    if not row.get('source') or not row.get('availability_evidence'):errors.append('出典・取得根拠が不足')
    if not re.fullmatch(r'[0-9a-fA-F]{64}',str(row.get('source_sha256',''))):errors.append('原本SHA-256が不足')
    for field in ['available_at','retrieved_at']:
        try:
            if stamp(row[field])>cutoff:errors.append('予測時点後の'+field)
        except (KeyError,ValueError,TypeError):errors.append(field+'が不正')
    if official and not _official(row.get('official_source') or row.get('source')):errors.append('JRA公式出典の確認が必要')
    return errors


def _histories(store,horse_ids,cutoff):
    if not horse_ids:return []
    rows=store.db.execute('''SELECT payload FROM horse_performances
      WHERE available_at<=? AND json_extract_string(payload,'$.horse_id') IN (SELECT unnest(?))
      QUALIFY row_number() OVER(PARTITION BY record_key ORDER BY available_at DESC)=1
      ORDER BY record_key''',[cutoff,sorted(horse_ids)]).fetchall()
    return [json.loads(r[0]) for r in rows]


def _past(rows,horse_id,cutoff,race_id=None):
    return sorted([r for r in rows if r['horse_id']==horse_id and r['race_id']!=race_id
                   and stamp(r['start_at'])<cutoff and r.get('status','finished')=='finished'],
                  key=lambda r:r['start_at'],reverse=True)


def audit_live_race(store,race_id,as_of=None):
    cutoff=stamp(as_of or now());errors=[];warnings=[];horse_rows=[]
    races=store.records('races',cutoff,[race_id])
    if not races:return dict(race_id=race_id,as_of=cutoff,ready=False,errors=['公式の確定出走表が未登録'],warnings=[],horses=[])
    race=races[0]
    if cutoff>stamp(now()):errors.append('未来の予測時点は指定できません')
    if cutoff>=race['start_at']:errors.append('発走時刻を過ぎています')
    if race.get('confirmed') is not True:errors.append('レースの出走確定確認が未記録')
    errors.extend(_provenance(race,cutoff,official=True))
    entries=[e for e in store.records('race_entries',cutoff) if e['race_id']==race_id]
    if len(entries)!=race.get('declared_entries'):errors.append('公式出走頭数と入力件数が不一致')
    if len({e['horse_id'] for e in entries})!=len(entries) or len({e['number'] for e in entries})!=len(entries):errors.append('馬ID・馬番が重複')
    active=[e for e in entries if e.get('status')=='active']
    if not 3<=len(active)<=18:errors.append('有効出走馬は3〜18頭必要')
    history=_histories(store,{e['horse_id'] for e in active},cutoff)
    for e in entries:
        gaps=[];notes=[]
        if e.get('official_confirmed') is not True:gaps.append('公式出走表との照合が未記録')
        if e.get('identity_verified') is not True or not e.get('identity_source'):gaps.append('馬IDの照合が未記録')
        if not re.fullmatch(r'\d{10}',e['horse_id']):gaps.append('10桁馬IDが不正')
        if e.get('status')=='active':
            if type(e.get('frame')) is not int or e['frame'] not in range(1,9):gaps.append('枠順が未確認')
            if not e.get('jockey'):gaps.append('騎手が未確認')
            if not isinstance(e.get('carried_weight'),(int,float)) or not 30<=e['carried_weight']<=80:gaps.append('斤量が未確認')
        gaps.extend(_provenance(e,cutoff,official=True))
        past=_past(history,e['horse_id'],cutoff,race_id)
        if e.get('status')=='active':
            if not past:gaps.append('発走前に利用可能な過去走がない')
            if len(past)<3:notes.append('直近3走不足：'+str(len(past))+'走')
            # The unchanged model uses up to 20 runs for fits/variance, not only three.
            for h in past[:20]:
                gaps.extend(_provenance(h,cutoff))
                finish=h.get('finish');field_size=h.get('field_size')
                if not isinstance(finish,(int,float)) or not isinstance(field_size,(int,float)) or not 1<=finish<=field_size:gaps.append('過去走の着順・頭数が不正')
            if any(h.get('going') is None for h in past[:3]):notes.append('過去馬場に欠損')
            if not e.get('style'):notes.append('脚質未入力')
        item=dict(horse_id=e['horse_id'],name=e['name'],number=e['number'],status=e['status'],past_runs=len(past),last3=past[:3],errors=sorted(set(gaps)),warnings=notes)
        horse_rows.append(item)
        errors.extend(f"{e['number']}番 {e['name']}：{g}" for g in sorted(set(gaps)))
        warnings.extend(f"{e['number']}番 {e['name']}：{n}" for n in notes)
    tracks=store.records('track_conditions',cutoff,[race_id])
    if not tracks:warnings.append('当日馬場・バイアス未入力：モデル既定の補正値を使用')
    else:errors.extend(_provenance(tracks[0],cutoff))
    pads=[p for p in store.records('paddock_evaluations',cutoff) if p['race_id']==race_id]
    if not pads:warnings.append('パドック未入力')
    for row in pads+[o for o in store.records('odds_snapshots',cutoff) if o['race_id']==race_id]:errors.extend(_provenance(row,cutoff))
    return dict(race_id=race_id,as_of=cutoff,ready=not errors,errors=sorted(set(errors)),warnings=warnings,horses=horse_rows,
                declared_entries=race.get('declared_entries'),active_entries=len(active),confirmation='Local recorded checks, not independent publication-time certification.')


def preparation_status(store,as_of=None):
    cutoff=stamp(as_of or now());targets=store.records('race_targets',cutoff)
    registrations=store.records('race_registrations',cutoff)
    ids={r['horse_id'] for r in registrations if r.get('horse_id')}
    history=_histories(store,ids,cutoff)
    races=store.records('races',cutoff);out=[]
    for target in targets:
        candidates=[r for r in registrations if r['target_key']==target['target_key']]
        people=[]
        for r in candidates:
            past=_past(history,r.get('horse_id'),cutoff)
            count=len([h for h in history if h['horse_id']==r.get('horse_id')])
            people.append(dict(horse_id=r.get('horse_id'),name=r['horse_name'],history_records=count,usable_past_runs=len(past),last3=past[:3],missing_to_three=max(0,3-len(past)),identity_verified=r.get('identity_verified') is True))
        rid=target.get('official_race_id') or next((r['race_id'] for r in races if r.get('target_key')==target['target_key']),None)
        gate=audit_live_race(store,rid,cutoff) if rid else dict(ready=False,errors=['公式の確定出走表が未登録'],warnings=[],horses=[])
        locked=store.load('predictions',rid+':live') if rid else None
        out.append(dict(target_key=target['target_key'],name=target['name'],date=target['date'],race_id=rid,registered_candidates=len(candidates),unresolved_ids=sum(not r['horse_id'] for r in people),history_records=sum(r['history_records'] for r in people),usable_past_runs=sum(r['usable_past_runs'] for r in people),missing_to_three=sum(r['missing_to_three'] for r in people),horses=people,ready=gate['ready'],errors=gate['errors'],warnings=gate['warnings'],locked=locked is not None,prediction_id=locked['id'] if locked else None))
    return dict(as_of=cutoff,targets=out,total_history_records=len(history),status_counts=dict(Counter(h.get('status','finished') for h in history)),unresolved_ids=sum(not r.get('horse_id') for r in registrations),ready_races=sum(t['ready'] for t in out),locked_races=sum(t['locked'] for t in out))


def guarded_predict(store,race_id,as_of,seed=42,mode='live'):
    if mode=='live' and store.folder.name!='demo':
        saved=store.load('predictions',race_id+':live')
        if saved:return _queue_notice(store,{**saved,'cached':True})
        gate=audit_live_race(store,race_id,as_of)
        if not gate['ready']:raise ValueError('発走前固定の準備未完了：'+' / '.join(gate['errors']))
    return _queue_notice(store,engine_predict(store,race_id,as_of,seed,mode))


def _queue_notice(store,prediction):
    if prediction['mode']=='live' and store.folder.name!='demo':
        try:
            from .prediction_notifications import enqueue
            enqueue(prediction,store.folder/'prediction-outbox')
        except (OSError,ValueError) as e:
            return {**prediction,'notification_warning':str(e)}
    return prediction
