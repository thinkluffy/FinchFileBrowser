# The preview pane is never narrower than a column, so selecting a file or a
# folder in the last column extends the strip by the same amount.
import os, shutil, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk
from app.application import FinchApp
tmp = tempfile.mkdtemp(prefix="finch-pw-", dir=os.path.expanduser("~"))
os.makedirs(os.path.join(tmp, "sub"))
open(os.path.join(tmp, "f.txt"), "w").write("hello")
fails = []
def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name, extra, flush=True)
    if not ok: fails.append(name)
def select(win, idx, name):
    col = win.columns[idx]
    for row in col.store:
        if row[1] == name:
            col.view.set_cursor(row.path, None, False); return
    raise AssertionError(name)
app = FinchApp(); S = {}
def later(fn, ms=600): GLib.timeout_add(ms, fn); return False
def upper(win): return win.tab.hsw.get_hadjustment().get_upper()
def s1():
    S["win"] = win = app.get_active_window()
    win.resize(620, 500)            # strip narrower than two columns
    return later(s2)
def s2():
    win = S["win"]; S["base"] = upper(win); print("window", win.get_size(), "strip", win.tab.hsw.get_allocated_width()); select(win, 0, "sub"); return later(s3)
def s3():
    win = S["win"]; S["folder"] = upper(win)
    check("folder: strip is two columns wide", abs(S["folder"] - 2 * win.columns[0].get_allocated_width()) <= 2, (S["folder"], win.columns[0].get_allocated_width()))
    select(win, 0, "f.txt"); return later(s4)
def s4():
    win = S["win"]; grown = upper(win)
    check("file: strip is exactly as wide", abs(grown - S["folder"]) <= 2, (grown, S["folder"]))
    check("preview pane is one column wide", abs(win.preview.get_allocated_width() - win.columns[0].get_allocated_width()) <= 2,
          win.preview.get_allocated_width())
    win.set_column_width(300); return later(s5)
def s5():
    win = S["win"]
    check("preview follows the column width", abs(win.preview.get_allocated_width() - win.columns[0].get_allocated_width()) <= 2,
          win.preview.get_allocated_width())
    win.set_column_width(240)
    print("%d failures" % len(fails)); app.quit(); return False
GLib.timeout_add(1200, s1)
app.run(["finch", tmp])
shutil.rmtree(tmp)
