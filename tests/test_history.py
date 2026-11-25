import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gio, GLib, Gtk
from app.application import FinchApp

tmp = tempfile.mkdtemp(prefix="finch-hist-", dir=os.path.expanduser("~"))
os.makedirs(os.path.join(tmp, "A", "x", "y")); os.makedirs(os.path.join(tmp, "B"))
open(os.path.join(tmp, "A", "x", "f.txt"), "w").write("f")
P = lambda *p: os.path.join(tmp, *p)
F = lambda *p: Gio.File.new_for_path(P(*p))
results = []
def check(name, ok):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, flush=True)
app = FinchApp(); S = {}
def later(fn, ms=700): GLib.timeout_add(ms, fn); return False
def snapshot(win):
    cols = [c.gfile.get_path() for c in win.columns]
    sel = [(c.selected()[0].get_path() if c.selected()[0] else None) for c in win.columns]
    return cols, sel
def select(win, idx, name):
    col = win.columns[idx]
    for row in col.store:
        if row[1] == name:
            col.view.set_cursor(row.path, None, False); return
    raise AssertionError(f"{name} not in column {idx}")

def s1():
    S["win"] = win = app.get_active_window()
    check("back disabled at start", not win.lookup_action("back").get_enabled())
    select(win, 0, "x"); return later(s2, 500)
def s2():
    win = S["win"]; select(win, 1, "y"); return later(s3, 500)
def s3():
    win = S["win"]
    check("back enabled after browsing columns", win.lookup_action("back").get_enabled())
    check("three columns open", snapshot(win)[0] == [P("A"), P("A","x"), P("A","x","y")])
    win.set_root(F("B")); return later(s4)
def s4():
    win = S["win"]
    check("root is B", snapshot(win)[0] == [P("B")])
    win.go_back(); return later(s5, 1200)
def s5():
    win = S["win"]; cols, sel = snapshot(win)
    check("back restores root A with columns down to y", cols == [P("A"), P("A","x"), P("A","x","y")])
    check("back restores the selections", sel[:2] == [P("A","x"), P("A","x","y")])
    check("path box shows y", win.path_entry.get_text() == P("A","x","y"))
    check("forward enabled", win.lookup_action("forward").get_enabled())
    win.go_forward(); return later(s6)
def s6():
    win = S["win"]
    check("forward goes to B again", snapshot(win)[0] == [P("B")])
    win.go_back(); return later(s7, 1200)
def s7():
    win = S["win"]; select(win, 1, "f.txt"); return later(s8, 500)
def s8():
    win = S["win"]
    check("preview shown for f.txt", win.preview is not None)
    win.set_root(F("B")); return later(s9)
def s9():
    win = S["win"]; win.go_back(); return later(s10, 1200)
def s10():
    win = S["win"]; cols, sel = snapshot(win)
    check("back restores the selected file", cols == [P("A"), P("A","x")] and sel == [P("A","x"), P("A","x","f.txt")])
    check("…with its preview", win.preview is not None and win.preview.gfile.get_path() == P("A","x","f.txt"))
    check("no new history entry was recorded while restoring", win._hist_pos == len(win._history) - 2)
    win.go_back(); return later(s11, 1000)
def s11():
    win = S["win"]; cols, sel = snapshot(win)
    check("back again: A with x open", cols == [P("A"), P("A","x")] and sel == [P("A","x"), None])
    win.go_back(); return later(s12, 1000)
def s12():
    win = S["win"]; cols, sel = snapshot(win)
    check("back again: bare A", cols == [P("A")] and sel == [None])
    check("back now disabled", not win.lookup_action("back").get_enabled())
    win.go_up(); return later(s13, 1000)
def s13():
    win = S["win"]; cols, sel = snapshot(win)
    check("up: parent with A selected", cols == [tmp, P("A")] and sel[0] == P("A"))
    win.go_back(); return later(s14, 1000)
def s14():
    win = S["win"]; cols, sel = snapshot(win)
    check("back from up returns to A", cols == [P("A")])
    app.quit(); return False

GLib.timeout_add(800, s1)
app.run(["finch", P("A")])
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if all(ok for _, ok in results) else 1)
