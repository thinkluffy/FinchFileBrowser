import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk
from app.application import FinchApp
from app import preview as pvmod

tmp = tempfile.mkdtemp(prefix="finch-j-", dir=os.path.expanduser("~"))
open(os.path.join(tmp, "a.txt"), "w").write("line\n" * 400)
pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 300, 1200); pb.fill(0x3366ccff)
pb.savev(os.path.join(tmp, "tall.png"), "png", [], [])
pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 1600, 400); pb.fill(0xcc6633ff)
pb.savev(os.path.join(tmp, "wide.png"), "png", [], [])
results = []
def check(name, ok):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, flush=True)
# record every pixbuf scaling and every allocation of the media box
scaled = []
orig = pvmod.Preview._image_widget
def spy(self):
    w = orig(self)
    if w is not None: scaled.append((w.get_pixbuf().get_width(), w.get_pixbuf().get_height()))
    return w
pvmod.Preview._image_widget = spy
allocs = []
app = FinchApp(); S = {}
def later(fn, ms=800): GLib.timeout_add(ms, fn); return False
def select(col, name):
    for row in col.store:
        if row[1] == name: col.view.set_cursor(row.path, None, False); return
def watch(win):
    allocs.clear(); scaled.clear()
    win.preview.media.connect("size-allocate", lambda w, a: allocs.append((a.width, a.height)))
    win.preview.content.connect("size-allocate", lambda w, a: allocs.append(("c", a.width)))
def steady(name, win):
    media_hs = {h for a in allocs if a[0] != "c" for h in [a[1]]}
    content_ws = {a[1] for a in allocs if a[0] == "c"}
    final = win.preview.media.get_allocated_height()
    print("   scalings:", scaled, "media heights seen:", sorted(media_hs), "content widths seen:", sorted(content_ws))
    check(name + ": image scaled once, at its final size", len(scaled) == 1 and scaled[0][1] <= final and (scaled[0][1] == final or scaled[0][0] == win.preview._content_width))
    check(name + ": media box never allocated another height", media_hs == {final})
    check(name + ": content never allocated another width", len(content_ws) == 1)

def s1():
    S["win"] = win = app.get_active_window(); win.resize(1100, 800); return later(s2)
def s2():
    win = S["win"]
    orig_sel = win._on_item_selected
    def hooked(col, gfile, info):
        orig_sel(col, gfile, info)
        if win.preview is not None and info is not None and info.get_file_type() != 2: watch(win)
    # hook: install the recorders right after the preview is created (before its first allocation)
    for col in win.columns: col.disconnect_by_func(win._on_item_selected); col.connect("item-selected", hooked)
    select(win.columns[0], "tall.png"); return later(s3)
def s3():
    steady("tall image", S["win"]); select(S["win"].columns[0], "wide.png"); return later(s4)
def s4():
    steady("wide image", S["win"]); select(S["win"].columns[0], "a.txt"); return later(s5)
def s5():
    win = S["win"]
    media_hs = {a[1] for a in allocs if a[0] != "c"}
    check("text: media box never allocated another height", media_hs == {win.preview.media.get_allocated_height()})
    check("text: box taller than the minimum", win.preview.media.get_allocated_height() > 160)
    app.quit(); return False
GLib.timeout_add(900, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True)
print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
