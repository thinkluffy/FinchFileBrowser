import os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk
from app.application import FinchApp
from app import C_NAME

tmp = tempfile.mkdtemp(prefix="finch-sort-")
now = time.time()
def mk(name, size=None, age=0):
    p = os.path.join(tmp, name)
    if size is None: os.mkdir(p)
    else: open(p, "wb").write(b"x" * size)
    os.utime(p, (now - age, now - age))
mk("adir", age=3000); mk("zdir", age=0)
mk("b.txt", 10, age=2000); mk("a.txt", 300, age=100); mk("c.md", 100, age=1000)

app = FinchApp()
fails = []
def check(what, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + what, "" if ok else "got=%r want=%r" % (got, want), flush=True)
    if not ok: fails.append(what)

def names(col): return [row[C_NAME] for row in col.store]
def win(): return app.get_active_window()
def col(): return win().columns[0]
def set_sort(mode): win().lookup_action("sort-by").change_state(GLib.Variant.new_string(mode))
def set_rev(flag): win().lookup_action("sort-reversed").change_state(GLib.Variant.new_boolean(flag))

steps = []
def step(f): steps.append(f); return f

@step
def s0():
    set_sort("name"); set_rev(False)      # state.json may hold a previous run's choice
@step
def s1():
    check("default by name", names(col()), ["adir", "zdir", "a.txt", "b.txt", "c.md"])
    set_rev(True)
@step
def s2():
    check("name reversed (folders stay first)", names(col()), ["zdir", "adir", "c.md", "b.txt", "a.txt"])
    set_rev(False); set_sort("size")
@step
def s3():
    check("size largest first", names(col()), ["adir", "zdir", "a.txt", "c.md", "b.txt"])
    set_sort("modified")
@step
def s4():
    check("modified newest first", names(col()), ["zdir", "adir", "a.txt", "c.md", "b.txt"])
    set_sort("kind")
@step
def s5():
    check("kind then name", names(col()), ["adir", "zdir", "c.md", "a.txt", "b.txt"])
    set_rev(True)
@step
def s6():
    check("kind reversed", names(col()), ["zdir", "adir", "b.txt", "a.txt", "c.md"])
    # context menu: last item is the Sort By submenu, bound to the actions
    w = win(); c = col()
    w._on_context_menu(c, None, None, None)
    menus = [m for m in Gtk.Menu.get_for_attach_widget(c.view)]
    menu = menus[-1]
    last = menu.get_children()[-1]
    check("context menu ends with Sort By", last.get_label(), "Sort By")
    sub = last.get_submenu()
    items = [i for i in sub.get_children() if not isinstance(i, Gtk.SeparatorMenuItem)]
    check("submenu labels", [i.get_label() for i in items],
          ["Name", "Date Modified", "Size", "Kind", "Reversed Order"])
    check("submenu sensitive", all(i.get_sensitive() for i in items), True)
    check("Kind radio active", [i.get_active() for i in items[:4]], [False, False, False, True])
    check("Reversed check active", items[4].get_active(), True)
    menu.popdown()
    items[0].activate()   # click "Name" in the submenu
    items[4].activate()   # untick "Reversed Order"
@step
def s7():
    check("submenu click → by name", names(col()), ["adir", "zdir", "a.txt", "b.txt", "c.md"])
    check("action state follows", win().lookup_action("sort-by").get_state().get_string(), "name")
    set_sort("size")
    win().save_window_state()
    check("state saved", (win().state["sort_by"], win().state["sort_reversed"]), ("size", False))
    # a new column inherits the sort
    win().add_column(col().gfile.get_child("adir"))
@step
def s8():
    check("new column uses window sort", (win().columns[-1].sort_by, win().columns[-1].sort_reversed), ("size", False))
    set_sort("name"); win().save_window_state()      # leave state.json as other tests expect
    print("%d failures" % len(fails), flush=True)
    app.quit()

def run(i=0):
    if i < len(steps):
        steps[i](); GLib.timeout_add(400, run, i + 1)
    return False
GLib.timeout_add(1500, run)
app.run(["finch", tmp])
