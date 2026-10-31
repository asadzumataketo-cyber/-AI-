"""Parse a saved JRA official race card (出馬表) into staging records for Codex to review.

Read-only toward every store: nothing here opens a database. Output follows the
existing import bundle (schema_version 1, dataset 'real') used by import_tenno_card.py.
"""
import hashlib
import re
from html import unescape
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
VENUES = {'01': '札幌', '02': '函館', '03': '福島', '04': '新潟', '05': '東京',
          '06': '中山', '07': '中京', '08': '京都', '09': '阪神', '10': '小倉'}


def _text(html):
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', html))).strip()


def _first(pattern, html, default=None):
    m = re.search(pattern, html, re.S)
    return m.group(1) if m else default


def race_id_from_cname(cname):
    """pw01dde01|pw01sde10 + venue(2) + year(4) + kai(2) + day(2) + race(2) + yyyymmdd -> YYYYVVKKDDRR."""
    m = re.search(r'pw01[ds]de\d\d(\d\d)(\d{4})(\d\d)(\d\d)(\d\d)(\d{8})', cname or '')
    if not m:
        return None, None
    venue, year, kai, day, race, ymd = m.groups()
    return year + venue + kai + day + race, f'{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}'


def _past(cell):
    t = _text(cell)
    if not t:
        return None
    date = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', t)
    rid, link_date = race_id_from_cname(_first(r'accessS\.html\?CNAME=([^"/]+)', cell))
    place = _text(_first(r'<div class="place">(.*?)</div>', cell, ''))
    dist = re.search(r'(\d{3,4})(芝|ダ|障)', _text(_first(r'<span class="dist">(.*?)</span>', cell, '')))
    corners = re.findall(r'<li[^>]*>(\d+)</li>', _first(r'<div class="corner_list">(.*?)</div>', cell, ''))
    f3 = re.search(r'3F\s*([\d.]+)', t)
    finish = re.fullmatch(r'(\d+)\s*着', place)
    return dict(
        race_date=f'{int(date[1]):04d}-{int(date[2]):02d}-{int(date[3]):02d}' if date else None,
        link_date=link_date, race_id=rid, race_name=_text(_first(r'<div class="name">(.*?)</div>', cell, '')), venue=_text(_first(r'<div class="rc">(.*?)</div>', cell, '')),
        class_label=_text(_first(r'<div class="r_class">(.*?)</div>', cell, '')) or _first(r'alt="([^"]+)"', _first(r'<div class="r_class">(.*?)</div>', cell, ''), None),
        finish=int(finish[1]) if finish else None, status='finished' if finish else (place or 'unknown'),
        field_size=int(_first(r'<span class="max">(\d+)', cell, 0)) or None,
        distance=int(dist[1]) if dist else None, surface={'芝': '芝', 'ダ': 'ダート', '障': '障害'}.get(dist[2]) if dist else None,
        going=_text(_first(r'<span class="condition">(.*?)</span>', cell, '')) or None,
        time=_text(_first(r'<p class="time">(.*?)</p>', cell, '')) or None,
        last3f=float(f3[1]) if f3 else None, passing_positions='-'.join(corners) or None)


def parse_card(raw, url):
    html = raw.decode('cp932')
    race_id, race_date = race_id_from_cname(url)
    if not race_id:
        raise ValueError('URLからJRAレースIDを読めません')
    head = _text(_first(r'<div class="cell date">(.*?)</div>', html, ''))
    displayed = re.search(r'(\d{4})年(\d+)月(\d+)日', head)
    if not displayed or '-'.join([displayed[1], f'{int(displayed[2]):02}', f'{int(displayed[3]):02}']) != race_date:
        raise ValueError('出馬表の表示日付とURLが一致しません')
    hm = re.search(r'発走時刻：\s*(?:<strong>)?(\d{1,2})時(\d{1,2})分', html)
    course = re.search(r'コース：\s*([\d,]+)\s*メートル\s*（(芝|ダート)', _text(_first(r'(コース：.*?）)', html, '')))
    if not (hm and course):
        raise ValueError('発走時刻・コースを読めません')
    y, mo, d = map(int, race_date.split('-'))
    start = datetime(y, mo, d, int(hm[1]), int(hm[2]), tzinfo=JST)
    entries = []
    for tr in re.findall(r'<tr>\s*<td class="waku">(.*?)</tr>', html, re.S):
        hid = _first(r'accessU\.html\?CNAME=pw01dud00(\d{10})/', tr)
        if not hid:
            raise ValueError('10桁馬IDのない出走行があります')
        jockey_cell = _first(r'<td class="jockey">(.*?)</td>', tr, '')
        number = _text(_first(r'<td class="num">(.*?)</td>', tr, ''))
        if not _first(r'alt="枠(\d)', tr) or not re.match(r'\d+', number):
            raise ValueError('枠順・馬番が未発表です（枠順確定後に取り直す）')
        status = 'scratched' if re.search(r'取消', _text(tr)[:200]) else 'excluded' if re.search(r'除外', _text(tr)[:200]) else 'active'
        entries.append(dict(
            frame=int(_first(r'alt="枠(\d)', tr)), number=int(re.match(r'\d+', number)[0]), horse_id=hid,
            name=_text(_first(r'accessU\.html\?CNAME=pw01dud00\d{10}/[^"]*">(.*?)</a>', tr, '')),
            sex_age=_text(_first(r'<p class="age">(.*?)</p>', jockey_cell, '')) or None,
            carried_weight=float(_first(r'([\d.]+)\s*(?:<span>)?kg', _first(r'<p class="weight">(.*?)</p>', jockey_cell, ''))),
            jockey=_text(_first(r'<p class="jockey">(.*?)</p>', jockey_cell, '')),
            odds=float(_first(r'<span class="num"><strong[^>]*>([\d.]+)</strong>', tr)) if _first(r'<span class="num"><strong[^>]*>([\d.]+)</strong>', tr) else None,
            status=status, past=[p for p in map(_past, re.findall(r'<td class="past[^>]*>(.*?)</td>', tr, re.S)) if p]))
    if not entries:
        raise ValueError('出走馬を読めません')
    if len({e['number'] for e in entries}) != len(entries) or len({e['horse_id'] for e in entries}) != len(entries):
        raise ValueError('馬番・馬IDが重複しています')
    venue = VENUES[race_id[4:6]]
    if venue not in head:
        raise ValueError('開催場がレースIDと一致しません')
    return dict(race_id=race_id, race_date=race_date, start_at=start.isoformat(), venue=venue,
                name=_text(_first(r'<span class="race_name">(.*?)</span>', html, '')),
                surface=course[2], distance=int(course[1].replace(',', '')), entries=entries)


def check_identities(card, registry):
    """registry: candidate horse name -> 10-digit id from the earlier registration audit."""
    out = dict(matched=[], not_in_candidates=[], mismatched=[])
    for e in card['entries']:
        known = registry.get(e['name'])
        if known is None:
            out['not_in_candidates'].append(e['horse_id'])
        elif known == e['horse_id']:
            out['matched'].append(e['horse_id'])
        else:
            out['mismatched'].append((e['name'], known, e['horse_id']))
    return out


def classify_past(card, existing):
    """existing: (horse_id, race_id) -> stored payload. Never overwrites; only sorts candidates."""
    out = dict(new=[], duplicate=[], conflict=[], hold=[], reject=[])
    for e in card['entries']:
        for p in e['past']:
            item = dict(p, horse_id=e['horse_id'], horse_name=e['name'])
            if not p['race_date'] or p['race_date'] >= card['race_date']:
                out['reject'].append(dict(item, reason='対象レース当日以降・日付不明の走は使わない'))
            elif '障害' in (p['race_name'] or ''):
                out['hold'].append(dict(item, reason='障害競走。平地モデルの対象外として保留'))
            elif not p['race_id']:
                out['hold'].append(dict(item, reason='JRAレースIDなし（地方・海外など）。JRA過去走として扱わず保留'))
            elif p['link_date'] != p['race_date']:
                out['conflict'].append(dict(item, reason='リンクの日付と表示日付が不一致'))
            elif (e['horse_id'], p['race_id']) not in existing:
                out['new'].append(item)
            else:
                old = existing[(e['horse_id'], p['race_id'])]
                core = ['finish', 'field_size', 'race_date']
                if any(old.get(k) not in (None, p[k]) for k in core):
                    out['conflict'].append(dict(item, reason='既存行と着順・頭数・日付が不一致', existing={k: old.get(k) for k in core}))
                else:
                    enrich = {k: p[k] for k in ['going'] if old.get(k) is None and p[k] is not None}
                    out['duplicate'].append(dict(item, enrich=enrich))
    return out


def card_bundle(card, raw, expected_sha, url, retrieved_at, target):
    """Race + entries for the existing /api/import format. Refuses tampered input and post-start observation."""
    sha = hashlib.sha256(raw).hexdigest()
    if sha != expected_sha:
        raise ValueError('原本HTMLのSHA-256が記録と一致しません')
    seen = datetime.fromisoformat(retrieved_at)
    if seen >= datetime.fromisoformat(card['start_at']):
        raise ValueError('発走時刻以後に取得した出馬表は事前入力に使えません')
    meta = dict(source=url, source_sha256=sha, source_sha256_scope='original HTML bytes as served (cp932)',
                available_at=retrieved_at, retrieved_at=retrieved_at, updated_at=retrieved_at, published_at=None,
                data_grade='observed-before-event',
                availability_evidence='JRA公式出馬表を1回取得し、原本HTMLを保存。取得時刻を利用可能時刻とする。初回公開時刻は未検証。')
    rid = card['race_id']
    active = [e for e in card['entries'] if e['status'] == 'active']
    race = dict(meta, race_id=rid, target_key=target['target_key'], name=card['name'], venue=card['venue'],
                surface=card['surface'], distance=card['distance'], start_at=card['start_at'],
                declared_entries=len(card['entries']), confirmed=True)
    entries = [dict(meta, entry_id=f"{rid}:{e['horse_id']}", race_id=rid, horse_id=e['horse_id'], name=e['name'],
                    number=e['number'], frame=e['frame'], jockey=e['jockey'], carried_weight=e['carried_weight'],
                    status=e['status'], official_confirmed=True, identity_verified=True,
                    identity_source=f"https://www.jra.go.jp/JRADB/accessU.html?CNAME=pw01dud00{e['horse_id']}",
                    identity_evidence='JRA公式出馬表の馬名リンクに含まれる10桁ID。')
               for e in card['entries']]
    target_row = dict(target, **meta, official_race_id=rid, confirmed=True, stage='confirmed', start_at=card['start_at'])
    return dict(schema_version=1, dataset='real', active_entries=len(active),
                tables=dict(races=[race], race_entries=entries, race_targets=[target_row]))


RESULT_STATUS = {'取消': 'scratched', '除外': 'excluded', '中止': 'dnf', '失格': 'disqualified'}


def result_link(card_raw, race_id, race_date):
    """Find this race's official result page on its own card page (the check code cannot be computed)."""
    html = card_raw.decode('cp932', 'ignore')
    for cname in re.findall(r'pw01sde(?:01|10)\d{20}/[0-9A-F]{2}', html):
        if race_id_from_cname(cname) == (race_id, race_date):
            return cname
    return None


def parse_result(raw, race_id, source_url=None):
    html = raw.decode('cp932')
    head = _text(_first(r'<div class="cell date">(.*?)</div>', html, ''))
    m = re.search(r'(\d{4})年(\d+)月(\d+)日.*?(\d+)回(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉)(\d+)日', head)
    if not m or (m[1], m[4], m[5], m[6]) != (race_id[:4], str(int(race_id[6:8])), VENUES.get(race_id[4:6]), str(int(race_id[8:10]))):
        raise ValueError('結果ページの開催がレースIDと一致しません')
    if source_url is not None:
        actual_id, actual_date = race_id_from_cname(source_url)
        shown_date = f'{m[1]}-{int(m[2]):02}-{int(m[3]):02}'
        shown_numbers = re.findall(r'race_number_main/race_num_(\d+)\.png', html)
        if actual_id != race_id or actual_date != shown_date or shown_numbers != [str(int(race_id[10:12]))]:
            raise ValueError('結果URL・日付・レース番号が一致しません')
    rows = []
    for place, num, hid in re.findall(r'<tr>\s*<td class="place">(.*?)</td>.*?<td class="num">(.*?)</td>.*?pw01dud[01]0(\d{10})', html, re.S):
        p = _text(place)
        status = 'finished' if p.isdigit() else RESULT_STATUS.get(p)
        if status is None:
            raise ValueError(f'着順欄を読めません: {p}')
        rows.append(dict(horse_id=hid, number=int(_text(num)), status=status, finish=int(p) if p.isdigit() else None))
    if not rows or len({r['horse_id'] for r in rows}) != len(rows):
        raise ValueError('結果の行が読めないか重複しています')
    return rows


def validate_result_entries(rows, entries):
    """Reject partial, extra, or swapped horses before any result DB write."""
    expected = {e['horse_id']: int(e['number']) for e in entries}
    actual = {r['horse_id']: r['number'] for r in rows}
    if len(actual) != len(rows) or expected != actual:
        raise ValueError('結果の全頭馬ID・馬番が固定時点の出走表と一致しません')
    if not any(r['status'] == 'finished' and r['finish'] == 1 for r in rows):
        raise ValueError('確定勝者がありません')
