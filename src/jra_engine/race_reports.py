"""Resumeable daily PDF reports; result JSON and operator notes remain authoritative."""
import hashlib
import json
from pathlib import Path
from .store import digest
from . import report_slides


def make_notes(races, reports, date):
    complete = [r for r in races if r['target_key'] in reports]
    hits = sum(reports[r['target_key']]['metrics']['top1_hit'] for r in complete)
    overlap = sum(reports[r['target_key']]['metrics']['top3_overlap'] for r in complete)
    return dict(generated_by='jra_engine.race_reports', title='JRA予想 結果検証',
        subtitle=f'{date}／固定済み予測と確定結果の比較',
        footer='研究用・モデル未校正／独立した発走前時刻認証は未確認',
        hero=[dict(value=str(len(complete)),label='照合済みレース'),dict(value=f'{hits}/{len(complete)}',label='予想1位の優勝'),dict(value=f'{overlap}/{3*len(complete)}',label='予想上位3頭の重複')],
        summary=['発走前に固定した予測と確定結果を比較。予測原本は変更していない。','少数レースの結果は予測精度や利益優位の証明ではない。'],
        races=[dict(target_key=r['target_key'],short=r['name'],title=r['name']) for r in complete],
        chart_note='低いほど良い指標。オッズ基準には固定予測に含まれた時点の値のみを使う。欠損時は比較しない。',
        sections=[dict(title='検証条件と不足',items=['独立した発走前時刻認証は未確認。A級の独立認定とは区別する。','取消・除外が固定後に発生した場合、確率評価は算出せず順位照合を残す。','理由の仮説や解説はnotes.jsonへ追記できる。モデル係数の後出し変更はしない。'])])


def ensure(workdir, database, race, configured_races):
    workdir, database = Path(workdir), Path(database)
    date = race['target_key'][:10]
    same_day = [r for r in configured_races if r['target_key'][:10] == date]
    reports = {}
    for r in same_day:
        path = workdir/'outputs'/('result-'+r['target_key']+'.json')
        if path.exists():reports[r['target_key']] = json.loads(path.read_text(encoding='utf-8'))
    if not reports:return dict(status='pending',reason='No completed comparisons')
    folder = workdir/'reports'/date;folder.mkdir(parents=True,exist_ok=True)
    notes_path = folder/'notes.json'
    notes = json.loads(notes_path.read_text(encoding='utf-8')) if notes_path.exists() else None
    if notes is None or notes.get('generated_by') == 'jra_engine.race_reports':
        notes = make_notes(same_day,reports,date)
        temp=folder/'notes.pending.json';temp.write_text(json.dumps(notes,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(notes_path)
    # Preserve human notes, including their selection; await any listed unfinished race.
    if any(r['target_key'] not in reports for r in notes['races']):
        return dict(status='pending',reason='Operator notes include a race not compared yet')
    verified = report_slides.load(notes,workdir,database)
    fingerprint = digest([notes,[(r['report'],digest(r['prediction'])) for r in verified]])
    # Keep manual slides.pdf and older generated versions untouched.
    target = folder/'generated'/fingerprint
    manifest = target/'report.json'
    if manifest.exists():
        old=json.loads(manifest.read_text(encoding='utf-8'));pdf=Path(old['pdf'])
        if pdf.exists() and hashlib.sha256(pdf.read_bytes()).hexdigest() == old['pdf_sha256']:
            return dict(old,cached=True)
    result=report_slides.build(notes_path,workdir,database,target)
    result.update(status='completed',fingerprint=fingerprint,notes=str(notes_path),
                  pdf_sha256=hashlib.sha256(Path(result['pdf']).read_bytes()).hexdigest())
    temp=target/'report.pending.json';temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(manifest)
    return dict(result,cached=False)
