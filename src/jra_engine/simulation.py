"""Standalone Monte Carlo ranking simulator; ratings are not calibrated probabilities."""
from collections import Counter
import numpy as np


def simulate(horses, runs=100_000, batch_size=10_000, seed=42, pace_sigma=0.5):
    """Simulate finish orders using independent noise and a shared pace shock.

    Each horse: {"id": str, "rating": float, "pace_sensitivity": float}.
    Ratings and sensitivities must be estimated from historical data separately.
    """
    if len(horses) < 3 or len({str(h['id']) for h in horses}) != len(horses):
        raise ValueError('Need >=3 horses with unique ids')
    if runs < 1 or batch_size < 1 or pace_sigma < 0:
        raise ValueError('Invalid simulation parameters')
    ids = [str(h['id']) for h in horses]
    ratings = np.array([float(h['rating']) for h in horses])
    sensitivities = np.array([float(h.get('pace_sensitivity', 0)) for h in horses])
    if not (np.isfinite(ratings).all() and np.isfinite(sensitivities).all()):
        raise ValueError('Nonfinite horse inputs')
    rng = np.random.default_rng(seed)
    podium = np.zeros((len(horses), 3), dtype=np.int64)
    triples = Counter()
    for start in range(0, runs, batch_size):
        n = min(batch_size, runs-start)
        pace = rng.normal(0, pace_sigma, (n, 1))
        scores = ratings[None, :] + pace * sensitivities[None, :] + rng.normal(size=(n, len(horses)))
        top = np.argsort(-scores, axis=1)[:, :3]
        for place in range(3):
            podium[:, place] += np.bincount(top[:, place], minlength=len(horses))
        triples.update(map(tuple, top.tolist()))
    probabilities = [{"id": ids[i], "win": podium[i, 0]/runs,
                      "second": podium[i, 1]/runs, "third": podium[i, 2]/runs,
                      "top3": podium[i, :].sum()/runs} for i in range(len(ids))]
    trifecta = [{"horses": [ids[i] for i in t], "probability": count/runs}
                for t, count in triples.most_common(10)]
    return {"runs": runs, "seed": seed, "horses": probabilities, "trifecta_top10": trifecta,
            "warning": "Uncalibrated model: ratings are hypothetical; probabilities are not validated forecasts."}
