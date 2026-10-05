"""Statistical helpers and reporting for Hadron toy-simulation experiments.

These functions quantify simulation output only. They are not calibrated
particle-physics significance calculations.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
from datetime import datetime
from typing import Iterable, Mapping, Any

Z_95 = 1.959963984540054


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """Return a Wilson score interval as percentages."""
    if total <= 0:
        return 0.0, 0.0
    successes = max(0, min(int(successes), int(total)))
    n = float(total)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = (
        z
        * math.sqrt((p * (1.0 - p) / n) + z2 / (4.0 * n * n))
        / denom
    )
    low = max(0.0, center - margin) * 100.0
    high = min(1.0, center + margin) * 100.0
    return low, high


def config_fingerprint(config: Mapping[str, Any]) -> str:
    """Return a deterministic SHA-256 fingerprint for a simulation config."""
    canonical = json.dumps(
        dict(config),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def enrich_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Add uncertainty and normalized-rate fields to an experiment summary."""
    enriched = dict(summary)
    total = int(enriched.get("events_requested", 0) or 0)
    saved = int(enriched.get("saved_count", 0) or 0)
    higgs = int(enriched.get("higgs_count", 0) or 0)

    low, high = wilson_interval(saved, total)
    enriched["acceptance_ci95_low"] = low
    enriched["acceptance_ci95_high"] = high
    enriched["higgs_rate_per_1000"] = (higgs / total * 1000.0) if total else 0.0

    fingerprint_fields = {
        "preset": enriched.get("preset"),
        "beam_energy_gev": enriched.get("beam_energy_gev"),
        "events_requested": total,
        "seed": enriched.get("seed"),
        "l1_energy_threshold": enriched.get("l1_energy_threshold"),
        "met_trigger_threshold": enriched.get("met_trigger_threshold"),
        "higgs_window_gev": enriched.get("higgs_window_gev"),
        "noise_enabled": enriched.get("noise_enabled"),
        "noise_sigma": enriched.get("noise_sigma"),
        "resolution_sigma": enriched.get("resolution_sigma"),
    }
    enriched["reproducibility_hash"] = config_fingerprint(fingerprint_fields)
    return enriched


def compare_summaries(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, float]:
    """Compare two acceptance proportions using a pooled two-proportion z statistic.

    This is a descriptive diagnostic for toy-simulation runs, not a real
    experimental discovery-significance calculation.
    """
    n1 = int(a.get("events_requested", 0) or 0)
    n2 = int(b.get("events_requested", 0) or 0)
    x1 = int(a.get("saved_count", 0) or 0)
    x2 = int(b.get("saved_count", 0) or 0)

    p1 = x1 / n1 if n1 else 0.0
    p2 = x2 / n2 if n2 else 0.0
    delta_pp = (p1 - p2) * 100.0

    if n1 <= 0 or n2 <= 0:
        return {
            "acceptance_a": p1 * 100.0,
            "acceptance_b": p2 * 100.0,
            "delta_percentage_points": delta_pp,
            "z_score": 0.0,
            "two_sided_p_value": 1.0,
        }

    pooled = (x1 + x2) / (n1 + n2)
    variance = pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n2)
    if variance <= 0:
        z_score = 0.0
        p_value = 1.0
    else:
        z_score = (p1 - p2) / math.sqrt(variance)
        p_value = math.erfc(abs(z_score) / math.sqrt(2.0))

    return {
        "acceptance_a": p1 * 100.0,
        "acceptance_b": p2 * 100.0,
        "delta_percentage_points": delta_pp,
        "z_score": z_score,
        "two_sided_p_value": p_value,
    }


def _fmt(value: Any, digits: int = 3) -> str:
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_html_report(
    summaries: Iterable[Mapping[str, Any]],
    title: str = "Hadron Experiment Report",
) -> str:
    """Render a self-contained HTML report for one or more summaries."""
    items = [enrich_summary(item) for item in summaries]
    generated = datetime.now().isoformat(timespec="seconds")

    rows = []
    for i, item in enumerate(items, start=1):
        rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td>{html.escape(str(item.get('preset', '')))}</td>"
            f"<td>{_fmt(float(item.get('beam_energy_gev', 0)), 1)}</td>"
            f"<td>{int(item.get('events_requested', 0)):,}</td>"
            f"<td>{int(item.get('seed', 0))}</td>"
            f"<td>{_fmt(float(item.get('acceptance_rate', 0)), 3)}%</td>"
            f"<td>{_fmt(float(item.get('acceptance_ci95_low', 0)), 3)}–"
            f"{_fmt(float(item.get('acceptance_ci95_high', 0)), 3)}%</td>"
            f"<td>{int(item.get('higgs_count', 0)):,}</td>"
            f"<td>{_fmt(float(item.get('higgs_rate_per_1000', 0)), 3)}</td>"
            f"<td><code>{html.escape(str(item.get('reproducibility_hash', ''))[:16])}…</code></td>"
            "</tr>"
        )

    comparison_html = ""
    if len(items) >= 2:
        comp = compare_summaries(items[0], items[1])
        comparison_html = f"""
        <section>
          <h2>First two runs: acceptance comparison</h2>
          <p>
            Δ acceptance: <strong>{comp['delta_percentage_points']:.3f} percentage points</strong><br>
            Diagnostic z-score: <strong>{comp['z_score']:.3f}</strong><br>
            Two-sided p-value: <strong>{comp['two_sided_p_value']:.6g}</strong>
          </p>
          <p class="note">
            This comparison is a toy-simulation diagnostic and is not a particle-physics
            discovery-significance calculation.
          </p>
        </section>
        """

    table_rows = "\n".join(rows) or (
        '<tr><td colspan="10">No experiment summaries supplied.</td></tr>'
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin:0; font-family:Arial,Helvetica,sans-serif; background:#090b10; color:#e7ecf3; }}
main {{ max-width:1180px; margin:0 auto; padding:32px; }}
h1,h2 {{ margin-top:0; }}
.card, section {{ background:#11151d; border:1px solid #263042; border-radius:14px; padding:18px; margin:16px 0; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th,td {{ padding:10px 8px; border-bottom:1px solid #263042; text-align:right; }}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2) {{ text-align:left; }}
th {{ color:#8793a5; }}
code {{ color:#34d5ff; }}
.note {{ color:#8793a5; font-size:13px; }}
.gold {{ color:#ffd34e; }}
</style>
</head>
<body>
<main>
  <h1>{html.escape(title)}</h1>
  <p class="note">Generated {html.escape(generated)} · Hadron toy-simulation analysis</p>
  <section>
    <h2>Experiment summaries</h2>
    <table>
      <thead>
        <tr>
          <th>#</th><th>Preset</th><th>Beam GeV</th><th>Events</th><th>Seed</th>
          <th>Acceptance</th><th>95% interval</th><th>Higgs</th>
          <th>Higgs / 1000</th><th>Config hash</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </section>
  {comparison_html}
  <section>
    <h2>Interpretation note</h2>
    <p class="note">
      All values in this report come from synthetic toy events. Confidence intervals
      describe Monte Carlo sampling uncertainty inside this simulator only.
    </p>
  </section>
</main>
</body>
</html>
"""


__all__ = [
    "wilson_interval",
    "config_fingerprint",
    "enrich_summary",
    "compare_summaries",
    "render_html_report",
]
