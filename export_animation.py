#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_animation.py — GIMP 3.x Plugin
=======================================
Exports GIMP layers as an animation via ffmpeg, with a GTK3 settings dialog
and cross-platform video preview (mpv on Linux, IINA/QuickTime on macOS).

INSTALLATION
------------
Linux (GIMP 3.x):
    mkdir -p ~/.config/GIMP/3.0/plug-ins/export_animation
    cp export_animation.py ~/.config/GIMP/3.0/plug-ins/export_animation/
    chmod +x ~/.config/GIMP/3.0/plug-ins/export_animation/export_animation.py

macOS (GIMP 3.x):
    mkdir -p ~/Library/Application\\ Support/GIMP/3.0/plug-ins/export_animation
    cp export_animation.py ~/Library/Application\\ Support/GIMP/3.0/plug-ins/export_animation/
    chmod +x ~/Library/Application\\ Support/GIMP/3.0/plug-ins/export_animation/export_animation.py

GIMP 3.x requires the plugin to live in its own subdirectory with the same
name as the .py file (without extension). The directory name must match.

DEPENDENCIES
------------
- ffmpeg on PATH, or set FFMPEG_PATH constant below
- Linux:  mpv (recommended), vlc, or xdg-open as fallback
- macOS:  IINA (recommended), vlc, or 'open' (QuickTime) as fallback

Install:
    Arch Linux: sudo pacman -S ffmpeg mpv
    macOS:      brew install ffmpeg && brew install --cask iina

LAYER NAMING
------------
Layers are processed bottom-to-top (bottom layer = frame 1).
Per-frame delay can be embedded in layer names:
    "fire layer [120ms]"  → 120 ms for that frame
    "background"          → uses global FPS setting

COMPOSITING MODES
-----------------
- Isolated:     each layer is its own standalone frame
- Cumulative:   each frame shows all layers up to that point (paint-on-canvas)
"""

import sys
import os
import re
import shlex
import platform
import shutil
import subprocess
import tempfile

import gi
gi.require_version("Gimp",   "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gtk",    "3.0")
gi.require_version("GLib",   "2.0")

from gi.repository import Gimp, GimpUi, GLib, Gio
from gi.repository import Gtk

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"

PLAYER_CANDIDATES = {
    "Linux":  ["mpv", "vlc", "xdg-open"],
    "Darwin": ["iina", "vlc", "open"],
}

# Per-player loop flag.  Players not listed here get no loop flag.
# iina wraps mpv and requires the --mpv- prefix for mpv options.
PLAYER_LOOP_FLAG = {
    "mpv":  "--loop-file=inf",
    "iina": "--mpv-loop-file=inf",
}

SYSTEM = platform.system()

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def find_player():
    candidates = PLAYER_CANDIDATES.get(SYSTEM, ["vlc"])
    for p in candidates:
        if shutil.which(p):
            return p
    return None


def parse_layer_delay_ms(name):
    """Extract [Nms] annotation from layer name. Returns int or None."""
    m = re.search(r"\[(\d+)ms\]", name, re.IGNORECASE)
    return int(m.group(1)) if m else None


def ms_to_ffmpeg_duration(ms):
    return f"{ms / 1000:.6f}"


def open_preview(output_path):
    player = find_player()
    if not player:
        return False
    try:
        if player == "open":
            subprocess.Popen(["open", output_path])
        else:
            loop_flag = PLAYER_LOOP_FLAG.get(player)
            cmd = [player] + ([loop_flag] if loop_flag else []) + [output_path]
            try:
                subprocess.Popen(cmd)
            except (FileNotFoundError, TypeError):
                subprocess.Popen([player, output_path])
        return True
    except Exception:
        return False


def set_layer_visible_recursive(layer, visible):
    """Show or hide a layer and all its descendants."""
    layer.set_visible(visible)
    try:
        for child in layer.get_children():
            set_layer_visible_recursive(child, visible)
    except AttributeError:
        pass


def _collect_leaf_layers(layers):
    """Return all non-group layers from a bottom-to-top list, recursing into groups.

    Input must already be in bottom-to-top order.
    Group children from get_children() are top-to-bottom, so they are reversed
    before recursing to preserve the overall bottom-to-top ordering.
    """
    result = []
    for layer in layers:
        children = []
        try:
            children = list(layer.get_children())
        except AttributeError:
            pass
        if children:
            result.extend(_collect_leaf_layers(children[::-1]))
        else:
            result.append(layer)
    return result


def _show_with_ancestors(layer):
    """Make a leaf layer visible and walk up its parent chain showing each group."""
    layer.set_visible(True)
    try:
        parent = layer.get_parent()
        while parent is not None:
            parent.set_visible(True)
            parent = parent.get_parent()
    except AttributeError:
        pass


def _save_png(image, path):
    """Export a flattened image to PNG via the GIMP 3.0 PDB API.

    'file-png-export' takes the active drawable from the image implicitly;
    no drawable property exists on its config.
    """
    pdb = Gimp.get_pdb()
    proc = pdb.lookup_procedure('file-png-export')
    if proc is None:
        raise RuntimeError("'file-png-export' not found in PDB — check GIMP installation")
    config = proc.create_config()
    config.set_property('run-mode', Gimp.RunMode.NONINTERACTIVE)
    config.set_property('image',    image)
    config.set_property('file',     Gio.File.new_for_path(path))
    result = proc.run(config)
    status = result.index(0)
    if status != Gimp.PDBStatusType.SUCCESS:
        raise RuntimeError(f"file-png-export failed (status={status}) for {path!r}")


# ---------------------------------------------------------------------------
# Frame export
# ---------------------------------------------------------------------------

def export_frame(image, layer_index, tmpdir, flatten_below):
    """Export a single frame as PNG. layer_index is into the leaf-layer list."""
    tmp_image = image.duplicate()

    # Hide everything at every level first, then selectively reveal
    for top in tmp_image.get_layers():
        set_layer_visible_recursive(top, False)

    tmp_leaves = _collect_leaf_layers(list(tmp_image.get_layers())[::-1])

    if flatten_below:
        for j, leaf in enumerate(tmp_leaves):
            if j <= layer_index:
                _show_with_ancestors(leaf)
    else:
        _show_with_ancestors(tmp_leaves[layer_index])

    tmp_image.flatten()
    frame_path = os.path.join(tmpdir, f"frame_{layer_index:04d}.png")
    _save_png(tmp_image, frame_path)
    tmp_image.delete()
    return frame_path


def export_all_frames(image, tmpdir, default_fps, flatten_below, use_var_time,
                      reverse=False, progress_cb=None):
    """Export all leaf layers as PNG frames. Returns list of (path, delay_ms)."""
    frame_layers = _collect_leaf_layers(list(image.get_layers())[::-1])
    if reverse:
        frame_layers = frame_layers[::-1]
    default_ms = int(1000 / default_fps)
    frames = []

    for i, layer in enumerate(frame_layers):
        if progress_cb:
            progress_cb(i, len(frame_layers), f"Exporting frame {i + 1} of {len(frame_layers)}…")

        delay_ms = parse_layer_delay_ms(layer.get_name()) if use_var_time else None
        if delay_ms is None:
            delay_ms = default_ms

        path = export_frame(image, i, tmpdir, flatten_below)
        frames.append((path, delay_ms))

    return frames


# ---------------------------------------------------------------------------
# ffmpeg encoding
# ---------------------------------------------------------------------------

def write_concat_list(frames, concat_path):
    """Write an ffmpeg concat demuxer file for variable-duration input."""
    with open(concat_path, "w") as f:
        for png_path, delay_ms in frames:
            f.write(f"file '{png_path}'\n")
            f.write(f"duration {ms_to_ffmpeg_duration(delay_ms)}\n")
        # ffmpeg concat requires last file repeated without a duration
        if frames:
            f.write(f"file '{frames[-1][0]}'\n")


def run_ffmpeg(frames, output_path, fps, codec, quality, use_variable_timing,
               preset=None, pixel_fmt="yuv420p",
               gif_dither="paletteuse=dither=bayer:bayer_scale=5",
               extra_args=None):
    if not frames:
        raise RuntimeError("No frames to encode.")

    tmpdir = os.path.dirname(frames[0][0])
    ext    = os.path.splitext(output_path)[1].lower()

    if ext == ".gif":
        _encode_gif(frames, output_path, tmpdir, gif_dither, extra_args)
    elif use_variable_timing:
        _encode_variable(frames, output_path, tmpdir, codec, quality, preset, pixel_fmt, extra_args)
    else:
        _encode_fixed(frames, output_path, tmpdir, fps, codec, quality, preset, pixel_fmt, extra_args)


def _encode_gif(frames, output_path, tmpdir,
                gif_dither="paletteuse=dither=bayer:bayer_scale=5",
                extra_args=None):
    palette = os.path.join(tmpdir, "palette.png")
    concat  = os.path.join(tmpdir, "frames.txt")
    write_concat_list(frames, concat)

    # Pass 1 — generate optimal palette
    subprocess.run([
        FFMPEG_PATH, "-y",
        "-f", "concat", "-safe", "0", "-i", concat,
        "-vf", "palettegen=stats_mode=full",
        palette,
    ], check=True, capture_output=True)

    # Pass 2 — encode with palette dithering
    subprocess.run([
        FFMPEG_PATH, "-y",
        "-f", "concat", "-safe", "0", "-i", concat,
        "-i", palette,
        "-lavfi", gif_dither,
        *(extra_args or []),
        output_path,
    ], check=True, capture_output=True)


def _video_args(codec, quality, preset=None, pixel_fmt="yuv420p"):
    if codec == "h264":
        args = ["-c:v", "libx264", "-pix_fmt", pixel_fmt, "-crf", str(quality)]
        if preset:
            args += ["-preset", preset]
        return args
    else:  # vp9
        args = ["-c:v", "libvpx-vp9", "-crf", str(quality), "-b:v", "0"]
        if pixel_fmt != "yuv420p":
            args += ["-pix_fmt", pixel_fmt]
        return args


def _encode_variable(frames, output_path, tmpdir, codec, quality,
                     preset=None, pixel_fmt="yuv420p", extra_args=None):
    concat = os.path.join(tmpdir, "frames.txt")
    write_concat_list(frames, concat)
    subprocess.run([
        FFMPEG_PATH, "-y",
        "-f", "concat", "-safe", "0", "-i", concat,
        *_video_args(codec, quality, preset, pixel_fmt),
        *(extra_args or []),
        output_path,
    ], check=True, capture_output=True)


def _encode_fixed(frames, output_path, tmpdir, fps, codec, quality,
                  preset=None, pixel_fmt="yuv420p", extra_args=None):
    pattern = os.path.join(tmpdir, "frame_%04d.png")
    subprocess.run([
        FFMPEG_PATH, "-y",
        "-framerate", str(fps),
        "-i", pattern,
        *_video_args(codec, quality, preset, pixel_fmt),
        *(extra_args or []),
        output_path,
    ], check=True, capture_output=True)


# ---------------------------------------------------------------------------
# GTK3 Settings Dialog
# ---------------------------------------------------------------------------

FORMAT_DATA = {
    #  label           ext      codec
    "MP4 (H.264)":  (".mp4",  "h264"),
    "WebM (VP9)":   (".webm", "vp9"),
    "GIF":          (".gif",  "gif"),
}
FORMAT_LABELS = list(FORMAT_DATA.keys())

H264_PRESETS = [
    "ultrafast", "superfast", "veryfast", "faster",
    "fast", "medium", "slow", "slower", "veryslow",
]

PIXEL_FORMATS = ["yuv420p", "yuv444p"]

GIF_DITHER_OPTIONS = {
    "Bayer scale 5 (default)": "paletteuse=dither=bayer:bayer_scale=5",
    "Bayer scale 3":           "paletteuse=dither=bayer:bayer_scale=3",
    "Bayer scale 7":           "paletteuse=dither=bayer:bayer_scale=7",
    "Sierra-2-4A":             "paletteuse=dither=sierra2_4a",
    "Floyd-Steinberg":         "paletteuse=dither=floyd_steinberg",
    "None":                    "paletteuse=dither=none",
}


class ExportDialog(Gtk.Dialog):

    def __init__(self, parent, image):
        super().__init__(
            title="Export Layers as Animation",
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.image = image
        self.set_default_size(480, -1)
        self.set_border_width(12)

        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        ok_btn = self.add_button("Export & Preview", Gtk.ResponseType.OK)
        ok_btn.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)

        self._build_ui()

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self):
        box = self.get_content_area()
        box.set_spacing(12)

        # Info banner
        n = len(_collect_leaf_layers(list(self.image.get_layers())[::-1]))
        info = Gtk.Label()
        info.set_markup(
            f"<b>{n} layer{'s' if n != 1 else ''}</b> → "
            f"{'frames' if n != 1 else 'one frame'}.\n"
            "<small>Add <tt>[Nms]</tt> to a layer name to set its frame duration.</small>"
        )
        info.set_xalign(0)
        info.set_line_wrap(True)
        box.pack_start(info, False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 0)

        notebook = Gtk.Notebook()
        box.pack_start(notebook, True, True, 0)

        self._build_export_tab(notebook)
        self._build_advanced_tab(notebook)

        # Sync widget sensitivity to initial format selection
        self._on_format_changed(self._fmt_combo)

        box.show_all()

    def _build_export_tab(self, notebook):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.set_border_width(8)
        notebook.append_page(page, Gtk.Label(label="Export"))

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(12)
        page.pack_start(grid, False, False, 0)
        row = 0

        # Output path
        self._out_entry = Gtk.Entry()
        _gfile = self.image.get_file()
        _src   = _gfile.get_path() if _gfile else None
        if _src:
            _default_dir  = os.path.dirname(_src)
            _default_stem = os.path.splitext(os.path.basename(_src))[0]
        else:
            _desktop      = os.path.join(os.path.expanduser("~"), "Desktop")
            _default_dir  = _desktop if os.path.isdir(_desktop) else os.path.expanduser("~")
            _default_stem = "animation"
        self._out_entry.set_text(os.path.join(_default_dir, _default_stem + ".mp4"))
        self._out_entry.set_hexpand(True)
        browse = Gtk.Button(label="…")
        browse.connect("clicked", self._on_browse)
        path_box = Gtk.Box(spacing=4)
        path_box.pack_start(self._out_entry, True, True, 0)
        path_box.pack_start(browse, False, False, 0)
        self._add_row(grid, row, "Output file:", path_box); row += 1

        # Format
        self._fmt_combo = Gtk.ComboBoxText()
        for label in FORMAT_LABELS:
            self._fmt_combo.append_text(label)
        self._fmt_combo.set_active(0)
        self._fmt_combo.connect("changed", self._on_format_changed)
        self._add_row(grid, row, "Format:", self._fmt_combo); row += 1

        # FPS
        self._fps_spin = Gtk.SpinButton.new_with_range(1, 120, 1)
        self._fps_spin.set_value(12)
        self._add_row(grid, row, "Frame rate (FPS):", self._fps_spin); row += 1

        # Quality (CRF)
        self._quality_label = Gtk.Label(label="Quality (CRF 0–51):")
        self._quality_label.set_xalign(1)
        self._quality_spin = Gtk.SpinButton.new_with_range(0, 51, 1)
        self._quality_spin.set_value(23)
        grid.attach(self._quality_label, 0, row, 1, 1)
        grid.attach(self._quality_spin,  1, row, 1, 1); row += 1

        page.pack_start(Gtk.Separator(), False, False, 0)

        self._flatten_check = Gtk.CheckButton(
            label="Cumulative compositing (paint-on-canvas mode)")
        self._flatten_check.set_active(False)
        page.pack_start(self._flatten_check, False, False, 0)

        self._vartime_check = Gtk.CheckButton(
            label="Use per-layer delay from layer names, e.g. [75ms]")
        self._vartime_check.set_active(True)
        page.pack_start(self._vartime_check, False, False, 0)

        self._reverse_check = Gtk.CheckButton(label="Reverse frame order")
        self._reverse_check.set_active(False)
        page.pack_start(self._reverse_check, False, False, 0)

    def _build_advanced_tab(self, notebook):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.set_border_width(8)
        notebook.append_page(page, Gtk.Label(label="Advanced"))

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(12)
        page.pack_start(grid, False, False, 0)
        row = 0

        # H.264 preset
        self._preset_label = Gtk.Label(label="H.264 preset:")
        self._preset_label.set_xalign(1)
        self._preset_combo = Gtk.ComboBoxText()
        self._preset_combo.append_text("Default (medium)")
        for p in H264_PRESETS:
            self._preset_combo.append_text(p)
        self._preset_combo.set_active(0)
        grid.attach(self._preset_label, 0, row, 1, 1)
        grid.attach(self._preset_combo, 1, row, 1, 1); row += 1

        # Pixel format
        self._pixfmt_label = Gtk.Label(label="Pixel format:")
        self._pixfmt_label.set_xalign(1)
        self._pixfmt_combo = Gtk.ComboBoxText()
        for fmt in PIXEL_FORMATS:
            self._pixfmt_combo.append_text(fmt)
        self._pixfmt_combo.set_active(0)
        grid.attach(self._pixfmt_label, 0, row, 1, 1)
        grid.attach(self._pixfmt_combo, 1, row, 1, 1); row += 1

        # GIF dither
        self._gif_dither_label = Gtk.Label(label="GIF dither:")
        self._gif_dither_label.set_xalign(1)
        self._gif_dither_combo = Gtk.ComboBoxText()
        for label in GIF_DITHER_OPTIONS:
            self._gif_dither_combo.append_text(label)
        self._gif_dither_combo.set_active(0)
        grid.attach(self._gif_dither_label, 0, row, 1, 1)
        grid.attach(self._gif_dither_combo, 1, row, 1, 1); row += 1

        page.pack_start(Gtk.Separator(), False, False, 0)

        extra_label = Gtk.Label(label="Extra ffmpeg output args:")
        extra_label.set_xalign(0)
        page.pack_start(extra_label, False, False, 0)

        self._extra_args_entry = Gtk.Entry()
        self._extra_args_entry.set_placeholder_text("e.g. -vf scale=1280:-1  -tune animation")
        page.pack_start(self._extra_args_entry, False, False, 0)

    def _add_row(self, grid, row, label_text, widget):
        lbl = Gtk.Label(label=label_text)
        lbl.set_xalign(1)
        grid.attach(lbl,    0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)

    # ── Signal handlers ──────────────────────────────────────────────────

    def _on_format_changed(self, combo):
        fmt  = combo.get_active_text()
        ext, codec = FORMAT_DATA[fmt]
        is_gif  = codec == "gif"
        is_h264 = codec == "h264"

        self._quality_spin.set_sensitive(not is_gif)
        self._quality_label.set_sensitive(not is_gif)

        # Advanced tab sensitivity
        self._preset_combo.set_sensitive(is_h264)
        self._preset_label.set_sensitive(is_h264)
        self._pixfmt_combo.set_sensitive(not is_gif)
        self._pixfmt_label.set_sensitive(not is_gif)
        self._gif_dither_combo.set_sensitive(is_gif)
        self._gif_dither_label.set_sensitive(is_gif)

        # Update file extension
        base, _ = os.path.splitext(self._out_entry.get_text())
        self._out_entry.set_text(base + ext)

    def _on_browse(self, widget):
        dlg = Gtk.FileChooserDialog(
            title="Save animation as…",
            transient_for=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dlg.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL,
            "_Save",   Gtk.ResponseType.OK,
        )
        dlg.set_do_overwrite_confirmation(True)
        dlg.set_filename(self._out_entry.get_text())

        for label, (ext, _) in FORMAT_DATA.items():
            filt = Gtk.FileFilter()
            filt.set_name(f"{label} (*{ext})")
            filt.add_pattern(f"*{ext}")
            dlg.add_filter(filt)

        if dlg.run() == Gtk.ResponseType.OK:
            self._out_entry.set_text(dlg.get_filename())
        dlg.destroy()

    # ── Result accessor ──────────────────────────────────────────────────

    def get_settings(self):
        fmt = self._fmt_combo.get_active_text()
        ext, codec = FORMAT_DATA[fmt]

        preset_text = self._preset_combo.get_active_text()
        preset = None if preset_text == "Default (medium)" else preset_text

        extra_raw = self._extra_args_entry.get_text().strip()
        extra_args = shlex.split(extra_raw) if extra_raw else []

        gif_dither_label = self._gif_dither_combo.get_active_text()
        gif_dither = GIF_DITHER_OPTIONS.get(
            gif_dither_label, "paletteuse=dither=bayer:bayer_scale=5"
        )

        return {
            "output_path":  self._out_entry.get_text(),
            "codec":        codec,
            "fps":          int(self._fps_spin.get_value()),
            "quality":      int(self._quality_spin.get_value()),
            "flatten_below": self._flatten_check.get_active(),
            "use_var_time": self._vartime_check.get_active(),
            "reverse":      self._reverse_check.get_active(),
            "preset":       preset,
            "pixel_fmt":    self._pixfmt_combo.get_active_text(),
            "gif_dither":   gif_dither,
            "extra_args":   extra_args,
        }


# ---------------------------------------------------------------------------
# Progress dialog
# ---------------------------------------------------------------------------

class ProgressDialog(Gtk.Dialog):

    def __init__(self, parent, n_frames):
        super().__init__(
            title="Exporting animation…",
            transient_for=parent,
            modal=True,
        )
        self.set_default_size(360, -1)
        self.set_border_width(16)
        self.n_frames = n_frames

        box = self.get_content_area()
        box.set_spacing(8)

        self._label = Gtk.Label(label="Preparing…")
        self._label.set_xalign(0)
        box.pack_start(self._label, False, False, 0)

        self._bar = Gtk.ProgressBar()
        box.pack_start(self._bar, False, False, 0)

        box.show_all()

    def update(self, i, n, message=""):
        if message:
            self._label.set_text(message)
        self._bar.set_fraction(i / max(n, 1))
        self._bar.set_text(f"{i} / {n}")
        self._bar.set_show_text(True)
        # Flush GTK event queue
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


# ---------------------------------------------------------------------------
# GIMP 3.x Plugin class
# ---------------------------------------------------------------------------

class ExportAnimationPlugin(Gimp.PlugIn):

    # ── Plugin registration ──────────────────────────────────────────────

    def do_query_procedures(self):
        return ["export-layers-animation"]

    def do_set_i18n(self, name):
        return False   # No translation catalogue

    def do_create_procedure(self, name):
        procedure = Gimp.ImageProcedure.new(
            self,
            name,
            Gimp.PDBProcType.PLUGIN,
            self.run,
            None,
        )
        procedure.set_image_types("*")
        procedure.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)

        procedure.set_menu_label("Export Layers as Video…")
        procedure.add_menu_path("<Image>/Filters/Animation/")

        procedure.set_documentation(
            "Export layers as animation via ffmpeg",
            "Encodes each GIMP layer as a video frame using ffmpeg. "
            "Supports MP4/H.264, WebM/VP9, and GIF output. "
            "Per-frame timing can be set via [Nms] annotations in layer names.",
            name,
        )
        procedure.set_attribution("Marcus", "MIT", "2026")
        return procedure

    # ── Main entry point ─────────────────────────────────────────────────

    def run(self, procedure, run_mode, image, drawables, config, run_data):

        # Check ffmpeg is available
        if not (shutil.which(FFMPEG_PATH) or os.path.isfile(FFMPEG_PATH)):
            return procedure.new_return_values(
                Gimp.PDBStatusType.EXECUTION_ERROR,
                GLib.Error(
                    "ffmpeg not found. "
                    "Install it with: sudo pacman -S ffmpeg  (Arch) "
                    "or: brew install ffmpeg  (macOS)"
                ),
            )

        if not image.get_layers():
            return procedure.new_return_values(
                Gimp.PDBStatusType.EXECUTION_ERROR,
                GLib.Error("The image has no layers."),
            )

        if run_mode == Gimp.RunMode.INTERACTIVE:
            GimpUi.init("export_animation")

        # Show settings dialog
        parent_window = None  # GIMP 3 doesn't easily expose the main window
        dlg = ExportDialog(parent_window, image)
        response = dlg.run()

        if response != Gtk.ResponseType.OK:
            dlg.destroy()
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, None)

        settings = dlg.get_settings()
        dlg.destroy()

        output_path   = settings["output_path"]
        fps           = settings["fps"]
        codec         = settings["codec"]
        quality       = settings["quality"]
        flatten_below = settings["flatten_below"]
        use_var_time  = settings["use_var_time"]

        # Ensure output directory exists
        out_dir = os.path.dirname(output_path)
        if out_dir and not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as e:
                return procedure.new_return_values(
                    Gimp.PDBStatusType.EXECUTION_ERROR,
                    GLib.Error(f"Cannot create output directory: {e}"),
                )

        tmpdir = tempfile.mkdtemp(prefix="gimp_anim_")
        n_layers = len(_collect_leaf_layers(list(image.get_layers())[::-1]))
        progress_dlg = ProgressDialog(None, n_layers)
        progress_dlg.show()

        try:
            # ── Export frames ────────────────────────────────────────
            frames = export_all_frames(
                image, tmpdir, fps, flatten_below, use_var_time,
                reverse=settings["reverse"],
                progress_cb=lambda i, n, msg: progress_dlg.update(i, n, msg),
            )

            # ── Encode ───────────────────────────────────────────────
            progress_dlg.update(n_layers, n_layers, "Running ffmpeg…")
            run_ffmpeg(frames, output_path, fps, codec, quality, use_var_time,
                       preset=settings["preset"],
                       pixel_fmt=settings["pixel_fmt"],
                       gif_dither=settings["gif_dither"],
                       extra_args=settings["extra_args"])

        except subprocess.CalledProcessError as e:
            progress_dlg.destroy()
            shutil.rmtree(tmpdir, ignore_errors=True)
            stderr = e.stderr.decode(errors="replace") if e.stderr else "(no output)"
            Gimp.message(f"ffmpeg error:\n\n{stderr[-1200:]}")
            return procedure.new_return_values(
                Gimp.PDBStatusType.EXECUTION_ERROR,
                GLib.Error("ffmpeg encoding failed."),
            )
        except Exception as e:
            progress_dlg.destroy()
            shutil.rmtree(tmpdir, ignore_errors=True)
            Gimp.message(f"Export error:\n{type(e).__name__}: {e}")
            return procedure.new_return_values(
                Gimp.PDBStatusType.EXECUTION_ERROR,
                GLib.Error(str(e)),
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        progress_dlg.destroy()

        # ── Preview ──────────────────────────────────────────────────
        player = find_player()
        launched = open_preview(output_path)

        player_note = (
            f"Opening in {player}…" if launched
            else "No video player found (install mpv on Linux or IINA on macOS)."
        )

        Gimp.message(
            f"Animation exported successfully!\n\n"
            f"Output:  {output_path}\n"
            f"Frames:  {n_layers}\n"
            f"FPS:     {fps}\n"
            f"{player_note}"
        )

        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

Gimp.main(ExportAnimationPlugin.__gtype__, sys.argv)
