import os, sys, tempfile, shutil, time, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gtk
from app.application import FinchApp
from app import preview as pvmod

tmp = tempfile.mkdtemp(prefix="finch-video-", dir=os.path.expanduser("~"))
subprocess.run(["gst-launch-1.0", "-q", "videotestsrc", "num-buffers=75", "pattern=smpte", "!",
                "video/x-raw,width=320,height=240,framerate=25/1", "!", "videoconvert", "!",
                "vp8enc", "deadline=1", "!", "webmmux", "!", "filesink", "location=" + os.path.join(tmp, "clip.webm")],
               check=True, timeout=60, capture_output=True)
open(os.path.join(tmp, "broken.mp4"), "wb").write(b"\x00\x00\x00\x18ftypmp42 not a real video")
results = []
def check(name, ok):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, flush=True)
shown = []
orig = pvmod.Preview._show_media
def spy(self, w):
    shown.append(None if w is None else "picture" if isinstance(w, Gtk.Image) and w.get_pixbuf() is not None else "icon" if isinstance(w, Gtk.Image) else type(w).__name__)
    orig(self, w)
pvmod.Preview._show_media = spy
app = FinchApp(); S = {}
def later(fn, ms=2500): GLib.timeout_add(ms, fn); return False
def select(col, name):
    for row in col.store:
        if row[1] == name: col.view.set_cursor(row.path, None, False); return
def rows(win):
    g = win.preview.details; L = [c for c in g.get_children() if isinstance(c, Gtk.Label)]
    out = {}
    for c in L:
        if g.child_get_property(c, "left-attach") == 0:
            top = g.child_get_property(c, "top-attach")
            val = next(v for v in L if g.child_get_property(v, "left-attach") == 1 and g.child_get_property(v, "top-attach") == top)
            out[c.get_text()] = (top, val.get_text())
    return out
def s1():
    S["win"] = win = app.get_active_window(); win.resize(1100, 800); shown.clear()
    t0 = time.time(); select(win.columns[0], "clip.webm"); S["ms"] = (time.time() - t0) * 1000
    S["early"] = dict(rows(win))
    return later(s2)
def s2():
    win = S["win"]; r = rows(win); pb = win.preview._pixbuf
    check("selecting a video returns at once (%.0f ms)" % S["ms"], S["ms"] < 300)
    check("Duration and Dimensions rows start as placeholders", S["early"].get("Duration", (0, ""))[1] == "…" and S["early"].get("Dimensions", (0, ""))[1] == "…")
    check("duration filled in", r.get("Duration", (0, ""))[1] == "0:03")
    check("dimensions filled in", r.get("Dimensions", (0, ""))[1] == "320 × 240")
    check("rows ordered Duration, Dimensions, Created, Modified", r["Duration"][0] < r["Dimensions"][0] < r["Created"][0] < r["Modified"][0])
    check("a frame rendered at the bound, 4:3", pb is not None and pb.get_width() == 720 and abs(pb.get_width() / pb.get_height() - 4 / 3) < 0.02)
    img = win.preview.media.get_children()[0]
    check("frame shown fitted into the media box", isinstance(img, Gtk.Image) and img.get_pixbuf().get_width() == win.preview._content_width or img.get_pixbuf().get_height() == win.preview.media.get_allocated_height())
    check("no icon flashed before the frame", shown == [None, "picture"])
    shown.clear(); select(win.columns[0], "broken.mp4"); return later(s3)
def s3():
    win = S["win"]; r = rows(win)
    check("broken video: no Duration/Dimensions rows, icon fallback", "Duration" not in r and "Dimensions" not in r and shown[0] is None and shown[-1] == "icon")
    pvmod._run = lambda args: None; pvmod.GstPbutils = None
    shown.clear(); select(win.columns[0], "clip.webm"); return later(s4)
def s4():
    win = S["win"]; r = rows(win)
    check("tools unavailable: icon fallback, no pending rows left", shown[-1] == "icon" and "Duration" not in r and "Dimensions" not in r)
    app.quit(); return False
GLib.timeout_add(900, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True)
print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
