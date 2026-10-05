import csv
import json
import math
import random
import sqlite3
import subprocess
import sys
import os
from hadron_db import connect_db
import time
import tkinter as tk
import threading
import queue
import multiprocessing
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from hadron_analysis import compare_summaries, enrich_summary, render_html_report
from hadron_engine import generate_event as engine_generate_event, passes_hlt as engine_passes_hlt, passes_l1 as engine_passes_l1
from hadron_jobs import run_job_spec
from hadron_run_state import RunCounters
from hadron_audit import list_audit, verify_audit_chain
from hadron_capsule import create_capsule_from_database, inspect_capsule, list_capsules, register_capsule
from hadron_trust import add_trusted_key, list_trusted_keys, remove_trusted_key, verify_signature_with_trust_store
from hadron_reproduce import list_reproduction_checks, reproduce_capsule_to_database, restore_capsule_study
from hadron_regression import list_regression_runs, record_regression_run, run_capsule_regression
from hadron_campaign_run import (
    campaign_health,
    check_campaign_reference,
    create_campaign_report_bundle,
    list_campaign_references,
    run_campaign_to_database,
    save_campaign_reference,
)
from hadron_pipeline import (
    create_pipeline,
    list_pipeline_runs,
    list_pipelines,
    run_pipeline_to_database,
)
from hadron_campaign import (
    add_campaign_member,
    archive_campaign,
    campaign_snapshot,
    campaign_summary_text,
    create_campaign,
    export_campaign_bundle,
    import_campaign_bundle,
    list_campaigns,
    remove_campaign_member,
)
from hadron_release import (
    load_release_manifest,
    release_status_text,
    verify_release_manifest,
    verify_release_signature,
    write_integrity_report,
    write_study_provenance,
)
from hadron_study_compare import (
    clone_study_spec,
    compare_studies,
    delete_template,
    list_templates,
    provenance_payload,
    save_template,
)
from hadron_study_analysis import (
    aggregate_by_preset_energy,
    create_study_report_package,
    filter_results,
    heatmap_grid,
    normalized_results,
    rank_results,
)
from hadron_studies import (
    create_study,
    get_study,
    list_studies,
    mark_running_studies_interrupted,
    normalize_study_spec,
    set_study_status,
    study_summary_text,
)
from hadron_system import backup_database, database_diagnostics, environment_diagnostics, restore_database, write_crash_log
from hadron_storage import (
    create_support_bundle,
    export_workspace_bundle,
    import_workspace_bundle,
    load_session,
    migrate_database,
    resolve_data_dir,
    save_session,
)
from hadron_update import check_for_update
from hadron_version import (
    RELEASE_REPOSITORY,
    SCHEMA_VERSION,
    __version__,
)
from hadron_workspace import (
    detector_efficiency_matrix,
    experiment_overlay_points,
    export_project_file,
    fit_mass_spectrum,
    import_project_file,
    make_project_payload,
    trigger_efficiency_curve,
)


# ---------------------------------------------------------------------------
# Hadron physics core
# ---------------------------------------------------------------------------

SPEED_OF_LIGHT = 299792458
PROTON_MASS_EV = 938272081
TARGET_ENERGY_GEV = 6500.0
L1_ENERGY_THRESHOLD = 5000.0
HIGGS_MASS_GEV = 125.1


class HadronCollider:
    def __init__(self):
        self.name = "Hadron"
        self.beam_energy_gev = 0.938
        self.velocity = 0.0
        self.is_squeezed = False
        self._event_rng = random.Random()

    def calculate_step(self, target_energy):
        # UI Transition helper: Call this in a loop to update your GUI progress bars
        self.beam_energy_gev += (target_energy - self.beam_energy_gev) * 0.6
        gamma = (self.beam_energy_gev * 1e9 + PROTON_MASS_EV) / PROTON_MASS_EV
        self.velocity = SPEED_OF_LIGHT * np.sqrt(1 - (1 / (gamma**2)))
        percentage_c = (self.velocity / SPEED_OF_LIGHT) * 100
        magnetic_field = 0.1 + (self.beam_energy_gev / target_energy) * 8.3
        return self.beam_energy_gev, percentage_c, magnetic_field

    def generate_collision_event(self, detector):
        """Compatibility wrapper around the shared deterministic event engine."""
        return engine_generate_event(
            self._event_rng,
            self.beam_energy_gev,
            "STANDARD",
            noise=False,
            detector=detector,
        )

    def hardware_l1_trigger(self, event):
        return (
            event["transverse_energy"] > L1_ENERGY_THRESHOLD
            or event["muon_count"] >= 2
            or event["missing_energy"] > 500.0
        )

    def software_hlt_trigger(self, event):
        for mass in event["particle_masses"]:
            if abs(mass - HIGGS_MASS_GEV) < 3.0:
                return True, f"Higgs-Boson Candidate at {mass:.2f} GeV"
        if event["missing_energy"] > 600.0:
            return True, (
                f"Dark Matter Candidate "
                f"(MET: {event['missing_energy']:.1f} GeV)"
            )
        return False, "Standard Model Background Noise"


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class HadronDashboard(ctk.CTk):
    BG = "#090B10"
    PANEL = "#11151D"
    PANEL_2 = "#151B25"
    BORDER = "#263042"
    TEXT = "#E7ECF3"
    MUTED = "#8793A5"
    CYAN = "#34D5FF"
    MAGENTA = "#FF4FD8"
    GREEN = "#5BE38B"
    RED = "#FF626F"
    GRAY = "#7D8796"
    GOLD = "#FFD34E"

    def __init__(self):
        super().__init__()

        self.title("Hadron — Particle Accelerator Simulation")
        self.geometry("1460x900")
        self.minsize(1180, 760)
        self.configure(fg_color=self.BG)

        self.collider = HadronCollider()

        self.target_energy = TARGET_ENERGY_GEV
        self.current_magnetic_field = 0.1

        self.is_ramping = False
        self.stream_running = False
        self.stream_paused = False
        self.animation_running = True
        self.beam_angle = 0.0
        self.collision_count = 0
        self.saved_count = 0
        self.discarded_count = 0
        self.run_counters = RunCounters()
        self.collision_rate_hz = 3.0
        self.current_run_id = None
        self.accepted_events = []
        self.last_event = None

        self.mass_history = []
        self.higgs_candidates = []
        self.event_series = []

        # v0.3 visualization state
        self.plot_mode = "MASS"
        self.collision_bursts = []
        self.detector_window = None
        self.detector_canvas = None
        self.event3d_window = None
        self.event3d_canvas = None
        self.history_window = None
        self.history_list = None
        self.history_detail = None
        self.history_rows = []

        # v0.4 simulation configuration
        self.physics_presets = {
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
        self.active_preset = "STANDARD"
        self.detector_noise_enabled = True
        self.detector_noise_sigma = 0.9
        self.detector_resolution_sigma = 0.012
        self.replay_event = None

        # v0.5 analysis controls
        self.l1_energy_threshold = L1_ENERGY_THRESHOLD
        self.met_trigger_threshold = 500.0
        self.higgs_window_gev = 3.0
        self.event_inspector_window = None
        self.event_inspector_list = None
        self.event_inspector_detail = None
        self.event_inspector_rows = []
        self.bookmarks_window = None
        self.bookmark_list = None
        self.bookmark_rows = []
        self.compare_window = None
        self.workspace_window = None
        self.workspace_tabs = None
        self.workspace_overlay_canvas = None
        self.workspace_trigger_canvas = None
        self.workspace_matrix_canvas = None
        self.workspace_mass_canvas = None
        self.workspace_project_list = None
        self.workspace_status = None
        self.system_window = None
        self.system_text = None
        self.study_window = None
        self.study_list = None
        self.study_detail = None
        self.study_rows = []
        self.study_active_id = None
        self.study_thread = None
        self.study_messages = queue.Queue()
        self.study_results_window = None
        self.study_results_tabs = None
        self.study_results_filter_preset = "ALL"
        self.study_results_selected_id = None
        self.study_compare_window = None
        self.study_template_window = None
        self.study_template_list = None
        self.study_template_rows = []
        self.release_window = None
        self.release_text = None
        self.release_manifest_path = None
        self.release_manifest_data = None
        self.release_signature_path = None
        self.release_public_key_path = None
        self.release_signature_report = None
        self.audit_window = None
        self.audit_text = None
        self.capsule_window = None
        self.capsule_list = None
        self.capsule_rows = []
        self.trust_list = None
        self.trust_rows = []
        self.reproduction_list = None
        self.reproduction_rows = []
        self.regression_window = None
        self.regression_list = None
        self.regression_rows = []
        self.regression_detail = None
        self.regression_status = None
        self.regression_thread = None
        self.regression_messages = queue.Queue()
        self.campaign_window = None
        self.campaign_list = None
        self.campaign_rows = []
        self.campaign_detail = None
        self.campaign_member_list = None
        self.campaign_member_rows = []
        self.campaign_run_thread = None
        self.campaign_run_messages = queue.Queue()
        self.pipeline_window = None
        self.pipeline_list = None
        self.pipeline_rows = []
        self.pipeline_run_list = None
        self.pipeline_run_rows = []
        self.pipeline_detail = None
        self.pipeline_status = None
        self.pipeline_thread = None
        self.pipeline_messages = queue.Queue()
        self.update_window = None
        self.update_text = None
        self.update_status = None
        self.update_thread = None
        self.update_messages = queue.Queue()
        self.update_info = None

        # v0.6 experiment state
        self.experiment_window = None
        self.experiment_progress = None
        self.experiment_status = None
        self.experiment_results_box = None
        self.experiment_cancelled = False
        self.active_experiment = None
        self.event_filter = "ALL"

        self.data_dir = resolve_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        self.session_path = self.data_dir / "session.json"
        self.db_path = self.data_dir / "hadron_runs.db"
        self.detector_selection = "AUTO"
        self.migration_info = self._init_database()
        self._load_settings()
        self._load_session_state()

        self._build_grid()
        self._build_sidebar()
        self._build_main()
        self._build_log()
        self._apply_session_to_widgets()
        self._update_telemetry()
        self._draw_ring()
        self._refresh_plot()

        self.after(16, self._animate_ring)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._install_crash_handler()

        self.log(
            "SYSTEM",
            "Hadron v3.0 stable initialized · signed updates from "
            + RELEASE_REPOSITORY,
            "system",
        )
        if os.environ.get("HADRON_DISABLE_UPDATE_CHECK", "").strip() != "1":
            self.after(2500, self._start_auto_update_check)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_grid(self):
        self.grid_columnconfigure(0, weight=0, minsize=275)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0, minsize=225)

    def _panel(self, parent, **kwargs):
        return ctk.CTkFrame(
            parent,
            fg_color=self.PANEL,
            border_color=self.BORDER,
            border_width=1,
            corner_radius=14,
            **kwargs,
        )

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(
            self,
            width=285,
            corner_radius=0,
            fg_color="#0C1017",
            border_width=0,
        )
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)

        brand = ctk.CTkLabel(
            self.sidebar,
            text="HADRON",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=29, weight="bold"),
        )
        brand.grid(row=0, column=0, padx=24, pady=(22, 0), sticky="w")

        subtitle = ctk.CTkLabel(
            self.sidebar,
            text="ACCELERATOR CONTROL · v3.0 STABLE",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        subtitle.grid(row=1, column=0, padx=25, pady=(0, 18), sticky="w")

        section = ctk.CTkLabel(
            self.sidebar,
            text="BEAM OPERATIONS",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        section.grid(row=2, column=0, padx=24, pady=(0, 5), sticky="w")

        self.ramp_btn = ctk.CTkButton(
            self.sidebar,
            text="RAMP BEAMS",
            command=self.ramp_beams,
            height=38,
            corner_radius=10,
            fg_color="#18566C",
            hover_color="#216F89",
            text_color=self.TEXT,
            font=ctk.CTkFont(weight="bold"),
        )
        self.ramp_btn.grid(row=3, column=0, padx=22, pady=4, sticky="ew")

        self.squeeze_btn = ctk.CTkButton(
            self.sidebar,
            text="SQUEEZE BEAMS",
            command=self.squeeze_beams,
            height=38,
            corner_radius=10,
            fg_color="#3D2855",
            hover_color="#53356F",
            text_color=self.TEXT,
            font=ctk.CTkFont(weight="bold"),
        )
        self.squeeze_btn.grid(row=4, column=0, padx=22, pady=4, sticky="ew")

        stream_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        stream_row.grid(row=5, column=0, padx=22, pady=4, sticky="ew")
        stream_row.grid_columnconfigure((0, 1), weight=1)

        self.stream_btn = ctk.CTkButton(
            stream_row,
            text="START",
            command=self.toggle_stream,
            height=38,
            corner_radius=10,
            fg_color="#1B6A43",
            hover_color="#238958",
            text_color=self.TEXT,
            font=ctk.CTkFont(weight="bold"),
        )
        self.stream_btn.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self.pause_btn = ctk.CTkButton(
            stream_row,
            text="PAUSE",
            command=self.toggle_pause,
            height=38,
            corner_radius=10,
            fg_color="#554B24",
            hover_color="#746632",
            text_color=self.TEXT,
            font=ctk.CTkFont(weight="bold"),
            state="disabled",
        )
        self.pause_btn.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.reset_btn = ctk.CTkButton(
            self.sidebar,
            text="RESET RUN",
            command=self.reset_run,
            height=34,
            corner_radius=10,
            fg_color="#342D3C",
            hover_color="#4A4056",
            text_color=self.TEXT,
        )
        self.reset_btn.grid(row=6, column=0, padx=22, pady=(4, 8), sticky="ew")

        divider = ctk.CTkFrame(self.sidebar, height=1, fg_color=self.BORDER)
        divider.grid(row=7, column=0, padx=22, pady=10, sticky="ew")

        energy_label = ctk.CTkLabel(
            self.sidebar,
            text="TARGET BEAM ENERGY",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        energy_label.grid(row=8, column=0, padx=24, pady=(0, 3), sticky="w")

        self.energy_value = ctk.CTkLabel(
            self.sidebar,
            text=f"{self.target_energy:,.0f} GeV",
            text_color=self.CYAN,
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        self.energy_value.grid(row=9, column=0, padx=24, pady=(0, 3), sticky="w")

        self.energy_slider = ctk.CTkSlider(
            self.sidebar,
            from_=1000,
            to=7000,
            number_of_steps=60,
            command=self._on_energy_change,
        )
        self.energy_slider.set(self.target_energy)
        self.energy_slider.grid(row=10, column=0, padx=24, pady=(2, 2), sticky="ew")

        self.beam_progress = ctk.CTkProgressBar(
            self.sidebar,
            height=7,
            progress_color=self.CYAN,
        )
        self.beam_progress.set(self.collider.beam_energy_gev / self.target_energy)
        self.beam_progress.grid(row=11, column=0, padx=24, pady=(9, 2), sticky="ew")

        self.progress_label = ctk.CTkLabel(
            self.sidebar,
            text="Beam charge: idle",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10),
        )
        self.progress_label.grid(row=12, column=0, padx=24, sticky="w")

        detector_label = ctk.CTkLabel(
            self.sidebar,
            text="DETECTOR",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        detector_label.grid(row=13, column=0, padx=24, pady=(10, 3), sticky="w")

        self.detector_menu = ctk.CTkOptionMenu(
            self.sidebar,
            values=["AUTO", "ATLAS-SIM", "CMS-SIM", "INNER-TRACKER", "CALORIMETER"],
            fg_color=self.PANEL_2,
            button_color="#253044",
            button_hover_color="#34425A",
        )
        self.detector_menu.set(self.detector_selection)
        self.detector_menu.grid(row=14, column=0, padx=22, sticky="ew")

        rate_label = ctk.CTkLabel(
            self.sidebar,
            text="COLLISION RATE",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        rate_label.grid(row=15, column=0, padx=24, pady=(10, 3), sticky="w")

        self.rate_value = ctk.CTkLabel(
            self.sidebar,
            text=f"{self.collision_rate_hz:.1f} events/s",
            text_color=self.MAGENTA,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.rate_value.grid(row=16, column=0, padx=24, sticky="w")

        self.rate_slider = ctk.CTkSlider(
            self.sidebar,
            from_=1.0,
            to=12.0,
            number_of_steps=22,
            command=self._on_rate_change,
        )
        self.rate_slider.set(self.collision_rate_hz)
        self.rate_slider.grid(row=17, column=0, padx=24, pady=(2, 5), sticky="ew")

        preset_label = ctk.CTkLabel(
            self.sidebar,
            text="PHYSICS PRESET",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        preset_label.grid(row=18, column=0, padx=24, pady=(8, 3), sticky="w")

        self.preset_menu = ctk.CTkOptionMenu(
            self.sidebar,
            values=list(self.physics_presets.keys()),
            command=self._on_preset_change,
            fg_color=self.PANEL_2,
            button_color="#253044",
            button_hover_color="#34425A",
        )
        self.preset_menu.set(self.active_preset)
        self.preset_menu.grid(row=19, column=0, padx=22, sticky="ew")

        view_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        view_row.grid(row=20, column=0, padx=22, pady=(6, 4), sticky="ew")
        view_row.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkButton(
            view_row,
            text="DETECTOR",
            command=self.open_detector_view,
            height=32,
            fg_color="#26364A",
            hover_color="#354C68",
        ).grid(row=0, column=0, padx=(0, 3), sticky="ew")

        ctk.CTkButton(
            view_row,
            text="EVENT 3D",
            command=self.open_event3d_view,
            height=32,
            fg_color="#26364A",
            hover_color="#354C68",
        ).grid(row=0, column=1, padx=3, sticky="ew")

        ctk.CTkButton(
            view_row,
            text="HISTORY",
            command=self.open_run_history,
            height=32,
            fg_color="#26364A",
            hover_color="#354C68",
        ).grid(row=0, column=2, padx=(3, 0), sticky="ew")

        analysis_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        analysis_row.grid(row=21, column=0, padx=22, pady=(2, 3), sticky="ew")
        analysis_row.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            analysis_row,
            text="TRIGGER LAB",
            command=self.open_trigger_lab,
            height=30,
            fg_color="#3B334C",
            hover_color="#514567",
        ).grid(row=0, column=0, padx=(0, 3), sticky="ew")

        ctk.CTkButton(
            analysis_row,
            text="EVENTS",
            command=self.open_event_inspector,
            height=30,
            fg_color="#3B334C",
            hover_color="#514567",
        ).grid(row=0, column=1, padx=(3, 0), sticky="ew")

        lab_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        lab_row.grid(row=22, column=0, padx=22, pady=(2, 3), sticky="ew")
        lab_row.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkButton(
            lab_row,
            text="EXPERIMENT",
            command=self.open_experiment_lab,
            height=30,
            fg_color="#25495B",
            hover_color="#32647A",
        ).grid(row=0, column=0, padx=(0, 3), sticky="ew")

        ctk.CTkButton(
            lab_row,
            text="WORKSPACE",
            command=self.open_analysis_workspace,
            height=30,
            fg_color="#264758",
            hover_color="#37657A",
        ).grid(row=0, column=1, padx=3, sticky="ew")

        ctk.CTkButton(
            lab_row,
            text="STUDIES",
            command=self.open_study_queue,
            height=30,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=2, padx=(3, 0), sticky="ew")

        tools_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        tools_row.grid(row=23, column=0, padx=22, pady=(2, 3), sticky="ew")
        tools_row.grid_columnconfigure((0, 1, 2), weight=1)

        self.settings_btn = ctk.CTkButton(
            tools_row,
            text="SETTINGS",
            command=self.open_settings,
            height=30,
            fg_color="#2C2F3B",
            hover_color="#3C4151",
        )
        self.settings_btn.grid(row=0, column=0, padx=(0, 3), sticky="ew")

        ctk.CTkButton(
            tools_row,
            text="COMPARE",
            command=self.open_run_compare,
            height=30,
            fg_color="#2C2F3B",
            hover_color="#3C4151",
        ).grid(row=0, column=1, padx=3, sticky="ew")

        ctk.CTkButton(
            tools_row,
            text="SYSTEM",
            command=self.open_system_tools,
            height=30,
            fg_color="#2C2F3B",
            hover_color="#3C4151",
        ).grid(row=0, column=2, padx=(3, 0), sticky="ew")

        export_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        export_row.grid(row=24, column=0, padx=22, pady=(2, 4), sticky="ew")
        export_row.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            export_row,
            text="EXPORT CSV",
            command=self.export_csv,
            height=32,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            export_row,
            text="EXPORT JSON",
            command=self.export_json,
            height=32,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.sidebar.grid_rowconfigure(25, weight=1)

        footer = ctk.CTkLabel(
            self.sidebar,
            text="LOCAL RUN DATABASE\n~/.hadron/hadron_runs.db",
            text_color="#5F6978",
            justify="left",
            font=ctk.CTkFont(size=9),
        )
        footer.grid(row=26, column=0, padx=24, pady=14, sticky="sw")

    def _build_main(self):
        self.main = ctk.CTkFrame(self, fg_color="transparent")
        self.main.grid(row=0, column=1, padx=16, pady=(16, 8), sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(1, weight=1)

        # Telemetry
        telemetry = ctk.CTkFrame(self.main, fg_color="transparent")
        telemetry.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        for i in range(3):
            telemetry.grid_columnconfigure(i, weight=1)

        self.speed_value = self._telemetry_card(
            telemetry, 0, "BEAM SPEED", "0.000000 % c", self.CYAN
        )
        self.telemetry_energy = self._telemetry_card(
            telemetry, 1, "BEAM ENERGY", "0.938 GeV", self.MAGENTA
        )
        self.magnet_value = self._telemetry_card(
            telemetry, 2, "DIPOLE FIELD", "0.10 T", self.GREEN
        )

        # Center workspace
        workspace = ctk.CTkFrame(self.main, fg_color="transparent")
        workspace.grid(row=1, column=0, sticky="nsew")
        workspace.grid_columnconfigure(0, weight=1)
        workspace.grid_columnconfigure(1, weight=1)
        workspace.grid_rowconfigure(0, weight=1)

        self.ring_panel = self._panel(workspace)
        self.ring_panel.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        self.ring_panel.grid_rowconfigure(1, weight=1)
        self.ring_panel.grid_columnconfigure(0, weight=1)

        ring_title = ctk.CTkLabel(
            self.ring_panel,
            text="VISUAL ACCELERATOR",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        ring_title.grid(row=0, column=0, padx=18, pady=(14, 4), sticky="w")

        self.ring_canvas = tk.Canvas(
            self.ring_panel,
            bg=self.PANEL,
            bd=0,
            highlightthickness=0,
        )
        self.ring_canvas.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 6))
        self.ring_canvas.bind("<Configure>", lambda _e: self._draw_ring())

        self.event_detail = ctk.CTkLabel(
            self.ring_panel,
            text="LAST EVENT · waiting for stream",
            text_color=self.MUTED,
            anchor="w",
            justify="left",
            font=ctk.CTkFont(size=10),
        )
        self.event_detail.grid(row=2, column=0, padx=16, pady=(0, 10), sticky="ew")

        self.plot_panel = self._panel(workspace)
        self.plot_panel.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        self.plot_panel.grid_rowconfigure(1, weight=1)
        self.plot_panel.grid_columnconfigure(0, weight=1)

        plot_header = ctk.CTkFrame(self.plot_panel, fg_color="transparent")
        plot_header.grid(row=0, column=0, padx=14, pady=(10, 2), sticky="ew")
        plot_header.grid_columnconfigure(0, weight=1)

        self.plot_title = ctk.CTkLabel(
            plot_header,
            text="INVARIANT MASS DISTRIBUTION",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.plot_title.grid(row=0, column=0, padx=(4, 8), sticky="w")

        self.plot_selector = ctk.CTkSegmentedButton(
            plot_header,
            values=["MASS", "LIVE STATS"],
            command=self._on_plot_mode,
            height=28,
        )
        self.plot_selector.set(self.plot_mode)
        self.plot_selector.grid(row=0, column=1, sticky="e")

        ctk.CTkButton(
            plot_header,
            text="SNAPSHOT",
            command=self.export_plot_snapshot,
            width=78,
            height=28,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=0, column=2, padx=(8, 0), sticky="e")

        self.figure = Figure(figsize=(5.4, 4.2), dpi=100, facecolor=self.PANEL)
        self.ax = self.figure.add_subplot(111)
        self.figure.subplots_adjust(left=0.13, right=0.97, top=0.93, bottom=0.15)

        self.plot_canvas = FigureCanvasTkAgg(self.figure, master=self.plot_panel)
        self.plot_canvas.get_tk_widget().configure(
            bg=self.PANEL,
            highlightthickness=0,
        )
        self.plot_canvas.get_tk_widget().grid(
            row=1, column=0, sticky="nsew", padx=8, pady=(0, 8)
        )

    def _telemetry_card(self, parent, col, label, value, accent):
        card = self._panel(parent)
        card.grid(
            row=0,
            column=col,
            padx=(0 if col == 0 else 6, 0 if col == 2 else 6),
            sticky="ew",
        )

        ctk.CTkLabel(
            card,
            text=label,
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(13, 0))

        value_label = ctk.CTkLabel(
            card,
            text=value,
            text_color=accent,
            font=ctk.CTkFont(size=21, weight="bold"),
        )
        value_label.pack(anchor="w", padx=16, pady=(0, 13))
        return value_label

    def _build_log(self):
        log_panel = self._panel(self)
        log_panel.grid(
            row=1,
            column=1,
            padx=16,
            pady=(8, 16),
            sticky="nsew",
        )
        log_panel.grid_columnconfigure(0, weight=1)
        log_panel.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(log_panel, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header,
            text="THE GRID LOG",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=16, pady=10, sticky="w")

        self.stats_label = ctk.CTkLabel(
            header,
            text="EVENTS 0   SAVED 0   DISCARDED 0",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10),
        )
        self.stats_label.grid(row=0, column=1, padx=16, pady=10, sticky="e")

        log_wrap = ctk.CTkFrame(log_panel, fg_color="#090D13", corner_radius=9)
        log_wrap.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="nsew")
        log_wrap.grid_rowconfigure(0, weight=1)
        log_wrap.grid_columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_wrap,
            bg="#090D13",
            fg=self.TEXT,
            insertbackground=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=12,
            pady=8,
            font=("Consolas", 10),
        )
        scrollbar = ctk.CTkScrollbar(log_wrap, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 4), pady=4)

        self.log_text.tag_configure("saved", foreground=self.GREEN)
        self.log_text.tag_configure("discarded", foreground=self.RED)
        self.log_text.tag_configure("background", foreground=self.GRAY)
        self.log_text.tag_configure("system", foreground=self.CYAN)
        self.log_text.tag_configure("higgs", foreground=self.GOLD)
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    def _on_energy_change(self, value):
        self.target_energy = float(value)
        self.energy_value.configure(text=f"{self.target_energy:,.0f} GeV")
        ratio = min(1.0, self.collider.beam_energy_gev / self.target_energy)
        self.beam_progress.set(ratio)

    def _on_rate_change(self, value):
        self.collision_rate_hz = float(value)
        self.rate_value.configure(text=f"{self.collision_rate_hz:.1f} events/s")
        self._save_settings()

    def _on_plot_mode(self, value):
        self.plot_mode = value
        self._refresh_plot()

    def _on_preset_change(self, value):
        self.active_preset = value
        preset = self.physics_presets[value]
        self.detector_noise_sigma = float(preset["noise_sigma"])
        self.detector_resolution_sigma = float(preset["resolution_sigma"])
        self._save_settings()
        self.log(
            "PRESET",
            f"{value} selected · noise σ={self.detector_noise_sigma:.2f} GeV · "
            f"resolution={self.detector_resolution_sigma*100:.2f}%",
            "system",
        )

    def ramp_beams(self):
        if self.is_ramping:
            return

        self.is_ramping = True
        self.ramp_btn.configure(state="disabled", text="RAMPING...")
        self.log(
            "CONTROL",
            f"Ramping beam toward {self.target_energy:,.0f} GeV.",
            "system",
        )
        self._ramp_step()

    def _ramp_step(self):
        if not self.is_ramping:
            return

        energy, speed_pct, field = self.collider.calculate_step(self.target_energy)
        self.current_magnetic_field = field
        self._update_telemetry(speed_pct=speed_pct)

        ratio = min(1.0, energy / self.target_energy)
        self.beam_progress.set(ratio)
        self.progress_label.configure(text=f"Beam charge: {ratio * 100:05.1f}%")

        if abs(self.target_energy - energy) < max(0.2, self.target_energy * 0.0001):
            self.collider.beam_energy_gev = self.target_energy
            self.is_ramping = False
            self.ramp_btn.configure(state="normal", text="RAMP BEAMS")
            self.beam_progress.set(1.0)
            self.progress_label.configure(text="Beam charge: nominal")
            self._update_telemetry()
            self.log(
                "CONTROL",
                f"Beam reached {self.collider.beam_energy_gev:,.1f} GeV.",
                "saved",
            )
            return

        self.after(180, self._ramp_step)

    def squeeze_beams(self):
        self.collider.is_squeezed = not self.collider.is_squeezed
        if self.collider.is_squeezed:
            self.squeeze_btn.configure(text="UNSQUEEZE BEAMS")
            self.log(
                "OPTICS",
                "Beam squeeze enabled. Collision focus tightened.",
                "system",
            )
        else:
            self.squeeze_btn.configure(text="SQUEEZE BEAMS")
            self.log(
                "OPTICS",
                "Beam squeeze disabled.",
                "background",
            )
        self._draw_ring()

    def toggle_stream(self):
        if not self.stream_running:
            if self.collider.beam_energy_gev * 2 < 2000.0:
                self.log(
                    "DISCARDED",
                    "Stream blocked: ramp beam above 1,000 GeV first.",
                    "discarded",
                )
                return

            self.stream_running = True
            self.stream_paused = False
            self.pause_btn.configure(state="normal", text="PAUSE")
            self.stream_btn.configure(
                text="STOP",
                fg_color="#71303A",
                hover_color="#923E4B",
            )
            self._start_run_record()
            self.log("DAQ", "Collision event stream started.", "system")
            self._stream_step()
        else:
            self.stream_running = False
            self.stream_paused = False
            self.pause_btn.configure(state="disabled", text="PAUSE")
            self.stream_btn.configure(
                text="START",
                fg_color="#1B6A43",
                hover_color="#238958",
            )
            self._finish_run_record()
            self.log("DAQ", "Collision event stream stopped.", "background")

    def toggle_pause(self):
        if not self.stream_running:
            return
        self.stream_paused = not self.stream_paused
        if self.stream_paused:
            self.pause_btn.configure(text="RESUME")
            self.log("DAQ", "Collision stream paused.", "background")
        else:
            self.pause_btn.configure(text="PAUSE")
            self.log("DAQ", "Collision stream resumed.", "system")
            self._stream_step()

    def reset_run(self):
        if self.stream_running:
            self.stream_running = False
            self.stream_paused = False
            self._finish_run_record()

        self.pause_btn.configure(state="disabled", text="PAUSE")
        self.stream_btn.configure(
            text="START",
            fg_color="#1B6A43",
            hover_color="#238958",
        )

        self.collision_count = 0
        self.saved_count = 0
        self.discarded_count = 0
        self.accepted_events.clear()
        self.mass_history.clear()
        self.higgs_candidates.clear()
        self.event_series.clear()
        self.collision_bursts.clear()
        self.last_event = None
        self._update_stats()
        self._refresh_plot()
        self._draw_collision_effects(advance=False)
        self._draw_detector_cross_section()
        self.event_detail.configure(text="LAST EVENT · waiting for stream")
        self.log("CONTROL", "Run counters and in-memory event data reset.", "system")

    # ------------------------------------------------------------------
    # Physics presets + detector response
    # ------------------------------------------------------------------

    def _generate_event_with_rng(
        self,
        detector,
        rng,
        beam_energy_gev,
        preset_name,
        noise_enabled=None,
        noise_sigma=None,
        resolution_sigma=None,
    ):
        if noise_enabled is None:
            noise_enabled = self.detector_noise_enabled
        if noise_sigma is None:
            noise_sigma = self.detector_noise_sigma
        if resolution_sigma is None:
            resolution_sigma = self.detector_resolution_sigma

        return engine_generate_event(
            rng=rng,
            energy=beam_energy_gev,
            preset_name=preset_name,
            noise=noise_enabled,
            detector=detector,
            noise_sigma=noise_sigma,
            resolution_sigma=resolution_sigma,
        )

    def _generate_configured_event(self, detector):
        return self._generate_event_with_rng(
            detector=detector,
            rng=random,
            beam_energy_gev=self.collider.beam_energy_gev,
            preset_name=self.active_preset,
        )

    def _hardware_l1_trigger(self, event):
        return engine_passes_l1(
            event,
            self.l1_energy_threshold,
            self.met_trigger_threshold,
        )

    def _software_hlt_trigger(self, event):
        keep, _is_higgs, reason = engine_passes_hlt(
            event,
            self.met_trigger_threshold,
            self.higgs_window_gev,
        )
        return keep, reason

    # ------------------------------------------------------------------
    # Event simulation
    # ------------------------------------------------------------------

    def _stream_step(self):
        if not self.stream_running or self.stream_paused:
            return

        selected = self.detector_menu.get()
        detector = (
            random.choice(["ATLAS-SIM", "CMS-SIM", "INNER-TRACKER", "CALORIMETER"])
            if selected == "AUTO"
            else selected
        )

        event = self._generate_configured_event(detector)
        self.collision_count += 1
        self.run_counters.record_collision()
        self.last_event = event

        et = event["transverse_energy"]
        met = event["missing_energy"]
        mu = event["muon_count"]
        status = "L1_REJECT"
        reason = "Hardware L1 rejection"

        if not self._hardware_l1_trigger(event):
            self.discarded_count += 1
            self.run_counters.record_discarded()
            self.log(
                "DISCARDED",
                f"#{self.collision_count:05d} L1 reject | "
                f"{detector} | ET {et:7.1f} GeV | MET {met:6.1f} GeV | μ {mu}",
                "discarded",
            )
        else:
            keep, reason = self._software_hlt_trigger(event)
            if keep:
                status = "SAVED"
                self.saved_count += 1
                self.run_counters.record_saved()
                self.mass_history.extend(event["particle_masses"])

                is_higgs = any(
                    abs(m - HIGGS_MASS_GEV) < 3.0
                    for m in event["particle_masses"]
                )
                if is_higgs:
                    self.higgs_candidates.extend(
                        m
                        for m in event["particle_masses"]
                        if abs(m - HIGGS_MASS_GEV) < 3.0
                    )

                record = {
                    "event_number": self.collision_count,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "detector": detector,
                    "transverse_energy": et,
                    "missing_energy": met,
                    "muon_count": mu,
                    "particle_masses": list(event["particle_masses"]),
                    "reason": reason,
                    "is_higgs_candidate": is_higgs,
                }
                self.accepted_events.append(record)
                record["db_event_id"] = self._save_event_record(record)

                tag = "higgs" if is_higgs else "saved"
                self.log(
                    "SAVED",
                    f"#{self.collision_count:05d} HLT accept | {reason} | "
                    f"masses={', '.join(f'{m:.2f}' for m in event['particle_masses'])}",
                    tag,
                )
                # Plot refresh happens after the event is fully accounted for.
            else:
                status = "HLT_REJECT"
                self.discarded_count += 1
                self.run_counters.record_discarded()
                self.log(
                    "DISCARDED",
                    f"#{self.collision_count:05d} HLT reject | "
                    f"{reason} | ET {et:7.1f} GeV",
                    "background",
                )

        self.event_detail.configure(
            text=(
                f"LAST EVENT #{self.collision_count:05d} · {status} · {detector}  |  "
                f"ET {et:.1f} GeV  |  MET {met:.1f} GeV  |  μ {mu}"
            ),
            text_color=self.GREEN if status == "SAVED" else self.MUTED,
        )

        self.event_series.append(
            {
                "event": self.collision_count,
                "saved": self.saved_count,
                "discarded": self.discarded_count,
                "higgs": len(self.higgs_candidates),
                "status": status,
            }
        )
        self._spawn_collision_effect(event, status)
        self._update_stats()
        self._refresh_plot()

        delay = max(40, int(1000 / max(self.collision_rate_hz, 0.1)))
        self.after(delay, self._stream_step)

    # ------------------------------------------------------------------
    # Accelerator ring
    # ------------------------------------------------------------------

    def _draw_ring(self):
        canvas = self.ring_canvas
        canvas.delete("all")

        w = max(canvas.winfo_width(), 20)
        h = max(canvas.winfo_height(), 20)
        cx, cy = w / 2, h / 2

        radius = max(45, min(w, h) * (0.31 if self.collider.is_squeezed else 0.36))
        outer = radius + 26
        inner = radius - 26

        canvas.create_oval(
            cx - outer,
            cy - outer,
            cx + outer,
            cy + outer,
            outline="#202A38",
            width=2,
        )
        canvas.create_oval(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            outline="#466176",
            width=5,
        )
        canvas.create_oval(
            cx - inner,
            cy - inner,
            cx + inner,
            cy + inner,
            outline="#202A38",
            width=2,
        )

        # Interaction points
        for ang in (0, math.pi / 2, math.pi, 3 * math.pi / 2):
            x = cx + math.cos(ang) * radius
            y = cy + math.sin(ang) * radius
            r = 5
            canvas.create_oval(
                x - r, y - r, x + r, y + r,
                fill="#C9D3DF",
                outline="",
            )

        # Center labels
        canvas.create_text(
            cx,
            cy - 11,
            text="HADRON RING",
            fill=self.TEXT,
            font=("TkDefaultFont", 13, "bold"),
        )
        status = "SQUEEZED" if self.collider.is_squeezed else "NOMINAL OPTICS"
        canvas.create_text(
            cx,
            cy + 12,
            text=status,
            fill=self.MUTED,
            font=("TkDefaultFont", 9),
        )

        self._draw_beams()
        self._draw_collision_effects(advance=False)

    def _draw_beams(self):
        canvas = self.ring_canvas
        canvas.delete("beam")

        w = max(canvas.winfo_width(), 20)
        h = max(canvas.winfo_height(), 20)
        cx, cy = w / 2, h / 2
        radius = max(45, min(w, h) * (0.31 if self.collider.is_squeezed else 0.36))

        # Two counter-rotating bunches.
        a1 = self.beam_angle
        a2 = -self.beam_angle + math.pi

        for angle, color in ((a1, self.CYAN), (a2, self.MAGENTA)):
            x = cx + math.cos(angle) * radius
            y = cy + math.sin(angle) * radius

            # Glow rings
            canvas.create_oval(
                x - 13,
                y - 13,
                x + 13,
                y + 13,
                outline=color,
                width=1,
                tags="beam",
            )
            canvas.create_oval(
                x - 7,
                y - 7,
                x + 7,
                y + 7,
                fill=color,
                outline="#FFFFFF",
                width=1,
                tags="beam",
            )

    def _animate_ring(self):
        if not self.animation_running:
            return

        # Rotation rate scales gently with beam energy.
        ratio = min(1.0, self.collider.beam_energy_gev / max(self.target_energy, 1.0))
        self.beam_angle = (self.beam_angle + 0.012 + ratio * 0.085) % (math.pi * 2)
        self._draw_beams()
        self._draw_collision_effects(advance=True)
        self._draw_detector_cross_section()
        self._draw_event3d()
        self.after(16, self._animate_ring)

    # ------------------------------------------------------------------
    # Detector visualization
    # ------------------------------------------------------------------

    def _spawn_collision_effect(self, event, status):
        particle_count = max(
            4,
            min(
                14,
                len(event["particle_masses"])
                + event["muon_count"]
                + random.randint(2, 5),
            ),
        )

        base_colors = [self.CYAN, self.MAGENTA, self.GREEN, self.GOLD, "#D4E2F0"]
        tracks = []
        for i in range(particle_count):
            tracks.append(
                {
                    "angle": random.uniform(0, math.tau),
                    "length_scale": random.uniform(0.55, 1.0),
                    "curve": random.uniform(-0.28, 0.28),
                    "color": base_colors[i % len(base_colors)],
                }
            )

        self.collision_bursts.append(
            {
                "age": 0,
                "max_age": 38 if status == "SAVED" else 24,
                "status": status,
                "tracks": tracks,
                "energy": event["transverse_energy"],
            }
        )
        self.collision_bursts = self.collision_bursts[-5:]

    def _draw_collision_effects(self, advance=False):
        if not hasattr(self, "ring_canvas"):
            return

        canvas = self.ring_canvas
        canvas.delete("effect")

        w = max(canvas.winfo_width(), 20)
        h = max(canvas.winfo_height(), 20)
        cx, cy = w / 2, h / 2
        max_radius = max(35, min(w, h) * 0.30)

        alive = []
        for burst in self.collision_bursts:
            if advance:
                burst["age"] += 1

            age = burst["age"]
            max_age = burst["max_age"]
            if age >= max_age:
                continue

            alive.append(burst)
            phase = age / max_age
            travel = min(1.0, phase * 2.0)

            if age < 9:
                flash_r = 5 + age * 4.0
                flash_color = self.GOLD if burst["status"] == "SAVED" else self.CYAN
                canvas.create_oval(
                    cx - flash_r,
                    cy - flash_r,
                    cx + flash_r,
                    cy + flash_r,
                    outline=flash_color,
                    width=max(1, 4 - age // 3),
                    tags="effect",
                )

            for track in burst["tracks"]:
                angle = track["angle"]
                length = max_radius * track["length_scale"] * travel
                bend = track["curve"] * travel

                x1 = cx + math.cos(angle) * 5
                y1 = cy + math.sin(angle) * 5
                mid_angle = angle + bend * 0.45
                end_angle = angle + bend
                mx = cx + math.cos(mid_angle) * length * 0.55
                my = cy + math.sin(mid_angle) * length * 0.55
                x2 = cx + math.cos(end_angle) * length
                y2 = cy + math.sin(end_angle) * length

                color = track["color"] if phase < 0.62 else "#465365"
                canvas.create_line(
                    x1,
                    y1,
                    mx,
                    my,
                    x2,
                    y2,
                    smooth=True,
                    splinesteps=10,
                    fill=color,
                    width=2 if burst["status"] == "SAVED" else 1,
                    tags="effect",
                )
                if phase < 0.7:
                    r = 2.5
                    canvas.create_oval(
                        x2 - r,
                        y2 - r,
                        x2 + r,
                        y2 + r,
                        fill=color,
                        outline="",
                        tags="effect",
                    )

        self.collision_bursts = alive

    def open_detector_view(self):
        if self.detector_window is not None and self.detector_window.winfo_exists():
            self.detector_window.focus()
            return

        self.detector_window = ctk.CTkToplevel(self)
        self.detector_window.title("Hadron — Detector Cross-Section")
        self.detector_window.geometry("820x720")
        self.detector_window.minsize(640, 560)
        self.detector_window.configure(fg_color=self.BG)
        self.detector_window.grid_columnconfigure(0, weight=1)
        self.detector_window.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.detector_window, fg_color="transparent")
        header.grid(row=0, column=0, padx=18, pady=(16, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="DETECTOR CROSS-SECTION",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        self.detector_status = ctk.CTkLabel(
            header,
            text="Waiting for collision data",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=11),
        )
        self.detector_status.grid(row=1, column=0, sticky="w")

        self.detector_canvas = tk.Canvas(
            self.detector_window,
            bg=self.PANEL,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.BORDER,
        )
        self.detector_canvas.grid(
            row=1, column=0, padx=18, pady=(0, 18), sticky="nsew"
        )
        self.detector_canvas.bind(
            "<Configure>", lambda _e: self._draw_detector_cross_section()
        )
        self.detector_window.protocol("WM_DELETE_WINDOW", self._close_detector_view)
        self._draw_detector_cross_section()

    def _close_detector_view(self):
        if self.detector_window is not None:
            self.detector_window.destroy()
        self.detector_window = None
        self.detector_canvas = None

    def _draw_detector_cross_section(self):
        canvas = self.detector_canvas
        if canvas is None:
            return
        try:
            if not canvas.winfo_exists():
                return
        except tk.TclError:
            return

        canvas.delete("all")
        w = max(canvas.winfo_width(), 40)
        h = max(canvas.winfo_height(), 40)
        cx, cy = w / 2, h / 2
        base = min(w, h) * 0.40

        layers = [
            (1.00, "#253246", "Muon system"),
            (0.80, "#283B43", "Hadronic calorimeter"),
            (0.61, "#44384D", "EM calorimeter"),
            (0.42, "#263C48", "Tracker"),
            (0.18, "#2F3541", "Beam pipe"),
        ]

        for scale, color, label in layers:
            r = base * scale
            canvas.create_oval(
                cx - r,
                cy - r,
                cx + r,
                cy + r,
                fill=color,
                outline="#66748A",
                width=1,
            )

        # Redraw inner layers in sequence to create visible rings.
        for scale, color, _label in reversed(layers[1:]):
            r = base * scale
            canvas.create_oval(
                cx - r,
                cy - r,
                cx + r,
                cy + r,
                fill=color,
                outline="#66748A",
                width=1,
            )

        # Collision point.
        canvas.create_oval(
            cx - 5, cy - 5, cx + 5, cy + 5,
            fill=self.GOLD, outline="#FFF2A8"
        )

        if self.collision_bursts:
            burst = self.collision_bursts[-1]
            phase = min(1.0, burst["age"] / max(1, burst["max_age"]))
            travel = min(1.0, phase * 2.2)
            for track in burst["tracks"]:
                angle = track["angle"]
                length = base * 0.95 * track["length_scale"] * travel
                bend = track["curve"] * travel
                end_angle = angle + bend
                x2 = cx + math.cos(end_angle) * length
                y2 = cy + math.sin(end_angle) * length
                canvas.create_line(
                    cx,
                    cy,
                    x2,
                    y2,
                    fill=track["color"],
                    width=2,
                )
                canvas.create_oval(
                    x2 - 3, y2 - 3, x2 + 3, y2 + 3,
                    fill=track["color"], outline=""
                )

        legend_x = 18
        legend_y = 18
        for scale, color, label in layers:
            canvas.create_rectangle(
                legend_x, legend_y, legend_x + 12, legend_y + 12,
                fill=color, outline="#66748A"
            )
            canvas.create_text(
                legend_x + 20, legend_y + 6,
                text=label, fill=self.TEXT, anchor="w",
                font=("TkDefaultFont", 9)
            )
            legend_y += 20

        if hasattr(self, "detector_status"):
            if self.last_event:
                self.detector_status.configure(
                    text=(
                        f"{self.last_event['detector']} · "
                        f"{len(self.last_event['particle_masses'])} reconstructed masses · "
                        f"{self.last_event['muon_count']} muons"
                    )
                )
            else:
                self.detector_status.configure(text="Waiting for collision data")

    # ------------------------------------------------------------------
    # Pseudo-3D event display
    # ------------------------------------------------------------------

    def open_event3d_view(self):
        if self.event3d_window is not None and self.event3d_window.winfo_exists():
            self.event3d_window.focus()
            return

        self.event3d_window = ctk.CTkToplevel(self)
        self.event3d_window.title("Hadron — Event Display 3D")
        self.event3d_window.geometry("900x720")
        self.event3d_window.minsize(720, 560)
        self.event3d_window.configure(fg_color=self.BG)
        self.event3d_window.grid_columnconfigure(0, weight=1)
        self.event3d_window.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.event3d_window, fg_color="transparent")
        header.grid(row=0, column=0, padx=18, pady=(16, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="PSEUDO-3D EVENT DISPLAY",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        self.event3d_status = ctk.CTkLabel(
            header,
            text="Live display · perspective projection",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=11),
        )
        self.event3d_status.grid(row=1, column=0, sticky="w")

        self.event3d_canvas = tk.Canvas(
            self.event3d_window,
            bg="#070A0F",
            bd=0,
            highlightthickness=1,
            highlightbackground=self.BORDER,
        )
        self.event3d_canvas.grid(row=1, column=0, padx=18, pady=(0, 18), sticky="nsew")
        self.event3d_canvas.bind("<Configure>", lambda _e: self._draw_event3d())
        self.event3d_window.protocol("WM_DELETE_WINDOW", self._close_event3d_view)
        self._draw_event3d()

    def _close_event3d_view(self):
        if self.event3d_window is not None:
            self.event3d_window.destroy()
        self.event3d_window = None
        self.event3d_canvas = None

    def _project3d(self, x, y, z, cx, cy, scale):
        depth = 3.2 + z
        depth = max(0.8, depth)
        factor = scale / depth
        return cx + x * factor, cy - y * factor

    def _draw_event3d(self):
        canvas = self.event3d_canvas
        if canvas is None:
            return
        try:
            if not canvas.winfo_exists():
                return
        except tk.TclError:
            return

        canvas.delete("all")
        w = max(canvas.winfo_width(), 100)
        h = max(canvas.winfo_height(), 100)
        cx, cy = w / 2, h / 2
        scale = min(w, h) * 0.42

        # Detector barrel wireframe.
        barrel_r = 1.35
        z_planes = [-1.2, -0.6, 0.0, 0.6, 1.2]
        for z in z_planes:
            pts = []
            for i in range(41):
                a = math.tau * i / 40
                x = math.cos(a) * barrel_r
                y = math.sin(a) * barrel_r
                pts.extend(self._project3d(x, y, z, cx, cy, scale))
            canvas.create_line(*pts, fill="#243448", width=1)

        for i in range(12):
            a = math.tau * i / 12
            x = math.cos(a) * barrel_r
            y = math.sin(a) * barrel_r
            x1, y1 = self._project3d(x, y, -1.2, cx, cy, scale)
            x2, y2 = self._project3d(x, y, 1.2, cx, cy, scale)
            canvas.create_line(x1, y1, x2, y2, fill="#1C2A3A", width=1)

        # Beam axis.
        x1, y1 = self._project3d(0, 0, -1.8, cx, cy, scale)
        x2, y2 = self._project3d(0, 0, 1.8, cx, cy, scale)
        canvas.create_line(x1, y1, x2, y2, fill="#6C7A91", width=2)

        event = self.replay_event or self.last_event
        if event:
            masses = event.get("particle_masses", [])
            colors = [self.CYAN, self.MAGENTA, self.GREEN, self.GOLD, "#E8EEF6"]

            for i, mass in enumerate(masses):
                seed = (i + 1) * 1.618 + mass * 0.013
                angle = (seed % 1.0) * math.tau
                eta = math.sin(seed * 2.3) * 0.9
                length = 0.9 + min(1.2, mass / 120.0)

                x = math.cos(angle) * length
                y = math.sin(angle) * length
                z = eta

                sx, sy = self._project3d(0, 0, 0, cx, cy, scale)
                ex, ey = self._project3d(x, y, z, cx, cy, scale)
                color = colors[i % len(colors)]

                canvas.create_line(
                    sx, sy, ex, ey,
                    fill=color,
                    width=3 if abs(mass - HIGGS_MASS_GEV) < 3.0 else 2,
                )
                canvas.create_oval(
                    ex - 4, ey - 4, ex + 4, ey + 4,
                    fill=color, outline="#FFFFFF"
                )
                canvas.create_text(
                    ex + 7, ey - 7,
                    text=f"{mass:.1f}",
                    fill=color,
                    anchor="w",
                    font=("Consolas", 8),
                )

            source = "REPLAY" if self.replay_event else "LIVE"
            self.event3d_status.configure(
                text=(
                    f"{source} · {event.get('detector', 'unknown')} · "
                    f"{len(masses)} masses · preset {event.get('preset', self.active_preset)}"
                )
            )
        else:
            canvas.create_text(
                cx, cy,
                text="No event available",
                fill=self.MUTED,
                font=("TkDefaultFont", 12),
            )
            self.event3d_status.configure(text="Waiting for event data")

    # ------------------------------------------------------------------
    # Plot + telemetry
    # ------------------------------------------------------------------

    def _refresh_plot(self):
        if self.plot_mode == "LIVE STATS":
            self.plot_title.configure(text="LIVE RUN STATISTICS")
            self._refresh_statistics()
        else:
            self.plot_title.configure(text="INVARIANT MASS DISTRIBUTION")
            self._refresh_histogram()

    def _refresh_statistics(self):
        ax = self.ax
        ax.clear()
        ax.set_facecolor(self.PANEL)

        for spine in ax.spines.values():
            spine.set_color("#364355")

        ax.tick_params(colors=self.MUTED, labelsize=8)
        ax.xaxis.label.set_color(self.MUTED)
        ax.yaxis.label.set_color(self.MUTED)
        ax.set_xlabel("Collision event", fontsize=9)
        ax.set_ylabel("Cumulative count", fontsize=9)
        ax.grid(alpha=0.13)

        if not self.event_series:
            ax.text(
                0.5,
                0.5,
                "No run statistics yet",
                transform=ax.transAxes,
                ha="center",
                va="center",
                color=self.MUTED,
                fontsize=10,
            )
            self.plot_canvas.draw_idle()
            return

        x = [row["event"] for row in self.event_series]
        saved = [row["saved"] for row in self.event_series]
        discarded = [row["discarded"] for row in self.event_series]
        higgs = [row["higgs"] for row in self.event_series]

        ax.plot(x, saved, color=self.GREEN, linewidth=1.7, label="Saved")
        ax.plot(x, discarded, color=self.RED, linewidth=1.4, label="Discarded")
        if any(higgs):
            ax.plot(x, higgs, color=self.GOLD, linewidth=1.4, label="Higgs candidates")

        total = max(self.collision_count, 1)
        acceptance = (self.saved_count / total) * 100
        ax.text(
            0.02,
            0.96,
            f"Acceptance {acceptance:.1f}%  ·  Higgs {len(self.higgs_candidates)}",
            transform=ax.transAxes,
            ha="left",
            va="top",
            color=self.TEXT,
            fontsize=8,
        )
        legend = ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.88), fontsize=8)
        if legend:
            legend.get_frame().set_facecolor(self.PANEL_2)
            legend.get_frame().set_edgecolor(self.BORDER)
            for text_item in legend.get_texts():
                text_item.set_color(self.TEXT)

        self.plot_canvas.draw_idle()

    def _refresh_histogram(self):
        ax = self.ax
        ax.clear()
        ax.set_facecolor(self.PANEL)

        for spine in ax.spines.values():
            spine.set_color("#364355")

        ax.tick_params(colors=self.MUTED, labelsize=8)
        ax.xaxis.label.set_color(self.MUTED)
        ax.yaxis.label.set_color(self.MUTED)
        ax.set_xlabel("Invariant mass (GeV)", fontsize=9)
        ax.set_ylabel("Accepted candidates", fontsize=9)
        ax.grid(axis="y", alpha=0.13)

        if not self.mass_history:
            ax.text(
                0.5,
                0.5,
                "No accepted collision data yet",
                transform=ax.transAxes,
                ha="center",
                va="center",
                color=self.MUTED,
                fontsize=10,
            )
            ax.set_xlim(0, 160)
            self.plot_canvas.draw_idle()
            return

        max_mass = max(160.0, max(self.mass_history) + 10.0)
        bins = np.linspace(0.0, max_mass, 34)
        counts, edges = np.histogram(self.mass_history, bins=bins)

        widths = np.diff(edges)
        bars = ax.bar(
            edges[:-1],
            counts,
            width=widths * 0.93,
            align="edge",
            color="#3A8FB7",
            edgecolor="#5CCCF5",
            linewidth=0.7,
        )

        # Highlight the histogram bin that contains the Higgs mass
        # once at least one candidate has been accepted.
        if self.higgs_candidates:
            for bar, left, right in zip(bars, edges[:-1], edges[1:]):
                if left <= HIGGS_MASS_GEV < right:
                    bar.set_facecolor(self.GOLD)
                    bar.set_edgecolor("#FFF0A8")
                    break

            ax.axvline(
                HIGGS_MASS_GEV,
                color=self.GOLD,
                linestyle="--",
                linewidth=1.2,
                alpha=0.9,
            )
            ax.text(
                HIGGS_MASS_GEV + 2,
                max(counts.max(), 1) * 0.88,
                "Higgs region",
                color=self.GOLD,
                fontsize=8,
            )

        ax.set_xlim(0, max_mass)
        self.plot_canvas.draw_idle()

    def _update_telemetry(self, speed_pct=None):
        if speed_pct is None:
            gamma = (
                self.collider.beam_energy_gev * 1e9 + PROTON_MASS_EV
            ) / PROTON_MASS_EV
            self.collider.velocity = SPEED_OF_LIGHT * np.sqrt(
                max(0.0, 1 - (1 / (gamma**2)))
            )
            speed_pct = self.collider.velocity / SPEED_OF_LIGHT * 100

        self.speed_value.configure(text=f"{speed_pct:0.9f} % c")
        self.telemetry_energy.configure(
            text=f"{self.collider.beam_energy_gev:,.3f} GeV"
        )
        self.magnet_value.configure(
            text=f"{self.current_magnetic_field:0.3f} T"
        )

    def _update_stats(self):
        self.stats_label.configure(
            text=(
                f"EVENTS {self.collision_count}   "
                f"SAVED {self.saved_count}   "
                f"DISCARDED {self.discarded_count}"
            )
        )

    # ------------------------------------------------------------------
    # Run history
    # ------------------------------------------------------------------

    def open_run_history(self):
        if self.history_window is not None and self.history_window.winfo_exists():
            self.history_window.focus()
            self._load_run_history()
            return

        self.history_window = ctk.CTkToplevel(self)
        self.history_window.title("Hadron — Run History")
        self.history_window.geometry("920x620")
        self.history_window.minsize(760, 500)
        self.history_window.configure(fg_color=self.BG)
        self.history_window.grid_columnconfigure(0, weight=1)
        self.history_window.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.history_window, fg_color="transparent")
        header.grid(row=0, column=0, padx=18, pady=(16, 8), sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header,
            text="RUN MANAGER",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=(0, 10), sticky="w")

        self.history_search = ctk.CTkEntry(
            header,
            placeholder_text="Search ID, date, tags, notes…",
        )
        self.history_search.grid(row=0, column=1, padx=(0, 8), sticky="ew")
        self.history_search.bind("<KeyRelease>", lambda _e: self._load_run_history())

        self.history_archive_filter = ctk.CTkOptionMenu(
            header,
            values=["ACTIVE", "ARCHIVED", "ALL"],
            width=105,
            command=lambda _v: self._load_run_history(),
        )
        self.history_archive_filter.set("ACTIVE")
        self.history_archive_filter.grid(row=0, column=2, padx=(0, 8))

        ctk.CTkButton(
            header,
            text="REFRESH",
            width=90,
            height=30,
            command=self._load_run_history,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=0, column=3, sticky="e")

        body = ctk.CTkFrame(self.history_window, fg_color="transparent")
        body.grid(row=1, column=0, padx=18, pady=(0, 18), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = self._panel(body)
        left.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            left,
            text="RECENT RUNS",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w")

        self.history_list = tk.Listbox(
            left,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            selectforeground=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 10),
        )
        self.history_list.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self.history_list.bind("<<ListboxSelect>>", self._show_run_details)

        right = self._panel(body)
        right.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            right,
            text="RUN DETAILS",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w")

        self.history_detail = tk.Text(
            right,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        self.history_detail.grid(row=1, column=0, padx=10, pady=(0, 6), sticky="nsew")
        self.history_detail.configure(state="disabled")

        self.replay_btn = ctk.CTkButton(
            right,
            text="REPLAY LATEST SAVED EVENT",
            command=self.replay_selected_run_event,
            height=32,
            fg_color="#3A3157",
            hover_color="#514277",
        )
        self.replay_btn.grid(row=2, column=0, padx=10, pady=(0, 6), sticky="ew")

        annotation = ctk.CTkFrame(right, fg_color="transparent")
        annotation.grid(row=3, column=0, padx=10, pady=(0, 10), sticky="ew")
        annotation.grid_columnconfigure(0, weight=1)

        self.history_tags_entry = ctk.CTkEntry(
            annotation,
            placeholder_text="tags: higgs, calibration, test",
        )
        self.history_tags_entry.grid(row=0, column=0, columnspan=3, pady=(0, 5), sticky="ew")

        self.history_note_entry = ctk.CTkEntry(
            annotation,
            placeholder_text="Run note",
        )
        self.history_note_entry.grid(row=1, column=0, columnspan=3, pady=(0, 5), sticky="ew")

        ctk.CTkButton(
            annotation,
            text="SAVE META",
            command=self._save_run_annotation,
            height=30,
        ).grid(row=2, column=0, padx=(0, 3), sticky="ew")

        ctk.CTkButton(
            annotation,
            text="ARCHIVE / RESTORE",
            command=self._toggle_run_archive,
            height=30,
            fg_color="#554B24",
            hover_color="#746632",
        ).grid(row=2, column=1, padx=3, sticky="ew")

        ctk.CTkButton(
            annotation,
            text="EXPORT RUN",
            command=self._export_selected_run_json,
            height=30,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=2, column=2, padx=(3, 0), sticky="ew")

        self.history_window.protocol("WM_DELETE_WINDOW", self._close_history_window)
        self._load_run_history()

    def _close_history_window(self):
        if self.history_window is not None:
            self.history_window.destroy()
        self.history_window = None
        self.history_list = None
        self.history_detail = None
        self.history_rows = []

    def _load_run_history(self):
        if self.history_list is None:
            return

        search = ""
        archive_filter = "ACTIVE"
        try:
            search = self.history_search.get().strip().lower()
            archive_filter = self.history_archive_filter.get()
        except AttributeError:
            pass

        where = []
        params = []
        if archive_filter == "ACTIVE":
            where.append("COALESCE(a.archived, 0) = 0")
        elif archive_filter == "ARCHIVED":
            where.append("COALESCE(a.archived, 0) = 1")

        if search:
            like = f"%{search}%"
            where.append(
                "("
                "LOWER(CAST(r.id AS TEXT)) LIKE ? OR "
                "LOWER(r.started_at) LIKE ? OR "
                "LOWER(COALESCE(a.tags_json, '')) LIKE ? OR "
                "LOWER(COALESCE(a.note, '')) LIKE ?"
                ")"
            )
            params.extend([like, like, like, like])

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        with connect_db(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    r.id,
                    r.started_at,
                    r.ended_at,
                    r.target_energy_gev,
                    r.final_energy_gev,
                    r.collision_count,
                    r.saved_count,
                    r.discarded_count,
                    COALESCE(a.tags_json, '[]'),
                    COALESCE(a.note, ''),
                    COALESCE(a.archived, 0)
                FROM runs r
                LEFT JOIN run_annotations a ON a.run_id = r.id
                {where_sql}
                ORDER BY r.id DESC
                LIMIT 250
                """,
                params,
            ).fetchall()

        self.history_rows = rows
        self.history_list.delete(0, "end")

        if not rows:
            self.history_list.insert("end", "No matching runs.")
            return

        for row in rows:
            (
                run_id, started, ended, target, final_energy,
                collisions, saved, discarded, tags_json, note, archived
            ) = row
            try:
                tags = ", ".join(json.loads(tags_json))
            except (TypeError, json.JSONDecodeError):
                tags = ""
            archive_mark = "[A] " if archived else ""
            tag_text = f"  [{tags}]" if tags else ""
            self.history_list.insert(
                "end",
                (
                    f"{archive_mark}#{run_id:04d}  {started.replace('T', ' ')}  "
                    f"{collisions:5d} ev  {saved:4d} saved  "
                    f"{target:7.1f} GeV{tag_text}"
                ),
            )

    def replay_selected_run_event(self):
        if self.history_list is None or not self.history_rows:
            return
        selected = self.history_list.curselection()
        if not selected:
            return

        run_id = self.history_rows[selected[0]][0]
        with connect_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT detector,
                       transverse_energy,
                       missing_energy,
                       muon_count,
                       particle_masses_json,
                       reason,
                       is_higgs_candidate
                FROM accepted_events
                WHERE run_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()

        if not row:
            self.log("REPLAY", f"Run #{run_id} has no saved events.", "background")
            return

        detector, et, met, mu, masses_json, reason, is_higgs = row
        self.replay_event = {
            "detector": detector,
            "transverse_energy": et,
            "missing_energy": met,
            "muon_count": mu,
            "particle_masses": json.loads(masses_json),
            "reason": reason,
            "is_higgs_candidate": bool(is_higgs),
            "preset": "HISTORICAL",
        }
        self.open_event3d_view()
        self._draw_event3d()
        self.log("REPLAY", f"Loaded latest saved event from run #{run_id}.", "system")

    def _show_run_details(self, _event=None):
        if self.history_list is None or self.history_detail is None:
            return

        selected = self.history_list.curselection()
        if not selected or not self.history_rows:
            return

        row = self.history_rows[selected[0]]
        (
            run_id, started, ended, target, final_energy, collisions,
            saved, discarded, tags_json, note, archived
        ) = row

        with connect_db(self.db_path) as conn:
            higgs_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM accepted_events
                WHERE run_id = ? AND is_higgs_candidate = 1
                """,
                (run_id,),
            ).fetchone()[0]

            detector_rows = conn.execute(
                """
                SELECT detector, COUNT(*)
                FROM accepted_events
                WHERE run_id = ?
                GROUP BY detector
                ORDER BY COUNT(*) DESC
                """,
                (run_id,),
            ).fetchall()

        acceptance = (saved / collisions * 100.0) if collisions else 0.0
        detector_summary = "\n".join(
            f"  {name:<18} {count:5d}" for name, count in detector_rows
        ) or "  No accepted detector events"

        detail = (
            f"Run ID:             {run_id}\n"
            f"Started:            {started}\n"
            f"Ended:              {ended or 'not finalized'}\n\n"
            f"Target energy:      {target:,.3f} GeV\n"
            f"Final beam energy:  {(final_energy or 0):,.3f} GeV\n\n"
            f"Collisions:         {collisions}\n"
            f"Saved:              {saved}\n"
            f"Discarded:          {discarded}\n"
            f"Acceptance:         {acceptance:.2f}%\n"
            f"Higgs candidates:   {higgs_count}\n"
            f"Archived:           {'YES' if archived else 'NO'}\n"
            f"Tags:               {', '.join(json.loads(tags_json)) if tags_json else ''}\n"
            f"Note:               {note}\n\n"
            f"Accepted detectors:\n{detector_summary}\n"
        )

        try:
            self.history_tags_entry.delete(0, "end")
            self.history_tags_entry.insert(
                0,
                ", ".join(json.loads(tags_json)) if tags_json else "",
            )
            self.history_note_entry.delete(0, "end")
            self.history_note_entry.insert(0, note)
        except (AttributeError, json.JSONDecodeError):
            pass

        self.history_detail.configure(state="normal")
        self.history_detail.delete("1.0", "end")
        self.history_detail.insert("1.0", detail)
        self.history_detail.configure(state="disabled")

    # ------------------------------------------------------------------
    # Trigger lab + event analysis
    # ------------------------------------------------------------------

    def open_trigger_lab(self):
        window = ctk.CTkToplevel(self)
        window.title("Hadron — Trigger Lab")
        window.geometry("560x520")
        window.resizable(False, False)
        window.configure(fg_color=self.BG)
        window.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            window,
            text="TRIGGER LAB",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(18, 6), sticky="w")

        ctk.CTkLabel(
            window,
            text="Adjust toy trigger thresholds for future simulated events.",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=1, column=0, padx=20, pady=(0, 14), sticky="w")

        l1_value = ctk.CTkLabel(
            window, text=f"{self.l1_energy_threshold:.0f} GeV", text_color=self.CYAN
        )
        l1_value.grid(row=2, column=0, padx=20, sticky="e")
        ctk.CTkLabel(
            window, text="L1 transverse-energy threshold", text_color=self.TEXT
        ).grid(row=2, column=0, padx=20, sticky="w")
        l1_slider = ctk.CTkSlider(window, from_=2000, to=9000, number_of_steps=70)
        l1_slider.set(self.l1_energy_threshold)
        l1_slider.grid(row=3, column=0, padx=20, pady=(4, 12), sticky="ew")
        l1_slider.configure(
            command=lambda v: l1_value.configure(text=f"{float(v):.0f} GeV")
        )

        met_value = ctk.CTkLabel(
            window, text=f"{self.met_trigger_threshold:.0f} GeV", text_color=self.MAGENTA
        )
        met_value.grid(row=4, column=0, padx=20, sticky="e")
        ctk.CTkLabel(
            window, text="Missing-energy trigger threshold", text_color=self.TEXT
        ).grid(row=4, column=0, padx=20, sticky="w")
        met_slider = ctk.CTkSlider(window, from_=100, to=800, number_of_steps=70)
        met_slider.set(self.met_trigger_threshold)
        met_slider.grid(row=5, column=0, padx=20, pady=(4, 12), sticky="ew")
        met_slider.configure(
            command=lambda v: met_value.configure(text=f"{float(v):.0f} GeV")
        )

        higgs_value = ctk.CTkLabel(
            window, text=f"±{self.higgs_window_gev:.1f} GeV", text_color=self.GOLD
        )
        higgs_value.grid(row=6, column=0, padx=20, sticky="e")
        ctk.CTkLabel(
            window, text="Higgs mass-window half-width", text_color=self.TEXT
        ).grid(row=6, column=0, padx=20, sticky="w")
        higgs_slider = ctk.CTkSlider(window, from_=0.5, to=10.0, number_of_steps=38)
        higgs_slider.set(self.higgs_window_gev)
        higgs_slider.grid(row=7, column=0, padx=20, pady=(4, 12), sticky="ew")
        higgs_slider.configure(
            command=lambda v: higgs_value.configure(text=f"±{float(v):.1f} GeV")
        )

        ctk.CTkLabel(
            window,
            text=(
                "Existing run counts are not recomputed after a threshold change.\n"
                "Only subsequently generated events use the new thresholds."
            ),
            text_color=self.MUTED,
            justify="left",
            font=ctk.CTkFont(size=10),
        ).grid(row=8, column=0, padx=20, pady=(8, 12), sticky="w")

        def apply():
            self.l1_energy_threshold = float(l1_slider.get())
            self.met_trigger_threshold = float(met_slider.get())
            self.higgs_window_gev = float(higgs_slider.get())
            self._save_settings()
            self.log(
                "TRIGGER",
                f"L1>{self.l1_energy_threshold:.0f} GeV · "
                f"MET>{self.met_trigger_threshold:.0f} GeV · "
                f"Higgs ±{self.higgs_window_gev:.1f} GeV",
                "system",
            )
            window.destroy()

        ctk.CTkButton(
            window,
            text="APPLY TRIGGER SETTINGS",
            command=apply,
            height=38,
            fg_color="#18566C",
            hover_color="#216F89",
        ).grid(row=9, column=0, padx=20, pady=(10, 18), sticky="ew")

    def open_event_inspector(self):
        if (
            self.event_inspector_window is not None
            and self.event_inspector_window.winfo_exists()
        ):
            self.event_inspector_window.focus()
            self._load_event_inspector()
            return

        self.event_inspector_window = ctk.CTkToplevel(self)
        self.event_inspector_window.title("Hadron — Event Inspector")
        self.event_inspector_window.geometry("980x640")
        self.event_inspector_window.minsize(820, 520)
        self.event_inspector_window.configure(fg_color=self.BG)
        self.event_inspector_window.grid_columnconfigure(0, weight=1)
        self.event_inspector_window.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.event_inspector_window, fg_color="transparent")
        header.grid(row=0, column=0, padx=18, pady=(16, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="ACCEPTED EVENT INSPECTOR",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        self.event_filter_menu = ctk.CTkOptionMenu(
            header,
            values=["ALL", "HIGGS", "HIGH MET", "ATLAS-SIM", "CMS-SIM"],
            command=self._set_event_filter,
            width=118,
            fg_color=self.PANEL_2,
            button_color="#253044",
            button_hover_color="#34425A",
        )
        self.event_filter_menu.set(self.event_filter)
        self.event_filter_menu.grid(row=0, column=1, padx=(8, 0), sticky="e")

        ctk.CTkButton(
            header,
            text="BOOKMARKS",
            command=self.open_bookmarks,
            width=100,
            height=30,
            fg_color="#3A3157",
            hover_color="#514277",
        ).grid(row=0, column=2, padx=(8, 0), sticky="e")

        ctk.CTkButton(
            header,
            text="REFRESH",
            command=self._load_event_inspector,
            width=82,
            height=30,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=0, column=3, padx=(8, 0), sticky="e")

        body = ctk.CTkFrame(self.event_inspector_window, fg_color="transparent")
        body.grid(row=1, column=0, padx=18, pady=(0, 18), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = self._panel(body)
        left.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)

        self.event_inspector_list = tk.Listbox(
            left,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            selectforeground=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 10),
        )
        self.event_inspector_list.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.event_inspector_list.bind(
            "<<ListboxSelect>>", self._show_event_inspector_detail
        )

        right = self._panel(body)
        right.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        self.event_inspector_detail = tk.Text(
            right,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        self.event_inspector_detail.grid(
            row=0, column=0, padx=10, pady=(10, 6), sticky="nsew"
        )
        self.event_inspector_detail.configure(state="disabled")

        action_row = ctk.CTkFrame(right, fg_color="transparent")
        action_row.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")
        action_row.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            action_row,
            text="REPLAY IN 3D",
            command=self.replay_inspector_event,
            height=32,
            fg_color="#3A3157",
            hover_color="#514277",
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            action_row,
            text="BOOKMARK",
            command=self.bookmark_inspector_event,
            height=32,
            fg_color="#5B4720",
            hover_color="#775D2A",
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.event_inspector_window.protocol(
            "WM_DELETE_WINDOW", self._close_event_inspector
        )
        self._load_event_inspector()

    def _set_event_filter(self, value):
        self.event_filter = value
        self._load_event_inspector()

    def _close_event_inspector(self):
        if self.event_inspector_window is not None:
            try:
                self.event_inspector_window.destroy()
            except tk.TclError:
                pass
        self.event_inspector_window = None
        self.event_inspector_list = None
        self.event_inspector_detail = None
        self.event_inspector_rows = []

    def _load_event_inspector(self):
        if self.event_inspector_list is None:
            return

        where_sql = ""
        params = ()
        if self.event_filter == "HIGGS":
            where_sql = "WHERE is_higgs_candidate = 1"
        elif self.event_filter == "HIGH MET":
            where_sql = "WHERE missing_energy >= 600"
        elif self.event_filter in ("ATLAS-SIM", "CMS-SIM"):
            where_sql = "WHERE detector = ?"
            params = (self.event_filter,)

        with connect_db(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT id, run_id, event_number, timestamp, detector,
                       transverse_energy, missing_energy, muon_count,
                       particle_masses_json, reason, is_higgs_candidate
                FROM accepted_events
                {where_sql}
                ORDER BY id DESC
                LIMIT 250
                """,
                params,
            ).fetchall()

        self.event_inspector_rows = rows
        self.event_inspector_list.delete(0, "end")

        for row in rows:
            (
                event_id,
                run_id,
                event_no,
                _ts,
                detector,
                _et,
                met,
                _mu,
                _masses_json,
                _reason,
                is_higgs,
            ) = row
            marker = "★" if is_higgs else " "
            self.event_inspector_list.insert(
                "end",
                f"{marker} #{event_id:05d}  run {run_id or 0:04d}  "
                f"ev {event_no:05d}  {detector:<14}  MET {met:6.1f}",
            )

        if not rows:
            self.event_inspector_list.insert("end", "No accepted events saved yet.")

    def _selected_inspector_row(self):
        if self.event_inspector_list is None or not self.event_inspector_rows:
            return None
        selected = self.event_inspector_list.curselection()
        if not selected:
            return None
        return self.event_inspector_rows[selected[0]]

    def _show_event_inspector_detail(self, _event=None):
        row = self._selected_inspector_row()
        if row is None or self.event_inspector_detail is None:
            return

        (
            event_id,
            run_id,
            event_no,
            ts,
            detector,
            et,
            met,
            mu,
            masses_json,
            reason,
            is_higgs,
        ) = row
        masses = json.loads(masses_json)

        detail = (
            f"Database ID:      {event_id}\n"
            f"Run ID:           {run_id}\n"
            f"Event number:     {event_no}\n"
            f"Timestamp:        {ts}\n"
            f"Detector:         {detector}\n\n"
            f"Transverse E:     {et:.3f} GeV\n"
            f"Missing E:        {met:.3f} GeV\n"
            f"Muon count:       {mu}\n"
            f"Higgs candidate:  {'YES' if is_higgs else 'NO'}\n\n"
            f"Masses (GeV):\n"
            + "\n".join(f"  {m:.6f}" for m in masses)
            + f"\n\nHLT reason:\n{reason}\n"
        )

        self.event_inspector_detail.configure(state="normal")
        self.event_inspector_detail.delete("1.0", "end")
        self.event_inspector_detail.insert("1.0", detail)
        self.event_inspector_detail.configure(state="disabled")

    def replay_inspector_event(self):
        row = self._selected_inspector_row()
        if row is None:
            return

        (
            event_id,
            _run_id,
            _event_no,
            _ts,
            detector,
            et,
            met,
            mu,
            masses_json,
            reason,
            is_higgs,
        ) = row

        self.replay_event = {
            "detector": detector,
            "transverse_energy": et,
            "missing_energy": met,
            "muon_count": mu,
            "particle_masses": json.loads(masses_json),
            "reason": reason,
            "is_higgs_candidate": bool(is_higgs),
            "preset": "HISTORICAL",
        }
        self.open_event3d_view()
        self._draw_event3d()
        self.log("REPLAY", f"Loaded accepted event #{event_id}.", "system")

    def bookmark_inspector_event(self):
        row = self._selected_inspector_row()
        if row is None:
            return

        event_id = row[0]
        with connect_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO bookmarks (event_id, created_at, note)
                VALUES (?, ?, '')
                """,
                (event_id, datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()

        self.log("BOOKMARK", f"Event #{event_id} bookmarked.", "saved")

    def open_bookmarks(self):
        if self.bookmarks_window is not None and self.bookmarks_window.winfo_exists():
            self.bookmarks_window.focus()
            self._load_bookmarks()
            return

        self.bookmarks_window = ctk.CTkToplevel(self)
        self.bookmarks_window.title("Hadron — Bookmarked Events")
        self.bookmarks_window.geometry("760x500")
        self.bookmarks_window.configure(fg_color=self.BG)
        self.bookmarks_window.grid_columnconfigure(0, weight=1)
        self.bookmarks_window.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self.bookmarks_window,
            text="BOOKMARKED EVENTS",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=18, pady=(16, 8), sticky="w")

        self.bookmark_list = tk.Listbox(
            self.bookmarks_window,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 10),
        )
        self.bookmark_list.grid(
            row=1, column=0, padx=18, pady=(0, 10), sticky="nsew"
        )

        buttons = ctk.CTkFrame(self.bookmarks_window, fg_color="transparent")
        buttons.grid(row=2, column=0, padx=18, pady=(0, 16), sticky="ew")
        buttons.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            buttons,
            text="REPLAY",
            command=self.replay_bookmark,
            fg_color="#3A3157",
            hover_color="#514277",
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            buttons,
            text="REMOVE",
            command=self.remove_bookmark,
            fg_color="#71303A",
            hover_color="#923E4B",
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.bookmarks_window.protocol("WM_DELETE_WINDOW", self._close_bookmarks)
        self._load_bookmarks()

    def _close_bookmarks(self):
        if self.bookmarks_window is not None:
            try:
                self.bookmarks_window.destroy()
            except tk.TclError:
                pass
        self.bookmarks_window = None
        self.bookmark_list = None
        self.bookmark_rows = []

    def _load_bookmarks(self):
        if self.bookmark_list is None:
            return

        with connect_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT b.id, e.id, e.run_id, e.event_number, e.detector,
                       e.transverse_energy, e.missing_energy, e.muon_count,
                       e.particle_masses_json, e.reason, e.is_higgs_candidate
                FROM bookmarks b
                JOIN accepted_events e ON e.id = b.event_id
                ORDER BY b.id DESC
                """
            ).fetchall()

        self.bookmark_rows = rows
        self.bookmark_list.delete(0, "end")

        for row in rows:
            bookmark_id, event_id, run_id, _event_no, detector, *_rest = row
            is_higgs = row[-1]
            marker = "★" if is_higgs else "•"
            self.bookmark_list.insert(
                "end",
                f"{marker} bookmark {bookmark_id:04d}  event {event_id:05d}  "
                f"run {run_id or 0:04d}  {detector}",
            )

        if not rows:
            self.bookmark_list.insert("end", "No bookmarks.")

    def _selected_bookmark_row(self):
        if self.bookmark_list is None or not self.bookmark_rows:
            return None
        selected = self.bookmark_list.curselection()
        if not selected:
            return None
        return self.bookmark_rows[selected[0]]

    def replay_bookmark(self):
        row = self._selected_bookmark_row()
        if row is None:
            return

        (
            _bookmark_id,
            _event_id,
            _run_id,
            _event_no,
            detector,
            et,
            met,
            mu,
            masses_json,
            reason,
            is_higgs,
        ) = row

        self.replay_event = {
            "detector": detector,
            "transverse_energy": et,
            "missing_energy": met,
            "muon_count": mu,
            "particle_masses": json.loads(masses_json),
            "reason": reason,
            "is_higgs_candidate": bool(is_higgs),
            "preset": "BOOKMARK",
        }
        self.open_event3d_view()
        self._draw_event3d()

    def remove_bookmark(self):
        row = self._selected_bookmark_row()
        if row is None:
            return

        bookmark_id = row[0]
        with connect_db(self.db_path) as conn:
            conn.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))
            conn.commit()
        self._load_bookmarks()

    def open_run_compare(self):
        with connect_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, started_at, target_energy_gev, final_energy_gev,
                       collision_count, saved_count, discarded_count
                FROM runs
                WHERE collision_count > 0
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()

        if len(rows) < 2:
            self.log("COMPARE", "At least two completed runs are required.", "background")
            return

        if self.compare_window is not None and self.compare_window.winfo_exists():
            self.compare_window.destroy()

        self.compare_window = ctk.CTkToplevel(self)
        self.compare_window.title("Hadron — Run Comparison")
        self.compare_window.geometry("780x560")
        self.compare_window.configure(fg_color=self.BG)
        self.compare_window.grid_columnconfigure(0, weight=1)
        self.compare_window.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self.compare_window,
            text="RUN COMPARISON",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=18, pady=(16, 8), sticky="w")

        fig = Figure(figsize=(7.2, 4.6), dpi=100, facecolor=self.PANEL)
        ax = fig.add_subplot(111)
        ax.set_facecolor(self.PANEL)

        recent = list(reversed(rows[:10]))
        run_ids = [str(row[0]) for row in recent]
        acceptance = [
            (row[5] / row[4] * 100.0) if row[4] else 0.0
            for row in recent
        ]

        x = list(range(len(run_ids)))
        ax.bar(x, acceptance)
        ax.set_xticks(x)
        ax.set_xticklabels(run_ids)
        ax.set_xlabel("Run ID")
        ax.set_ylabel("Acceptance (%)")
        ax.set_title("Recent run acceptance", color=self.TEXT)
        ax.tick_params(colors=self.MUTED)
        ax.xaxis.label.set_color(self.MUTED)
        ax.yaxis.label.set_color(self.MUTED)
        for spine in ax.spines.values():
            spine.set_color("#364355")
        ax.grid(axis="y", alpha=0.13)

        canvas = FigureCanvasTkAgg(fig, master=self.compare_window)
        canvas.get_tk_widget().configure(bg=self.PANEL, highlightthickness=0)
        canvas.get_tk_widget().grid(
            row=1, column=0, padx=18, pady=(0, 18), sticky="nsew"
        )
        canvas.draw_idle()

    def export_plot_snapshot(self):
        path = filedialog.asksaveasfilename(
            title="Export Hadron plot snapshot",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png")],
            initialfile=f"hadron_{self.plot_mode.lower().replace(' ', '_')}.png",
        )
        if not path:
            return

        self.figure.savefig(
            path,
            dpi=180,
            facecolor=self.figure.get_facecolor(),
        )
        self.log("EXPORT", f"Plot snapshot saved: {path}", "saved")

    # ------------------------------------------------------------------
    # Experiment Lab
    # ------------------------------------------------------------------

    def open_experiment_lab(self):
        if self.experiment_window is not None and self.experiment_window.winfo_exists():
            self.experiment_window.focus()
            return

        self.experiment_window = ctk.CTkToplevel(self)
        self.experiment_window.title("Hadron — Experiment Lab")
        self.experiment_window.geometry("760x650")
        self.experiment_window.minsize(680, 590)
        self.experiment_window.configure(fg_color=self.BG)
        self.experiment_window.grid_columnconfigure(0, weight=1)
        self.experiment_window.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            self.experiment_window,
            text="REPRODUCIBLE EXPERIMENT LAB",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(18, 4), sticky="w")

        ctk.CTkLabel(
            self.experiment_window,
            text=(
                "Batch runs use a fixed seed and do not alter the live dashboard run. "
                "Results are toy-simulation summaries."
            ),
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10),
            wraplength=700,
            justify="left",
        ).grid(row=1, column=0, padx=20, pady=(0, 12), sticky="w")

        controls = self._panel(self.experiment_window)
        controls.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="ew")
        for col in range(4):
            controls.grid_columnconfigure(col, weight=1)

        ctk.CTkLabel(controls, text="Preset", text_color=self.MUTED).grid(
            row=0, column=0, padx=8, pady=(10, 2), sticky="w"
        )
        self.exp_preset = ctk.CTkOptionMenu(
            controls,
            values=list(self.physics_presets.keys()),
            fg_color=self.PANEL_2,
            button_color="#253044",
        )
        self.exp_preset.set(self.active_preset)
        self.exp_preset.grid(row=1, column=0, padx=8, pady=(0, 10), sticky="ew")

        ctk.CTkLabel(controls, text="Beam energy (GeV)", text_color=self.MUTED).grid(
            row=0, column=1, padx=8, pady=(10, 2), sticky="w"
        )
        self.exp_energy = ctk.CTkEntry(controls)
        self.exp_energy.insert(0, f"{self.target_energy:.0f}")
        self.exp_energy.grid(row=1, column=1, padx=8, pady=(0, 10), sticky="ew")

        ctk.CTkLabel(controls, text="Events", text_color=self.MUTED).grid(
            row=0, column=2, padx=8, pady=(10, 2), sticky="w"
        )
        self.exp_events = ctk.CTkEntry(controls)
        self.exp_events.insert(0, "5000")
        self.exp_events.grid(row=1, column=2, padx=8, pady=(0, 10), sticky="ew")

        ctk.CTkLabel(controls, text="Seed", text_color=self.MUTED).grid(
            row=0, column=3, padx=8, pady=(10, 2), sticky="w"
        )
        self.exp_seed = ctk.CTkEntry(controls)
        self.exp_seed.insert(0, "42")
        self.exp_seed.grid(row=1, column=3, padx=8, pady=(0, 10), sticky="ew")

        buttons = ctk.CTkFrame(controls, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=4, padx=8, pady=(0, 10), sticky="ew")
        buttons.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkButton(
            buttons,
            text="RUN BATCH",
            command=self.start_batch_experiment,
            fg_color="#18566C",
            hover_color="#216F89",
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            buttons,
            text="ENERGY SWEEP",
            command=self.start_energy_sweep,
            fg_color="#3A3157",
            hover_color="#514277",
        ).grid(row=0, column=1, padx=4, sticky="ew")

        ctk.CTkButton(
            buttons,
            text="CANCEL",
            command=self.cancel_experiment,
            fg_color="#71303A",
            hover_color="#923E4B",
        ).grid(row=0, column=2, padx=(4, 0), sticky="ew")

        results_panel = self._panel(self.experiment_window)
        results_panel.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="nsew")
        results_panel.grid_columnconfigure(0, weight=1)
        results_panel.grid_rowconfigure(1, weight=1)

        self.experiment_status = ctk.CTkLabel(
            results_panel,
            text="Ready",
            text_color=self.MUTED,
            anchor="w",
        )
        self.experiment_status.grid(row=0, column=0, padx=12, pady=(10, 4), sticky="ew")

        self.experiment_results_box = tk.Text(
            results_panel,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        self.experiment_results_box.grid(
            row=1, column=0, padx=10, pady=(0, 8), sticky="nsew"
        )
        self.experiment_results_box.configure(state="disabled")

        self.experiment_progress = ctk.CTkProgressBar(results_panel, height=8)
        self.experiment_progress.set(0)
        self.experiment_progress.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")

        report_row = ctk.CTkFrame(self.experiment_window, fg_color="transparent")
        report_row.grid(row=4, column=0, padx=20, pady=(0, 8), sticky="ew")
        report_row.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            report_row,
            text="COMPARE LATEST 2",
            command=self.compare_latest_experiments,
            height=32,
            fg_color="#3A3157",
            hover_color="#514277",
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            report_row,
            text="EXPORT HTML REPORT",
            command=self.export_experiment_report,
            height=32,
            fg_color="#25495B",
            hover_color="#32647A",
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        ctk.CTkButton(
            self.experiment_window,
            text="EXPORT EXPERIMENT HISTORY CSV",
            command=self.export_experiment_history,
            height=32,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=5, column=0, padx=20, pady=(0, 16), sticky="ew")

        self.experiment_window.protocol("WM_DELETE_WINDOW", self._close_experiment_lab)

    def _close_experiment_lab(self):
        self.experiment_cancelled = True
        if self.experiment_window is not None:
            try:
                self.experiment_window.destroy()
            except tk.TclError:
                pass
        self.experiment_window = None
        self.experiment_progress = None
        self.experiment_status = None
        self.experiment_results_box = None
        self.active_experiment = None

    def cancel_experiment(self):
        self.experiment_cancelled = True
        if self.experiment_status is not None:
            self.experiment_status.configure(text="Cancellation requested…")

    def _experiment_inputs(self):
        try:
            preset = self.exp_preset.get()
            energy = float(self.exp_energy.get())
            events = int(self.exp_events.get())
            seed = int(self.exp_seed.get())
        except (ValueError, AttributeError):
            raise ValueError("Energy, events, and seed must be numeric.")

        if preset not in self.physics_presets:
            raise ValueError("Unknown physics preset.")
        if not 1000.0 <= energy <= 7000.0:
            raise ValueError("Beam energy must be between 1000 and 7000 GeV.")
        if not 1 <= events <= 1_000_000:
            raise ValueError("Events must be between 1 and 1,000,000.")
        return preset, energy, events, seed

    def start_batch_experiment(self):
        if self.active_experiment is not None:
            return
        try:
            preset, energy, events, seed = self._experiment_inputs()
        except ValueError as exc:
            self._write_experiment_text(f"Input error: {exc}\n")
            return

        self.experiment_cancelled = False
        self.active_experiment = {
            "mode": "batch",
            "preset": preset,
            "energy": energy,
            "events": events,
            "seed": seed,
            "rng": random.Random(seed),
            "processed": 0,
            "saved": 0,
            "discarded": 0,
            "higgs": 0,
        }
        self.experiment_progress.set(0)
        self.experiment_status.configure(
            text=f"Running {events:,} events · {preset} · seed {seed}"
        )
        self._write_experiment_text("")
        self.after(1, self._experiment_chunk)

    def start_energy_sweep(self):
        if self.active_experiment is not None:
            return
        try:
            preset, _energy, events, seed = self._experiment_inputs()
        except ValueError as exc:
            self._write_experiment_text(f"Input error: {exc}\n")
            return

        points = [1000.0, 2500.0, 4000.0, 5500.0, 6500.0, 7000.0]
        self.experiment_cancelled = False
        self.active_experiment = {
            "mode": "sweep",
            "preset": preset,
            "events": events,
            "seed": seed,
            "points": points,
            "point_index": 0,
            "summaries": [],
        }
        self.experiment_progress.set(0)
        self._write_experiment_text("")
        self._start_next_sweep_point()

    def _start_next_sweep_point(self):
        exp = self.active_experiment
        if exp is None or exp.get("mode") != "sweep":
            return
        if self.experiment_cancelled:
            self._finish_experiment_cancelled()
            return

        idx = exp["point_index"]
        if idx >= len(exp["points"]):
            summaries = exp["summaries"]
            lines = ["ENERGY SWEEP COMPLETE", ""]
            for item in summaries:
                lines.append(
                    f"{item['beam_energy_gev']:7.0f} GeV  "
                    f"accept {item['acceptance_rate']:6.2f}%  "
                    f"Higgs {item['higgs_count']:5d}"
                )
            self._write_experiment_text("\n".join(lines) + "\n")
            self.experiment_status.configure(text="Energy sweep complete")
            self.experiment_progress.set(1)
            self.active_experiment = None
            return

        energy = exp["points"][idx]
        point_seed = exp["seed"] + idx
        exp["point"] = {
            "preset": exp["preset"],
            "energy": energy,
            "events": exp["events"],
            "seed": point_seed,
            "rng": random.Random(point_seed),
            "processed": 0,
            "saved": 0,
            "discarded": 0,
            "higgs": 0,
        }
        self.experiment_status.configure(
            text=f"Sweep {idx + 1}/{len(exp['points'])} · {energy:.0f} GeV · seed {point_seed}"
        )
        self.after(1, self._experiment_chunk)

    def _experiment_chunk(self):
        exp = self.active_experiment
        if exp is None:
            return
        if self.experiment_cancelled:
            self._finish_experiment_cancelled()
            return

        point = exp if exp["mode"] == "batch" else exp["point"]
        remaining = point["events"] - point["processed"]
        chunk = min(500, remaining)

        detectors = ["ATLAS-SIM", "CMS-SIM", "INNER-TRACKER", "CALORIMETER"]
        for _ in range(chunk):
            detector = point["rng"].choice(detectors)
            event = self._generate_event_with_rng(
                detector=detector,
                rng=point["rng"],
                beam_energy_gev=point["energy"],
                preset_name=point["preset"],
                noise_enabled=self.detector_noise_enabled,
                noise_sigma=self.detector_noise_sigma,
                resolution_sigma=self.detector_resolution_sigma,
            )
            point["processed"] += 1

            if not self._hardware_l1_trigger(event):
                point["discarded"] += 1
                continue

            keep, _reason = self._software_hlt_trigger(event)
            if keep:
                point["saved"] += 1
                if any(
                    abs(m - HIGGS_MASS_GEV) < self.higgs_window_gev
                    for m in event["particle_masses"]
                ):
                    point["higgs"] += 1
            else:
                point["discarded"] += 1

        fraction = point["processed"] / point["events"]
        if exp["mode"] == "sweep":
            total_fraction = (
                exp["point_index"] + fraction
            ) / len(exp["points"])
        else:
            total_fraction = fraction
        self.experiment_progress.set(total_fraction)

        if point["processed"] < point["events"]:
            self.after(1, self._experiment_chunk)
            return

        summary = self._finalize_experiment_point(point)

        if exp["mode"] == "batch":
            self._write_experiment_summary(summary)
            self.experiment_status.configure(text="Batch experiment complete")
            self.experiment_progress.set(1)
            self.active_experiment = None
        else:
            exp["summaries"].append(summary)
            exp["point_index"] += 1
            self._start_next_sweep_point()

    def _finalize_experiment_point(self, point):
        acceptance = (
            point["saved"] / point["events"] * 100.0
            if point["events"]
            else 0.0
        )
        summary = enrich_summary({
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "preset": point["preset"],
            "beam_energy_gev": point["energy"],
            "events_requested": point["events"],
            "seed": point["seed"],
            "saved_count": point["saved"],
            "discarded_count": point["discarded"],
            "higgs_count": point["higgs"],
            "acceptance_rate": acceptance,
            "l1_energy_threshold": self.l1_energy_threshold,
            "met_trigger_threshold": self.met_trigger_threshold,
            "higgs_window_gev": self.higgs_window_gev,
            "noise_enabled": self.detector_noise_enabled,
            "noise_sigma": self.detector_noise_sigma,
            "resolution_sigma": self.detector_resolution_sigma,
        })

        with connect_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO experiments (
                    created_at, preset, beam_energy_gev, events_requested, seed,
                    saved_count, discarded_count, higgs_count, acceptance_rate,
                    summary_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary["created_at"],
                    summary["preset"],
                    summary["beam_energy_gev"],
                    summary["events_requested"],
                    summary["seed"],
                    summary["saved_count"],
                    summary["discarded_count"],
                    summary["higgs_count"],
                    summary["acceptance_rate"],
                    json.dumps(summary),
                ),
            )
            conn.commit()

        return summary

    def _write_experiment_summary(self, summary):
        text = (
            "EXPERIMENT COMPLETE\n\n"
            f"Preset:          {summary['preset']}\n"
            f"Beam energy:     {summary['beam_energy_gev']:.0f} GeV\n"
            f"Events:          {summary['events_requested']:,}\n"
            f"Seed:            {summary['seed']}\n"
            f"Saved:           {summary['saved_count']:,}\n"
            f"Discarded:       {summary['discarded_count']:,}\n"
            f"Acceptance:      {summary['acceptance_rate']:.3f}%\n"
            f"95% interval:    {summary['acceptance_ci95_low']:.3f}% – "
            f"{summary['acceptance_ci95_high']:.3f}%\n"
            f"Higgs candidates:{summary['higgs_count']:>8,}\n"
            f"Higgs / 1000:    {summary['higgs_rate_per_1000']:.3f}\n"
            f"Config hash:     {summary['reproducibility_hash'][:20]}…\n\n"
            f"L1 threshold:    {summary['l1_energy_threshold']:.0f} GeV\n"
            f"MET threshold:   {summary['met_trigger_threshold']:.0f} GeV\n"
            f"Higgs window:    ±{summary['higgs_window_gev']:.1f} GeV\n"
        )
        self._write_experiment_text(text)

    def _write_experiment_text(self, text):
        if self.experiment_results_box is None:
            return
        self.experiment_results_box.configure(state="normal")
        self.experiment_results_box.delete("1.0", "end")
        self.experiment_results_box.insert("1.0", text)
        self.experiment_results_box.configure(state="disabled")

    def _finish_experiment_cancelled(self):
        if self.experiment_status is not None:
            self.experiment_status.configure(text="Experiment cancelled")
        self.active_experiment = None
        self.experiment_cancelled = False

    def _latest_experiment_summaries(self, limit=2):
        with connect_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT summary_json
                FROM experiments
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        summaries = []
        for (payload,) in rows:
            try:
                summaries.append(enrich_summary(json.loads(payload)))
            except (TypeError, json.JSONDecodeError):
                continue
        return summaries

    def compare_latest_experiments(self):
        summaries = self._latest_experiment_summaries(2)
        if len(summaries) < 2:
            self._write_experiment_text(
                "At least two saved experiments are required for comparison.\n"
            )
            return

        a, b = summaries[0], summaries[1]
        comp = compare_summaries(a, b)
        text = (
            "LATEST TWO EXPERIMENTS\n\n"
            f"A: {a['preset']} · {a['beam_energy_gev']:.0f} GeV · "
            f"seed {a['seed']} · {a['acceptance_rate']:.3f}%\n"
            f"B: {b['preset']} · {b['beam_energy_gev']:.0f} GeV · "
            f"seed {b['seed']} · {b['acceptance_rate']:.3f}%\n\n"
            f"Δ acceptance:    {comp['delta_percentage_points']:+.3f} percentage points\n"
            f"Diagnostic z:    {comp['z_score']:+.3f}\n"
            f"Two-sided p:     {comp['two_sided_p_value']:.6g}\n\n"
            "These statistics compare toy Monte Carlo proportions only; "
            "they are not experimental discovery significance.\n"
        )
        self._write_experiment_text(text)

    def export_experiment_report(self):
        summaries = self._latest_experiment_summaries(25)
        if not summaries:
            self._write_experiment_text("No experiment summaries are available.\n")
            return

        path = filedialog.asksaveasfilename(
            title="Export Hadron experiment HTML report",
            defaultextension=".html",
            filetypes=[("HTML files", "*.html")],
            initialfile="hadron_experiment_report.html",
        )
        if not path:
            return

        report = render_html_report(
            summaries,
            title="Hadron v0.7 Experiment Report",
        )
        Path(path).write_text(report, encoding="utf-8")
        self.log("EXPORT", f"Experiment HTML report saved: {path}", "saved")

    def export_experiment_history(self):
        path = filedialog.asksaveasfilename(
            title="Export Hadron experiment history",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile="hadron_experiments.csv",
        )
        if not path:
            return

        with connect_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, preset, beam_energy_gev, events_requested,
                       seed, saved_count, discarded_count, higgs_count,
                       acceptance_rate
                FROM experiments
                ORDER BY id DESC
                """
            ).fetchall()

        fields = [
            "id", "created_at", "preset", "beam_energy_gev", "events_requested",
            "seed", "saved_count", "discarded_count", "higgs_count",
            "acceptance_rate",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(fields)
            writer.writerows(rows)

        self.log("EXPORT", f"Experiment history saved: {path}", "saved")

    # ------------------------------------------------------------------
    # Study Queue
    # ------------------------------------------------------------------

    def open_study_queue(self):
        if self.study_window is not None and self.study_window.winfo_exists():
            self.study_window.focus()
            self._load_study_queue()
            return

        # Any job left RUNNING after a previous process exit becomes interrupted.
        mark_running_studies_interrupted(
            self.db_path,
            exclude_id=self.study_active_id,
        )

        self.study_window = ctk.CTkToplevel(self)
        self.study_window.title("Hadron — Study Queue")
        self.study_window.geometry("1040x700")
        self.study_window.minsize(860, 600)
        self.study_window.configure(fg_color=self.BG)
        self.study_window.grid_columnconfigure(0, weight=1)
        self.study_window.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self.study_window,
            text="STUDY QUEUE",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=18, pady=(16, 4), sticky="w")

        ctk.CTkLabel(
            self.study_window,
            text=(
                "Run energy/preset grids in the background. "
                "The live dashboard remains responsive."
            ),
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=1, column=0, padx=18, pady=(0, 10), sticky="w")

        controls = self._panel(self.study_window)
        controls.grid(row=2, column=0, padx=18, pady=(0, 10), sticky="new")
        for col in range(6):
            controls.grid_columnconfigure(col, weight=1)

        labels = [
            "Study name",
            "Energies (GeV)",
            "Preset",
            "Events/job",
            "Repeats",
            "Workers",
        ]
        for col, label in enumerate(labels):
            ctk.CTkLabel(
                controls,
                text=label,
                text_color=self.MUTED,
                font=ctk.CTkFont(size=10),
            ).grid(row=0, column=col, padx=5, pady=(8, 2), sticky="w")

        self.study_name_entry = ctk.CTkEntry(controls)
        self.study_name_entry.insert(0, "Hadron Study")
        self.study_name_entry.grid(row=1, column=0, padx=5, pady=(0, 6), sticky="ew")

        self.study_energies_entry = ctk.CTkEntry(controls)
        self.study_energies_entry.insert(0, "1000,2500,4000,5500,6500,7000")
        self.study_energies_entry.grid(row=1, column=1, padx=5, pady=(0, 6), sticky="ew")

        self.study_preset_menu = ctk.CTkOptionMenu(
            controls,
            values=["ALL PRESETS"] + list(self.physics_presets.keys()),
        )
        self.study_preset_menu.set(self.active_preset)
        self.study_preset_menu.grid(row=1, column=2, padx=5, pady=(0, 6), sticky="ew")

        self.study_events_entry = ctk.CTkEntry(controls)
        self.study_events_entry.insert(0, "5000")
        self.study_events_entry.grid(row=1, column=3, padx=5, pady=(0, 6), sticky="ew")

        self.study_repeats_entry = ctk.CTkEntry(controls)
        self.study_repeats_entry.insert(0, "1")
        self.study_repeats_entry.grid(row=1, column=4, padx=5, pady=(0, 6), sticky="ew")

        self.study_workers_entry = ctk.CTkEntry(controls)
        self.study_workers_entry.insert(0, "1")
        self.study_workers_entry.grid(row=1, column=5, padx=5, pady=(0, 6), sticky="ew")

        ctk.CTkLabel(
            controls,
            text="Seed",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=2, column=0, padx=5, pady=(4, 2), sticky="w")

        self.study_seed_entry = ctk.CTkEntry(controls)
        self.study_seed_entry.insert(0, "42")
        self.study_seed_entry.grid(row=3, column=0, padx=5, pady=(0, 8), sticky="ew")

        self.study_status_label = ctk.CTkLabel(
            controls,
            text="Ready",
            text_color=self.MUTED,
            anchor="w",
        )
        self.study_status_label.grid(
            row=3, column=1, columnspan=2, padx=5, pady=(0, 8), sticky="ew"
        )

        ctk.CTkButton(
            controls,
            text="RUN STUDY",
            command=self._start_study,
            fg_color="#18566C",
            hover_color="#216F89",
        ).grid(row=3, column=3, padx=5, pady=(0, 8), sticky="ew")

        ctk.CTkButton(
            controls,
            text="REFRESH",
            command=self._load_study_queue,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=3, column=4, padx=5, pady=(0, 8), sticky="ew")

        ctk.CTkButton(
            controls,
            text="RESULTS",
            command=self._open_selected_study_results,
            fg_color="#3A3157",
            hover_color="#514277",
        ).grid(row=3, column=5, padx=5, pady=(0, 8), sticky="ew")

        actions = ctk.CTkFrame(controls, fg_color="transparent")
        actions.grid(row=4, column=0, columnspan=6, padx=5, pady=(0, 8), sticky="ew")
        actions.grid_columnconfigure((0, 1, 2, 3, 4, 5, 6), weight=1)

        ctk.CTkButton(
            actions,
            text="HTML REPORT",
            command=self._export_selected_study_html,
            height=30,
            fg_color="#3A3157",
            hover_color="#514277",
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            actions,
            text="REPORT PACKAGE ZIP",
            command=self._export_selected_study_package,
            height=30,
            fg_color="#5A4524",
            hover_color="#765D31",
        ).grid(row=0, column=1, padx=4, sticky="ew")

        ctk.CTkButton(
            actions,
            text="COMPARE STUDIES",
            command=self._open_study_compare,
            height=30,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=2, padx=4, sticky="ew")

        ctk.CTkButton(
            actions,
            text="TEMPLATES",
            command=self._open_study_templates,
            height=30,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=3, padx=4, sticky="ew")

        ctk.CTkButton(
            actions,
            text="PROVENANCE",
            command=self._export_selected_study_provenance,
            height=30,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=4, padx=4, sticky="ew")

        ctk.CTkButton(
            actions,
            text="INTEGRITY",
            command=self._export_selected_integrity_report,
            height=30,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=5, padx=4, sticky="ew")

        ctk.CTkButton(
            actions,
            text="CAPSULE",
            command=self._create_selected_study_capsule,
            height=30,
            fg_color="#36503E",
            hover_color="#4A6D55",
        ).grid(row=0, column=6, padx=(4, 0), sticky="ew")

        body = ctk.CTkFrame(self.study_window, fg_color="transparent")
        body.grid(row=3, column=0, padx=18, pady=(0, 18), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = self._panel(body)
        left.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)

        self.study_list = tk.Listbox(
            left,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            selectforeground=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 10),
        )
        self.study_list.grid(row=0, column=0, padx=10, pady=(10, 6), sticky="nsew")
        self.study_list.bind("<<ListboxSelect>>", self._show_study_detail)

        list_actions = ctk.CTkFrame(left, fg_color="transparent")
        list_actions.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")
        list_actions.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            list_actions,
            text="EXPORT JSON",
            command=self._export_selected_study_json,
            height=30,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            list_actions,
            text="CLONE / RERUN",
            command=self._clone_selected_study,
            height=30,
            fg_color="#3B334C",
            hover_color="#514567",
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        right = self._panel(body)
        right.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        self.study_detail = tk.Text(
            right,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        self.study_detail.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.study_detail.configure(state="disabled")

        self.study_window.protocol("WM_DELETE_WINDOW", self._close_study_queue)
        self._load_study_queue()
        self._poll_study_messages()

    def _clone_selected_study(self):
        study = self._selected_study()
        if study is None:
            return

        spec = clone_study_spec(study)
        self.study_name_entry.delete(0, "end")
        self.study_name_entry.insert(0, f"{study['name']} (clone)")

        self.study_energies_entry.delete(0, "end")
        self.study_energies_entry.insert(
            0,
            ",".join(f"{energy:g}" for energy in spec["energies"]),
        )

        presets = spec["presets"]
        if set(presets) == set(self.physics_presets.keys()):
            self.study_preset_menu.set("ALL PRESETS")
        else:
            self.study_preset_menu.set(presets[0])

        self.study_events_entry.delete(0, "end")
        self.study_events_entry.insert(0, str(spec["events"]))
        self.study_repeats_entry.delete(0, "end")
        self.study_repeats_entry.insert(0, str(spec["repeats"]))
        self.study_workers_entry.delete(0, "end")
        self.study_workers_entry.insert(0, str(spec["workers"]))
        self.study_seed_entry.delete(0, "end")
        self.study_seed_entry.insert(0, str(spec["seed"]))

        self.study_status_label.configure(
            text=f"Loaded study #{study['id']} into the form. Edit or rerun."
        )

    def _open_study_compare(self):
        complete = [
            study
            for study in list_studies(self.db_path)
            if study.get("result")
        ]
        if len(complete) < 2:
            if self.study_status_label is not None:
                self.study_status_label.configure(
                    text="At least two completed studies are required."
                )
            return

        if (
            self.study_compare_window is not None
            and self.study_compare_window.winfo_exists()
        ):
            self.study_compare_window.focus()
            return

        self.study_compare_window = ctk.CTkToplevel(self)
        self.study_compare_window.title("Hadron — Study Comparison")
        self.study_compare_window.geometry("1050x700")
        self.study_compare_window.minsize(860, 580)
        self.study_compare_window.configure(fg_color=self.BG)
        self.study_compare_window.grid_columnconfigure(0, weight=1)
        self.study_compare_window.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self.study_compare_window,
            text="STUDY COMPARISON",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=18, pady=(16, 6), sticky="w")

        controls = ctk.CTkFrame(
            self.study_compare_window,
            fg_color="transparent",
        )
        controls.grid(row=1, column=0, padx=18, pady=(0, 8), sticky="ew")
        controls.grid_columnconfigure((0, 1), weight=1)

        values = [
            f"#{study['id']} · {study['name']}"
            for study in complete
        ]
        self.study_compare_map = {
            f"#{study['id']} · {study['name']}": study
            for study in complete
        }

        self.study_compare_a = ctk.CTkOptionMenu(
            controls,
            values=values,
        )
        self.study_compare_a.set(values[0])
        self.study_compare_a.grid(
            row=0, column=0, padx=(0, 4), sticky="ew"
        )

        self.study_compare_b = ctk.CTkOptionMenu(
            controls,
            values=values,
        )
        self.study_compare_b.set(values[1])
        self.study_compare_b.grid(
            row=0, column=1, padx=(4, 0), sticky="ew"
        )

        ctk.CTkButton(
            controls,
            text="COMPARE",
            command=self._refresh_study_compare,
            height=30,
        ).grid(row=1, column=0, columnspan=2, pady=(6, 0), sticky="ew")

        body = ctk.CTkFrame(
            self.study_compare_window,
            fg_color="transparent",
        )
        body.grid(row=2, column=0, padx=18, pady=(0, 18), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = self._panel(body)
        left.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)

        self.study_compare_text = tk.Text(
            left,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        self.study_compare_text.grid(
            row=0, column=0, padx=10, pady=10, sticky="nsew"
        )

        right = self._panel(body)
        right.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        self.study_compare_figure = Figure(
            figsize=(6.5, 5.2),
            dpi=100,
            facecolor=self.PANEL,
        )
        self.study_compare_ax = self.study_compare_figure.add_subplot(111)
        self.study_compare_canvas = FigureCanvasTkAgg(
            self.study_compare_figure,
            master=right,
        )
        self.study_compare_canvas.get_tk_widget().configure(
            bg=self.PANEL,
            highlightthickness=0,
        )
        self.study_compare_canvas.get_tk_widget().grid(
            row=0, column=0, padx=10, pady=10, sticky="nsew"
        )

        self.study_compare_window.protocol(
            "WM_DELETE_WINDOW",
            self._close_study_compare,
        )
        self._refresh_study_compare()

    def _close_study_compare(self):
        if self.study_compare_window is not None:
            try:
                self.study_compare_window.destroy()
            except tk.TclError:
                pass
        self.study_compare_window = None

    def _refresh_study_compare(self):
        if self.study_compare_window is None:
            return

        study_a = self.study_compare_map[self.study_compare_a.get()]
        study_b = self.study_compare_map[self.study_compare_b.get()]
        comparison = compare_studies(study_a, study_b)

        lines = [
            "CONFIGURATION DIFFERENCES",
            "",
        ]
        if comparison["config_diff"]:
            for item in comparison["config_diff"]:
                lines.append(
                    f"{item['field']}:"
                    f"\n  A = {item['a']}"
                    f"\n  B = {item['b']}\n"
                )
        else:
            lines.append("No configuration differences.\n")

        lines += ["", "RESULT DELTAS", ""]
        for row in comparison["grid"]:
            delta = row["acceptance_delta_pp"]
            higgs_delta = row["higgs_rate_delta"]
            lines.append(
                f"{row['preset']:<14} {row['beam_energy_gev']:7.0f} GeV  "
                f"Δaccept={delta:+.3f} pp  "
                f"ΔHiggs/1000={higgs_delta:+.3f}"
                if delta is not None and higgs_delta is not None
                else
                f"{row['preset']:<14} {row['beam_energy_gev']:7.0f} GeV  incomplete pair"
            )

        self.study_compare_text.delete("1.0", "end")
        self.study_compare_text.insert("1.0", "\n".join(lines))

        ax = self.study_compare_ax
        ax.clear()
        self._style_study_ax(ax)
        ax.set_title("Acceptance delta: Study A − Study B")
        ax.set_xlabel("Beam energy (GeV)")
        ax.set_ylabel("Δ acceptance (percentage points)")
        ax.axhline(0.0, linewidth=1.0, alpha=0.5)

        by_preset = {}
        for row in comparison["grid"]:
            if row["acceptance_delta_pp"] is None:
                continue
            by_preset.setdefault(row["preset"], []).append(row)

        for preset, points in by_preset.items():
            points.sort(key=lambda item: item["beam_energy_gev"])
            ax.plot(
                [item["beam_energy_gev"] for item in points],
                [item["acceptance_delta_pp"] for item in points],
                marker="o",
                label=preset,
            )

        legend = ax.legend(fontsize=8)
        if legend:
            legend.get_frame().set_facecolor(self.PANEL_2)
            legend.get_frame().set_edgecolor(self.BORDER)
            for label in legend.get_texts():
                label.set_color(self.TEXT)
        ax.grid(alpha=0.13)
        self.study_compare_canvas.draw_idle()

    def _open_study_templates(self):
        if (
            self.study_template_window is not None
            and self.study_template_window.winfo_exists()
        ):
            self.study_template_window.focus()
            self._load_study_templates()
            return

        self.study_template_window = ctk.CTkToplevel(self)
        self.study_template_window.title("Hadron — Study Templates")
        self.study_template_window.geometry("760x520")
        self.study_template_window.configure(fg_color=self.BG)
        self.study_template_window.grid_columnconfigure(0, weight=1)
        self.study_template_window.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self.study_template_window,
            text="STUDY TEMPLATES",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=18, pady=(16, 8), sticky="w")

        body = ctk.CTkFrame(
            self.study_template_window,
            fg_color="transparent",
        )
        body.grid(row=1, column=0, padx=18, pady=(0, 10), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self.study_template_list = tk.Listbox(
            body,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 10),
        )
        self.study_template_list.grid(
            row=0, column=0, padx=0, pady=0, sticky="nsew"
        )

        buttons = ctk.CTkFrame(
            self.study_template_window,
            fg_color="transparent",
        )
        buttons.grid(row=2, column=0, padx=18, pady=(0, 16), sticky="ew")
        buttons.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkButton(
            buttons,
            text="SAVE SELECTED STUDY",
            command=self._save_selected_study_template,
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            buttons,
            text="LOAD TEMPLATE",
            command=self._load_selected_template_into_form,
        ).grid(row=0, column=1, padx=4, sticky="ew")

        ctk.CTkButton(
            buttons,
            text="DELETE TEMPLATE",
            command=self._delete_selected_template,
            fg_color="#71303A",
            hover_color="#923E4B",
        ).grid(row=0, column=2, padx=(4, 0), sticky="ew")

        self.study_template_window.protocol(
            "WM_DELETE_WINDOW",
            self._close_study_templates,
        )
        self._load_study_templates()

    def _close_study_templates(self):
        if self.study_template_window is not None:
            try:
                self.study_template_window.destroy()
            except tk.TclError:
                pass
        self.study_template_window = None
        self.study_template_list = None
        self.study_template_rows = []

    def _load_study_templates(self):
        if self.study_template_list is None:
            return
        self.study_template_rows = list_templates(self.db_path)
        self.study_template_list.delete(0, "end")
        if not self.study_template_rows:
            self.study_template_list.insert("end", "No study templates.")
            return
        for item in self.study_template_rows:
            source = (
                f"study #{item['source_study_id']}"
                if item["source_study_id"] is not None
                else "manual"
            )
            self.study_template_list.insert(
                "end",
                f"#{item['id']:04d}  {item['name']}  ·  {source}",
            )

    def _selected_template(self):
        if self.study_template_list is None or not self.study_template_rows:
            return None
        selected = self.study_template_list.curselection()
        if not selected:
            return None
        return self.study_template_rows[selected[0]]

    def _save_selected_study_template(self):
        study = self._selected_study()
        if study is None:
            if self.study_status_label is not None:
                self.study_status_label.configure(
                    text="Select a study before saving a template."
                )
            return
        template_id = save_template(
            self.db_path,
            name=f"{study['name']} Template",
            spec=study["spec"],
            source_study_id=study["id"],
        )
        self._load_study_templates()
        self.log(
            "STUDY",
            f"Saved template #{template_id} from study #{study['id']}.",
            "saved",
        )

    def _load_selected_template_into_form(self):
        template = self._selected_template()
        if template is None:
            return
        spec = template["spec"]
        self.study_name_entry.delete(0, "end")
        self.study_name_entry.insert(0, template["name"])

        self.study_energies_entry.delete(0, "end")
        self.study_energies_entry.insert(
            0,
            ",".join(f"{energy:g}" for energy in spec["energies"]),
        )

        presets = spec["presets"]
        if set(presets) == set(self.physics_presets.keys()):
            self.study_preset_menu.set("ALL PRESETS")
        else:
            self.study_preset_menu.set(presets[0])

        for widget, value in [
            (self.study_events_entry, spec["events"]),
            (self.study_repeats_entry, spec["repeats"]),
            (self.study_workers_entry, spec["workers"]),
            (self.study_seed_entry, spec["seed"]),
        ]:
            widget.delete(0, "end")
            widget.insert(0, str(value))

        self.study_status_label.configure(
            text=f"Loaded template #{template['id']} into the form."
        )

    def _delete_selected_template(self):
        template = self._selected_template()
        if template is None:
            return
        delete_template(self.db_path, template["id"])
        self._load_study_templates()

    def _export_selected_study_provenance(self):
        study = self._selected_study()
        if study is None:
            if self.study_status_label is not None:
                self.study_status_label.configure(
                    text="Select a study before exporting provenance."
                )
            return

        path = filedialog.asksaveasfilename(
            title=f"Export provenance for study #{study['id']}",
            defaultextension=".provenance.json",
            filetypes=[("Hadron provenance", "*.provenance.json"), ("JSON", "*.json")],
            initialfile=f"hadron-study-{study['id']}.provenance.json",
        )
        if not path:
            return

        write_study_provenance(path, study)
        self.log(
            "EXPORT",
            f"Study #{study['id']} provenance saved: {path}",
            "saved",
        )

    def _export_selected_integrity_report(self):
        study = self._selected_study()
        if study is None:
            if self.study_status_label is not None:
                self.study_status_label.configure(
                    text="Select a study before exporting integrity."
                )
            return

        path = filedialog.asksaveasfilename(
            title=f"Export integrity report for study #{study['id']}",
            defaultextension=".integrity.json",
            filetypes=[("Hadron integrity report", "*.integrity.json"), ("JSON", "*.json")],
            initialfile=f"hadron-study-{study['id']}.integrity.json",
        )
        if not path:
            return

        write_integrity_report(
            path,
            release_verification=getattr(
                self, "release_verification_report", None
            ),
            release_signature=self.release_signature_report,
            study=study,
            db_path=self.db_path,
        )
        self.log(
            "EXPORT",
            f"Study #{study['id']} integrity report saved: {path}",
            "saved",
        )

    def _create_selected_study_capsule(self):
        study = self._selected_study()
        if study is None or not study.get("result"):
            if self.study_status_label is not None:
                self.study_status_label.configure(
                    text="Select a completed study before creating a capsule."
                )
            return

        path = filedialog.asksaveasfilename(
            title=f"Seal study #{study['id']} as a Hadron capsule",
            defaultextension=".hadron-capsule.zip",
            filetypes=[
                ("Hadron capsule", "*.hadron-capsule.zip"),
                ("ZIP archive", "*.zip"),
            ],
            initialfile=f"Hadron-study-{study['id']}.hadron-capsule.zip",
        )
        if not path:
            return

        try:
            result = create_capsule_from_database(
                self.db_path,
                study["id"],
                path,
            )
        except Exception as exc:
            self.log("CAPSULE", f"Capsule creation failed: {exc}", "discarded")
            return

        self.log(
            "CAPSULE",
            f"Study #{study['id']} capsule sealed: {path}",
            "saved" if result["verification"]["ok"] else "discarded",
        )
        if self.study_status_label is not None:
            self.study_status_label.configure(
                text=(
                    f"Capsule #{result['registry_id']} created · "
                    f"{result['capsule_sha256'][:16]}…"
                )
            )

    def _open_selected_study_results(self):
        study = self._selected_study()
        if study is None or not study.get("result"):
            if self.study_status_label is not None:
                self.study_status_label.configure(
                    text="Select a completed study before opening results."
                )
            return

        self.study_results_selected_id = int(study["id"])
        if (
            self.study_results_window is not None
            and self.study_results_window.winfo_exists()
        ):
            self.study_results_window.focus()
            self._refresh_study_results_dashboard()
            return

        self.study_results_window = ctk.CTkToplevel(self)
        self.study_results_window.title(
            f"Hadron — Study #{study['id']} Results"
        )
        self.study_results_window.geometry("1160x800")
        self.study_results_window.minsize(920, 680)
        self.study_results_window.configure(fg_color=self.BG)
        self.study_results_window.grid_columnconfigure(0, weight=1)
        self.study_results_window.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(
            self.study_results_window,
            fg_color="transparent",
        )
        header.grid(row=0, column=0, padx=18, pady=(14, 6), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        self.study_results_title = ctk.CTkLabel(
            header,
            text="STUDY RESULTS",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        self.study_results_title.grid(row=0, column=0, sticky="w")

        self.study_results_subtitle = ctk.CTkLabel(
            header,
            text="",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10),
        )
        self.study_results_subtitle.grid(row=1, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text="Preset filter",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=1, padx=(8, 4), sticky="e")

        self.study_results_preset_menu = ctk.CTkOptionMenu(
            header,
            values=["ALL"] + list(self.physics_presets.keys()),
            width=130,
            command=self._on_study_results_filter,
        )
        self.study_results_preset_menu.set("ALL")
        self.study_results_preset_menu.grid(
            row=0, column=2, rowspan=2, padx=(0, 8), sticky="e"
        )

        ctk.CTkButton(
            header,
            text="PACKAGE ZIP",
            command=self._export_selected_study_package,
            width=100,
            height=30,
            fg_color="#5A4524",
            hover_color="#765D31",
        ).grid(row=0, column=3, rowspan=2, sticky="e")

        self.study_results_tabs = ctk.CTkTabview(
            self.study_results_window,
            fg_color=self.PANEL,
            segmented_button_fg_color=self.PANEL_2,
        )
        self.study_results_tabs.grid(
            row=1, column=0, padx=18, pady=(4, 18), sticky="nsew"
        )

        for name in [
            "ACCEPTANCE HEATMAP",
            "HIGGS HEATMAP",
            "ENERGY OVERLAY",
            "RANKING",
        ]:
            self.study_results_tabs.add(name)

        self._build_study_heatmap_tab("ACCEPTANCE HEATMAP")
        self._build_study_heatmap_tab("HIGGS HEATMAP")
        self._build_study_overlay_tab()
        self._build_study_ranking_tab()

        self.study_results_window.protocol(
            "WM_DELETE_WINDOW",
            self._close_study_results,
        )
        self._refresh_study_results_dashboard()

    def _close_study_results(self):
        if self.study_results_window is not None:
            try:
                self.study_results_window.destroy()
            except tk.TclError:
                pass
        self.study_results_window = None
        self.study_results_tabs = None
        self.study_results_selected_id = None

    def _study_results_study(self):
        if self.study_results_selected_id is None:
            return None
        return get_study(
            self.db_path,
            self.study_results_selected_id,
        )

    def _study_results_rows(self):
        study = self._study_results_study()
        if study is None:
            return []
        rows = normalized_results(study)
        return filter_results(
            rows,
            preset=self.study_results_filter_preset,
        )

    def _on_study_results_filter(self, value):
        self.study_results_filter_preset = value
        self._refresh_study_results_dashboard()

    def _build_study_heatmap_tab(self, name):
        tab = self.study_results_tabs.tab(name)
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        fig = Figure(figsize=(8.5, 5.5), dpi=100, facecolor=self.PANEL)
        ax = fig.add_subplot(111)
        canvas = FigureCanvasTkAgg(fig, master=tab)
        canvas.get_tk_widget().configure(
            bg=self.PANEL,
            highlightthickness=0,
        )
        canvas.get_tk_widget().grid(
            row=0, column=0, padx=10, pady=10, sticky="nsew"
        )

        if name == "ACCEPTANCE HEATMAP":
            self.study_acceptance_figure = fig
            self.study_acceptance_ax = ax
            self.study_acceptance_canvas = canvas
        else:
            self.study_higgs_figure = fig
            self.study_higgs_ax = ax
            self.study_higgs_canvas = canvas

    def _build_study_overlay_tab(self):
        tab = self.study_results_tabs.tab("ENERGY OVERLAY")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        self.study_overlay_figure = Figure(
            figsize=(8.5, 5.5),
            dpi=100,
            facecolor=self.PANEL,
        )
        self.study_overlay_ax = self.study_overlay_figure.add_subplot(111)
        self.study_overlay_canvas = FigureCanvasTkAgg(
            self.study_overlay_figure,
            master=tab,
        )
        self.study_overlay_canvas.get_tk_widget().configure(
            bg=self.PANEL,
            highlightthickness=0,
        )
        self.study_overlay_canvas.get_tk_widget().grid(
            row=0, column=0, padx=10, pady=10, sticky="nsew"
        )

    def _build_study_ranking_tab(self):
        tab = self.study_results_tabs.tab("RANKING")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        controls = ctk.CTkFrame(tab, fg_color="transparent")
        controls.grid(row=0, column=0, padx=10, pady=(8, 2), sticky="ew")
        controls.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            controls,
            text="Rank by",
            text_color=self.MUTED,
        ).grid(row=0, column=0, sticky="e", padx=(0, 6))

        self.study_rank_metric = ctk.CTkOptionMenu(
            controls,
            values=[
                "acceptance_rate",
                "higgs_rate_per_1000",
                "higgs_count",
                "saved_count",
            ],
            width=180,
            command=lambda _v: self._refresh_study_ranking(),
        )
        self.study_rank_metric.set("acceptance_rate")
        self.study_rank_metric.grid(row=0, column=1, sticky="e")

        self.study_ranking_list = tk.Listbox(
            tab,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            selectforeground=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 10),
        )
        self.study_ranking_list.grid(
            row=1, column=0, padx=10, pady=(4, 10), sticky="nsew"
        )

    def _style_study_ax(self, ax):
        ax.set_facecolor(self.PANEL)
        for spine in ax.spines.values():
            spine.set_color("#364355")
        ax.tick_params(colors=self.MUTED)
        ax.xaxis.label.set_color(self.MUTED)
        ax.yaxis.label.set_color(self.MUTED)
        ax.title.set_color(self.TEXT)

    def _draw_study_heatmap(self, kind):
        rows = self._study_results_rows()
        if kind == "acceptance":
            metric = "acceptance_mean"
            ax = self.study_acceptance_ax
            canvas = self.study_acceptance_canvas
            title = "Mean acceptance (%)"
        else:
            metric = "higgs_rate_mean_per_1000"
            ax = self.study_higgs_ax
            canvas = self.study_higgs_canvas
            title = "Mean Higgs candidates / 1000 events"

        ax.clear()
        self._style_study_ax(ax)

        if not rows:
            ax.text(
                0.5,
                0.5,
                "No matching study results",
                transform=ax.transAxes,
                ha="center",
                va="center",
                color=self.MUTED,
            )
            canvas.draw_idle()
            return

        grid = heatmap_grid(rows, metric=metric)
        matrix = np.asarray(grid["matrix"], dtype=float)

        image = ax.imshow(matrix, aspect="auto")
        ax.set_title(title)
        ax.set_xlabel("Beam energy (GeV)")
        ax.set_ylabel("Preset")
        ax.set_xticks(range(len(grid["energies"])))
        ax.set_xticklabels(
            [f"{energy:.0f}" for energy in grid["energies"]]
        )
        ax.set_yticks(range(len(grid["presets"])))
        ax.set_yticklabels(grid["presets"])

        for r in range(matrix.shape[0]):
            for c in range(matrix.shape[1]):
                value = matrix[r, c]
                if np.isfinite(value):
                    ax.text(
                        c,
                        r,
                        f"{value:.2f}",
                        ha="center",
                        va="center",
                        color=self.TEXT,
                        fontsize=8,
                    )

        canvas.draw_idle()

    def _draw_study_overlay(self):
        rows = self._study_results_rows()
        ax = self.study_overlay_ax
        ax.clear()
        self._style_study_ax(ax)
        ax.set_title("Acceptance vs beam energy")
        ax.set_xlabel("Beam energy (GeV)")
        ax.set_ylabel("Acceptance (%)")
        ax.grid(alpha=0.13)

        aggregate = aggregate_by_preset_energy(rows)
        presets = sorted({item["preset"] for item in aggregate})

        if not aggregate:
            ax.text(
                0.5,
                0.5,
                "No matching study results",
                transform=ax.transAxes,
                ha="center",
                va="center",
                color=self.MUTED,
            )
            self.study_overlay_canvas.draw_idle()
            return

        for preset in presets:
            points = [
                item
                for item in aggregate
                if item["preset"] == preset
            ]
            x = [item["beam_energy_gev"] for item in points]
            y = [item["acceptance_mean"] for item in points]
            low = [item["ci95_low_mean"] for item in points]
            high = [item["ci95_high_mean"] for item in points]

            ax.plot(x, y, marker="o", linewidth=1.6, label=preset)
            ax.fill_between(x, low, high, alpha=0.12)

        legend = ax.legend(fontsize=8)
        if legend:
            legend.get_frame().set_facecolor(self.PANEL_2)
            legend.get_frame().set_edgecolor(self.BORDER)
            for item in legend.get_texts():
                item.set_color(self.TEXT)

        self.study_overlay_canvas.draw_idle()

    def _refresh_study_ranking(self):
        if not hasattr(self, "study_ranking_list"):
            return

        rows = self._study_results_rows()
        metric = self.study_rank_metric.get()
        ranked = rank_results(rows, metric=metric)

        self.study_ranking_list.delete(0, "end")
        if not ranked:
            self.study_ranking_list.insert(
                "end",
                "No matching study results.",
            )
            return

        for index, row in enumerate(ranked, start=1):
            value = float(row.get(metric, 0.0))
            self.study_ranking_list.insert(
                "end",
                (
                    f"{index:03d}  "
                    f"{row.get('preset', ''):<14}  "
                    f"{float(row.get('beam_energy_gev', 0)):7.0f} GeV  "
                    f"rep {int(row.get('repeat', 0)):02d}  "
                    f"{metric}={value:.4f}"
                ),
            )

    def _refresh_study_results_dashboard(self):
        study = self._study_results_study()
        if study is None:
            return

        if hasattr(self, "study_results_title"):
            self.study_results_title.configure(
                text=f"STUDY #{study['id']} RESULTS"
            )
            result = study.get("result") or {}
            self.study_results_subtitle.configure(
                text=(
                    f"{study['name']} · {study['status']} · "
                    f"{result.get('jobs', 0)} jobs"
                )
            )

        self._draw_study_heatmap("acceptance")
        self._draw_study_heatmap("higgs")
        self._draw_study_overlay()
        self._refresh_study_ranking()

    def _export_selected_study_package(self):
        study = self._selected_study()
        if study is None and self.study_results_selected_id is not None:
            study = get_study(
                self.db_path,
                self.study_results_selected_id,
            )
        if study is None or not study.get("result"):
            if self.study_status_label is not None:
                self.study_status_label.configure(
                    text="Select a completed study before packaging."
                )
            return

        path = filedialog.asksaveasfilename(
            title=f"Create report package for study #{study['id']}",
            defaultextension=".zip",
            filetypes=[("ZIP archive", "*.zip")],
            initialfile=f"Hadron-study-{study['id']}-report.zip",
        )
        if not path:
            return

        create_study_report_package(path, study)
        self.log(
            "EXPORT",
            f"Study #{study['id']} report package saved: {path}",
            "saved",
        )

    def _close_study_queue(self):
        self._close_study_results()
        self._close_study_compare()
        self._close_study_templates()
        if self.study_window is not None:
            try:
                self.study_window.destroy()
            except tk.TclError:
                pass
        self.study_window = None
        self.study_list = None
        self.study_detail = None
        self.study_rows = []

    def _study_input_spec(self):
        try:
            energies = [
                float(value.strip())
                for value in self.study_energies_entry.get().split(",")
                if value.strip()
            ]
            preset_value = self.study_preset_menu.get()
            presets = (
                list(self.physics_presets.keys())
                if preset_value == "ALL PRESETS"
                else [preset_value]
            )
            raw = {
                "events": int(self.study_events_entry.get()),
                "energies": energies,
                "presets": presets,
                "seed": int(self.study_seed_entry.get()),
                "repeats": int(self.study_repeats_entry.get()),
                "workers": int(self.study_workers_entry.get()),
                "l1_threshold": self.l1_energy_threshold,
                "met_threshold": self.met_trigger_threshold,
                "higgs_window": self.higgs_window_gev,
                "noise": self.detector_noise_enabled,
            }
        except (ValueError, AttributeError) as exc:
            raise ValueError("Study fields contain invalid numbers.") from exc

        return normalize_study_spec(raw)

    def _start_study(self):
        if self.study_active_id is not None:
            self.study_status_label.configure(
                text=f"Study #{self.study_active_id} is still running."
            )
            return

        try:
            spec = self._study_input_spec()
        except ValueError as exc:
            self.study_status_label.configure(text=f"Input error: {exc}")
            return

        name = self.study_name_entry.get().strip() or "Hadron Study"
        study_id = create_study(self.db_path, name=name, spec=spec)
        set_study_status(self.db_path, study_id, "RUNNING")

        self.study_active_id = study_id
        self.study_status_label.configure(
            text=(
                f"Running study #{study_id}: "
                f"{len(spec['energies']) * len(spec['presets']) * spec['repeats']} jobs"
            )
        )
        self._load_study_queue()

        self.study_thread = threading.Thread(
            target=self._study_worker,
            args=(study_id, spec),
            daemon=True,
        )
        self.study_thread.start()

    def _study_worker(self, study_id, spec):
        try:
            payload = run_job_spec(
                spec,
                workers=spec["workers"],
            )
            self.study_messages.put(("complete", study_id, payload))
        except Exception as exc:
            self.study_messages.put(("failed", study_id, str(exc)))

    def _poll_study_messages(self):
        while True:
            try:
                message = self.study_messages.get_nowait()
            except queue.Empty:
                break

            kind, study_id, payload = message
            if kind == "complete":
                set_study_status(
                    self.db_path,
                    study_id,
                    "COMPLETE",
                    result=payload,
                )
                if self.study_status_label is not None:
                    self.study_status_label.configure(
                        text=f"Study #{study_id} complete · {payload.get('jobs', 0)} jobs"
                    )
                self.log(
                    "STUDY",
                    f"Study #{study_id} completed ({payload.get('jobs', 0)} jobs).",
                    "saved",
                )
            else:
                set_study_status(
                    self.db_path,
                    study_id,
                    "FAILED",
                    error_text=str(payload),
                )
                if self.study_status_label is not None:
                    self.study_status_label.configure(
                        text=f"Study #{study_id} failed."
                    )
                self.log(
                    "STUDY",
                    f"Study #{study_id} failed: {payload}",
                    "discarded",
                )

            if self.study_active_id == study_id:
                self.study_active_id = None
                self.study_thread = None
            self._load_study_queue()

        if self.study_window is not None:
            try:
                if self.study_window.winfo_exists():
                    self.after(250, self._poll_study_messages)
            except tk.TclError:
                pass

    def _load_study_queue(self):
        if self.study_list is None:
            return

        mark_running_studies_interrupted(
            self.db_path,
            exclude_id=self.study_active_id,
        )

        self.study_rows = list_studies(self.db_path)
        self.study_list.delete(0, "end")

        if not self.study_rows:
            self.study_list.insert("end", "No saved studies.")
            return

        for study in self.study_rows:
            spec = study["spec"]
            job_count = (
                len(spec.get("energies", []))
                * len(spec.get("presets", []))
                * int(spec.get("repeats", 1))
            )
            self.study_list.insert(
                "end",
                (
                    f"#{study['id']:04d}  {study['status']:<11}  "
                    f"{study['name'][:24]:<24}  {job_count:4d} jobs"
                ),
            )

    def _selected_study(self):
        if self.study_list is None or not self.study_rows:
            return None
        selected = self.study_list.curselection()
        if not selected:
            return None
        return self.study_rows[selected[0]]

    def _show_study_detail(self, _event=None):
        study = self._selected_study()
        if study is None or self.study_detail is None:
            return
        self.study_detail.configure(state="normal")
        self.study_detail.delete("1.0", "end")
        self.study_detail.insert("1.0", study_summary_text(study))
        self.study_detail.configure(state="disabled")

    def _export_selected_study_json(self):
        study = self._selected_study()
        if study is None:
            return

        path = filedialog.asksaveasfilename(
            title=f"Export study #{study['id']}",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile=f"hadron-study-{study['id']}.json",
        )
        if not path:
            return

        Path(path).write_text(
            json.dumps(study, indent=2),
            encoding="utf-8",
        )
        self.log("EXPORT", f"Study #{study['id']} JSON saved: {path}", "saved")

    def _export_selected_study_html(self):
        study = self._selected_study()
        if study is None or not study.get("result"):
            if self.study_status_label is not None:
                self.study_status_label.configure(
                    text="Select a completed study before exporting HTML."
                )
            return

        results = study["result"].get("results", [])
        if not results:
            return

        path = filedialog.asksaveasfilename(
            title=f"Export study #{study['id']} HTML report",
            defaultextension=".html",
            filetypes=[("HTML files", "*.html")],
            initialfile=f"hadron-study-{study['id']}.html",
        )
        if not path:
            return

        report = render_html_report(
            results,
            title=f"Hadron v{__version__} Study #{study['id']} — {study['name']}",
        )
        Path(path).write_text(report, encoding="utf-8")
        self.log("EXPORT", f"Study #{study['id']} HTML report saved: {path}", "saved")

    # ------------------------------------------------------------------
    # Analysis workspace
    # ------------------------------------------------------------------

    def open_analysis_workspace(self):
        if self.workspace_window is not None and self.workspace_window.winfo_exists():
            self.workspace_window.focus()
            self._workspace_refresh_all()
            return

        self.workspace_window = ctk.CTkToplevel(self)
        self.workspace_window.title("Hadron — Analysis Workspace")
        self.workspace_window.geometry("1120x780")
        self.workspace_window.minsize(900, 650)
        self.workspace_window.configure(fg_color=self.BG)
        self.workspace_window.grid_columnconfigure(0, weight=1)
        self.workspace_window.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.workspace_window, fg_color="transparent")
        header.grid(row=0, column=0, padx=18, pady=(14, 6), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="ANALYSIS WORKSPACE",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        self.workspace_status = ctk.CTkLabel(
            header,
            text="Toy-simulation analysis · not calibrated experimental physics",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10),
        )
        self.workspace_status.grid(row=1, column=0, sticky="w")

        ctk.CTkButton(
            header,
            text="REFRESH ALL",
            command=self._workspace_refresh_all,
            width=100,
            height=30,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=0, column=1, rowspan=2, padx=(8, 0), sticky="e")

        self.workspace_tabs = ctk.CTkTabview(
            self.workspace_window,
            fg_color=self.PANEL,
            segmented_button_fg_color=self.PANEL_2,
        )
        self.workspace_tabs.grid(row=1, column=0, padx=18, pady=(4, 18), sticky="nsew")

        for name in ["OVERLAY", "TRIGGER CURVE", "DETECTOR MATRIX", "MASS FIT", "PROJECTS"]:
            self.workspace_tabs.add(name)

        self._workspace_build_overlay_tab()
        self._workspace_build_trigger_tab()
        self._workspace_build_matrix_tab()
        self._workspace_build_mass_tab()
        self._workspace_build_projects_tab()

        self.workspace_window.protocol("WM_DELETE_WINDOW", self._close_analysis_workspace)
        self._workspace_refresh_all()

    def _close_analysis_workspace(self):
        if self.workspace_window is not None:
            try:
                self.workspace_window.destroy()
            except tk.TclError:
                pass
        self.workspace_window = None
        self.workspace_tabs = None
        self.workspace_overlay_canvas = None
        self.workspace_trigger_canvas = None
        self.workspace_matrix_canvas = None
        self.workspace_mass_canvas = None
        self.workspace_project_list = None

    def _workspace_make_plot(self, parent):
        figure = Figure(figsize=(7.6, 5.2), dpi=100, facecolor=self.PANEL)
        ax = figure.add_subplot(111)
        canvas = FigureCanvasTkAgg(figure, master=parent)
        canvas.get_tk_widget().configure(bg=self.PANEL, highlightthickness=0)
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)
        return figure, ax, canvas

    def _workspace_style_ax(self, ax):
        ax.set_facecolor(self.PANEL)
        for spine in ax.spines.values():
            spine.set_color("#364355")
        ax.tick_params(colors=self.MUTED)
        ax.xaxis.label.set_color(self.MUTED)
        ax.yaxis.label.set_color(self.MUTED)
        ax.title.set_color(self.TEXT)
        ax.grid(alpha=0.13)

    def _workspace_load_experiments(self, limit=100):
        with connect_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, summary_json
                FROM experiments
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()

        output = []
        for experiment_id, payload in rows:
            try:
                summary = enrich_summary(json.loads(payload))
                summary["experiment_id"] = experiment_id
                output.append(summary)
            except (TypeError, json.JSONDecodeError):
                continue
        return output

    def _workspace_build_overlay_tab(self):
        tab = self.workspace_tabs.tab("OVERLAY")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        host = ctk.CTkFrame(tab, fg_color="transparent")
        host.grid(row=0, column=0, sticky="nsew")
        self.workspace_overlay_figure, self.workspace_overlay_ax, self.workspace_overlay_canvas = (
            self._workspace_make_plot(host)
        )

    def _workspace_refresh_overlay(self):
        if self.workspace_overlay_canvas is None:
            return
        ax = self.workspace_overlay_ax
        ax.clear()
        self._workspace_style_ax(ax)
        ax.set_title("Experiment overlay · acceptance vs beam energy")
        ax.set_xlabel("Beam energy (GeV)")
        ax.set_ylabel("Acceptance (%)")

        summaries = self._workspace_load_experiments(200)
        groups = experiment_overlay_points(summaries)

        if not groups:
            ax.text(
                0.5, 0.5, "No saved experiments yet",
                transform=ax.transAxes, ha="center", va="center", color=self.MUTED
            )
        else:
            for preset, points in groups.items():
                x = [p["energy"] for p in points]
                y = [p["acceptance"] for p in points]
                ax.plot(x, y, marker="o", linewidth=1.5, label=preset)
                for p in points:
                    ax.vlines(
                        p["energy"],
                        p["ci_low"],
                        p["ci_high"],
                        linewidth=0.8,
                        alpha=0.6,
                    )
            legend = ax.legend(fontsize=8)
            if legend:
                legend.get_frame().set_facecolor(self.PANEL_2)
                legend.get_frame().set_edgecolor(self.BORDER)
                for label in legend.get_texts():
                    label.set_color(self.TEXT)

        self.workspace_overlay_canvas.draw_idle()

    def _workspace_build_trigger_tab(self):
        tab = self.workspace_tabs.tab("TRIGGER CURVE")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        controls = ctk.CTkFrame(tab, fg_color="transparent")
        controls.grid(row=0, column=0, padx=10, pady=8, sticky="ew")
        controls.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.ws_trigger_preset = ctk.CTkOptionMenu(
            controls,
            values=list(self.physics_presets.keys()),
        )
        self.ws_trigger_preset.set(self.active_preset)
        self.ws_trigger_preset.grid(row=0, column=0, padx=4, sticky="ew")

        self.ws_trigger_energy = ctk.CTkEntry(controls)
        self.ws_trigger_energy.insert(0, f"{self.target_energy:.0f}")
        self.ws_trigger_energy.grid(row=0, column=1, padx=4, sticky="ew")

        self.ws_trigger_events = ctk.CTkEntry(controls)
        self.ws_trigger_events.insert(0, "3000")
        self.ws_trigger_events.grid(row=0, column=2, padx=4, sticky="ew")

        ctk.CTkButton(
            controls,
            text="RUN THRESHOLD SCAN",
            command=self._workspace_refresh_trigger,
            fg_color="#18566C",
            hover_color="#216F89",
        ).grid(row=0, column=3, padx=4, sticky="ew")

        host = ctk.CTkFrame(tab, fg_color="transparent")
        host.grid(row=1, column=0, sticky="nsew")
        self.workspace_trigger_figure, self.workspace_trigger_ax, self.workspace_trigger_canvas = (
            self._workspace_make_plot(host)
        )

    def _workspace_refresh_trigger(self):
        if self.workspace_trigger_canvas is None:
            return
        try:
            preset = self.ws_trigger_preset.get()
            energy = float(self.ws_trigger_energy.get())
            events = int(self.ws_trigger_events.get())
        except (ValueError, AttributeError):
            return

        thresholds = np.linspace(2000.0, 9000.0, 15)
        curve = trigger_efficiency_curve(
            events=events,
            energy=energy,
            preset=preset,
            seed=4242,
            thresholds=thresholds,
            met_threshold=self.met_trigger_threshold,
            higgs_window=self.higgs_window_gev,
            noise=self.detector_noise_enabled,
        )

        ax = self.workspace_trigger_ax
        ax.clear()
        self._workspace_style_ax(ax)
        ax.set_title("Trigger efficiency curve")
        ax.set_xlabel("L1 transverse-energy threshold (GeV)")
        ax.set_ylabel("Efficiency (%)")
        ax.plot(
            [p["threshold"] for p in curve],
            [p["l1_efficiency"] for p in curve],
            marker="o",
            label="L1 pass",
        )
        ax.plot(
            [p["threshold"] for p in curve],
            [p["final_efficiency"] for p in curve],
            marker="s",
            label="Final saved",
        )
        legend = ax.legend(fontsize=8)
        if legend:
            legend.get_frame().set_facecolor(self.PANEL_2)
            legend.get_frame().set_edgecolor(self.BORDER)
            for label in legend.get_texts():
                label.set_color(self.TEXT)
        self.workspace_trigger_canvas.draw_idle()

    def _workspace_build_matrix_tab(self):
        tab = self.workspace_tabs.tab("DETECTOR MATRIX")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        controls = ctk.CTkFrame(tab, fg_color="transparent")
        controls.grid(row=0, column=0, padx=10, pady=8, sticky="ew")
        controls.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.ws_matrix_preset = ctk.CTkOptionMenu(
            controls,
            values=list(self.physics_presets.keys()),
        )
        self.ws_matrix_preset.set(self.active_preset)
        self.ws_matrix_preset.grid(row=0, column=0, padx=4, sticky="ew")

        self.ws_matrix_energy = ctk.CTkEntry(controls)
        self.ws_matrix_energy.insert(0, f"{self.target_energy:.0f}")
        self.ws_matrix_energy.grid(row=0, column=1, padx=4, sticky="ew")

        self.ws_matrix_events = ctk.CTkEntry(controls)
        self.ws_matrix_events.insert(0, "2000")
        self.ws_matrix_events.grid(row=0, column=2, padx=4, sticky="ew")

        ctk.CTkButton(
            controls,
            text="COMPUTE MATRIX",
            command=self._workspace_refresh_matrix,
            fg_color="#18566C",
            hover_color="#216F89",
        ).grid(row=0, column=3, padx=4, sticky="ew")

        host = ctk.CTkFrame(tab, fg_color="transparent")
        host.grid(row=1, column=0, sticky="nsew")
        self.workspace_matrix_figure, self.workspace_matrix_ax, self.workspace_matrix_canvas = (
            self._workspace_make_plot(host)
        )

    def _workspace_refresh_matrix(self):
        if self.workspace_matrix_canvas is None:
            return
        try:
            preset = self.ws_matrix_preset.get()
            energy = float(self.ws_matrix_energy.get())
            events = int(self.ws_matrix_events.get())
        except (ValueError, AttributeError):
            return

        matrix = detector_efficiency_matrix(
            events_per_detector=events,
            energy=energy,
            preset=preset,
            seed=9191,
            l1_threshold=self.l1_energy_threshold,
            met_threshold=self.met_trigger_threshold,
            higgs_window=self.higgs_window_gev,
            noise=self.detector_noise_enabled,
        )
        detectors = list(matrix.keys())
        metrics = ["l1_efficiency", "final_efficiency", "higgs_efficiency", "high_met_fraction"]
        labels = ["L1", "Saved", "Higgs", "High MET"]
        data = np.asarray([[matrix[d][m] for m in metrics] for d in detectors], dtype=float)

        ax = self.workspace_matrix_ax
        ax.clear()
        self._workspace_style_ax(ax)
        ax.set_title("Toy detector efficiency matrix (%)")
        image = ax.imshow(data, aspect="auto")
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels)
        ax.set_yticks(range(len(detectors)))
        ax.set_yticklabels(detectors)
        for row in range(data.shape[0]):
            for col in range(data.shape[1]):
                ax.text(
                    col, row, f"{data[row, col]:.1f}",
                    ha="center", va="center", color=self.TEXT, fontsize=8
                )
        self.workspace_matrix_canvas.draw_idle()

    def _workspace_build_mass_tab(self):
        tab = self.workspace_tabs.tab("MASS FIT")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        controls = ctk.CTkFrame(tab, fg_color="transparent")
        controls.grid(row=0, column=0, padx=10, pady=8, sticky="ew")
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_columnconfigure(1, weight=0)

        self.ws_mass_status = ctk.CTkLabel(
            controls,
            text="Fits reconstructed masses stored in accepted events.",
            text_color=self.MUTED,
            anchor="w",
        )
        self.ws_mass_status.grid(row=0, column=0, padx=4, sticky="ew")

        ctk.CTkButton(
            controls,
            text="REFIT",
            command=self._workspace_refresh_mass,
            width=90,
            fg_color="#18566C",
            hover_color="#216F89",
        ).grid(row=0, column=1, padx=4)

        host = ctk.CTkFrame(tab, fg_color="transparent")
        host.grid(row=1, column=0, sticky="nsew")
        self.workspace_mass_figure, self.workspace_mass_ax, self.workspace_mass_canvas = (
            self._workspace_make_plot(host)
        )

    def _workspace_refresh_mass(self):
        if self.workspace_mass_canvas is None:
            return
        masses = []
        with connect_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT particle_masses_json FROM accepted_events ORDER BY id DESC LIMIT 5000"
            ).fetchall()
        for (payload,) in rows:
            try:
                masses.extend(float(x) for x in json.loads(payload))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

        fit = fit_mass_spectrum(masses)
        ax = self.workspace_mass_ax
        ax.clear()
        self._workspace_style_ax(ax)
        ax.set_title("Mass spectrum with sideband diagnostic")
        ax.set_xlabel("Reconstructed mass (GeV)")
        ax.set_ylabel("Entries")

        if masses:
            ax.hist(masses, bins=60)
            ax.axvline(HIGGS_MASS_GEV, linestyle="--", linewidth=1.2)
            if fit["window_low"]:
                ax.axvspan(fit["window_low"], fit["window_high"], alpha=0.12)
            self.ws_mass_status.configure(
                text=(
                    f"Entries {fit['entries']} · peak mean {fit['peak_mean']:.2f} GeV · "
                    f"σ {fit['peak_sigma']:.2f} · excess {fit['estimated_excess']:.1f} · "
                    f"toy S/√B {fit['toy_s_over_sqrt_b']:.2f}"
                )
            )
        else:
            ax.text(
                0.5, 0.5, "No accepted-event masses in database",
                transform=ax.transAxes, ha="center", va="center", color=self.MUTED
            )
        self.workspace_mass_canvas.draw_idle()

    def _workspace_build_projects_tab(self):
        tab = self.workspace_tabs.tab("PROJECTS")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        name_row = ctk.CTkFrame(tab, fg_color="transparent")
        name_row.grid(row=0, column=0, columnspan=2, padx=10, pady=8, sticky="ew")
        name_row.grid_columnconfigure(0, weight=1)

        self.ws_project_name = ctk.CTkEntry(name_row, placeholder_text="Project name")
        self.ws_project_name.grid(row=0, column=0, padx=(0, 6), sticky="ew")
        self.ws_project_name.insert(0, "Hadron Analysis")

        ctk.CTkButton(
            name_row,
            text="SAVE CURRENT",
            command=self._workspace_save_project,
            width=110,
            fg_color="#18566C",
            hover_color="#216F89",
        ).grid(row=0, column=1, padx=3)

        ctk.CTkButton(
            name_row,
            text="EXPORT",
            command=self._workspace_export_project,
            width=80,
        ).grid(row=0, column=2, padx=3)

        ctk.CTkButton(
            name_row,
            text="IMPORT",
            command=self._workspace_import_project,
            width=80,
        ).grid(row=0, column=3, padx=(3, 0))

        left = self._panel(tab)
        left.grid(row=1, column=0, padx=(10, 5), pady=(0, 10), sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)

        self.workspace_project_list = tk.Listbox(
            left,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 10),
        )
        self.workspace_project_list.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.workspace_project_list.bind(
            "<<ListboxSelect>>", self._workspace_show_project
        )

        right = self._panel(tab)
        right.grid(row=1, column=1, padx=(5, 10), pady=(0, 10), sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        self.ws_project_detail = tk.Text(
            right,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        self.ws_project_detail.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.ws_project_detail.configure(state="disabled")
        self.workspace_project_rows = []

    def _workspace_refresh_projects(self):
        if self.workspace_project_list is None:
            return
        with connect_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, name, updated_at, payload_json
                FROM analysis_projects
                ORDER BY id DESC
                """
            ).fetchall()
        self.workspace_project_rows = rows
        self.workspace_project_list.delete(0, "end")
        for project_id, name, updated_at, _payload in rows:
            self.workspace_project_list.insert(
                "end",
                f"#{project_id:04d}  {name}  ·  {updated_at.replace('T', ' ')}"
            )
        if not rows:
            self.workspace_project_list.insert("end", "No saved analysis projects.")

    def _workspace_selected_project(self):
        if self.workspace_project_list is None or not self.workspace_project_rows:
            return None
        selected = self.workspace_project_list.curselection()
        if not selected:
            return None
        return self.workspace_project_rows[selected[0]]

    def _workspace_show_project(self, _event=None):
        row = self._workspace_selected_project()
        if row is None:
            return
        project_id, name, updated_at, payload_json = row
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            payload = {}
        text = (
            f"Project ID: {project_id}\n"
            f"Name:       {name}\n"
            f"Updated:    {updated_at}\n\n"
            f"Experiments: {payload.get('experiment_ids', [])}\n\n"
            f"Notes:\n{payload.get('notes', '')}\n"
        )
        self.ws_project_detail.configure(state="normal")
        self.ws_project_detail.delete("1.0", "end")
        self.ws_project_detail.insert("1.0", text)
        self.ws_project_detail.configure(state="disabled")

    def _workspace_current_project_payload(self):
        experiments = self._workspace_load_experiments(25)
        ids = [int(item["experiment_id"]) for item in experiments]
        name = self.ws_project_name.get().strip() if self.ws_project_name else "Hadron Analysis"
        return make_project_payload(
            name=name,
            experiment_ids=ids,
            notes="Saved from Hadron v0.8 Analysis Workspace.",
            workspace_state={
                "active_preset": self.active_preset,
                "target_energy": self.target_energy,
                "l1_energy_threshold": self.l1_energy_threshold,
                "met_trigger_threshold": self.met_trigger_threshold,
                "higgs_window_gev": self.higgs_window_gev,
            },
        )

    def _workspace_save_project(self):
        payload = self._workspace_current_project_payload()
        now = datetime.now().isoformat(timespec="seconds")
        with connect_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO analysis_projects (name, created_at, updated_at, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (payload["name"], now, now, json.dumps(payload)),
            )
            conn.commit()
        self._workspace_refresh_projects()
        self.log("PROJECT", f"Saved analysis project: {payload['name']}", "saved")

    def _workspace_export_project(self):
        row = self._workspace_selected_project()
        if row is None:
            payload = self._workspace_current_project_payload()
        else:
            payload = json.loads(row[3])
        path = filedialog.asksaveasfilename(
            title="Export Hadron analysis project",
            defaultextension=".hadron-project.json",
            filetypes=[("Hadron project", "*.hadron-project.json"), ("JSON", "*.json")],
            initialfile="hadron-analysis.hadron-project.json",
        )
        if not path:
            return
        export_project_file(path, payload)
        self.log("PROJECT", f"Project exported: {path}", "saved")

    def _workspace_import_project(self):
        path = filedialog.askopenfilename(
            title="Import Hadron analysis project",
            filetypes=[("Hadron project", "*.hadron-project.json"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            payload = import_project_file(path)
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            self.log("PROJECT", f"Import failed: {exc}", "discarded")
            return

        now = datetime.now().isoformat(timespec="seconds")
        with connect_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO analysis_projects (name, created_at, updated_at, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (payload["name"], now, now, json.dumps(payload)),
            )
            conn.commit()
        self._workspace_refresh_projects()
        self.log("PROJECT", f"Imported project: {payload['name']}", "saved")

    def _workspace_refresh_all(self):
        if self.workspace_window is None:
            return
        self._workspace_refresh_overlay()
        self._workspace_refresh_mass()
        self._workspace_refresh_projects()
        if self.workspace_status is not None:
            self.workspace_status.configure(
                text="Workspace refreshed · toy-simulation analysis only"
            )

    # ------------------------------------------------------------------
    # Signed GitHub updater
    # ------------------------------------------------------------------

    def _start_auto_update_check(self):
        if self.update_thread is not None:
            return
        self.update_thread = threading.Thread(
            target=self._update_check_worker,
            daemon=True,
        )
        self.update_thread.start()
        self._poll_update_messages()

    def _update_check_worker(self):
        try:
            info = check_for_update(timeout=15.0)
            self.update_messages.put(("complete", info))
        except Exception as exc:
            self.update_messages.put(("failed", str(exc)))

    def _poll_update_messages(self):
        processed = False
        while True:
            try:
                message = self.update_messages.get_nowait()
            except queue.Empty:
                break

            processed = True
            kind, payload = message
            if kind == "complete":
                self.update_info = payload
                if payload.get("update_available"):
                    self.log(
                        "UPDATE",
                        (
                            f"Hadron v{payload['latest_version']} is available "
                            f"from {RELEASE_REPOSITORY}."
                        ),
                        "saved",
                    )
                else:
                    self.log(
                        "UPDATE",
                        f"Hadron v{__version__} is current.",
                        "system",
                    )
                self._render_update_center()
            else:
                self.log(
                    "UPDATE",
                    f"Automatic update check unavailable: {payload}",
                    "system",
                )
                if self.update_status is not None:
                    self.update_status.configure(
                        text=f"Update check unavailable: {payload}"
                    )

            self.update_thread = None

        if (
            self.update_thread is not None
            and not processed
        ):
            self.after(250, self._poll_update_messages)

    def open_update_center(self):
        if self.update_window is not None and self.update_window.winfo_exists():
            self.update_window.focus()
            self._render_update_center()
            return

        self.update_window = ctk.CTkToplevel(self)
        self.update_window.title("Hadron — Signed Updates")
        self.update_window.geometry("760x500")
        self.update_window.minsize(650, 440)
        self.update_window.configure(fg_color=self.BG)
        self.update_window.grid_columnconfigure(0, weight=1)
        self.update_window.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(
            self.update_window,
            fg_color="transparent",
        )
        header.grid(
            row=0,
            column=0,
            padx=18,
            pady=(16, 8),
            sticky="ew",
        )
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="SIGNED UPDATE CENTER",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        actions = ctk.CTkFrame(
            header,
            fg_color="transparent",
        )
        actions.grid(row=0, column=1, sticky="e")

        ctk.CTkButton(
            actions,
            text="CHECK NOW",
            command=self._update_check_now_ui,
            width=100,
            height=30,
        ).grid(row=0, column=0, padx=(0, 4))

        ctk.CTkButton(
            actions,
            text="INSTALL UPDATE",
            command=self._install_update_ui,
            width=125,
            height=30,
            fg_color="#36503E",
            hover_color="#4A6D55",
        ).grid(row=0, column=1, padx=(4, 0))

        self.update_text = tk.Text(
            self.update_window,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=14,
            pady=12,
            font=("Consolas", 10),
        )
        self.update_text.grid(
            row=1,
            column=0,
            padx=18,
            pady=(0, 8),
            sticky="nsew",
        )
        self.update_text.configure(state="disabled")

        self.update_status = ctk.CTkLabel(
            self.update_window,
            text="",
            text_color=self.MUTED,
            anchor="w",
        )
        self.update_status.grid(
            row=2,
            column=0,
            padx=18,
            pady=(0, 16),
            sticky="ew",
        )

        self.update_window.protocol(
            "WM_DELETE_WINDOW",
            self._close_update_center,
        )
        self._render_update_center()

    def _close_update_center(self):
        if self.update_window is not None:
            try:
                self.update_window.destroy()
            except tk.TclError:
                pass
        self.update_window = None
        self.update_text = None
        self.update_status = None

    def _render_update_center(self):
        if self.update_text is None:
            return

        info = self.update_info
        lines = [
            "HADRON SIGNED UPDATE POLICY",
            "",
            f"Current version:  {__version__}",
            f"Repository:       {RELEASE_REPOSITORY}",
            "",
            "Updates are accepted only when:",
            "  1. GitHub release is newer and non-prerelease",
            "  2. manifest is Ed25519-signed by the pinned bxane key",
            "  3. signature identifies bxane / bxane-dev / bxane-dev/hadron",
            "  4. installer SHA-256 matches the signed manifest",
            "",
        ]

        if info is None:
            lines += [
                "Latest version:   not checked yet",
                "",
                "Use CHECK NOW, or wait for the automatic startup check.",
            ]
        else:
            lines += [
                f"Latest version:   {info['latest_version']}",
                (
                    "Status:           UPDATE AVAILABLE"
                    if info["update_available"]
                    else "Status:           UP TO DATE"
                ),
                f"Release page:     {info['release']['html_url']}",
            ]

        self.update_text.configure(state="normal")
        self.update_text.delete("1.0", "end")
        self.update_text.insert("1.0", "\n".join(lines))
        self.update_text.configure(state="disabled")

        if self.update_status is not None:
            if info is None:
                self.update_status.configure(
                    text="Signed update feed ready."
                )
            elif info["update_available"]:
                self.update_status.configure(
                    text=(
                        f"Signed Hadron v{info['latest_version']} "
                        "is available."
                    )
                )
            else:
                self.update_status.configure(
                    text="This installation is current."
                )

    def _update_check_now_ui(self):
        if self.update_thread is not None:
            if self.update_status is not None:
                self.update_status.configure(
                    text="Update check already running."
                )
            return
        if self.update_status is not None:
            self.update_status.configure(text="Checking GitHub Releases…")
        self._start_auto_update_check()

    def _updater_command(self):
        if getattr(sys, "frozen", False):
            updater = Path(sys.executable).with_name(
                "HadronUpdater.exe"
            )
            return [str(updater)]
        return [
            sys.executable,
            str(Path(__file__).with_name("hadron_updater_cli.py")),
        ]

    def _install_update_ui(self):
        if self.update_info is None:
            self._update_check_now_ui()
            return
        if not self.update_info.get("update_available"):
            if self.update_status is not None:
                self.update_status.configure(
                    text="No newer stable release is available."
                )
            return

        command = self._updater_command() + [
            "apply",
            "--dir",
            str(self.data_dir / "updates"),
        ]

        try:
            subprocess.Popen(
                command,
                close_fds=True,
            )
        except Exception as exc:
            if self.update_status is not None:
                self.update_status.configure(
                    text=f"Could not launch updater: {exc}"
                )
            return

        self.log(
            "UPDATE",
            "Verified updater launched; closing Hadron for installation.",
            "system",
        )
        self.after(300, self._on_close)

    # ------------------------------------------------------------------
    # System tools
    # ------------------------------------------------------------------

    def open_system_tools(self):
        if self.system_window is not None and self.system_window.winfo_exists():
            self.system_window.focus()
            self._refresh_system_diagnostics()
            return

        self.system_window = ctk.CTkToplevel(self)
        self.system_window.title("Hadron — System Tools")
        self.system_window.geometry("980x600")
        self.system_window.minsize(820, 520)
        self.system_window.configure(fg_color=self.BG)
        self.system_window.grid_columnconfigure(0, weight=1)
        self.system_window.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.system_window, fg_color="transparent")
        header.grid(row=0, column=0, padx=18, pady=(16, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="SYSTEM TOOLS",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        buttons = ctk.CTkFrame(header, fg_color="transparent")
        buttons.grid(row=0, column=1, sticky="e")

        ctk.CTkButton(
            buttons,
            text="BACKUP DB",
            command=self._backup_database_ui,
            width=90,
            height=30,
        ).grid(row=0, column=0, padx=3)

        ctk.CTkButton(
            buttons,
            text="RESTORE DB",
            command=self._restore_database_ui,
            width=90,
            height=30,
            fg_color="#71303A",
            hover_color="#923E4B",
        ).grid(row=0, column=1, padx=3)

        ctk.CTkButton(
            buttons,
            text="WORKSPACE OUT",
            command=self._export_workspace_bundle_ui,
            width=102,
            height=30,
            fg_color="#25495B",
            hover_color="#32647A",
        ).grid(row=0, column=2, padx=3)

        ctk.CTkButton(
            buttons,
            text="WORKSPACE IN",
            command=self._import_workspace_bundle_ui,
            width=102,
            height=30,
            fg_color="#5A3C2B",
            hover_color="#76503A",
        ).grid(row=0, column=3, padx=3)

        ctk.CTkButton(
            buttons,
            text="SUPPORT ZIP",
            command=self._create_support_bundle_ui,
            width=92,
            height=30,
            fg_color="#25495B",
            hover_color="#32647A",
        ).grid(row=0, column=4, padx=3)

        ctk.CTkButton(
            buttons,
            text="RELEASE",
            command=self.open_release_center,
            width=80,
            height=30,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=5, padx=3)

        ctk.CTkButton(
            buttons,
            text="CAPSULES",
            command=self.open_capsule_center,
            width=86,
            height=30,
            fg_color="#36503E",
            hover_color="#4A6D55",
        ).grid(row=0, column=6, padx=3)

        ctk.CTkButton(
            buttons,
            text="REGRESSION",
            command=self.open_regression_center,
            width=94,
            height=30,
            fg_color="#3A3157",
            hover_color="#514277",
        ).grid(row=0, column=7, padx=3)

        ctk.CTkButton(
            buttons,
            text="CAMPAIGNS",
            command=self.open_campaign_center,
            width=94,
            height=30,
            fg_color="#294D4B",
            hover_color="#376866",
        ).grid(row=0, column=8, padx=3)

        ctk.CTkButton(
            buttons,
            text="PIPELINES",
            command=self.open_pipeline_center,
            width=90,
            height=30,
            fg_color="#4B3D68",
            hover_color="#635189",
        ).grid(row=0, column=9, padx=3)

        ctk.CTkButton(
            buttons,
            text="UPDATES",
            command=self.open_update_center,
            width=82,
            height=30,
            fg_color="#36503E",
            hover_color="#4A6D55",
        ).grid(row=0, column=10, padx=3)

        ctk.CTkButton(
            buttons,
            text="REFRESH",
            command=self._refresh_system_diagnostics,
            width=80,
            height=30,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=0, column=11, padx=(3, 0))

        self.system_text = tk.Text(
            self.system_window,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=14,
            pady=12,
            font=("Consolas", 10),
        )
        self.system_text.grid(row=1, column=0, padx=18, pady=(0, 18), sticky="nsew")
        self.system_text.configure(state="disabled")

        self.system_window.protocol("WM_DELETE_WINDOW", self._close_system_tools)
        self._refresh_system_diagnostics()

    def _close_system_tools(self):
        if self.system_window is not None:
            try:
                self.system_window.destroy()
            except tk.TclError:
                pass
        self.system_window = None
        self.system_text = None

    def _refresh_system_diagnostics(self):
        if self.system_text is None:
            return
        env = environment_diagnostics()
        db = database_diagnostics(self.db_path)

        lines = [
            f"HADRON v{__version__} SYSTEM DIAGNOSTICS",
            "",
            f"App version:  {__version__}",
            f"Schema:       {SCHEMA_VERSION}",
            f"Python:       {env['python']}",
            f"Platform:     {env['platform']}",
            f"Machine:      {env['machine']}",
            f"Executable:   {env['executable']}",
            "",
            f"Database:     {db['path']}",
            f"Exists:       {db['exists']}",
            f"Size:         {db['size_bytes']:,} bytes",
            f"Integrity:    {db.get('integrity', 'unknown')}",
            "",
            "TABLE COUNTS",
        ]

        for table, count in (db.get("tables") or {}).items():
            lines.append(f"  {table:<20} {count}")

        benchmark_events = 2000
        benchmark_started = time.perf_counter()
        try:
            benchmark_rng = random.Random(7711)
            for _ in range(benchmark_events):
                self._generate_event_with_rng(
                    "ATLAS-SIM",
                    benchmark_rng,
                    6500.0,
                    "STANDARD",
                    noise_enabled=True,
                )
            benchmark_elapsed = max(time.perf_counter() - benchmark_started, 1e-9)
            benchmark_rate = benchmark_events / benchmark_elapsed
        except Exception:
            benchmark_rate = 0.0

        lines += [
            "",
            f"Engine bench: {benchmark_rate:,.0f} generated events/s",
            "",
            f"Data dir:     {self.data_dir}",
            f"Settings:     {self.settings_path}",
            f"Crash logs:   {self.data_dir / 'crashes'}",
            f"Session:      {self.session_path}",
            "",
            "LAST MIGRATION",
            f"  old schema: {self.migration_info.get('old_version')}",
            f"  new schema: {self.migration_info.get('new_version')}",
            f"  backup:     {self.migration_info.get('backup_path') or 'not required'}",
        ]

        self.system_text.configure(state="normal")
        self.system_text.delete("1.0", "end")
        self.system_text.insert("1.0", "\n".join(lines))
        self.system_text.configure(state="disabled")

    def open_release_center(self):
        if self.release_window is not None and self.release_window.winfo_exists():
            self.release_window.focus()
            return

        self.release_window = ctk.CTkToplevel(self)
        self.release_window.title("Hadron — Release Center")
        self.release_window.geometry("840x620")
        self.release_window.minsize(700, 520)
        self.release_window.configure(fg_color=self.BG)
        self.release_window.grid_columnconfigure(0, weight=1)
        self.release_window.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self.release_window,
            text="RELEASE CENTER",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=18, pady=(16, 4), sticky="w")

        self.release_status_label = ctk.CTkLabel(
            self.release_window,
            text=f"Installed: Hadron v{__version__} · no manifest loaded",
            text_color=self.MUTED,
            anchor="w",
        )
        self.release_status_label.grid(
            row=1, column=0, padx=18, pady=(0, 8), sticky="ew"
        )

        panel = self._panel(self.release_window)
        panel.grid(row=2, column=0, padx=18, pady=(0, 10), sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        controls = ctk.CTkFrame(panel, fg_color="transparent")
        controls.grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")
        controls.grid_columnconfigure((0, 1, 2, 3), weight=1)

        ctk.CTkButton(
            controls,
            text="LOAD MANIFEST",
            command=self._release_load_manifest,
            height=32,
            fg_color="#25495B",
            hover_color="#32647A",
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            controls,
            text="VERIFY ARTIFACTS",
            command=self._release_verify_manifest,
            height=32,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=1, padx=4, sticky="ew")

        ctk.CTkButton(
            controls,
            text="LOAD SIGNATURE",
            command=self._release_load_signature,
            height=32,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=2, padx=4, sticky="ew")

        ctk.CTkButton(
            controls,
            text="LOAD PUBLIC KEY",
            command=self._release_load_public_key,
            height=32,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=3, padx=(4, 0), sticky="ew")

        ctk.CTkButton(
            controls,
            text="VERIFY SIGNATURE",
            command=self._release_verify_signature,
            height=32,
            fg_color="#3A3157",
            hover_color="#514277",
        ).grid(row=1, column=0, padx=(0, 4), pady=(6, 0), sticky="ew")

        ctk.CTkButton(
            controls,
            text="AUDIT CHAIN",
            command=self._open_audit_viewer,
            height=32,
            fg_color="#3A3157",
            hover_color="#514277",
        ).grid(row=1, column=1, padx=4, pady=(6, 0), sticky="ew")

        ctk.CTkButton(
            controls,
            text="EXPORT INTEGRITY",
            command=self._release_export_integrity,
            height=32,
            fg_color="#5A4524",
            hover_color="#765D31",
        ).grid(row=1, column=2, padx=4, pady=(6, 0), sticky="ew")

        ctk.CTkButton(
            controls,
            text="VERIFY TRUSTED",
            command=self._release_verify_trusted_signature,
            height=32,
            fg_color="#36503E",
            hover_color="#4A6D55",
        ).grid(row=1, column=3, padx=4, pady=(6, 0), sticky="ew")

        ctk.CTkButton(
            controls,
            text="EXPORT VERIFY JSON",
            command=self._release_export_verification,
            height=32,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=1, column=4, padx=(4, 0), pady=(6, 0), sticky="ew")

        self.release_text = tk.Text(
            panel,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=14,
            pady=12,
            font=("Consolas", 10),
        )
        self.release_text.grid(
            row=1, column=0, padx=10, pady=(4, 10), sticky="nsew"
        )
        self.release_text.configure(state="disabled")

        self.release_window.protocol(
            "WM_DELETE_WINDOW",
            self._close_release_center,
        )
        self._release_write_text(
            "Hadron Release Center\n\n"
            "Load a Hadron release manifest JSON to compare versions and "
            "verify local files with SHA-256 checksums.\n\n"
            "This is an offline verifier. It does not contact update servers."
        )

    def _close_release_center(self):
        self._close_audit_viewer()
        if self.release_window is not None:
            try:
                self.release_window.destroy()
            except tk.TclError:
                pass
        self.release_window = None
        self.release_text = None
        self.release_manifest_path = None
        self.release_manifest_data = None

    def _release_write_text(self, value):
        if self.release_text is None:
            return
        self.release_text.configure(state="normal")
        self.release_text.delete("1.0", "end")
        self.release_text.insert("1.0", value)
        self.release_text.configure(state="disabled")

    def _release_load_manifest(self):
        path = filedialog.askopenfilename(
            title="Load Hadron release manifest",
            filetypes=[("Hadron release manifest", "*-manifest.json"), ("JSON", "*.json")],
        )
        if not path:
            return

        try:
            manifest = load_release_manifest(path)
        except Exception as exc:
            self._release_write_text(f"Manifest load failed:\n{exc}")
            return

        self.release_manifest_path = Path(path)
        self.release_manifest_data = manifest

        from hadron_release import compare_versions
        comparison = compare_versions(__version__, manifest["version"])
        status = release_status_text(comparison)

        self.release_status_label.configure(
            text=(
                f"Installed v{__version__} · manifest v{manifest['version']} · {status}"
            )
        )

        lines = [
            f"Manifest: {path}",
            f"Manifest version: {manifest.get('manifest_version', 1)}",
            f"Release version: {manifest['version']}",
            f"Status: {status}",
            "",
            "FILES",
        ]
        for item in manifest["files"]:
            lines.append(
                f"  {item['name']:<34} {item['size_bytes']:>12,} bytes  "
                f"{item['sha256'][:16]}…"
            )
        self._release_write_text("\n".join(lines))

    def _release_verify_manifest(self):
        if self.release_manifest_path is None:
            self._release_write_text("Load a release manifest first.")
            return

        report = verify_release_manifest(self.release_manifest_path)
        self.release_verification_report = report

        lines = [
            f"Release verification · v{report['manifest']['version']}",
            f"Base directory: {report['base_dir']}",
            f"Overall: {'VERIFIED' if report['ok'] else 'FAILED'}",
            "",
        ]
        for check in report["checks"]:
            marker = "OK" if check["ok"] else "FAIL"
            lines.append(f"[{marker}] {check['name']}")
            if not check["ok"]:
                if not check["exists"]:
                    lines.append("       file missing")
                else:
                    lines.append(
                        f"       expected {check['expected_sha256']}"
                    )
                    lines.append(
                        f"       actual   {check['actual_sha256']}"
                    )

        self._release_write_text("\n".join(lines))
        self.release_status_label.configure(
            text=(
                f"Manifest v{report['manifest']['version']} · "
                f"{'SHA-256 VERIFIED' if report['ok'] else 'VERIFICATION FAILED'}"
            )
        )

    def _release_export_verification(self):
        report = getattr(self, "release_verification_report", None)
        if report is None:
            self._release_verify_manifest()
            report = getattr(self, "release_verification_report", None)
        if report is None:
            return

        path = filedialog.asksaveasfilename(
            title="Export Hadron release verification",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile="Hadron-release-verification.json",
        )
        if not path:
            return

        Path(path).write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )
        self.log(
            "RELEASE",
            f"Release verification exported: {path}",
            "saved" if report["ok"] else "background",
        )

    def _release_load_signature(self):
        path = filedialog.askopenfilename(
            title="Load Hadron release signature",
            filetypes=[("Hadron signature", "*.signature.json"), ("JSON", "*.json")],
        )
        if path:
            self.release_signature_path = Path(path)
            self.release_status_label.configure(
                text=f"Signature loaded: {Path(path).name}"
            )

    def _release_load_public_key(self):
        path = filedialog.askopenfilename(
            title="Load Ed25519 public key",
            filetypes=[("PEM public key", "*.pem"), ("All files", "*.*")],
        )
        if path:
            self.release_public_key_path = Path(path)
            try:
                key_id = add_trusted_key(
                    self.db_path,
                    label=Path(path).stem,
                    public_key_path=path,
                )
                self.release_status_label.configure(
                    text=f"Public key loaded and trusted as key #{key_id}: {Path(path).name}"
                )
            except Exception as exc:
                self.release_status_label.configure(
                    text=f"Public key load failed: {exc}"
                )

    def _release_verify_trusted_signature(self):
        if self.release_manifest_path is None:
            self._release_write_text("Load a release manifest first.")
            return
        if self.release_signature_path is None:
            self._release_write_text("Load a release signature first.")
            return

        report = verify_signature_with_trust_store(
            self.db_path,
            manifest_path=self.release_manifest_path,
            signature_path=self.release_signature_path,
        )

        if report.get("ok"):
            trusted = report["trusted_key"]
            self.release_signature_report = report["verification"]
            self._release_write_text(
                "TRUST-STORE SIGNATURE VERIFIED\n\n"
                f"Key #{trusted['id']}: {trusted['label']}\n"
                f"Fingerprint:\n{trusted['fingerprint_sha256']}"
            )
            self.release_status_label.configure(
                text=f"Signature verified with trusted key #{trusted['id']}"
            )
        else:
            self._release_write_text(
                "TRUST-STORE SIGNATURE FAILED\n\n"
                + report.get("reason", "No trusted key verified the signature.")
            )
            self.release_status_label.configure(
                text="Trusted signature verification FAILED"
            )

    def _release_verify_signature(self):
        if self.release_manifest_path is None:
            self._release_write_text("Load a release manifest first.")
            return
        if self.release_signature_path is None:
            self._release_write_text("Load a release signature first.")
            return
        if self.release_public_key_path is None:
            self._release_write_text("Load an Ed25519 public key first.")
            return

        try:
            report = verify_release_signature(
                self.release_manifest_path,
                self.release_signature_path,
                self.release_public_key_path,
            )
        except Exception as exc:
            self._release_write_text(f"Signature verification failed:\n{exc}")
            return

        self.release_signature_report = report
        lines = [
            "ED25519 RELEASE SIGNATURE",
            "",
            f"Overall: {'VERIFIED' if report['ok'] else 'FAILED'}",
            f"Manifest hash: {'OK' if report['manifest_hash_ok'] else 'FAIL'}",
            f"Public key fingerprint: {'OK' if report['fingerprint_ok'] else 'FAIL'}",
            f"Cryptographic signature: {'OK' if report['signature_ok'] else 'FAIL'}",
            "",
            f"Fingerprint SHA-256:\n{report['public_key_fingerprint_sha256']}",
        ]
        self._release_write_text("\n".join(lines))
        self.release_status_label.configure(
            text="Ed25519 signature VERIFIED" if report["ok"]
            else "Ed25519 signature FAILED"
        )

    def _release_export_integrity(self):
        path = filedialog.asksaveasfilename(
            title="Export Hadron integrity report",
            defaultextension=".integrity.json",
            filetypes=[("Hadron integrity report", "*.integrity.json"), ("JSON", "*.json")],
            initialfile="Hadron-integrity-report.integrity.json",
        )
        if not path:
            return

        write_integrity_report(
            path,
            release_verification=getattr(
                self, "release_verification_report", None
            ),
            release_signature=self.release_signature_report,
            study=None,
            db_path=self.db_path,
        )
        self.log(
            "RELEASE",
            f"Integrity report exported: {path}",
            "saved",
        )

    def _open_audit_viewer(self):
        if self.audit_window is not None and self.audit_window.winfo_exists():
            self.audit_window.focus()
            self._refresh_audit_viewer()
            return

        self.audit_window = ctk.CTkToplevel(self)
        self.audit_window.title("Hadron — Audit Chain")
        self.audit_window.geometry("900x640")
        self.audit_window.minsize(700, 520)
        self.audit_window.configure(fg_color=self.BG)
        self.audit_window.grid_columnconfigure(0, weight=1)
        self.audit_window.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self.audit_window,
            text="HASH-CHAINED AUDIT LOG",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=18, pady=(16, 8), sticky="w")

        self.audit_text = tk.Text(
            self.audit_window,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=14,
            pady=12,
            font=("Consolas", 10),
        )
        self.audit_text.grid(
            row=1, column=0, padx=18, pady=(0, 8), sticky="nsew"
        )

        ctk.CTkButton(
            self.audit_window,
            text="REFRESH / VERIFY CHAIN",
            command=self._refresh_audit_viewer,
            height=32,
        ).grid(row=2, column=0, padx=18, pady=(0, 16), sticky="ew")

        self.audit_window.protocol(
            "WM_DELETE_WINDOW",
            self._close_audit_viewer,
        )
        self._refresh_audit_viewer()

    def _close_audit_viewer(self):
        if self.audit_window is not None:
            try:
                self.audit_window.destroy()
            except tk.TclError:
                pass
        self.audit_window = None
        self.audit_text = None

    def _refresh_audit_viewer(self):
        if self.audit_text is None:
            return
        chain = verify_audit_chain(self.db_path)
        rows = list_audit(self.db_path, limit=150)

        lines = [
            f"Chain status: {'VALID' if chain['ok'] else 'BROKEN'}",
            f"Entries: {chain['entries']}",
            f"Head hash: {chain['head_hash']}",
            "",
        ]
        for row in rows:
            lines.append(
                f"#{row['id']:05d}  {row['timestamp']}  "
                f"{row['category']}/{row['action']}  "
                f"{row['entity_type']}:{row['entity_id'] or '-'}"
            )
            lines.append(f"  hash {row['entry_hash']}")
        self.audit_text.delete("1.0", "end")
        self.audit_text.insert("1.0", "\n".join(lines))

    def open_capsule_center(self):
        if self.capsule_window is not None and self.capsule_window.winfo_exists():
            self.capsule_window.focus()
            self._refresh_capsule_center()
            return

        self.capsule_window = ctk.CTkToplevel(self)
        self.capsule_window.title("Hadron — Capsule & Trust Center")
        self.capsule_window.geometry("1260x700")
        self.capsule_window.minsize(980, 580)
        self.capsule_window.configure(fg_color=self.BG)
        self.capsule_window.grid_columnconfigure((0, 1, 2), weight=1)
        self.capsule_window.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self.capsule_window,
            text="CAPSULE & TRUST CENTER",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, columnspan=3, padx=18, pady=(16, 8), sticky="w")

        left = self._panel(self.capsule_window)
        left.grid(row=1, column=0, padx=(18, 6), pady=(0, 12), sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            left,
            text="CAPSULE REGISTRY",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, padx=10, pady=(10, 4), sticky="w")

        self.capsule_list = tk.Listbox(
            left,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 9),
        )
        self.capsule_list.grid(row=1, column=0, padx=10, pady=4, sticky="nsew")

        capsule_buttons = ctk.CTkFrame(left, fg_color="transparent")
        capsule_buttons.grid(row=2, column=0, padx=10, pady=(4, 10), sticky="ew")
        capsule_buttons.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            capsule_buttons,
            text="VERIFY CAPSULE FILE",
            command=self._verify_external_capsule_ui,
            height=30,
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            capsule_buttons,
            text="REFRESH",
            command=self._refresh_capsule_center,
            height=30,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        right = self._panel(self.capsule_window)
        right.grid(row=1, column=1, padx=(6, 18), pady=(0, 12), sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            right,
            text="TRUSTED PUBLIC KEYS",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, padx=10, pady=(10, 4), sticky="w")

        self.trust_list = tk.Listbox(
            right,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 9),
        )
        self.trust_list.grid(row=1, column=0, padx=10, pady=4, sticky="nsew")

        trust_buttons = ctk.CTkFrame(right, fg_color="transparent")
        trust_buttons.grid(row=2, column=0, padx=10, pady=(4, 10), sticky="ew")
        trust_buttons.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            trust_buttons,
            text="IMPORT PUBLIC KEY",
            command=self._import_trusted_key_ui,
            height=30,
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            trust_buttons,
            text="REMOVE TRUST",
            command=self._remove_selected_trusted_key,
            height=30,
            fg_color="#71303A",
            hover_color="#923E4B",
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        reproduce = self._panel(self.capsule_window)
        reproduce.grid(
            row=1, column=2, padx=(6, 18), pady=(0, 12), sticky="nsew"
        )
        reproduce.grid_columnconfigure(0, weight=1)
        reproduce.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            reproduce,
            text="REPRODUCTION CHECKS",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, padx=10, pady=(10, 4), sticky="w")

        self.reproduction_list = tk.Listbox(
            reproduce,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 9),
        )
        self.reproduction_list.grid(
            row=1, column=0, padx=10, pady=4, sticky="nsew"
        )

        reproduce_buttons = ctk.CTkFrame(
            reproduce,
            fg_color="transparent",
        )
        reproduce_buttons.grid(
            row=2, column=0, padx=10, pady=(4, 10), sticky="ew"
        )
        reproduce_buttons.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            reproduce_buttons,
            text="REPRODUCE CAPSULE",
            command=self._reproduce_external_capsule_ui,
            height=30,
            fg_color="#36503E",
            hover_color="#4A6D55",
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            reproduce_buttons,
            text="RESTORE STUDY",
            command=self._restore_external_capsule_ui,
            height=30,
            fg_color="#3A3157",
            hover_color="#514277",
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.capsule_status = ctk.CTkLabel(
            self.capsule_window,
            text="",
            text_color=self.MUTED,
            anchor="w",
        )
        self.capsule_status.grid(
            row=2, column=0, columnspan=3, padx=18, pady=(0, 16), sticky="ew"
        )

        self.capsule_window.protocol(
            "WM_DELETE_WINDOW",
            self._close_capsule_center,
        )
        self._refresh_capsule_center()

    def _close_capsule_center(self):
        if self.capsule_window is not None:
            try:
                self.capsule_window.destroy()
            except tk.TclError:
                pass
        self.capsule_window = None
        self.capsule_list = None
        self.trust_list = None
        self.capsule_rows = []
        self.trust_rows = []
        self.reproduction_list = None
        self.reproduction_rows = []

    def _refresh_capsule_center(self):
        self.capsule_rows = list_capsules(self.db_path)
        self.trust_rows = list_trusted_keys(self.db_path)
        self.reproduction_rows = list_reproduction_checks(self.db_path)

        if self.capsule_list is not None:
            self.capsule_list.delete(0, "end")
            if not self.capsule_rows:
                self.capsule_list.insert("end", "No registered capsules.")
            for item in self.capsule_rows:
                marker = "OK" if item["verified"] else "FAIL"
                self.capsule_list.insert(
                    "end",
                    (
                        f"#{item['id']:04d} [{marker}] study {item['study_id']} · "
                        f"{item['capsule_name']} · {item['capsule_sha256'][:12]}…"
                    ),
                )

        if self.trust_list is not None:
            self.trust_list.delete(0, "end")
            if not self.trust_rows:
                self.trust_list.insert("end", "No trusted public keys.")
            for item in self.trust_rows:
                self.trust_list.insert(
                    "end",
                    (
                        f"#{item['id']:04d} {item['label']} · "
                        f"{item['fingerprint_sha256'][:20]}…"
                    ),
                )

        if self.reproduction_list is not None:
            self.reproduction_list.delete(0, "end")
            if not self.reproduction_rows:
                self.reproduction_list.insert(
                    "end", "No reproduction checks."
                )
            for item in self.reproduction_rows:
                marker = "MATCH" if item["exact_reproduction"] else "DIFF"
                self.reproduction_list.insert(
                    "end",
                    (
                        f"#{item['id']:04d} [{marker}] "
                        f"{item['jobs_matching']}/{item['jobs_compared']} · "
                        f"{(item['source_study_name'] or 'study')[:22]}"
                    ),
                )

    def _reproduce_external_capsule_ui(self):
        path = filedialog.askopenfilename(
            title="Reproduce Hadron capsule",
            filetypes=[
                ("Hadron capsule", "*.hadron-capsule.zip"),
                ("ZIP archive", "*.zip"),
            ],
        )
        if not path:
            return

        self.capsule_status.configure(
            text="Running deterministic capsule reproduction…"
        )
        try:
            report = reproduce_capsule_to_database(
                self.db_path,
                path,
            )
        except Exception as exc:
            self.capsule_status.configure(
                text=f"Reproduction failed: {exc}"
            )
            return

        self.capsule_status.configure(
            text=(
                f"Reproduction #{report['database_check_id']}: "
                f"{'EXACT MATCH' if report['exact_reproduction'] else 'DIVERGENCE'} · "
                f"{report['jobs_matching']}/{report['jobs_compared']} jobs"
            )
        )
        self._refresh_capsule_center()

    def _restore_external_capsule_ui(self):
        path = filedialog.askopenfilename(
            title="Restore study from Hadron capsule",
            filetypes=[
                ("Hadron capsule", "*.hadron-capsule.zip"),
                ("ZIP archive", "*.zip"),
            ],
        )
        if not path:
            return
        try:
            study_id = restore_capsule_study(
                self.db_path,
                path,
            )
            self.capsule_status.configure(
                text=f"Capsule restored as local study #{study_id}."
            )
            self._load_study_queue()
        except Exception as exc:
            self.capsule_status.configure(
                text=f"Capsule restore failed: {exc}"
            )

    def _verify_external_capsule_ui(self):
        path = filedialog.askopenfilename(
            title="Verify Hadron capsule",
            filetypes=[
                ("Hadron capsule", "*.hadron-capsule.zip"),
                ("ZIP archive", "*.zip"),
            ],
        )
        if not path:
            return

        try:
            report = inspect_capsule(path)
            registry_id = register_capsule(
                self.db_path,
                study_id=report["manifest"].get("study_id"),
                capsule_name=Path(path).name,
                capsule_sha256=report["capsule_sha256"],
                verified=report["ok"],
                manifest=report["manifest"],
            )
            self.capsule_status.configure(
                text=(
                    f"Capsule #{registry_id}: "
                    f"{'VERIFIED' if report['ok'] else 'FAILED'} · "
                    f"{report['capsule_sha256'][:24]}…"
                )
            )
            self._refresh_capsule_center()
        except Exception as exc:
            self.capsule_status.configure(
                text=f"Capsule verification failed: {exc}"
            )

    def _import_trusted_key_ui(self):
        path = filedialog.askopenfilename(
            title="Import trusted Ed25519 public key",
            filetypes=[("PEM public key", "*.pem"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            key_id = add_trusted_key(
                self.db_path,
                label=Path(path).stem,
                public_key_path=path,
            )
            self.capsule_status.configure(
                text=f"Trusted public key #{key_id} imported."
            )
            self._refresh_capsule_center()
        except Exception as exc:
            self.capsule_status.configure(text=f"Key import failed: {exc}")

    def _remove_selected_trusted_key(self):
        if self.trust_list is None or not self.trust_rows:
            return
        selected = self.trust_list.curselection()
        if not selected:
            return

        item = self.trust_rows[selected[0]]
        remove_trusted_key(self.db_path, item["id"])
        self.capsule_status.configure(
            text=f"Trust removed from key #{item['id']}."
        )
        self._refresh_capsule_center()

    def open_regression_center(self):
        if (
            self.regression_window is not None
            and self.regression_window.winfo_exists()
        ):
            self.regression_window.focus()
            self._refresh_regression_center()
            return

        self.regression_window = ctk.CTkToplevel(self)
        self.regression_window.title("Hadron — Regression Center")
        self.regression_window.geometry("1050x680")
        self.regression_window.minsize(820, 560)
        self.regression_window.configure(fg_color=self.BG)
        self.regression_window.grid_columnconfigure(0, weight=1)
        self.regression_window.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self.regression_window,
            text="CAPSULE REGRESSION CENTER",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=18, pady=(16, 4), sticky="w")

        controls = ctk.CTkFrame(
            self.regression_window,
            fg_color="transparent",
        )
        controls.grid(row=1, column=0, padx=18, pady=(0, 8), sticky="ew")
        controls.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            controls,
            text="RUN CAPSULE DIRECTORY",
            command=self._run_regression_directory_ui,
            height=32,
            fg_color="#3A3157",
            hover_color="#514277",
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            controls,
            text="REFRESH HISTORY",
            command=self._refresh_regression_center,
            height=32,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.regression_status = ctk.CTkLabel(
            controls,
            text="Ready",
            text_color=self.MUTED,
            anchor="w",
        )
        self.regression_status.grid(
            row=1, column=0, columnspan=2, pady=(6, 0), sticky="ew"
        )

        body = ctk.CTkFrame(
            self.regression_window,
            fg_color="transparent",
        )
        body.grid(row=2, column=0, padx=18, pady=(0, 18), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        left = self._panel(body)
        left.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)

        self.regression_list = tk.Listbox(
            left,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 9),
        )
        self.regression_list.grid(
            row=0, column=0, padx=10, pady=10, sticky="nsew"
        )
        self.regression_list.bind(
            "<<ListboxSelect>>",
            self._show_regression_detail,
        )

        right = self._panel(body)
        right.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        self.regression_detail = tk.Text(
            right,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=14,
            pady=12,
            font=("Consolas", 10),
        )
        self.regression_detail.grid(
            row=0, column=0, padx=10, pady=10, sticky="nsew"
        )
        self.regression_detail.configure(state="disabled")

        self.regression_window.protocol(
            "WM_DELETE_WINDOW",
            self._close_regression_center,
        )
        self._refresh_regression_center()
        self._poll_regression_messages()

    def _close_regression_center(self):
        if self.regression_window is not None:
            try:
                self.regression_window.destroy()
            except tk.TclError:
                pass
        self.regression_window = None
        self.regression_list = None
        self.regression_detail = None
        self.regression_rows = []

    def _refresh_regression_center(self):
        self.regression_rows = list_regression_runs(self.db_path)
        if self.regression_list is None:
            return

        self.regression_list.delete(0, "end")
        if not self.regression_rows:
            self.regression_list.insert("end", "No regression runs.")
            return

        for item in self.regression_rows:
            marker = "PASS" if item["all_passed"] else "FAIL"
            self.regression_list.insert(
                "end",
                (
                    f"#{item['id']:04d} [{marker}] "
                    f"{item['passed']}/{item['capsules_checked']} · "
                    f"{item['created_at'].replace('T', ' ')}"
                ),
            )

    def _show_regression_detail(self, _event=None):
        if (
            self.regression_list is None
            or self.regression_detail is None
            or not self.regression_rows
        ):
            return
        selected = self.regression_list.curselection()
        if not selected:
            return

        item = self.regression_rows[selected[0]]
        report = item["report"]

        lines = [
            f"Regression run #{item['id']}",
            f"Mode:       {item['mode']}",
            f"Created:    {item['created_at']}",
            f"Status:     {'PASS' if item['all_passed'] else 'FAIL'}",
            f"Checked:    {item['capsules_checked']}",
            f"Passed:     {item['passed']}",
            f"Failed:     {item['failed']}",
            "",
            "CAPSULE MATRIX",
        ]

        for row in report.get("rows", []):
            lines.append(
                f"[{row.get('status', '?'):<10}] "
                f"{row.get('capsule_name', '?')} · "
                f"{row.get('study_name', '')}"
            )
            if row.get("failure_fields"):
                lines.append(
                    "  differing fields: "
                    + ", ".join(row["failure_fields"])
                )
            if row.get("error"):
                lines.append("  error: " + str(row["error"]))

        failure_fields = report.get("failure_field_counts") or {}
        if failure_fields:
            lines += ["", "FAILURE FIELD COUNTS"]
            for field, count in failure_fields.items():
                lines.append(f"  {field:<32} {count}")

        self.regression_detail.configure(state="normal")
        self.regression_detail.delete("1.0", "end")
        self.regression_detail.insert("1.0", "\n".join(lines))
        self.regression_detail.configure(state="disabled")

    def _run_regression_directory_ui(self):
        if self.regression_thread is not None:
            self.regression_status.configure(
                text="A regression run is already active."
            )
            return

        directory = filedialog.askdirectory(
            title="Select directory containing Hadron capsules"
        )
        if not directory:
            return

        self.regression_status.configure(
            text=f"Running capsule regression: {directory}"
        )
        self.regression_thread = threading.Thread(
            target=self._regression_worker,
            args=(directory,),
            daemon=True,
        )
        self.regression_thread.start()

    def _regression_worker(self, directory):
        try:
            report = run_capsule_regression(
                [directory],
                workers_override=1,
            )
            self.regression_messages.put(
                ("complete", directory, report)
            )
        except Exception as exc:
            self.regression_messages.put(
                ("failed", directory, str(exc))
            )

    def _poll_regression_messages(self):
        while True:
            try:
                message = self.regression_messages.get_nowait()
            except queue.Empty:
                break

            kind, directory, payload = message
            if kind == "complete":
                run_id = record_regression_run(
                    self.db_path,
                    payload,
                    mode="gui-directory",
                )
                self.regression_status.configure(
                    text=(
                        f"Regression #{run_id}: "
                        f"{'PASS' if payload['all_passed'] else 'FAIL'} · "
                        f"{payload['passed']}/{payload['capsules_checked']} capsules"
                    )
                )
                self.log(
                    "REGRESSION",
                    (
                        f"Regression #{run_id}: "
                        f"{payload['passed']}/{payload['capsules_checked']} passed."
                    ),
                    "saved" if payload["all_passed"] else "discarded",
                )
            else:
                self.regression_status.configure(
                    text=f"Regression failed: {payload}"
                )
                self.log(
                    "REGRESSION",
                    f"Regression failed for {directory}: {payload}",
                    "discarded",
                )

            self.regression_thread = None
            self._refresh_regression_center()

        if self.regression_window is not None:
            try:
                if self.regression_window.winfo_exists():
                    self.after(250, self._poll_regression_messages)
            except tk.TclError:
                pass

    def open_campaign_center(self):
        if self.campaign_window is not None and self.campaign_window.winfo_exists():
            self.campaign_window.focus()
            self._refresh_campaign_center()
            return

        self.campaign_window = ctk.CTkToplevel(self)
        self.campaign_window.title("Hadron — Campaign Center")
        self.campaign_window.geometry("1180x760")
        self.campaign_window.minsize(920, 620)
        self.campaign_window.configure(fg_color=self.BG)
        self.campaign_window.grid_columnconfigure(0, weight=1)
        self.campaign_window.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self.campaign_window,
            text="CAMPAIGN CENTER · v2.1",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=18, pady=(16, 4), sticky="w")

        ctk.CTkLabel(
            self.campaign_window,
            text=(
                "Group studies, capsules, reproduction checks and regression runs "
                "into one portable research workspace."
            ),
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=1, column=0, padx=18, pady=(0, 8), sticky="w")

        body = ctk.CTkFrame(self.campaign_window, fg_color="transparent")
        body.grid(row=2, column=0, padx=18, pady=(0, 10), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        left = self._panel(body)
        left.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            left,
            text="CAMPAIGNS",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, padx=10, pady=(10, 4), sticky="w")

        self.campaign_list = tk.Listbox(
            left,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            selectforeground=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 9),
        )
        self.campaign_list.grid(
            row=1, column=0, padx=10, pady=4, sticky="nsew"
        )
        self.campaign_list.bind(
            "<<ListboxSelect>>",
            self._show_campaign_detail,
        )

        create_panel = ctk.CTkFrame(left, fg_color="transparent")
        create_panel.grid(
            row=2, column=0, padx=10, pady=(4, 6), sticky="ew"
        )
        create_panel.grid_columnconfigure(0, weight=1)

        self.campaign_name_entry = ctk.CTkEntry(create_panel)
        self.campaign_name_entry.insert(0, "Hadron Campaign")
        self.campaign_name_entry.grid(
            row=0, column=0, padx=(0, 4), sticky="ew"
        )

        ctk.CTkButton(
            create_panel,
            text="CREATE",
            command=self._create_campaign_ui,
            width=82,
            height=30,
            fg_color="#294D4B",
            hover_color="#376866",
        ).grid(row=0, column=1, sticky="e")

        left_actions = ctk.CTkFrame(left, fg_color="transparent")
        left_actions.grid(
            row=3, column=0, padx=10, pady=(0, 10), sticky="ew"
        )
        left_actions.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkButton(
            left_actions,
            text="EXPORT",
            command=self._export_campaign_ui,
            height=30,
            fg_color="#25495B",
            hover_color="#32647A",
        ).grid(row=0, column=0, padx=(0, 3), sticky="ew")

        ctk.CTkButton(
            left_actions,
            text="IMPORT",
            command=self._import_campaign_ui,
            height=30,
            fg_color="#5A3C2B",
            hover_color="#76503A",
        ).grid(row=0, column=1, padx=3, sticky="ew")

        ctk.CTkButton(
            left_actions,
            text="ARCHIVE",
            command=self._archive_selected_campaign,
            height=30,
            fg_color="#71303A",
            hover_color="#923E4B",
        ).grid(row=0, column=2, padx=(3, 0), sticky="ew")

        execution = ctk.CTkFrame(left, fg_color="transparent")
        execution.grid(
            row=4, column=0, padx=10, pady=(0, 10), sticky="ew"
        )
        execution.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkButton(
            execution,
            text="HEALTH",
            command=self._campaign_health_ui,
            height=30,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=0, padx=(0, 3), sticky="ew")

        ctk.CTkButton(
            execution,
            text="SAVE REF",
            command=self._campaign_save_reference_ui,
            height=30,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=1, padx=3, sticky="ew")

        ctk.CTkButton(
            execution,
            text="CHECK REF",
            command=self._campaign_check_reference_ui,
            height=30,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=2, padx=(3, 0), sticky="ew")

        ctk.CTkButton(
            execution,
            text="RUN CAMPAIGN",
            command=self._campaign_run_ui,
            height=30,
            fg_color="#294D4B",
            hover_color="#376866",
        ).grid(row=1, column=0, columnspan=2, padx=(0, 3), pady=(6, 0), sticky="ew")

        ctk.CTkButton(
            execution,
            text="REPORT ZIP",
            command=self._campaign_report_ui,
            height=30,
            fg_color="#5A4524",
            hover_color="#765D31",
        ).grid(row=1, column=2, padx=(3, 0), pady=(6, 0), sticky="ew")

        right = self._panel(body)
        right.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=2)
        right.grid_rowconfigure(2, weight=1)

        self.campaign_detail = tk.Text(
            right,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=14,
            pady=12,
            font=("Consolas", 10),
        )
        self.campaign_detail.grid(
            row=0, column=0, padx=10, pady=(10, 5), sticky="nsew"
        )
        self.campaign_detail.configure(state="disabled")

        member_controls = ctk.CTkFrame(right, fg_color="transparent")
        member_controls.grid(
            row=1, column=0, padx=10, pady=5, sticky="ew"
        )
        member_controls.grid_columnconfigure((0, 1, 2), weight=1)

        self.campaign_member_type = ctk.CTkOptionMenu(
            member_controls,
            values=[
                "study",
                "capsule",
                "regression",
                "reproduction",
                "template",
            ],
        )
        self.campaign_member_type.set("study")
        self.campaign_member_type.grid(
            row=0, column=0, padx=(0, 4), sticky="ew"
        )

        self.campaign_member_id = ctk.CTkEntry(member_controls)
        self.campaign_member_id.insert(0, "1")
        self.campaign_member_id.grid(
            row=0, column=1, padx=4, sticky="ew"
        )

        ctk.CTkButton(
            member_controls,
            text="ADD MEMBER ID",
            command=self._add_campaign_member_ui,
            height=30,
            fg_color="#294D4B",
            hover_color="#376866",
        ).grid(row=0, column=2, padx=(4, 0), sticky="ew")

        self.campaign_member_list = tk.Listbox(
            right,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            selectforeground=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 9),
        )
        self.campaign_member_list.grid(
            row=2, column=0, padx=10, pady=5, sticky="nsew"
        )

        member_actions = ctk.CTkFrame(right, fg_color="transparent")
        member_actions.grid(
            row=3, column=0, padx=10, pady=(5, 10), sticky="ew"
        )
        member_actions.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            member_actions,
            text="ADD LATEST STUDY",
            command=self._add_latest_study_to_campaign,
            height=30,
            fg_color="#30445A",
            hover_color="#405E7B",
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            member_actions,
            text="REMOVE SELECTED MEMBER",
            command=self._remove_campaign_member_ui,
            height=30,
            fg_color="#71303A",
            hover_color="#923E4B",
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.campaign_status = ctk.CTkLabel(
            self.campaign_window,
            text="Ready",
            text_color=self.MUTED,
            anchor="w",
        )
        self.campaign_status.grid(
            row=3, column=0, padx=18, pady=(0, 16), sticky="ew"
        )

        self.campaign_window.protocol(
            "WM_DELETE_WINDOW",
            self._close_campaign_center,
        )
        self._refresh_campaign_center()
        self._poll_campaign_run_messages()

    def _close_campaign_center(self):
        if self.campaign_window is not None:
            try:
                self.campaign_window.destroy()
            except tk.TclError:
                pass
        self.campaign_window = None
        self.campaign_list = None
        self.campaign_detail = None
        self.campaign_member_list = None
        self.campaign_rows = []
        self.campaign_member_rows = []

    def _refresh_campaign_center(self):
        self.campaign_rows = list_campaigns(self.db_path)
        if self.campaign_list is None:
            return

        self.campaign_list.delete(0, "end")
        if not self.campaign_rows:
            self.campaign_list.insert("end", "No active campaigns.")
            self._set_campaign_detail("")
            return

        for item in self.campaign_rows:
            self.campaign_list.insert(
                "end",
                (
                    f"#{item['id']:04d}  {item['name'][:28]:<28}  "
                    f"{item['updated_at'].replace('T', ' ')}"
                ),
            )

    def _selected_campaign(self):
        if self.campaign_list is None or not self.campaign_rows:
            return None
        selected = self.campaign_list.curselection()
        if not selected:
            return None
        return self.campaign_rows[selected[0]]

    def _set_campaign_detail(self, text):
        if self.campaign_detail is None:
            return
        self.campaign_detail.configure(state="normal")
        self.campaign_detail.delete("1.0", "end")
        self.campaign_detail.insert("1.0", text)
        self.campaign_detail.configure(state="disabled")

    def _show_campaign_detail(self, _event=None):
        campaign = self._selected_campaign()
        if campaign is None:
            return

        try:
            payload = campaign_snapshot(self.db_path, campaign["id"])
        except Exception as exc:
            self._set_campaign_detail(f"Campaign load failed: {exc}")
            return

        self._set_campaign_detail(campaign_summary_text(payload))
        self.campaign_member_rows = payload["members"]

        if self.campaign_member_list is not None:
            self.campaign_member_list.delete(0, "end")
            for member in self.campaign_member_rows:
                marker = "OK" if member["resolved"] else "MISSING"
                self.campaign_member_list.insert(
                    "end",
                    (
                        f"[{marker:<7}] {member['member_type']:<12} "
                        f"#{member['member_id']}  {member.get('label', '')}"
                    ),
                )

    def _create_campaign_ui(self):
        name = self.campaign_name_entry.get().strip()
        campaign_id = create_campaign(
            self.db_path,
            name=name or "Hadron Campaign",
        )
        self.campaign_status.configure(
            text=f"Campaign #{campaign_id} created."
        )
        self._refresh_campaign_center()

    def _add_campaign_member_ui(self):
        campaign = self._selected_campaign()
        if campaign is None:
            self.campaign_status.configure(text="Select a campaign first.")
            return

        try:
            member_id = int(self.campaign_member_id.get())
            member_type = self.campaign_member_type.get()
            membership_id = add_campaign_member(
                self.db_path,
                campaign["id"],
                member_type=member_type,
                member_id=member_id,
            )
        except Exception as exc:
            self.campaign_status.configure(
                text=f"Could not add campaign member: {exc}"
            )
            return

        self.campaign_status.configure(
            text=f"Membership #{membership_id} added."
        )
        self._show_campaign_detail()

    def _add_latest_study_to_campaign(self):
        campaign = self._selected_campaign()
        if campaign is None:
            self.campaign_status.configure(text="Select a campaign first.")
            return

        studies = list_studies(self.db_path, limit=1)
        if not studies:
            self.campaign_status.configure(text="No saved studies available.")
            return

        study = studies[0]
        add_campaign_member(
            self.db_path,
            campaign["id"],
            member_type="study",
            member_id=study["id"],
            label=study["name"],
        )
        self.campaign_status.configure(
            text=f"Study #{study['id']} added to campaign."
        )
        self._show_campaign_detail()

    def _remove_campaign_member_ui(self):
        campaign = self._selected_campaign()
        if (
            campaign is None
            or self.campaign_member_list is None
            or not self.campaign_member_rows
        ):
            return

        selected = self.campaign_member_list.curselection()
        if not selected:
            return

        member = self.campaign_member_rows[selected[0]]
        remove_campaign_member(
            self.db_path,
            campaign["id"],
            member_type=member["member_type"],
            member_id=member["member_id"],
        )
        self.campaign_status.configure(
            text=(
                f"Removed {member['member_type']} "
                f"#{member['member_id']} from campaign."
            )
        )
        self._show_campaign_detail()

    def _archive_selected_campaign(self):
        campaign = self._selected_campaign()
        if campaign is None:
            return
        archive_campaign(
            self.db_path,
            campaign["id"],
            archived=True,
        )
        self.campaign_status.configure(
            text=f"Campaign #{campaign['id']} archived."
        )
        self._refresh_campaign_center()

    def _campaign_health_ui(self):
        campaign = self._selected_campaign()
        if campaign is None:
            self.campaign_status.configure(text="Select a campaign first.")
            return

        try:
            health = campaign_health(self.db_path, campaign["id"])
        except Exception as exc:
            self.campaign_status.configure(
                text=f"Campaign health failed: {exc}"
            )
            return

        lines = [
            f"CAMPAIGN HEALTH · #{campaign['id']} {campaign['name']}",
            "",
            (
                f"Healthy members: {health['healthy_members']}/"
                f"{health['members']}"
            ),
            f"Overall: {'HEALTHY' if health['healthy'] else 'ATTENTION REQUIRED'}",
            "",
        ]
        for row in health["rows"]:
            lines.append(
                f"[{row['status']:<10}] {row['member_type']:<12} "
                f"#{row['member_id']}  {row['label']}"
            )
            if row["reason"]:
                lines.append(f"  {row['reason']}")

        self._set_campaign_detail("\n".join(lines))
        self.campaign_status.configure(
            text=(
                f"Health: {health['healthy_members']}/{health['members']} "
                "members healthy."
            )
        )

    def _campaign_save_reference_ui(self):
        campaign = self._selected_campaign()
        if campaign is None:
            self.campaign_status.configure(text="Select a campaign first.")
            return

        try:
            reference_id = save_campaign_reference(
                self.db_path,
                campaign["id"],
                name="Campaign Reference",
            )
        except Exception as exc:
            self.campaign_status.configure(
                text=f"Reference creation failed: {exc}"
            )
            return

        self.campaign_status.configure(
            text=f"Campaign reference #{reference_id} saved."
        )
        self.log(
            "CAMPAIGN",
            f"Campaign #{campaign['id']} reference #{reference_id} saved.",
            "saved",
        )

    def _campaign_check_reference_ui(self):
        campaign = self._selected_campaign()
        if campaign is None:
            self.campaign_status.configure(text="Select a campaign first.")
            return

        refs = list_campaign_references(
            self.db_path,
            campaign["id"],
            limit=1,
        )
        if not refs:
            self.campaign_status.configure(
                text="No campaign reference saved yet."
            )
            return

        try:
            check = check_campaign_reference(
                self.db_path,
                refs[0]["payload"],
            )
        except Exception as exc:
            self.campaign_status.configure(
                text=f"Reference check failed: {exc}"
            )
            return

        lines = [
            f"REFERENCE CHECK · #{refs[0]['id']}",
            "",
            f"Members matching: {check['members_matching']}/{check['members_checked']}",
            f"Drifted members:  {check['drifted_members']}",
            f"Overall: {'MATCH' if check['matches'] else 'DRIFT DETECTED'}",
            "",
        ]
        for row in check["rows"]:
            lines.append(
                f"[{row['status']:<9}] {row['member_type']:<12} "
                f"#{row['member_id']}"
            )

        self._set_campaign_detail("\n".join(lines))
        self.campaign_status.configure(
            text=(
                "Reference MATCH"
                if check["matches"]
                else f"Reference drift: {check['drifted_members']} member(s)."
            )
        )

    def _campaign_run_ui(self):
        campaign = self._selected_campaign()
        if campaign is None:
            self.campaign_status.configure(text="Select a campaign first.")
            return

        if self.campaign_run_thread is not None:
            self.campaign_status.configure(
                text="A campaign verification run is already active."
            )
            return

        campaign_id = int(campaign["id"])
        self.campaign_status.configure(
            text=f"Running deterministic campaign verification #{campaign_id}…"
        )

        self.campaign_run_thread = threading.Thread(
            target=self._campaign_run_worker,
            args=(campaign_id,),
            daemon=True,
        )
        self.campaign_run_thread.start()

    def _campaign_run_worker(self, campaign_id):
        try:
            report = run_campaign_to_database(
                self.db_path,
                campaign_id,
                workers_override=1,
            )
            self.campaign_run_messages.put(
                ("complete", campaign_id, report)
            )
        except Exception as exc:
            self.campaign_run_messages.put(
                ("failed", campaign_id, str(exc))
            )

    def _poll_campaign_run_messages(self):
        while True:
            try:
                message = self.campaign_run_messages.get_nowait()
            except queue.Empty:
                break

            kind, campaign_id, payload = message
            if kind == "complete":
                self.campaign_status.configure(
                    text=(
                        f"Campaign run #{payload['database_run_id']}: "
                        f"{'PASS' if payload['all_passed'] else 'FAIL'} · "
                        f"{payload['passed']}/{payload['studies']} studies"
                    )
                )
                self.log(
                    "CAMPAIGN",
                    (
                        f"Campaign #{campaign_id} deterministic run: "
                        f"{payload['passed']}/{payload['studies']} matched."
                    ),
                    "saved" if payload["all_passed"] else "discarded",
                )

                lines = [
                    f"CAMPAIGN RUN · #{payload['database_run_id']}",
                    "",
                    f"Studies: {payload['studies']}",
                    f"Passed:  {payload['passed']}",
                    f"Overall: {'PASS' if payload['all_passed'] else 'FAIL'}",
                    "",
                ]
                for row in payload["rows"]:
                    lines.append(
                        f"[{row['status']:<10}] study #{row['study_id']} "
                        f"{row.get('study_name', '')}"
                    )
                self._set_campaign_detail("\n".join(lines))
            else:
                self.campaign_status.configure(
                    text=f"Campaign run failed: {payload}"
                )
                self.log(
                    "CAMPAIGN",
                    f"Campaign #{campaign_id} run failed: {payload}",
                    "discarded",
                )

            self.campaign_run_thread = None

        if self.campaign_window is not None:
            try:
                if self.campaign_window.winfo_exists():
                    self.after(250, self._poll_campaign_run_messages)
            except tk.TclError:
                pass

    def _campaign_report_ui(self):
        campaign = self._selected_campaign()
        if campaign is None:
            self.campaign_status.configure(text="Select a campaign first.")
            return

        path = filedialog.asksaveasfilename(
            title=f"Create report for campaign #{campaign['id']}",
            defaultextension=".hadron-campaign-report.zip",
            filetypes=[
                ("Hadron campaign report", "*.hadron-campaign-report.zip"),
                ("ZIP archive", "*.zip"),
            ],
            initialfile=(
                f"Hadron-campaign-{campaign['id']}-report."
                "hadron-campaign-report.zip"
            ),
        )
        if not path:
            return

        try:
            create_campaign_report_bundle(
                self.db_path,
                campaign["id"],
                path,
            )
        except Exception as exc:
            self.campaign_status.configure(
                text=f"Campaign report failed: {exc}"
            )
            return

        self.campaign_status.configure(
            text=f"Campaign report package saved: {path}"
        )
        self.log(
            "CAMPAIGN",
            f"Campaign #{campaign['id']} report package: {path}",
            "saved",
        )

    def _export_campaign_ui(self):
        campaign = self._selected_campaign()
        if campaign is None:
            self.campaign_status.configure(text="Select a campaign first.")
            return

        path = filedialog.asksaveasfilename(
            title=f"Export campaign #{campaign['id']}",
            defaultextension=".hadron-campaign.zip",
            filetypes=[
                ("Hadron campaign", "*.hadron-campaign.zip"),
                ("ZIP archive", "*.zip"),
            ],
            initialfile=(
                f"Hadron-campaign-{campaign['id']}.hadron-campaign.zip"
            ),
        )
        if not path:
            return

        export_campaign_bundle(
            self.db_path,
            campaign["id"],
            path,
        )
        self.campaign_status.configure(
            text=f"Campaign exported: {path}"
        )
        self.log(
            "CAMPAIGN",
            f"Campaign #{campaign['id']} exported: {path}",
            "saved",
        )

    def _import_campaign_ui(self):
        path = filedialog.askopenfilename(
            title="Import Hadron campaign",
            filetypes=[
                ("Hadron campaign", "*.hadron-campaign.zip"),
                ("ZIP archive", "*.zip"),
            ],
        )
        if not path:
            return

        try:
            campaign_id = import_campaign_bundle(
                self.db_path,
                path,
                restore_studies=True,
            )
        except Exception as exc:
            self.campaign_status.configure(
                text=f"Campaign import failed: {exc}"
            )
            return

        self.campaign_status.configure(
            text=f"Campaign imported as #{campaign_id}."
        )
        self._refresh_campaign_center()
        self._load_study_queue()

    def open_pipeline_center(self):
        if (
            self.pipeline_window is not None
            and self.pipeline_window.winfo_exists()
        ):
            self.pipeline_window.focus()
            self._refresh_pipeline_center()
            return

        self.pipeline_window = ctk.CTkToplevel(self)
        self.pipeline_window.title("Hadron — Pipeline Center")
        self.pipeline_window.geometry("1120x700")
        self.pipeline_window.minsize(860, 580)
        self.pipeline_window.configure(fg_color=self.BG)
        self.pipeline_window.grid_columnconfigure(0, weight=1)
        self.pipeline_window.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self.pipeline_window,
            text="PIPELINE CENTER · v2.2",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=18, pady=(16, 4), sticky="w")

        ctk.CTkLabel(
            self.pipeline_window,
            text=(
                "Run campaign health, reference, deterministic verification "
                "and reporting as one gated workflow."
            ),
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=1, column=0, padx=18, pady=(0, 8), sticky="w")

        body = ctk.CTkFrame(self.pipeline_window, fg_color="transparent")
        body.grid(row=2, column=0, padx=18, pady=(0, 10), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        left = self._panel(body)
        left.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            left,
            text="PIPELINES",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, padx=10, pady=(10, 4), sticky="w")

        self.pipeline_list = tk.Listbox(
            left,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 9),
        )
        self.pipeline_list.grid(
            row=1, column=0, padx=10, pady=4, sticky="nsew"
        )
        self.pipeline_list.bind(
            "<<ListboxSelect>>",
            self._show_pipeline_detail,
        )

        create_box = ctk.CTkFrame(left, fg_color="transparent")
        create_box.grid(
            row=2, column=0, padx=10, pady=(4, 6), sticky="ew"
        )
        create_box.grid_columnconfigure(0, weight=1)

        self.pipeline_name_entry = ctk.CTkEntry(create_box)
        self.pipeline_name_entry.insert(0, "Release Gate")
        self.pipeline_name_entry.grid(
            row=0, column=0, padx=(0, 4), sticky="ew"
        )

        self.pipeline_campaign_entry = ctk.CTkEntry(
            create_box,
            width=70,
        )
        self.pipeline_campaign_entry.insert(0, "1")
        self.pipeline_campaign_entry.grid(
            row=0, column=1, padx=4
        )

        ctk.CTkButton(
            create_box,
            text="CREATE",
            command=self._create_pipeline_ui,
            width=80,
            height=30,
            fg_color="#4B3D68",
            hover_color="#635189",
        ).grid(row=0, column=2, padx=(4, 0))

        actions = ctk.CTkFrame(left, fg_color="transparent")
        actions.grid(
            row=3, column=0, padx=10, pady=(0, 10), sticky="ew"
        )
        actions.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            actions,
            text="RUN PIPELINE",
            command=self._run_selected_pipeline_ui,
            height=30,
            fg_color="#4B3D68",
            hover_color="#635189",
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            actions,
            text="REFRESH",
            command=self._refresh_pipeline_center,
            height=30,
            fg_color="#273548",
            hover_color="#354861",
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        right = self._panel(body)
        right.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=2)
        right.grid_rowconfigure(2, weight=1)

        self.pipeline_detail = tk.Text(
            right,
            bg="#0A0E14",
            fg=self.TEXT,
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=14,
            pady=12,
            font=("Consolas", 10),
        )
        self.pipeline_detail.grid(
            row=0, column=0, padx=10, pady=(10, 5), sticky="nsew"
        )
        self.pipeline_detail.configure(state="disabled")

        ctk.CTkLabel(
            right,
            text="RUN HISTORY",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=1, column=0, padx=10, pady=(5, 0), sticky="w")

        self.pipeline_run_list = tk.Listbox(
            right,
            bg="#0A0E14",
            fg=self.TEXT,
            selectbackground="#244861",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 9),
        )
        self.pipeline_run_list.grid(
            row=2, column=0, padx=10, pady=(4, 10), sticky="nsew"
        )

        self.pipeline_status = ctk.CTkLabel(
            self.pipeline_window,
            text="Ready",
            text_color=self.MUTED,
            anchor="w",
        )
        self.pipeline_status.grid(
            row=3, column=0, padx=18, pady=(0, 16), sticky="ew"
        )

        self.pipeline_window.protocol(
            "WM_DELETE_WINDOW",
            self._close_pipeline_center,
        )
        self._refresh_pipeline_center()
        self._poll_pipeline_messages()

    def _close_pipeline_center(self):
        if self.pipeline_window is not None:
            try:
                self.pipeline_window.destroy()
            except tk.TclError:
                pass
        self.pipeline_window = None
        self.pipeline_list = None
        self.pipeline_run_list = None
        self.pipeline_detail = None
        self.pipeline_rows = []
        self.pipeline_run_rows = []

    def _refresh_pipeline_center(self):
        self.pipeline_rows = list_pipelines(self.db_path)
        if self.pipeline_list is None:
            return

        self.pipeline_list.delete(0, "end")
        if not self.pipeline_rows:
            self.pipeline_list.insert("end", "No active pipelines.")
            return

        for item in self.pipeline_rows:
            self.pipeline_list.insert(
                "end",
                (
                    f"#{item['id']:04d} campaign #{item['campaign_id']} · "
                    f"{item['name'][:28]}"
                ),
            )

    def _selected_pipeline(self):
        if self.pipeline_list is None or not self.pipeline_rows:
            return None
        selected = self.pipeline_list.curselection()
        if not selected:
            return None
        return self.pipeline_rows[selected[0]]

    def _set_pipeline_detail(self, text):
        if self.pipeline_detail is None:
            return
        self.pipeline_detail.configure(state="normal")
        self.pipeline_detail.delete("1.0", "end")
        self.pipeline_detail.insert("1.0", text)
        self.pipeline_detail.configure(state="disabled")

    def _show_pipeline_detail(self, _event=None):
        pipeline = self._selected_pipeline()
        if pipeline is None:
            return

        lines = [
            f"Pipeline #{pipeline['id']} · {pipeline['name']}",
            f"Campaign: #{pipeline['campaign_id']}",
            f"Updated:  {pipeline['updated_at']}",
            "",
            "STAGES",
        ]
        for index, stage in enumerate(pipeline["spec"]["stages"], start=1):
            lines.append(f"  {index}. {stage}")

        lines += [
            "",
            "GATES",
            f"  Health required:     {pipeline['spec']['require_healthy']}",
            (
                "  Reference required:  "
                f"{pipeline['spec']['require_reference_match']}"
            ),
            (
                "  Campaign pass needed: "
                f"{pipeline['spec']['require_campaign_pass']}"
            ),
            (
                "  Auto reference:       "
                f"{pipeline['spec']['auto_create_reference']}"
            ),
            f"  Workers:              {pipeline['spec']['workers']}",
        ]
        self._set_pipeline_detail("\n".join(lines))

        self.pipeline_run_rows = list_pipeline_runs(
            self.db_path,
            pipeline["id"],
            limit=100,
        )
        if self.pipeline_run_list is not None:
            self.pipeline_run_list.delete(0, "end")
            if not self.pipeline_run_rows:
                self.pipeline_run_list.insert("end", "No pipeline runs.")
            for item in self.pipeline_run_rows:
                marker = "PASS" if item["passed"] else "FAIL"
                self.pipeline_run_list.insert(
                    "end",
                    (
                        f"#{item['id']:04d} [{marker}] "
                        f"{item['created_at'].replace('T', ' ')}"
                    ),
                )

    def _create_pipeline_ui(self):
        try:
            campaign_id = int(self.pipeline_campaign_entry.get())
            pipeline_id = create_pipeline(
                self.db_path,
                name=self.pipeline_name_entry.get().strip()
                or "Release Gate",
                campaign_id=campaign_id,
                spec={
                    "stages": [
                        "health",
                        "ensure-reference",
                        "reference-check",
                        "campaign-run",
                        "report",
                    ],
                    "auto_create_reference": True,
                },
            )
        except Exception as exc:
            self.pipeline_status.configure(
                text=f"Pipeline creation failed: {exc}"
            )
            return

        self.pipeline_status.configure(
            text=f"Pipeline #{pipeline_id} created."
        )
        self._refresh_pipeline_center()

    def _run_selected_pipeline_ui(self):
        pipeline = self._selected_pipeline()
        if pipeline is None:
            self.pipeline_status.configure(text="Select a pipeline first.")
            return

        if self.pipeline_thread is not None:
            self.pipeline_status.configure(
                text="A pipeline is already running."
            )
            return

        pipeline_id = int(pipeline["id"])
        report_dir = self.data_dir / "pipeline-reports"
        self.pipeline_status.configure(
            text=f"Running pipeline #{pipeline_id}…"
        )

        self.pipeline_thread = threading.Thread(
            target=self._pipeline_worker,
            args=(pipeline_id, report_dir),
            daemon=True,
        )
        self.pipeline_thread.start()

    def _pipeline_worker(self, pipeline_id, report_dir):
        try:
            report = run_pipeline_to_database(
                self.db_path,
                pipeline_id,
                report_dir=report_dir,
            )
            self.pipeline_messages.put(
                ("complete", pipeline_id, report)
            )
        except Exception as exc:
            self.pipeline_messages.put(
                ("failed", pipeline_id, str(exc))
            )

    def _poll_pipeline_messages(self):
        while True:
            try:
                message = self.pipeline_messages.get_nowait()
            except queue.Empty:
                break

            kind, pipeline_id, payload = message
            if kind == "complete":
                self.pipeline_status.configure(
                    text=(
                        f"Pipeline run #{payload['database_run_id']}: "
                        f"{'PASS' if payload['passed'] else 'FAIL'}"
                    )
                )
                lines = [
                    f"PIPELINE RUN #{payload['database_run_id']}",
                    "",
                    f"Overall: {'PASS' if payload['passed'] else 'FAIL'}",
                    f"Halted:  {payload['halted']}",
                    "",
                ]
                for stage in payload["stages"]:
                    marker = (
                        "SKIP"
                        if stage["skipped"]
                        else ("PASS" if stage["ok"] else "FAIL")
                    )
                    lines.append(
                        f"[{marker:<4}] {stage['stage']}"
                    )
                self._set_pipeline_detail("\n".join(lines))
                self.log(
                    "PIPELINE",
                    (
                        f"Pipeline #{pipeline_id}: "
                        f"{'PASS' if payload['passed'] else 'FAIL'}."
                    ),
                    "saved" if payload["passed"] else "discarded",
                )
            else:
                self.pipeline_status.configure(
                    text=f"Pipeline failed: {payload}"
                )
                self.log(
                    "PIPELINE",
                    f"Pipeline #{pipeline_id} failed: {payload}",
                    "discarded",
                )

            self.pipeline_thread = None
            self._refresh_pipeline_center()

        if self.pipeline_window is not None:
            try:
                if self.pipeline_window.winfo_exists():
                    self.after(250, self._poll_pipeline_messages)
            except tk.TclError:
                pass

    def _export_workspace_bundle_ui(self):
        path = filedialog.asksaveasfilename(
            title="Export full Hadron workspace",
            defaultextension=".hadron-workspace.zip",
            filetypes=[
                ("Hadron workspace", "*.hadron-workspace.zip"),
                ("ZIP archive", "*.zip"),
            ],
            initialfile="Hadron-workspace.hadron-workspace.zip",
        )
        if not path:
            return
        try:
            export_workspace_bundle(
                path,
                data_dir=self.data_dir,
                db_path=self.db_path,
                settings_path=self.settings_path,
                session_path=self.session_path,
            )
            self.log("SYSTEM", f"Workspace exported: {path}", "saved")
        except Exception as exc:
            self.log("SYSTEM", f"Workspace export failed: {exc}", "discarded")

    def _import_workspace_bundle_ui(self):
        path = filedialog.askopenfilename(
            title="Import full Hadron workspace",
            filetypes=[
                ("Hadron workspace", "*.hadron-workspace.zip"),
                ("ZIP archive", "*.zip"),
            ],
        )
        if not path:
            return

        if self.stream_running:
            self.stream_running = False
            self.stream_paused = False
            self._finish_run_record()

        try:
            result = import_workspace_bundle(
                path,
                data_dir=self.data_dir,
                db_path=self.db_path,
                settings_path=self.settings_path,
                session_path=self.session_path,
            )
            self.migration_info = result["migration"]
            self._load_settings()
            self._load_session_state()
            self._apply_session_to_widgets()
            self._refresh_system_diagnostics()
            self.log(
                "SYSTEM",
                f"Workspace imported. Safety backup: {result.get('safety_backup') or 'none'}",
                "saved",
            )
        except Exception as exc:
            self.log("SYSTEM", f"Workspace import failed: {exc}", "discarded")

    def _create_support_bundle_ui(self):
        path = filedialog.asksaveasfilename(
            title="Create Hadron support bundle",
            defaultextension=".zip",
            filetypes=[("ZIP archive", "*.zip")],
            initialfile="Hadron-support-bundle.zip",
        )
        if not path:
            return
        try:
            create_support_bundle(
                path,
                data_dir=self.data_dir,
                db_path=self.db_path,
                settings_path=self.settings_path,
                session_path=self.session_path,
            )
            self.log("SYSTEM", f"Support bundle created: {path}", "saved")
        except Exception as exc:
            self.log("SYSTEM", f"Support bundle failed: {exc}", "discarded")

    def _backup_database_ui(self):
        path = filedialog.asksaveasfilename(
            title="Backup Hadron database",
            defaultextension=".sqlite3",
            filetypes=[("SQLite database", "*.sqlite3"), ("All files", "*.*")],
            initialfile="hadron-backup.sqlite3",
        )
        if not path:
            return
        try:
            backup_database(self.db_path, path)
            self.log("SYSTEM", f"Database backup created: {path}", "saved")
        except Exception as exc:
            self.log("SYSTEM", f"Backup failed: {exc}", "discarded")

    def _restore_database_ui(self):
        path = filedialog.askopenfilename(
            title="Restore Hadron database",
            filetypes=[("SQLite database", "*.sqlite3"), ("All files", "*.*")],
        )
        if not path:
            return

        # Close active live stream before replacing the database.
        if self.stream_running:
            self.stream_running = False
            self.stream_paused = False
            self._finish_run_record()

        safety_backup = self.data_dir / "pre-restore-backup.sqlite3"
        try:
            if self.db_path.exists():
                backup_database(self.db_path, safety_backup)
            restore_database(path, self.db_path)
            self.migration_info = self._init_database()
            self._refresh_system_diagnostics()
            self.log(
                "SYSTEM",
                f"Database restored. Safety backup: {safety_backup}",
                "saved",
            )
        except Exception as exc:
            self.log("SYSTEM", f"Restore failed: {exc}", "discarded")

    # ------------------------------------------------------------------
    # Session recovery
    # ------------------------------------------------------------------

    def _load_session_state(self):
        try:
            state = load_session(self.session_path)
        except Exception:
            state = {}

        self.target_energy = float(state.get("target_energy", self.target_energy))
        self.collision_rate_hz = float(
            state.get("collision_rate_hz", self.collision_rate_hz)
        )

        preset = state.get("active_preset", self.active_preset)
        if preset in self.physics_presets:
            self.active_preset = preset

        plot_mode = state.get("plot_mode", self.plot_mode)
        if plot_mode in ("MASS", "LIVE STATS"):
            self.plot_mode = plot_mode

        detector = state.get("detector_selection", "AUTO")
        allowed = {
            "AUTO", "ATLAS-SIM", "CMS-SIM", "INNER-TRACKER", "CALORIMETER"
        }
        self.detector_selection = detector if detector in allowed else "AUTO"
        self.restored_geometry = str(state.get("geometry", "")).strip()

    def _apply_session_to_widgets(self):
        if getattr(self, "restored_geometry", ""):
            try:
                self.geometry(self.restored_geometry)
            except tk.TclError:
                pass

        try:
            self.energy_slider.set(self.target_energy)
            self.energy_value.configure(text=f"{self.target_energy:,.0f} GeV")
            self.rate_slider.set(self.collision_rate_hz)
            self.rate_value.configure(text=f"{self.collision_rate_hz:.1f} events/s")
            self.preset_menu.set(self.active_preset)
            self.detector_menu.set(self.detector_selection)
            self.plot_selector.set(self.plot_mode)
        except (AttributeError, tk.TclError):
            pass

    def _save_session_state(self):
        try:
            detector = self.detector_menu.get()
        except Exception:
            detector = self.detector_selection

        save_session(
            self.session_path,
            {
                "target_energy": self.target_energy,
                "collision_rate_hz": self.collision_rate_hz,
                "active_preset": self.active_preset,
                "plot_mode": self.plot_mode,
                "detector_selection": detector,
                "geometry": self.geometry(),
                # Deliberately never restore an active acquisition stream.
                "stream_was_running": False,
            },
        )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _selected_history_row(self):
        if self.history_list is None or not self.history_rows:
            return None
        selected = self.history_list.curselection()
        if not selected:
            return None
        return self.history_rows[selected[0]]

    def _save_run_annotation(self):
        row = self._selected_history_row()
        if row is None:
            return
        run_id = row[0]
        raw_tags = self.history_tags_entry.get().strip()
        tags = sorted({
            tag.strip()
            for tag in raw_tags.split(",")
            if tag.strip()
        })
        note = self.history_note_entry.get().strip()
        now = datetime.now().isoformat(timespec="seconds")

        with connect_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO run_annotations(run_id, tags_json, note, archived, updated_at)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    tags_json=excluded.tags_json,
                    note=excluded.note,
                    updated_at=excluded.updated_at
                """,
                (run_id, json.dumps(tags), note, now),
            )
            conn.commit()

        self.log("RUN", f"Updated metadata for run #{run_id}.", "saved")
        self._load_run_history()

    def _toggle_run_archive(self):
        row = self._selected_history_row()
        if row is None:
            return
        run_id = row[0]
        archived = int(row[10])
        new_value = 0 if archived else 1
        now = datetime.now().isoformat(timespec="seconds")

        with connect_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO run_annotations(run_id, tags_json, note, archived, updated_at)
                VALUES (?, '[]', '', ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    archived=excluded.archived,
                    updated_at=excluded.updated_at
                """,
                (run_id, new_value, now),
            )
            conn.commit()

        action = "restored" if archived else "archived"
        self.log("RUN", f"Run #{run_id} {action}.", "system")
        self._load_run_history()

    def _export_selected_run_json(self):
        row = self._selected_history_row()
        if row is None:
            return
        run_id = row[0]

        with connect_db(self.db_path) as conn:
            run = conn.execute(
                """
                SELECT r.id, r.started_at, r.ended_at, r.target_energy_gev,
                       r.final_energy_gev, r.collision_count, r.saved_count,
                       r.discarded_count, COALESCE(a.tags_json, '[]'),
                       COALESCE(a.note, ''), COALESCE(a.archived, 0)
                FROM runs r
                LEFT JOIN run_annotations a ON a.run_id = r.id
                WHERE r.id = ?
                """,
                (run_id,),
            ).fetchone()
            events = conn.execute(
                """
                SELECT id, event_number, timestamp, detector,
                       transverse_energy, missing_energy, muon_count,
                       particle_masses_json, reason, is_higgs_candidate
                FROM accepted_events
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()

        payload = {
            "application": "Hadron",
            "version": __version__,
            "export_type": "run",
            "run": {
                "id": run[0],
                "started_at": run[1],
                "ended_at": run[2],
                "target_energy_gev": run[3],
                "final_energy_gev": run[4],
                "collision_count": run[5],
                "saved_count": run[6],
                "discarded_count": run[7],
                "tags": json.loads(run[8]),
                "note": run[9],
                "archived": bool(run[10]),
            },
            "accepted_events": [
                {
                    "id": e[0],
                    "event_number": e[1],
                    "timestamp": e[2],
                    "detector": e[3],
                    "transverse_energy": e[4],
                    "missing_energy": e[5],
                    "muon_count": e[6],
                    "particle_masses": json.loads(e[7]),
                    "reason": e[8],
                    "is_higgs_candidate": bool(e[9]),
                }
                for e in events
            ],
        }

        path = filedialog.asksaveasfilename(
            title=f"Export Hadron run #{run_id}",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile=f"hadron-run-{run_id}.json",
        )
        if not path:
            return
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.log("EXPORT", f"Run #{run_id} exported: {path}", "saved")

    def _load_settings(self):
        defaults = {
            "active_preset": "STANDARD",
            "detector_noise_enabled": True,
            "detector_noise_sigma": 0.9,
            "detector_resolution_sigma": 0.012,
            "collision_rate_hz": 3.0,
            "l1_energy_threshold": L1_ENERGY_THRESHOLD,
            "met_trigger_threshold": 500.0,
            "higgs_window_gev": 3.0,
        }

        if self.settings_path.exists():
            try:
                loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
                defaults.update(loaded)
            except Exception:
                pass

        if defaults["active_preset"] in self.physics_presets:
            self.active_preset = defaults["active_preset"]

        self.detector_noise_enabled = bool(defaults["detector_noise_enabled"])
        self.detector_noise_sigma = float(defaults["detector_noise_sigma"])
        self.detector_resolution_sigma = float(defaults["detector_resolution_sigma"])
        self.collision_rate_hz = float(defaults["collision_rate_hz"])
        self.l1_energy_threshold = float(defaults["l1_energy_threshold"])
        self.met_trigger_threshold = float(defaults["met_trigger_threshold"])
        self.higgs_window_gev = float(defaults["higgs_window_gev"])

    def _save_settings(self):
        payload = {
            "active_preset": self.active_preset,
            "detector_noise_enabled": self.detector_noise_enabled,
            "detector_noise_sigma": self.detector_noise_sigma,
            "detector_resolution_sigma": self.detector_resolution_sigma,
            "collision_rate_hz": self.collision_rate_hz,
            "l1_energy_threshold": self.l1_energy_threshold,
            "met_trigger_threshold": self.met_trigger_threshold,
            "higgs_window_gev": self.higgs_window_gev,
        }
        self.settings_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def open_settings(self):
        window = ctk.CTkToplevel(self)
        window.title("Hadron — Settings")
        window.geometry("520x430")
        window.resizable(False, False)
        window.configure(fg_color=self.BG)
        window.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            window,
            text="SIMULATION SETTINGS",
            text_color=self.TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(18, 12), sticky="w")

        noise_var = tk.BooleanVar(value=self.detector_noise_enabled)
        noise_switch = ctk.CTkSwitch(
            window,
            text="Enable detector noise / resolution smearing",
            variable=noise_var,
        )
        noise_switch.grid(row=1, column=0, padx=20, pady=8, sticky="w")

        ctk.CTkLabel(
            window,
            text="Noise sigma (GeV)",
            text_color=self.MUTED,
        ).grid(row=2, column=0, padx=20, pady=(12, 2), sticky="w")

        noise_slider = ctk.CTkSlider(
            window,
            from_=0.0,
            to=5.0,
            number_of_steps=50,
        )
        noise_slider.set(self.detector_noise_sigma)
        noise_slider.grid(row=3, column=0, padx=20, pady=4, sticky="ew")

        ctk.CTkLabel(
            window,
            text="Relative mass/energy resolution",
            text_color=self.MUTED,
        ).grid(row=4, column=0, padx=20, pady=(12, 2), sticky="w")

        resolution_slider = ctk.CTkSlider(
            window,
            from_=0.0,
            to=0.05,
            number_of_steps=50,
        )
        resolution_slider.set(self.detector_resolution_sigma)
        resolution_slider.grid(row=5, column=0, padx=20, pady=4, sticky="ew")

        info = ctk.CTkLabel(
            window,
            text=(
                "These controls affect the toy detector response only.\\n"
                "They do not represent calibrated real detector performance."
            ),
            text_color=self.MUTED,
            justify="left",
            font=ctk.CTkFont(size=10),
        )
        info.grid(row=6, column=0, padx=20, pady=(18, 8), sticky="w")

        def apply_settings():
            self.detector_noise_enabled = bool(noise_var.get())
            self.detector_noise_sigma = float(noise_slider.get())
            self.detector_resolution_sigma = float(resolution_slider.get())
            self._save_settings()
            self.log(
                "SETTINGS",
                f"Noise {'on' if self.detector_noise_enabled else 'off'} · "
                f"σ={self.detector_noise_sigma:.2f} GeV · "
                f"resolution={self.detector_resolution_sigma*100:.2f}%",
                "system",
            )
            window.destroy()

        ctk.CTkButton(
            window,
            text="SAVE SETTINGS",
            command=apply_settings,
            height=38,
            fg_color="#18566C",
            hover_color="#216F89",
        ).grid(row=7, column=0, padx=20, pady=(14, 18), sticky="ew")

    # ------------------------------------------------------------------
    # Persistence + export
    # ------------------------------------------------------------------

    def _init_database(self):
        return migrate_database(
            self.db_path,
            backup_dir=self.data_dir / "migration_backups",
        )

    def _start_run_record(self):
        if self.current_run_id is not None:
            return
        self.run_counters.reset()
        with connect_db(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO runs (started_at, target_energy_gev, final_energy_gev)
                VALUES (?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    self.target_energy,
                    self.collider.beam_energy_gev,
                ),
            )
            self.current_run_id = cur.lastrowid
            conn.commit()

    def _finish_run_record(self):
        if self.current_run_id is None:
            return
        run_collisions, run_saved, run_discarded = (
            self.run_counters.to_db_tuple()
        )
        with connect_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE runs
                SET ended_at = ?,
                    final_energy_gev = ?,
                    collision_count = ?,
                    saved_count = ?,
                    discarded_count = ?
                WHERE id = ?
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    self.collider.beam_energy_gev,
                    run_collisions,
                    run_saved,
                    run_discarded,
                    self.current_run_id,
                ),
            )
            conn.commit()
        self.current_run_id = None

    def _save_event_record(self, record):
        if self.current_run_id is None:
            return None
        with connect_db(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO accepted_events (
                    run_id,
                    event_number,
                    timestamp,
                    detector,
                    transverse_energy,
                    missing_energy,
                    muon_count,
                    particle_masses_json,
                    reason,
                    is_higgs_candidate
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.current_run_id,
                    record["event_number"],
                    record["timestamp"],
                    record["detector"],
                    record["transverse_energy"],
                    record["missing_energy"],
                    record["muon_count"],
                    json.dumps(record["particle_masses"]),
                    record["reason"],
                    int(record["is_higgs_candidate"]),
                ),
            )
            conn.commit()
            return cur.lastrowid

    def export_csv(self):
        if not self.accepted_events:
            self.log("EXPORT", "No accepted events are available for export.", "background")
            return

        path = filedialog.asksaveasfilename(
            title="Export Hadron accepted events",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile="hadron_accepted_events.csv",
        )
        if not path:
            return

        fields = [
            "event_number",
            "timestamp",
            "detector",
            "transverse_energy",
            "missing_energy",
            "muon_count",
            "particle_masses",
            "reason",
            "is_higgs_candidate",
        ]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for event in self.accepted_events:
                row = dict(event)
                row["particle_masses"] = ";".join(
                    f"{mass:.6f}" for mass in row["particle_masses"]
                )
                writer.writerow(row)

        self.log("EXPORT", f"CSV saved: {path}", "saved")

    def export_json(self):
        if not self.accepted_events:
            self.log("EXPORT", "No accepted events are available for export.", "background")
            return

        path = filedialog.asksaveasfilename(
            title="Export Hadron accepted events",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile="hadron_accepted_events.json",
        )
        if not path:
            return

        payload = {
            "application": "Hadron",
            "version": __version__,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "beam_energy_gev": self.collider.beam_energy_gev,
            "target_energy_gev": self.target_energy,
            "events": self.accepted_events,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        self.log("EXPORT", f"JSON saved: {path}", "saved")

    def _install_crash_handler(self):
        import sys

        previous_hook = sys.excepthook

        def handler(exc_type, exc_value, exc_traceback):
            try:
                path = write_crash_log(
                    self.data_dir / "crashes",
                    exc_type,
                    exc_value,
                    exc_traceback,
                )
                try:
                    self.log("CRASH", f"Crash log written: {path}", "discarded")
                except Exception:
                    pass
            finally:
                previous_hook(exc_type, exc_value, exc_traceback)

        sys.excepthook = handler

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def log(self, label, message, tag="system"):
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {label:<10} {message}\n"

        self.log_text.configure(state="normal")
        self.log_text.insert("end", line, tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self):
        self.animation_running = False
        self.stream_running = False
        self.stream_paused = False
        self._finish_run_record()
        self._save_settings()
        self._save_session_state()
        self._close_detector_view()
        self._close_event3d_view()
        self._close_history_window()
        self._close_event_inspector()
        self._close_bookmarks()
        self._close_experiment_lab()
        if self.study_active_id is not None:
            try:
                set_study_status(
                    self.db_path,
                    self.study_active_id,
                    "INTERRUPTED",
                    error_text="Application closed before the study completed.",
                )
            except Exception:
                pass
            self.study_active_id = None
        self._close_study_queue()
        self._close_analysis_workspace()
        self._close_release_center()
        self._close_capsule_center()
        self._close_regression_center()
        self._close_campaign_center()
        self._close_pipeline_center()
        self._close_update_center()
        self._close_system_tools()
        if self.compare_window is not None:
            try:
                if self.compare_window.winfo_exists():
                    self.compare_window.destroy()
            except tk.TclError:
                pass
        self.destroy()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = HadronDashboard()
    app.mainloop()
