"""Unattended race-day jobs, run by Windows Task Scheduler (see scheduler.py). No LLM, no retries on refusal.

python jobs.py lock <target_key>     fetch the official card; import + lock once the draw is out (no-op if locked)
python jobs.py result <target_key>   after the race: find the official result, import it, compare, save a report
Exit codes: 0 done or already done, 3 not available yet, 1 error. Every run is logged to outputs/jobs.log.
"""
import hashlib, json, os, subprocess, sys, uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
import duckdb
from . import jra_card as jc

ROOT = Path(__file__).resolve().parents[2]
W = Path(os.environ.get('JRA_JOB_WORKDIR', ROOT / 'data' / 'race-jobs'))
from jra_engine.store import Store, now  # noqa: E402  (Codex's code, unchanged)
from jra_engine.readiness import guarded_predict  # noqa: E402
from jra_engine.pipeline import compare  # noqa: E402

BASE = 'https://www.jra.go.jp/JRADB/'
RACES = {}
DB = Path(os.environ.get('JRA_DATA_DIR', ROOT / 'data' / 'real'))
LOCK = DB / 'claude-jobs.lock'


def log(msg):
    (W / 'outputs').mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {' '.join(sys.argv[1:])}: {msg}"
    with open(W / 'outputs' / 'jobs.log', 'a', encoding='utf-8') as f:
        f.write(line + '\n')
    print(line)


def fetch(path):
    """One plain GET with curl's default User-Agent; a refusal is reported, never worked around."""
    if not __import__('re').fullmatch(r'access[DS]\.html\?CNAME=pw01[ds]de\d{22}/[0-9A-F]{2}', path):
        raise ValueError('Unexpected JRA URL')
    res = subprocess.run(['curl', '--silent', '--show-error', '--max-time', '45', '-w', '%{http_code}', BASE + path], capture_output=True, timeout=60)
    raw, code = res.stdout[:-3], res.stdout[-3:].decode()
    if res.returncode or code != '200':
        raise RuntimeError(f'HTTP {code}: {path}')
    seen = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    name = f"{path.split('CNAME=')[1].replace('/', '_')}-{seen[:19].replace(':', '')}.html"
    (W / 'raw' / 'jobs').mkdir(parents=True, exist_ok=True)
    (W / 'raw' / 'jobs' / name).write_bytes(raw)
    return raw, hashlib.sha256(raw).hexdigest(), seen, BASE + path


class db_lock:
    """Fail closed on an existing lock. Never steal an old or active writer's lock."""
    def __enter__(self):
        self.token = uuid.uuid4().hex
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as e:
            raise RuntimeError('DBロック使用中。古くても自動削除しません') from e
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(dict(pid=os.getpid(), token=self.token, created_at=now()), f)
        return self

    def __exit__(self, *a):
        if LOCK.exists() and json.loads(LOCK.read_text(encoding='utf-8')).get('token') == self.token:
            LOCK.unlink()


def race_info(key):
    if not RACES:
        RACES.update({r['target_key']: r for r in json.loads((W / 'races.json').read_text(encoding='utf-8'))})
    return RACES[key]


def meta(url, sha, seen, evidence):
    return dict(source=url, source_sha256=sha, source_sha256_scope='original HTML bytes as served (cp932)',
                available_at=seen, retrieved_at=seen, updated_at=seen, published_at=None,
                data_grade='observed-before-event', availability_evidence=evidence)


def lock(key):
    race = race_info(key)
    rid, _ = jc.race_id_from_cname(race['cname'])
    db = duckdb.connect(str(DB / 'research.duckdb'), read_only=True)
    try:
        if db.execute('SELECT 1 FROM predictions WHERE id=?', [rid + ':live']).fetchone():
            log('固定済み。取得もしない'); return 0
    finally:
        db.close()
    until = datetime.fromisoformat(race['start_at']) - datetime.now(timezone.utc)
    if not timedelta(hours=1) <= until <= timedelta(hours=2):
        log('新規固定は発走1〜2時間前のみ。既存固定は保持'); return 3
    raw, sha, seen, url = fetch('accessD.html?CNAME=' + race['cname'])
    try:
        card = jc.parse_card(raw, url)
    except ValueError as e:
        if '枠順' in str(e):
            log('枠順未発表。次の回で再確認'); return 3
        raise
    with db_lock():
        s = Store(DB)
        try:
            if s.load('predictions', card['race_id'] + ':live'):
                log('固定済み。何もしない'); return 0
            target = next(t for t in s.records('race_targets', now()) if t['target_key'] == key)
            b = jc.card_bundle(card, raw, sha, url, seen, target)
            m = {k: b['tables']['races'][0][k] for k in meta(url, sha, seen, '')}
            b['tables']['odds_snapshots'] = [dict(m, entry_id=f"{card['race_id']}:{e['horse_id']}", race_id=card['race_id'],
                                                  horse_id=e['horse_id'], odds=e['odds'], odds_kind='単勝・発売中の途中経過')
                                             for e in card['entries'] if e['odds'] is not None]
            b.pop('active_entries')
            inserted = s.import_bundle(b)
            p = guarded_predict(s, card['race_id'], now(), seed=42, mode='live')
        finally:
            s.close()
    log(f"取込{inserted}件、固定 {p['id']} as_of={p['as_of']} 1位={p['horses'][0]['name']}")
    return 0


def result(key):
    race = race_info(key)
    out = W / 'outputs' / f'result-{key}.json'
    if out.exists():
        log('照合済み。再取り込みせずレポートを確認'); return build_report(key)
    if datetime.now(timezone.utc) < datetime.fromisoformat(race['start_at']):
        log('発走前'); return 3
    card_raw, *_ = fetch('accessD.html?CNAME=' + race['cname'])
    rid, date = jc.race_id_from_cname(race['cname'])
    link = jc.result_link(card_raw, rid, date)
    if not link:
        log('出馬表ページに結果へのリンクがまだない'); return 3
    raw, sha, seen, url = fetch('accessS.html?CNAME=' + link)
    rows = jc.parse_result(raw, rid, source_url=url)
    m = meta(url, sha, seen, 'JRA公式レース結果を発走後に1回取得。ラベル専用で、特徴量には使わない。')
    m['data_grade'] = 'confirmed-after-event'
    with db_lock():
        if out.exists():
            log('照合済み。何もしない'); return 0
        s = Store(DB)
        try:
            p = s.load('predictions', rid + ':live')
            if not p:
                log('発走前固定がないため照合しない'); return 1
            names = {e['horse_id']: e for e in s.records('race_entries', p['as_of']) if e['race_id'] == rid}
            res = [dict(m, entry_id=f"{rid}:{r['horse_id']}", race_id=rid, horse_id=r['horse_id'], status=r['status'],
                        finish=r['finish'], number=r['number'], final=True) for r in rows]
            jc.validate_result_entries(rows, list(names.values()))
            s.import_bundle(dict(schema_version=1, dataset='real', tables=dict(race_results=res)))
            report = compare(s, p['id'])
            from .baseline import baselines
            winners = [r['horse_id'] for r in report['rows'] if r['actual_rank'] == 1]
            report['baselines'] = None if report['metrics']['post_lock_withdrawals'] else baselines(p['horses'], winners)
            temp = out.with_suffix('.tmp')
            temp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
            temp.replace(out)
        finally:
            s.close()
    mt = report['metrics']
    log(f"照合完了 Top1={mt['top1_hit']} Top3重複={mt['top3_overlap']} tau-b={mt['kendall_tau_b']} 勝ち馬予測順位={mt['winner_predicted_ranks']}")
    return build_report(key)


def build_report(key):
    from .race_reports import ensure
    race=race_info(key)
    result=ensure(W,DB/'research.duckdb',race,list(RACES.values()))
    log('PDFレポート '+json.dumps(result,ensure_ascii=False))
    return 0 if result['status']=='completed' else 3


def odds(key):
    """Observe singles around T-30 in a separate immutable file, never update forecasts."""
    race=race_info(key);out=W/'outputs'/f'odds-{key}-t30.json'
    rid,_=jc.race_id_from_cname(race['cname'])
    if out.exists():
        saved=json.loads(out.read_text(encoding='utf-8'))
        from .store import digest
        if digest(saved['observation'])!=saved['sha256'] or saved['observation']['race_id']!=rid:raise ValueError('Odds observation hash/identity mismatch')
        log('30分前オッズ記録済み');return 0
    start=datetime.fromisoformat(race['start_at'])
    if abs((start-datetime.now(timezone.utc)).total_seconds()-1800)>300:
        log('30分前オッズの観測時間外（許容±5分）。時刻を代用しない');return 3
    from .article_analysis import load_snapshot
    from .store import digest
    p=load_snapshot(DB,rid+':live')
    raw,sha,seen,url=fetch('accessD.html?CNAME='+race['cname'])
    card=jc.parse_card(raw,url)
    if card['race_id']!=rid or datetime.fromisoformat(card['start_at'])!=start:
        raise ValueError('Odds race identity/start mismatch')
    if abs((start-datetime.fromisoformat(seen)).total_seconds()-1800)>300:
        raise ValueError('Odds response outside T-30 observation window')
    if {e['horse_id']:e['number'] for e in card['entries']}!={h['id']:h['number'] for h in p['horses']}:
        raise ValueError('Odds runners differ from frozen forecast')
    import math
    if any(e['odds'] is not None and (not math.isfinite(e['odds']) or e['odds']<1) for e in card['entries']):
        raise ValueError('Invalid observed singles odds')
    observation=dict(race_id=rid,prediction_id=p['id'],snapshot_hash=digest(p),
        observed_at=seen,retrieved_at=seen,published_at=None,scheduled_at=(start-timedelta(minutes=30)).isoformat(),
        source=url,source_sha256=sha,kind='single_win_interim',
        entries=[{k:e[k] for k in ('horse_id','number','status','odds')} for e in card['entries']],
        missing=sum(e['odds'] is None for e in card['entries']),
        note='当該時刻の観測。初回公開時刻は未確認。固定予測へ逆流させない。')
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x',encoding='utf-8') as f:json.dump(dict(observation=observation,sha256=digest(observation)),f,ensure_ascii=False,indent=2,allow_nan=False)
    log(f"30分前単勝オッズ保存 {len(card['entries'])}頭、欠損{observation['missing']}、予測更新なし")
    return 0


def main():
    import argparse
    global W, DB, LOCK
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('lock', 'result', 'report', 'odds'))
    parser.add_argument('target_key')
    parser.add_argument('--workdir', type=Path, default=W)
    parser.add_argument('--data-dir', type=Path, default=DB)
    args = parser.parse_args()
    W, DB = args.workdir, args.data_dir
    LOCK = DB / 'claude-jobs.lock'
    (W / 'outputs').mkdir(exist_ok=True)
    try:
        sys.exit({'lock': lock, 'result': result, 'report': build_report, 'odds': odds}[args.command](args.target_key))
    except Exception as e:  # logged so an unattended failure is visible later
        log(f'エラー: {e!r}'); sys.exit(1)


if __name__ == '__main__':
    main()
