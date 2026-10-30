#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BUILD_DIR="$PROJECT_DIR/build/appimage"
PYI_DIST="$BUILD_DIR/pyinstaller-dist"
APPDIR="$BUILD_DIR/WaylandGameLighting.AppDir"
OUTPUT="$PROJECT_DIR/dist/Wayland-Game-Lighting-x86_64.AppImage"
APPIMAGETOOL="$PROJECT_DIR/tools/appimagetool"

cd "$PROJECT_DIR"

echo "==> Building GUI..."
.venv/bin/pyinstaller \
    --clean --noconfirm --onedir \
    --name wayland-vts-lighting \
    --distpath "$PYI_DIST" \
    --workpath "$BUILD_DIR/work-gui" \
    --specpath "$BUILD_DIR/spec-gui" \
    gui.py

echo "==> Building backend..."
.venv/bin/pyinstaller \
    --clean --noconfirm --onedir \
    --name wayland-vts-lighting-backend \
    --collect-all pipewire_capture \
    --distpath "$PYI_DIST" \
    --workpath "$BUILD_DIR/work-backend" \
    --specpath "$BUILD_DIR/spec-backend" \
    lighting.py

echo "==> Creating AppDir..."
rm -rf "$APPDIR"

GUI_DIR="$APPDIR/usr/lib/wayland-vts-lighting/gui"
BACKEND_DIR="$APPDIR/usr/lib/wayland-vts-lighting/backend"

mkdir -p "$GUI_DIR" "$BACKEND_DIR"
mkdir -p "$APPDIR/usr/share/applications"

cp -a "$PYI_DIST/wayland-vts-lighting/." "$GUI_DIR/"
cp -a "$PYI_DIST/wayland-vts-lighting-backend/." "$BACKEND_DIR/"

echo "==> Creating AppRun..."
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/usr/bin/env bash
set -e
APPDIR="$(dirname "$(readlink -f "$0")")"
exec "$APPDIR/usr/lib/wayland-vts-lighting/gui/wayland-vts-lighting" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

echo "==> Creating desktop entry..."
cat > "$APPDIR/wayland-game-lighting.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Wayland Game Lighting
Comment=Game lighting effects for VTube Studio using Wayland capture
Exec=AppRun
Icon=wayland-game-lighting
Terminal=false
Categories=Utility;
StartupNotify=true
DESKTOP

cp "$APPDIR/wayland-game-lighting.desktop" \
   "$APPDIR/usr/share/applications/"

echo "==> Creating application icon..."
cat > "$APPDIR/wayland-game-lighting.svg" <<'ICON'
<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <rect x="8" y="8" width="240" height="240" rx="52" fill="#17191f"/>
  <rect x="28" y="28" width="200" height="200" rx="38" fill="#292e39" stroke="#6554c0" stroke-width="8"/>
  <path d="M128 48 L128 208 M48 128 L208 128" stroke="#8172d9" stroke-width="10" stroke-linecap="round"/>
  <circle cx="128" cy="128" r="54" fill="#6554c0"/>
  <circle cx="128" cy="128" r="32" fill="#e7e9ef"/>
  <circle cx="128" cy="128" r="13" fill="#6554c0"/>
</svg>
ICON

ln -sf wayland-game-lighting.svg "$APPDIR/.DirIcon"

echo "==> Checking appimagetool..."
if [[ ! -x "$APPIMAGETOOL" ]]; then
    mkdir -p "$PROJECT_DIR/tools"
    curl -L \
      "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
      -o "$APPIMAGETOOL"
    chmod +x "$APPIMAGETOOL"
fi

mkdir -p "$PROJECT_DIR/dist"

echo "==> Creating AppImage..."
ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "$OUTPUT"

echo
echo "Build complete:"
echo "$OUTPUT"
