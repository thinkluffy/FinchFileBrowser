import os, sys, tempfile, shutil, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "Finch"))
import cairo, gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gtk
from app.application import FinchApp
from app import preview as pvmod

tmp = tempfile.mkdtemp(prefix="finch-pdf-", dir=os.path.expanduser("~"))
surf = cairo.PDFSurface(os.path.join(tmp, "doc.pdf"), 400, 600)   # portrait, 3 pages
cr = cairo.Context(surf)
for i in range(3):
    cr.set_source_rgb(0.2 * i, 0.4, 0.8); cr.rectangle(50, 50, 300, 500); cr.fill(); cr.show_page()
surf.finish()
open(os.path.join(tmp, "broken.pdf"), "wb").write(b"%PDF-1.4 not really a pdf")
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
def later(fn, ms=1500): GLib.timeout_add(ms, fn); return False
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
    t0 = time.time(); select(win.columns[0], "doc.pdf"); S["ms"] = (time.time() - t0) * 1000
    GLib.timeout_add(50, lambda: S.__setitem__("early", dict(rows(win))) or False)
    return later(s2)
def s2():
    win = S["win"]; r = rows(win); pb = win.preview._pixbuf
    check("selecting a PDF returns at once (%.0f ms)" % S["ms"], S["ms"] < 300)
    check("Pages row placeholder first, then the count", S["early"].get("Pages", (0, ""))[1] == "…" and r.get("Pages", (0, ""))[1] == "3 pages")
    check("…placed between Kind and Size", r["Kind"][0] < r["Pages"][0] < r["Size"][0])
    check("first page rendered, bounded, portrait", pb is not None and pb.get_height() == 720 and abs(pb.get_width() / pb.get_height() - 400 / 600) < 0.02)
    img = win.preview.media.get_children()[0]
    check("page shown fitted into the media box", isinstance(img, Gtk.Image) and img.get_pixbuf().get_height() == win.preview.media.get_allocated_height())
    check("no icon flashed before the page", shown == [None, "picture"])
    shown.clear(); select(win.columns[0], "broken.pdf"); return later(s3)
def s3():
    win = S["win"]; r = rows(win)
    check("broken PDF: no Pages row, icon fallback, no crash", "Pages" not in r and shown[0] is None and shown[-1] == "icon")
    # pdftoppm missing: still no crash, icon fallback
    pvmod._run = lambda args: None
    shown.clear(); select(win.columns[0], "doc.pdf"); return later(s4)
def s4():
    win = S["win"]
    check("poppler-utils unavailable: icon fallback, no Pages row", shown[-1] == "icon" and "Pages" not in rows(win))
    app.quit(); return False
GLib.timeout_add(900, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True)
print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
