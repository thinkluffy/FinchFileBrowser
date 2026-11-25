import os, sys, tempfile, shutil, subprocess
os.environ["GDK_CORE_DEVICE_EVENTS"] = "1"   # so XSendEvent key events reach GTK
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from app.application import FinchApp
from app import clipboard

tmp = tempfile.mkdtemp(prefix="finch-keys-", dir=os.path.expanduser("~"))
open(os.path.join(tmp, "a.txt"), "w").write("hello")
os.makedirs(os.path.join(tmp, "sub"))
results = []
def check(name, ok):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, file=sys.stderr, flush=True)
cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
app = FinchApp()
S = {}
def later(fn, ms=500): GLib.timeout_add(ms, fn); return False
def xdo(*keys):
    xid = S["win"].get_window().get_xid()
    subprocess.run(["xdotool", "key", "--window", str(xid), *keys], check=True)

def s1():
    S["win"] = win = app.get_active_window()
    print("step1", file=sys.stderr, flush=True)
    cb.set_text("", -1)
    win.path_entry.grab_focus(); win.path_entry.select_region(0, -1)
    xdo("ctrl+c")
    return later(s2)
def s2():
    win = S["win"]
    check("Ctrl+C in path entry copies its text", cb.wait_for_text() == tmp and not clipboard.has_files())
    col = win.columns[0]
    col.view.grab_focus(); col.view.set_cursor(Gtk.TreePath.new_first(), None, False)   # "sub" sorts first
    print("step2: sending ctrl+c to column", file=sys.stderr, flush=True)
    xdo("ctrl+c")
    return later(s3)
def s3():
    win = S["win"]
    check("Ctrl+C in column copies the selected item", clipboard.has_files() and cb.wait_for_uris() == [Gio.File.new_for_path(os.path.join(tmp, "sub")).get_uri()])
    win.columns[0].view.set_cursor(Gtk.TreePath.new_from_indices([1]), None, False)
    xdo("ctrl+x")
    return later(s4)
def s4():
    win = S["win"]
    sel = cb.wait_for_contents(Gdk.Atom.intern(clipboard.COPIED_FILES, False)); data = bytes(sel.get_data()).decode() if sel else ""
    check("Ctrl+X marks cut", data.startswith("cut\n") and "a.txt" in data)
    win.set_root(Gio.File.new_for_path(os.path.join(tmp, "sub")))
    GLib.timeout_add(400, lambda: (win.columns[0].view.grab_focus(), xdo("ctrl+v"), False)[2])
    return later(s5, 1500)
def s5():
    win = S["win"]
    check("Ctrl+V moves the file into the focused folder", os.path.exists(os.path.join(tmp, "sub", "a.txt")) and not os.path.exists(os.path.join(tmp, "a.txt")))
    cb.set_text("XYZ", -1)
    win.path_entry.grab_focus(); win.path_entry.set_text("")
    xdo("ctrl+v")
    return later(s6)
def s6():
    win = S["win"]
    check("Ctrl+V in path entry pastes text", win.path_entry.get_text() == "XYZ")
    xdo("alt+Up")
    return later(s7, 800)
def s7():
    win = S["win"]
    check("Alt+Up from the entry still navigates up", win.columns[0].gfile.get_path() == tmp)
    # F5 with the column focused (plain accelerator still works)
    win.columns[0].view.grab_focus()
    orig = win.reload_all; S["reloaded"] = False
    win.lookup_action("reload").connect("activate", lambda *a: S.__setitem__("reloaded", True))
    xdo("F5")
    return later(s8)
def s8():
    check("F5 accelerator still fires", S["reloaded"])
    cb.set_text("", -1)
    app.quit(); return False

GLib.timeout_add(800, s1)
app.run(["finch", tmp])
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if all(ok for _, ok in results) else 1)
