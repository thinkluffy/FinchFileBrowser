"""Copy, cut and paste of files through the system clipboard.

Files go on the CLIPBOARD selection in the format Nemo and Nautilus use,
``x-special/gnome-copied-files`` ("copy" or "cut" on the first line, then one
URI per line), plus ``text/uri-list`` and the plain paths as text. Pasting
reads the same formats, so files copied or cut in Nemo paste into Finch and
the other way round.

GtkClipboard's set_with_data() is not available from Python, so a hidden
widget owns the selection and serves the formats on request. The copying
itself is done by the transfer module.
"""
from gi.repository import Gdk, Gio, Gtk

COPIED_FILES = "x-special/gnome-copied-files"
_TEXT_TARGETS = ("UTF8_STRING", "STRING", "text/plain", "text/plain;charset=utf-8")
_T_FILES, _T_URIS, _T_TEXT = range(3)
_NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS


class _Owner(Gtk.Invisible):
    """Holds the CLIPBOARD selection and answers requests for its formats."""

    def __init__(self):
        super().__init__()
        self.op = "copy"
        self.gfiles = []
        targets = [Gtk.TargetEntry.new(COPIED_FILES, 0, _T_FILES),
                   Gtk.TargetEntry.new("text/uri-list", 0, _T_URIS)]
        targets += [Gtk.TargetEntry.new(t, 0, _T_TEXT) for t in _TEXT_TARGETS]
        Gtk.selection_add_targets(self, Gdk.SELECTION_CLIPBOARD, targets)

    def do_selection_get(self, sel, info, _time):
        uris = [f.get_uri() for f in self.gfiles]
        if info == _T_FILES:
            sel.set(sel.get_target(), 8, (self.op + "\n" + "\n".join(uris)).encode())
        elif info == _T_URIS:
            sel.set(sel.get_target(), 8, ("\r\n".join(uris) + "\r\n").encode())
        else:
            sel.set_text("\n".join(f.get_path() or f.get_uri() for f in self.gfiles), -1)

    def do_selection_clear_event(self, _event):
        self.gfiles = []            # another program took the clipboard over
        return True


_owner = None


def set_files(gfiles, cut=False):
    """Put `gfiles` on the clipboard, marked for copying or (cut) moving."""
    global _owner
    if _owner is None:
        _owner = _Owner()
    _owner.op = "cut" if cut else "copy"
    _owner.gfiles = list(gfiles)
    Gtk.selection_owner_set(_owner, Gdk.SELECTION_CLIPBOARD, Gdk.CURRENT_TIME)


def clear():
    """Give the clipboard up if it still holds our files (a cut is pasted once)."""
    if _owner is not None and _owner.gfiles:
        _owner.gfiles = []
        Gtk.selection_owner_set(None, Gdk.SELECTION_CLIPBOARD, Gdk.CURRENT_TIME)


def has_files():
    cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    return (cb.wait_is_target_available(Gdk.Atom.intern(COPIED_FILES, False))
            or cb.wait_is_target_available(Gdk.Atom.intern("text/uri-list", False)))


def get_files():
    """The clipboard's files as ("copy" | "cut", [Gio.File])."""
    cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    sel = cb.wait_for_contents(Gdk.Atom.intern(COPIED_FILES, False))
    if sel is not None and sel.get_length() > 0:
        lines = bytes(sel.get_data()).decode("utf-8", "replace").split("\n")
        op = "cut" if lines[0].strip() == "cut" else "copy"
        uris = [line.strip() for line in lines[1:] if line.strip()]
    else:
        op, uris = "copy", cb.wait_for_uris() or []
    return op, [Gio.File.new_for_uri(uri) for uri in uris]
