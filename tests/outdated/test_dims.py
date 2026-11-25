import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0"); gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk
from app.application import FinchApp
tmp = tempfile.mkdtemp(prefix="finch-dim-", dir=os.path.expanduser("~"))
pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 1920, 1080); pb.fill(0x3366ccff)
pb.savev(os.path.join(tmp, "big.png"), "png", [], [])
open(os.path.join(tmp, "a.txt"), "w").write("hi")
open(os.path.join(tmp, "broken.png"), "wb").write(b"\x89PNG not really")
results = []
def check(name, ok):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, flush=True)
app = FinchApp(); S = {}
def later(fn, ms=800): GLib.timeout_add(ms, fn); return False
def select(col, name):
    for row in col.store:
        if row[1] == name: col.view.set_cursor(row.path, None, False); return
def labels(win): return [c.get_text() for c in win.preview.details.get_children() if isinstance(c, Gtk.Label)]
def s1():
    S["win"] = win = app.get_active_window(); select(win.columns[0], "big.png"); return later(s2)
def s2():
    win = S["win"]; L = labels(win)
    check("image preview lists its dimensions", "Dimensions" in L and "1920 × 1080" in L)
    check("…between Kind and Size", L.index("Dimensions") > L.index("Kind") and L.index("Dimensions") < L.index("Size"))
    select(win.columns[0], "a.txt"); return later(s3)
def s3():
    win = S["win"]
    check("text file has no dimensions row", "Dimensions" not in labels(win))
    select(win.columns[0], "broken.png"); return later(s4)
def s4():
    win = S["win"]
    check("unreadable image: preview still shown, no dimensions row", win.preview is not None and "Dimensions" not in labels(win))
    app.quit(); return False
GLib.timeout_add(900, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True)
print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
