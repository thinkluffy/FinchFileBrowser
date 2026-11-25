import os, sys, tempfile, shutil, subprocess, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "Finch"))
os.environ["GDK_CORE_DEVICE_EVENTS"] = "1"
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0"); gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk
from app.application import FinchApp

tmp = tempfile.mkdtemp(prefix="finch-rd-", dir=os.path.expanduser("~"))
os.makedirs(os.path.join(tmp, "dest"))
open(os.path.join(tmp, "a.txt"), "w").write("hi"); open(os.path.join(tmp, "b.txt"), "w").write("yo")
P = lambda *p: os.path.join(tmp, *p)
results = []
def check(name, ok):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, flush=True)
app = FinchApp(); S = {}
def later(fn, ms=800): GLib.timeout_add(ms, fn); return False
def row_root(col, name):
    for row in col.store:
        if row[1] == name:
            rect = col.view.get_cell_area(row.path, col.name_col)
            ox, oy = col.view.get_bin_window().get_origin()[1:]
            return ox + rect.x + 30, oy + rect.y + rect.height // 2
def xdo(*args): subprocess.run(["xdotool", *args], check=True)
def drag(x0, y0, x1, y1, ctrl=False):
    xdo("mousemove", str(x0), str(y0)); time.sleep(0.15)
    xdo("mousedown", "1"); time.sleep(0.15)
    if ctrl: xdo("keydown", "ctrl")
    steps = 12
    for i in range(1, steps + 1):
        xdo("mousemove", str(x0 + (x1 - x0) * i // steps), str(y0 + (y1 - y0) * i // steps)); time.sleep(0.06)
    time.sleep(0.4)
    xdo("mouseup", "1")
    if ctrl: xdo("keyup", "ctrl")

def s1():
    S["win"] = win = app.get_active_window(); win.resize(1100, 700); win.present(); return later(s2, 1200)
def s2():
    win = S["win"]
    xid = win.get_window().get_xid()
    active = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True).stdout.strip()
    if active != str(xid):
        print("SKIP: Finch window is not the active window (%s vs %s); no pointer actions sent" % (active, xid)); app.quit(); return False
    S["xid"] = xid
    col = win.columns[0]
    S["dest"] = row_root(col, "dest")
    drag(*row_root(col, "a.txt"), *S["dest"])
    return later(s3, 1500)
def s3():
    win = S["win"]
    check("real drag onto a folder row moves the file", os.path.exists(P("dest", "a.txt")) and not os.path.exists(P("a.txt")))
    check("source column still lists the rest", [r[1] for r in win.columns[0].store] == ["dest", "b.txt"])
    # ctrl-drag copies
    active = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True).stdout.strip()
    if active != str(S["xid"]):
        print("SKIP: window no longer active"); app.quit(); return False
    col = win.columns[0]
    drag(*row_root(col, "b.txt"), *row_root(col, "dest"), ctrl=True)
    return later(s4, 1500)
def s4():
    check("real Ctrl-drag onto a folder row copies the file", os.path.exists(P("dest", "b.txt")) and os.path.exists(P("b.txt")))
    win = S["win"]
    check("no drop highlight left behind", not win.columns[0].view.get_style_context().has_class("drop-target") and win.columns[0].view.get_drag_dest_row()[0] is None)
    app.quit(); return False
GLib.timeout_add(900, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True)
print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
