import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gio, Gdk, Gtk
from app.application import FinchApp
from app import C_NAME
tmp = tempfile.mkdtemp(prefix="finch-leaf-")
os.mkdir(os.path.join(tmp, "folder")); open(os.path.join(tmp, "notes.txt"), "w").write("x")
app = FinchApp(); fails = []
def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name, extra); fails.append(name) if not ok else None
def labels(bar): return [b.get_label() for g, b in bar._crumbs]
def active(bar): return [g.get_basename() for g, b in bar._crumbs if b.get_active()]
def select(w, name):
    col = w.columns[0]; col.view.set_cursor(next(r for r in col.store if r[C_NAME] == name).path, None, False)
def s0():
    app.get_active_window().lookup_action("view-mode").change_state(GLib.Variant("s", "columns"))
    GLib.timeout_add(700, s1); return False
def s1():
    w = app.get_active_window(); select(w, "notes.txt"); GLib.timeout_add(500, s2); return False
def s2():
    w = app.get_active_window(); bar = w.pathbar; base = os.path.basename(tmp)
    check("file selected: preview shows it", w.preview is not None and w.preview.name.get_text() == "notes.txt")
    check("…but the location is its folder", w.tab.location.get_path() == tmp)
    check("…the path bar ends at the folder", active(bar) == [base] and "notes.txt" not in [g.get_basename() for g, b in bar._crumbs], str(active(bar)))
    check("…the entry text is the folder", bar.entry.get_text() == tmp)
    check("…the title is the folder", w.header.get_title() == base and w.tab.title.get_text() == base)
    select(w, "folder"); GLib.timeout_add(500, s3); return False
def s3():
    w = app.get_active_window(); bar = w.pathbar
    check("folder selected: location is that folder", w.tab.location.get_path() == os.path.join(tmp, "folder") and active(bar) == ["folder"])
    w.lookup_action("back").activate(); GLib.timeout_add(600, s4); return False
def s4():
    w = app.get_active_window(); bar = w.pathbar
    check("back: the file is selected again, location its folder", w.preview is not None and w.preview.name.get_text() == "notes.txt" and active(bar) == [os.path.basename(tmp)], str(active(bar)))
    print("FAILURES:", fails); app.quit(); return False
GLib.timeout_add(1200, s0)
app.run(["finch", tmp])
