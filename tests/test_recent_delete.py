# Trash / delete / "remove from recent" on items of recent:///.
# Run through xrun_private.sh: needs the private recent list and trash.
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gio, Gtk
box = os.environ["FINCH_SANDBOX"]; files = box + "/files"
assert GLib.get_user_data_dir().startswith(box), "not sandboxed"
rm = Gtk.RecentManager.get_default()
def add(path, mime):
    d = Gtk.RecentData(); d.mime_type = mime; d.app_name = "finch-test"; d.app_exec = "true %u"
    assert rm.add_full(Gio.File.new_for_path(path).get_uri(), d)
for n in "abcdefgh":
    open("%s/%s.txt" % (files, n), "w").write("x"); add("%s/%s.txt" % (files, n), "text/plain")
os.makedirs(files + "/dir/sub"); open(files + "/dir/sub/inner.txt", "w").write("x")
# the backend hides folders recorded as inode/directory; the ones that show up
# (added by the portal's file chooser) carry application/octet-stream
add(files + "/dir", "application/octet-stream")
from app.application import FinchApp
from app import listing, window, C_NAME, C_FILE
errors, asked = [], []
def fake_error(parent, title, text): errors.append((title, text))
def fake_confirm(parent, title, *rest): asked.append(title); return True
listing.show_error = window.show_error = fake_error
listing.confirm = fake_confirm
results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra, flush=True)
real = lambda n: os.path.exists(files + "/" + n)
trashed = lambda n: os.path.exists(GLib.get_user_data_dir() + "/Trash/files/" + n)
app = FinchApp(); S = {}
def col(): return S["w"].columns[0]
def names(): return sorted(row[C_NAME] for row in col().store)
def select(*want):
    c = col(); idx = [i for i, row in enumerate(c.store) if row[C_NAME] in want]
    assert len(idx) == len(want), (want, names())
    if hasattr(c, "items"):
        c._select_only(idx[0])
    else:
        sel = c.view.get_selection(); sel.unselect_all()
        for i in idx: sel.select_path(Gtk.TreePath(i))
def gfile(name):
    return next(row[C_FILE] for row in col().store if row[C_NAME] == name)
def later(fn, ms=1200):
    def run():
        try: return fn()
        except Exception:
            import traceback; traceback.print_exc(); app.quit(); return False
    GLib.timeout_add(ms, run); return False
def mode(m): S["w"].lookup_action("view-mode").change_state(GLib.Variant("s", m))

def s1():
    S["w"] = app.get_active_window(); mode("columns")
    check("recent lists the nine items", names() == sorted([n + ".txt" for n in "abcdefgh"] + ["dir"]), str(names()))
    # the context menu: "Remove from Recent" only in Recent
    from gi.repository import Gdk
    from app import C_INFO
    shown = []
    Gtk.Menu.popup_at_pointer = lambda menu, ev: shown.append([c.get_label() for c in menu.get_children() if c.get_label()])
    ev = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    select("a.txt"); row = next(r for r in col().store if r[C_NAME] == "a.txt")
    S["w"]._on_context_menu(col(), row[C_FILE], row[C_INFO], ev)
    check("menu: one item has Remove from Recent, Move to Trash, Delete",
          shown and all(l in shown[-1] for l in ("Remove from Recent", "Move to Trash", "Delete")), str(shown[-1:]))
    select("a.txt", "b.txt")
    S["w"]._on_context_menu(col(), row[C_FILE], row[C_INFO], ev)
    check("menu: several items have Remove from Recent too", len(shown) == 2 and "Remove from Recent" in shown[-1], str(shown[-1:]))
    S["shown"] = shown; S["ev"] = ev
    select("a.txt"); col().trash_item()
    return later(s2)
def s2():
    check("trash: no error", not errors, str(errors)); del errors[:]
    check("trash: the real file went to the trash", not real("a.txt") and trashed("a.txt"))
    check("trash: the row is gone", "a.txt" not in names(), str(names()))
    select("b.txt"); col().delete_item()
    return later(s3)
def s3():
    check("delete: asks with the file's name", asked and "b.txt" in asked[-1], str(asked)); del asked[:]
    check("delete: no error", not errors, str(errors)); del errors[:]
    check("delete: the real file is gone for good", not real("b.txt") and not trashed("b.txt"))
    check("delete: the row is gone", "b.txt" not in names(), str(names()))
    select("c.txt")
    if hasattr(col(), "remove_recent"): col().remove_recent()
    return later(s4)
def s4():
    check("remove from recent: no question, no error", not asked and not errors, str(asked + errors))
    check("remove from recent: the real file stays", real("c.txt"))
    check("remove from recent: the row is gone", "c.txt" not in names(), str(names()))
    select("d.txt", "e.txt"); col().trash_item()
    return later(s5)
def s5():
    check("trash two: both in the trash, no error", trashed("d.txt") and trashed("e.txt") and not errors, str(errors)); del errors[:]
    check("trash two: rows gone", not {"d.txt", "e.txt"} & set(names()), str(names()))
    select("dir"); col().delete_item()
    return later(s6)
def s6():
    check("delete folder: asks with its name", asked and "dir" in asked[-1], str(asked)); del asked[:]
    check("delete folder: tree gone, no error", not real("dir") and not errors, str(errors)); del errors[:]
    check("delete folder: row gone", "dir" not in names(), str(names()))
    # dropped on the trash (sidebar or a trash column)
    S["w"].drop_files([gfile("f.txt")], Gio.File.new_for_uri("trash:///"), True)
    return later(s7)
def s7():
    check("drop on trash: real file trashed, no error", trashed("f.txt") and not errors, str(errors)); del errors[:]
    check("drop on trash: row gone", "f.txt" not in names(), str(names()))
    mode("grid")
    return later(s8)
def s8():
    select("g.txt"); col().trash_item(gfile("g.txt"))
    return later(s9)
def s9():
    check("grid: trash works", trashed("g.txt") and not errors and "g.txt" not in names(), str(errors) + str(names()))
    check("left alone: h.txt", real("h.txt") and "h.txt" in names())
    mode("columns")
    S["w"].set_root(Gio.File.new_for_path(files))
    return later(s10)
def s10():
    from app import C_INFO
    row = next(r for r in col().store if r[C_NAME] == "h.txt"); select("h.txt")
    S["w"]._on_context_menu(col(), row[C_FILE], row[C_INFO], S["ev"])
    labels = S["shown"][-1]
    check("menu: an ordinary folder has no Remove from Recent", "Move to Trash" in labels and "Remove from Recent" not in labels, str(labels))
    col().remove_recent()
    check("remove_recent does nothing outside Recent", real("h.txt") and not errors)
    col().trash_item()
    return later(s11)
def s11():
    check("ordinary folder: trash still works", trashed("h.txt") and not errors and "h.txt" not in names(), str(errors))
    return later(lambda: (app.quit(), False)[1], 300)
later(s1, 2500)
app.run(["finch", "recent:///"])
print("%d/%d passed" % (sum(ok for _n, ok in results), len(results)))
