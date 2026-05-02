#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="export_animation"
PLUGIN_FILE="${PLUGIN_NAME}.py"

# ── Resolve plugin source ────────────────────────────────────────────────────
# Support two modes:
#   1. Run from the cloned repo:  ./install.sh
#   2. Piped from curl:           curl -fsSL .../install.sh | bash
#      In this case $0 is not a file path, so we download the plugin too.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-/dev/null}")" 2>/dev/null && pwd || true)"
LOCAL_PLUGIN="${SCRIPT_DIR}/${PLUGIN_FILE}"

if [[ -f "$LOCAL_PLUGIN" ]]; then
    SOURCE="$LOCAL_PLUGIN"
else
    # Being piped via curl — download the plugin alongside this script
    GITHUB_RAW="https://raw.githubusercontent.com/YOUR_USERNAME/gimp-animation-plugin/main/${PLUGIN_FILE}"
    TMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$TMP_DIR"' EXIT
    echo "Downloading ${PLUGIN_FILE}…"
    curl -fsSL "$GITHUB_RAW" -o "${TMP_DIR}/${PLUGIN_FILE}"
    SOURCE="${TMP_DIR}/${PLUGIN_FILE}"
fi

# ── Detect OS and set install path ───────────────────────────────────────────

OS="$(uname -s)"

case "$OS" in
    Linux)
        GIMP_PLUGIN_DIR="${HOME}/.config/GIMP/3.0/plug-ins/${PLUGIN_NAME}"
        ;;
    Darwin)
        GIMP_PLUGIN_DIR="${HOME}/Library/Application Support/GIMP/3.0/plug-ins/${PLUGIN_NAME}"
        ;;
    *)
        echo "Unsupported OS: ${OS}" >&2
        echo "Supported: Linux, macOS" >&2
        exit 1
        ;;
esac

# ── Check dependencies ───────────────────────────────────────────────────────

MISSING=()

if ! command -v gimp &>/dev/null && [[ ! -d "/Applications/GIMP.app" ]]; then
    MISSING+=("gimp")
fi

if ! command -v ffmpeg &>/dev/null; then
    MISSING+=("ffmpeg")
fi

if [[ "${#MISSING[@]}" -gt 0 ]]; then
    echo ""
    echo "Warning: the following dependencies were not found on PATH:"
    for dep in "${MISSING[@]}"; do
        echo "  - $dep"
    done
    echo ""
    if [[ "$OS" == "Linux" ]]; then
        echo "Install with:  sudo pacman -S ${MISSING[*]}   (Arch)"
        echo "               sudo apt install ${MISSING[*]}  (Debian/Ubuntu)"
    else
        echo "Install with:  brew install ffmpeg"
        echo "               brew install --cask gimp"
    fi
    echo ""
    read -r -p "Continue installation anyway? [y/N] " REPLY
    [[ "${REPLY,,}" == "y" ]] || { echo "Aborted."; exit 1; }
fi

# ── Install ──────────────────────────────────────────────────────────────────

echo ""
echo "Installing to: ${GIMP_PLUGIN_DIR}"
mkdir -p "$GIMP_PLUGIN_DIR"
cp "$SOURCE" "${GIMP_PLUGIN_DIR}/${PLUGIN_FILE}"
chmod +x "${GIMP_PLUGIN_DIR}/${PLUGIN_FILE}"

echo ""
echo "Done. Restart GIMP and find the plugin under:"
echo "  Filters → Animation → Export Layers as Video…"
