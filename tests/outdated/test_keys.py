import os, sys, tempfile, shutil, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from app.window import FinchWindow
from app import clipboard

tmp = tempfile.mkdtemp(prefix="finch-keys-", dir=os.path.expanduser("~"))
open(os.path.join(tmp, "a.txt"), "w").write("hello")
os.makedirs(os.path.join(tmp, "sub"))
results = []
def check(name, ok): results.append((name, bool(ok)))
cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
calls = []

class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="daocheng.FinchKeyTest", flags=Gio.ApplicationFlags.NON_UNIQUE)
    def do_activate(self):
        self.win = FinchWindow(self, Gio.File.new_for_path(tmp), {})
        orig = self.win.do_key_press_event
        self.win.show_all(); self.win.present()
        GLib.timeout_add(800, self.s1)
    def later(self, fn, ms=500): GLib.timeout_add(ms, fn); return False
    def xdo(self, *keys):
        subprocess.run(["xdotool", "key", "--clearmodifiers", *keys], check=True)

    def s1(self):
        check("window is active", self.win.is_active())
        cb.set_text("", -1)
        entry = self.win.path_entry
        entry.grab_focus(); entry.select_region(0, -1)
        self.xdo("ctrl+c")
        return self.later(self.s2)
    def s2(self):
        check("Ctrl+C in path entry copies its text", cb.wait_for_text() == tmp and not clipboard.has_files())
        col = self.win.columns[0]
        col.view.grab_focus(); col.view.set_cursor(Gtk.TreePath.new_first(), None, False)   # "sub" sorts first
        self.xdo("ctrl+c")
        return self.later(self.s3)
    def s3(self):
        check("Ctrl+C in column copies the selected item", clipboard.has_files() and cb.wait_for_uris() == [Gio.File.new_for_path(os.path.join(tmp, "sub")).get_uri()])
        # select a.txt, cut it, go into sub, paste
        col = self.win.columns[0]
        col.view.set_cursor(Gtk.TreePath.new_from_indices([1]), None, False)
        self.xdo("ctrl+x")
        return self.later(self.s4)
    def s4(self):
        data = bytes(cb.wait_for_contents(Gdk.Atom.intern(clipboard.COPIED_FILES, False)).get_data()).decode()
        check("Ctrl+X marks cut", data.startswith("cut\n") and "a.txt" in data)
        self.win.set_root(Gio.File.new_for_path(os.path.join(tmp, "sub")))
        GLib.timeout_add(400, lambda: (self.win.columns[0].view.grab_focus(), self.xdo("ctrl+v"), False)[2])
        return self.later(self.s5, 1500)
    def s5(self):
        check("Ctrl+V moves the file into the focused folder", os.path.exists(os.path.join(tmp, "sub", "a.txt")) and not os.path.exists(os.path.join(tmp, "a.txt")))
        # Ctrl+V in the path entry pastes text, not files
        cb.set_text("XYZ", -1)
        entry = self.win.path_entry
        entry.grab_focus(); entry.set_text(""); 
        self.xdo("ctrl+v")
        return self.later(self.s6)
    def s6(self):
        check("Ctrl+V in path entry pastes text", self.win.path_entry.get_text() == "XYZ")
        # Alt+Up from the entry still goes up (accelerator falls through)
        self.xdo("alt+Up")
        return self.later(self.s7, 800)
    def s7(self):
        check("Alt+Up from the entry still navigates up", self.win.columns[0].gfile.get_path() == tmp)
        cb.set_text("", -1)
        self.quit(); return False

App().run([])
shutil.rmtree(tmp, ignore_errors=True)
for name, ok in results: print(("PASS" if ok else "FAIL"), name)
sys.exit(0 if all(ok for _, ok in results) else 1)
