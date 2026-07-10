import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nvda_predictor.dataset import make_supervised, walk_forward_splits
from nvda_predictor.features import FEATURE_COLUMNS


def _features(n=200):
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2022-01-03", periods=n)
    df = pd.DataFrame(
        rng.normal(size=(n, len(FEATURE_COLUMNS))), index=idx, columns=FEATURE_COLUMNS
    )
    df["target"] = rng.integers(0, 2, n).astype(float)
    df.loc[df.index[-1], "target"] = np.nan
    return df


def test_shapes_and_alignment():
    window = 10
    feats = _features()
    sup = make_supervised(feats, window)
    assert sup.X_seq.shape == (len(sup), window, len(FEATURE_COLUMNS))
    assert sup.X_flat.shape == (len(sup), len(FEATURE_COLUMNS))
    # last labeled row is the second-to-last date
    assert sup.dates[-1] == feats.index[-2]
    # sequence ends at the sample's own date
    np.testing.assert_allclose(sup.X_seq[-1, -1], sup.X_flat[-1])
    expected = feats.loc[sup.dates[-1], FEATURE_COLUMNS].to_numpy(dtype=np.float32)
    np.testing.assert_allclose(sup.X_flat[-1], expected)


def test_walk_forward_no_overlap_and_chronology():
    splits = list(walk_forward_splits(2000, n_folds=4, test_size=100))
    assert len(splits) == 4
    for tr, te in splits:
        assert tr.max() < te.min()  # train strictly precedes test
    all_test = np.concatenate([te for _, te in splits])
    assert len(all_test) == len(set(all_test)) == 400
    assert all_test[-1] == 1999
