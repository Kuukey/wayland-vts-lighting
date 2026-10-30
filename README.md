# Wayland Game Lighting — desktop frontend

A small PySide6 desktop control panel for the existing PipeWire → VTube Studio lighting backend. Designed for KDE Plasma on Wayland; it does not use X11 or XWayland capture APIs.

## What it controls

- **Select window & start** opens the existing KDE ScreenCast portal picker through `PortalCapture().select_window()`.
- Capture FPS and VTube Studio tint-update FPS.
- Lighting intensity, color smoothing, brightness smoothing.
- Optional game-brightness matching and its range.
- Start/stop status and backend activity log.
- A vertically scrollable layout for shorter windows.
- **Reset to defaults** restores and saves all default settings.

Settings are saved to `~/.config/wayland-vts-lighting/settings.json` and applied on the next start. The backend still uses the existing VTube Studio token at `~/.config/wayland-vts-lighting/token`.

## Install into the existing project

1. Extract these files into `~/Projects/wayland-vts-lighting`, replacing `lighting.py` only after keeping a backup if you want to preserve the current copy.
2. From that directory, run:

   ```bash
   ./install_gui.sh
   ```

3. Launch:

   ```bash
   ./launch.sh
   ```

The installer automatically detects `.venv` or `venv`. If your virtual environment has a different name, run `PYTHON=/path/to/python ./install_gui.sh` and use the same `PYTHON` override with `launch.sh`.

## Notes

- The first launch may prompt VTube Studio to authorize the plugin if its token has not been saved yet.
- Clicking **Select window & start** opens the desktop portal. Choose the game window in that dialog.
- Settings are intentionally locked in for each run; stop and restart lighting to apply changes. The reset button saves defaults immediately, but a running backend must be restarted to use them.
- Capture/update FPS are targets, not guarantees. Real rates depend on PipeWire and VTube Studio response latency.
- This package does not change OBS, Spout2PW, VTube Studio's model, or the existing token.
