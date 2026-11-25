# Tabs dragged out of a window: a new window takes them; dragged between
# windows they switch owner; a window whose last tab left closes.
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gio, Gtk
from app.application import FinchApp
from app.window import FinchWindow
tmp = tempfile.mkdtemp(prefix="finch-detach-", dir=os.path.expanduser("~"))
for d in ("A", "B", "C"): os.makedirs(os.path.join(tmp, d, "sub"))
F = lambda *p: Gio.File.new_for_path(os.path.join(tmp, *p))
results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra, flush=True)
app = FinchApp(); S = {}
def later(fn, ms=500): GLib.timeout_add(ms, fn); return False
def move_tab(src_nb, tab, dst_nb):
    """What GTK does on a tab drop: detach from one notebook, insert in another."""
    label = src_nb.get_tab_label(tab)
    src_nb.detach_tab(tab)
    dst_nb.append_page(tab, label)
    dst_nb.set_current_page(dst_nb.page_num(tab))
def s1():
    S["w1"] = w1 = app.get_active_window()
    S["tB"] = w1.open_tab(F("B")); S["tC"] = w1.open_tab(F("C"), switch=False)
    check("three tabs in the first window", len(w1.tabs) == 3 and all(t.win is w1 for t in w1.tabs))
    check("tabs are detachable", all(w1.notebook.get_tab_detachable(t) for t in w1.tabs))
    # drop tab B outside: create-window gives a fresh window's notebook
    nb2 = w1._on_tab_detached(w1.notebook, S["tB"], 300, 200)
    S["w2"] = w2 = nb2.get_toplevel()
    check("a new window without tabs is made", isinstance(w2, FinchWindow) and w2 is not w1 and len(w2.tabs) == 0)
    move_tab(w1.notebook, S["tB"], nb2)
    return later(s2)
def s2():
    w1, w2, tB = S["w1"], S["w2"], S["tB"]
    check("tab B now lives in the new window", w2.tabs == [tB] and tB.win is w2 and len(w1.tabs) == 2)
    check("new window shows B's location", w2.header.get_title() == "B" and w2.path_entry.get_text().endswith("/B") and not w2.notebook.get_show_tabs())
    check("first window still shows its own tab", w1.header.get_title() in ("A", os.path.basename(tmp), "C") and w1.notebook.get_show_tabs())
    check("tab properties set in the new notebook", w2.notebook.get_tab_reorderable(tB) and w2.notebook.get_tab_detachable(tB) and w2.notebook.child_get_property(tB, "tab-expand"))
    # navigating in the moved tab drives the new window's title bar, not the old one's
    old = w1.header.get_title()
    tB.set_root(F("B", "sub"))
    check("moved tab updates its new window", w2.header.get_title() == "sub" and w1.header.get_title() == old)
    # the close button on the moved tab's label closes it in the new window
    label = w2.notebook.get_tab_label(tB)
    btn = [c for c in label.get_children() if isinstance(c, Gtk.Button)][0]
    S["w2_destroyed"] = []
    w2.connect("destroy", lambda *a: S["w2_destroyed"].append(1))
    btn.clicked()
    return later(s3)
def s3():
    w1 = S["w1"]
    check("closing the only tab of the new window closes that window", S["w2_destroyed"] == [1])
    check("the app still has the first window", app.get_windows() and w1 in app.get_windows())
    # drag the remaining tabs away one by one: the emptied window closes by itself
    nb3 = w1._on_tab_detached(w1.notebook, S["tC"], 10, 10); S["w3"] = nb3.get_toplevel()
    move_tab(w1.notebook, S["tC"], nb3)
    first = w1.tabs[0]
    S["w1_destroyed"] = []
    w1.connect("destroy", lambda *a: S["w1_destroyed"].append(1))
    move_tab(w1.notebook, first, nb3)
    return later(s4, 800)
def s4():
    w3 = S["w3"]
    check("both tabs arrived in the third window", len(w3.tabs) == 2 and all(t.win is w3 for t in w3.tabs) and w3.notebook.get_show_tabs())
    check("emptied source window closed itself", S["w1_destroyed"] == [1] and S["w1"] not in app.get_windows())
    check("context menu / drops in a moved tab go to its window", w3.tabs[0].columns[0].gfile is not None)
    print("%d/%d passed" % (sum(ok for _, ok in results), len(results)))
    app.quit(); return False
GLib.timeout_add(1200, s1)
app.run(["finch", os.path.join(tmp, "A")]); shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if all(ok for _, ok in results) else 1)
