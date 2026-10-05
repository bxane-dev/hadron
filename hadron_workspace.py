"""Analysis-workspace helpers for Hadron v0.8.

All computations operate on synthetic Hadron toy-simulation data.
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from hadron_engine import DETECTORS, PRESETS, generate_event, passes_hlt, passes_l1
from hadron_analysis import enrich_summary
from hadron_version import __version__

HIGGS_MASS_GEV = 125.1


def experiment_overlay_points(
    summaries: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, float]]]:
    """Group experiment summaries by preset for overlay plots."""
    groups: dict[str, list[dict[str, float]]] = {}
    for item in summaries:
        preset = str(item.get("preset", "UNKNOWN"))
        groups.setdefault(preset, []).append(
            {
                "energy": float(item.get("beam_energy_gev", 0.0)),
                "acceptance": float(item.get("acceptance_rate", 0.0)),
                "ci_low": float(item.get("acceptance_ci95_low", 0.0)),
                "ci_high": float(item.get("acceptance_ci95_high", 0.0)),
                "higgs_rate": float(item.get("higgs_rate_per_1000", 0.0)),
            }
        )
    for values in groups.values():
        values.sort(key=lambda x: x["energy"])
    return groups


def trigger_efficiency_curve(
    *,
    events: int,
    energy: float,
    preset: str,
    seed: int,
    thresholds: Sequence[float],
    met_threshold: float = 500.0,
    higgs_window: float = 3.0,
    noise: bool = True,
) -> list[dict[str, float]]:
    """Generate one deterministic event sample and scan L1 ET thresholds."""
    if preset not in PRESETS:
        raise ValueError("Unknown preset")
    if events <= 0:
        raise ValueError("events must be positive")

    rng = random.Random(seed)
    sample = [generate_event(rng, energy, preset, noise=noise) for _ in range(events)]

    output = []
    for threshold in thresholds:
        l1_pass = 0
        final_pass = 0
        for event in sample:
            if passes_l1(event, float(threshold), met_threshold):
                l1_pass += 1
                keep, _is_higgs, _reason = passes_hlt(event, met_threshold, higgs_window)
                if keep:
                    final_pass += 1
        output.append(
            {
                "threshold": float(threshold),
                "l1_efficiency": l1_pass / events * 100.0,
                "final_efficiency": final_pass / events * 100.0,
            }
        )
    return output


def detector_efficiency_matrix(
    *,
    events_per_detector: int,
    energy: float,
    preset: str,
    seed: int,
    l1_threshold: float = 5000.0,
    met_threshold: float = 500.0,
    higgs_window: float = 3.0,
    noise: bool = True,
) -> dict[str, dict[str, float]]:
    """Return a label-only detector comparison using one common toy sample.

    The current engine has no detector-specific response model. Therefore this
    function intentionally applies the exact same generated event sample and
    trigger logic to every detector label. Any detector-specific differences
    shown elsewhere would be misleading until explicit response profiles exist.
    """
    if events_per_detector <= 0:
        raise ValueError("events_per_detector must be positive")
    if preset not in PRESETS:
        raise ValueError("Unknown preset")

    rng = random.Random(seed)
    events = [
        generate_event(
            rng,
            energy,
            preset,
            noise=noise,
            detector="COMMON-SAMPLE",
        )
        for _ in range(events_per_detector)
    ]

    l1_count = 0
    accepted = 0
    higgs = 0
    high_met = 0

    for event in events:
        if event["missing_energy"] >= 600.0:
            high_met += 1

        if passes_l1(event, l1_threshold, met_threshold):
            l1_count += 1
            keep, is_higgs, _reason = passes_hlt(
                event,
                met_threshold,
                higgs_window,
            )
            if keep:
                accepted += 1
                higgs += int(is_higgs)

    common = {
        "l1_efficiency": l1_count / events_per_detector * 100.0,
        "final_efficiency": accepted / events_per_detector * 100.0,
        "higgs_efficiency": higgs / events_per_detector * 100.0,
        "high_met_fraction": high_met / events_per_detector * 100.0,
    }

    return {
        detector: dict(common)
        for detector in DETECTORS
    }


def fit_mass_spectrum(
    masses: Iterable[float],
    *,
    center: float = HIGGS_MASS_GEV,
    signal_half_width: float = 8.0,
    sideband_width: float = 10.0,
) -> dict[str, float]:
    """Return a simple sideband-subtracted Higgs-region diagnostic.

    This is intentionally lightweight and is not a calibrated physics fit.
    """
    values = np.asarray([float(x) for x in masses if math.isfinite(float(x))], dtype=float)
    if values.size == 0:
        return {
            "entries": 0,
            "peak_mean": 0.0,
            "peak_sigma": 0.0,
            "signal_window_count": 0.0,
            "estimated_background": 0.0,
            "estimated_excess": 0.0,
            "toy_s_over_sqrt_b": 0.0,
        }

    s_lo = center - signal_half_width
    s_hi = center + signal_half_width

    left_lo = s_lo - sideband_width
    left_hi = s_lo
    right_lo = s_hi
    right_hi = s_hi + sideband_width

    signal_values = values[(values >= s_lo) & (values <= s_hi)]
    left_count = int(np.sum((values >= left_lo) & (values < left_hi)))
    right_count = int(np.sum((values > right_lo) & (values <= right_hi)))

    sideband_total_width = sideband_width * 2.0
    background_density = (left_count + right_count) / sideband_total_width
    expected_background = background_density * (signal_half_width * 2.0)
    signal_count = int(signal_values.size)
    excess = max(0.0, signal_count - expected_background)

    if signal_values.size:
        weights_centered = signal_values
        peak_mean = float(np.mean(weights_centered))
        peak_sigma = float(np.std(weights_centered, ddof=1)) if signal_values.size > 1 else 0.0
    else:
        peak_mean = 0.0
        peak_sigma = 0.0

    toy_significance = excess / math.sqrt(max(expected_background, 1.0))

    return {
        "entries": int(values.size),
        "peak_mean": peak_mean,
        "peak_sigma": peak_sigma,
        "signal_window_count": float(signal_count),
        "estimated_background": float(expected_background),
        "estimated_excess": float(excess),
        "toy_s_over_sqrt_b": float(toy_significance),
        "window_low": float(s_lo),
        "window_high": float(s_hi),
    }


def make_project_payload(
    *,
    name: str,
    experiment_ids: Sequence[int],
    notes: str = "",
    workspace_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a portable Hadron analysis-project payload."""
    return {
        "format": "hadron-analysis-project",
        "format_version": 1,
        "application_version": __version__,
        "name": str(name).strip() or "Untitled Project",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "experiment_ids": [int(x) for x in experiment_ids],
        "notes": str(notes),
        "workspace_state": dict(workspace_state or {}),
    }


def validate_project_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("format") != "hadron-analysis-project":
        raise ValueError("Not a Hadron analysis project")
    if int(payload.get("format_version", 0)) != 1:
        raise ValueError("Unsupported project format version")
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("Project name is required")
    ids = payload.get("experiment_ids", [])
    if not isinstance(ids, list):
        raise ValueError("experiment_ids must be a list")

    normalized = dict(payload)
    normalized["name"] = name
    normalized["experiment_ids"] = [int(x) for x in ids]
    normalized["notes"] = str(payload.get("notes", ""))
    normalized["workspace_state"] = dict(payload.get("workspace_state", {}))
    return normalized


def export_project_file(path: str | Path, payload: Mapping[str, Any]) -> None:
    normalized = validate_project_payload(payload)
    Path(path).write_text(json.dumps(normalized, indent=2), encoding="utf-8")


def import_project_file(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_project_payload(payload)


__all__ = [
    "experiment_overlay_points",
    "trigger_efficiency_curve",
    "detector_efficiency_matrix",
    "fit_mass_spectrum",
    "make_project_payload",
    "validate_project_payload",
    "export_project_file",
    "import_project_file",
]
