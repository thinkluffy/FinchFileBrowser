# The context menu's Open item names the program that will open the file.
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gio, Gtk
from app.application import FinchApp
from app import C_NAME
from app.util import open_label, default_opener
tmp = tempfile.mkdtemp(prefix="finch-openwith-", dir=os.path.expanduser("~"))
os.makedirs(os.path.join(tmp, "folder"))
for n in ("a.txt", "b.txt"): open(os.path.join(tmp, n), "w").write("x")
with open(os.path.join(tmp, "pic.png"), "wb") as f: f.write(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
open(os.path.join(tmp, "weird.xyzzyqq"), "w").write("?")
results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra, flush=True)
txt_app = Gio.AppInfo.get_default_for_type("text/plain", False).get_display_name()
png_app = Gio.AppInfo.get_default_for_type("image/png", False).get_display_name()
def labels(menu):
    return [mi.get_label() for mi in menu.get_children() if isinstance(mi, Gtk.MenuItem) and mi.get_label()]
def menu_for(w, col, names):
    rows = [r for r in col.store if r[C_NAME] in names]
    col._select_only(rows[0].path)
    for r in rows[1:]: col._select_add(r.path)
    w.tab._on_context_menu.__func__  # exists
    w._on_context_menu(col, rows[0][3], rows[0][4], None)
    return Gtk.Menu.get_for_attach_widget(col.view)[-1]
app = FinchApp(); S = {}
def later(fn, ms=500): GLib.timeout_add(ms, fn); return False
def s1():
    S["w"] = w = app.get_active_window(); col = w.columns[0]
    w.lookup_action("view-mode").change_state(GLib.Variant("s", "columns"))
    info = lambda n: next(r for r in col.store if r[C_NAME] == n)[4]
    check("default_opener: text file", default_opener([info("a.txt")]).get_display_name() == txt_app)
    check("default_opener: two text files share one", default_opener([info("a.txt"), info("b.txt")]).get_display_name() == txt_app)
    check("default_opener: text + image differ -> None", default_opener([info("a.txt"), info("pic.png")]) is None)
    check("default_opener: directory -> None", default_opener([info("folder")]) is None)
    lbl, icon = open_label([info("a.txt")])
    check('label: Open With App', lbl == "Open With %s" % txt_app and isinstance(icon, Gio.Icon), lbl)
    lbl, icon = open_label([info("folder")])
    check("label: plain Open for a folder", lbl == "Open" and icon == "document-open-symbolic")
    lbl, _i = open_label([info("weird.xyzzyqq")])
    check("label: unknown type -> plain Open or a handler", lbl == "Open" or lbl.startswith("Open With "), lbl)
    m = menu_for(w, col, ["a.txt"])
    check('menu on a text file starts with Open With App', labels(m)[0] == "Open With %s" % txt_app, str(labels(m)[:2]))
    first = [mi for mi in m.get_children() if isinstance(mi, Gtk.ImageMenuItem)][0]
    check("the item carries the app's icon", first.get_image().get_property("gicon") is not None)
    m.destroy()
    m = menu_for(w, col, ["pic.png"])
    check('image file names the image app', labels(m)[0] == "Open With %s" % png_app, str(labels(m)[:1]))
    m.destroy()
    m = menu_for(w, col, ["a.txt", "b.txt"])
    check("two text files: Open with the shared app", labels(m)[0] == "Open With %s" % txt_app, str(labels(m)[:1]))
    m.destroy()
    m = menu_for(w, col, ["a.txt", "pic.png"])
    check("mixed selection: plain Open", labels(m)[0] == "Open", str(labels(m)[:1]))
    m.destroy()
    m = menu_for(w, col, ["folder"])
    check("folder: plain Open, then Open in New Tab", labels(m)[:2] == ["Open", "Open in New Tab"], str(labels(m)[:2]))
    m.destroy()
    print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
    app.quit(); return False
GLib.timeout_add(1200, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if all(ok for _, ok in results) else 1)
