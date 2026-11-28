# In the columns, the rows of pictures and videos show the file itself in
# place of the file type's icon: the icon view's thumbnails at the size of a
# row icon. Pictures are read in the background, for the rows in view only;
# a video shows what the thumbnail cache has, which the preview pane fills.
# tests/data/clip.avi is five frames of a test pattern (made with gst-launch).
import os, shutil, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gio, GdkPixbuf, Gtk
from app import thumbs, C_NAME, C_FILE, C_INFO, C_ICON
from app.application import FinchApp
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
work = os.path.join(os.environ["FINCH_SANDBOX"], "files", "pics")
many = os.path.join(os.environ["FINCH_SANDBOX"], "files", "many")
os.makedirs(work); os.makedirs(many)
P = lambda name: os.path.join(work, name)
def picture(path, width, height, rgb):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pb.fill((rgb[0] << 24) | (rgb[1] << 16) | (rgb[2] << 8) | 0xff)
    pb.savev(path, "png", [], [])
    return pb
RED, GREEN, BLUE, YELLOW = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)
picture(P("wide.png"), 200, 100, RED)
picture(P("tall.png"), 50, 200, GREEN)
picture(P("tiny.png"), 8, 8, BLUE)
open(P("broken.png"), "w").write("not a picture at all")
open(P("notes.txt"), "w").write("just text")
for name in ("cached.avi", "plain.avi"):
    shutil.copy(os.path.join(DATA, "clip.avi"), P(name))
# A thumbnail of cached.avi is in the cache already, as if Nemo had made it.
st = os.stat(P("cached.avi"))
thumbs.disk_save(Gio.File.new_for_path(P("cached.avi")).get_uri(), st.st_mtime, st.st_size,
                 picture(P(".thumb.png"), 256, 144, YELLOW), {}, "large")
os.unlink(P(".thumb.png"))
for i in range(300):
    picture(os.path.join(many, "%03d.png" % i), 32, 32, RED)
HAVE_FFMPEG = shutil.which("ffmpegthumbnailer") is not None

results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra if not ok else "", flush=True)
def px(pixbuf, x, y):
    n = pixbuf.get_n_channels(); at = y * pixbuf.get_rowstride() + x * n
    return tuple(pixbuf.get_pixels()[at:at + 3])
def clear(pixbuf, x, y):
    """Whether the pixel is transparent."""
    return pixbuf.get_has_alpha() and pixbuf.get_pixels()[y * pixbuf.get_rowstride() + x * 4 + 3] == 0
def box(pixbuf):
    """(left, top, right, bottom) of what is not transparent."""
    if pixbuf is None: return None
    w, h = pixbuf.get_width(), pixbuf.get_height()
    on = [(x, y) for y in range(h) for x in range(w) if not clear(pixbuf, x, y)]
    return (min(x for x, _y in on), min(y for _x, y in on), max(x for x, _y in on), max(y for _x, y in on))
def near(got, want, slack=24):
    return all(abs(g - w) <= slack for g, w in zip(got, want))
def dims(pb): return None if pb is None else (pb.get_width(), pb.get_height())

app = FinchApp(); S = {}
def later(fn, ms=1200):
    def run():
        try: return fn()
        except Exception:
            import traceback; traceback.print_exc(); app.quit(); return False
    GLib.timeout_add(ms, run); return False
def w(): return S["w"]
def col(): return w().tab.columns[0]
def row(name): return next(r for r in col().store if r[C_NAME] == name)
def shown(name):
    """The picture a row shows, or None when it has the file type's icon."""
    icon = row(name)[C_ICON]
    return icon if isinstance(icon, GdkPixbuf.Pixbuf) else None
def themed(name): return row(name)[C_ICON].equal(row(name)[C_INFO].get_icon())
def stamp(r):
    i = r[C_INFO]
    return thumbs.stamp(r[C_FILE], i.get_attribute_uint64("time::modified"), i.get_size())
def middle(pb): return px(pb, pb.get_width() // 2, pb.get_height() // 2)

def s1():
    S["w"] = app.get_active_window()
    w().lookup_action("view-mode").change_state(GLib.Variant("s", "columns"))
    return later(s2)
def s2():
    got = {n: shown(n) for n in ("wide.png", "tall.png", "tiny.png", "broken.png", "notes.txt", "cached.avi", "plain.avi")}
    sizes = {n: dims(p) for n, p in got.items()}
    boxes = {n: box(p) for n, p in got.items()}
    check("a picture's row shows the picture, at the size of a row icon",
          sizes["wide.png"] == (16, 16) and sizes["tall.png"] == (16, 16), str(sizes))
    check("a wide picture keeps its shape, in the middle of the icon's square", boxes["wide.png"] == (0, 4, 15, 11), str(boxes["wide.png"]))
    check("a tall one too", boxes["tall.png"] == (6, 0, 9, 15), str(boxes["tall.png"]))
    check("it is that picture", got["wide.png"] is not None and near(middle(got["wide.png"]), RED)
          and got["tall.png"] is not None and near(middle(got["tall.png"]), GREEN))
    check("a tiny picture fills the icon, as it does in the icon view", sizes["tiny.png"] == (16, 16) and near(middle(got["tiny.png"]), BLUE), str(sizes))
    check("a file that is no picture after all keeps the file type's icon", got["broken.png"] is None and themed("broken.png"),
          row("broken.png")[C_INFO].get_content_type())
    check("other files keep theirs", got["notes.txt"] is None and themed("notes.txt"))
    check("a video with a cached thumbnail shows it", boxes["cached.avi"] == (0, 3, 15, 11) and near(middle(got["cached.avi"]), YELLOW),
          "%s %s" % (boxes["cached.avi"], row("cached.avi")[C_INFO].get_content_type()))
    check("a video without one keeps the file type's icon", got["plain.avi"] is None and themed("plain.avi")
          and thumbs.thumb_get(stamp(row("plain.avi"))) == (True, None), str(thumbs.thumb_get(stamp(row("plain.avi")))))
    # Looking at the video makes the preview pane render a frame and cache it.
    col()._select_only(col().handle_of(row("plain.avi")[C_FILE]))
    return later(s3, 2500)
def s3():
    if HAVE_FFMPEG:
        pb = shown("plain.avi")
        check("once the preview has rendered a frame of it, the row shows that", box(pb) == (0, 3, 15, 11), str(box(pb)))
    else:
        print("SKIP the preview's frame in the row: no ffmpegthumbnailer", flush=True)
    # A reload puts the pictures back with the rows, not a moment later.
    before = {n: shown(n) for n in ("wide.png", "tall.png", "cached.avi")}
    c = col(); rebuilt = c._rows_changed
    def spy():
        rebuilt()
        S["at once"] = {n: shown(n) for n in before}
    c._rows_changed = spy
    c._reload_keep_selection()
    return later(s4, 600)
def s4():
    got = S.get("at once")
    check("reload: the rows have their pictures the moment the list is rebuilt",
          got is not None and all(p is not None for p in got.values()), str(got))
    del col()._rows_changed
    # The file changes: its row follows.
    picture(P("wide.png"), 100, 100, BLUE)
    st = os.stat(P("wide.png")); os.utime(P("wide.png"), (st.st_atime, st.st_mtime + 5))
    col()._reload_keep_selection()      # what the folder's monitor does (there may be none: inotify limits)
    return later(s5, 1500)
def s5():
    pb = shown("wide.png")
    check("a picture that was replaced: the row shows the new one", dims(pb) == (16, 16) and near(middle(pb), BLUE),
          "%s %s" % (dims(pb), pb and middle(pb)))
    check("...and the others still theirs", shown("tall.png") is not None and shown("cached.avi") is not None)
    w().tab.set_root(Gio.File.new_for_path(many))
    return later(s6, 2500)
def s6():
    rows = list(col().store)
    check("300 pictures listed", len(rows) == 300, str(len(rows)))
    made = [thumbs.thumb_get(stamp(r))[0] for r in rows]
    span = col().view.get_visible_range()
    last_seen = span[1].get_indices()[0]
    check("long folder: the rows in view have their pictures",
          all(isinstance(r[C_ICON], GdkPixbuf.Pixbuf) for r in rows[:last_seen + 1]), "in view up to %d" % last_seen)
    check("long folder: nothing was read for the rows below", last_seen < 299 and not any(made[last_seen + 1:]),
          "in view up to %d, made %d" % (last_seen, sum(made)))
    col().view.scroll_to_cell(Gtk.TreePath(299), None, True, 1.0, 0)
    return later(s7, 2000)
def s7():
    rows = list(col().store)
    first_seen = col().view.get_visible_range()[0].get_indices()[0]
    check("long folder: scrolled to the end, the rows there get theirs",
          first_seen > 100 and all(isinstance(r[C_ICON], GdkPixbuf.Pixbuf) for r in rows[first_seen:]), "in view from %d" % first_seen)
    check("long folder: the rows scrolled past were not read", not thumbs.thumb_get(stamp(rows[first_seen // 2 + 20]))[0])
    check("long folder: the rows left behind keep theirs", isinstance(rows[0][C_ICON], GdkPixbuf.Pixbuf))
    app.quit(); return False
later(s1, 1500)
app.run(["finch", work])
print("%d/%d passed" % (sum(ok for _n, ok in results), len(results)))
