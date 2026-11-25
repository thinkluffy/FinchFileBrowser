# No arguments: one tab on the home folder, whatever state.json says.
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gio, Gtk
from app import util as stmod
from app.application import FinchApp
# plant stale keys the old versions wrote
st = stmod.load_state()
st.update({"tabs": ["file:///tmp", "file:///usr"], "last_root": "file:///tmp", "current_tab": 1})
stmod.save_state(st)
app = FinchApp()
fails = []
def go():
    w = app.get_active_window()
    home = Gio.File.new_for_path(GLib.get_home_dir())
    ok = len(w.tabs) == 1 and w.tab.root.equal(home)
    print(("PASS" if ok else "FAIL") + " no args: single tab on home", [t.root.get_uri() for t in w.tabs])
    if not ok: fails.append(1)
    w.save_window_state()
    st2 = stmod.load_state()
    ok = not any(k in st2 for k in ("tabs", "last_root", "current_tab"))
    print(("PASS" if ok else "FAIL") + " saved state has no location keys", sorted(st2))
    if not ok: fails.append(1)
    print("%d failures" % len(fails)); app.quit(); return False
GLib.timeout_add(1500, go)
app.run(["finch"])
