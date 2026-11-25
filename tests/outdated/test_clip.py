import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from app.window import FinchWindow
from app.transfer import Transfer as Job
from app import clipboard

tmp = tempfile.mkdtemp(prefix="finch-clip-", dir=os.path.expanduser("~"))
os.makedirs(os.path.join(tmp, "src", "tree", "sub"))
open(os.path.join(tmp, "src", "a.txt"), "w").write("hello")
open(os.path.join(tmp, "src", "tree", "sub", "deep.txt"), "w").write("deep")
os.symlink("a.txt", os.path.join(tmp, "src", "link"))
os.makedirs(os.path.join(tmp, "dst"))
F = lambda *p: Gio.File.new_for_path(os.path.join(tmp, *p))
results = []
def check(name, ok): results.append((name, bool(ok)))
cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="daocheng.FinchClipTest", flags=Gio.ApplicationFlags.NON_UNIQUE)
    def do_activate(self):
        self.win = FinchWindow(self, F("src"), {})
        self.win.show_all()
        GLib.timeout_add(500, self.s1)
    def later(self, fn, ms=600): GLib.timeout_add(ms, fn); return False

    def s1(self):
        # copy a.txt: check clipboard formats
        self.win.copy_to_clipboard(F("src", "a.txt"))
        sel = cb.wait_for_contents(Gdk.Atom.intern(clipboard.COPIED_FILES, False))
        data = bytes(sel.get_data()).decode()
        check("gnome-copied-files starts with copy", data.startswith("copy\n"))
        check("uri-list has file", cb.wait_for_uris() == [F("src", "a.txt").get_uri()])
        check("text is the path", cb.wait_for_text() == os.path.join(tmp, "src", "a.txt"))
        check("has_files", clipboard.has_files())
        check("paste action enabled", self.win.lookup_action("paste").get_enabled())
        # paste into the same folder -> a copy with a new name
        self.win.paste(self.win.columns[0].gfile)
        return self.later(self.s2)
    def s2(self):
        names = sorted(os.listdir(os.path.join(tmp, "src")))
        copies = [n for n in names if n.startswith("a ") and n.endswith(".txt")]
        check("pasted next to original with suffix", len(copies) == 1 and open(os.path.join(tmp, "src", copies[0])).read() == "hello")
        self.copy_name = copies[0] if copies else None
        sel = self.win.columns[0].selected()[0]
        check("pasted item selected", sel is not None and sel.get_basename() == self.copy_name)
        # paste a directory tree + a symlink into dst via transfer
        Job([F("src", "tree"), F("src", "link")], F("dst"), False).start(lambda j: None, lambda j, c, e, x: self.s3(c, e))
        return False
    def s3(self, created, errors):
        check("tree copied recursively", open(os.path.join(tmp, "dst", "tree", "sub", "deep.txt")).read() == "deep" and not errors)
        check("symlink copied as symlink", os.path.islink(os.path.join(tmp, "dst", "link")))
        check("created list", sorted(c.get_basename() for c in created) == ["link", "tree"])
        # copy a folder into itself -> error, nothing created
        Job([F("src", "tree")], F("src", "tree", "sub"), False).start(lambda j: None, lambda j, c, e, x: self.s4(c, e))
        return False
    def s4(self, created, errors):
        check("copy into itself refused", not created and len(errors) == 1)
        # cut a.txt and paste into dst
        self.win.copy_to_clipboard(F("src", "a.txt"), cut=True)
        data = bytes(cb.wait_for_contents(Gdk.Atom.intern(clipboard.COPIED_FILES, False)).get_data()).decode()
        check("cut marked in clipboard", data.startswith("cut\n"))
        self.win.set_root(F("dst"))
        GLib.timeout_add(500, lambda: (self.win.paste(), False)[1])
        return self.later(self.s5, 1300)
    def s5(self):
        check("cut file moved", os.path.exists(os.path.join(tmp, "dst", "a.txt")) and not os.path.exists(os.path.join(tmp, "src", "a.txt")))
        check("clipboard cleared after cut-paste", not clipboard.has_files())
        check("paste action disabled now", not self.win.lookup_action("paste").get_enabled())
        # Ctrl+C in the path entry copies text, not the file
        entry = self.win.path_entry
        entry.grab_focus(); entry.select_region(0, -1)
        ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        ev.key.keyval = Gdk.KEY_c
        ev.key.state = Gdk.ModifierType.CONTROL_MASK
        ev.key.window = self.win.get_window()
        ev.key.send_event = True
        self.win.event(ev)
        return self.later(self.s6, 300)
    def s6(self):
        check("Ctrl+C in path entry copies text", cb.wait_for_text() == os.path.join(tmp, "dst") and not clipboard.has_files())
        # and Ctrl+C with the column focused copies the selected file
        col = self.win.columns[0]
        col.view.grab_focus(); col.view.set_cursor(Gtk.TreePath.new_first(), None, False)
        ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        ev.key.keyval = Gdk.KEY_c; ev.key.state = Gdk.ModifierType.CONTROL_MASK
        ev.key.window = self.win.get_window(); ev.key.send_event = True
        self.win.event(ev)
        return self.later(self.s7, 300)
    def s7(self):
        check("Ctrl+C in column copies the file", clipboard.has_files() and len(cb.wait_for_uris()) == 1)
        cb.set_text("", -1)
        self.quit(); return False

App().run([])
shutil.rmtree(tmp, ignore_errors=True)
for name, ok in results: print(("PASS" if ok else "FAIL"), name)
sys.exit(0 if all(ok for _, ok in results) else 1)
