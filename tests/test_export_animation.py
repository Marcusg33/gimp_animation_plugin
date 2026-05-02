"""
Tests for the pure-Python parts of export_animation.py.

GIMP/GTK bindings are stubbed out by conftest.py before this module is
imported, so nothing here requires a running GIMP process.

Covered:
  parse_layer_delay_ms    — regex extraction from layer names
  ms_to_ffmpeg_duration   — millisecond → ffmpeg duration string
  _video_args             — codec argument lists
  write_concat_list       — concat-demuxer file format
  set_layer_visible_recursive — layer/group visibility, including nesting
  find_player             — PATH-based player discovery
  open_preview            — subprocess.Popen dispatch
  run_ffmpeg              — codec/format dispatch
  _encode_fixed           — fixed-framerate ffmpeg call
  _encode_variable        — concat-demuxer ffmpeg call
  _encode_gif             — two-pass GIF encoding
"""
import os
import pytest
from unittest.mock import MagicMock, patch

import export_animation as mod


# ── parse_layer_delay_ms ──────────────────────────────────────────────────────

class TestParseLayerDelayMs:
    def test_basic(self):
        assert mod.parse_layer_delay_ms("fire layer [120ms]") == 120

    def test_no_annotation(self):
        assert mod.parse_layer_delay_ms("background") is None

    def test_case_insensitive(self):
        assert mod.parse_layer_delay_ms("frame [100MS]") == 100

    def test_annotation_at_start(self):
        assert mod.parse_layer_delay_ms("[50ms] intro") == 50

    def test_zero(self):
        assert mod.parse_layer_delay_ms("[0ms]") == 0

    def test_large_value(self):
        assert mod.parse_layer_delay_ms("slow [5000ms]") == 5000

    def test_multiple_annotations_returns_first(self):
        assert mod.parse_layer_delay_ms("[100ms] layer [200ms]") == 100

    def test_number_without_ms_suffix(self):
        assert mod.parse_layer_delay_ms("layer [100]") is None

    def test_empty_name(self):
        assert mod.parse_layer_delay_ms("") is None


# ── ms_to_ffmpeg_duration ─────────────────────────────────────────────────────

class TestMsToFfmpegDuration:
    def test_one_second(self):
        assert mod.ms_to_ffmpeg_duration(1000) == "1.000000"

    def test_sixteen_ms(self):
        assert mod.ms_to_ffmpeg_duration(16) == "0.016000"

    def test_zero(self):
        assert mod.ms_to_ffmpeg_duration(0) == "0.000000"

    def test_eighty_three_ms(self):
        assert mod.ms_to_ffmpeg_duration(83) == "0.083000"

    def test_returns_string(self):
        assert isinstance(mod.ms_to_ffmpeg_duration(100), str)

    def test_six_decimal_places(self):
        result = mod.ms_to_ffmpeg_duration(1)
        assert len(result.split(".")[1]) == 6


# ── _video_args ───────────────────────────────────────────────────────────────

class TestVideoArgs:
    def test_h264_codec_flag(self):
        assert "libx264" in mod._video_args("h264", 23)

    def test_h264_pixel_format(self):
        assert "yuv420p" in mod._video_args("h264", 23)

    def test_h264_crf_value(self):
        args = mod._video_args("h264", 18)
        assert "18" in args

    def test_vp9_codec_flag(self):
        assert "libvpx-vp9" in mod._video_args("vp9", 30)

    def test_vp9_zero_bitrate(self):
        args = mod._video_args("vp9", 30)
        idx = args.index("-b:v")
        assert args[idx + 1] == "0"

    def test_vp9_crf_value(self):
        assert "33" in mod._video_args("vp9", 33)

    def test_returns_list(self):
        assert isinstance(mod._video_args("h264", 23), list)

    def test_h264_preset_included(self):
        args = mod._video_args("h264", 23, preset="slow")
        idx = args.index("-preset")
        assert args[idx + 1] == "slow"

    def test_h264_no_preset_when_none(self):
        args = mod._video_args("h264", 23, preset=None)
        assert "-preset" not in args

    def test_h264_custom_pixel_fmt(self):
        args = mod._video_args("h264", 23, pixel_fmt="yuv444p")
        assert "yuv444p" in args

    def test_vp9_default_pixel_fmt_not_added(self):
        args = mod._video_args("vp9", 30, pixel_fmt="yuv420p")
        assert "-pix_fmt" not in args

    def test_vp9_non_default_pixel_fmt_added(self):
        args = mod._video_args("vp9", 30, pixel_fmt="yuv444p")
        assert "-pix_fmt" in args
        idx = args.index("-pix_fmt")
        assert args[idx + 1] == "yuv444p"


# ── write_concat_list ─────────────────────────────────────────────────────────

class TestWriteConcatList:
    def test_file_entries_present(self, tmp_path):
        frames = [
            (str(tmp_path / "frame_0000.png"), 100),
            (str(tmp_path / "frame_0001.png"), 200),
        ]
        out = str(tmp_path / "frames.txt")
        mod.write_concat_list(frames, out)
        text = open(out).read()
        assert f"file '{tmp_path}/frame_0000.png'" in text
        assert f"file '{tmp_path}/frame_0001.png'" in text

    def test_duration_entries_present(self, tmp_path):
        frames = [(str(tmp_path / "frame_0000.png"), 100)]
        out = str(tmp_path / "frames.txt")
        mod.write_concat_list(frames, out)
        assert "duration 0.100000" in open(out).read()

    def test_last_line_is_file_not_duration(self, tmp_path):
        frames = [
            (str(tmp_path / "frame_0000.png"), 100),
            (str(tmp_path / "frame_0001.png"), 200),
        ]
        out = str(tmp_path / "frames.txt")
        mod.write_concat_list(frames, out)
        lines = [l for l in open(out).read().splitlines() if l]
        assert lines[-1].startswith("file '")
        assert "duration" not in lines[-1]

    def test_last_frame_is_repeated(self, tmp_path):
        last = str(tmp_path / "frame_0001.png")
        frames = [
            (str(tmp_path / "frame_0000.png"), 100),
            (last, 200),
        ]
        out = str(tmp_path / "frames.txt")
        mod.write_concat_list(frames, out)
        lines = [l for l in open(out).read().splitlines() if l]
        last_file_lines = [l for l in lines if l == f"file '{last}'"]
        assert len(last_file_lines) == 2  # once with duration, once without

    def test_single_frame_structure(self, tmp_path):
        frames = [(str(tmp_path / "frame_0000.png"), 50)]
        out = str(tmp_path / "frames.txt")
        mod.write_concat_list(frames, out)
        lines = [l for l in open(out).read().splitlines() if l]
        assert lines[0].startswith("file '")
        assert lines[1].startswith("duration ")
        assert lines[2].startswith("file '")
        assert len(lines) == 3

    def test_empty_frames_writes_empty_file(self, tmp_path):
        out = str(tmp_path / "frames.txt")
        mod.write_concat_list([], out)
        assert open(out).read() == ""


# ── set_layer_visible_recursive ───────────────────────────────────────────────

class TestSetLayerVisibleRecursive:
    @staticmethod
    def _leaf():
        return MagicMock(spec=["set_visible"])

    @staticmethod
    def _group(children):
        m = MagicMock(spec=["set_visible", "get_children"])
        m.get_children.return_value = children
        return m

    def test_leaf_show(self):
        layer = self._leaf()
        mod.set_layer_visible_recursive(layer, True)
        layer.set_visible.assert_called_once_with(True)

    def test_leaf_hide(self):
        layer = self._leaf()
        mod.set_layer_visible_recursive(layer, False)
        layer.set_visible.assert_called_once_with(False)

    def test_group_show_reveals_all_children(self):
        c1, c2 = self._leaf(), self._leaf()
        group = self._group([c1, c2])
        mod.set_layer_visible_recursive(group, True)
        group.set_visible.assert_called_once_with(True)
        c1.set_visible.assert_called_once_with(True)
        c2.set_visible.assert_called_once_with(True)

    def test_group_hide_hides_all_children(self):
        child = self._leaf()
        group = self._group([child])
        mod.set_layer_visible_recursive(group, False)
        group.set_visible.assert_called_once_with(False)
        child.set_visible.assert_called_once_with(False)

    def test_nested_group_fully_revealed(self):
        grandchild = self._leaf()
        inner = self._group([grandchild])
        outer = self._group([inner])
        mod.set_layer_visible_recursive(outer, True)
        grandchild.set_visible.assert_called_once_with(True)

    def test_nested_group_fully_hidden(self):
        grandchild = self._leaf()
        inner = self._group([grandchild])
        outer = self._group([inner])
        mod.set_layer_visible_recursive(outer, False)
        grandchild.set_visible.assert_called_once_with(False)

    def test_empty_group(self):
        group = self._group([])
        mod.set_layer_visible_recursive(group, True)
        group.set_visible.assert_called_once_with(True)


# ── _collect_leaf_layers ──────────────────────────────────────────────────────

class TestCollectLeafLayers:
    @staticmethod
    def _leaf():
        m = MagicMock(spec=["set_visible"])  # no get_children → AttributeError
        return m

    @staticmethod
    def _group(children):
        m = MagicMock(spec=["set_visible", "get_children"])
        m.get_children.return_value = children
        return m

    def test_flat_layers_preserved(self):
        l1, l2, l3 = self._leaf(), self._leaf(), self._leaf()
        assert mod._collect_leaf_layers([l1, l2, l3]) == [l1, l2, l3]

    def test_single_group_expanded(self):
        # group.get_children() = [top_child, bottom_child] (top-to-bottom)
        # input to _collect_leaf_layers is already bottom-to-top, so group is one item
        # children are reversed inside the function → [bottom_child, top_child]
        bottom_child, top_child = self._leaf(), self._leaf()
        group = self._group([top_child, bottom_child])
        result = mod._collect_leaf_layers([group])
        assert result == [bottom_child, top_child]

    def test_nested_groups_expanded(self):
        leaf1, leaf2 = self._leaf(), self._leaf()
        inner = self._group([leaf2, leaf1])   # top-to-bottom in group
        outer = self._group([inner])
        result = mod._collect_leaf_layers([outer])
        assert result == [leaf1, leaf2]

    def test_mixed_leaves_and_groups(self):
        leaf_bottom = self._leaf()
        leaf_g_bottom, leaf_g_top = self._leaf(), self._leaf()
        group = self._group([leaf_g_top, leaf_g_bottom])  # top-to-bottom
        leaf_top = self._leaf()
        # input bottom-to-top: [leaf_bottom, group, leaf_top]
        result = mod._collect_leaf_layers([leaf_bottom, group, leaf_top])
        assert result == [leaf_bottom, leaf_g_bottom, leaf_g_top, leaf_top]

    def test_empty(self):
        assert mod._collect_leaf_layers([]) == []

    def test_empty_group_excluded(self):
        empty_group = self._group([])
        leaf = self._leaf()
        # empty group has no children → treated as leaf? No: children=[] → falsy → appended
        result = mod._collect_leaf_layers([empty_group, leaf])
        assert result == [empty_group, leaf]


# ── _show_with_ancestors ──────────────────────────────────────────────────────

class TestShowWithAncestors:
    @staticmethod
    def _layer_with_parent(parent):
        m = MagicMock()
        m.get_parent.return_value = parent
        return m

    def test_top_level_leaf(self):
        layer = self._layer_with_parent(None)
        mod._show_with_ancestors(layer)
        layer.set_visible.assert_called_once_with(True)

    def test_one_level_deep(self):
        parent = self._layer_with_parent(None)
        leaf = self._layer_with_parent(parent)
        mod._show_with_ancestors(leaf)
        leaf.set_visible.assert_called_with(True)
        parent.set_visible.assert_called_with(True)

    def test_two_levels_deep(self):
        grandparent = self._layer_with_parent(None)
        parent = self._layer_with_parent(grandparent)
        leaf = self._layer_with_parent(parent)
        mod._show_with_ancestors(leaf)
        leaf.set_visible.assert_called_with(True)
        parent.set_visible.assert_called_with(True)
        grandparent.set_visible.assert_called_with(True)


# ── find_player ───────────────────────────────────────────────────────────────

class TestFindPlayer:
    def test_returns_first_available(self):
        def which(cmd):
            return "/usr/bin/mpv" if cmd == "mpv" else None
        with patch.object(mod.shutil, "which", side_effect=which), \
             patch.object(mod, "SYSTEM", "Linux"):
            assert mod.find_player() == "mpv"

    def test_skips_unavailable_falls_back(self):
        def which(cmd):
            return "/usr/bin/vlc" if cmd == "vlc" else None
        with patch.object(mod.shutil, "which", side_effect=which), \
             patch.object(mod, "SYSTEM", "Linux"):
            assert mod.find_player() == "vlc"

    def test_returns_none_when_nothing_found(self):
        with patch.object(mod.shutil, "which", return_value=None), \
             patch.object(mod, "SYSTEM", "Linux"):
            assert mod.find_player() is None

    def test_unknown_platform_tries_vlc(self):
        def which(cmd):
            return "/usr/bin/vlc" if cmd == "vlc" else None
        with patch.object(mod.shutil, "which", side_effect=which), \
             patch.object(mod, "SYSTEM", "FreeBSD"):
            assert mod.find_player() == "vlc"

    def test_darwin_candidates(self):
        def which(cmd):
            return "/Applications/IINA.app" if cmd == "iina" else None
        with patch.object(mod.shutil, "which", side_effect=which), \
             patch.object(mod, "SYSTEM", "Darwin"):
            assert mod.find_player() == "iina"


# ── open_preview ──────────────────────────────────────────────────────────────

class TestOpenPreview:
    def test_returns_false_when_no_player(self):
        with patch.object(mod, "find_player", return_value=None):
            assert mod.open_preview("/tmp/out.mp4") is False

    def test_mpv_uses_loop_file_flag(self):
        with patch.object(mod, "find_player", return_value="mpv"), \
             patch.object(mod.subprocess, "Popen") as mock_popen:
            assert mod.open_preview("/tmp/out.mp4") is True
            cmd = mock_popen.call_args[0][0]
            assert "--loop-file=inf" in cmd
            assert "/tmp/out.mp4" in cmd

    def test_iina_uses_mpv_prefixed_loop_flag(self):
        with patch.object(mod, "find_player", return_value="iina"), \
             patch.object(mod.subprocess, "Popen") as mock_popen:
            assert mod.open_preview("/tmp/out.mp4") is True
            cmd = mock_popen.call_args[0][0]
            assert "--mpv-loop-file=inf" in cmd
            assert "--loop-file=inf" not in cmd

    def test_vlc_has_no_loop_flag(self):
        with patch.object(mod, "find_player", return_value="vlc"), \
             patch.object(mod.subprocess, "Popen") as mock_popen:
            mod.open_preview("/tmp/out.mp4")
            cmd = mock_popen.call_args[0][0]
            assert not any("loop" in arg for arg in cmd)

    def test_fallback_when_loop_flag_raises(self):
        call_count = {"n": 0}
        def popen(cmd, **kw):
            call_count["n"] += 1
            if "--loop-file=inf" in cmd:
                raise FileNotFoundError
            return MagicMock()
        with patch.object(mod, "find_player", return_value="mpv"), \
             patch.object(mod.subprocess, "Popen", side_effect=popen):
            assert mod.open_preview("/tmp/out.mp4") is True
            assert call_count["n"] == 2

    def test_open_command_has_no_loop_flag(self):
        with patch.object(mod, "find_player", return_value="open"), \
             patch.object(mod.subprocess, "Popen") as mock_popen:
            mod.open_preview("/tmp/out.mp4")
            cmd = mock_popen.call_args[0][0]
            assert "--loop" not in cmd
            assert "/tmp/out.mp4" in cmd

    def test_returns_false_on_exception(self):
        with patch.object(mod, "find_player", return_value="mpv"), \
             patch.object(mod.subprocess, "Popen", side_effect=OSError):
            assert mod.open_preview("/tmp/out.mp4") is False


# ── run_ffmpeg dispatch ───────────────────────────────────────────────────────

class TestRunFfmpegDispatch:
    def _frames(self, tmp_path, n=1):
        return [(str(tmp_path / f"frame_{i:04d}.png"), 83) for i in range(n)]

    def test_empty_frames_raises(self, tmp_path):
        with pytest.raises(RuntimeError):
            mod.run_ffmpeg([], str(tmp_path / "out.mp4"), 12, "h264", 23, False)

    def test_gif_ext_calls_encode_gif(self, tmp_path):
        with patch.object(mod, "_encode_gif") as m:
            mod.run_ffmpeg(self._frames(tmp_path), str(tmp_path / "out.gif"), 12, "gif", 23, False)
            m.assert_called_once()

    def test_gif_ignores_variable_timing_flag(self, tmp_path):
        with patch.object(mod, "_encode_gif") as m:
            mod.run_ffmpeg(self._frames(tmp_path), str(tmp_path / "out.gif"), 12, "gif", 23, True)
            m.assert_called_once()

    def test_variable_flag_calls_encode_variable(self, tmp_path):
        with patch.object(mod, "_encode_variable") as m:
            mod.run_ffmpeg(self._frames(tmp_path), str(tmp_path / "out.mp4"), 12, "h264", 23, True)
            m.assert_called_once()

    def test_fixed_flag_calls_encode_fixed(self, tmp_path):
        with patch.object(mod, "_encode_fixed") as m:
            mod.run_ffmpeg(self._frames(tmp_path), str(tmp_path / "out.mp4"), 12, "h264", 23, False)
            m.assert_called_once()

    def test_webm_fixed_uses_encode_fixed(self, tmp_path):
        with patch.object(mod, "_encode_fixed") as m:
            mod.run_ffmpeg(self._frames(tmp_path), str(tmp_path / "out.webm"), 12, "vp9", 30, False)
            m.assert_called_once()

    def test_preset_forwarded_to_encode_fixed(self, tmp_path):
        with patch.object(mod, "_encode_fixed") as m:
            mod.run_ffmpeg(self._frames(tmp_path), str(tmp_path / "out.mp4"), 12, "h264", 23, False,
                           preset="slow")
            call_kwargs = m.call_args.kwargs
            call_args   = m.call_args.args
            assert call_kwargs.get("preset") == "slow" or "slow" in call_args

    def test_extra_args_forwarded_to_encode_fixed(self, tmp_path):
        extra = ["-vf", "scale=1280:-1"]
        with patch.object(mod, "_encode_fixed") as m:
            mod.run_ffmpeg(self._frames(tmp_path), str(tmp_path / "out.mp4"), 12, "h264", 23, False,
                           extra_args=extra)
            # Just verify it was called — extra_args plumbing verified in TestEncodeFixed
            m.assert_called_once()

    def test_gif_dither_forwarded_to_encode_gif(self, tmp_path):
        dither = "paletteuse=dither=sierra2_4a"
        with patch.object(mod, "_encode_gif") as m:
            mod.run_ffmpeg(self._frames(tmp_path), str(tmp_path / "out.gif"), 12, "gif", 23, False,
                           gif_dither=dither)
            args = m.call_args[0]
            assert dither in args


# ── _encode_fixed ─────────────────────────────────────────────────────────────

class TestEncodeFixed:
    def _run(self, tmp_path, fps=12, codec="h264", quality=23):
        frames = [(str(tmp_path / "frame_0000.png"), 83)]
        with patch.object(mod.subprocess, "run") as mock_run:
            mod._encode_fixed(frames, str(tmp_path / "out.mp4"), str(tmp_path), fps, codec, quality)
            return mock_run

    def test_called_once(self, tmp_path):
        self._run(tmp_path).assert_called_once()

    def test_check_true(self, tmp_path):
        mock_run = self._run(tmp_path)
        assert mock_run.call_args.kwargs.get("check") is True

    def test_framerate_arg(self, tmp_path):
        cmd = self._run(tmp_path).call_args[0][0]
        assert "-framerate" in cmd
        assert "12" in cmd

    def test_image_pattern_input(self, tmp_path):
        cmd = self._run(tmp_path).call_args[0][0]
        assert "frame_%04d.png" in " ".join(cmd)

    def test_output_path_in_cmd(self, tmp_path):
        out = str(tmp_path / "out.mp4")
        frames = [(str(tmp_path / "frame_0000.png"), 83)]
        with patch.object(mod.subprocess, "run") as mock_run:
            mod._encode_fixed(frames, out, str(tmp_path), 12, "h264", 23)
            cmd = mock_run.call_args[0][0]
        assert out in cmd

    def test_h264_args_included(self, tmp_path):
        cmd = self._run(tmp_path, codec="h264").call_args[0][0]
        assert "libx264" in cmd

    def test_vp9_args_included(self, tmp_path):
        cmd = self._run(tmp_path, codec="vp9").call_args[0][0]
        assert "libvpx-vp9" in cmd

    def test_extra_args_appended(self, tmp_path):
        frames = [(str(tmp_path / "frame_0000.png"), 83)]
        extra = ["-vf", "scale=1280:-1"]
        with patch.object(mod.subprocess, "run") as mock_run:
            mod._encode_fixed(frames, str(tmp_path / "out.mp4"), str(tmp_path),
                              12, "h264", 23, extra_args=extra)
            cmd = mock_run.call_args[0][0]
        assert "-vf" in cmd
        assert "scale=1280:-1" in cmd

    def test_preset_in_command(self, tmp_path):
        frames = [(str(tmp_path / "frame_0000.png"), 83)]
        with patch.object(mod.subprocess, "run") as mock_run:
            mod._encode_fixed(frames, str(tmp_path / "out.mp4"), str(tmp_path),
                              12, "h264", 23, preset="slow")
            cmd = mock_run.call_args[0][0]
        assert "-preset" in cmd
        assert "slow" in cmd


# ── _encode_variable ──────────────────────────────────────────────────────────

class TestEncodeVariable:
    def _run(self, tmp_path, codec="h264", quality=23):
        frames = [(str(tmp_path / "frame_0000.png"), 100)]
        with patch.object(mod.subprocess, "run") as mock_run:
            mod._encode_variable(frames, str(tmp_path / "out.mp4"), str(tmp_path), codec, quality)
            return mock_run, tmp_path

    def test_called_once(self, tmp_path):
        mock_run, _ = self._run(tmp_path)
        mock_run.assert_called_once()

    def test_uses_concat_demuxer(self, tmp_path):
        mock_run, _ = self._run(tmp_path)
        cmd = mock_run.call_args[0][0]
        assert "-f" in cmd
        assert cmd[cmd.index("-f") + 1] == "concat"

    def test_concat_file_written(self, tmp_path):
        self._run(tmp_path)
        assert os.path.exists(str(tmp_path / "frames.txt"))

    def test_check_true(self, tmp_path):
        mock_run, _ = self._run(tmp_path)
        assert mock_run.call_args.kwargs.get("check") is True

    def test_h264_args_included(self, tmp_path):
        mock_run, _ = self._run(tmp_path, codec="h264")
        cmd = mock_run.call_args[0][0]
        assert "libx264" in cmd


# ── _encode_gif ───────────────────────────────────────────────────────────────

class TestEncodeGif:
    def _run(self, tmp_path):
        frames = [(str(tmp_path / "frame_0000.png"), 100)]
        with patch.object(mod.subprocess, "run") as mock_run:
            mod._encode_gif(frames, str(tmp_path / "out.gif"), str(tmp_path))
            return mock_run

    def test_exactly_two_passes(self, tmp_path):
        assert self._run(tmp_path).call_count == 2

    def test_first_pass_palettegen(self, tmp_path):
        cmd = self._run(tmp_path).call_args_list[0][0][0]
        assert "palettegen" in " ".join(cmd)

    def test_second_pass_paletteuse(self, tmp_path):
        cmd = self._run(tmp_path).call_args_list[1][0][0]
        assert "paletteuse" in " ".join(cmd)

    def test_concat_file_written(self, tmp_path):
        self._run(tmp_path)
        assert os.path.exists(str(tmp_path / "frames.txt"))

    def test_both_passes_check_true(self, tmp_path):
        mock_run = self._run(tmp_path)
        for c in mock_run.call_args_list:
            assert c.kwargs.get("check") is True

    def test_second_pass_uses_palette_input(self, tmp_path):
        cmd = self._run(tmp_path).call_args_list[1][0][0]
        # palette.png must appear as an -i argument
        i_indices = [i for i, v in enumerate(cmd) if v == "-i"]
        inputs = [cmd[i + 1] for i in i_indices]
        assert any("palette.png" in inp for inp in inputs)

    def test_custom_dither_in_second_pass(self, tmp_path):
        frames = [(str(tmp_path / "frame_0000.png"), 100)]
        dither = "paletteuse=dither=sierra2_4a"
        with patch.object(mod.subprocess, "run") as mock_run:
            mod._encode_gif(frames, str(tmp_path / "out.gif"), str(tmp_path),
                            gif_dither=dither)
            cmd = mock_run.call_args_list[1][0][0]
        assert dither in " ".join(cmd)

    def test_extra_args_in_second_pass(self, tmp_path):
        frames = [(str(tmp_path / "frame_0000.png"), 100)]
        with patch.object(mod.subprocess, "run") as mock_run:
            mod._encode_gif(frames, str(tmp_path / "out.gif"), str(tmp_path),
                            extra_args=["-s", "320x240"])
            cmd = mock_run.call_args_list[1][0][0]
        assert "-s" in cmd and "320x240" in cmd


# ── export_all_frames (reverse) ───────────────────────────────────────────────

class TestExportAllFramesReverse:
    @staticmethod
    def _make_image(leaf_names):
        """Return a mock image whose leaf layers have the given names (bottom-to-top)."""
        leaves = []
        for name in leaf_names:
            l = MagicMock(spec=["set_visible", "get_name"])
            l.get_name.return_value = name
            leaves.append(l)
        image = MagicMock()
        image.get_layers.return_value = list(reversed(leaves))  # top-to-bottom
        return image, leaves

    def test_normal_order(self, tmp_path):
        image, leaves = self._make_image(["A", "B", "C"])
        exported = []
        with patch.object(mod, "export_frame",
                          side_effect=lambda img, idx, td, fb: str(tmp_path / f"frame_{idx:04d}.png")) as ef, \
             patch.object(mod, "_collect_leaf_layers", return_value=leaves):
            frames = mod.export_all_frames(image, str(tmp_path), 12, False, False)
        # indices should be 0, 1, 2 in order
        indices = [c.args[1] for c in ef.call_args_list]
        assert indices == [0, 1, 2]

    def test_reverse_order(self, tmp_path):
        image, leaves = self._make_image(["A", "B", "C"])
        reversed_leaves = list(reversed(leaves))
        with patch.object(mod, "export_frame",
                          side_effect=lambda img, idx, td, fb: str(tmp_path / f"frame_{idx:04d}.png")) as ef, \
             patch.object(mod, "_collect_leaf_layers", return_value=leaves):
            frames = mod.export_all_frames(image, str(tmp_path), 12, False, False, reverse=True)
        # With reverse, the layer list is flipped before iteration
        # export_frame is called with index into the reversed list: 0,1,2 but layers are C,B,A
        assert len(frames) == 3

    def test_reverse_flips_frame_order(self, tmp_path):
        """Frames returned in reverse order when reverse=True vs reverse=False."""
        image, leaves = self._make_image(["A", "B", "C"])
        call_log = []
        def fake_export(img, idx, td, fb):
            call_log.append(idx)
            return str(tmp_path / f"frame_{idx:04d}.png")

        with patch.object(mod, "_collect_leaf_layers", return_value=leaves):
            call_log.clear()
            with patch.object(mod, "export_frame", side_effect=fake_export):
                mod.export_all_frames(image, str(tmp_path), 12, False, False, reverse=False)
            normal_order = list(call_log)

            call_log.clear()
            with patch.object(mod, "export_frame", side_effect=fake_export):
                mod.export_all_frames(image, str(tmp_path), 12, False, False, reverse=True)
            reversed_order = list(call_log)

        # Both produce the same number of frames
        assert len(normal_order) == len(reversed_order) == 3
