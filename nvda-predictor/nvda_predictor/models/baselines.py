"""Classical baselines the LSTM must beat to justify its complexity.

Most high-star prediction repos skip this comparison; we keep it because a
direction classifier that can't beat logistic regression on the same
features isn't adding value.
"""

from __future__ import annotations

from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression


def make_baselines(seed: int) -> dict[str, object]:
    return {
        "logistic": LogisticRegression(max_iter=2000, C=0.5),
        "random_forest": RandomForestClassifier(
            n_estimators=400,
            max_depth=6,
            min_samples_leaf=20,
            random_state=seed,
            n_jobs=-1,
        ),
        "gradient_boosting": GradientBoostingClassifier(
            n_estimators=300,
            max_depth=3,
            learning_rate=0.05,
            subsample=0.8,
            random_state=seed,
        ),
    }
