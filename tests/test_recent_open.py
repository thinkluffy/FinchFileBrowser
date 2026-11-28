# The Recent place and the files opened from Finch. A file opened here goes
# on the recently used list (programs are never really started: the launch
# is replaced by a recorder); opened from Recent, it is the file itself that
# is handed to the program. The entries of Recent are virtual
# (recent:///<hash>): their rows, the icon view and the preview pane show
# the pictures of the files they stand for.
import os, shutil, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gio, GdkPixbuf, Gtk
box = os.environ["FINCH_SANDBOX"]; files = box + "/files"
assert GLib.get_user_data_dir().startswith(box), "not sandboxed"
from app import thumbs, util, C_NAME, C_FILE, C_INFO, C_ICON
from app.application import FinchApp
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
P = lambda name: os.path.join(files, name)
U = lambda name: Gio.File.new_for_path(P(name)).get_uri()
def picture(path, width, height, rgb):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pb.fill((rgb[0] << 24) | (rgb[1] << 16) | (rgb[2] << 8) | 0xff)
    pb.savev(path, "png", [], [])
    return pb
RED, YELLOW = (255, 0, 0), (255, 255, 0)
picture(P("pic.png"), 200, 100, RED)
shutil.copy(os.path.join(DATA, "clip.avi"), P("clip.avi"))
st = os.stat(P("clip.avi"))
thumbs.disk_save(U("clip.avi"), st.st_mtime, st.st_size, picture(P(".thumb.png"), 256, 144, YELLOW), {}, "large")
os.unlink(P(".thumb.png"))
open(P("notes.txt"), "w").write("some text")
os.makedirs(P("Downloads")); open(P("Downloads/opened.txt"), "w").write("opened from Finch")
rm = Gtk.RecentManager.get_default()
for name, mime in (("pic.png", "image/png"), ("clip.avi", "video/vnd.avi"), ("notes.txt", "text/plain")):
    d = Gtk.RecentData(); d.mime_type = mime; d.app_name = "finch-test"; d.app_exec = "true %u"
    assert rm.add_full(U(name), d)
launched = []
Gio.AppInfo.launch_default_for_uri = staticmethod(lambda uri, ctx: launched.append(uri) or True)

results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra if not ok else "", flush=True)
def near(got, want, slack=24): return all(abs(g - w) <= slack for g, w in zip(got, want))
def middle(pb):
    n = pb.get_n_channels(); at = (pb.get_height() // 2) * pb.get_rowstride() + (pb.get_width() // 2) * n
    return tuple(pb.get_pixels()[at:at + 3])
app = FinchApp(); S = {}
def later(fn, ms=1200):
    def run():
        try: return fn()
        except Exception:
            import traceback; traceback.print_exc(); app.quit(); return False
    GLib.timeout_add(ms, run); return False
def w(): return S["w"]
def mode(m): w().lookup_action("view-mode").change_state(GLib.Variant("s", m))
def col(): return w().tab.columns[0]
def names(): return sorted(r[C_NAME] for r in col().store)
def row(name): return next(r for r in col().store if r[C_NAME] == name)
def shown(name):
    icon = row(name)[C_ICON]
    return icon if isinstance(icon, GdkPixbuf.Pixbuf) else None
def real_stamp(name):
    s = os.stat(P(name)); return thumbs.stamp(Gio.File.new_for_path(P(name)), s.st_mtime, s.st_size)

def s1():
    S["w"] = app.get_active_window(); mode("columns")
    check("recent lists the three files", names() == ["clip.avi", "notes.txt", "pic.png"], str(names()))
    check("its entries are virtual", row("pic.png")[C_FILE].get_uri_scheme() == "recent" and row("pic.png")[C_FILE].get_path() is None,
          row("pic.png")[C_FILE].get_uri())
    return later(s2, 1500)
def s2():
    pb, clip = shown("pic.png"), shown("clip.avi")
    check("recent: a picture's row shows the picture", pb is not None and near(middle(pb), RED))
    check("recent: a video's row shows its cached thumbnail", clip is not None and near(middle(clip), YELLOW))
    check("recent: other rows keep the file type's icon", shown("notes.txt") is None)
    known, thumb = thumbs.thumb_get(real_stamp("pic.png"))
    check("recent: the thumbnail is the file's own, shared with its folder", known and thumb is not None)
    col()._select_only(col().handle_of(row("pic.png")[C_FILE]))
    return later(s3)
def s3():
    pv = w().tab.preview
    rows = dict(pv._rows) if pv is not None else {}
    check("recent: the preview pane shows the picture", pv is not None and pv._showing == "content" and pv._pixbuf is not None,
          str(pv and (pv._showing, pv._pixbuf)))
    check("recent: ...and its size", rows.get("Dimensions") == "200 × 100", str(rows))
    mode("grid")
    return later(s4)
def s4():
    g = w().tab.columns[0]
    check("recent, icon view: still the three files", sorted(it[1] for it in g.items) == ["clip.avi", "notes.txt", "pic.png"])
    known, thumb = thumbs.thumb_get(real_stamp("clip.avi"))
    check("recent, icon view: thumbnails from the files themselves", known and thumb is not None and near(middle(thumb), YELLOW))
    mode("columns")
    return later(s4b)
def s4b():
    # Opened from Recent: the program gets the file, not the virtual entry.
    r = row("notes.txt")
    w().tab._on_item_activated(col(), r[C_FILE], r[C_INFO])
    check("opened from recent: the file itself is handed over", launched == [U("notes.txt")], str(launched))
    del launched[:]
    w().tab.set_root(Gio.File.new_for_path(P("Downloads")))
    return later(s5)
def s5():
    r = row("opened.txt")
    check("not on the list before it is opened", not rm.has_item(U("Downloads/opened.txt")))
    w().tab._on_item_activated(col(), r[C_FILE], r[C_INFO])
    check("opening a file starts its program", launched == [U("Downloads/opened.txt")], str(launched))
    return later(s6, 1500)
def s6():
    check("...and puts it on the recently used list", rm.has_item(U("Downloads/opened.txt")))
    # A folder shown in the file manager (the fallback of "Open in Nemo") is not a recently used file.
    util.launch_default(Gio.File.new_for_path(P("Downloads")), w(), remember=False)
    w().tab.set_root(Gio.File.new_for_uri("recent:///"))
    return later(s7, 2000)
def s7():
    check("recent shows the file opened from Finch", names() == ["clip.avi", "notes.txt", "opened.txt", "pic.png"], str(names()))
    check("a folder is not put there", not rm.has_item(U("Downloads")))
    app.quit(); return False
later(s1, 1500)
app.run(["finch", "recent:///"])
print("%d/%d passed" % (sum(ok for _n, ok in results), len(results)))
