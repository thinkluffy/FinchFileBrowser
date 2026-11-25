import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gtk, Gdk
from app.application import FinchApp
from app import VERSION
app = FinchApp(); fails = []
def check(n, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + n, extra, flush=True)
    if not ok: fails.append(n)
def go():
    w = app.get_active_window()
    labels = []
    def walk(item):
        if isinstance(item, Gtk.MenuItem):
            labels.append(item.get_label()); sub = item.get_submenu()
            if sub: [walk(c) for c in sub.get_children()]
    [walk(c) for c in w.app_menu.get_children()]
    check("menu ends with About", labels[-1] == "About Finch", labels[-3:])
    w.lookup_action("about").activate()
    GLib.timeout_add(600, look); return False
def look():
    w = app.get_active_window()
    dlgs = [t for t in Gtk.Window.list_toplevels() if isinstance(t, Gtk.AboutDialog)]
    ok = len(dlgs) == 1 and dlgs[0].get_transient_for() is w and dlgs[0].get_version() == VERSION and dlgs[0].get_program_name() == "Finch"
    check("About dialog shown with the version", ok, (len(dlgs), dlgs and dlgs[0].get_version()))
    if dlgs:
        gw = dlgs[0].get_window(); s = dlgs[0].get_scale_factor()
        if len(sys.argv) > 1:           # a screenshot of the dialog, when a file name is given
            Gdk.pixbuf_get_from_window(gw, 0, 0, gw.get_width(), gw.get_height()).savev(sys.argv[1], "png", [], [])
        dlgs[0].response(Gtk.ResponseType.DELETE_EVENT)
    GLib.timeout_add(300, done); return False
def done():
    dlgs = [t for t in Gtk.Window.list_toplevels() if isinstance(t, Gtk.AboutDialog) and t.get_visible()]
    check("closing destroys it", not dlgs)
    print("%d failures" % len(fails)); app.quit(); return False
GLib.timeout_add(1200, go)
app.run(["finch", tempfile.mkdtemp(prefix="finch-about-")])
