import os, sys, tempfile, shutil, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0"); gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk
from app.application import FinchApp
from app import preview as pvmod

tmp = tempfile.mkdtemp(prefix="finch-async-", dir=os.path.expanduser("~"))
pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 800, 600); pb.fill(0x3366ccff)
pb.savev(os.path.join(tmp, "pic.png"), "png", [], [])
pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 3000, 2000); pb.fill(0xcc3366ff)
pb.savev(os.path.join(tmp, "huge.png"), "png", [], [])
open(os.path.join(tmp, "a.txt"), "w").write("hello\n" * 100)
results = []
def check(name, ok):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, flush=True)
# slow down every read by one second, off the main thread
orig_read = pvmod.Preview._read
reads = []
def slow_read(self, gen, gfile, path, is_image, size):
    reads.append((gfile.get_basename(), threading.current_thread() is threading.main_thread()))
    time.sleep(1.0); orig_read(self, gen, gfile, path, is_image, size)
pvmod.Preview._read = slow_read
app = FinchApp(); S = {"ticks": 0}
def later(fn, ms=800): GLib.timeout_add(ms, fn); return False
def select(col, name):
    for row in col.store:
        if row[1] == name: col.view.set_cursor(row.path, None, False); return
def labels(win): return {c.get_text() for c in win.preview.details.get_children() if isinstance(c, Gtk.Label)}
def media(win): return win.preview.media.get_children()[0] if win.preview and win.preview.media.get_children() else None

def s1():
    S["win"] = win = app.get_active_window()
    GLib.timeout_add(100, lambda: S.__setitem__("ticks", S["ticks"] + 1) or True)
    t0 = time.time(); select(win.columns[0], "pic.png"); S["sel_ms"] = (time.time() - t0) * 1000
    return later(s2, 300)
def s2():
    win = S["win"]
    check("selecting returns at once (%.0f ms)" % S["sel_ms"], S["sel_ms"] < 300)
    check("name and basic details shown immediately", win.preview.name.get_text() == "pic.png" and "Size" in labels(win) and "…" in labels(win))
    check("placeholder icon while the picture loads", isinstance(media(win), Gtk.Image) and media(win).get_pixbuf() is None)
    check("read happens off the main thread", reads and reads[-1] == ("pic.png", False))
    S["ticks_at_300"] = S["ticks"]
    return later(s3, 1200)
def s3():
    win = S["win"]
    check("main loop kept ticking during the read", S["ticks"] - S["ticks_at_300"] >= 8)
    img = media(win)
    check("picture and dimensions arrive afterwards", img is not None and img.get_pixbuf() is not None and "800 × 600" in labels(win))
    # switch files while a read is in flight: the stale result must be dropped
    select(win.columns[0], "huge.png")
    GLib.timeout_add(300, lambda: select(win.columns[0], "a.txt") or False)
    return later(s4, 2200)
def s4():
    win = S["win"]
    check("stale picture never lands on the newer preview", win.preview.name.get_text() == "a.txt" and win.preview._pixbuf is None and "Dimensions" not in labels(win))
    check("text preview arrives", isinstance(media(win), Gtk.ScrolledWindow))
    select(win.columns[0], "huge.png"); return later(s5, 1800)
def s5():
    win = S["win"]; pb = win.preview._pixbuf
    check("big picture kept bounded in memory", pb is not None and pb.get_width() <= 720 and pb.get_height() <= 720 and "3000 × 2000" in labels(win))
    win.resize(1100, 500); return later(s6, 900)
def s6():
    win = S["win"]
    img = media(win)
    check("resize re-fits from memory without another read", len([r for r in reads if r[0] == "huge.png"]) == 2 and img.get_pixbuf().get_height() == win.preview.media.get_allocated_height())
    app.quit(); return False
GLib.timeout_add(900, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True)
print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
