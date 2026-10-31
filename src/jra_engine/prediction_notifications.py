"""Local chat outbox; no purchases, LLM calls, DB writes or delivery transport."""
from datetime import datetime,timezone
from pathlib import Path
import argparse,json
import duckdb
from .store import digest,encoded

def build_notice(snapshot):
    p={k:v for k,v in snapshot.items() if k not in ('cached','notification_warning')}
    horses=sorted(p['horses'],key=lambda h:h['predicted_rank'])
    if len(horses)<3 or len({h['id'] for h in horses})!=len(horses):
        raise ValueError('Need at least three distinct runners')
    axis=max(horses,key=lambda h:(h['top3'],-h['average_rank']))
    partners=[h for h in horses if h['id']!=axis['id']][:2]
    a,b,c=axis['number'],partners[0]['number'],partners[1]['number']
    bets=[dict(type='ワイド',numbers=sorted([a,b]),budget_cap=.20),
          dict(type='ワイド',numbers=sorted([a,c]),budget_cap=.15),
          dict(type='ワイド',numbers=sorted([b,c]),budget_cap=.10),
          dict(type='3連複',numbers=sorted([a,b,c]),budget_cap=.05)]
    return dict(notification_id=digest([p['id'],digest(p)]),prediction_id=p['id'],snapshot_hash=digest(p),
        race=p['race'],as_of=p['as_of'],model_version=p['model_version'],seed=p['seed'],simulations=p['simulations'],
        ranking=[{k:h.get(k) for k in ['id','number','name','predicted_rank','win','top3','average_rank','missing']} for h in horses],
        axis_number=a,bets=bets,reserve_fraction=.50,budget_mode='ratio_only',
        bet_method='Top3率最大を軸、固定勝率順の上位2頭を相手とする簡易ルール。評議会の定性順位とは別。',
        bet_status='候補・採否保留',combination_probabilities=None,market_odds_verified=False,
        warning='仮想研究用。対象券種オッズ・同時的中率未確認、モデル未校正。配分は上限で最適化結果ではない。')

def enqueue(snapshot,folder):
    notice=build_notice(snapshot);folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/(notice['notification_id']+'.json')
    try:
        with path.open('x',encoding='utf-8') as f:f.write(encoded(notice))
    except FileExistsError:
        if json.loads(path.read_text(encoding='utf-8'))['snapshot_hash']!=notice['snapshot_hash']:
            raise ValueError('Outbox identity conflict')
    return path

def collect(data_folder,state_folder):
    """Read only metadata for acknowledged forecasts; load bodies only if pending."""
    data_folder=Path(data_folder);state_folder=Path(state_folder);state_folder.mkdir(parents=True,exist_ok=True)
    if (data_folder/'claude-jobs.lock').exists():return dict(status='writer_busy',pending=[])
    ack=state_folder/'prediction-notified.json'
    acknowledged=json.loads(ack.read_text(encoding='utf-8')) if ack.exists() else {}
    db=duckdb.connect(str(data_folder/'research.duckdb'),read_only=True)
    pending=[]
    try:
        for identity,h in db.execute("SELECT id,hash FROM predictions WHERE id LIKE '%:live' ORDER BY created_at,id").fetchall():
            if acknowledged.get(identity)==h:continue
            p=json.loads(db.execute('SELECT payload FROM predictions WHERE id=?',[identity]).fetchone()[0])
            if digest(p)!=h:raise ValueError('Prediction hash mismatch')
            path=enqueue(p,state_folder/'prediction-outbox')
            notice=json.loads(path.read_text(encoding='utf-8'))
            notice['race_started']=datetime.now(timezone.utc)>=datetime.fromisoformat(p['race']['start_at'])
            if notice['race_started']:
                notice['bet_status']='発走後・買い目の採用対象外'
                notice['bets']=[];notice['reserve_fraction']=1.
            pending.append(dict(prediction_id=identity,snapshot_hash=h,notice_path=str(path),notice=notice))
    finally:db.close()
    return dict(status='ok',pending=pending)

def acknowledge(state_folder,identity,h):
    path=Path(state_folder)/'prediction-notified.json'
    values=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    values[identity]=h;temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(values,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)

def render_notice(notice):
    n=notice;started=datetime.now(timezone.utc)>=datetime.fromisoformat(n['race']['start_at'])
    lines=[f"## {n['race']['name']}（{n['race']['start_at']}）",'',
      f"固定時点 {n['as_of']}／{n['model_version']}／{n['simulations']:,}回／seed {n['seed']}",
      '', '|予測順位|馬番・馬名|勝率|3着以内率|', '|---:|---|---:|---:|']
    lines += [f"|{h['predicted_rank']}|{h['number']} {h['name']}|{h['win']:.2%}|{h['top3']:.2%}|" for h in n['ranking']]
    lines += ['', '発走後のため買い目の採用対象外。順位は結果検証用。' if started else '買い目候補（仮想・採否保留）：']
    if not started:
        lines += [f"- {b['type']} {'–'.join(map(str,b['numbers']))}：総予算の{b['budget_cap']:.0%}まで" for b in n['bets']]
        lines += [f"- 留保：{n['reserve_fraction']:.0%}", '', n['bet_method'], n['warning'],
                  '同じ3頭への複数券種は損失が相関する。発走直前の取消・馬場・オッズは別確認が必要。']
    lines += ['', '欠損項目：'+ '／'.join(sorted({m for h in n['ranking'] for m in (h.get('missing') or [])}))]
    return '\n'.join(lines)+'\n'

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['poll','ack'])
    p.add_argument('--data-dir',type=Path);p.add_argument('--state-dir',type=Path,required=True)
    p.add_argument('--prediction-id');p.add_argument('--snapshot-hash');a=p.parse_args()
    if a.command=='poll':
        if not a.data_dir:p.error('--data-dir required')
        print(json.dumps(collect(a.data_dir,a.state_dir),ensure_ascii=False))
    else:
        if not a.prediction_id or not a.snapshot_hash:p.error('prediction id and hash required')
        acknowledge(a.state_dir,a.prediction_id,a.snapshot_hash)
if __name__=='__main__':main()
