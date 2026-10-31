"""Append-only, as-of records. All raw records remain on this computer."""
import hashlib
import json
import shutil
import re
import math
from datetime import datetime, timezone
from pathlib import Path
import duckdb

TABLES = {'horses':'horse_id','races':'race_id','race_entries':'entry_id',
 'race_results':'entry_id','horse_performances':'entry_id','track_conditions':'race_id',
 'odds_snapshots':'entry_id','paddock_evaluations':'entry_id',
 'race_registrations':'fact_id','observed_performances':'fact_id','race_targets':'target_key'}
def now(): return datetime.now(timezone.utc).isoformat()
def stamp(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None: raise ValueError('日時にはタイムゾーンが必要です')
    return t.astimezone(timezone.utc).isoformat()
def encoded(obj): return json.dumps(obj,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))
def digest(obj): return hashlib.sha256(encoded(obj).encode()).hexdigest()

class Store:
    def __init__(self, folder='data/live'):
        self.folder=Path(folder); self.folder.mkdir(parents=True,exist_ok=True)
        path=self.folder/'research.duckdb'
        # Copy only a closed database, before applying any schema changes.
        marker=self.folder/'schema-version-1'
        if path.exists() and not marker.exists():
            backup=self.folder/'backups'; backup.mkdir(exist_ok=True)
            shutil.copy2(path,backup/('pre-open-'+datetime.now().strftime('%Y%m%d%H%M%S%f')+'.duckdb'))
        self.db=duckdb.connect(str(path))
        self.db.execute("SET memory_limit='512MB'")
        for table in TABLES:
            self.db.execute(f'CREATE TABLE IF NOT EXISTS {table}(record_key VARCHAR, available_at VARCHAR, source VARCHAR, retrieved_at VARCHAR, updated_at VARCHAR, payload VARCHAR, hash VARCHAR, PRIMARY KEY(record_key,available_at))')
            self.db.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_time ON {table}(available_at,record_key)')
        for table in ['predictions','prediction_probabilities','simulation_summaries','model_versions','backtest_results','error_logs']:
            self.db.execute(f'CREATE TABLE IF NOT EXISTS {table}(id VARCHAR PRIMARY KEY, created_at VARCHAR, payload VARCHAR, hash VARCHAR)')
        marker.write_text('1',encoding='ascii')
    def close(self): self.db.close()
    def import_bundle(self,bundle):
        if bundle.get('schema_version')!=1: raise ValueError('schema_version は1です')
        expected='demo' if self.folder.name=='demo' else 'real'
        if bundle.get('dataset')!=expected: raise ValueError('架空と実データのDBは別です。datasetを確認してください')
        rows=bundle.get('tables',{})
        if not rows or set(rows)-TABLES.keys(): raise ValueError('不正なテーブル')
        count=0
        self.db.execute('BEGIN')
        try:
            for table, records in rows.items():
                for r in records:
                    for field in [TABLES[table],'source','available_at','retrieved_at','updated_at','availability_evidence']:
                        if not r.get(field): raise ValueError(f'{table}: {field} が必要です')
                    if expected=='real':
                        if 'race_id' in r and not re.fullmatch(r'\d{12}',str(r['race_id'])):raise ValueError('JRA race_id は公式12桁IDです')
                        unresolved=table in ['race_registrations','observed_performances'] and r.get('horse_id') is None
                        if 'horse_id' in r and not unresolved and not re.fullmatch(r'\d{10}',str(r['horse_id'])):raise ValueError('horse_id は公式10桁IDです')
                    if table in ['race_entries','race_results','horse_performances','paddock_evaluations','odds_snapshots']:
                        for field in ['race_id','horse_id']:
                            if not r.get(field):raise ValueError(field+' が必要です')
                        if str(r['entry_id'])!=str(r['race_id'])+':'+str(r['horse_id']):raise ValueError('entry_id は race_id:horse_id です')
                    if table=='race_entries':
                        if not isinstance(r.get('number'),(int,float)) or int(r['number'])!=r['number'] or not 1<=r['number']<=18 or not r.get('name'):raise ValueError('馬番・馬名が必要です')
                        if r.get('status') not in ['active','scratched','excluded']:raise ValueError('出走状態が不正です')
                    if table=='race_results':
                        if r.get('status') not in ['finished','scratched','excluded','dnf','disqualified']:raise ValueError('確定状態が不正です')
                        if r.get('status')=='finished' and (not isinstance(r.get('finish'),(int,float)) or int(r['finish'])!=r['finish'] or not 1<=r['finish']<=18):raise ValueError('着順が不正です')
                    for field in ['score','front_bias','closing_percentile','odds','carried_weight']:
                        if r.get(field) is not None and (not isinstance(r[field],(int,float)) or not math.isfinite(r[field])):raise ValueError(field+' が不正です')
                    if table=='paddock_evaluations' and not -2<=r.get('score',0)<=2:raise ValueError('パドックscoreは-2〜2')
                    if table=='track_conditions' and not -2<=r.get('front_bias',0)<=2:raise ValueError('front_biasは-2〜2')
                    if table=='horse_performances':r['start_at']=stamp(r['start_at'])
                    for field in ['available_at','retrieved_at','updated_at']: r[field]=stamp(r[field])
                    if stamp(r['available_at'])>stamp(r['retrieved_at']): raise ValueError('公開日時は取得日時以下です')
                    if table=='races':
                        if r.get('venue') not in ['札幌','函館','福島','新潟','東京','中山','中京','京都','阪神','小倉'] or r.get('surface') not in ['芝','ダート']: raise ValueError('JRA平地のみ対応')
                        r['start_at']=stamp(r['start_at'])
                    key=str(r[TABLES[table]]); payload=encoded(r); h=digest(r)
                    old=self.db.execute(f'SELECT hash FROM {table} WHERE record_key=? AND available_at=?',[key,r['available_at']]).fetchone()
                    if old and old[0]!=h: raise ValueError('同一ID・公開日時の改変は禁止。訂正は新しい公開日時で追記してください')
                    if not old:
                        self.db.execute(f'INSERT INTO {table} VALUES (?,?,?,?,?,?,?)',[key,r['available_at'],r['source'],r['retrieved_at'],r['updated_at'],payload,h]);count+=1
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise
        return count
    def records(self,table,as_of,keys=None):
        if table not in TABLES: raise ValueError('table')
        args=[stamp(as_of)]; sql=f'SELECT payload FROM {table} WHERE available_at<=?'
        if keys is not None:
            if not keys:return []
            sql+=' AND record_key IN ('+','.join('?' for _ in keys)+')';args+=list(keys)
        sql+=' QUALIFY row_number() OVER(PARTITION BY record_key ORDER BY available_at DESC)=1 ORDER BY record_key'
        return [json.loads(row[0]) for row in self.db.execute(sql,args).fetchall()]
    def save(self,table,key,obj):
        if table not in ['predictions','prediction_probabilities','simulation_summaries','model_versions','backtest_results','error_logs']: raise ValueError('table')
        old=self.db.execute(f'SELECT payload,hash FROM {table} WHERE id=?',[key]).fetchone()
        if old:
            if old[1]!=digest(obj): raise ValueError('固定済み記録は変更できません')
            return json.loads(old[0])
        self.db.execute(f'INSERT INTO {table} VALUES (?,?,?,?)',[key,now(),encoded(obj),digest(obj)])
        return obj
    def load(self,table,key):
        if table not in ['predictions','backtest_results']:raise ValueError('table')
        row=self.db.execute(f'SELECT payload,hash FROM {table} WHERE id=?',[key]).fetchone()
        if not row:return None
        value=json.loads(row[0])
        if digest(value)!=row[1]:raise ValueError('保存記録のハッシュ不一致')
        return value
