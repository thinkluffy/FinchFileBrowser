# The Nemo-style path bar: folder buttons that follow the location, a
# text entry behind a toggle (kept in the state) or shown once by Ctrl+L.
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gio, Gtk
from app.application import FinchApp
tmp = tempfile.mkdtemp(prefix="finch-pathbar-", dir=os.path.expanduser("~"))
os.makedirs(os.path.join(tmp, "A", "B", "C"))
F = lambda *p: Gio.File.new_for_path(os.path.join(tmp, *p))
results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra, flush=True)
def labels(bar):
    out = []
    for g, btn in bar._crumbs:
        out.append([c for c in btn.get_child().get_children() if isinstance(c, Gtk.Label)][0].get_text())
    return out
def active(bar):
    return [g.get_basename() for g, b in bar._crumbs if b.get_active()]
def key(widget, name):
    ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS); ev.keyval = name; ev.window = widget.get_window()
    return widget.emit("key-press-event", ev)
app = FinchApp(); S = {}
def later(fn, ms=500): GLib.timeout_add(ms, fn); return False
def s1():
    S["w"] = w = app.get_active_window(); bar = w.pathbar
    w.lookup_action("view-mode").change_state(GLib.Variant("s", "columns"))
    home = os.path.basename(os.path.expanduser("~"))
    check("home shows one crumb: the home folder", labels(bar) == [home] and bar.stack.get_visible_child_name() == "crumbs" and not bar.entry_mode)
    w.set_root(F("A", "B", "C"))
    return later(s2)
def s2():
    w = S["w"]; bar = w.pathbar
    home = os.path.basename(os.path.expanduser("~"))
    check("crumbs from home down to C", labels(bar) == [home, os.path.basename(tmp), "A", "B", "C"], str(labels(bar)))
    check("C is the pressed one", active(bar) == ["C"])
    check("entry text follows", bar.entry.get_text() == os.path.join(tmp, "A", "B", "C"))
    # Going up in column view puts the parent's column to the left with the
    # folder we came from selected in it: its own column stays, and so does
    # the location.
    w.go_up()
    return later(s3)
def s3():
    w = S["w"]; bar = w.pathbar
    cols = [c.gfile.get_basename() for c in w.columns]
    check("after Up: columns B | C, C still the location", labels(bar)[-2:] == ["B", "C"] and active(bar) == ["C"] and cols == ["B", "C"], "%s %s cols=%s" % (labels(bar), active(bar), cols))
    # clicking a crumb navigates there
    [b for g, b in bar._crumbs if g.get_basename() == "A"][0].clicked()
    return later(s4)
def s4():
    w = S["w"]; bar = w.pathbar
    check("clicking the A crumb goes to A", w.tab.root.equal(F("A")) and active(bar) == ["A"] and labels(bar)[-1] == "C")
    # clicking the pressed crumb again does nothing (stays pressed)
    btnA = [b for g, b in bar._crumbs if g.get_basename() == "A"][0]
    btnA.clicked()
    check("the pressed crumb cannot be unpressed", btnA.get_active() and w.tab.root.equal(F("A")))
    # middle click: a new tab in the background
    n = len(w.tabs)
    ev = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE); ev.button = 2
    btnC = [b for g, b in bar._crumbs if g.get_basename() == "C"][0]
    btnC.emit("button-release-event", ev)
    return later(s5)
def s5():
    w = S["w"]; bar = w.pathbar
    check("middle click opens the folder in a new tab", len(w.tabs) == 2 and w.tabs[1].root.equal(F("A", "B", "C")) and w.tab is w.tabs[0])
    w.close_tab(w.tabs[1])
    # a path outside home starts at the file system root
    w.set_root(Gio.File.new_for_path("/usr/share"))
    return later(s6)
def s6():
    w = S["w"]; bar = w.pathbar
    check("outside home: File System / usr / share", labels(bar) == ["File System", "usr", "share"] and active(bar) == ["share"])
    # Ctrl+L: the entry, temporarily
    w.lookup_action("focus-path").activate()
    check("Ctrl+L shows the entry with focus", bar.stack.get_visible_child_name() == "entry" and bar.entry.has_focus() and not bar.entry_mode)
    bar.entry.set_text("/nowhere-typed")
    key(bar.entry, Gdk.KEY_Escape)
    return later(s7)
def s7():
    w = S["w"]; bar = w.pathbar
    check("Escape returns to the crumbs and restores the text", bar.stack.get_visible_child_name() == "crumbs" and bar.entry.get_text() == "/usr/share")
    check("focus went back to the files", w.columns[0].view.has_focus())
    # Ctrl+L, type a path, Enter: navigates and returns to the crumbs
    w.lookup_action("focus-path").activate()
    bar.entry.set_text(os.path.join(tmp, "A"))
    bar.entry.emit("activate")
    return later(s8)
def s8():
    w = S["w"]; bar = w.pathbar
    check("Enter in the entry navigates", w.tab.root.equal(F("A")) and active(bar) == ["A"])
    check("...and leaves the temporary entry", bar.stack.get_visible_child_name() == "crumbs")
    # the toggle: the entry stays
    bar.toggle.set_active(True)
    check("toggle shows the entry for good", bar.stack.get_visible_child_name() == "entry" and bar.entry_mode)
    w.set_root(F("A", "B"))
    return later(s9)
def s9():
    w = S["w"]; bar = w.pathbar
    check("entry follows the location while toggled", bar.entry.get_text() == os.path.join(tmp, "A", "B") and bar.stack.get_visible_child_name() == "entry")
    key(bar.entry, Gdk.KEY_Escape)
    check("Escape keeps the toggled entry", bar.stack.get_visible_child_name() == "entry")
    w.save_window_state()
    check("state remembers the toggle", w.state.get("location_entry") is True)
    bar.toggle.set_active(False)
    check("toggle off: crumbs again", bar.stack.get_visible_child_name() == "crumbs" and active(bar) == ["B"])
    w.save_window_state()
    check("state cleared", w.state.get("location_entry") is False)
    # the trash
    w.set_root(Gio.File.new_for_uri("trash:///"))
    return later(s10)
def s10():
    w = S["w"]; bar = w.pathbar
    check("trash: one crumb", labels(bar) == ["Trash"] and active(bar) == [""] or active(bar) == ["/"], str((labels(bar), active(bar))))
    print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
    app.quit(); return False
GLib.timeout_add(1200, s1)
app.run(["finch"]); shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if all(ok for _, ok in results) else 1)
