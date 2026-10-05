"""Shared deterministic simulation engine for Hadron v0.9.

This module powers the GUI, headless runner, workspace scans, and queued jobs.
It generates synthetic toy events only.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

HIGGS_MASS_GEV = 125.1
DETECTORS = ["ATLAS-SIM", "CMS-SIM", "INNER-TRACKER", "CALORIMETER"]

PRESETS = {
    "STANDARD": {
        "higgs_probability": 0.05,
        "met_tail_probability": 0.20,
        "noise_sigma": 0.9,
        "resolution_sigma": 0.012,
    },
    "HIGGS STUDY": {
        "higgs_probability": 0.18,
        "met_tail_probability": 0.16,
        "noise_sigma": 0.5,
        "resolution_sigma": 0.008,
    },
    "DARK MATTER": {
        "higgs_probability": 0.03,
        "met_tail_probability": 0.42,
        "noise_sigma": 1.1,
        "resolution_sigma": 0.015,
    },
    "HIGH PILEUP": {
        "higgs_probability": 0.06,
        "met_tail_probability": 0.24,
        "noise_sigma": 2.8,
        "resolution_sigma": 0.025,
    },
}


@dataclass(frozen=True)
class SimulationConfig:
    events: int = 10000
    energy: float = 6500.0
    preset: str = "STANDARD"
    seed: int = 42
    l1_threshold: float = 5000.0
    met_threshold: float = 500.0
    higgs_window: float = 3.0
    noise: bool = True
    noise_sigma: float | None = None
    resolution_sigma: float | None = None

    def validate(self) -> "SimulationConfig":
        if self.events < 1:
            raise ValueError("events must be >= 1")
        if not 1000.0 <= self.energy <= 7000.0:
            raise ValueError("energy must be between 1000 and 7000 GeV")
        if self.preset not in PRESETS:
            raise ValueError(f"unknown preset: {self.preset}")
        if self.l1_threshold < 0:
            raise ValueError("l1_threshold must be >= 0")
        if self.met_threshold < 0:
            raise ValueError("met_threshold must be >= 0")
        if self.higgs_window <= 0:
            raise ValueError("higgs_window must be > 0")
        if self.noise_sigma is not None and self.noise_sigma < 0:
            raise ValueError("noise_sigma must be >= 0")
        if (
            self.resolution_sigma is not None
            and self.resolution_sigma < 0
        ):
            raise ValueError("resolution_sigma must be >= 0")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_event(
    rng: random.Random,
    energy: float,
    preset_name: str,
    *,
    noise: bool = True,
    detector: str | None = None,
    noise_sigma: float | None = None,
    resolution_sigma: float | None = None,
) -> dict[str, Any]:
    if preset_name not in PRESETS:
        raise ValueError(f"unknown preset: {preset_name}")

    preset = PRESETS[preset_name]
    if noise_sigma is None:
        noise_sigma = float(preset["noise_sigma"])
    if resolution_sigma is None:
        resolution_sigma = float(preset["resolution_sigma"])

    total_collision_energy = max(float(energy) * 2.0, 2000.0)
    transverse_energy = rng.uniform(2000.0, total_collision_energy)

    if rng.random() < preset["met_tail_probability"]:
        missing_energy = rng.uniform(300.0, 800.0)
    else:
        missing_energy = rng.uniform(0.0, 70.0)

    muon_count = rng.choices(
        [0, 1, 2, 4],
        weights=[0.56, 0.25, 0.15, 0.04],
    )[0]

    if rng.random() < preset["higgs_probability"]:
        detected_masses = [
            rng.uniform(20, 50),
            rng.uniform(70, 80),
            rng.normalvariate(HIGGS_MASS_GEV, 1.5),
        ]
    else:
        detected_masses = [
            rng.uniform(5, 100)
            for _ in range(rng.randint(2, 6))
        ]

    if noise:
        detected_masses = [
            max(
                0.0,
                mass
                + rng.gauss(0.0, noise_sigma)
                + rng.gauss(0.0, abs(mass) * resolution_sigma),
            )
            for mass in detected_masses
        ]
        transverse_energy = max(
            0.0,
            transverse_energy
            + rng.gauss(0.0, transverse_energy * resolution_sigma),
        )
        missing_energy = max(
            0.0,
            missing_energy
            + rng.gauss(0.0, max(1.0, noise_sigma * 8.0)),
        )

    return {
        "detector": detector or rng.choice(DETECTORS),
        "transverse_energy": transverse_energy,
        "missing_energy": missing_energy,
        "muon_count": muon_count,
        "particle_masses": detected_masses,
        "preset": preset_name,
        "noise_enabled": bool(noise),
    }


def passes_l1(
    event: Mapping[str, Any],
    l1_threshold: float,
    met_threshold: float,
) -> bool:
    return (
        float(event["transverse_energy"]) > float(l1_threshold)
        or int(event["muon_count"]) >= 2
        or float(event["missing_energy"]) > float(met_threshold)
    )


def passes_hlt(
    event: Mapping[str, Any],
    met_threshold: float,
    higgs_window: float,
) -> tuple[bool, bool, str]:
    for mass in event["particle_masses"]:
        if abs(float(mass) - HIGGS_MASS_GEV) < float(higgs_window):
            return True, True, f"Higgs-Boson Candidate at {float(mass):.2f} GeV"

    if float(event["missing_energy"]) > max(600.0, float(met_threshold)):
        return (
            True,
            False,
            f"Dark Matter Candidate (MET: {float(event['missing_energy']):.1f} GeV)",
        )

    return False, False, "Standard Model Background Noise"


def evaluate_event(
    event: Mapping[str, Any],
    *,
    l1_threshold: float,
    met_threshold: float,
    higgs_window: float,
) -> dict[str, Any]:
    if not passes_l1(event, l1_threshold, met_threshold):
        return {
            "status": "L1_REJECT",
            "saved": False,
            "higgs": False,
            "reason": "Hardware L1 rejection",
        }

    keep, is_higgs, reason = passes_hlt(event, met_threshold, higgs_window)
    return {
        "status": "SAVED" if keep else "HLT_REJECT",
        "saved": bool(keep),
        "higgs": bool(is_higgs),
        "reason": reason,
    }


def run_counts(config: SimulationConfig) -> dict[str, int | float]:
    config.validate()
    rng = random.Random(config.seed)
    saved = 0
    discarded = 0
    higgs = 0
    l1_rejected = 0
    hlt_rejected = 0

    for _ in range(config.events):
        event = generate_event(
            rng,
            config.energy,
            config.preset,
            noise=config.noise,
            noise_sigma=config.noise_sigma,
            resolution_sigma=config.resolution_sigma,
        )
        result = evaluate_event(
            event,
            l1_threshold=config.l1_threshold,
            met_threshold=config.met_threshold,
            higgs_window=config.higgs_window,
        )

        if result["saved"]:
            saved += 1
            higgs += int(result["higgs"])
        else:
            discarded += 1
            if result["status"] == "L1_REJECT":
                l1_rejected += 1
            else:
                hlt_rejected += 1

    return {
        "saved_count": saved,
        "discarded_count": discarded,
        "higgs_count": higgs,
        "l1_rejected_count": l1_rejected,
        "hlt_rejected_count": hlt_rejected,
        "acceptance_rate": saved / config.events * 100.0,
    }


__all__ = [
    "HIGGS_MASS_GEV",
    "DETECTORS",
    "PRESETS",
    "SimulationConfig",
    "generate_event",
    "passes_l1",
    "passes_hlt",
    "evaluate_event",
    "run_counts",
]
