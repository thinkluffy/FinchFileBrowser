"""Which emblems the icon view gives encrypted / read-only / unreadable /
linked files. With an argument, also saves a screenshot of the grid there."""
import os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gio, Gdk, Gtk
from app.application import FinchApp
from app import grid as G, C_NAME, C_FILE, C_INFO

tmp = tempfile.mkdtemp(prefix="finch-lockshot-", dir=os.path.expanduser("~"))
os.makedirs(tmp + "/src"); open(tmp + "/src/a.txt", "w").write("hello")
run = lambda *a: subprocess.run(["7z", "a", "-bso0", "-bse0", *a], cwd=tmp + "/src", check=True)
run(tmp + "/plain.zip", "a.txt"); run("-tzip", "-psecret", tmp + "/secret.zip", "a.txt")
run("-psecret", "-mhe=on", tmp + "/secret_he.7z", "a.txt")
shutil.rmtree(tmp + "/src")
open(tmp + "/ro.txt", "w").write("x"); os.chmod(tmp + "/ro.txt", 0o444)
open(tmp + "/noread.txt", "w").write("x"); os.chmod(tmp + "/noread.txt", 0)
os.symlink("plain.zip", tmp + "/link.zip"); os.symlink("ro.txt", tmp + "/rolink.txt")
os.mkdir(tmp + "/rodir"); os.chmod(tmp + "/rodir", 0o555)
shutil.copy(tmp + "/secret.zip", tmp + "/rosecret.zip"); os.chmod(tmp + "/rosecret.zip", 0o444)
open(tmp + "/notes.txt", "w").write("x"); os.mkdir(tmp + "/Folder")

NAMES = {G.LOCK_EMBLEM: "lock", G.READONLY_EMBLEM: "ro", G.UNREADABLE_EMBLEM: "noread",
         G.SYMLINK_EMBLEM: "link"}
WANT = {"plain.zip": [], "secret.zip": ["lock"], "secret_he.7z": ["lock"], "ro.txt": ["ro"],
        "noread.txt": ["noread"], "link.zip": ["link"], "rolink.txt": ["ro", "link"],
        "rodir": ["ro"], "rosecret.zip": ["lock"], "notes.txt": [], "Folder": []}
app = FinchApp()
fails = []

def go():
    w = app.get_active_window()
    w.lookup_action("view-mode").change_state(GLib.Variant.new_string("grid"))
    GLib.timeout_add(1500, check); return False

def check():
    g = app.get_active_window().columns[0]
    got = {row[C_NAME]: [NAMES[e] for e in g._emblems(row[C_FILE], row[C_INFO], [])] for row in g.items}
    print("emblems:", got)
    for name, want in WANT.items():
        if got.get(name) != want:
            fails.append("%s: want %s got %s" % (name, want, got.get(name)))
    GLib.timeout_add(300, shot); return False

def shot():
    w = app.get_active_window(); gw = w.get_window()
    pb = Gdk.pixbuf_get_from_window(gw, 0, 0, gw.get_width(), gw.get_height())
    if len(sys.argv) > 1:               # tests/run.sh -- python3 tests/test_emblems.py out.png
        pb.savev(sys.argv[1], "png", [], [])
    w.lookup_action("view-mode").change_state(GLib.Variant.new_string("columns"))
    GLib.timeout_add(300, lambda: (app.quit(), False)[1]); return False

GLib.timeout_add(1200, go)
app.run(["finch", tmp])
os.chmod(tmp + "/rodir", 0o755); shutil.rmtree(tmp)
print("FAIL\n" + "\n".join(fails) if fails else "PASS emblem checks")
