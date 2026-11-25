# The path bar keeps the folders below the current one after going up, but
# must drop them once they are deleted.
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gio, Gtk
from app.application import FinchApp
tmp = tempfile.mkdtemp(prefix="finch-crumb-", dir=os.path.expanduser("~"))
for p in ("P/D", "Q/E/F", "R/G", "R/H", "T/K/L"):
    os.makedirs(os.path.join(tmp, p))
open(os.path.join(tmp, "R", "file.txt"), "w").write("x")
F = lambda *p: Gio.File.new_for_path(os.path.join(tmp, *p))
results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra, flush=True)
def tail(bar, n):
    return [g.get_basename() for g, _b in bar._crumbs][-n:]
def active(bar):
    return [g.get_basename() for g, b in bar._crumbs if b.get_active()]
def mode(w, m): w.lookup_action("view-mode").change_state(GLib.Variant("s", m))
def select(col, name):
    for i, row in enumerate(col.store):
        if row[1] == name:
            col._select_only(i if hasattr(col, "items") else Gtk.TreePath(i)); return
    raise KeyError(name)
app = FinchApp(); S = {}
def later(fn, ms=700): GLib.timeout_add(ms, fn); return False

# 1. columns: P holds only D; select D, delete it
def s1():
    S["w"] = w = app.get_active_window(); mode(w, "columns")
    w.set_root(F("P"), select=F("P", "D"))
    return later(s2)
def s2():
    w = S["w"]; bar = w.pathbar
    check("columns: selecting D puts it in the bar", tail(bar, 2) == ["P", "D"] and active(bar) == ["D"], str(tail(bar, 3)))
    os.rmdir(os.path.join(tmp, "P", "D"))
    return later(s3, 1500)
def s3():
    w = S["w"]; bar = w.pathbar
    check("columns: location falls back to P", w.tab.location.equal(F("P")))
    check("columns: deleted only child leaves the bar", tail(bar, 1) == ["P"] and active(bar) == ["P"], str(tail(bar, 3)))
    # 2. columns: R holds G, H and a file; G selected then deleted, others stay
    w.set_root(F("R"), select=F("R", "G"))
    return later(s4)
def s4():
    w = S["w"]; bar = w.pathbar
    check("columns: G in the bar", tail(bar, 2) == ["R", "G"])
    select(w.columns[0], "file.txt")          # location back on R, G kept as deeper crumb
    return later(s5)
def s5():
    w = S["w"]; bar = w.pathbar
    check("columns: G stays while it exists", tail(bar, 2) == ["R", "G"] and active(bar) == ["R"], str(tail(bar, 3)))
    os.rmdir(os.path.join(tmp, "R", "G"))
    return later(s6, 1500)
def s6():
    w = S["w"]; bar = w.pathbar
    check("columns: G deleted while a sibling is selected leaves the bar", tail(bar, 1) == ["R"], str(tail(bar, 3)))
    # 3. grid: into Q/E/F, up twice to Q; delete F (deep) through E gone
    mode(w, "grid")
    w.set_root(F("Q", "E", "F"))
    return later(s7)
def s7():
    w = S["w"]; w.go_up(); return later(s8)
def s8():
    w = S["w"]; w.go_up(); return later(s9)
def s9():
    w = S["w"]; bar = w.pathbar
    check("grid: up twice keeps E and F", tail(bar, 3) == ["Q", "E", "F"] and active(bar) == ["Q"], str(tail(bar, 4)))
    shutil.rmtree(os.path.join(tmp, "Q", "E"))
    return later(s10, 1500)
def s10():
    w = S["w"]; bar = w.pathbar
    check("grid: deleting the only child drops it and what was below", tail(bar, 1) == ["Q"] and active(bar) == ["Q"], str(tail(bar, 4)))
    # 4. grid: T/K/L, up twice to T; delete only L: K stays
    w.set_root(F("T", "K", "L"))
    return later(s11)
def s11():
    w = S["w"]; w.go_up(); return later(s12)
def s12():
    w = S["w"]; bar = w.pathbar
    check("grid: at K the bar still ends in L", tail(bar, 3) == ["T", "K", "L"] and active(bar) == ["K"], str(tail(bar, 4)))
    os.rmdir(os.path.join(tmp, "T", "K", "L"))
    return later(s13, 1500)
def s13():
    w = S["w"]; bar = w.pathbar
    check("grid: L gone, K kept", tail(bar, 2) == ["T", "K"] and active(bar) == ["K"], str(tail(bar, 4)))
    mode(w, "columns")
    return later(done, 300)
def done():
    app.quit(); return False
GLib.timeout_add(1200, s1)
app.run(["finch", tmp])
shutil.rmtree(tmp)
print("%d/%d passed" % (sum(ok for _n, ok in results), len(results)))
