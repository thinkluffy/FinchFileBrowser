# Clicking a level in the path bar keeps the columns: it goes back (or down)
# to that folder's node instead of making the folder the left-most column.
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gio, Gtk
from app.application import FinchApp
tmp = tempfile.mkdtemp(prefix="finch-crumbback-", dir=os.path.expanduser("~"))
os.makedirs(os.path.join(tmp, "R", "A", "B", "C"))
os.makedirs(os.path.join(tmp, "R", "A", "B", "other"))
os.makedirs(os.path.join(tmp, "R", "Z"))
os.makedirs(os.path.join(tmp, "R", ".hid", "deep"))
for p in ("R/A/B/C/file.txt", "R/A/x.txt", "R/r.txt"):
    open(os.path.join(tmp, p), "w").write("x")
F = lambda *p: Gio.File.new_for_path(os.path.join(tmp, *p))
results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra, flush=True)
app = FinchApp(); S = {}
def later(fn, ms=800):
    def run():
        try: return fn()
        except Exception:
            import traceback; traceback.print_exc(); app.quit(); return False
    GLib.timeout_add(ms, run); return False
def mode(m): S["w"].lookup_action("view-mode").change_state(GLib.Variant("s", m))
def cols(): return [c.gfile.get_basename() for c in S["w"].tab.columns]
def sel(i): return [g.get_basename() for g in S["w"].tab.columns[i].selected_all()[0]]
def loc(): return S["w"].tab.location.get_basename()
def cur(): return S["w"].current_dir().get_basename()
def focus(): return next((i for i, c in enumerate(S["w"].tab.columns) if c.view.is_focus()), None)
def bar(n): return [g.get_basename() for g, _b in S["w"].pathbar._crumbs][-n:]
def active(): return [g.get_basename() for g, b in S["w"].pathbar._crumbs if b.get_active()]
def click(name):
    btn = next(b for g, b in S["w"].pathbar._crumbs if g.get_basename() == name)
    btn.clicked()
def state(): return "cols=%s loc=%s cur=%s focus=%s bar=%s active=%s" % (cols(), loc(), cur(), focus(), bar(5), active())

def s1():
    S["w"] = w = app.get_active_window(); mode("columns")
    w.set_root(F("R"), select=F("R", "A", "B", "C"))
    return later(s2)
def s2():
    check("start: four columns down to C", cols() == ["R", "A", "B", "C"] and loc() == "C", state())
    click("A")
    return later(s3)
def s3():
    check("click A: columns go back to R | A", cols() == ["R", "A"], state())
    check("click A: A stays selected in R, nothing selected in A", sel(0) == ["A"] and sel(1) == [], "%s %s" % (sel(0), sel(1) if len(cols()) > 1 else "-"))
    check("click A: location, title and current folder are A", loc() == "A" and cur() == "A" and S["w"].get_title() == "A", state())
    check("click A: the bar keeps B and C, A pressed", bar(4) == ["R", "A", "B", "C"] and active() == ["A"], state())
    check("click A: focus on the column holding the A node", focus() == 0, state())
    check("click A: no preview", S["w"].tab.preview is None)
    click("C")
    return later(s4)
def s4():
    check("click C (deeper, kept in the bar): columns re-open down to C", cols() == ["R", "A", "B", "C"] and loc() == "C", state())
    check("click C: selections along the way", sel(0) == ["A"] and sel(1) == ["B"] and sel(2) == ["C"] and sel(3) == [])
    check("click C: focus on B's column, current folder C", focus() == 2 and cur() == "C", state())
    click("R")
    return later(s5)
def s5():
    check("click R (the left-most column): only R, nothing selected", cols() == ["R"] and sel(0) == [] and loc() == "R" and cur() == "R", state())
    check("click R: bar keeps the deeper levels", bar(4) == ["R", "A", "B", "C"] and active() == ["R"], state())
    click("B")
    return later(s6)
def s6():
    check("click B (two levels down): R | A | B", cols() == ["R", "A", "B"] and loc() == "B" and sel(1) == ["B"] and sel(2) == [], state())
    check("click B: focus on A's column, current folder B", focus() == 1 and cur() == "B", state())
    S["w"].go_back()
    return later(s7)
def s7():
    check("back: R again", loc() == "R" and cols() == ["R"], state())
    S["w"].go_back()
    return later(s8)
def s8():
    check("back again: C", loc() == "C" and cols() == ["R", "A", "B", "C"], state())
    S["w"].go_back()
    return later(s9)
def s9():
    check("back again: A", loc() == "A" and cols() == ["R", "A"], state())
    S["w"].go_forward()
    return later(s10)
def s10():
    check("forward: C", loc() == "C" and cols() == ["R", "A", "B", "C"], state())
    # a file selected (preview shown): going back to R closes it
    S["w"].set_root(F("R"), select=F("R", "A", "x.txt"))
    return later(s11)
def s11():
    check("file selected: R | A and a preview", cols() == ["R", "A"] and S["w"].tab.preview is not None and loc() == "A", state())
    click("R")
    return later(s12)
def s12():
    check("click R with a file selected below: only R, no preview", cols() == ["R"] and S["w"].tab.preview is None and loc() == "R", state())
    # a level above the left-most column: nothing to go back to, it becomes the root
    click(os.path.basename(tmp))
    return later(s13)
def s13():
    check("click above the left-most column: it becomes the left-most column", cols() == [os.path.basename(tmp)] and loc() == os.path.basename(tmp), state())
    # the folder to go down to has no node in its parent's listing (hidden)
    S["w"].set_root(F("R", ".hid", "deep"))
    return later(s14)
def s14():
    click("R")
    return later(s15)
def s15():
    check("above the root again: R alone, bar keeps .hid and deep", cols() == ["R"] and bar(3) == ["R", ".hid", "deep"], state())
    click(".hid")
    return later(s15b)
def s15b():
    check("no node for a hidden folder: it becomes the left-most column", cols() == [".hid"] and loc() == ".hid", state())
    mode("grid")
    return later(s16)
def s16():
    S["w"].set_root(F("R", "A", "B"))
    return later(s17)
def s17():
    click("A")
    return later(s18)
def s18():
    check("grid: clicking A shows A alone", cols() == ["A"] and loc() == "A", state())
    mode("columns")
    return later(lambda: (app.quit(), False)[1], 300)
later(s1, 1500)
app.run(["finch", tmp])
shutil.rmtree(tmp)
print("%d/%d passed" % (sum(ok for _n, ok in results), len(results)))
