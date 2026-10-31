"""Race-day report slides: result JSON + locked predictions + a notes file -> 16:9 HTML -> PDF (headless Edge).

python report_slides.py reports/2026-10-31/notes.json
Reads the DB read-only. No extra packages; fonts are the Windows-installed BIZ UDGothic / Noto Sans JP.
"""
import html, json, subprocess, sys, os, tempfile
from pathlib import Path
import duckdb
from .store import digest
from .baseline import baselines

ROOT = Path(__file__).resolve().parents[2]
W = Path(os.environ.get('JRA_JOB_WORKDIR', ROOT / 'data/race-jobs'))
DB = Path(os.environ.get('JRA_DATA_DIR', ROOT / 'data/real')) / 'research.duckdb'
EDGE = os.environ.get('JRA_PDF_BROWSER', r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
SERIES = [('model', 'モデル', '#2a78d6'), ('uniform', '全馬同確率', '#eb6834'), ('odds', '発走前オッズ', '#1baf7a')]
e = html.escape

CSS = """
@page { size: 1280px 720px; margin: 0 }
* { box-sizing: border-box }
body { margin: 0; font-family: 'BIZ UDPGothic', 'BIZ UDGothic', 'Noto Sans JP', 'Yu Gothic', sans-serif; color: #0b0b0b; background: #fcfcfb }
.slide { width: 1280px; height: 720px; padding: 56px 72px; page-break-after: always; position: relative; overflow: hidden; background: #fcfcfb }
.slide:last-child { page-break-after: auto }
h1 { font-size: 52px; margin: 0 0 16px; line-height: 1.2 }
h2 { font-size: 34px; margin: 0 0 28px; padding-bottom: 12px; border-bottom: 3px solid #2a78d6 }
p, li { font-size: 24px; line-height: 1.6; color: #0b0b0b }
.sub { font-size: 26px; color: #52514e }
.foot { position: absolute; left: 72px; right: 72px; bottom: 28px; font-size: 15px; color: #52514e; display: flex; justify-content: space-between }
table { border-collapse: collapse; width: 100%; font-size: 21px }
th { text-align: left; color: #52514e; font-weight: 600; border-bottom: 2px solid #d9d8d4; padding: 8px 10px }
td { border-bottom: 1px solid #e7e6e2; padding: 8px 10px }
td.n { text-align: right; font-variant-numeric: tabular-nums }
tr.hit td { background: #e9f2fc }
.cols { display: flex; gap: 40px }
.cols > div { flex: 1 }
.chips { display: flex; gap: 14px; margin-bottom: 22px }
.chip { border: 1px solid #d9d8d4; border-radius: 10px; padding: 10px 16px; min-width: 150px }
.chip b { display: block; font-size: 30px }
.chip span { font-size: 16px; color: #52514e }
.hero { display: flex; gap: 28px; margin: 24px 0 }
.hero div { flex: 1; border-left: 6px solid #2a78d6; padding: 6px 18px }
.hero b { font-size: 40px; display: block }
.hero span { font-size: 19px; color: #52514e }
.legend { display: flex; gap: 24px; font-size: 18px; color: #52514e; margin: 0 0 8px }
.legend i { display: inline-block; width: 14px; height: 14px; border-radius: 3px; margin-right: 6px; vertical-align: -1px }
"""


def load(notes, workdir=None, database=None):
    workdir, database = Path(workdir or W), Path(database or DB)
    if (database.parent/'claude-jobs.lock').exists():
        raise RuntimeError('DB writer busy; report deferred')
    races = []
    with duckdb.connect(str(database), read_only=True) as db:
        for r in notes['races']:
            rep = json.loads((workdir / 'outputs' / f"result-{r['target_key']}.json").read_text(encoding='utf-8'))
            row = db.execute('SELECT payload,hash FROM predictions WHERE id=?', [rep['prediction_id']]).fetchone()
            if not row:
                raise ValueError('Report forecast missing')
            p = json.loads(row[0])
            if digest(p) != row[1] or rep['race_id'] != p['race']['race_id']:
                raise ValueError('Report snapshot identity/hash mismatch')
            # The result JSON adds baselines to the immutable stored comparison.
            core = {k:v for k,v in rep.items() if k != 'baselines'}
            comparisons = db.execute('SELECT payload,hash FROM backtest_results WHERE id LIKE ?', [rep['prediction_id']+':%']).fetchall()
            if not any(digest(json.loads(v)) == h == digest(core) for v,h in comparisons):
                raise ValueError('Result report differs from stored comparison')
            winners = [x['horse_id'] for x in rep['rows'] if x['actual_rank'] == 1]
            expected = None if rep['metrics']['post_lock_withdrawals'] else baselines(p['horses'], winners)
            if rep.get('baselines') != expected:
                raise ValueError('Report baseline differs from frozen inputs')
            races.append(dict(r, report=rep, prediction=p))
    if not races or len(races) > 2:
        raise ValueError('A report must contain one or two completed races')
    return races


def chart(races):
    """Grouped horizontal bars of Log Loss (lower is better); values labeled on every bar (contrast relief)."""
    rows = [(r['short'], k, label, color, (r['report']['baselines'] or {}).get(k) or {}) for r in races for k, label, color in SERIES]
    top = max([float(v.get('log_loss') or 0) for *_, v in rows]+[1.]) * 1.15
    x0, width, bar, gap, group = 190, 820, 26, 2, 34
    y, out = 10, []
    for i, (race, k, label, color, v) in enumerate(rows):
        if i % 3 == 0:
            if i:
                y += group
            out.append(f'<text x="0" y="{y + 52}" font-size="22" fill="#0b0b0b" font-weight="600">{e(race)}</text>')
        if v:
            w = width * v['log_loss'] / top
            out.append(f'<rect x="{x0}" y="{y}" width="{w:.1f}" height="{bar}" rx="4" fill="{color}"><title>{e(label)} {v["log_loss"]:.2f}</title></rect>')
            out.append(f'<text x="{x0 + w + 10:.1f}" y="{y + 19}" font-size="18" fill="#0b0b0b">{v["log_loss"]:.2f}</text>')
        else:
            out.append(f'<text x="{x0}" y="{y + 19}" font-size="18" fill="#52514e">{e(label)}：オッズ欠損のため比較なし</text>')
        y += bar + gap + 6
    out.append(f'<line x1="{x0}" y1="0" x2="{x0}" y2="{y}" stroke="#a8a7a2" stroke-width="1"/>')
    legend = ''.join(f'<span><i style="background:{c}"></i>{e(l)}</span>' for _, l, c in SERIES)
    return f'<div class="legend">{legend}</div><svg width="1130" height="{y + 10}" role="img" aria-label="Log Loss比較">{"".join(out)}</svg>'


def race_slide(r, foot):
    rep, p = r['report'], r['prediction']
    m = rep['metrics']
    by = {row['horse_id']: row for row in rep['rows']}
    actual_top = sorted([row for row in rep['rows'] if row['actual_rank']], key=lambda x: x['actual_rank'])[:5]
    pred = p['horses'][:5]

    def tr(name, num, a, b, hit):
        return f'<tr class="{"hit" if hit else ""}"><td class="n">{a}</td><td>{num} {e(name)}</td><td class="n">{b}</td></tr>'
    left = ''.join(tr(h['name'], h['number'], h['predicted_rank'], by[h['id']]['actual_rank'] or by[h['id']]['status'],
                      h['predicted_rank'] <= 3 and (by[h['id']]['actual_rank'] or 99) <= 3) for h in pred)
    right = ''.join(tr(x['name'], x['number'], x['actual_rank'], x['predicted_rank'], x['predicted_rank'] <= 3) for x in actual_top)
    chips = [('予想1位の勝ち', '○' if m['top1_hit'] else '×'), ('上位3頭の的中', f"{m['top3_overlap']}頭"),
             ('勝ち馬の予想順位', '・'.join(map(str, m['winner_predicted_ranks'])) + '位'),
             ('全頭の順位一致', f"{m['kendall_tau_b']:+.2f}" if m['kendall_tau_b'] is not None else '—')]
    chip_html = ''.join(f'<div class="chip"><b>{v}</b><span>{e(k)}</span></div>' for k, v in chips)
    return f'''<section class="slide"><h2>{e(r['title'])}</h2><div class="chips">{chip_html}</div>
<div class="cols"><div><table><tr><th>予想</th><th>馬</th><th>実際</th></tr>{left}</table></div>
<div><table><tr><th>着順</th><th>馬</th><th>予想</th></tr>{right}</table></div></div>
<p class="sub" style="font-size:19px;margin-top:16px">水色の行＝予想上位3頭と実際の3着以内が重なった馬。全頭の順位一致は +1 が完全一致、0 は順位の関連が弱い。</p>{foot}</section>'''


def build(notes_path, workdir=None, database=None, output_dir=None, browser=None):
    notes = json.loads(Path(notes_path).read_text(encoding='utf-8'))
    races = load(notes, workdir, database)
    foot = f'<div class="foot"><span>{e(notes["footer"])}</span><span>研究用の記録。馬券購入の推奨ではない。</span></div>'
    hero = ''.join(f'<div><b>{e(h["value"])}</b><span>{e(h["label"])}</span></div>' for h in notes['hero'])
    ul = lambda xs: '<ul>' + ''.join(f'<li>{e(x)}</li>' for x in xs) + '</ul>'
    slides = [f'<section class="slide" style="display:flex;flex-direction:column;justify-content:center"><h1>{e(notes["title"])}</h1><p class="sub">{e(notes["subtitle"])}</p>{foot}</section>',
              f'<section class="slide"><h2>まとめ</h2><div class="hero">{hero}</div>{ul(notes["summary"])}{foot}</section>']
    slides += [race_slide(r, foot) for r in races]
    slides.append(f'<section class="slide"><h2>確率の当たり具合（Log Loss・低いほど良い）</h2>{chart(races)}<p class="sub" style="font-size:19px">{e(notes["chart_note"])}</p>{foot}</section>')
    for sec in notes['sections']:
        slides.append(f'<section class="slide"><h2>{e(sec["title"])}</h2>{ul(sec["items"])}{foot}</section>')
    folder = Path(output_dir or Path(notes_path).resolve().parent)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder/'slides.html'
    out.write_text(f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>{e(notes["title"])}</title><style>{CSS}</style></head><body>{"".join(slides)}</body></html>', encoding='utf-8')
    pdf = out.with_suffix('.pdf')
    # A private browser profile avoids taking over an already running Edge session.
    with tempfile.TemporaryDirectory(prefix='pdf-profile-', dir=folder, ignore_cleanup_errors=True) as profile:
        temporary = folder/'slides.pending.pdf'
        subprocess.run([str(browser or EDGE), '--headless=new', '--disable-gpu', '--no-first-run',
                        '--no-pdf-header-footer', f'--user-data-dir={profile}',
                        f'--print-to-pdf={temporary}', out.as_uri()], check=True, capture_output=True, timeout=60)
        if not temporary.exists() or not temporary.read_bytes().startswith(b'%PDF-'):
            raise RuntimeError('Browser did not produce a valid PDF')
        temporary.replace(pdf)
    return dict(pdf=str(pdf), html=str(out), slides=len(slides))


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('notes',type=Path)
    parser.add_argument('--workdir',type=Path);parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--output-dir',type=Path);args=parser.parse_args()
    print(json.dumps(build(args.notes,args.workdir,args.data_dir/'research.duckdb' if args.data_dir else None,args.output_dir),ensure_ascii=False))
