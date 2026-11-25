# The sidebar's Network row, entering pointer entries (network computers,
# shares; here the "File System" entry of computer:///, which works the same
# and is always there) and mounting on demand (here a zip through gvfs'
# archive backend: needs mounting, no server, no password).
# Run through xrun_private.sh: the mounts stay in the private session.
import os, sys, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gio, Gtk, Gdk
box = os.environ["FINCH_SANDBOX"]; files = box + "/files"
def archive_uri(path):
    return "archive://" + GLib.uri_escape_string(Gio.File.new_for_path(path).get_uri(), None, False).replace("%", "%25") + "/"
with zipfile.ZipFile(files + "/t.zip", "w") as z:
    z.writestr("in/a.txt", "x"); z.writestr("top.txt", "y")
ARCH = Gio.File.new_for_uri(archive_uri(files + "/t.zip"))
MISSING = Gio.File.new_for_uri(archive_uri(files + "/missing.zip"))
from app.application import FinchApp
from app import window, tab as tabmod, listing, C_NAME, C_FILE, C_INFO
from app.sidebar import _place_type, PLACE_CONNECT_TO_SERVER
errors = []
def fake_error(parent, title, text): errors.append((title, text))
window.show_error = tabmod.show_error = listing.show_error = fake_error
results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra, flush=True)
app = FinchApp(); S = {}
def later(fn, ms=1000):
    def run():
        try: return fn()
        except Exception:
            import traceback; traceback.print_exc(); app.quit(); return False
    GLib.timeout_add(ms, run); return False
def w(): return S["w"]
def mode(m): w().lookup_action("view-mode").change_state(GLib.Variant("s", m))
def cols(): return [c.gfile.get_uri() for c in w().tab.columns]
def names(i=0): return sorted(r[C_NAME] for r in w().tab.columns[i].store)
def row(name, i=0): return next(r for r in w().tab.columns[i].store if r[C_NAME] == name)
def select(name, i=0):
    c = w().tab.columns[i]; c._select_only(c.handle_of(row(name, i)[C_FILE]))
def page(i=0):
    c = w().tab.columns[i]; return c.stack.get_visible_child_name(), c.message.get_text()
def state(): return "cols=%s loc=%s title=%r page=%s errors=%s" % (cols(), w().tab.location.get_uri(), w().get_title(), page(), errors)
shown = []
Gtk.Menu.popup_at_pointer = lambda menu, ev: shown.append([c.get_label() for c in menu.get_children() if c.get_label()])

def s1():
    S["w"] = app.get_active_window(); mode("columns")
    sb = w().sidebar; rows = [r for r in sb._list.get_children() if r.get_visible()]
    last = rows[-1]; S["netrow"] = last
    check("sidebar: the last row is Network", _place_type(last) == PLACE_CONNECT_TO_SERVER and last.get_property("label") == "Network", str([r.get_property("label") for r in rows]))
    head = last.get_header()
    check("sidebar: under a Network heading", head is not None and head.get_text() == "Network", repr(head and head.get_text()))
    heads = [r.get_header().get_text() for r in rows if r.get_header() is not None]
    check("sidebar: headings Places, Devices, Network", heads[0] == "Places" and "Devices" in heads and heads[-1] == "Network" and heads.count("Network") == 1, str(heads))
    # a real click, on the nested X server only (DISPLAY is :7 here)
    import subprocess
    assert os.environ["DISPLAY"] == ":7"
    alloc = last.get_allocation(); x, y = last.translate_coordinates(w(), alloc.width // 2, alloc.height // 2)
    ox, oy = w().get_window().get_origin()[1:]
    subprocess.run(["xdotool", "mousemove", str(ox + x), str(oy + y), "click", "1"], check=True)
    return later(s2, 2500)
def s2():
    check("Network row: opens network:///", cols() == ["network:///"] and w().get_title() == "Network", state())
    check("Network row: stays highlighted", w().sidebar._list.get_selected_row() is S["netrow"] or
          _place_type(w().sidebar._list.get_selected_row()) == PLACE_CONNECT_TO_SERVER)
    check("Network: no error dialog", not errors, str(errors))
    print("   network lists:", names(), page())
    w().set_root(Gio.File.new_for_uri("computer:///"))
    return later(s3, 1500)
def s3():
    check("computer:/// lists File System", "File System" in names(), str(names()))
    select("File System")
    return later(s4)
def s4():
    pv = w().tab.preview
    check("pointer entry selected: a preview, no column, location unchanged", cols() == ["computer:///"] and pv is not None, state())
    check("preview: says where it leads", pv is not None and pv.summary.get_text() == "Network location" and ("Links to", "/") in pv._rows, str(pv and (pv.summary.get_text(), pv._rows)))
    r = row("File System")
    w()._on_context_menu(w().tab.columns[0], r[C_FILE], r[C_INFO], Gdk.Event.new(Gdk.EventType.BUTTON_PRESS))
    check("context menu: Open, Open in New Tab, Open in Nemo, Copy Path; no Rename or Trash",
          shown and shown[-1][:4] == ["Open", "Open in New Tab", "Open in Nemo", "Copy Path"] and not {"Rename", "Move to Trash", "Cut"} & set(shown[-1]), str(shown[-1:]))
    c = w().tab.columns[0]; n = len(w().tabs)
    c._press_middle(c.handle_of(r[C_FILE]))
    S["n"] = n
    return later(s5)
def s5():
    check("middle click: opens its target in a new tab", len(w().tabs) == S["n"] + 1 and w().tabs[-1].root.get_uri() == "file:///", str([t.root.get_uri() for t in w().tabs]))
    c = w().tab.columns[0]
    c.emit("item-activated", row("File System")[C_FILE], row("File System")[C_INFO])
    return later(s6)
def s6():
    check("activate (double click / Ctrl+O): goes to the target", cols() == ["file:///"], state())
    w().go_back()
    return later(s7)
def s7():
    check("back: computer:/// again", cols()[0] == "computer:///", state())
    select("File System")
    w().tab._on_key_nav(w().tab.columns[0], "right")
    return later(s8)
def s8():
    check("Right on a pointer entry: enters it", cols() == ["file:///"], state())
    # mount on demand
    w().set_root(ARCH)
    return later(s9, 2500)
def s9():
    check("unmounted location: mounted and listed", names() == ["in", "top.txt"] and page()[0] == "list", state())
    check("its title is the mount's name", w().get_title() == "t.zip", state())
    check("no error dialog", not errors, str(errors))
    bar = [b.get_child().get_children()[-1].get_text() for g, b in w().pathbar._crumbs]
    check("path bar: one level, named like the mount", bar == ["t.zip"], str(bar))
    select("in")
    return later(s10)
def s10():
    check("inside the mount: columns work", len(cols()) == 2 and names(1) == ["a.txt"], state())
    ARCH.find_enclosing_mount(None).unmount_with_operation(0, None, None, lambda m, r: m.unmount_with_operation_finish(r))
    return later(s11, 1500)
def s11():
    # typed into the path entry while not mounted
    w()._on_path_entered(w().pathbar, ARCH.get_child("in").get_uri())
    return later(s12, 2500)
def s12():
    check("typed address of an unmounted folder: no error, mounted, listed", not errors and names() == ["a.txt"], state())
    w().set_root(MISSING)
    return later(s13, 2500)
def s13():
    check("mount fails: the reason is shown in the column, no dialog, no hang", page()[0] == "message" and page()[1] not in ("", "Connecting…") and not errors, state())
    mode("grid")
    return later(s14)
def s14():
    w().set_root(Gio.File.new_for_uri("computer:///"))
    return later(s15, 1500)
def s15():
    g = w().tab.columns[0]
    g.emit("item-activated", row("File System")[C_FILE], row("File System")[C_INFO])
    return later(s16)
def s16():
    check("grid: activating a pointer entry goes to the target", cols() == ["file:///"], state())
    mode("columns")
    try: ARCH.find_enclosing_mount(None).unmount_with_operation(0, None, None, lambda m, r: m.unmount_with_operation_finish(r))
    except GLib.Error: pass
    return later(lambda: (app.quit(), False)[1], 1200)
later(s1, 2000)
app.run(["finch", GLib.get_home_dir()])
print("%d/%d passed" % (sum(ok for _n, ok in results), len(results)))
