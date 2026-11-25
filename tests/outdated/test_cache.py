import os, sys, tempfile, shutil, time, subprocess, hashlib, stat
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "Finch"))
cache_home = tempfile.mkdtemp(prefix="finch-cache-home-", dir=os.path.expanduser("~"))
os.environ["XDG_CACHE_HOME"] = cache_home          # keep the real thumbnail cache untouched
import cairo, gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0"); gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk
from app.application import FinchApp
from app import preview as pvmod, thumbs

tmp = tempfile.mkdtemp(prefix="finch-cache-", dir=os.path.expanduser("~"))
P = lambda *p: os.path.join(tmp, *p)
surf = cairo.PDFSurface(P("doc.pdf"), 400, 600); cr = cairo.Context(surf)
for i in range(2): cr.set_source_rgb(0.3, 0.5, 0.8); cr.rectangle(50, 50, 300, 500); cr.fill(); cr.show_page()
surf.finish()
subprocess.run(["gst-launch-1.0", "-q", "videotestsrc", "num-buffers=50", "!", "video/x-raw,width=320,height=240,framerate=25/1", "!",
                "videoconvert", "!", "vp8enc", "deadline=1", "!", "webmmux", "!", "filesink", "location=" + P("clip.webm")], check=True, timeout=60, capture_output=True)
pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 800, 600); pb.fill(0x3366ccff); pb.savev(P("pic.png"), "png", [], [])
results = []
def check(name, ok):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, flush=True)
runs = []
orig_run = pvmod._run
def counting_run(args): runs.append(args[0]); return orig_run(args)
pvmod._run = counting_run
shown = []
orig_show = pvmod.Preview._show_media
def spy(self, w):
    shown.append(None if w is None else ("picture", w.get_pixbuf().get_width()) if isinstance(w, Gtk.Image) and w.get_pixbuf() is not None else "icon" if isinstance(w, Gtk.Image) else type(w).__name__)
    orig_show(self, w)
pvmod.Preview._show_media = spy
uri = lambda name: Gio.File.new_for_path(P(name)).get_uri()
entry = lambda size, name: os.path.join(cache_home, "thumbnails", size, hashlib.md5(uri(name).encode()).hexdigest() + ".png")
app = FinchApp(); S = {}
def later(fn, ms=2000): GLib.timeout_add(ms, fn); return False
def select(col, name):
    for row in col.store:
        if row[1] == name: col.view.set_cursor(row.path, None, False); return
def rows(win):
    g = win.preview.details; L = [c for c in g.get_children() if isinstance(c, Gtk.Label)]
    out = {}
    for c in L:
        if g.child_get_property(c, "left-attach") == 0:
            top = g.child_get_property(c, "top-attach")
            out[c.get_text()] = next(v.get_text() for v in L if g.child_get_property(v, "left-attach") == 1 and g.child_get_property(v, "top-attach") == top)
    return out

def s1():
    S["win"] = win = app.get_active_window(); win.resize(1100, 800)
    select(win.columns[0], "doc.pdf"); return later(s2)
def s2():
    win = S["win"]; r = rows(win)
    check("first PDF preview renders (pdfinfo + pdftoppm ran)", runs.count("pdftoppm") == 1 and r.get("Pages") == "2 pages")
    path = entry("xx-large", "doc.pdf")
    check("…and is written to the freedesktop cache as xx-large", os.path.exists(path))
    if os.path.exists(path):
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
        check("cache entry carries URI, MTime, Pages and is private", pb.get_option("tEXt::Thumb::URI") == uri("doc.pdf") and pb.get_option("tEXt::Thumb::MTime") == str(int(os.stat(P("doc.pdf")).st_mtime)) and pb.get_option("tEXt::Thumb::Document::Pages") == "2" and stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700)
    # memory layer: re-select without any tool
    runs.clear(); select(win.columns[0], "pic.png"); GLib.timeout_add(600, lambda: select(win.columns[0], "doc.pdf") or False)
    return later(s3)
def s3():
    win = S["win"]
    check("re-selecting hits the memory cache: no tool run, page and count shown", "pdftoppm" not in runs and "pdfinfo" not in runs and win.preview._pixbuf is not None and rows(win).get("Pages") == "2 pages")
    check("images are not written to the disk cache", not os.path.exists(entry("xx-large", "pic.png")))
    # disk layer: forget memory, break the tools, re-select
    thumbs.memory_clear(); runs.clear(); pvmod._run = lambda args: (runs.append(args[0]), None)[1]
    select(win.columns[0], "pic.png"); GLib.timeout_add(600, lambda: select(win.columns[0], "doc.pdf") or False)
    return later(s4)
def s4():
    win = S["win"]
    check("disk cache serves page and count with the tools broken", win.preview._pixbuf is not None and win.preview._pixbuf.get_height() == 720 and rows(win).get("Pages") == "2 pages" and "pdftoppm" not in runs)
    # modified file: entry stale, re-render needed (tools broken: icon), then with tools back a fresh entry is written
    os.utime(P("doc.pdf"), (time.time() + 100, time.time() + 100)); thumbs.memory_clear(); runs.clear(); shown.clear()
    select(win.columns[0], "pic.png"); GLib.timeout_add(1200, lambda: select(win.columns[0], "doc.pdf") or False)
    return later(s5, 3200)
def s5():
    win = S["win"]
    check("changed file invalidates the entry (tool asked again)", "pdftoppm" in runs and shown[-1] == "icon" and "Pages" not in rows(win))
    pvmod._run = counting_run; thumbs.memory_clear(); runs.clear()
    select(win.columns[0], "pic.png"); GLib.timeout_add(600, lambda: select(win.columns[0], "doc.pdf") or False)
    return later(s6)
def s6():
    win = S["win"]; path = entry("xx-large", "doc.pdf")
    pb = GdkPixbuf.Pixbuf.new_from_file(path)
    check("fresh entry written with the new mtime", pb.get_option("tEXt::Thumb::MTime") == str(int(os.stat(P("doc.pdf")).st_mtime)) and rows(win).get("Pages") == "2 pages")
    # damaged cache file: ignored, re-rendered
    open(path, "wb").write(b"\x89PNG garbage"); thumbs.memory_clear(); runs.clear()
    select(win.columns[0], "pic.png"); GLib.timeout_add(600, lambda: select(win.columns[0], "doc.pdf") or False)
    return later(s7)
def s7():
    win = S["win"]
    check("damaged cache entry is ignored and re-rendered", "pdftoppm" in runs and win.preview._pixbuf is not None and os.path.getsize(entry("xx-large", "doc.pdf")) > 1000)
    # video: first render writes duration + dims; then served from disk with GStreamer and tools gone
    select(win.columns[0], "clip.webm"); return later(s8, 3000)
def s8():
    win = S["win"]; r = rows(win); path = entry("xx-large", "clip.webm")
    pb = GdkPixbuf.Pixbuf.new_from_file(path) if os.path.exists(path) else None
    check("video entry written with Movie::Length and Image::Width/Height", pb is not None and pb.get_option("tEXt::Thumb::Movie::Length") == "2" and pb.get_option("tEXt::Thumb::Image::Width") == "320" and r.get("Duration") == "0:02")
    thumbs.memory_clear(); runs.clear(); S["gst"] = pvmod.GstPbutils; pvmod.GstPbutils = None; pvmod._run = lambda args: (runs.append(args[0]), None)[1]
    select(win.columns[0], "pic.png"); GLib.timeout_add(600, lambda: select(win.columns[0], "clip.webm") or False)
    return later(s9)
def s9():
    win = S["win"]; r = rows(win)
    check("video served from disk: frame, duration and dimensions without tools", win.preview._pixbuf is not None and r.get("Duration") == "0:02" and r.get("Dimensions") == "320 × 240" and "ffmpegthumbnailer" not in runs)
    pvmod.GstPbutils = S["gst"]
    # a small Nemo-style thumbnail stands in while a slow rendering is made
    small = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 128, 96); small.fill(0x00ff00ff)
    d = os.path.join(cache_home, "thumbnails", "normal"); os.makedirs(d, 0o700, exist_ok=True)
    small.savev(entry("normal", "pic.png"), "png", ["tEXt::Thumb::URI", "tEXt::Thumb::MTime"], [uri("pic.png"), str(int(os.stat(P("pic.png")).st_mtime))])
    def slow_run(args): time.sleep(0.8); return orig_run(args)
    orig_picture = pvmod._picture
    def slow_picture(*a): time.sleep(0.8); return orig_picture(*a)
    pvmod._picture = slow_picture; pvmod._run = counting_run; thumbs.memory_clear(); shown.clear()
    select(win.columns[0], "doc.pdf"); GLib.timeout_add(1200, lambda: (shown.clear(), select(win.columns[0], "pic.png")) and False)
    GLib.timeout_add(1600, lambda: S.__setitem__("mid", list(shown)) or False)
    return later(s10, 3200)
def s10():
    win = S["win"]
    check("Nemo's small thumbnail shown at native size while rendering", S["mid"] == [None, ("picture", 128)])
    check("…then replaced by the full rendering", shown[-1][0] == "picture" and shown[-1][1] > 128 and win.preview._pixbuf.get_width() == 800)
    check("memory cache bounded", len(thumbs._memory) <= thumbs.MEMORY_ENTRIES)
    app.quit(); return False
GLib.timeout_add(900, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True); shutil.rmtree(cache_home, ignore_errors=True)
print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
