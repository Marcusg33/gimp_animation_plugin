# GIMP Animation Export Plugin

Exports GIMP layers as a video animation via ffmpeg. Supports MP4/H.264, WebM/VP9, and GIF output, with an interactive GTK3 settings dialog and automatic video preview on export.

Targets **GIMP 3.0.x** on Linux and macOS.

## Features

- Exports all layers (including nested layer groups) as animation frames
- Per-frame timing via `[Nms]` annotations in layer names (e.g. `"explosion [120ms]"`)
- Cumulative compositing mode (paint-on-canvas)
- Reverse frame order toggle
- Advanced ffmpeg controls: H.264 preset, pixel format, GIF dither algorithm, extra ffmpeg args
- Automatic video preview via mpv (Linux) or IINA (macOS)

## Requirements

- GIMP 3.0.x
- ffmpeg on `PATH`
- **Linux:** mpv (recommended), vlc, or xdg-open
- **macOS:** IINA (recommended), vlc, or QuickTime (`open`)

```bash
# Arch Linux
sudo pacman -S ffmpeg mpv

# macOS
brew install ffmpeg && brew install --cask iina
```

## Installation

### Quick install (Linux/macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/gimp-animation-plugin/main/install.sh | bash
```

### Manual install

**Linux:**
```bash
mkdir -p ~/.config/GIMP/3.0/plug-ins/export_animation
cp export_animation.py ~/.config/GIMP/3.0/plug-ins/export_animation/
chmod +x ~/.config/GIMP/3.0/plug-ins/export_animation/export_animation.py
```

**macOS:**
```bash
mkdir -p ~/Library/Application\ Support/GIMP/3.0/plug-ins/export_animation
cp export_animation.py ~/Library/Application\ Support/GIMP/3.0/plug-ins/export_animation/
chmod +x ~/Library/Application\ Support/GIMP/3.0/plug-ins/export_animation/export_animation.py
```

Restart GIMP. The plugin appears under **Filters → Animation → Export Layers as Video…**

> GIMP 3.x requires the plugin to live in its own subdirectory with the same name as the `.py` file (without extension).

## Usage

1. Open an `.xcf` file with layers to animate
2. Go to **Filters → Animation → Export Layers as Video…**
3. Set output path, format, and FPS in the **Export** tab
4. Optionally tune encoding in the **Advanced** tab
5. Click **Export & Preview** — the video opens automatically when done

### Layer naming

Layers are processed bottom-to-top (bottom layer = frame 1). Embed per-frame delay in layer names:

```
fire burst [120ms]   →  120 ms for this frame
background           →  uses global FPS setting
```

### Layer groups

Layer groups are flattened recursively — all leaf layers inside a group are treated as individual frames. Group layers themselves are not frames.

### Compositing modes

| Mode | Behaviour |
|------|-----------|
| Isolated (default) | Each layer is its own standalone frame |
| Cumulative | Each frame shows all layers up to that point (paint-on-canvas) |

## Output formats

| Format | Extension | Notes |
|--------|-----------|-------|
| MP4 (H.264) | `.mp4` | Best compatibility. CRF 0–51, lower = better quality. |
| WebM (VP9) | `.webm` | Open format. CRF 0–63. |
| GIF | `.gif` | Two-pass palette encoding for best quality. |

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv run pytest        # run tests
uv run pytest -v     # verbose
```

Tests mock all GIMP/GTK bindings and run without a GIMP process.

## Distribution

To share the plugin:

- **GitHub release:** tag a release and attach `export_animation.py` as a release asset — users download one file
- **Install script:** an `install.sh` that copies the file to the correct directory and sets permissions
- **AUR (Arch):** a PKGBUILD can automate install for Arch Linux users
- **Homebrew (macOS):** a formula can do the same for macOS users

There is no official GIMP 3.x plugin store yet. The community shares plugins via GitHub and forums (GIMP GitLab, gimpchat.com).

## License

MIT
