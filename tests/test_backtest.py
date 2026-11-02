import math
import pytest
from jra_engine.backtest import evaluate, score_race, tau_b, load_races


def race(rid='202605010101', start='2026-01-05T05:00:00+00:00', as_of='2026-01-05T03:00:00+00:00', grade='A', odds=(2., 4., 8., 16.), finish=(1, 2, 3, 4), status=None):
    wins = (.4, .3, .2, .1)
    horses = [dict(id=str(i), win=wins[i], top3=min(1., wins[i] * 2.4), average_rank=i + 1., predicted_rank=i + 1, odds=odds[i] if odds else None) for i in range(4)]
    return dict(race_id=rid, start_at=start, as_of=as_of, grade=grade, horses=horses, finish={str(i): finish[i] for i in range(4)}, status=status or {},
                trifecta_top=['0', '1', '2'])


def test_tau_b_perfect_reverse_and_tie():
    assert tau_b([1, 2, 3], [1, 2, 3]) == pytest.approx(1)
    assert tau_b([1, 2, 3], [3, 2, 1]) == pytest.approx(-1)
    assert tau_b([1, 2, 3], [1, 1, 2]) == pytest.approx(2 / math.sqrt(2 * 3))


def test_score_race_uses_only_recorded_values():
    s = score_race(race(finish=(2, 1, 3, 4)))
    assert s['top1_hit'] == 0 and s['top3_overlap'] == 3 and s['exact_top3'] == 0 and s['winner_rank'] == 2
    assert s['log_loss']['model'] == pytest.approx(-math.log(.3))
    assert s['log_loss']['uniform'] == pytest.approx(math.log(4))
    assert s['log_loss']['odds'] is not None and s['trio_hit'] == 1


def test_missing_odds_are_not_scored_or_paid():
    s = score_race(race(odds=None))
    assert s['log_loss']['odds'] is None and s['win_bet_return'] is None and s['favourite_in_top3'] is None


def test_win_bet_return_uses_recorded_odds():
    assert score_race(race())['win_bet_return'] == 2.
    assert score_race(race(finish=(2, 1, 3, 4)))['win_bet_return'] == 0.


def test_withdrawal_excludes_probability_scores_only():
    s = score_race(race(finish=(None, 1, 2, 3), status={'0': 'scratched'}))
    assert s['log_loss'] is None and s['brier'] is None and s['top1_hit'] == 0


def test_leak_and_missing_results_are_skipped_with_reason():
    out = evaluate([race(as_of='2026-01-05T05:00:00+00:00'), race(rid='2', finish=(1, 2, None, None))])
    assert out['races_scored'] == 0 and [s['reason'] for s in out['skipped']] == ['as_of_not_before_start', 'results_incomplete']


def test_duplicate_race_rejected():
    with pytest.raises(ValueError):
        evaluate([race(), race()])


def test_split_is_by_time_and_grades_are_separate():
    out = evaluate([race(rid='1'), race(rid='2', start='2026-02-05T05:00:00+00:00', as_of='2026-02-05T03:00:00+00:00'),
                    race(rid='3', start='2026-02-06T05:00:00+00:00', as_of='2026-02-06T03:00:00+00:00', grade='C')], validation_from='2026-02-01')
    assert out['groups']['development/A']['races'] == 1
    assert out['groups']['validation/A']['races'] == 1
    assert out['groups']['validation/C']['races'] == 1


def test_verdict_needs_enough_races_on_grade_a_validation():
    out = evaluate([race()], validation_from='2026-01-01', min_validation_races=2)
    assert out['verdict']['status'] == 'insufficient_races' and out['verdict']['races'] == 1


def test_verdict_pass_and_fail():
    days = [f'2026-01-{d:02d}' for d in range(1, 21)]
    def many(finish):
        return [race(rid=str(i), start=d + 'T05:00:00+00:00', as_of=d + 'T03:00:00+00:00', finish=finish) for i, d in enumerate(days)]
    good = many((1, 2, 3, 4))
    for r in good:
        for h, w, o in zip(r['horses'], (.9, .05, .03, .02), (2., 3., 4., 5.)):
            h['win'], h['odds'] = w, o
    assert evaluate(good, validation_from='2026-01-01', min_validation_races=20)['verdict']['status'] == 'passed'
    assert evaluate(good, validation_from='2026-01-01', min_validation_races=20, min_validation_days=21)['verdict']['status'] == 'insufficient_races'
    bad = many((4, 3, 2, 1))
    assert evaluate(bad, validation_from='2026-01-01', min_validation_races=20)['verdict']['status'] == 'failed'


def test_calibration_counts_every_horse_once():
    out = evaluate([race()])
    bins = out['groups']['all/A']['calibration']['top3']
    assert sum(b['n'] for b in bins) == 4


def test_favourite_and_longshot_subgroups():
    upset = race(rid='2', start='2026-01-06T05:00:00+00:00', as_of='2026-01-06T03:00:00+00:00', odds=(2., 4., 8., 30.), finish=(4, 1, 2, 3))
    group = evaluate([race(), upset])['groups']['all/A']
    assert group['favourite_missed_top3']['races'] == 1
    assert group['longshot_in_top3']['races'] == 1


def test_load_races_prefers_live_excludes_exploration_and_reads_results(tmp_path):
    duckdb = pytest.importorskip('duckdb')
    path = tmp_path / 'r.duckdb'
    db = duckdb.connect(str(path))
    db.execute('CREATE TABLE predictions(id VARCHAR, created_at VARCHAR, payload VARCHAR, hash VARCHAR)')
    db.execute('CREATE TABLE race_results(record_key VARCHAR, available_at VARCHAR, source VARCHAR, retrieved_at VARCHAR, updated_at VARCHAR, payload VARCHAR, hash VARCHAR)')
    def pred(pid, rid, mode, created):
        import json
        body = dict(id=pid, mode=mode, as_of='2026-01-05T03:00:00+00:00', race=dict(race_id=rid, start_at='2026-01-05T05:00:00+00:00'),
                    horses=[dict(id=str(i), win=.5, top3=1., average_rank=1., predicted_rank=i + 1, odds=None) for i in range(2)], trifecta_top10=[])
        db.execute('INSERT INTO predictions VALUES (?,?,?,?)', [pid, created, json.dumps(body), 'x'])
    pred('R:live', 'R', 'live', '1'); pred('R:replay:a', 'R', 'retrospective', '2'); pred('E:replay:a', '202608040711', 'retrospective', '3')
    pred('M:replay:old', 'M', 'retrospective', '1'); pred('M:replay:new', 'M', 'retrospective', '2')
    import json
    def result(horse, at='2026-01-05T06:00:00+00:00', **fields):
        db.execute('INSERT INTO race_results VALUES (?,?,?,?,?,?,?)', [f'R:{horse}', at, 's', 'r', 'u', json.dumps(dict(race_id='R', horse_id=horse, available_at=at, **fields)), 'x'])
    result('0', finish=1, status='finished', final=True)
    result('1', finish=2, status='disqualified', final=True)   # a stored place must not count
    result('2', finish=1, status='finished', final=False)      # provisional
    result('3', at='2026-01-05T04:00:00+00:00', finish=1, status='finished', final=True)  # published before the start
    db.close()
    races = {r['race_id']: r for r in load_races(path)}
    assert set(races) == {'R', 'M'}
    assert races['R']['grade'] == 'A' and races['R']['finish']['0'] == 1
    assert races['R']['finish'] == {'0': 1, '1': None} and races['R']['status']['1'] == 'disqualified'
    assert races['M']['grade'] == 'C' and races['M']['prediction_id'] == 'M:replay:new'


def test_calibration_bins_every_probability_including_float_edges():
    r = race()
    for h, p in zip(r['horses'], (.6, .3, 1., .7)):
        h['top3'] = p
    bins = evaluate([r])['groups']['all/A']['calibration']['top3']
    assert [b['range'] for b in bins] == ['0.3-0.4', '0.6-0.7', '0.7-0.8', '0.9-1.0'] and sum(b['n'] for b in bins) == 4


def test_winner_not_in_forecast_is_skipped_not_fatal():
    r = race(finish=(2, 3, 4, None), status={'3': 'dnf'})
    r['finish']['other'] = 1
    assert evaluate([r])['skipped'] == [dict(race_id=r['race_id'], reason='no_winner')]


def test_scratched_top_pick_is_refunded():
    assert score_race(race(finish=(None, 1, 2, 3), status={'0': 'scratched'}))['win_bet_return'] == 1.


def test_leak_check_compares_instants_not_text():
    r = race(as_of='2026-01-05T14:00:00+09:00', start='2026-01-05T05:30:00+00:00')  # 05:00 UTC, before the start
    assert evaluate([r])['races_scored'] == 1
    r = race(as_of='2026-01-05T15:00:00+09:00', start='2026-01-05T05:30:00+00:00')  # 06:00 UTC, after the start
    assert evaluate([r])['skipped'][0]['reason'] == 'as_of_not_before_start'
