import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk
from app.application import FinchApp
from app import MIN_PREVIEW_MEDIA, MAX_PREVIEW_MEDIA

tmp = tempfile.mkdtemp(prefix="finch-h-", dir=os.path.expanduser("~"))
open(os.path.join(tmp, "a.txt"), "w").write("line\n" * 400)
pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 300, 1200); pb.fill(0x3366ccff)
pb.savev(os.path.join(tmp, "tall.png"), "png", [], [])
results = []
def check(name, ok):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, flush=True)
app = FinchApp(); S = {}
def later(fn, ms=700): GLib.timeout_add(ms, fn); return False
def select(col, name):
    for row in col.store:
        if row[1] == name: col.view.set_cursor(row.path, None, False); return
def expected(pv):
    spare = pv.get_allocated_height() - pv._others_height(pv._content_width)
    return max(MIN_PREVIEW_MEDIA, min(MAX_PREVIEW_MEDIA, spare))
def media_h(win): return win.preview.media.get_allocated_height()

def s1():
    S["win"] = win = app.get_active_window()
    win.resize(1100, 700); select(win.columns[0], "a.txt"); return later(s2)
def s2():
    win = S["win"]; pv = win.preview
    S["h700"] = media_h(win)
    check("700px window: text box fills the spare height", media_h(win) == expected(pv) and media_h(win) > MIN_PREVIEW_MEDIA)
    sw = pv.media.get_children()[0]
    check("text scroller fills the media box", sw.get_allocated_height() == media_h(win))
    check("name/details/button fit under the box", pv.content.get_allocated_height() <= pv.get_allocated_height() + 1)
    win.resize(1100, 1000); return later(s3)
def s3():
    win = S["win"]
    S["h1000"] = media_h(win)
    check("taller window: box grows", media_h(win) > S["h700"] and media_h(win) == expected(win.preview))
    win.resize(1100, 1500); return later(s4)
def s4():
    win = S["win"]
    h = media_h(win)
    check("very tall window: box capped at MAX", h == MAX_PREVIEW_MEDIA or (h == expected(win.preview) and win.get_allocated_height() < 1500))
    win.resize(1100, 450); return later(s5)
def s5():
    win = S["win"]; pv = win.preview
    check("window shrinks again after the box grew", win.get_allocated_height() < 700)
    check("small window: box at MIN", media_h(win) == MIN_PREVIEW_MEDIA == expected(pv))
    pref = pv.get_preferred_height()
    check("pane's min height ignores the box size", pref[0] == pv._others_height(pv._content_width) + MIN_PREVIEW_MEDIA)
    win.resize(1100, 900); select(win.columns[0], "tall.png"); return later(s6)
def s6():
    win = S["win"]; pv = win.preview
    img = pv.media.get_children()[0]
    ph = img.get_pixbuf().get_height()
    check("image scaled to the media box height", ph == media_h(win) == expected(pv) and ph > MIN_PREVIEW_MEDIA)
    win.resize(1100, 500); return later(s7)
def s7():
    win = S["win"]; pv = win.preview
    img = pv.media.get_children()[0]
    check("image shrinks with the window", img.get_pixbuf().get_height() == media_h(win) == expected(pv) and win.get_allocated_height() < 700)
    app.quit(); return False
app.connect("activate", lambda *_a: later(s1, 900))
app.run([sys.argv[0], tmp]); shutil.rmtree(tmp)
print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
