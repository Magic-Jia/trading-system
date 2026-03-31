from __future__ import annotations

from typing import Any, Mapping


def render_regime_scorecard(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    by_regime = dict(experiment.get("by_regime", {}))
    best_regime = None
    best_return = None
    worst_regime = None
    worst_return = None
    for label, payload in by_regime.items():
        current = float(dict(payload.get("forward_return_by_window", {})).get("3d", 0.0))
        if best_return is None or current > best_return:
            best_regime, best_return = label, current
        if worst_return is None or current < worst_return:
            worst_regime, worst_return = label, current

    regimes_with_samples = len(by_regime)
    promotion_pass = regimes_with_samples >= 2 and (best_return or 0.0) > 0 and (worst_return or 0.0) < 0
    summary = (
        f"{best_regime} leads forward return dispersion while {worst_regime} stays weakest"
        if promotion_pass
        else "regime separation is not yet strong enough for promotion"
    )

    return {
        "metadata": {
            "experiment_name": experiment_name,
            "dataset_root": metadata.get("dataset_root"),
            "baseline_name": metadata.get("baseline_name"),
            "variant_name": metadata.get("variant_name"),
            "sample_period": metadata.get("sample_period"),
        },
        "key_metrics": {
            "snapshot_count": dict(experiment.get("metadata", {})).get("snapshot_count", 0),
            "regimes_covered": regimes_with_samples,
            "best_regime_3d": best_regime,
            "best_regime_3d_return": best_return or 0.0,
            "worst_regime_3d": worst_regime,
            "worst_regime_3d_return": worst_return or 0.0,
        },
        "decision_summary": {
            "decision": "保留" if promotion_pass else "暂缓，等更多样本",
            "summary": summary,
        },
        "promotion_gate": {
            "status": "pass" if promotion_pass else "hold",
            "checks": {
                "has_multiple_regimes": regimes_with_samples >= 2,
                "positive_best_regime": (best_return or 0.0) > 0,
                "negative_worst_regime": (worst_return or 0.0) < 0,
            },
        },
    }
