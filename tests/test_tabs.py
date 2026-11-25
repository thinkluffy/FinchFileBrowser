"""Tabs: open/close/switch, per-tab history, title bar follows, settings apply to all, state, menu, middle-click."""
import os, sys, tempfile, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gio, Gtk, Gdk
from app.application import FinchApp
from app import C_NAME

tmp = tempfile.mkdtemp(prefix="finch-tabs-")
os.makedirs(os.path.join(tmp, "A", "x")); os.makedirs(os.path.join(tmp, "B", "y"))
open(os.path.join(tmp, "A", "f.txt"), "w").write("f")
open(os.path.join(tmp, "B", ".hidden"), "w").write("h")
F = lambda *p: Gio.File.new_for_path(os.path.join(tmp, *p))
fails = []
def check(what, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + what, extra if not ok else "", flush=True)
    if not ok: fails.append(what)
def names(col): return [r[C_NAME] for r in col.store]
def select(col, name):
    row = next(r for r in col.store if r[C_NAME] == name); col.view.set_cursor(row.path, None, False)

app = FinchApp()
def win(): return app.get_active_window()
steps = []
def step(f): steps.append(f); return f

@step
def s1():
    w = win()
    check("starts with one tab, tab bar hidden", len(w.tabs) == 1 and not w.notebook.get_show_tabs())
    check("window.columns is the tab's columns", w.columns is w.tab.columns and w.columns[0].gfile.equal(F()))
    w.open_tab(F("B"))
@step
def s2():
    w = win()
    check("second tab opened and current", len(w.tabs) == 2 and w.tab.root.equal(F("B")) and w.notebook.get_show_tabs())
    check("title bar follows the current tab", w.header.get_title() == "B" and w.path_entry.get_text() == os.path.join(tmp, "B"))
    check("tab label", w.tab.title.get_text() == "B")
    check("up action enabled from B", w.lookup_action("up").get_enabled())
    select(w.columns[0], "y")
@step
def s3():
    w = win()
    check("selecting a folder in tab 2 adds a column there", len(w.tab.columns) == 2 and w.header.get_title() == "y")
    check("tab 1 untouched", len(w.tabs[0].columns) == 1)
    w.notebook.set_current_page(0)
@step
def s4():
    w = win()
    check("switch back: title/path show tab 1", w.header.get_title() == os.path.basename(tmp) and w.path_entry.get_text() == tmp)
    check("back disabled in tab 1 (no history)", not w.lookup_action("back").get_enabled())
    w.notebook.set_current_page(1)
@step
def s5():
    w = win()
    check("tab 2 keeps its columns and location", len(w.columns) == 2 and w.header.get_title() == "y")
    check("back enabled in tab 2 (own history)", w.lookup_action("back").get_enabled())
    w.go_back()
@step
def s6():
    w = win()
    check("per-tab history: back in tab 2 returns to B", w.header.get_title() == "B" and w.tabs[0].can_go_back() is False)
    # hidden files toggle applies to every tab
    w.lookup_action("show-hidden").change_state(GLib.Variant.new_boolean(True))
@step
def s7():
    w = win()
    check("show-hidden applies to all tabs", ".hidden" in names(w.tabs[1].columns[0]) and names(w.tabs[0].columns[0]) == ["A", "B"])
    w.lookup_action("show-hidden").change_state(GLib.Variant.new_boolean(False))
    # context menu on a folder offers Open in New Tab; on a file it does not
    col = w.tabs[0].columns[0]; w.notebook.set_current_page(0)
    def labels(name):
        col = w.tabs[0].columns[0]          # the column there now: set_root replaces it
        select(col, name)
        w._on_context_menu(col, *(next((r[3], r[4]) for r in col.store if r[C_NAME] == name)), None)
        menu = Gtk.Menu.get_for_attach_widget(col.view)[-1]
        out = [i.get_label() for i in menu.get_children() if not isinstance(i, Gtk.SeparatorMenuItem)]
        menu.popdown(); menu.destroy(); return out
    check("folder menu has Open in New Tab", "Open in New Tab" in labels("A"))
    w.set_root(F("A"))
    GLib.timeout_add(500, lambda: (check("file menu has no Open in New Tab", "Open in New Tab" not in labels("f.txt")), next_step(), False)[2])
    return "wait"
@step
def s8():
    w = win()
    # middle-click signal on a folder row opens a background tab
    col = w.columns[0]; w.set_root(F())
    GLib.timeout_add(500, lambda: (w.columns[0].emit("open-new-tab", F("A")), next_step(), False)[2])
    return "wait"
@step
def s9():
    w = win()
    check("open-new-tab signal adds a background tab", len(w.tabs) == 3 and w.tabs[2].root.equal(F("A")) and w.notebook.get_current_page() == 0)
    # new-tab action duplicates the current dir; close-tab closes the current one only
    w.lookup_action("new-tab").activate()
    check("new-tab action opens and switches", len(w.tabs) == 4 and w.notebook.get_current_page() == 3 and w.tab.root.equal(F()))
    w.lookup_action("close-tab").activate()
    check("close-tab removes the current tab", len(w.tabs) == 3)
    w.lookup_action("next-tab").activate(); p1 = w.notebook.get_current_page()
    w.lookup_action("prev-tab").activate(); p0 = w.notebook.get_current_page()
    check("next/prev tab cycle", (p1, p0) == ((p0 + 1) % 3, p0))
    # state remembers neither the tabs nor where the user was
    w.notebook.set_current_page(1)
    w.state["tabs"] = ["file:///stale"]; w.state["last_root"] = "file:///stale"; w.state["current_tab"] = 9
    w.save_window_state()
    st = w.state
    check("state drops tabs / last_root / current_tab", not any(k in st for k in ("tabs", "last_root", "current_tab")), repr(st))
    # the sidebar's NEW_TAB flag opens a tab, NORMAL changes the current one
    w._on_sidebar_open(w.sidebar, F("A"), Gtk.PlacesOpenFlags.NEW_TAB)
    check("sidebar middle-click opens a tab", len(w.tabs) == 4)
    w._on_sidebar_open(w.sidebar, F("B"), Gtk.PlacesOpenFlags.NORMAL)
    check("sidebar click changes the current tab", len(w.tabs) == 4 and w.tab.root.equal(F("B")))
    # closing tabs down to one hides the bar; the last one would close the window
    while len(w.tabs) > 1: w.close_tab(w.tabs[-1])
    check("one tab left: bar hidden", len(w.tabs) == 1 and not w.notebook.get_show_tabs())
    print("%d failures" % len(fails), flush=True)
    app.quit()

idx = [0]
def next_step(*_a):
    if idx[0] < len(steps):
        f = steps[idx[0]]; idx[0] += 1
        def go():
            if f() != "wait": GLib.timeout_add(500, next_step)
            return False
        GLib.timeout_add(100, go)
    return False
GLib.timeout_add(1200, next_step)
app.run(["finch", tmp])
