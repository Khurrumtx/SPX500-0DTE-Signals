"""Central configuration for the NVDA predictor."""

from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PACKAGE_ROOT / "data"
ARTIFACTS_DIR = PACKAGE_ROOT / "artifacts"


@dataclass
class Config:
    ticker: str = "NVDA"
    start: str = "2005-01-01"

    # Sequence model (replicated from jaungiers' config.json:
    # 3x LSTM(100) + dropout 0.2, window 50; classification head instead
    # of the original linear regression head).
    window: int = 50
    lstm_units: int = 100
    dropout: float = 0.2
    learning_rate: float = 1e-3
    batch_size: int = 32
    max_epochs: int = 50
    early_stopping_patience: int = 5
    validation_fraction: float = 0.1  # tail of each training fold

    # Walk-forward evaluation
    n_folds: int = 4
    test_days_per_fold: int = 250  # ~1 trading year per fold

    # Backtest
    prob_threshold: float = 0.5
    cost_bps: float = 5.0  # one-way transaction cost in basis points

    seed: int = 42

    data_dir: Path = field(default=DATA_DIR)
    artifacts_dir: Path = field(default=ARTIFACTS_DIR)

    @property
    def cache_path(self) -> Path:
        return self.data_dir / f"{self.ticker}.csv"
