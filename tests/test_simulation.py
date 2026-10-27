import pytest
from jra_engine import simulate

HORSES = [dict(id=str(i), rating=float(i)/10, pace_sensitivity=(-1)**i) for i in range(8)]

def test_reproducible():
    assert simulate(HORSES, runs=2000, seed=7) == simulate(HORSES, runs=2000, seed=7)

def test_probability_consistency():
    result = simulate(HORSES, runs=2500)
    for field in ('win', 'second', 'third'):
        assert sum(x[field] for x in result['horses']) == pytest.approx(1)
    for x in result['horses']:
        assert x['top3'] == pytest.approx(x['win'] + x['second'] + x['third'])
    assert len(result['trifecta_top10']) == 10

def test_invalid():
    with pytest.raises(ValueError):
        simulate(HORSES[:2])
