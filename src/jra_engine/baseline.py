"""Model vs. M0 uniform vs. odds-only baseline, scored on the same winner (Issue #6).

Odds must be the values observed at the prediction's as_of (the sealed snapshot
only carries those); never pass final or post-race odds here.
A handful of races cannot show that one model beats another.
"""
import math


def _score(probs, target):
    return dict(log_loss=-sum(t*math.log(max(probs[h], 1e-12)) for h, t in target.items() if t),
                brier=sum((probs[h]-target[h])**2 for h in probs))


def baselines(horses, winners):
    """horses: [{"id", "win", "odds"}] for every runner; winners: ids placed 1st (dead heats allowed).

    Like compare(), skip races with post-lock withdrawals: the sample space changed.
    """
    ids = [str(h['id']) for h in horses]
    winners = {str(w) for w in winners}
    if not winners or not winners <= set(ids):
        raise ValueError('winners must be runners in the snapshot')
    target = {h: (1/len(winners) if h in winners else 0.) for h in ids}
    model = {str(h['id']): float(h['win']) for h in horses}
    uniform = {h: 1/len(ids) for h in ids}
    # Missing or impossible odds are not filled in: the odds baseline is skipped instead.
    bad = [h for h in horses if isinstance(h.get('odds'), bool) or not isinstance(h.get('odds'), (int, float)) or not math.isfinite(h['odds']) or not h['odds'] >= 1.0]
    odds = None
    if not bad:
        inverse = {str(h['id']): 1/float(h['odds']) for h in horses}
        total = sum(inverse.values())  # dividing by the overround removes the takeout
        odds = _score({h: v/total for h, v in inverse.items()}, target)
    return dict(model=_score(model, target), uniform=_score(uniform, target), odds=odds,
                odds_missing=len(bad), runners=len(ids))
