import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk
from app.window import FinchWindow
from app import C_FILE

tmp = tempfile.mkdtemp(prefix="finch-test-", dir=os.path.expanduser("~"))
open(os.path.join(tmp, "only.txt"), "w").write("hello")
results = []

class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="daocheng.FinchTest", flags=Gio.ApplicationFlags.NON_UNIQUE)
    def do_activate(self):
        self.win = FinchWindow(self, Gio.File.new_for_path(tmp), {})
        self.win.show_all()
        GLib.timeout_add(500, self.step1)
    def step1(self):
        col = self.win.columns[0]
        col.view.set_cursor(Gtk.TreePath.new_first(), None, False)
        GLib.timeout_add(500, self.step2)
        return False
    def step2(self):
        results.append(("preview shown after select", self.win.preview is not None))
        # Scenario A: file vanishes, reload the way empty_trash does
        os.remove(os.path.join(tmp, "only.txt"))
        col = self.win.columns[0]
        col._enumerate(None, emit=True)
        GLib.timeout_add(700, self.step3)
        return False
    def step3(self):
        results.append(("preview removed after emit=True reload with nothing", self.win.preview is None))
        # Scenario B: trash the only file via trash_item
        open(os.path.join(tmp, "only2.txt"), "w").write("x")
        col = self.win.columns[0]
        col._enumerate(None, emit=True)
        GLib.timeout_add(700, self.step4)
        return False
    def step4(self):
        col = self.win.columns[0]
        col.view.set_cursor(Gtk.TreePath.new_first(), None, False)
        GLib.timeout_add(500, self.step5)
        return False
    def step5(self):
        results.append(("preview shown again", self.win.preview is not None))
        self.win.columns[0].trash_item()
        GLib.timeout_add(700, self.step6)
        return False
    def step6(self):
        results.append(("preview removed after trashing last item", self.win.preview is None))
        self.quit()
        return False

App().run([])
shutil.rmtree(tmp, ignore_errors=True)
ok = all(r for _, r in results)
for name, r in results:
    print(("PASS" if r else "FAIL"), name)
sys.exit(0 if ok else 1)
