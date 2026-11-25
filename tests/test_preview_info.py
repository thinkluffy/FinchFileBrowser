import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gio, Gdk, Gtk, GdkPixbuf
from app.application import FinchApp
from app import C_NAME
tmp = tempfile.mkdtemp(prefix="finch-pv-")
os.mkdir(os.path.join(tmp, "folder"))
with open(os.path.join(tmp, "notes.txt"), "w") as f: f.write("hello " * 200)
pic = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 320, 200); pic.fill(0x3366ccff)
pic.savev(os.path.join(tmp, "pic.png"), "png", [], [])
app = FinchApp(); fails = []
def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name, extra); fails.append(name) if not ok else None
def labels(pv): return sorted([c for c in pv.details.get_children() if isinstance(c, Gtk.Label)], key=lambda c: (pv.details.child_get_property(c, "top-attach"), pv.details.child_get_property(c, "left-attach")))
def rows(pv): return [(k.get_text(), v.get_text()) for k, v in zip(labels(pv)[0::2], labels(pv)[1::2])]
def seps(pv): return [c for c in pv.details.get_children() if isinstance(c, Gtk.Separator)]
def select(w, *names):
    col = w.columns[0]; sel = col.view.get_selection()
    paths = [next(r for r in col.store if r[C_NAME] == n).path for n in names]
    col.view.set_cursor(paths[0], None, False)
    for p in paths[1:]: sel.select_path(p)
def s0():
    app.get_active_window().lookup_action("view-mode").change_state(GLib.Variant("s", "columns"))
    GLib.timeout_add(700, s1); return False
def s1():
    w = app.get_active_window(); select(w, "notes.txt"); GLib.timeout_add(500, s2); return False
def s2():
    w = app.get_active_window(); pv = w.preview
    check("name shown, left aligned, bold class", pv.name.get_text() == "notes.txt" and pv.name.get_xalign() == 0.0 and pv.name.get_style_context().has_class("preview-name"))
    check("summary is kind - size", pv.summary.get_text().replace("\u00a0", " ").endswith(" - 1.2 kB") and "text" in pv.summary.get_text().lower(), pv.summary.get_text())
    check("Information heading visible", pv.heading.get_visible() and pv.heading.get_text() == "Information")
    r = rows(pv)
    check("rows are Created, Modified", [k for k, _ in r] == ["Created", "Modified"], r)
    check("Kind/Size no longer rows", not any(k in ("Kind", "Size") for k, _ in r))
    check("values right aligned", all(v.get_xalign() == 1.0 for v in labels(pv)[1::2]))
    check("a hairline between the rows", len(seps(pv)) == len(r) - 1)
    kl, vl = labels(pv)[0], labels(pv)[1]
    check("label at left edge, value at right edge", kl.get_allocation().x < vl.get_allocation().x + vl.get_allocation().width - 40 and vl.get_allocation().x + vl.get_allocation().width >= pv.details.get_allocation().width - 2, (kl.get_allocation().x, vl.get_allocation().x + vl.get_allocation().width, pv.details.get_allocation().width))
    select(w, "pic.png"); GLib.timeout_add(800, s3); return False
def s3():
    w = app.get_active_window(); pv = w.preview; r = rows(pv)
    check("image: Dimensions row first, filled", r[0] == ("Dimensions", "320 × 200"), r)
    check("image rows: Dimensions, Created, Modified", [k for k, _ in r] == ["Dimensions", "Created", "Modified"])
    check("image summary", pv.summary.get_text().startswith("PNG image - "), pv.summary.get_text())
    check("hairlines match", len(seps(pv)) == 2)
    select(w, "folder", "notes.txt", "pic.png"); GLib.timeout_add(500, s4); return False
def s4():
    w = app.get_active_window(); pv = w.preview; r = rows(pv)
    check("multi: name", pv.name.get_text() == "3 items")
    check("multi: summary", pv.summary.get_text().startswith("1 folder, 2 files - "), pv.summary.get_text())
    check("multi: Items row only", [k for k, _ in r] == ["Items"] and "notes.txt" in r[0][1], r)
    check("multi: heading shown", pv.heading.get_visible())
    print("FAILURES:", fails); app.quit(); return False
GLib.timeout_add(1200, s0)
app.run(["finch", tmp])
