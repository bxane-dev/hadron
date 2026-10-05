import json
import tempfile
import unittest
from pathlib import Path

from hadron_workspace import (
    detector_efficiency_matrix,
    experiment_overlay_points,
    export_project_file,
    fit_mass_spectrum,
    import_project_file,
    make_project_payload,
    trigger_efficiency_curve,
)


class WorkspaceTests(unittest.TestCase):
    def test_overlay_grouping(self):
        groups = experiment_overlay_points([
            {
                "preset": "STANDARD",
                "beam_energy_gev": 6500,
                "acceptance_rate": 10,
                "acceptance_ci95_low": 9,
                "acceptance_ci95_high": 11,
                "higgs_rate_per_1000": 2,
            },
            {
                "preset": "STANDARD",
                "beam_energy_gev": 3000,
                "acceptance_rate": 5,
                "acceptance_ci95_low": 4,
                "acceptance_ci95_high": 6,
                "higgs_rate_per_1000": 1,
            },
        ])
        self.assertEqual([p["energy"] for p in groups["STANDARD"]], [3000.0, 6500.0])

    def test_trigger_curve_shape(self):
        curve = trigger_efficiency_curve(
            events=200,
            energy=6500,
            preset="STANDARD",
            seed=44,
            thresholds=[2000, 5000, 9000],
        )
        self.assertEqual(len(curve), 3)
        self.assertGreaterEqual(curve[0]["l1_efficiency"], curve[-1]["l1_efficiency"])

    def test_detector_matrix(self):
        matrix = detector_efficiency_matrix(
            events_per_detector=100,
            energy=6500,
            preset="STANDARD",
            seed=9,
        )
        self.assertEqual(len(matrix), 4)
        for values in matrix.values():
            self.assertIn("final_efficiency", values)
            self.assertGreaterEqual(values["final_efficiency"], 0.0)
            self.assertLessEqual(values["final_efficiency"], 100.0)

    def test_mass_fit(self):
        masses = [124.5, 125.0, 125.2, 126.0, 109.0, 111.0, 139.0, 141.0]
        fit = fit_mass_spectrum(masses)
        self.assertEqual(fit["entries"], len(masses))
        self.assertGreater(fit["signal_window_count"], 0)
        self.assertGreater(fit["peak_mean"], 120)

    def test_project_roundtrip(self):
        payload = make_project_payload(
            name="Test",
            experiment_ids=[1, 2, 3],
            notes="hello",
            workspace_state={"x": 1},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.hadron-project.json"
            export_project_file(path, payload)
            loaded = import_project_file(path)
        self.assertEqual(loaded["name"], "Test")
        self.assertEqual(loaded["experiment_ids"], [1, 2, 3])
        self.assertEqual(loaded["workspace_state"]["x"], 1)


if __name__ == "__main__":
    unittest.main()
