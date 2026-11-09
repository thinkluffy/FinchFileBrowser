"""The right-most pane: image / text preview and details of the selected file."""
import subprocess
import threading

import gi
from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Pango

try:                                    # video metadata; optional
    gi.require_version("Gst", "1.0")
    gi.require_version("GstPbutils", "1.0")
    from gi.repository import Gst, GstPbutils
except (ImportError, ValueError):
    Gst = GstPbutils = None

from . import (MAX_IMAGE_PREVIEW, MAX_PREVIEW_CONTENT, MAX_PREVIEW_MEDIA,
               MAX_TEXT_PREVIEW, MIN_PREVIEW_MEDIA, _, ngettext, thumbs)
from .util import is_text_like, launch_default


# How long the media box stays empty before the file's icon stands in for
# content that is still being read (milliseconds).
PLACEHOLDER_DELAY = 150
# PDFs and video frames are rendered by poppler-utils / ffmpegthumbnailer in
# a separate process: a broken file cannot take the browser down with it,
# and a slow one is given up on (seconds).
TOOL_TIMEOUT = 10
_gst_lock = threading.Lock()


def _run(args):
    """Output of a poppler-utils command, or None when it is missing, fails
    or takes too long."""
    try:
        done = subprocess.run(args, capture_output=True, timeout=TOOL_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _pdf_pages(path):
    out = _run(["pdfinfo", path])
    for line in (out or b"").decode("utf-8", errors="replace").splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1])
            except ValueError:
                return None
    return None


def _png_pixbuf(png):
    if not png:
        return None
    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(png)
        loader.close()
    except GLib.Error:
        return None
    return loader.get_pixbuf()


_BOUND = max(MAX_PREVIEW_CONTENT, MAX_PREVIEW_MEDIA)    # longer side of a rendering


def _pdf_first_page(path):
    """The first page rendered to a pixbuf, or None."""
    return _png_pixbuf(_run(["pdftoppm", "-png", "-f", "1", "-l", "1",
                             "-scale-to", str(_BOUND), path]))


def _video_frame(path):
    """A frame from 10% into the video (past most title cards), or None."""
    return _png_pixbuf(_run(["ffmpegthumbnailer", "-i", path, "-o", "/dev/stdout",
                             "-c", "png", "-s", str(_BOUND), "-t", "10%"]))


def _video_info(gfile):
    """(duration in seconds, (width, height)) of a video, each None when
    unknown, from a GStreamer discovery."""
    if GstPbutils is None:
        return None, None
    with _gst_lock:
        if not Gst.is_initialized():
            Gst.init(None)
    try:
        info = GstPbutils.Discoverer.new(5 * Gst.SECOND).discover_uri(gfile.get_uri())
    except GLib.Error:
        return None, None
    duration = info.get_duration()
    streams = info.get_video_streams()
    return ((duration // Gst.SECOND if 0 < duration != Gst.CLOCK_TIME_NONE else None),
            (streams[0].get_width(), streams[0].get_height()) if streams else None)


# The facts the worker reads for each kind of file (keys of a `meta` dict,
# see thumbs), and how it gets them and the picture.
_FACTS = {"image": ("dims",), "pdf": ("pages",), "video": ("duration", "dims")}


def _facts(kind, gfile, path):
    meta = {}
    if kind == "image":
        _fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(path)
        if width > 0 and height > 0:
            meta["dims"] = (width, height)
    elif kind == "pdf":
        pages = _pdf_pages(path)
        if pages:
            meta["pages"] = pages
    elif kind == "video":
        duration, dims = _video_info(gfile)
        if duration is not None:
            meta["duration"] = duration
        if dims:
            meta["dims"] = dims
    return meta


def _picture(kind, path, size, meta):
    """A rendering of the file bounded to the largest media box, or None."""
    if kind == "image":
        if size > MAX_IMAGE_PREVIEW:
            return None
        dims = meta.get("dims")
        if dims and dims[0] <= MAX_PREVIEW_CONTENT and dims[1] <= MAX_PREVIEW_MEDIA:
            return GdkPixbuf.Pixbuf.new_from_file(path)
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(
            path, MAX_PREVIEW_CONTENT, MAX_PREVIEW_MEDIA, True)
    if kind == "pdf":
        return _pdf_first_page(path)
    if kind == "video":
        return _video_frame(path)
    return None


def _format_duration(seconds):
    hours, rest = divmod(int(round(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return "%d:%02d:%02d" % (hours, minutes, secs) if hours else "%d:%02d" % (minutes, secs)


class Preview(Gtk.Box):
    """Right-most pane shown when a file (not a directory) is selected."""

    def __init__(self, width):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.gfile = None       # the previewed file, or None for a selection
        self.gfiles = []        # what the Open button opens
        # The previewed picture, decoded once on a worker thread and bounded
        # to the largest media box; re-fitting it on resize is in-memory.
        self._pixbuf = None
        self._rows = []         # the details rows, (label, value)
        self._icon = None       # the shown file's icon, the fallback content
        self._standin = None    # a small cached thumbnail, if one turned up
        self._showing = None    # None (box empty), "placeholder" or "content"
        self._gen = 0           # bumped whenever what is shown changes
        # The pane takes whatever room is left in the strip. Its content is
        # `width` wide at least, grows with the pane up to MAX_PREVIEW_CONTENT,
        # and sits centred once the pane is wider than that. Likewise the
        # media box (picture / text) takes what is left of the pane's height
        # once the name, details and button have theirs, between
        # MIN_PREVIEW_MEDIA and MAX_PREVIEW_MEDIA.
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                               margin=12, halign=Gtk.Align.CENTER)
        super().pack_start(self.content, True, True, 0)
        self._content_width = 0
        self._media_height = MIN_PREVIEW_MEDIA
        self._height = 0        # the pane's height, told or allocated

        self.media = Gtk.Box(valign=Gtk.Align.CENTER)
        self.media.set_size_request(-1, MIN_PREVIEW_MEDIA)
        self.pack_start(self.media, False, False, 0)

        self.name = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                              justify=Gtk.Justification.CENTER, selectable=True)
        self.name.get_style_context().add_class("title-3")
        self.pack_start(self.name, False, False, 0)

        self.details = Gtk.Grid(row_spacing=4, column_spacing=12,
                                halign=Gtk.Align.CENTER)
        self.pack_start(self.details, False, False, 0)

        self.open_btn = Gtk.Button(label=_("Open"), halign=Gtk.Align.CENTER)
        self.open_btn.connect("clicked", self._on_open)
        self.pack_start(self.open_btn, False, False, 6)

        self.set_width(width)
        self.connect("size-allocate", self._on_size_allocate)
        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, *_args):
        self._gen += 1          # results still on their way are dropped

    def pack_start(self, child, expand, fill, padding):
        self.content.pack_start(child, expand, fill, padding)

    # The pane's own height request must not follow the media box, or the
    # window could never shrink again once the box has grown into it: report
    # the height of a pane with the box at its minimum instead.
    def do_get_preferred_height(self):
        height = self._others_height(self._content_width) + MIN_PREVIEW_MEDIA
        return height, height

    def do_get_preferred_height_for_width(self, _width):
        return self.do_get_preferred_height()

    def _others_height(self, width):
        """Height of everything in the content but the media box, laid out
        `width` wide: name, details, button, spacing and margins."""
        box = self.content
        total = box.get_margin_top() + box.get_margin_bottom()
        others = [c for c in box.get_children() if c is not self.media]
        for child in others:
            total += child.get_preferred_height_for_width(max(width, 1))[1]
            total += 2 * box.query_child_packing(child)[2]      # padding
        return total + box.get_spacing() * len(others)

    def set_width(self, width):
        """Minimum width of the pane (and of its content)."""
        self._min_width = width
        self.set_size_request(width, -1)
        self._available = width
        self._refit()

    def set_available(self, width, defer=True):
        """Room the strip has left for the pane; the content grows into it up
        to MAX_PREVIEW_CONTENT. (The pane's own allocation cannot be used for
        this: inside the horizontal scroller it is always granted its
        request, so the request would only ever feed back into itself.)
        Called from the strip's size-allocate, so the layout is applied once
        that pass is over unless `defer` is False."""
        self._available = width
        self._refit(defer)

    def set_height(self, height):
        """Height the pane is going to get. Telling it before the first show
        lets the content come up at its final size instead of growing into
        the pane a moment later."""
        self._height = height
        self._refit(defer=False)

    def _on_size_allocate(self, _widget, alloc):
        self._height = alloc.height
        self._refit(defer=True)

    def _refit(self, defer=False):
        margins = self.content.get_margin_start() + self.content.get_margin_end()
        width = min(max(self._available, self._min_width), MAX_PREVIEW_CONTENT) - margins
        # The media box gets what is left of the pane's height once the name,
        # details and button have their share, within its bounds.
        spare = self._height - self._others_height(width)
        media = max(MIN_PREVIEW_MEDIA, min(MAX_PREVIEW_MEDIA, spare))
        if (width, media) == (self._content_width, self._media_height):
            return
        self._content_width, self._media_height = width, media
        if defer:
            # Changing size requests from inside size-allocate is not
            # allowed; apply once the current layout pass is over.
            GLib.idle_add(self._apply_layout)
        else:
            self._apply_layout()

    def _apply_layout(self):
        self.content.set_size_request(self._content_width, -1)
        self.media.set_size_request(-1, self._media_height)
        if self._pixbuf is not None:
            self._show_media(self._image_widget())
        return False

    def _image_widget(self, pixbuf=None):
        """The picture (by default the previewed one) fitted into the media
        box, keeping its aspect ratio and never blown up past its own size."""
        pb = pixbuf or self._pixbuf
        w, h = max(self._content_width, 1), max(self._media_height, 1)
        scale = min(w / pb.get_width(), h / pb.get_height(), 1.0)
        tw = max(1, round(pb.get_width() * scale))
        th = max(1, round(pb.get_height() * scale))
        if (tw, th) != (pb.get_width(), pb.get_height()):
            pb = pb.scale_simple(tw, th, GdkPixbuf.InterpType.BILINEAR)
        return Gtk.Image.new_from_pixbuf(pb)

    def _show_media(self, widget):
        for child in self.media.get_children():
            self.media.remove(child)
        if widget is None:
            return
        widget.set_halign(Gtk.Align.FILL if isinstance(widget, Gtk.ScrolledWindow)
                          else Gtk.Align.CENTER)
        widget.show_all()
        self.media.pack_start(widget, True, True, 0)

    def _on_open(self, *_args):
        for gfile in self.gfiles:
            launch_default(gfile, self)

    def set_file(self, gfile, info):
        """Show `gfile`: what the directory listing already knows at once,
        and whatever has to be read from the file (pixel size, picture, text)
        as soon as a worker thread has it, so slow media never stall the
        window."""
        self.gfile = gfile
        self.gfiles = [gfile]
        self._pixbuf = None
        self._gen += 1

        ctype = info.get_content_type() or ""
        size = info.get_size()
        path = gfile.get_path()
        # What the worker thread reads for this kind of file, and the details
        # rows only it can fill in (shown as "…" until then).
        kind, pending = None, []
        if path and ctype.startswith("image/"):
            kind, pending = "image", [_("Dimensions")]
        elif path and ctype == "application/pdf":
            kind, pending = "pdf", [_("Pages")]
        elif path and ctype.startswith("video/"):
            kind, pending = "video", [_("Duration"), _("Dimensions")]
        elif is_text_like(ctype):
            kind = "text"
        self.name.set_text(info.get_display_name() or info.get_name())
        rows = [(_("Kind"), Gio.content_type_get_description(ctype) or ctype)]
        rows.extend((label, "…") for label in pending)
        if info.get_file_type() != Gio.FileType.DIRECTORY:   # a folder's size says nothing
            rows.append((_("Size"), GLib.format_size(size)))
        mtime = info.get_modification_date_time()
        if mtime is not None:
            rows.append((_("Modified"), mtime.to_local().format("%Y-%m-%d %H:%M")))
        target = (info.get_symlink_target()
                  if info.has_attribute("standard::symlink-target") else None)
        if target:
            rows.append((_("Links to"), target))
        self._set_details(rows)

        self.open_btn.set_sensitive(True)
        self._icon = info.get_icon()
        self._standin, self._showing = None, None
        if kind is not None:
            # The box stays empty while the content is read; it usually
            # arrives within a frame or two from a local disk, and anything
            # shown in between (the icon, a small cached thumbnail) would
            # only flash. Slow media get a placeholder after a moment.
            self._show_media(None)
            GLib.timeout_add(PLACEHOLDER_DELAY, self._show_placeholder, self._gen)
            mtime_s = info.get_attribute_uint64("time::modified")
            threading.Thread(target=self._read, daemon=True,
                             args=(self._gen, gfile, path, kind, size, pending, mtime_s)).start()
        else:
            self._show_placeholder(self._gen)   # no preview for this kind of file

    def _show_placeholder(self, gen):
        """Something for the media box while there is no content: a small
        cached thumbnail at its own size if one has turned up, else the
        file's icon. Nothing once content is there or another file is shown."""
        if gen != self._gen or self._showing == "content":
            return False
        if self._standin is not None:
            self._show_media(self._image_widget(self._standin))
        elif self._showing is None:
            icon = Gtk.Image.new_from_gicon(self._icon, Gtk.IconSize.DIALOG)
            icon.set_pixel_size(96)
            self._show_media(icon)
        self._showing = "placeholder"
        return False

    def _read(self, gen, gfile, path, kind, size, pending, mtime):
        """Worker thread: the first bytes of a text file, or a rendering of
        the file (a bounded copy of a picture, the first page of a PDF, a
        frame of a video) with the facts that go with it (pixel size, pages,
        duration), from the caches when they have it. Delivers (meta,
        pixbuf, text)."""
        meta, pixbuf, text = {}, None, None
        try:
            if kind == "text":
                stream = gfile.read(None)
                data = stream.read_bytes(MAX_TEXT_PREVIEW, None).get_data()
                stream.close(None)
                text = data.decode("utf-8", errors="replace")
            else:
                meta, pixbuf = self._rendering(gen, gfile, path, kind, size, mtime)
        except GLib.Error:
            pass
        GLib.idle_add(self._on_read, gen, pending, meta, pixbuf, text)

    def _rendering(self, gen, gfile, path, kind, size, mtime):
        key = thumbs.stamp(gfile, mtime, size)
        hit = thumbs.memory_get(key)
        if hit is not None:
            return hit[1], hit[0]
        uri = gfile.get_uri()
        cached = thumbs.disk_lookup(uri, mtime)
        meta, pixbuf, standin = {}, None, None
        if cached is not None:
            standin, meta, final = cached
            if final:
                pixbuf = standin
            else:
                # Nemo's small thumbnail is shown while the rendering is made.
                GLib.idle_add(self._on_standin, gen, standin)
        if any(fact not in meta for fact in _FACTS[kind]):
            meta.update(_facts(kind, gfile, path))
        rendered = False
        if pixbuf is None:
            pixbuf = _picture(kind, path, size, meta)
            rendered = pixbuf is not None
            if pixbuf is None:
                pixbuf = standin            # better than the icon
        if pixbuf is not None:
            thumbs.memory_put(key, pixbuf, meta)
            if rendered and kind in ("pdf", "video"):
                thumbs.disk_save(uri, mtime, size, pixbuf, meta)
        return meta, pixbuf

    def _on_standin(self, gen, pixbuf):
        """A small cached thumbnail was found: it becomes the placeholder,
        replacing the icon if that is already up; otherwise it waits for the
        placeholder moment (and is never shown if the content beats it)."""
        if gen == self._gen and self._showing != "content":
            self._standin = pixbuf
            if self._showing == "placeholder":
                self._show_placeholder(gen)
        return False

    def _on_read(self, gen, pending, meta, pixbuf, text):
        if gen != self._gen:
            return False            # another file (or nothing) is shown now
        values = {}
        if "dims" in meta:
            # TRANSLATORS: image size in pixels, e.g. "1920 × 1080"
            values[_("Dimensions")] = _("%d × %d") % meta["dims"]
        if "pages" in meta:
            values[_("Pages")] = ngettext("%d page", "%d pages", meta["pages"]) % meta["pages"]
        if "duration" in meta:
            values[_("Duration")] = _format_duration(meta["duration"])
        if pending:
            # Fill in the rows that were waiting; drop those with no value.
            rows = [(k, values[k]) if k in pending else (k, v)
                    for k, v in self._rows if k not in pending or k in values]
            self._set_details(rows)
        if pixbuf is not None:
            self._pixbuf = pixbuf
            self._showing = "content"
            self._show_media(self._image_widget())
        elif text is not None:
            self._showing = "content"
            self._show_media(self._text_widget(text))
        else:
            self._show_placeholder(gen)     # nothing to show: the placeholder stays
        return False

    def set_files(self, gfiles, infos):
        """Summary of several selected items: their icons, how many folders
        and files, the total size and the first few names."""
        self.gfile = None
        self._pixbuf = None
        self._gen += 1
        is_dir = [i.get_file_type() == Gio.FileType.DIRECTORY for i in infos]
        self.gfiles = [g for g, d in zip(gfiles, is_dir) if not d]
        n, dirs = len(gfiles), sum(is_dir)
        files = n - dirs
        self.name.set_text(ngettext("%d item", "%d items", n) % n)
        kinds = []
        if dirs:
            kinds.append(ngettext("%d folder", "%d folders", dirs) % dirs)
        if files:
            kinds.append(ngettext("%d file", "%d files", files) % files)
        rows = [(_("Kind"), _(", ").join(kinds))]
        if files:
            total = sum(i.get_size() for i, d in zip(infos, is_dir) if not d)
            rows.append((_("Size"), GLib.format_size(total)))
        names = [i.get_display_name() or i.get_name() for i in infos[:8]]
        if n > len(names):
            names.append(_("and %d more…") % (n - len(names)))
        rows.append((_("Items"), "\n".join(names)))
        self._set_details(rows)

        grid = Gtk.Grid(row_spacing=6, column_spacing=6,
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        per_row = 3 if n > 4 else 2
        for k, info in enumerate(infos[:9]):
            img = Gtk.Image.new_from_gicon(info.get_icon(), Gtk.IconSize.DIALOG)
            img.set_pixel_size(48)
            grid.attach(img, k % per_row, k // per_row, 1, 1)
        self._show_media(grid)
        self.open_btn.set_sensitive(bool(self.gfiles))

    def _set_details(self, rows):
        self._rows = list(rows)
        for child in self.details.get_children():
            self.details.remove(child)
        for i, (k, v) in enumerate(rows):
            kl = Gtk.Label(label=k, xalign=1.0, yalign=0.0)
            kl.get_style_context().add_class("dim-label")
            vl = Gtk.Label(label=v, xalign=0.0, wrap=True, selectable=True,
                           max_width_chars=28)
            self.details.attach(kl, 0, i, 1, 1)
            self.details.attach(vl, 1, i, 1, 1)
        # The name and details are in place (and shown, or they would report
        # no size): now the media box can be sized for the room they leave,
        # and a picture scaled to it in one go.
        self.show_all()
        self._refit(defer=False)

    def _text_widget(self, text):
        tv = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                          left_margin=6, right_margin=6, top_margin=6)
        tv.set_wrap_mode(Gtk.WrapMode.NONE)
        tv.get_buffer().set_text(text)
        sw = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN)   # fills the media box
        sw.add(tv)
        return sw
