"""
Mocks out gi/GIMP/GTK bindings so the plugin module can be imported
outside a running GIMP process.  Must run before any import of the module.
"""
import sys
import pathlib
from unittest.mock import MagicMock

# Add project root to path so `import export_animation` works.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


class _MockGtkDialog:
    """Minimal Gtk.Dialog stand-in — just allows subclassing and super().__init__."""
    def __init__(self, *args, **kwargs):
        pass


class _MockGimpPlugIn:
    """Minimal Gimp.PlugIn stand-in — provides __gtype__ so Gimp.main() doesn't crash."""
    __gtype__ = MagicMock()


def _setup_gi_mocks():
    mock_gi = MagicMock()

    mock_gimp = MagicMock()
    mock_gimp.PlugIn = _MockGimpPlugIn
    mock_gimp.RunMode.NONINTERACTIVE = 0
    mock_gimp.RunMode.INTERACTIVE = 1

    mock_gtk = MagicMock()
    mock_gtk.Dialog = _MockGtkDialog

    mock_repo = MagicMock()
    mock_repo.Gimp = mock_gimp
    mock_repo.GimpUi = MagicMock()
    mock_repo.GObject = MagicMock()
    mock_repo.GLib = MagicMock()
    mock_repo.Gio = MagicMock()
    mock_repo.Gtk = mock_gtk

    sys.modules.update({
        "gi":                    mock_gi,
        "gi.repository":         mock_repo,
        "gi.repository.Gimp":    mock_gimp,
        "gi.repository.GimpUi":  MagicMock(),
        "gi.repository.GObject": MagicMock(),
        "gi.repository.GLib":    MagicMock(),
        "gi.repository.Gio":     MagicMock(),
        "gi.repository.Gtk":     mock_gtk,
    })


_setup_gi_mocks()
