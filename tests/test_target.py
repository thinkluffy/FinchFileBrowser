import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from app.application import FinchApp
from app import clipboard

tmp = tempfile.mkdtemp(prefix="finch-tgt-", dir=os.path.expanduser("~"))
os.makedirs(os.path.join(tmp, "sub")); open(os.path.join(tmp, "a.txt"), "w").write("hi")
P = lambda *p: os.path.join(tmp, *p)
results = []
def check(name, ok):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, flush=True)
app = FinchApp(); S = {}
def later(fn, ms=600): GLib.timeout_add(ms, fn); return False
def select(col, name):
    for row in col.store:
        if row[1] == name: col.view.set_cursor(row.path, None, False); return

def s1():
    S["win"] = win = app.get_active_window()
    check("nothing selected: current dir is the column's folder", win.current_dir().get_path() == tmp)
    win.copy_to_clipboard(Gio.File.new_for_path(P("a.txt")))
    select(win.columns[0], "sub")
    return later(s2)
def s2():
    win = S["win"]
    check("folder selected: current dir is that folder", win.current_dir().get_path() == P("sub"))
    check("focus still in first column", win._focused_column() is win.columns[0])
    win.paste()                      # Ctrl+V path
    return later(s3, 900)
def s3():
    win = S["win"]
    check("pasted into the selected folder", os.path.exists(P("sub", "a.txt")) and os.listdir(tmp).count("a.txt") == 1 and not any(n.startswith("a ") for n in os.listdir(tmp)))
    check("pasted item selected in the folder's column", len(win.columns) == 2 and win.columns[1].selected()[0] is not None and win.columns[1].selected()[0].get_path() == P("sub", "a.txt"))
    # file selected in the focused column: current dir is the column's folder
    win.columns[0].view.grab_focus(); select(win.columns[0], "a.txt")
    return later(s4)
def s4():
    win = S["win"]
    check("file selected: current dir is the column's folder", win.current_dir().get_path() == tmp)
    # new folder with a folder selected goes inside it
    select(win.columns[0], "sub")
    return later(s5)
def s5():
    win = S["win"]; win.new_folder(); return later(s6, 900)
def s6():
    win = S["win"]
    made = [n for n in os.listdir(P("sub")) if n != "a.txt"]
    check("new folder created inside the selected folder", len(made) == 1 and os.path.isdir(P("sub", made[0])))
    check("…and its rename editor opened in that column", win.columns[1]._renaming)
    win.columns[1].name_renderer.stop_editing(True)   # cancel the edit
    # explicit target (context menu on a folder)
    win.copy_to_clipboard(Gio.File.new_for_path(P("a.txt")))
    win.paste(Gio.File.new_for_path(P("sub", made[0])))
    return later(s7, 900)
def s7():
    made = [n for n in os.listdir(P("sub")) if n != "a.txt"]
    check("explicit folder target pastes into it", os.path.exists(P("sub", made[0], "a.txt")))
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text("", -1)
    app.quit(); return False

GLib.timeout_add(800, s1)
app.run(["finch", tmp])
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if all(ok for _, ok in results) else 1)
