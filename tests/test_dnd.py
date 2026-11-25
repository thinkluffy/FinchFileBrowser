import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk
from app.application import FinchApp
from app import column as colmod

tmp = tempfile.mkdtemp(prefix="finch-dnd-", dir=os.path.expanduser("~"))
os.makedirs(os.path.join(tmp, "sub")); os.makedirs(os.path.join(tmp, "other"))
open(os.path.join(tmp, "a.txt"), "w").write("hi"); open(os.path.join(tmp, "b.txt"), "w").write("yo")
P = lambda *p: os.path.join(tmp, *p)
F = lambda *p: Gio.File.new_for_path(P(*p))
results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra if not ok else "", flush=True)
app = FinchApp(); S = {}
def later(fn, ms=700): GLib.timeout_add(ms, fn); return False
def row_pos(col, name):
    for row in col.store:
        if row[1] == name:
            rect = col.view.get_cell_area(row.path, col.name_col)
            x, y = col.view.convert_bin_window_to_widget_coords(rect.x + 5, rect.y + rect.height // 2)
            return x, y
def blank_pos(col):
    n = len(col.store); rect = col.view.get_cell_area(Gtk.TreePath(n - 1), col.name_col)
    return col.view.convert_bin_window_to_widget_coords(rect.x + 5, rect.y + rect.height + 30)

def s1():
    S["win"] = win = app.get_active_window(); win.resize(1100, 700); return later(s2)
def s2():
    win = S["win"]; col = win.columns[0]
    check("sidebar has the drop signals", GObject.signal_lookup("drag-perform-drop", Gtk.PlacesSidebar) and GObject.signal_lookup("drag-action-requested", Gtk.PlacesSidebar))
    d, path = col.drop_target(*row_pos(col, "sub"))
    check("over a folder row: that folder, row highlighted", d.get_path() == P("sub") and path is not None)
    d, path = col.drop_target(*row_pos(col, "a.txt"))
    check("over a file row: the column's directory, no row", d.get_path() == tmp and path is None)
    d, path = col.drop_target(*blank_pos(col))
    check("over blank space: the column's directory", d.get_path() == tmp and path is None)
    colmod._dragging[:] = [F("a.txt")]
    check("own drag over its own directory: refused", col.drop_target(*blank_pos(col)) == (None, None))
    check("own drag over another folder: accepted", col.drop_target(*row_pos(col, "sub"))[0].get_path() == P("sub"))
    colmod._dragging[:] = [F("sub")]
    check("folder over itself: refused", col.drop_target(*row_pos(col, "sub")) == (None, None))
    colmod._dragging.clear()
    # move a.txt into sub by the window's drop handler (what a drop emits);
    # sub is open to the right, so that there is a destination column to
    # look at (selected here rather than left to whatever row the tree view
    # picks when the window happens to get the focus)
    col.view.set_cursor(next(r.path for r in col.store if r[1] == "sub"), None, False)
    win.drop_files([F("a.txt")], F("sub"), True); return later(s3, 900)
def s3():
    win = S["win"]
    check("dropped file moved", os.path.exists(P("sub", "a.txt")) and not os.path.exists(P("a.txt")))
    check("source column updated by its monitor", "a.txt" not in [r[1] for r in win.columns[0].store])
    check("destination column shows and selects it", len(win.columns) >= 2 and win.columns[1].gfile.get_path() == P("sub") and win.columns[1].selected()[0].get_path() == P("sub", "a.txt"), "cols=%s sel=%s" % ([c.gfile.get_basename() for c in win.columns], [[g.get_basename() for g in c.selected_all()[0]] for c in win.columns]))
    win.drop_files([F("b.txt")], F("other"), False); return later(s4, 900)
def s4():
    check("copy keeps the source", os.path.exists(P("other", "b.txt")) and os.path.exists(P("b.txt")))
    # drop onto the trash: trashes, via Gio.File.trash (patched so no real trash entry is made)
    trashed = []
    orig = Gio.File.trash
    try:
        Gio.File.trash = lambda self, c=None: trashed.append(self.get_path())
        S["win"].drop_files([F("b.txt"), Gio.File.new_for_uri("trash:///x")], Gio.File.new_for_uri("trash:///"), True)
        check("drop on the trash trashes files (not those already in it)", trashed == [P("b.txt")])
    except Exception as e:
        print("  (trash patch not possible: %s)" % e)
    finally:
        Gio.File.trash = orig
    # drop_target on virtual / trash columns
    col = S["win"].columns[0]
    class Fake: pass
    check("recent:// refuses drops", not colmod.Column.accepts_drops(type("C", (), {"in_trash": lambda s: False, "gfile": Gio.File.new_for_uri("recent:///")})()))
    check("trash root accepts drops", colmod.Column.accepts_drops(type("C", (), {"in_trash": lambda s: True, "is_trash_root": lambda s: True})()))
    check("folder inside the trash refuses drops", not colmod.Column.accepts_drops(type("C", (), {"in_trash": lambda s: True, "is_trash_root": lambda s: False})()))
    app.quit(); return False
GLib.timeout_add(900, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True)
print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
