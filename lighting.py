import json
import threading
import time
import uuid

import numpy as np
from pathlib import Path
from websockets.sync.client import connect
from pipewire_capture import PortalCapture, CaptureStream, is_available


# -----------------------------
# Configuration
# -----------------------------

PLUGIN_NAME = "Wayland Game Lighting"
PLUGIN_DEVELOPER = "Kuuki"

TOKEN_FILE = (
    Path.home()
    / ".config"
    / "wayland-vts-lighting"
    / "token"
)

VTS_URL = "ws://127.0.0.1:8001"

# Target capture and color-update rates.
# Actual rates depend on PipeWire and VTube Studio response times.
CAPTURE_FPS = 40
COLOR_UPDATE_FPS = 40

CAPTURE_INTERVAL = 1.0 / CAPTURE_FPS
COLOR_UPDATE_INTERVAL = 1.0 / COLOR_UPDATE_FPS

# Capture-thread smoothing.
COLOR_SMOOTHING = 0.22
BRIGHTNESS_SMOOTHING = 0.18

# Color matching.
LIGHTING_INTENSITY = 0.65

# Brightness matching.
BRIGHTNESS_INTERVAL = 0.35
BRIGHTNESS_RANGE = 12
BRIGHTNESS_CENTER = 128
BRIGHTNESS_UPDATE_THRESHOLD = 2
BRIGHTNESS_BOOST = 0

# VTube Studio Color Grading.
COLOR_GRADING_STRENGTH = 1.0
POST_PROCESSING_FADE_TIME = 0.3


SETTINGS_FILE = Path.home() / ".config" / "wayland-vts-lighting" / "settings.json"

try:
    _settings = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
except (OSError, json.JSONDecodeError):
    _settings = {}

CAPTURE_FPS = int(np.clip(_settings.get("capture_fps", CAPTURE_FPS), 1, 120))
COLOR_UPDATE_FPS = int(np.clip(_settings.get("color_update_fps", COLOR_UPDATE_FPS), 1, 120))
CAPTURE_INTERVAL = 1.0 / CAPTURE_FPS
COLOR_UPDATE_INTERVAL = 1.0 / COLOR_UPDATE_FPS

COLOR_SMOOTHING = float(np.clip(_settings.get("color_smoothing", COLOR_SMOOTHING), 0.01, 1.0))
BRIGHTNESS_SMOOTHING = float(np.clip(_settings.get("brightness_smoothing", BRIGHTNESS_SMOOTHING), 0.01, 1.0))
LIGHTING_INTENSITY = float(np.clip(_settings.get("lighting_intensity", LIGHTING_INTENSITY), 0.0, 1.0))
BRIGHTNESS_RANGE = int(np.clip(_settings.get("brightness_range", BRIGHTNESS_RANGE), 0, 50))
BRIGHTNESS_ENABLED = bool(_settings.get("brightness_enabled", True))
BRIGHTNESS_BOOST = int(np.clip(_settings.get("brightness_boost", BRIGHTNESS_BOOST), 0, 20))

try:
    SETTINGS_MTIME_NS = SETTINGS_FILE.stat().st_mtime_ns
except OSError:
    SETTINGS_MTIME_NS = None


def reload_settings_if_changed():
    """Reload settings when the GUI saves a new settings.json."""

    global SETTINGS_MTIME_NS
    global COLOR_UPDATE_FPS, COLOR_UPDATE_INTERVAL
    global COLOR_SMOOTHING, BRIGHTNESS_SMOOTHING
    global LIGHTING_INTENSITY, BRIGHTNESS_RANGE
    global BRIGHTNESS_ENABLED, BRIGHTNESS_BOOST

    try:
        mtime = SETTINGS_FILE.stat().st_mtime_ns
    except OSError:
        return None

    if mtime == SETTINGS_MTIME_NS:
        return None

    try:
        settings = json.loads(SETTINGS_FILE.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        # The GUI may still be writing the file. Retry next iteration.
        return None

    old_enabled = BRIGHTNESS_ENABLED
    old_brightness_values = (
        BRIGHTNESS_RANGE,
        BRIGHTNESS_BOOST,
    )
    old_color_update_fps = COLOR_UPDATE_FPS

    # Capture FPS cannot change on an already-running PipeWire stream.
    requested_capture_fps = int(
        np.clip(settings.get("capture_fps", CAPTURE_FPS), 1, 120)
    )

    COLOR_UPDATE_FPS = int(
        np.clip(settings.get("color_update_fps", COLOR_UPDATE_FPS), 1, 120)
    )
    COLOR_UPDATE_INTERVAL = 1.0 / COLOR_UPDATE_FPS

    COLOR_SMOOTHING = float(
        np.clip(settings.get("color_smoothing", COLOR_SMOOTHING), 0.01, 1.0)
    )
    BRIGHTNESS_SMOOTHING = float(
        np.clip(settings.get("brightness_smoothing", BRIGHTNESS_SMOOTHING), 0.01, 1.0)
    )
    LIGHTING_INTENSITY = float(
        np.clip(settings.get("lighting_intensity", LIGHTING_INTENSITY), 0.0, 1.0)
    )
    BRIGHTNESS_RANGE = int(
        np.clip(settings.get("brightness_range", BRIGHTNESS_RANGE), 0, 50)
    )
    BRIGHTNESS_ENABLED = bool(
        settings.get("brightness_enabled", BRIGHTNESS_ENABLED)
    )
    BRIGHTNESS_BOOST = int(
        np.clip(settings.get("brightness_boost", BRIGHTNESS_BOOST), 0, 20)
    )

    SETTINGS_MTIME_NS = mtime

    return {
        "brightness_enabled_changed": old_enabled != BRIGHTNESS_ENABLED,
        "brightness_values_changed": old_brightness_values != (
            BRIGHTNESS_RANGE,
            BRIGHTNESS_BOOST,
        ),
        "color_update_fps_changed": old_color_update_fps != COLOR_UPDATE_FPS,
        "capture_fps_restart_required": requested_capture_fps != CAPTURE_FPS,
    }



# -----------------------------
# VTube Studio API
# -----------------------------

def make_request(ws, message_type, data=None):
    payload = {
        "apiName": "VTubeStudioPublicAPI",
        "apiVersion": "1.0",
        "requestID": str(uuid.uuid4()),
        "messageType": message_type,
        "data": data or {},
    }

    ws.send(json.dumps(payload))
    response = json.loads(ws.recv(timeout=10))

    if response.get("messageType") == "APIError":
        raise RuntimeError(
            response.get("data", {}).get(
                "message",
                "VTube Studio API error",
            )
        )

    return response


def authenticate(ws):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
    else:
        print("Requesting VTube Studio authorization...")

        response = make_request(
            ws,
            "AuthenticationTokenRequest",
            {
                "pluginName": PLUGIN_NAME,
                "pluginDeveloper": PLUGIN_DEVELOPER,
            },
        )

        token = response["data"]["authenticationToken"]
        TOKEN_FILE.write_text(token)
        TOKEN_FILE.chmod(0o600)

    response = make_request(
        ws,
        "AuthenticationRequest",
        {
            "pluginName": PLUGIN_NAME,
            "pluginDeveloper": PLUGIN_DEVELOPER,
            "authenticationToken": token,
        },
    )

    if not response.get("data", {}).get("authenticated"):
        raise RuntimeError("VTube Studio authentication failed.")

    print("Connected and authenticated with VTube Studio.")


# -----------------------------
# Model color and brightness
# -----------------------------

def set_tint(ws, rgb):
    red, green, blue = [
        int(np.clip(value, 0, 255))
        for value in rgb
    ]

    make_request(
        ws,
        "ColorTintRequest",
        {
            "colorTint": {
                "colorR": red,
                "colorG": green,
                "colorB": blue,
                "colorA": 255,
                "mixWithSceneLightingColor": 1,
            },
            "artMeshMatcher": {
                "tintAll": True,
            },
        },
    )


def set_model_brightness(ws, brightness, enabled=True):
    make_request(
        ws,
        "PostProcessingUpdateRequest",
        {
            "postProcessingOn": enabled,
            "setPostProcessingPreset": False,
            "setPostProcessingValues": True,
            "presetToSet": "",
            "postProcessingFadeTime": POST_PROCESSING_FADE_TIME,
            "setAllOtherValuesToDefault": False,
            "usingRestrictedEffects": False,
            "randomizeAll": False,
            "randomizeAllChaosLevel": 0.0,
            "postProcessingValues": [
                {
                    "configID": "ColorGrading_Strength",
                    "configValue": str(COLOR_GRADING_STRENGTH),
                },
                {
                    "configID": "ColorGrading_Brightness",
                    "configValue": str(brightness),
                },
            ],
        },
    )


def restore_vts(ws, post_processing_enabled):
    print("\nRestoring neutral lighting...")

    try:
        set_tint(ws, (255, 255, 255))
        print("Neutral tint restored.")
    except Exception as error:
        print(f"Could not restore tint: {error}")

    if post_processing_enabled:
        try:
            set_model_brightness(
                ws,
                brightness=0,
                enabled=False,
            )
            print("Post-processing disabled and brightness reset.")
        except Exception as error:
            print(f"Could not restore brightness: {error}")


# -----------------------------
# Lighting calculations
# -----------------------------

def sample_game(frame):
    """Return average RGB and perceived luminance from a BGRA frame."""

    sample = frame[::4, ::4, :3]

    blue, green, red = np.mean(
        sample,
        axis=(0, 1),
    )

    rgb = np.array(
        [red, green, blue],
        dtype=float,
    )

    luminance = (
        0.2126 * red
        + 0.7152 * green
        + 0.0722 * blue
    )

    return rgb, luminance


def calculate_tint(rgb):
    """Blend sampled game color with neutral white."""

    return (
        255 * (1.0 - LIGHTING_INTENSITY)
        + rgb * LIGHTING_INTENSITY
    )


def calculate_brightness(luminance):
    """Map game luminance to VTS brightness, with an optional brightness boost."""

    brightness = (
        (luminance - BRIGHTNESS_CENTER)
        / BRIGHTNESS_CENTER
    ) * BRIGHTNESS_RANGE

    matched_brightness = int(
        np.clip(
            round(brightness),
            -BRIGHTNESS_RANGE,
            BRIGHTNESS_RANGE,
        )
    )

    return matched_brightness + BRIGHTNESS_BOOST


# -----------------------------
# Independent game-capture thread
# -----------------------------

def capture_worker(stream, shared, lock, stop_event, finished_event):
    """
    Capture and process frames without waiting for VTube Studio.

    The shared dictionary stores only the newest sample. Older samples
    are overwritten so the API loop never builds up a backlog.
    """

    smoothed_rgb = None
    smoothed_luminance = None

    try:
        while not stop_event.is_set() and not stream.window_invalid:
            frame = stream.get_frame()

            if frame is None:
                time.sleep(0.002)
                continue

            current_rgb, current_luminance = sample_game(frame)

            if smoothed_rgb is None:
                smoothed_rgb = current_rgb
                smoothed_luminance = current_luminance
            else:
                smoothed_rgb = (
                    smoothed_rgb * (1.0 - COLOR_SMOOTHING)
                    + current_rgb * COLOR_SMOOTHING
                )

                smoothed_luminance = (
                    smoothed_luminance * (1.0 - BRIGHTNESS_SMOOTHING)
                    + current_luminance * BRIGHTNESS_SMOOTHING
                )

            with lock:
                shared["rgb"] = smoothed_rgb.copy()
                shared["luminance"] = float(smoothed_luminance)
                shared["timestamp"] = time.monotonic()

    except Exception as error:
        with lock:
            shared["error"] = repr(error)

        stop_event.set()

    finally:
        finished_event.set()


# -----------------------------
# Main application
# -----------------------------

def main():
    if not is_available():
        raise RuntimeError("Wayland ScreenCast portal unavailable.")

    print("Wayland Game Lighting")
    print("---------------------")
    print("Connecting to VTube Studio...")

    with connect(VTS_URL, open_timeout=5) as ws:
        authenticate(ws)

        print("\nOpening game selection dialog...")
        session = PortalCapture().select_window()

        if session is None:
            print("No game window selected.")
            return

        stream = None
        capture_thread = None
        stop_event = threading.Event()
        finished_event = threading.Event()
        post_processing_enabled = False

        try:
            print(
                f"Selected game: "
                f"{session.width}x{session.height}"
            )

            stream = CaptureStream(
                session.fd,
                session.node_id,
                session.width,
                session.height,
                capture_interval=CAPTURE_INTERVAL,
            )

            stream.start()

            shared = {
                "rgb": None,
                "luminance": None,
                "timestamp": 0.0,
                "error": None,
            }

            lock = threading.Lock()

            capture_thread = threading.Thread(
                target=capture_worker,
                args=(
                    stream,
                    shared,
                    lock,
                    stop_event,
                    finished_event,
                ),
                name="game-capture",
                daemon=True,
            )

            capture_thread.start()

            # Enable brightness processing only when requested in settings.
            if BRIGHTNESS_ENABLED:
                set_model_brightness(
                    ws,
                    brightness=0,
                    enabled=True,
                )
                post_processing_enabled = True

            print("\nLive lighting active!")
            print(f"Capture target: {CAPTURE_FPS} FPS")
            print(f"Color update target: {COLOR_UPDATE_FPS} FPS")
            print(f"Brightness matching: {'ON' if BRIGHTNESS_ENABLED else 'OFF'}")
            print("Press Ctrl+C to stop.\n")

            next_color_update = time.monotonic()
            last_brightness_update = 0.0
            last_brightness_sent = None
            last_status_update = 0.0

            while not stop_event.is_set() and not stream.window_invalid:
                with lock:
                    rgb = (
                        shared["rgb"].copy()
                        if shared["rgb"] is not None
                        else None
                    )
                    luminance = shared["luminance"]
                    sample_timestamp = shared["timestamp"]
                    capture_error = shared["error"]

                if capture_error is not None:
                    raise RuntimeError(
                        f"Game capture thread failed: {capture_error}"
                    )

                if rgb is None:
                    if finished_event.is_set():
                        break

                    time.sleep(0.002)
                    continue

                now = time.monotonic()

                # Check whether the GUI has saved new settings.
                changes = reload_settings_if_changed()

                if changes is not None:
                    if changes["capture_fps_restart_required"]:
                        print(
                            "\nCapture FPS changed. Restart lighting "
                            "to apply the new capture rate."
                        )

                    if changes["color_update_fps_changed"]:
                        next_color_update = 0.0
                        print(
                            f"\nVTube Studio update rate changed to "
                            f"{COLOR_UPDATE_FPS} FPS."
                        )

                    # Enable or disable VTS brightness processing immediately.
                    if changes["brightness_enabled_changed"]:
                        if BRIGHTNESS_ENABLED:
                            current_brightness = calculate_brightness(luminance)

                            set_model_brightness(
                                ws,
                                brightness=current_brightness,
                                enabled=True,
                            )

                            post_processing_enabled = True
                            last_brightness_sent = current_brightness

                            print("\nBrightness matching enabled.")
                        else:
                            set_model_brightness(
                                ws,
                                brightness=0,
                                enabled=False,
                            )

                            post_processing_enabled = False
                            last_brightness_sent = None

                            print("\nBrightness matching disabled.")

                        last_brightness_update = time.monotonic()

                    # Force a brightness update when range or boost changes.
                    elif (
                        BRIGHTNESS_ENABLED
                        and changes["brightness_values_changed"]
                    ):
                        last_brightness_sent = None
                        last_brightness_update = 0.0

                # Detect a capture thread that has stopped updating.
                if now - sample_timestamp > 2.0:
                    raise RuntimeError(
                        "Game capture stopped producing fresh frames."
                    )

                if now >= next_color_update:
                    # Always use the newest available sample.
                    set_tint(ws, calculate_tint(rgb))

                    next_color_update += COLOR_UPDATE_INTERVAL

                    # Skip missed deadlines rather than sending stale updates.
                    if now >= next_color_update:
                        next_color_update = now + COLOR_UPDATE_INTERVAL

                # Brightness uses a slower update cadence than color.
                if BRIGHTNESS_ENABLED and now - last_brightness_update >= BRIGHTNESS_INTERVAL:
                    brightness = calculate_brightness(luminance)

                    should_update_brightness = (
                        last_brightness_sent is None
                        or abs(
                            brightness - last_brightness_sent
                        ) >= BRIGHTNESS_UPDATE_THRESHOLD
                    )

                    if should_update_brightness:
                        set_model_brightness(
                            ws,
                            brightness=brightness,
                            enabled=True,
                        )
                        last_brightness_sent = brightness

                    last_brightness_update = time.monotonic()

                # Keep terminal output from becoming another source of overhead.
                if now - last_status_update >= 0.5:
                    red, green, blue = [
                        int(value) for value in rgb
                    ]

                    current_brightness = calculate_brightness(luminance)

                    print(
                        f"\rRGB: {red:3d} {green:3d} {blue:3d}"
                        f" | Luminance: {luminance:5.1f}"
                        f" | VTS Brightness: {current_brightness:+3d}",
                        end="",
                        flush=True,
                    )

                    last_status_update = now

                # Yield briefly; the capture thread runs independently.
                time.sleep(0.001)

            with lock:
                capture_error = shared["error"]

            if capture_error is not None:
                raise RuntimeError(
                    f"Game capture thread failed: {capture_error}"
                )

            print("\nGame window closed.")

        except KeyboardInterrupt:
            print("\nStopping lighting...")

        finally:
            stop_event.set()

            # Give the capture thread a chance to exit before stopping the stream.
            if capture_thread is not None and capture_thread.is_alive():
                capture_thread.join(timeout=1.0)

            if stream is not None:
                stream.stop()

            # If stopping the stream unblocked get_frame(), wait once more.
            if capture_thread is not None and capture_thread.is_alive():
                capture_thread.join(timeout=1.0)

            session.close()
            restore_vts(ws, post_processing_enabled)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"\nERROR: {error}")
