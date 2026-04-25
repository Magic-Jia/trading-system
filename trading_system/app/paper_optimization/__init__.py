"""Paper optimization fact collection."""

from .collector import collect_signal_facts
from .metrics import write_daily_metrics_and_health_report
from .models import PaperSignalFact, PaperTradeOutcome
from .outcomes import collect_trade_outcomes

__all__ = [
    "PaperSignalFact",
    "PaperTradeOutcome",
    "collect_signal_facts",
    "collect_trade_outcomes",
    "write_daily_metrics_and_health_report",
]
