# Android packages show their launcher icon: in the preview pane and as the
# thumbnail of the icon view. The packages in tests/data were built with
# aapt2 (see tests/data/make_apks.py): a bitmap icon in two densities, an
# adaptive icon (colour behind a vector), a vector with a gradient, none.
# The preview pane also says which package it is and its version, and the
# rows of the columns carry the icon too.
import os, shutil, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gio, GdkPixbuf, Gtk
from app import apk, thumbs, C_NAME, C_FILE, C_INFO, C_ICON
from app.application import FinchApp
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
work = os.path.join(os.environ["FINCH_SANDBOX"], "files", "apks")
os.makedirs(work)
for name in ("bitmap.apk", "adaptive.apk", "vector.apk", "noicon.apk"):
    shutil.copy(os.path.join(DATA, name), work)
open(os.path.join(work, "broken.apk"), "w").write("not a zip at all")
open(os.path.join(work, "notes.txt"), "w").write("not a package")
P = lambda name: os.path.join(work, name)
results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, extra if not ok else "", flush=True)
def px(pixbuf, x, y):
    """(r, g, b, a) at (x, y)."""
    n = pixbuf.get_n_channels(); at = y * pixbuf.get_rowstride() + x * n
    data = pixbuf.get_pixels()[at:at + n]
    return tuple(data) + ((255,) if n == 3 else ())
def near(got, want, slack=24):
    return all(abs(g - w) <= slack for g, w in zip(got, want))
def dims(pb): return None if pb is None else (pb.get_width(), pb.get_height())

# -- the reader itself ---------------------------------------------------------
pb = apk.icon(P("bitmap.apk"), 256)
check("bitmap icon: the densest one, at its own size", dims(pb) == (144, 144) and near(px(pb, 72, 72), (255, 0, 0, 255)), "%s %s" % (dims(pb), pb and px(pb, 72, 72)))
pb = apk.icon(P("bitmap.apk"), 64)
check("bitmap icon: scaled down to the size asked for", dims(pb) == (64, 64) and near(px(pb, 32, 32), (255, 0, 0, 255)), str(dims(pb)))
pb = apk.icon(P("adaptive.apk"), 256)
check("adaptive icon: drawn at the size asked for", dims(pb) == (256, 256), str(dims(pb)))
if pb is not None:
    check("adaptive icon: the vector foreground in the middle", near(px(pb, 128, 128), (255, 0, 0, 255)), str(px(pb, 128, 128)))
    check("adaptive icon: the colour background around it", near(px(pb, 128, 40), (0, 255, 0, 255)) and near(px(pb, 40, 128), (0, 255, 0, 255)), "%s %s" % (px(pb, 128, 40), px(pb, 40, 128)))
    check("adaptive icon: foreground where the visible 72 of 108 units put it", near(px(pb, 96, 128), (255, 0, 0, 255)) and near(px(pb, 76, 128), (0, 255, 0, 255)), "%s %s" % (px(pb, 96, 128), px(pb, 76, 128)))
    check("adaptive icon: corners cut off", px(pb, 2, 2)[3] == 0 and px(pb, 253, 253)[3] == 0, "%s %s" % (px(pb, 2, 2), px(pb, 253, 253)))
pb = apk.icon(P("vector.apk"), 256)
check("vector icon: drawn at the size asked for", dims(pb) == (256, 256), str(dims(pb)))
if pb is not None:
    left, right, mid = px(pb, 12, 30), px(pb, 243, 30), px(pb, 128, 128)
    check("vector icon: the gradient runs from red to blue", left[0] > 200 and left[2] < 60 and right[2] > 200 and right[0] < 60 and left[3] == right[3] == 255, "%s %s" % (left, right))
    check("vector icon: the translated group's square on top", near(mid, (255, 255, 255, 255)), str(mid))
check("no icon in the manifest: None", apk.icon(P("noicon.apk"), 256) is None)
check("not a zip: None", apk.icon(P("broken.apk"), 256) is None)
check("no such file: None", apk.icon(P("missing.apk"), 256) is None)
got = apk.info(P("bitmap.apk"))
check("manifest: package, version name and number", got == {"package": "finch.test", "version": "1.2.3", "code": 7}, str(got))
got = apk.info(P("adaptive.apk"))
check("manifest: a version name kept in the resources", got == {"package": "finch.test", "version": "2.0-beta", "code": 42}, str(got))
got = apk.info(P("noicon.apk"))
check("manifest: a number without a name", got == {"package": "finch.test", "code": 5}, str(got))
got = apk.info(P("vector.apk"))
check("manifest: no version at all", got == {"package": "finch.test"}, str(got))
check("manifest: nothing from a damaged or missing file", apk.info(P("broken.apk")) == {} and apk.info(P("missing.apk")) == {})

# -- in the application ----------------------------------------------------------
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
def row(name): return next(r for r in col().store if r[C_NAME] == name)
def select(name): col()._select_only(col().handle_of(row(name)[C_FILE]))
def logo(name):
    """The row's icon if it is a picture (not the file type's themed icon)."""
    icon = row(name)[C_ICON]
    return icon if isinstance(icon, GdkPixbuf.Pixbuf) else None
def facts():
    """The details rows the package fills in, {label: value}."""
    return {k: v for k, v in w().tab.preview._rows if k in ("Package", "Version")}
def stamp(name):
    r = row(name); i = r[C_INFO]
    return thumbs.stamp(r[C_FILE], i.get_attribute_uint64("time::modified"), i.get_size())
def s1():
    S["w"] = app.get_active_window(); mode("columns")
    check("packages are recognised by type", row("vector.apk")[C_INFO].get_content_type() == apk.CONTENT_TYPE, row("vector.apk")[C_INFO].get_content_type())
    select("adaptive.apk")
    return later(s2)
def rows_checked(when):
    got = {n: logo(n) for n in ("bitmap.apk", "adaptive.apk", "vector.apk", "noicon.apk", "broken.apk")}
    check("columns%s: the rows of packages with an icon show it, at the size of a row icon" % when,
          all(dims(got[n]) == (16, 16) for n in ("bitmap.apk", "adaptive.apk", "vector.apk")), str({n: dims(p) for n, p in got.items()}))
    check("columns%s: it is the package's icon" % when, got["bitmap.apk"] is not None and near(px(got["bitmap.apk"], 8, 8), (255, 0, 0, 255))
          and got["adaptive.apk"] is not None and near(px(got["adaptive.apk"], 8, 8), (255, 0, 0, 255)) and near(px(got["adaptive.apk"], 8, 3), (0, 255, 0, 255)))
    check("columns%s: a package without icon and a damaged one keep the file type's icon" % when, got["noicon.apk"] is None and got["broken.apk"] is None
          and row("noicon.apk")[C_ICON].equal(row("noicon.apk")[C_INFO].get_icon()))
def s2():
    pv = w().tab.preview
    rows_checked("")
    check("other files keep theirs", logo("notes.txt") is None)
    check("preview: the package's icon is the picture", pv is not None and pv._showing == "content" and dims(pv._pixbuf) == (256, 256) and near(px(pv._pixbuf, 128, 128), (255, 0, 0, 255)), str(pv and (pv._showing, dims(pv._pixbuf))))
    r = row("adaptive.apk")
    hit = thumbs.disk_lookup(r[C_FILE].get_uri(), r[C_INFO].get_attribute_uint64("time::modified"))
    check("preview: kept in the thumbnail cache", hit is not None and dims(hit[0]) == (256, 256) and hit[2], str(hit and dims(hit[0])))
    check("...the sandbox's, not the real one", thumbs._cache_dir().startswith(os.environ["FINCH_SANDBOX"]), thumbs._cache_dir())
    check("preview: package and version rows", facts() == {"Package": "finch.test", "Version": "2.0-beta (42)"}, str(pv._rows))
    check("preview: they come before the dates", [k for k, _v in pv._rows][:2] == ["Package", "Version"], str(pv._rows))
    select("vector.apk")
    return later(s2b)
def s2b():
    check("preview: no version row for a package without one", facts() == {"Package": "finch.test"}, str(w().tab.preview._rows))
    select("adaptive.apk")
    return later(s2c)
def s2c():
    check("preview: the rows are there again from the cache", facts() == {"Package": "finch.test", "Version": "2.0-beta (42)"}, str(w().tab.preview._rows))
    select("noicon.apk")
    return later(s3)
def s3():
    pv = w().tab.preview
    check("preview: a package without icon keeps the file type's icon", pv is not None and pv._showing == "placeholder" and pv._pixbuf is None, str(pv and pv._showing))
    check("preview: ...and still says what it is", facts() == {"Package": "finch.test", "Version": "5"}, str(pv._rows))
    select("broken.apk")
    return later(s4)
def s4():
    pv = w().tab.preview
    check("preview: a damaged package too", pv is not None and pv._showing == "placeholder", str(pv and pv._showing))
    check("preview: no empty rows for it", facts() == {} and not any(v == "…" for _k, v in pv._rows), str(pv._rows))
    mode("grid")
    return later(s5, 2500)
def s5():
    got = {n: thumbs.thumb_get(stamp(n)) for n in ("bitmap.apk", "adaptive.apk", "vector.apk", "noicon.apk")}
    check("icon view: every package has been looked at", all(known for known, _pb in got.values()), str({n: k for n, (k, _p) in got.items()}))
    check("icon view: thumbnails for the three with an icon, none for the fourth",
          all(got[n][1] is not None for n in ("bitmap.apk", "adaptive.apk", "vector.apk")) and got["noicon.apk"][1] is None,
          str({n: dims(p) for n, (_k, p) in got.items()}))
    pb = got["bitmap.apk"][1]
    check("icon view: the thumbnail is the icon", pb is not None and near(px(pb, pb.get_width() // 2, pb.get_height() // 2), (255, 0, 0, 255)))
    mode("columns")
    return later(s6, 300)
def s6():
    rows_checked(" again")      # known by now: there as soon as the list is
    return later(lambda: (app.quit(), False)[1], 100)
later(s1, 1500)
app.run(["finch", work])
print("%d/%d passed" % (sum(ok for _n, ok in results), len(results)))
