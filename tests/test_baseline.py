import math
import pytest
from jra_engine.baseline import baselines

def horses(wins, odds):
    return [dict(id=str(i), win=w, odds=o) for i, (w, o) in enumerate(zip(wins, odds))]

def test_uniform_is_m0():
    r = baselines(horses([.4, .3, .2, .1], [None]*4), ['0'])
    assert r['uniform']['log_loss'] == pytest.approx(math.log(4))
    assert r['uniform']['brier'] == pytest.approx(.75)

def test_model_matches_compare_formula():
    r = baselines(horses([.5, .3, .2], [None]*3), ['1'])
    assert r['model']['log_loss'] == pytest.approx(-math.log(.3))
    assert r['model']['brier'] == pytest.approx(.25 + .49 + .04)

def test_odds_takeout_is_removed():
    # 1.8/3.6/3.6 overround 1.111 -> normalized .5/.25/.25
    r = baselines(horses([1/3]*3, [1.8, 3.6, 3.6]), ['0'])
    assert r['odds']['log_loss'] == pytest.approx(math.log(2))
    assert r['odds_missing'] == 0

def test_missing_odds_are_not_filled():
    r = baselines(horses([.5, .3, .2], [2.0, None, 0.5]), ['0'])
    assert r['odds'] is None and r['odds_missing'] == 2
    assert r['model'] is not None and r['uniform'] is not None

def test_dead_heat_splits_target():
    r = baselines(horses([.5, .5], [None]*2), ['0', '1'])
    assert r['model']['brier'] == pytest.approx(0)

def test_unknown_winner_rejected():
    with pytest.raises(ValueError):
        baselines(horses([.5, .5], [None]*2), ['X'])
