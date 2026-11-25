import os, sys, tempfile, shutil, subprocess, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "Finch"))
os.environ["GDK_CORE_DEVICE_EVENTS"] = "1"
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0"); gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk
from app.application import FinchApp

tmp = tempfile.mkdtemp(prefix="finch-rm-", dir=os.path.expanduser("~"))
os.makedirs(os.path.join(tmp, "dest"))
for n in ("a.txt", "b.txt", "c.txt", "d.txt"): open(os.path.join(tmp, n), "w").write(n)
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
            sf = col.get_scale_factor()
            return (ox + rect.x + 30) * sf, (oy + rect.y + rect.height // 2) * sf
def xdo(*args): subprocess.run(["xdotool", *args], check=True)
def click(x, y, mod=None):
    xdo("mousemove", str(x), str(y)); time.sleep(0.1)
    if mod: xdo("keydown", mod)
    xdo("click", "1")
    if mod: xdo("keyup", mod)
def drag(x0, y0, x1, y1):
    xdo("mousemove", str(x0), str(y0)); time.sleep(0.15); xdo("mousedown", "1"); time.sleep(0.15)
    for i in range(1, 13):
        xdo("mousemove", str(x0 + (x1 - x0) * i // 12), str(y0 + (y1 - y0) * i // 12)); time.sleep(0.06)
    time.sleep(0.4); xdo("mouseup", "1")
def active_ok(win):
    active = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True).stdout.strip()
    return active == str(win.get_window().get_xid())
sel_names = lambda col: sorted(g.get_basename() for g in col.selected_all()[0])

def s1():
    S["win"] = win = app.get_active_window(); win.resize(1100, 700); win.present(); return later(s2, 1200)
def s2():
    win = S["win"]
    if not active_ok(win):
        print("SKIP: Finch window not active; no pointer actions sent"); app.quit(); return False
    col = win.columns[0]
    def seq():
        click(*row_root(col, "a.txt")); time.sleep(0.3)
        click(*row_root(col, "c.txt"), mod="ctrl"); time.sleep(0.3)
    threading.Thread(target=seq, daemon=True).start(); return later(s3, 1800)
def s3():
    win = S["win"]; col = win.columns[0]
    check("click then Ctrl+click selects two rows", sel_names(col) == ["a.txt", "c.txt"])
    check("…with a summary preview on the right", win.preview is not None and win.preview.gfile is None)
    xid = win.get_window().get_xid()
    def seq():
        subprocess.run(["xdotool", "key", "--window", str(xid), "shift+Down"], check=True)
    threading.Thread(target=seq, daemon=True).start(); return later(s4, 1200)
def s4():
    win = S["win"]; col = win.columns[0]
    check("Shift+Down extends the selection", "d.txt" in sel_names(col) and len(sel_names(col)) >= 2)
    col = win.columns[0]
    def seq():
        click(*row_root(col, "a.txt")); time.sleep(0.3)
        click(*row_root(col, "b.txt"), mod="shift"); time.sleep(0.3)
        drag(*row_root(col, "a.txt"), *row_root(col, "dest"))
    threading.Thread(target=seq, daemon=True).start(); return later(s5, 4000)
def s5():
    win = S["win"]
    check("Shift+click range then drag moves both files", sorted(os.listdir(P("dest"))) == ["a.txt", "b.txt"] and not os.path.exists(P("a.txt")))
    check("source column lists the rest", [r[1] for r in win.columns[0].store] == ["dest", "c.txt", "d.txt"])
    app.quit(); return False
GLib.timeout_add(900, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True)
print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
