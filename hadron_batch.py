import argparse
import csv
import json
import random
from datetime import datetime
from pathlib import Path

from hadron_analysis import enrich_summary, render_html_report
from hadron_version import __version__

from hadron_engine import (
    DETECTORS,
    PRESETS,
    SimulationConfig,
    generate_event,
    passes_hlt,
    passes_l1,
    run_counts,
)


def run_experiment(
    events,
    energy,
    preset,
    seed,
    l1_threshold,
    met_threshold,
    higgs_window,
    noise,
    noise_sigma=None,
    resolution_sigma=None,
):
    config = SimulationConfig(
        events=int(events),
        energy=float(energy),
        preset=str(preset),
        seed=int(seed),
        l1_threshold=float(l1_threshold),
        met_threshold=float(met_threshold),
        higgs_window=float(higgs_window),
        noise=bool(noise),
        noise_sigma=(
            None if noise_sigma is None else float(noise_sigma)
        ),
        resolution_sigma=(
            None
            if resolution_sigma is None
            else float(resolution_sigma)
        ),
    )
    counts = run_counts(config)

    return enrich_summary({
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "preset": config.preset,
        "beam_energy_gev": config.energy,
        "events_requested": config.events,
        "seed": config.seed,
        "saved_count": counts["saved_count"],
        "discarded_count": counts["discarded_count"],
        "higgs_count": counts["higgs_count"],
        "l1_rejected_count": counts["l1_rejected_count"],
        "hlt_rejected_count": counts["hlt_rejected_count"],
        "acceptance_rate": counts["acceptance_rate"],
        "l1_energy_threshold": config.l1_threshold,
        "met_trigger_threshold": config.met_threshold,
        "higgs_window_gev": config.higgs_window,
        "noise_enabled": config.noise,
        "noise_sigma": (
            PRESETS[config.preset]["noise_sigma"]
            if config.noise_sigma is None
            else config.noise_sigma
        ),
        "resolution_sigma": (
            PRESETS[config.preset]["resolution_sigma"]
            if config.resolution_sigma is None
            else config.resolution_sigma
        ),
    })

def main():
    parser = argparse.ArgumentParser(
        description="Hadron v3.0 headless toy-simulation experiment runner."
    )
    parser.add_argument("--events", type=int, default=10000)
    parser.add_argument("--energy", type=float, default=6500.0)
    parser.add_argument(
        "--energies",
        type=str,
        default="",
        help="Comma-separated beam energies for a sweep, e.g. 1000,3000,5000,6500",
    )
    parser.add_argument("--preset", choices=list(PRESETS), default="STANDARD")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--l1", type=float, default=5000.0)
    parser.add_argument("--met", type=float, default=500.0)
    parser.add_argument("--higgs-window", type=float, default=3.0)
    parser.add_argument("--no-noise", action="store_true")
    parser.add_argument(
        "--noise-sigma",
        type=float,
        default=None,
        help="Override preset detector noise sigma.",
    )
    parser.add_argument(
        "--resolution-sigma",
        type=float,
        default=None,
        help="Override preset fractional detector resolution.",
    )
    parser.add_argument("--output", type=Path, default=Path("hadron_batch_results.json"))
    parser.add_argument(
        "--html-report",
        type=Path,
        default=None,
        help="Optional self-contained HTML report path.",
    )
    args = parser.parse_args()

    if args.events < 1:
        parser.error("--events must be >= 1")

    energies = (
        [float(x.strip()) for x in args.energies.split(",") if x.strip()]
        if args.energies
        else [args.energy]
    )

    results = []
    for idx, energy in enumerate(energies):
        result = run_experiment(
            events=args.events,
            energy=energy,
            preset=args.preset,
            seed=args.seed + idx,
            l1_threshold=args.l1,
            met_threshold=args.met,
            higgs_window=args.higgs_window,
            noise=not args.no_noise,
            noise_sigma=args.noise_sigma,
            resolution_sigma=args.resolution_sigma,
        )
        results.append(result)
        print(
            f"{energy:7.1f} GeV | saved {result['saved_count']:7d} | "
            f"discarded {result['discarded_count']:7d} | "
            f"accept {result['acceptance_rate']:6.2f}% | "
            f"Higgs {result['higgs_count']:6d}"
        )

    payload = {
        "application": "Hadron",
        "version": __version__,
        "mode": "headless-batch",
        "results": results,
    }

    if args.output.suffix.lower() == ".csv":
        fields = list(results[0].keys()) if results else []
        with args.output.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for result in results:
                writer.writerow({
                    key: json.dumps(value, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in result.items()
                })
    else:
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Saved: {args.output}")

    if args.html_report is not None:
        args.html_report.write_text(
            render_html_report(results, title="Hadron v3.0 Batch Report"),
            encoding="utf-8",
        )
        print(f"HTML report: {args.html_report}")


if __name__ == "__main__":
    main()
