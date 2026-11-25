import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from app.application import FinchApp
from app import clipboard, column as colmod, listing

tmp = tempfile.mkdtemp(prefix="finch-multi-", dir=os.path.expanduser("~"))
os.makedirs(os.path.join(tmp, "dir1")); os.makedirs(os.path.join(tmp, "dir2")); os.makedirs(os.path.join(tmp, "dest"))
for n, size in (("a.txt", 10), ("b.txt", 20), ("c.txt", 30), ("d.txt", 40)):
    open(os.path.join(tmp, n), "w").write("x" * size)
P = lambda *p: os.path.join(tmp, *p)
F = lambda *p: Gio.File.new_for_path(P(*p))
results = []
def check(name, ok):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, flush=True)
app = FinchApp(); S = {}
def later(fn, ms=700): GLib.timeout_add(ms, fn); return False
def path_of(col, name):
    for row in col.store:
        if row[1] == name: return row.path
def select(col, *names):
    sel = col.view.get_selection()
    col.view.set_cursor(path_of(col, names[0]), None, False)
    for n in names[1:]: sel.select_path(path_of(col, n))
names = lambda col: [r[1] for r in col.store]
cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

def s1():
    S["win"] = win = app.get_active_window(); win.resize(1100, 700)
    col = win.columns[0]
    # no rubber banding: with it GtkTreeView turns a drag from an unselected row into a rubber band
    check("selection mode is MULTIPLE, without rubber banding", col.view.get_selection().get_mode() == Gtk.SelectionMode.MULTIPLE and not col.view.get_rubber_banding())
    select(col, "dir1"); return later(s2)
def s2():
    win = S["win"]; col = win.columns[0]
    check("single selection still opens the folder", len(win.columns) == 2 and win.columns[1].gfile.get_path() == P("dir1"))
    select(col, "dir1", "a.txt", "b.txt"); return later(s3)
def s3():
    win = S["win"]; col = win.columns[0]
    check("multi selection closes the folder column", len(win.columns) == 1)
    pv = win.preview
    check("…and shows a summary preview", pv is not None and pv.gfile is None and pv.name.get_text() == "3 items")
    labels = [c.get_text() for c in pv.details.get_children() if isinstance(c, Gtk.Label)]
    check("summary lists kinds, total size and names", pv.summary.get_text().replace("\u00a0", " ") == "1 folder, 2 files - 30 bytes" and any("a.txt" in l for l in labels))
    check("selected() is empty for a multi selection", col.selected() == (None, None))
    check("current dir falls back to the column's folder", win.current_dir().get_path() == tmp)
    check("status line shows the selection", "3 selected" in win.status.get_text() and "30 bytes" in win.status.get_text())
    check("history not recorded for multi selection", win._history[win._hist_pos][1].get_path() in (tmp, P("dir1")))
    # right-click on a selected row keeps the selection; on another row selects just it
    win.columns[0].handler_block_by_func(win.tab._on_context_menu)
    seen = []
    col.connect("context-menu", lambda c, g, i, e: seen.append(g.get_path() if g else None))
    ev = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS); ev.button.button = 3
    rect = col.view.get_cell_area(path_of(col, "a.txt"), col.name_col)
    x, y = col.view.convert_bin_window_to_widget_coords(rect.x + 5, rect.y + rect.height // 2)
    ev.button.x, ev.button.y = x, y
    col._on_button_press(col.view, ev.button)
    check("right-click on a selected row keeps the selection", len(col.selected_all()[0]) == 3 and seen[-1] == P("a.txt"))
    rect = col.view.get_cell_area(path_of(col, "c.txt"), col.name_col)
    ev.button.x, ev.button.y = col.view.convert_bin_window_to_widget_coords(rect.x + 5, rect.y + rect.height // 2)
    col._on_button_press(col.view, ev.button)
    return later(s4, 400)
def s4():
    win = S["win"]; col = win.columns[0]
    check("right-click elsewhere selects only that row", col.selected_all()[0] and [g.get_path() for g in col.selected_all()[0]] == [P("c.txt")])
    check("…and previews it", win.preview is not None and win.preview.gfile is not None)
    # copy two files, paste into dest: both land there and are selected in dest's column
    select(col, "a.txt", "b.txt"); col.view.grab_focus()
    win.copy_selection()
    return later(s5, 400)
def s5():
    win = S["win"]; col = win.columns[0]
    uris = cb.wait_for_uris() or []
    check("Ctrl+C copies every selected file", sorted(uris) == sorted([F("a.txt").get_uri(), F("b.txt").get_uri()]))
    check("rename refused for a multi selection", (col.start_rename(), col._renaming)[1] is False)
    select(col, "dest"); return later(s6)
def s6():
    win = S["win"]
    win.paste(); return later(s7, 1000)
def s7():
    win = S["win"]
    check("paste puts both files there", sorted(os.listdir(P("dest"))) == ["a.txt", "b.txt"])
    dcol = win._column_for(F("dest"))
    check("…and selects both in that column, with a summary", dcol is not None and len(dcol.selected_all()[0]) == 2 and win.preview is not None and win.preview.gfile is None)
    # reload keeps a multi selection
    open(P("dest", "z.txt"), "w").write("new")
    return later(s8, 1200)
def s8():
    win = S["win"]; dcol = win._column_for(F("dest"))
    check("monitor reload keeps the whole selection", dcol is not None and names(dcol) == ["a.txt", "b.txt", "z.txt"] and sorted(g.get_basename() for g in dcol.selected_all()[0]) == ["a.txt", "b.txt"])
    # neighbour after a batch removal
    check("neighbour of a batch: first row after the last of them", dcol._neighbour_of([F("dest", "a.txt"), F("dest", "b.txt")]).get_basename() == "z.txt")
    check("neighbour when nothing follows: row before", dcol._neighbour_of([F("dest", "b.txt"), F("dest", "z.txt")]).get_basename() == "a.txt")
    # batch delete with the confirmation stubbed
    listing.confirm = colmod.confirm = lambda *a, **k: True
    select(dcol, "a.txt", "b.txt"); dcol.delete_item()
    return later(s9, 1000)
def s9():
    win = S["win"]; dcol = win._column_for(F("dest"))
    check("batch delete removes both and selects the neighbour", os.listdir(P("dest")) == ["z.txt"] and dcol is not None and dcol.selected()[0] is not None and dcol.selected()[0].get_basename() == "z.txt")
    # batch trash through Gio.File.trash (stubbed: nothing lands in the real trash)
    trashed = []
    orig = Gio.File.trash
    Gio.File.trash = lambda self, c=None: trashed.append(self.get_basename())
    col = win.columns[0]; col.view.grab_focus(); select(col, "c.txt", "d.txt"); col.trash_item()
    Gio.File.trash = orig
    check("batch trash trashes every selected item", sorted(trashed) == ["c.txt", "d.txt"])
    # drag data: all selected uris
    col = win.columns[0]; select(col, "a.txt", "b.txt")
    class D:
        def __init__(self): self.uris = None
        def set_uris(self, u): self.uris = u
    d = D(); col._on_drag_data_get(col.view, None, d, 0, 0); col._on_drag_begin(col.view, None)
    check("drag carries every selected file", sorted(d.uris) == sorted([F("a.txt").get_uri(), F("b.txt").get_uri()]) and len(colmod._dragging) == 2)
    colmod._dragging.clear()
    # Ctrl+O on a mixed selection activates the files only
    opened = []
    col.connect("item-activated", lambda c, g, i: opened.append(g.get_basename()))
    win.columns[0].handler_block_by_func(win.tab._on_item_activated)
    select(col, "dir1", "a.txt")
    ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS); ev.key.keyval = Gdk.KEY_o; ev.key.state = Gdk.ModifierType.CONTROL_MASK
    col._on_key_press(col.view, ev.key)
    check("Ctrl+O on a mixed selection opens the files only", opened == ["a.txt"])
    cb.set_text("", -1); app.quit(); return False
GLib.timeout_add(900, s1)
app.run(["finch", tmp]); shutil.rmtree(tmp, ignore_errors=True)
print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
