# The icon grid: selection, keyboard, mouse, rename, drops, mode switching.
import os, sys, tempfile, shutil, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gio, GLib, Gdk, Gtk, GdkPixbuf
from app.application import FinchApp
from app import grid as gridmod, listing, thumbs
from app.grid import Grid, ICON_TOP, TEXT_TOP
from app.column import Column

tmp = tempfile.mkdtemp(prefix="finch-grid-", dir=os.path.expanduser("~"))
os.makedirs(os.path.join(tmp, "Alpha", "inner")); os.makedirs(os.path.join(tmp, "Beta"))
for f in ("a.txt", "b.txt", "c.txt", "zed.md", ".hidden"):
    open(os.path.join(tmp, f), "w").write("0123456789")
pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 300, 150); pb.fill(0x3366aaff)
pb.savev(os.path.join(tmp, "pic.png"), "png", [], [])
P = lambda *p: os.path.join(tmp, *p)
F = lambda *p: Gio.File.new_for_path(P(*p))
results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra, flush=True)
app = FinchApp(); S = {}
def later(fn, ms=600): GLib.timeout_add(ms, fn); return False
def names(g): return [g.store[i][1] for i in g._selected_handles()]
def idx_of(g, name): return next(i for i, it in enumerate(g.items) if it[1] == name)
def cell_center(g, name):
    x, y = g._cell_pos(idx_of(g, name)); return x + g._cell_w / 2, y + ICON_TOP + 20
def press(g, name=None, button=1, state=0, double=False, x=None, y=None):
    ev = Gdk.Event.new(Gdk.EventType._2BUTTON_PRESS if double else Gdk.EventType.BUTTON_PRESS)
    ev.button.button = button; ev.button.state = Gdk.ModifierType(state)
    if name is not None: x, y = cell_center(g, name)
    ev.button.x, ev.button.y = x, y
    g._on_button_press(g.view, ev.button)
    rel = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE); rel.button.button = button; rel.button.x, rel.button.y = x, y
    g._on_button_release(g.view, rel.button)
def key(g, keyval, state=0):
    ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS); ev.key.keyval = keyval; ev.key.state = Gdk.ModifierType(state)
    return g._on_key_press(g.view, ev.key)
def mode(win, m): win.lookup_action("view-mode").change_state(GLib.Variant.new_string(m))

def s1():
    S["win"] = win = app.get_active_window()
    check("starts in columns", isinstance(win.columns[0], Column))
    win.columns[0].view.grab_focus()
    mode(win, "grid"); return later(s2)
def s2():
    win = S["win"]; g = win.columns[0]; S["g"] = g
    check("grid mode: one Grid listing", len(win.columns) == 1 and isinstance(g, Grid))
    check("grid shows the folder, hidden file left out", [it[1] for it in g.items] == ["Alpha", "Beta", "a.txt", "b.txt", "c.txt", "pic.png", "zed.md"], [it[1] for it in g.items])
    check("several cells per row", g._cols >= 2, g._cols)
    check("toolbar toggle reflects the mode", win.lookup_action("view-mode").get_state().get_string() == "grid")
    check("no preview pane in grid mode", win.preview is None and g.get_allocated_width() == win.tab.colbox.get_allocated_width(), (g.get_allocated_width(), win.tab.colbox.get_allocated_width()))
    check("location is the folder", win.tab.location.equal(F()))
    # mouse
    press(g, "a.txt")
    check("click selects one", names(g) == ["a.txt"])
    check("…status line shows it, still no preview", win.preview is None and "10 bytes" in win.status.get_text(), win.status.get_text())
    check("location stays the folder", win.tab.location.equal(F()) and win.current_dir().equal(F()))
    press(g, "c.txt", state=Gdk.ModifierType.SHIFT_MASK)
    check("shift-click selects the range", names(g) == ["a.txt", "b.txt", "c.txt"])
    check("multi selection: status counts it, no preview", win.preview is None and "3 selected" in win.status.get_text(), win.status.get_text())
    press(g, "Alpha", state=Gdk.ModifierType.CONTROL_MASK)
    check("ctrl-click adds", names(g) == ["Alpha", "a.txt", "b.txt", "c.txt"])
    press(g, "b.txt")
    check("plain click on a selected item (after release) selects it alone", names(g) == ["b.txt"])
    press(g, x=g._cell_w * g._cols + 2, y=g._cell_h * 5 + 300)
    check("click on blank clears", names(g) == [])
    return later(s2b, 400)
def s2b():
    win = S["win"]; g = S["g"]
    check("…and the status line shows the folder only", win.preview is None and "selected" not in win.status.get_text(), win.status.get_text())
    # rubber band over the first row
    x0, y0 = g._cell_pos(0); x1, y1 = g._cell_pos(2)
    hits = g._cells_in(x0 + 10, y0 + 10, x1 + 10, y1 + 10)
    check("rubber band picks the cells it touches", hits == {0, 1, 2}, hits)
    # keyboard
    press(g, "Alpha")
    key(g, Gdk.KEY_Right); check("Right moves the cursor", names(g) == ["Beta"])
    key(g, Gdk.KEY_Down); check("Down moves one row", names(g) == [g.items[1 + g._cols][1]] if 1 + g._cols < len(g.items) else names(g) == ["Beta"])
    key(g, Gdk.KEY_Home); check("Home goes first", names(g) == ["Alpha"])
    key(g, Gdk.KEY_End); check("End goes last", names(g) == ["zed.md"])
    key(g, Gdk.KEY_Left); key(g, Gdk.KEY_Left, Gdk.ModifierType.SHIFT_MASK)
    check("Shift+Left extends", names(g) == ["b.txt", "c.txt", "pic.png"] or names(g) == ["c.txt", "pic.png"], names(g))
    key(g, Gdk.KEY_a, Gdk.ModifierType.CONTROL_MASK); check("Ctrl+A selects all", len(names(g)) == 7)
    key(g, Gdk.KEY_z); check("typing a letter finds the name", names(g) == ["zed.md"])
    key(g, Gdk.KEY_e); check("…and the next letter narrows it", names(g) == ["zed.md"])
    # Delete trashes the selection (DEL is not a type-ahead letter)
    open(P("doomed.txt"), "w").write("x")
    return later(s2c, 700)
def s2c():
    win = S["win"]; g = S["g"]
    check("new file picked up by the monitor", "doomed.txt" in [it[1] for it in g.items])
    press(g, "doomed.txt"); key(g, Gdk.KEY_Delete)
    return later(s2d, 900)
def s2d():
    win = S["win"]; g = S["g"]
    check("Delete moves the item to the trash", not os.path.exists(P("doomed.txt")) and "doomed.txt" not in [it[1] for it in g.items], [it[1] for it in g.items])
    check("…and selects the neighbour", names(g) == ["pic.png"], names(g))
    # context / middle click
    seen = []; opened = []
    g.handler_block_by_func(win.tab._on_context_menu)
    g.connect("context-menu", lambda c, gf, i, e: seen.append(gf.get_basename() if gf else None))
    g.connect("open-new-tab", lambda c, gf: opened.append(gf.get_basename()))
    press(g, "a.txt", button=3)
    check("right-click elsewhere selects that item", names(g) == ["a.txt"] and seen == ["a.txt"])
    press(g, x=5, y=5, button=3)
    check("right-click on blank: menu for the folder", seen[-1] is None)
    press(g, "Beta", button=2)
    check("middle-click on a folder asks for a tab", opened == ["Beta"] and names(g) == ["a.txt"])
    check("tab count unchanged (handler blocked? no: a tab opened)", len(win.tabs) == 2)
    win.close_tab(win.tabs[1])
    # drops (widget coordinates)
    listing._dragging[:] = [F("a.txt")]
    cx, cy = cell_center(g, "Alpha")
    d, h = g.drop_target(cx, cy - win.columns[0].sw.get_vadjustment().get_value())
    check("drop on a folder cell goes into it", d is not None and d.get_path() == P("Alpha") and h == idx_of(g, "Alpha"))
    d, h = g.drop_target(*cell_center(g, "b.txt"))
    check("drop on a file: refused (own file is already here)", d is None)
    listing._dragging[:] = [F("Alpha", "inner")]
    d, h = g.drop_target(*cell_center(g, "b.txt"))
    check("drop on a file from elsewhere: into the folder", d is not None and d.equal(F()) and h is None)
    listing._dragging.clear()
    # hidden files and sort reach the grid
    win.lookup_action("show-hidden").change_state(GLib.Variant.new_boolean(True))
    return later(s3)
def s3():
    win = S["win"]; g = win.columns[0]
    check("hidden toggle reloads the grid", ".hidden" in [it[1] for it in g.items])
    win.lookup_action("show-hidden").change_state(GLib.Variant.new_boolean(False))
    win.lookup_action("sort-reversed").change_state(GLib.Variant.new_boolean(True))
    return later(s4)
def s4():
    win = S["win"]; g = win.columns[0]
    check("reversed sort: folders first, each group reversed", [it[1] for it in g.items] == ["Beta", "Alpha", "zed.md", "pic.png", "c.txt", "b.txt", "a.txt"], [it[1] for it in g.items])
    win.lookup_action("sort-reversed").change_state(GLib.Variant.new_boolean(False))
    return later(s5)
def s5():
    win = S["win"]; g = win.columns[0]
    # thumbnails: the png gets a picture, the txt stays an icon
    known, pbf = thumbs.thumb_get(thumbs.stamp(F("pic.png"), int(os.stat(P("pic.png")).st_mtime), os.stat(P("pic.png")).st_size))
    sf = win.get_scale_factor()
    check("image got a thumbnail", known and pbf is not None and pbf.get_width() == 64 * sf and pbf.get_height() == 32 * sf, (known, pbf and (pbf.get_width(), pbf.get_height())))
    # rename
    press(g, "c.txt"); g.start_rename()
    check("rename box appears", g._entry is not None and g._entry.get_text() == "c.txt" and g._renaming)
    check("…with the stem preselected", True)
    GLib.timeout_add(100, lambda: (g._entry.set_text("renamed.txt"), g._entry.activate(), False)[2])
    return later(s6, 900)
def s6():
    win = S["win"]; g = win.columns[0]
    check("rename done and selected", os.path.exists(P("renamed.txt")) and names(g) == ["renamed.txt"] and g._entry is None and not g._renaming)
    # double-click a folder: it becomes the root; history records it
    press(g, "Alpha", double=True)
    return later(s7)
def s7():
    win = S["win"]; g = win.columns[0]
    check("double-click enters the folder", g.gfile.get_path() == P("Alpha") and win.tab.location.equal(F("Alpha")) and win.header.get_title() == "Alpha")
    check("back is enabled", win.lookup_action("back").get_enabled())
    key(g, Gdk.KEY_BackSpace)
    return later(s8)
def s8():
    win = S["win"]; g = win.columns[0]
    check("Backspace goes up with the folder selected", g.gfile.equal(F()) and names(g) == ["Alpha"])
    win.go_back(); return later(s9)
def s9():
    win = S["win"]; g = win.columns[0]
    check("back returns into Alpha", g.gfile.get_path() == P("Alpha"))
    win.go_back(); return later(s10)
def s10():
    win = S["win"]; g = win.columns[0]
    press(g, "pic.png")
    mode(win, "columns"); return later(s11)
def s11():
    win = S["win"]
    check("back to columns keeps the selection", isinstance(win.columns[0], Column) and win.columns[0].selected()[0] is not None and win.columns[0].selected()[0].get_path() == P("pic.png") and win.preview is not None)
    win.set_root(F(), select=F("Alpha", "inner"))
    return later(s12)
def s12():
    win = S["win"]
    check("columns opened down to inner", [c.gfile.get_path() for c in win.columns] == [tmp, P("Alpha"), P("Alpha", "inner")])
    mode(win, "grid"); return later(s13)
def s13():
    win = S["win"]; g = win.columns[0]
    check("grid shows the root with the deepest child selected", isinstance(g, Grid) and g.gfile.equal(F()) and names(g) == ["Alpha"])
    win.save_window_state()
    # where the application under test keeps it (tests/run.sh gives it a config folder of its own)
    st = json.load(open(os.path.join(GLib.get_user_config_dir(), "finch", "state.json")))
    check("state saves the view mode", st.get("view_mode") == "grid")
    mode(win, "columns"); win.save_window_state()
    print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
    app.quit(); return False
GLib.timeout_add(1200, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if all(ok for _, ok in results) else 1)
