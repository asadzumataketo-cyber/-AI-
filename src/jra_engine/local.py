"""Local UI and CLI. No external data fetching and no LLM calls."""
import argparse
import csv
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from .store import Store,now,stamp
from .pipeline import compare
from .readiness import guarded_predict as predict, preparation_status, audit_live_race

ROOT=Path(__file__).resolve().parents[2]
def demo_bundle():
    from datetime import datetime,timedelta,timezone
    t=datetime.now(timezone.utc);start=t+timedelta(days=2)
    meta=dict(source='synthetic:test-only',available_at=(t-timedelta(hours=1)).isoformat(),retrieved_at=t.isoformat(),updated_at=t.isoformat(),availability_evidence='人工生成テストデータ・実レースではない')
    race=dict(meta,race_id='DEMO-ONLY',name='架空レース E2E',venue='東京',surface='芝',distance=2000,start_at=start.isoformat(),declared_entries=6)
    tables=dict(races=[race],horses=[],race_entries=[],horse_performances=[])
    for i in range(1,7):
        h=f'DEMO-H{i}';entry=dict(meta,entry_id=f'DEMO-ONLY:{h}',race_id='DEMO-ONLY',horse_id=h,number=i,name=f'架空馬{i}',style=['先行','差し','逃げ'][i%3],status='active',carried_weight=57)
        tables['horses'].append(dict(meta,horse_id=h,name=entry['name']))
        tables['race_entries'].append(entry)
        for j in range(3):
            past=start-timedelta(days=30*(j+1))
            tables['horse_performances'].append(dict(meta,entry_id=f'DEMO-PAST{j}:{h}',horse_id=h,race_id=f'DEMO-PAST{j}',start_at=past.isoformat(),venue='東京',surface='芝',distance=2000,field_size=10,finish=i,going='良',status='finished'))
    return dict(schema_version=1,dataset='demo',tables=tables)

class Handler(BaseHTTPRequestHandler):
    def reply(self,value,status=200):
        data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        try:
            url=urlparse(self.path);q=parse_qs(url.query);s=self.server.store
            if url.path=='/api/races':return self.reply(s.records('races',now()))
            if url.path=='/api/readiness':return self.reply(audit_live_race(s,q['race_id'][0],q.get('as_of',[None])[0]) if q.get('race_id') else preparation_status(s))
            if url.path=='/api/preparation':return self.reply(dict(targets=s.records('race_targets',now()),registrations=s.records('race_registrations',now()),history=s.records('observed_performances',now())))
            if url.path=='/api/snapshot':return self.reply(s.load('predictions',q['id'][0]))
            if url.path=='/api/compare':return self.reply(compare(s,q['id'][0]))
            if url.path=='/api/health':return self.reply(dict(ok=True,dataset=s.folder.name,model='jra-weekend-1.0.0-baseline'))
            if url.path!='/':return self.reply(dict(error='not found'),404)
            data=(ROOT/'web'/'index.html').read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(data)
        except Exception as e:logging.exception('request failed');self.reply(dict(error=str(e)),400)
    def do_POST(self):
        # Browser-origin checks prevent other websites from writing to the local DB.
        if self.headers.get('Origin') not in [None,f'http://127.0.0.1:{self.server.server_port}'] or self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':return self.reply(dict(error='origin'),403)
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size>5_000_000:raise ValueError('入力は5MB以内です')
            if self.headers.get('Content-Type')!='application/json':raise ValueError('application/json required')
            data=json.loads(self.rfile.read(size));s=self.server.store
            if self.path=='/api/import':return self.reply(dict(inserted=s.import_bundle(data)))
            if self.path=='/api/predict':return self.reply(predict(s,**data))
            raise ValueError('unknown route')
        except Exception as e:logging.exception('request failed');self.reply(dict(error=str(e)),400)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dataset',choices=['real','demo'],default='real');parser.add_argument('--data-dir',default='data');sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init');sub.add_parser('demo');imp=sub.add_parser('import');imp.add_argument('file');imp.add_argument('--table')
    pr=sub.add_parser('predict');pr.add_argument('race_id');pr.add_argument('--as-of',default=None);pr.add_argument('--mode',choices=['live','retrospective'],default='live');pr.add_argument('--seed',type=int,default=42)
    co=sub.add_parser('compare');co.add_argument('prediction_id');sv=sub.add_parser('serve');sv.add_argument('--port',type=int,default=4180)
    ready=sub.add_parser('readiness');ready.add_argument('race_id',nargs='?');ready.add_argument('--as-of')
    args=parser.parse_args();folder=Path(args.data_dir)/args.dataset;folder.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=folder/'errors.log',level=logging.WARNING)
    s=Store(folder)
    try:
        if args.command=='demo':
            if args.dataset!='demo':raise ValueError('demoは --dataset demo が必要です')
            print(json.dumps(dict(inserted=s.import_bundle(demo_bundle())),ensure_ascii=False))
        elif args.command=='import':
            if args.table:
                with open(args.file,encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
                numeric={'number','distance','declared_entries','field_size','finish','carried_weight','score','odds','sample_count','front_bias','closing_percentile'}
                for r in rows:
                    for k,v in list(r.items()):
                        if k in numeric:r[k]=float(v) if v else None
                        elif k=='final':r[k]=v.lower()=='true'
                bundle=dict(schema_version=1,dataset=args.dataset,tables={args.table:rows})
            else:bundle=json.loads(Path(args.file).read_text(encoding='utf-8-sig'))
            print(json.dumps(dict(inserted=s.import_bundle(bundle))))
        elif args.command=='predict':print(json.dumps(predict(s,args.race_id,args.as_of or now(),args.seed,args.mode),ensure_ascii=False))
        elif args.command=='compare':print(json.dumps(compare(s,args.prediction_id),ensure_ascii=False))
        elif args.command=='readiness':print(json.dumps(audit_live_race(s,args.race_id,args.as_of) if args.race_id else preparation_status(s,args.as_of),ensure_ascii=False))
        elif args.command=='serve':
            server=HTTPServer(('127.0.0.1',args.port),Handler);server.store=s
            print(f'Local: http://127.0.0.1:{args.port} dataset={args.dataset}',flush=True);server.serve_forever()
        else:print('Database ready')
    finally:s.close()
if __name__=='__main__':main()
