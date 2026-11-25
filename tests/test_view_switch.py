# Switching between the columns and the icon view stays in the current
# folder: the grid shows the folder the path bar ends in (not the left-most
# column's), and going back to the columns brings the same columns back.
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gio, Gtk
from app.application import FinchApp
from app.column import Column
from app.grid import Grid
tmp = tempfile.mkdtemp(prefix="finch-viewswitch-")
os.makedirs(os.path.join(tmp, "R", "D", "Sub"))
os.makedirs(os.path.join(tmp, "O"))
for p in ("R/r.txt", "R/D/a.txt", "R/D/b.txt", "R/D/Sub/c.txt", "O/o.txt"):
    open(os.path.join(tmp, p), "w").write("x")
F = lambda *p: Gio.File.new_for_path(os.path.join(tmp, *p))
results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra if not ok else "", flush=True)
app = FinchApp(); S = {}
def later(fn, ms=800):
    def run():
        try: return fn()
        except Exception:
            import traceback; traceback.print_exc(); app.quit(); return False
    GLib.timeout_add(ms, run); return False
def tab(): return S["w"].tab
def mode(m): S["w"].lookup_action("view-mode").change_state(GLib.Variant("s", m))
def cols(): return [c.gfile.get_basename() for c in tab().columns]
def kinds(): return {type(c).__name__ for c in tab().columns}
def sel(i=-1): return sorted(g.get_basename() for g in tab().columns[i].selected_all()[0])
def loc(): return tab().location.get_basename()
def bar(): return [g.get_basename() for g, b in S["w"].pathbar._crumbs if b.get_active()]
def pick(i, *names):
    c = tab().columns[i]
    c._select_only(c.handle_of(F(*names)))
def state(): return "%s cols=%s sel=%s loc=%s bar=%s" % (sorted(kinds()), cols(), sel(), loc(), bar())
def grid_at(folder, selected=()):
    return kinds() == {"Grid"} and cols() == [folder] and sel() == sorted(selected) and loc() == folder and bar() == [folder]

# 1. a file selected two columns in: the case of the report
def s1():
    S["w"] = w = app.get_active_window(); mode("columns")
    w.set_root(F("R"), select=F("R", "D"))
    return later(s2)
def s2():
    pick(1, "R", "D", "a.txt")
    return later(s3)
def s3():
    check("columns: R | D with a file selected, the location is D", cols() == ["R", "D"] and sel() == ["a.txt"] and loc() == "D" and tab().preview is not None, state())
    mode("grid")
    return later(s4)
def s4():
    check("grid: shows D, the folder the file is in, with the file selected", grid_at("D", ["a.txt"]), state())
    mode("columns")
    return later(s5)
def s5():
    check("columns again: the same columns, file and preview", kinds() == {"Column"} and cols() == ["R", "D"] and sel(0) == ["D"] and sel() == ["a.txt"] and loc() == "D" and tab().preview is not None, state())
    # 2. a folder open with nothing selected in it
    S["w"].set_root(F("R"), select=F("R", "D", "Sub"))
    return later(s6)
def s6():
    check("columns: R | D | Sub", cols() == ["R", "D", "Sub"] and sel() == [] and loc() == "Sub", state())
    mode("grid")
    return later(s7)
def s7():
    check("grid: shows Sub, nothing selected", grid_at("Sub"), state())
    mode("columns")
    return later(s8)
def s8():
    check("columns again: R | D | Sub", cols() == ["R", "D", "Sub"] and sel(1) == ["Sub"] and sel() == [] and loc() == "Sub", state())
    # 3. several files selected: their folder
    c = tab().columns[1]
    c.view.get_selection().unselect_all()
    for name in ("a.txt", "b.txt"):
        c.view.get_selection().select_path(c.handle_of(F("R", "D", name)))
    return later(s9)
def s9():
    check("columns: two files selected in D", cols() == ["R", "D"] and sel() == ["a.txt", "b.txt"] and loc() == "D", state())
    mode("grid")
    return later(s10)
def s10():
    check("grid: shows D", kinds() == {"Grid"} and cols() == ["D"] and loc() == "D", state())
    # 4. moving on in the grid: below the old left-most column, the columns
    # open down to the new folder; outside it, it is the left-most column
    S["w"].set_root(F("R", "D", "Sub"))
    return later(s11)
def s11():
    pick(0, "R", "D", "Sub", "c.txt")
    mode("columns")
    return later(s12)
def s12():
    check("columns: from R down to the file picked in the grid", cols() == ["R", "D", "Sub"] and sel() == ["c.txt"] and loc() == "Sub", state())
    mode("grid")
    return later(s13)
def s13():
    S["w"].set_root(F("O"))
    return later(s14)
def s14():
    S["w"].set_root(F("R", "D"))
    return later(s15)
def s15():
    mode("columns")
    return later(s16)
def s16():
    check("columns: after leaving R in the grid, the grid's folder alone", cols() == ["D"] and loc() == "D", state())
    # 5. the left-most column alone
    mode("grid")
    return later(s17)
def s17():
    check("grid: a single column's folder", grid_at("D"), state())
    mode("columns")
    return later(s18)
def s18():
    check("columns: and back", cols() == ["D"] and loc() == "D", state())
    # 6. back in the grid to a place visited in the columns
    S["w"].set_root(F("R"), select=F("R", "D"))
    return later(s19)
def s19():
    pick(1, "R", "D", "b.txt")
    return later(s20)
def s20():
    mode("grid")
    return later(s21)
def s21():
    S["w"].set_root(F("O"))
    return later(s22)
def s22():
    check("grid: went on to O", grid_at("O"), state())
    S["w"].go_back()
    return later(s23)
def s23():
    check("grid: back shows D with the file, not R", grid_at("D", ["b.txt"]), state())
    mode("columns")
    return later(s24)
def s24():
    check("columns: the place as it was visited", cols() == ["R", "D"] and sel() == ["b.txt"] and loc() == "D", state())
    S["w"].set_root(F("R"), select=F("R", "D", "Sub"))
    return later(s25)
def s25():
    S["w"].set_root(F("O"))
    return later(s26)
def s26():
    mode("grid")
    return later(s27)
def s27():
    S["w"].go_back()
    return later(s28)
def s28():
    check("grid: back to a folder visited in the columns shows that folder", grid_at("Sub"), state())
    S["w"].go_forward()
    return later(s29)
def s29():
    check("grid: forward again", grid_at("O"), state())
    # 7. a place visited in the grid is not taken for one of the columns:
    # Up leaves the folder selected in its parent, and back returns to that
    S["w"].set_root(F("R", "D", "Sub"))
    return later(s30)
def s30():
    S["w"].go_up()
    return later(s31)
def s31():
    check("grid: up shows D with Sub selected", grid_at("D", ["Sub"]), state())
    S["w"].set_root(F("O"))
    return later(s32)
def s32():
    S["w"].go_back()
    return later(s33)
def s33():
    check("grid: back to it shows D with Sub selected again, not Sub", grid_at("D", ["Sub"]), state())
    mode("columns")
    return later(lambda: (app.quit(), False)[1], 300)
later(s1, 1500)
app.run(["finch", tmp])
shutil.rmtree(tmp)
print("%d/%d passed" % (sum(ok for _n, ok in results), len(results)))
