import hashlib
import pytest
from jra_engine import jra_card as jc

URL = 'https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0105202604101120261031/C6'

def past(date, venue, cname, cls, place, field, gate, dist, cond, f3='3F 35.0'):
    link = f'<a href="/JRADB/accessS.html?CNAME={cname}/1E">R</a>' if cname else 'R'
    return f'''<td class="past p1"><div class="date">{date}</div><div class="rc">{venue}</div>
<div class="race_line"><div class="name">{link}</div><div class="r_class"><span class="grade_icon no_grade">{cls}</span></div></div>
<div class="place">{place}<span>着</span></div><div class="num"><span class="max">{field}<span>頭</span></span><span class="gate">{gate}<span>番</span></span></div>
<div class="jockey">騎手</div><div class="weight">57.0<span>kg</span></div><span class="dist">{dist}</span><p class="time">1:36.0</p>
<span class="condition">{cond}</span><div class="corner_list"><ul><li>3</li><li>2</li></ul></div><div class="f3">{f3}</div></td>'''

def row(frame, num, hid, name, pasts=''):
    return f'''<tr><td class="waku"><img src="/JRADB/img/waku/{frame}.png" alt="枠{frame}白" /></td><td class="num">{num}</td>
<td class="horse"><div class="name"><a href="/JRADB/accessU.html?CNAME=pw01dud00{hid}/EA">{name}</a></div></td>
<td class="jockey"><p class="age">牡4/鹿</p><p class="weight">56.0kg</p><p class="jockey"><a>騎手 太郎</a></p></td>{pasts}</tr>'''

def page(rows):
    return f'''<html><div class="cell date">2026年10月31日（土曜） 4回東京10日</div><div class="cell time"> 発走時刻：<strong>15時30分</strong></div>
<span class="race_name">テストステークス</span><div class="cell course">コース：<span>1,600</span>メートル（ダート・左）</div>
<table><tbody>{rows}</tbody></table></html>'''.encode('cp932')

GOOD = page(row(1, 1, '2022100001', '馬A', past('2026年9月1日', '東京', 'pw01sde1005202604010520260901', 'OP', '2', '16', '3', '1600ダ', '良'))
            + row(2, 2, '2022100002', '馬B', past('2026年5月3日', '名古', None, '', '9', '12', '', '2100ダ', '重')))

def test_parse_card():
    c = jc.parse_card(GOOD, URL)
    assert c['race_id'] == '202605041011' and c['venue'] == '東京' and c['surface'] == 'ダート'
    assert c['distance'] == 1600 and c['start_at'] == '2026-10-31T15:30:00+09:00'
    e = c['entries'][0]
    assert (e['frame'], e['number'], e['horse_id'], e['name'], e['carried_weight']) == (1, 1, '2022100001', '馬A', 56.0)
    p = e['past'][0]
    assert p['race_id'] == '202605040105' and p['finish'] == 2 and p['field_size'] == 16 and p['going'] == '良'
    assert p['race_date'] == '2026-09-01' and p['surface'] == 'ダート' and p['distance'] == 1600

def test_name_after_icon_and_red_odds():
    r = row(1, 1, '2022100001', '馬A', past('2026年9月1日', '東京', 'pw01sde1005202604010520260901', 'OP', '2', '16', '3', '1600ダ', '良'))
    r = r.replace('<div class="name"><a', '<div class="name"><span class="horse_icon"><img alt="マルガイ"></span><a')
    r = r.replace('</a></div></td>', '</a></div><span class="num"><strong class="red">4.5</strong></span></td>', 1)
    e = jc.parse_card(page(r), URL)['entries'][0]
    assert e['name'] == '馬A' and e['odds'] == 4.5

def test_missing_horse_id_rejected():
    bad = page(row(1, 1, 'XXXX', '馬A'))
    with pytest.raises(ValueError):
        jc.parse_card(bad, URL)

def test_card_before_draw_rejected():
    blank = page(row(1, 1, '2022100001', '馬A').replace('<img src="/JRADB/img/waku/1.png" alt="枠1白" />', '').replace('<td class="num">1</td>', '<td class="num"></td>'))
    with pytest.raises(ValueError, match='枠順'):
        jc.parse_card(blank, URL)

def test_duplicate_numbers_rejected():
    bad = page(row(1, 1, '2022100001', '馬A') + row(1, 1, '2022100002', '馬B'))
    with pytest.raises(ValueError):
        jc.parse_card(bad, URL)

def card():
    return jc.parse_card(GOOD, URL)

def test_identity_check():
    reg = {'馬A': '2022100001', '馬B': '2099999999'}
    r = jc.check_identities(card(), reg)
    assert r['matched'] == ['2022100001'] and r['mismatched'] == [('馬B', '2099999999', '2022100002')]

def test_classify_past_runs():
    existing = {('2022100001', '202605040105'): dict(finish=2, field_size=16, race_date='2026-09-01', going=None)}
    out = jc.classify_past(card(), existing)
    assert out['duplicate'][0]['enrich'] == {'going': '良'}
    assert out['hold'][0]['reason'].startswith('JRAレースIDなし')
    existing[('2022100001', '202605040105')]['finish'] = 5
    out = jc.classify_past(card(), existing)
    assert out['conflict'] and not out['duplicate']

def test_link_date_mismatch_and_jump_race_not_staged():
    cells = (past('2026年9月2日', '東京', 'pw01sde1005202604010520260901', 'OP', '2', '16', '3', '1600ダ', '良')
             + past('2026年1月24日', '小倉', 'pw01sde1010202601010520260124', '', '7', '11', '2', '2860芝', '良').replace('>R<', '>障害未勝利<'))
    out = jc.classify_past(jc.parse_card(page(row(1, 1, '2022100001', '馬A', cells)), URL), {})
    assert out['conflict'][0]['reason'].startswith('リンクの日付') and out['hold'][0]['reason'].startswith('障害')
    assert not out['new']

def test_target_race_result_never_staged():
    leak = page(row(1, 1, '2022100001', '馬A', past('2026年10月31日', '東京', 'pw01sde1005202604101120261031', 'OP', '1', '16', '1', '1600ダ', '良')))
    out = jc.classify_past(jc.parse_card(leak, URL), {})
    assert not out['new'] and out['reject'][0]['reason'].startswith('対象レース当日以降')

def test_bundle_provenance_and_future():
    raw_sha = hashlib.sha256(GOOD).hexdigest()
    b = jc.card_bundle(card(), GOOD, raw_sha, URL, '2026-10-30T17:50:10+00:00', target={'target_key': 'k'})
    e = b['tables']['race_entries'][0]
    assert e['source_sha256'] == raw_sha and e['published_at'] is None and e['available_at'] == e['retrieved_at']
    assert b['tables']['races'][0]['declared_entries'] == 2
    with pytest.raises(ValueError):
        jc.card_bundle(card(), GOOD, '0' * 64, URL, '2026-10-30T17:50:10+00:00', target={'target_key': 'k'})
    with pytest.raises(ValueError):  # a card observed after the start cannot be pre-race input
        jc.card_bundle(card(), GOOD, raw_sha, URL, '2026-10-31T07:00:00+00:00', target={'target_key': 'k'})


def result_page(rows):
    body = ''.join(f'<tr> <td class="place">{p}</td> <td class="waku"></td> <td class="num">{n}</td> <td class="horse"><a href="/JRADB/accessU.html?CNAME=pw01dud10{h}/0A">x</a></td></tr>' for p, n, h in rows)
    return f'<div class="cell date">2026年10月31日（土曜） 4回東京10日</div><table><tbody>{body}</tbody></table>'.encode('cp932')


def test_parse_result_statuses_and_dead_heat():
    rows = jc.parse_result(result_page([('1', 3, '2022100001'), ('1', 5, '2022100002'), ('中止', 1, '2022100003'), ('取消', 2, '2022100004')]), '202605041011')
    assert [r['finish'] for r in rows] == [1, 1, None, None]
    assert [r['status'] for r in rows] == ['finished', 'finished', 'dnf', 'scratched']


def test_parse_result_rejects_unknown_place():
    with pytest.raises(ValueError):
        jc.parse_result(result_page([('?', 3, '2022100001')]), '202605041011')


def test_result_link_only_for_this_race():
    card = b'<a href="/JRADB/accessS.html?CNAME=pw01sde1005202604101120261031/7C">r</a><a href="/JRADB/accessS.html?CNAME=pw01sde1005202604101020261031/11">x</a>'
    assert jc.result_link(card, '202605041011', '2026-10-31') == 'pw01sde1005202604101120261031/7C'
    assert jc.result_link(card, '202605041012', '2026-10-31') is None


def test_schedule_works_back_from_post_time():
    from datetime import datetime, timezone
    from jra_engine import race_scheduler as scheduler
    races = [dict(target_key='k', start_at='2026-10-31T15:30:00+09:00')]
    jobs = scheduler.plan(races, datetime(2026, 10, 31, 1, 0, tzinfo=timezone.utc))  # 10:00 JST
    times = [(j['cmd'], j['at'].strftime('%H:%M')) for j in jobs]
    assert {t for cmd,t in times if cmd=='lock'} == {'13:30','14:00','14:30'}
    assert ('odds','15:00') in times
    assert ('result', '16:00') in times and ('result', '19:30') in times


@pytest.mark.parametrize('prefix', ['01', '10'])
def test_result_link_current_and_history_formats_match_full_identity(prefix):
    # The live Oct 31 card uses sde01; past-performance links use sde10.
    expected = f'pw01sde{prefix}05202604101120261031/82'
    raw = (f'<a href="/JRADB/accessS.html?CNAME=pw01sde1005202604101020261031/11">other race</a>'
           f'<a href="/JRADB/accessS.html?CNAME=pw01sde0105202604101120261030/82">wrong date</a>'
           f'<a href="/JRADB/accessS.html?CNAME={expected}">result</a>').encode()
    assert jc.result_link(raw, '202605041011', '2026-10-31') == expected
    assert jc.result_link(raw, '202608041011', '2026-10-31') is None
    assert jc.result_link(raw, '202605041011', '2026-10-29') is None
