#!/usr/bin/env python3
"""Wayland Game Lighting — KDE/Wayland desktop frontend."""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QSlider, QSpinBox, QPlainTextEdit,
    QScrollArea, QVBoxLayout, QWidget,
)

# Detect whether the application is running from a PyInstaller build.
FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent.parent

    # Support both the current PyInstaller build and the final AppImage layout.
    backend_candidates = [
        APP_DIR / "backend" / "wayland-vts-lighting-backend",
        APP_DIR / "wayland-vts-lighting-backend" / "wayland-vts-lighting-backend",
    ]

    BACKEND = next(
        (path for path in backend_candidates if path.exists()),
        backend_candidates[0],
    )
else:
    APP_DIR = Path(__file__).resolve().parent
    BACKEND = APP_DIR / "lighting.py"

CONFIG_DIR = Path.home() / ".config" / "wayland-vts-lighting"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
DEFAULTS = {
    "capture_fps": 60,
    "color_update_fps": 40,
    "lighting_intensity": 0.65,
    "color_smoothing": 0.22,
    "brightness_smoothing": 0.18,
    "brightness_range": 12,
    "brightness_enabled": True,
    "brightness_boost": 0,
}


def load_settings():
    try:
        saved = json.loads(SETTINGS_FILE.read_text())
        return {**DEFAULTS, **saved}
    except (OSError, json.JSONDecodeError, TypeError):
        return DEFAULTS.copy()


def save_settings(settings):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temp = SETTINGS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, indent=2) + "\n")
    temp.replace(SETTINGS_FILE)


class SliderSetting(QWidget):
    def __init__(self, title, minimum, maximum, value, suffix="", scale=1, parent=None):
        super().__init__(parent)
        self.scale = scale
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        self.label = QLabel(title)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(round(value * scale))
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(54)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.label, 2)
        row.addWidget(self.slider, 4)
        row.addWidget(self.value_label, 0)
        self.slider.valueChanged.connect(self.update_label)
        self.update_label(self.slider.value())

    def update_label(self, value):
        shown = value / self.scale
        self.value_label.setText(f"{shown:g}{'%' if self.scale == 100 else ''}")

    def value(self):
        return self.slider.value() / self.scale


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wayland Game Lighting")
        self.setMinimumSize(620, 500)
        self.resize(780, 800)
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(APP_DIR))
        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.started.connect(self.on_started)
        self.process.finished.connect(self.on_finished)
        self.settings = load_settings()
        self._build_ui()
        self._load_controls()

        # Automatically save settings shortly after a control changes.
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(300)
        self.autosave_timer.timeout.connect(
            lambda: self.save_current_settings(quiet=True)
        )

        self._connect_autosave()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_process_state)
        self.refresh_timer.start(500)

    def _build_ui(self):
        # Put the complete settings page in a scroll area. On shorter windows,
        # the controls keep their natural size instead of being compressed.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setCentralWidget(scroll)

        central = QWidget()
        scroll.setWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(14)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("Game Lighting")
        title.setObjectName("title")
        subtitle = QLabel("PipeWire window capture → VTube Studio")
        subtitle.setObjectName("muted")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        header.addStretch()
        self.status = QLabel("●  Stopped")
        self.status.setObjectName("status")
        header.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(header)

        capture_box = QGroupBox("Window capture")
        capture_layout = QVBoxLayout(capture_box)
        self.capture_target = QLabel("No game window selected")
        self.capture_target.setObjectName("captureTarget")
        self.capture_target.setWordWrap(True)
        capture_layout.addWidget(self.capture_target)
        note = QLabel("Selecting a window opens the KDE Wayland ScreenCast picker. Choose the game window there.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        capture_layout.addWidget(note)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Select window and start")
        self.start_button.setObjectName("primary")
        self.stop_button = QPushButton("Stop lighting")
        self.stop_button.setEnabled(False)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        capture_layout.addLayout(buttons)
        self.start_button.clicked.connect(self.start_lighting)
        self.stop_button.clicked.connect(self.stop_lighting)
        outer.addWidget(capture_box)

        perf = QGroupBox("Performance")
        grid = QGridLayout(perf)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Capture rate"), 0, 0)
        self.capture_fps = QComboBox()
        self.capture_fps.addItems(["30 FPS", "40 FPS", "60 FPS"])
        grid.addWidget(self.capture_fps, 0, 1)
        grid.addWidget(QLabel("VTube Studio update rate"), 1, 0)
        self.update_fps = QComboBox()
        self.update_fps.addItems(["20 FPS", "30 FPS", "40 FPS", "60 FPS"])
        grid.addWidget(self.update_fps, 1, 1)
        hint = QLabel("Higher rates feel smoother but depend on PipeWire capture and VTube Studio response time.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        grid.addWidget(hint, 2, 0, 1, 2)
        outer.addWidget(perf)

        lighting = QGroupBox("Lighting")
        light_layout = QVBoxLayout(lighting)
        self.intensity = SliderSetting("Lighting intensity", 0, 100, self.settings["lighting_intensity"], scale=100)
        self.color_smooth = SliderSetting("Color smoothing", 1, 100, self.settings["color_smoothing"], scale=100)
        self.brightness_smooth = SliderSetting("Brightness smoothing", 1, 100, self.settings["brightness_smoothing"], scale=100)
        light_layout.addWidget(self.intensity)
        light_layout.addWidget(self.color_smooth)
        light_layout.addWidget(self.brightness_smooth)
        brightness_row = QHBoxLayout()
        self.brightness_enabled = QCheckBox("Match game brightness")
        self.brightness_range = QSpinBox()
        self.brightness_range.setRange(0, 50)
        self.brightness_range.setSuffix(" range")
        brightness_row.addWidget(self.brightness_enabled)
        brightness_row.addStretch()
        brightness_row.addWidget(QLabel("Strength"))
        brightness_row.addWidget(self.brightness_range)
        light_layout.addLayout(brightness_row)

        self.brightness_boost = SliderSetting(
            "Brightness boost",
            0,
            20,
            self.settings["brightness_boost"],
        )
        light_layout.addWidget(self.brightness_boost)

        boost_note = QLabel(
            "Adds extra brightness on top of game matching. "
            "Requires Match game brightness to be enabled."
        )
        boost_note.setObjectName("muted")
        boost_note.setWordWrap(True)
        light_layout.addWidget(boost_note)

        outer.addWidget(lighting)

        footer = QHBoxLayout()
        self.save_button = QPushButton("Save settings")
        self.save_button.clicked.connect(
            lambda: self.save_current_settings()
        )
        footer.addWidget(self.save_button)
        self.reset_button = QPushButton("Reset to defaults")
        self.reset_button.clicked.connect(self.reset_to_defaults)
        footer.addWidget(self.reset_button)
        footer.addStretch()
        self.apply_note = QLabel(
            "Most settings apply live. Capture rate requires restarting lighting."
        )
        self.apply_note.setObjectName("muted")
        footer.addWidget(self.apply_note)
        outer.addLayout(footer)

        log_box = QGroupBox("Activity")
        log_layout = QVBoxLayout(log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setMinimumHeight(110)
        self.log.setPlaceholderText("Application messages will appear here…")
        log_layout.addWidget(self.log)
        outer.addWidget(log_box, 1)

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #17191f; color: #e7e9ef; font-size: 10pt; }
            QLabel#title { font-size: 22pt; font-weight: 700; color: #ffffff; }
            QLabel#muted { color: #9aa2b2; font-size: 9pt; }
            QLabel#status { color: #f0b45b; font-weight: 600; }
            QLabel#captureTarget { color: #dce5f5; font-weight: 600; padding: 5px 0; }
            QGroupBox { border: 1px solid #343945; border-radius: 10px; margin-top: 10px; padding: 12px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #c5cce0; }
            QPushButton, QComboBox, QSpinBox { background: #292e39; border: 1px solid #424a5a; border-radius: 7px; padding: 8px 12px; }
            QPushButton:hover { background: #363e4c; }
            QPushButton:disabled { color: #747b89; background: #20232b; }
            QPushButton#primary { background: #6554c0; border-color: #8172d9; color: white; font-weight: 600; }
            QPushButton#primary:hover { background: #7665d2; }
            QComboBox, QSpinBox { min-width: 120px; }
            QSlider::groove:horizontal { height: 5px; background: #363d4a; border-radius: 2px; }
            QSlider::handle:horizontal { background: #a99af7; width: 15px; margin: -5px 0; border-radius: 7px; }
            QCheckBox { spacing: 8px; }
            QPlainTextEdit { background: #111319; border: 1px solid #303541; border-radius: 7px; color: #aebbd0; font-family: monospace; font-size: 9pt; }
        """)

    def _load_controls(self):
        self.capture_fps.setCurrentText(f"{self.settings['capture_fps']} FPS")
        self.update_fps.setCurrentText(f"{self.settings['color_update_fps']} FPS")
        self.intensity.slider.setValue(round(self.settings["lighting_intensity"] * 100))
        self.color_smooth.slider.setValue(round(self.settings["color_smoothing"] * 100))
        self.brightness_smooth.slider.setValue(round(self.settings["brightness_smoothing"] * 100))
        self.brightness_enabled.setChecked(bool(self.settings["brightness_enabled"]))
        self.brightness_range.setValue(int(self.settings["brightness_range"]))
        self.brightness_boost.slider.setValue(
        int(self.settings["brightness_boost"])
        )

    def _connect_autosave(self):
        # Performance dropdowns
        self.capture_fps.currentTextChanged.connect(self.schedule_autosave)
        self.update_fps.currentTextChanged.connect(self.schedule_autosave)

        # Lighting sliders
        self.intensity.slider.valueChanged.connect(self.schedule_autosave)
        self.color_smooth.slider.valueChanged.connect(self.schedule_autosave)
        self.brightness_smooth.slider.valueChanged.connect(self.schedule_autosave)
        self.brightness_boost.slider.valueChanged.connect(self.schedule_autosave)

        # Brightness controls
        self.brightness_enabled.toggled.connect(self.schedule_autosave)
        self.brightness_range.valueChanged.connect(self.schedule_autosave)


    def schedule_autosave(self, *_args):
        self.autosave_timer.start()

    def collect_settings(self):
        return {
            "capture_fps": int(self.capture_fps.currentText().split()[0]),
            "color_update_fps": int(self.update_fps.currentText().split()[0]),
            "lighting_intensity": self.intensity.value(),
            "color_smoothing": self.color_smooth.value(),
            "brightness_smoothing": self.brightness_smooth.value(),
            "brightness_range": self.brightness_range.value(),
            "brightness_enabled": self.brightness_enabled.isChecked(),
            "brightness_boost": int(self.brightness_boost.value()),
        }

    def save_current_settings(self, quiet=False):
        self.settings = self.collect_settings()
        try:
            save_settings(self.settings)
            if not quiet:
                self.log.appendPlainText(f"Settings saved to {SETTINGS_FILE}")
        except OSError as e:
            QMessageBox.critical(self, "Could not save settings", str(e))

    def reset_to_defaults(self):
        answer = QMessageBox.question(
            self,
            "Reset settings?",
            "Restore all lighting settings to their defaults and save them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.autosave_timer.stop()
        self.settings = DEFAULTS.copy()
        self._load_controls()
        self.save_current_settings(quiet=True)
        self.log.appendPlainText("Settings reset to defaults and saved.")
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.log.appendPlainText("Restart lighting to apply the default settings.")

    def start_lighting(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return

        if not BACKEND.exists():
            QMessageBox.critical(self, "Backend missing", f"Could not find {BACKEND}")
            return

        self.save_current_settings(quiet=True)
        self.log.clear()
        self.capture_target.setText("Waiting for the KDE window picker…")
        self.status.setText("●  Starting")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        if FROZEN:
            # Run the bundled backend executable.
            self.process.start(str(BACKEND), [])
        else:
            # Run the Python backend during development.
            self.process.start(sys.executable, [str(BACKEND)])

    def stop_lighting(self):
        pid = int(self.process.processId())
        if pid:
            try:
                os.kill(pid, signal.SIGINT)  # Let Python run its cleanup/finally block.
                self.log.appendPlainText("Stopping…")
            except ProcessLookupError:
                pass
        self.stop_button.setEnabled(False)

    def on_started(self):
        self.status.setText("●  Running")
        self.status.setStyleSheet("color: #78d6a0; font-weight: 600")
        self.log.appendPlainText("Lighting backend started. Complete the window picker.")

    def _consume_output(self, raw, is_error=False):
        text = bytes(raw).decode("utf-8", errors="replace")
        for line in text.replace("\r", "\n").splitlines():
            line = line.strip()
            if not line:
                continue
            self.log.appendPlainText(line)
            if "Selected game:" in line:
                self.capture_target.setText(line.replace("Selected game:", "Selected capture:").strip())
            elif "Live lighting active" in line:
                self.status.setText("●  Lighting active")
                self.status.setStyleSheet("color: #78d6a0; font-weight: 600")
            elif "No game window selected" in line:
                self.capture_target.setText("No window selected")
            elif "ERROR:" in line:
                self.status.setText("●  Error")
                self.status.setStyleSheet("color: #ff7777; font-weight: 600")

    def read_stdout(self):
        self._consume_output(self.process.readAllStandardOutput())

    def read_stderr(self):
        self._consume_output(self.process.readAllStandardError(), True)

    def on_finished(self, exit_code, exit_status):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if self.status.text() != "●  Error":
            self.status.setText("●  Stopped")
            self.status.setStyleSheet("color: #f0b45b; font-weight: 600")
        if exit_code:
            self.log.appendPlainText(f"Backend exited with code {exit_code}.")
        else:
            self.log.appendPlainText("Backend stopped.")

    def refresh_process_state(self):
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    def closeEvent(self, event):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            answer = QMessageBox.question(self, "Stop lighting?", "Lighting is running. Stop it and close the application?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.stop_lighting()
            if not self.process.waitForFinished(2500):
                self.process.kill()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Wayland Game Lighting")
    app.setFont(QFont("Noto Sans", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
