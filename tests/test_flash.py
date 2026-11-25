import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0"); gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk
from app.application import FinchApp
from app import preview as pvmod

tmp = tempfile.mkdtemp(prefix="finch-flash-", dir=os.path.expanduser("~"))
pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 800, 600); pb.fill(0x3366ccff)
pb.savev(os.path.join(tmp, "pic.png"), "png", [], [])
open(os.path.join(tmp, "a.txt"), "w").write("hello\n" * 100)
open(os.path.join(tmp, "blob.bin"), "wb").write(os.urandom(64))
open(os.path.join(tmp, "broken.png"), "wb").write(b"\x89PNG not really")
results = []
def check(name, ok):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, flush=True)
shown = []      # what each preview put in its media box, in order
orig = pvmod.Preview._show_media
def spy(self, widget):
    kind = (None if widget is None else "picture" if isinstance(widget, Gtk.Image) and widget.get_pixbuf() is not None
            else "icon" if isinstance(widget, Gtk.Image) else type(widget).__name__)
    shown.append(kind); orig(self, widget)
pvmod.Preview._show_media = spy
app = FinchApp(); S = {}
def later(fn, ms=800): GLib.timeout_add(ms, fn); return False
def select(col, name):
    for row in col.store:
        if row[1] == name: col.view.set_cursor(row.path, None, False); return
def s1():
    S["win"] = win = app.get_active_window(); shown.clear(); select(win.columns[0], "pic.png"); return later(s2)
def s2():
    check("picture: box emptied, then the picture, never the icon", shown == [None, "picture"])
    shown.clear(); select(S["win"].columns[0], "a.txt"); return later(s3)
def s3():
    check("text: box emptied, then the text, never the icon", shown == [None, "ScrolledWindow"])
    shown.clear(); select(S["win"].columns[0], "blob.bin"); return later(s4)
def s4():
    check("no preview for the kind: icon at once", shown == ["icon"])
    shown.clear(); select(S["win"].columns[0], "broken.png"); return later(s5)
def s5():
    check("unreadable picture: icon once the read gives up", shown[0] is None and shown[-1] == "icon" and shown.count("icon") == 1)
    app.quit(); return False
GLib.timeout_add(900, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True)
print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
