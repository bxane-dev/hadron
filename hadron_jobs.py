"""Queued/parallel Hadron v3.0 batch job runner."""

from __future__ import annotations

import argparse
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from hadron_batch import run_experiment
from hadron_version import __version__


def expand_job_spec(spec: dict[str, Any]) -> list[dict[str, Any]]:
    defaults = {
        "events": int(spec.get("events", 10000)),
        "preset": spec.get("preset", "STANDARD"),
        "seed": int(spec.get("seed", 42)),
        "l1_threshold": float(spec.get("l1_threshold", 5000.0)),
        "met_threshold": float(spec.get("met_threshold", 500.0)),
        "higgs_window": float(spec.get("higgs_window", 3.0)),
        "noise": bool(spec.get("noise", True)),
        "noise_sigma": (
            None
            if spec.get("noise_sigma") is None
            else float(spec.get("noise_sigma"))
        ),
        "resolution_sigma": (
            None
            if spec.get("resolution_sigma") is None
            else float(spec.get("resolution_sigma"))
        ),
    }

    energies = spec.get("energies", [spec.get("energy", 6500.0)])
    presets = spec.get("presets", [defaults["preset"]])
    repeats = int(spec.get("repeats", 1))

    jobs = []
    job_index = 0
    for preset in presets:
        for energy in energies:
            for repeat in range(repeats):
                jobs.append(
                    {
                        **defaults,
                        "preset": str(preset),
                        "energy": float(energy),
                        "seed": defaults["seed"] + job_index,
                        "repeat": repeat,
                        "job_index": job_index,
                    }
                )
                job_index += 1
    return jobs


def _run_one(job: dict[str, Any]) -> dict[str, Any]:
    result = run_experiment(
        events=job["events"],
        energy=job["energy"],
        preset=job["preset"],
        seed=job["seed"],
        l1_threshold=job["l1_threshold"],
        met_threshold=job["met_threshold"],
        higgs_window=job["higgs_window"],
        noise=job["noise"],
        noise_sigma=job["noise_sigma"],
        resolution_sigma=job["resolution_sigma"],
    )
    result["job_index"] = job["job_index"]
    result["repeat"] = job["repeat"]
    return result


def run_job_spec(
    spec: dict[str, Any],
    *,
    workers: int = 1,
) -> dict[str, Any]:
    jobs = expand_job_spec(spec)
    results = []

    if workers <= 1 or len(jobs) <= 1:
        results = [_run_one(job) for job in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_one, job): job for job in jobs}
            for future in as_completed(futures):
                results.append(future.result())

    results.sort(key=lambda x: x["job_index"])
    return {
        "application": "Hadron",
        "version": __version__,
        "mode": "job-queue",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "workers": int(max(1, workers)),
        "jobs": len(jobs),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Hadron v3.0 queued/parallel toy-simulation runner."
    )
    parser.add_argument("spec", type=Path, help="JSON job specification")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("hadron_jobs_results.json"),
    )
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    payload = run_job_spec(spec, workers=max(1, args.workers))
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(
        f"Completed {payload['jobs']} jobs with {payload['workers']} worker(s)."
    )
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
