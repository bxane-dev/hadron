import json
import tempfile
import unittest
from pathlib import Path

from hadron_analysis import (
    compare_summaries,
    config_fingerprint,
    enrich_summary,
    render_html_report,
    wilson_interval,
)
from hadron_batch import run_experiment


class AnalysisTests(unittest.TestCase):
    def test_wilson_interval_bounds(self):
        low, high = wilson_interval(50, 100)
        self.assertLess(low, 50.0)
        self.assertGreater(high, 50.0)
        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(high, 100.0)

    def test_wilson_empty(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 0.0))

    def test_fingerprint_is_deterministic(self):
        a = config_fingerprint({"b": 2, "a": 1})
        b = config_fingerprint({"a": 1, "b": 2})
        c = config_fingerprint({"a": 1, "b": 3})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_enrich_summary(self):
        summary = enrich_summary({
            "preset": "STANDARD",
            "beam_energy_gev": 6500.0,
            "events_requested": 1000,
            "seed": 42,
            "saved_count": 100,
            "discarded_count": 900,
            "higgs_count": 20,
            "acceptance_rate": 10.0,
            "l1_energy_threshold": 5000.0,
            "met_trigger_threshold": 500.0,
            "higgs_window_gev": 3.0,
            "noise_enabled": True,
            "noise_sigma": 0.9,
            "resolution_sigma": 0.012,
        })
        self.assertIn("acceptance_ci95_low", summary)
        self.assertIn("acceptance_ci95_high", summary)
        self.assertEqual(summary["higgs_rate_per_1000"], 20.0)
        self.assertEqual(len(summary["reproducibility_hash"]), 64)

    def test_compare_identical(self):
        a = {"events_requested": 1000, "saved_count": 100}
        b = {"events_requested": 1000, "saved_count": 100}
        comparison = compare_summaries(a, b)
        self.assertAlmostEqual(comparison["delta_percentage_points"], 0.0)
        self.assertAlmostEqual(comparison["z_score"], 0.0)
        self.assertAlmostEqual(comparison["two_sided_p_value"], 1.0)

    def test_html_report(self):
        report = render_html_report([
            {
                "preset": "STANDARD",
                "beam_energy_gev": 6500.0,
                "events_requested": 100,
                "seed": 42,
                "saved_count": 20,
                "discarded_count": 80,
                "higgs_count": 5,
                "acceptance_rate": 20.0,
                "l1_energy_threshold": 5000.0,
                "met_trigger_threshold": 500.0,
                "higgs_window_gev": 3.0,
                "noise_enabled": True,
                "noise_sigma": 0.9,
                "resolution_sigma": 0.012,
            }
        ])
        self.assertIn("<!doctype html>", report.lower())
        self.assertIn("STANDARD", report)
        self.assertIn("95% interval", report)

    def test_batch_is_reproducible(self):
        kwargs = dict(
            events=300,
            energy=6500.0,
            preset="STANDARD",
            seed=12345,
            l1_threshold=5000.0,
            met_threshold=500.0,
            higgs_window=3.0,
            noise=True,
        )
        a = run_experiment(**kwargs)
        b = run_experiment(**kwargs)
        fields = [
            "saved_count",
            "discarded_count",
            "higgs_count",
            "acceptance_rate",
            "acceptance_ci95_low",
            "acceptance_ci95_high",
            "reproducibility_hash",
        ]
        for field in fields:
            self.assertEqual(a[field], b[field])


if __name__ == "__main__":
    unittest.main()
