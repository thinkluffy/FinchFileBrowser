"""Caches for the preview pane's renderings (pictures, PDF pages, video
frames) and the facts read alongside them (pixel size, pages, duration).

Two layers. An in-process LRU makes re-selecting a file within a session
instant. Below it, the freedesktop thumbnail cache in ~/.cache/thumbnails,
the one Nemo and Cinnamon share: PNG files named by the MD5 of the file's
URI, in one directory per size, carrying the file's URI and modification
time (and, by the same spec, image size, page count and movie length) as
PNG text chunks. Finch writes its renderings there as "xx-large" entries,
for PDFs and videos only, and reads any size: a small entry made by Nemo is
a placeholder to show while the full rendering is made.

`meta` dicts use the keys "dims" ((width, height)), "pages" and "duration"
(seconds); only the known ones are present.

Thumbnailer, at the end, serves the icon grid: small pictures made on a
worker thread from the same caches (and, for images, from the file).
"""
import hashlib
import os
import tempfile
import threading
from collections import OrderedDict

from gi.repository import GdkPixbuf, GLib

from . import MAX_IMAGE_PREVIEW

MEMORY_ENTRIES = 32                  # at most ~2 MB each
# Freedesktop size names, largest first, with the bound of their longer side.
SIZES = (("xx-large", 1024), ("x-large", 512), ("large", 256), ("normal", 128))
GOOD_ENOUGH = 512                    # a cached entry this big is the final picture
WRITE_SIZE = "xx-large"

_lock = threading.Lock()
_memory = OrderedDict()              # stamp -> (pixbuf, meta)


def stamp(gfile, mtime, size):
    """The cache key of a file: its URI, modification time and size."""
    return (gfile.get_uri(), int(mtime), int(size))


# -- memory ------------------------------------------------------------------
def memory_get(key):
    with _lock:
        hit = _memory.get(key)
        if hit is not None:
            _memory.move_to_end(key)
    return hit


def memory_put(key, pixbuf, meta):
    with _lock:
        _memory[key] = (pixbuf, dict(meta))
        _memory.move_to_end(key)
        while len(_memory) > MEMORY_ENTRIES:
            _memory.popitem(last=False)


def memory_clear():
    with _lock:
        _memory.clear()


# -- disk --------------------------------------------------------------------
def _cache_dir():
    return os.path.join(GLib.get_user_cache_dir(), "thumbnails")


def _entry_name(uri):
    return hashlib.md5(uri.encode("utf-8")).hexdigest() + ".png"


def _meta_from_options(pixbuf):
    def num(key):
        value = pixbuf.get_option("tEXt::" + key)
        try:
            return int(value) if value else None
        except ValueError:
            return None
    meta = {}
    width, height = num("Thumb::Image::Width"), num("Thumb::Image::Height")
    if width and height:
        meta["dims"] = (width, height)
    pages = num("Thumb::Document::Pages")
    if pages:
        meta["pages"] = pages
    length = num("Thumb::Movie::Length")
    if length:
        meta["duration"] = length
    return meta


def disk_lookup(uri, mtime):
    """(pixbuf, meta, final?) for the newest valid cached thumbnail of `uri`,
    largest size first, or None. `final` says the entry is big enough to
    stand as the preview itself rather than a placeholder."""
    name = _entry_name(uri)
    for size_name, bound in SIZES:
        path = os.path.join(_cache_dir(), size_name, name)
        if not os.path.exists(path):
            continue
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        except GLib.Error:
            continue                                # damaged: ignore it
        if pixbuf.get_option("tEXt::Thumb::MTime") != str(int(mtime)):
            continue                                # stale
        return pixbuf, _meta_from_options(pixbuf), bound >= GOOD_ENOUGH
    return None


def disk_save(uri, mtime, size, pixbuf, meta, size_name=WRITE_SIZE):
    """Store `pixbuf` as the `size_name` thumbnail of `uri`, with the facts
    in `meta`, the way the spec wants it: private directory and file,
    written whole before it appears under its name. Failures are silent: a
    cache that cannot be written is no worse than none."""
    bound = dict(SIZES)[size_name]
    if max(pixbuf.get_width(), pixbuf.get_height()) > bound:
        return False
    keys = ["tEXt::Thumb::URI", "tEXt::Thumb::MTime", "tEXt::Thumb::Size", "tEXt::Software"]
    values = [uri, str(int(mtime)), str(int(size)), "Finch"]
    if "dims" in meta:
        keys += ["tEXt::Thumb::Image::Width", "tEXt::Thumb::Image::Height"]
        values += [str(meta["dims"][0]), str(meta["dims"][1])]
    if "pages" in meta:
        keys.append("tEXt::Thumb::Document::Pages")
        values.append(str(meta["pages"]))
    if "duration" in meta:
        keys.append("tEXt::Thumb::Movie::Length")
        values.append(str(int(meta["duration"])))
    directory = os.path.join(_cache_dir(), size_name)
    try:
        os.makedirs(directory, 0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".png")   # mode 0600
        os.close(fd)
        try:
            pixbuf.savev(tmp, "png", keys, values)
            os.replace(tmp, os.path.join(directory, _entry_name(uri)))
        except (GLib.Error, OSError):
            os.unlink(tmp)
            return False
    except OSError:
        return False
    return True


# -- the icon grid's thumbnails ------------------------------------------------
THUMB_ENTRIES = 4000                 # ~16 kB each at 64 px
GRID_WRITE_SIZE = "large"            # images rendered for the grid are kept at 256 px
_thumbs = OrderedDict()              # stamp -> small pixbuf, or None (nothing to show)
_thumbs_lock = threading.Lock()


def thumb_get(key):
    """(known?, pixbuf) for a grid thumbnail: known with None means the file
    has no picture, so the icon stands."""
    with _thumbs_lock:
        if key not in _thumbs:
            return False, None
        _thumbs.move_to_end(key)
        return True, _thumbs[key]


def _thumb_put(key, pixbuf):
    with _thumbs_lock:
        _thumbs[key] = pixbuf
        _thumbs.move_to_end(key)
        while len(_thumbs) > THUMB_ENTRIES:
            _thumbs.popitem(last=False)


def _fit(pixbuf, bound):
    """`pixbuf` scaled down to fit a `bound` square, never up."""
    w, h = pixbuf.get_width(), pixbuf.get_height()
    scale = min(bound / w, bound / h, 1.0)
    if scale >= 1.0:
        return pixbuf
    return pixbuf.scale_simple(max(1, round(w * scale)), max(1, round(h * scale)),
                               GdkPixbuf.InterpType.BILINEAR)


def _grid_picture(gfile, path, ctype, mtime, size):
    """A picture of the file for the grid: any cached thumbnail, else for an
    image a fresh rendering (kept in the disk cache for next time). PDFs and
    videos are only shown from the cache: rendering them is slow, and the
    preview pane writes an entry the first time one is looked at."""
    uri = gfile.get_uri()
    cached = disk_lookup(uri, mtime)
    if cached is not None:
        return cached[0]
    if not (ctype.startswith("image/") and path and size <= MAX_IMAGE_PREVIEW):
        return None
    bound = dict(SIZES)[GRID_WRITE_SIZE]
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, bound, bound, True)
    except GLib.Error:
        return None
    pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    _fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(path)
    meta = {"dims": (width, height)} if width > 0 and height > 0 else {}
    disk_save(uri, mtime, size, pixbuf, meta, GRID_WRITE_SIZE)
    return pixbuf


class Thumbnailer:
    """Makes grid thumbnails of `bound` px on one worker thread. `want()`
    replaces the queue with the files now in view, so scrolling past a
    thousand pictures never leaves a thousand renderings queued; each
    finished one is announced with `on_ready()` on the main loop."""

    def __init__(self, bound, on_ready):
        self.bound = bound
        self._on_ready = on_ready
        self._cond = threading.Condition()
        self._wanted = []
        self._thread = None
        self._stopped = False

    def want(self, requests):
        """`requests`: (key, gfile, path, ctype, mtime, size) tuples, first
        wanted first. Only what is not known yet is kept."""
        with self._cond:
            if self._stopped:
                return
            self._wanted = [r for r in requests if not thumb_get(r[0])[0]]
            if self._wanted and self._thread is None:
                self._thread = threading.Thread(target=self._work, daemon=True)
                self._thread.start()
            self._cond.notify()

    def stop(self):
        with self._cond:
            self._stopped = True
            self._wanted = []
            self._cond.notify()

    def _work(self):
        while True:
            with self._cond:
                while not self._wanted and not self._stopped:
                    self._cond.wait()
                if self._stopped:
                    return
                key, gfile, path, ctype, mtime, size = self._wanted.pop(0)
            if thumb_get(key)[0]:
                continue
            try:
                picture = _grid_picture(gfile, path, ctype, mtime, size)
            except GLib.Error:
                picture = None
            _thumb_put(key, _fit(picture, self.bound) if picture is not None else None)
            GLib.idle_add(self._on_ready)
