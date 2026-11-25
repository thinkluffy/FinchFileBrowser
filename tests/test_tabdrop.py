# A dragged tab is not a drop for listings (columns, grid, folder rows) and
# the notebook takes it only on its tab strip; everywhere else the drag has
# no target, which is what makes GTK ask for a new window.
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gio, Gtk
from app.application import FinchApp
from app import listing
tmp = tempfile.mkdtemp(prefix="finch-tabdrop-", dir=os.path.expanduser("~"))
for d in ("A", "B"): os.makedirs(os.path.join(tmp, d, "sub"))
open(os.path.join(tmp, "A", "f.txt"), "w").write("x")
F = lambda *p: Gio.File.new_for_path(os.path.join(tmp, *p))
results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra, flush=True)

class FakeCtx:
    def __init__(self, *names): self.names = names
    def list_targets(self): return [Gdk.Atom.intern(n, False) for n in self.names]
    def get_actions(self): return Gdk.DragAction.MOVE | Gdk.DragAction.COPY
    def get_device(self): return None
TAB, FILES = FakeCtx("GTK_NOTEBOOK_TAB"), FakeCtx("text/uri-list")
statuses = []
Gdk.drag_status = lambda ctx, action, time: statuses.append(int(action))

app = FinchApp(); S = {}
def later(fn, ms=600): GLib.timeout_add(ms, fn); return False
def s1():
    S["w"] = w = app.get_active_window()
    w.set_root(F("A")); w.open_tab(F("B"))
    w.notebook.set_current_page(0)
    return later(s2)
def s2():
    w = S["w"]; tab = w.tabs[0]; col = tab.columns[0]
    check("drag_has_files tells files from tabs", listing.drag_has_files(FILES) and not listing.drag_has_files(TAB))
    # a folder row of a column
    row_y = col.view.get_background_area(Gtk.TreePath.new_from_indices([0]), None).y + 4
    statuses.clear()
    r = col._on_drag_motion(col.view, TAB, 10, row_y, 0)
    check("column: tab drag over a folder row is not a drop site", r is False and statuses == [])
    check("column: nothing highlighted", col.view.get_drag_dest_row()[0] is None)
    r = col._on_drag_motion(col.view, TAB, 10, 400, 0)
    check("column: tab drag over the blank area is not a drop site", r is False and statuses == [])
    check("column: tab drop refused", col._on_drag_drop(col.view, TAB, 10, 400, 0) is False)
    statuses.clear()
    r = col._on_drag_motion(col.view, FILES, 10, row_y, 0)
    check("column: file drag over a folder row still accepted", r is True and statuses == [int(Gdk.DragAction.MOVE)])
    col._clear_drop()
    # the notebook: strip above the page, page below
    nb = w.notebook
    page = nb.get_nth_page(nb.get_current_page())
    top = page.translate_coordinates(nb, 0, 0)[1]
    check("strip is shown above the page", nb.get_show_tabs() and top > 10, f"top={top}")
    check("over_strip: strip yes, page no", nb.over_strip(50, top // 2) and not nb.over_strip(50, top + 50))
    statuses.clear()
    r = nb.do_drag_motion(TAB, 50, top + 50, 0)
    check("notebook: tab over the page -> status 0, handled", r is True and statuses == [0])
    # single-tab window: no strip, so nowhere to drop a tab
    w.close_tab(w.tabs[1])
    return later(s3)
def s3():
    w = S["w"]; nb = w.notebook
    check("single tab: no strip, tab drops refused everywhere", not nb.get_show_tabs() and not nb.over_strip(50, 2))
    # grid mode: the grid listing refuses tab drags too
    w.lookup_action("view-mode").change_state(GLib.Variant("s", "grid"))
    return later(s4)
def s4():
    w = S["w"]; tab = w.tabs[0]; grid = tab.columns[0]
    from app.grid import Grid
    check("in grid mode", isinstance(grid, Grid))
    statuses.clear()
    x, y = grid._to_widget(*grid._cell_pos(0)); x += 20; y += 20
    r = grid._on_drag_motion(grid.view, TAB, x, y, 0)
    check("grid: tab drag over a folder cell is not a drop site", r is False and statuses == [] and grid._drop_hit is None)
    r = grid._on_drag_motion(grid.view, FILES, x, y, 0)
    check("grid: file drag over a folder cell accepted", r is True and statuses == [int(Gdk.DragAction.MOVE)] and grid._drop_hit == 0)
    grid._clear_drop()
    w.lookup_action("view-mode").change_state(GLib.Variant("s", "columns"))
    return later(done)
def done():
    w = S["w"]; w.save_window_state()
    shutil.rmtree(tmp)
    n = sum(1 for _, ok in results if ok)
    print(f"{n}/{len(results)} passed", flush=True)
    app.quit(); return False
# FinchApp handles the command line itself: "activate" is never emitted.
later(s1, 1500)
app.run(["finch", tmp])
