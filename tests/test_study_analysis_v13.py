import tempfile
import unittest
import zipfile
from pathlib import Path

from hadron_study_analysis import (
    aggregate_by_preset_energy,
    create_study_report_package,
    filter_results,
    heatmap_grid,
    rank_results,
)


ROWS = [
    {
        "job_index": 0,
        "repeat": 0,
        "preset": "STANDARD",
        "beam_energy_gev": 1000.0,
        "events_requested": 1000,
        "seed": 1,
        "saved_count": 100,
        "discarded_count": 900,
        "higgs_count": 10,
        "acceptance_rate": 10.0,
        "l1_energy_threshold": 5000.0,
        "met_trigger_threshold": 500.0,
        "higgs_window_gev": 3.0,
        "noise_enabled": True,
        "noise_sigma": 0.9,
        "resolution_sigma": 0.012,
    },
    {
        "job_index": 1,
        "repeat": 1,
        "preset": "STANDARD",
        "beam_energy_gev": 1000.0,
        "events_requested": 1000,
        "seed": 2,
        "saved_count": 120,
        "discarded_count": 880,
        "higgs_count": 20,
        "acceptance_rate": 12.0,
        "l1_energy_threshold": 5000.0,
        "met_trigger_threshold": 500.0,
        "higgs_window_gev": 3.0,
        "noise_enabled": True,
        "noise_sigma": 0.9,
        "resolution_sigma": 0.012,
    },
    {
        "job_index": 2,
        "repeat": 0,
        "preset": "HIGGS STUDY",
        "beam_energy_gev": 6500.0,
        "events_requested": 1000,
        "seed": 3,
        "saved_count": 300,
        "discarded_count": 700,
        "higgs_count": 120,
        "acceptance_rate": 30.0,
        "l1_energy_threshold": 5000.0,
        "met_trigger_threshold": 500.0,
        "higgs_window_gev": 3.0,
        "noise_enabled": True,
        "noise_sigma": 0.5,
        "resolution_sigma": 0.008,
    },
]


class StudyAnalysisTests(unittest.TestCase):
    def test_aggregate_repeats(self):
        agg = aggregate_by_preset_energy(ROWS)
        standard = [
            row for row in agg
            if row["preset"] == "STANDARD"
            and row["beam_energy_gev"] == 1000.0
        ][0]
        self.assertEqual(standard["repeats"], 2)
        self.assertAlmostEqual(standard["acceptance_mean"], 11.0)
        self.assertEqual(standard["higgs_total"], 30)

    def test_filter_results(self):
        filtered = filter_results(ROWS, preset="STANDARD")
        self.assertEqual(len(filtered), 2)

    def test_rank_results(self):
        ranked = rank_results(ROWS, metric="acceptance_rate")
        self.assertEqual(ranked[0]["preset"], "HIGGS STUDY")

    def test_heatmap_grid(self):
        grid = heatmap_grid(ROWS, metric="acceptance_mean")
        self.assertIn("STANDARD", grid["presets"])
        self.assertIn(6500.0, grid["energies"])
        self.assertEqual(len(grid["matrix"]), len(grid["presets"]))

    def test_report_package(self):
        study = {
            "id": 7,
            "name": "Package Test",
            "status": "COMPLETE",
            "spec": {},
            "result": {
                "version": "1.6.0",
                "jobs": len(ROWS),
                "workers": 1,
                "results": ROWS,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.zip"
            create_study_report_package(path, study)
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                report = z.read("report.html").decode("utf-8")
                csv_text = z.read("results.csv").decode("utf-8")
        self.assertIn("report.html", names)
        self.assertIn("results.csv", names)
        self.assertIn("summary.json", names)
        self.assertIn("Package Test", report)
        self.assertIn("acceptance_rate", csv_text)


if __name__ == "__main__":
    unittest.main()
