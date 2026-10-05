"""Study-results analysis helpers for Hadron v1.3."""

from __future__ import annotations

import csv
import html
import io
import json
import math
import statistics
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from hadron_analysis import enrich_summary, render_html_report
from hadron_version import __version__


def normalized_results(study: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = study.get("result") or {}
    rows = result.get("results") or []
    return [enrich_summary(row) for row in rows]


def filter_results(
    rows: Sequence[Mapping[str, Any]],
    *,
    preset: str = "ALL",
    min_energy: float | None = None,
    max_energy: float | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if preset != "ALL" and str(item.get("preset")) != preset:
            continue
        energy = float(item.get("beam_energy_gev", 0.0))
        if min_energy is not None and energy < min_energy:
            continue
        if max_energy is not None and energy > max_energy:
            continue
        output.append(item)
    return output


def aggregate_by_preset_energy(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, float], list[dict[str, Any]]] = {}
    for raw in rows:
        row = enrich_summary(raw)
        key = (str(row.get("preset", "UNKNOWN")), float(row.get("beam_energy_gev", 0.0)))
        buckets.setdefault(key, []).append(row)

    output = []
    for (preset, energy), items in sorted(buckets.items()):
        acceptance = [float(x.get("acceptance_rate", 0.0)) for x in items]
        higgs_rate = [float(x.get("higgs_rate_per_1000", 0.0)) for x in items]
        ci_low = [float(x.get("acceptance_ci95_low", 0.0)) for x in items]
        ci_high = [float(x.get("acceptance_ci95_high", 0.0)) for x in items]

        output.append(
            {
                "preset": preset,
                "beam_energy_gev": energy,
                "repeats": len(items),
                "acceptance_mean": statistics.fmean(acceptance) if acceptance else 0.0,
                "acceptance_min": min(acceptance) if acceptance else 0.0,
                "acceptance_max": max(acceptance) if acceptance else 0.0,
                "ci95_low_mean": statistics.fmean(ci_low) if ci_low else 0.0,
                "ci95_high_mean": statistics.fmean(ci_high) if ci_high else 0.0,
                "higgs_rate_mean_per_1000": statistics.fmean(higgs_rate) if higgs_rate else 0.0,
                "higgs_total": sum(int(x.get("higgs_count", 0)) for x in items),
                "events_total": sum(int(x.get("events_requested", 0)) for x in items),
            }
        )
    return output


def rank_results(
    rows: Sequence[Mapping[str, Any]],
    *,
    metric: str = "acceptance_rate",
    descending: bool = True,
) -> list[dict[str, Any]]:
    valid_metrics = {
        "acceptance_rate",
        "higgs_rate_per_1000",
        "higgs_count",
        "saved_count",
    }
    if metric not in valid_metrics:
        raise ValueError(f"Unsupported ranking metric: {metric}")

    enriched = [enrich_summary(row) for row in rows]
    return sorted(
        enriched,
        key=lambda row: float(row.get(metric, 0.0)),
        reverse=descending,
    )


def heatmap_grid(
    rows: Sequence[Mapping[str, Any]],
    *,
    metric: str = "acceptance_mean",
) -> dict[str, Any]:
    aggregate = aggregate_by_preset_energy(rows)
    presets = sorted({row["preset"] for row in aggregate})
    energies = sorted({float(row["beam_energy_gev"]) for row in aggregate})

    lookup = {
        (row["preset"], float(row["beam_energy_gev"])): float(row.get(metric, 0.0))
        for row in aggregate
    }

    matrix = [
        [lookup.get((preset, energy), math.nan) for energy in energies]
        for preset in presets
    ]

    return {
        "presets": presets,
        "energies": energies,
        "matrix": matrix,
        "metric": metric,
    }


def _results_csv(rows: Sequence[Mapping[str, Any]]) -> str:
    enriched = [enrich_summary(row) for row in rows]
    if not enriched:
        return ""

    preferred = [
        "job_index",
        "repeat",
        "preset",
        "beam_energy_gev",
        "events_requested",
        "seed",
        "saved_count",
        "discarded_count",
        "higgs_count",
        "acceptance_rate",
        "acceptance_ci95_low",
        "acceptance_ci95_high",
        "higgs_rate_per_1000",
        "reproducibility_hash",
    ]
    extras = sorted(
        {
            key
            for row in enriched
            for key in row.keys()
            if key not in preferred
        }
    )
    fields = [field for field in preferred if any(field in row for row in enriched)] + extras

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in enriched:
        writer.writerow(
            {
                key: json.dumps(value, sort_keys=True)
                if isinstance(value, (dict, list))
                else value
                for key, value in row.items()
                if key in fields
            }
        )
    return buffer.getvalue()


def render_study_summary_html(study: Mapping[str, Any]) -> str:
    rows = normalized_results(study)
    aggregate = aggregate_by_preset_energy(rows)
    base_report = render_html_report(
        rows,
        title=f"Hadron v{__version__} Study #{study.get('id', '')} — {study.get('name', '')}",
    )

    aggregate_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(item['preset'])}</td>"
        f"<td>{item['beam_energy_gev']:.0f}</td>"
        f"<td>{item['repeats']}</td>"
        f"<td>{item['acceptance_mean']:.3f}%</td>"
        f"<td>{item['higgs_rate_mean_per_1000']:.3f}</td>"
        f"<td>{item['events_total']:,}</td>"
        "</tr>"
        for item in aggregate
    )

    section = f"""
<section>
  <h2>Preset × energy aggregate</h2>
  <table>
    <thead>
      <tr>
        <th>Preset</th><th>Energy GeV</th><th>Repeats</th>
        <th>Mean acceptance</th><th>Higgs / 1000</th><th>Total events</th>
      </tr>
    </thead>
    <tbody>{aggregate_rows}</tbody>
  </table>
</section>
"""

    return base_report.replace(
        "<section>\n    <h2>Interpretation note</h2>",
        section + "\n<section>\n    <h2>Interpretation note</h2>",
        1,
    )


def create_study_report_package(
    destination: str | Path,
    study: Mapping[str, Any],
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    rows = normalized_results(study)
    aggregate = aggregate_by_preset_energy(rows)
    ranking = rank_results(rows, metric="acceptance_rate") if rows else []

    summary_payload = {
        "application": "Hadron",
        "version": __version__,
        "package_type": "study-report",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "study": dict(study),
        "aggregate": aggregate,
        "top_acceptance_jobs": ranking[:10],
    }

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "study.json",
            json.dumps(study, indent=2),
        )
        z.writestr(
            "summary.json",
            json.dumps(summary_payload, indent=2),
        )
        z.writestr(
            "results.csv",
            _results_csv(rows),
        )
        z.writestr(
            "report.html",
            render_study_summary_html(study),
        )
        z.writestr(
            "README.txt",
            (
                "Hadron study report package\n"
                f"Study: {study.get('name', '')}\n"
                f"Study ID: {study.get('id', '')}\n"
                "Contents: report.html, results.csv, study.json, summary.json\n"
                "All results are synthetic toy-simulation outputs.\n"
            ),
        )

    return destination


__all__ = [
    "normalized_results",
    "filter_results",
    "aggregate_by_preset_energy",
    "rank_results",
    "heatmap_grid",
    "render_study_summary_html",
    "create_study_report_package",
]
