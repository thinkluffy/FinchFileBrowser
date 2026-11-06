"""The right-most pane: image / text preview and details of the selected file."""
from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Pango

from . import (MAX_IMAGE_PREVIEW, MAX_PREVIEW_CONTENT, MAX_PREVIEW_MEDIA,
               MAX_TEXT_PREVIEW, MIN_PREVIEW_MEDIA, _, ngettext)
from .util import is_text_like, launch_default


class Preview(Gtk.Box):
    """Right-most pane shown when a file (not a directory) is selected."""

    def __init__(self, width):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.gfile = None       # the previewed file, or None for a selection
        self.gfiles = []        # what the Open button opens
        self._image_path = None
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
        if self._image_path:
            self._show_media(self._image_widget(self._image_path))
        return False

    def _image_widget(self, path):
        w, h = max(self._content_width, 1), max(self._media_height, 1)
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, w, h, True)
        except GLib.Error:
            return None
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
        self.gfile = gfile
        self.gfiles = [gfile]
        self._image_path = None

        ctype = info.get_content_type() or ""
        size = info.get_size()
        self.name.set_text(info.get_display_name() or info.get_name())
        rows = [(_("Kind"), Gio.content_type_get_description(ctype) or ctype)]
        path = gfile.get_path()
        if ctype.startswith("image/") and path:
            # Pixel size from the file header only; the image is not decoded.
            _fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(path)
            if width > 0 and height > 0:
                # TRANSLATORS: image size in pixels, e.g. "1920 × 1080"
                rows.append((_("Dimensions"), _("%d × %d") % (width, height)))
        rows.append((_("Size"), GLib.format_size(size)))
        mtime = info.get_modification_date_time()
        if mtime is not None:
            rows.append((_("Modified"), mtime.to_local().format("%Y-%m-%d %H:%M")))
        target = (info.get_symlink_target()
                  if info.has_attribute("standard::symlink-target") else None)
        if target:
            rows.append((_("Links to"), target))
        self._set_details(rows)

        widget = None
        if ctype.startswith("image/") and path and size <= MAX_IMAGE_PREVIEW:
            widget = self._image_widget(path)
            if widget is not None:
                self._image_path = path     # re-scaled when the pane resizes
        elif is_text_like(ctype):
            widget = self._text_preview(gfile)
        if widget is None:
            widget = Gtk.Image.new_from_gicon(info.get_icon(), Gtk.IconSize.DIALOG)
            widget.set_pixel_size(96)
        self._show_media(widget)
        self.open_btn.set_sensitive(True)

    def set_files(self, gfiles, infos):
        """Summary of several selected items: their icons, how many folders
        and files, the total size and the first few names."""
        self.gfile = None
        self._image_path = None
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

    def _text_preview(self, gfile):
        try:
            stream = gfile.read(None)
            data = stream.read_bytes(MAX_TEXT_PREVIEW, None).get_data()
            stream.close(None)
        except GLib.Error:
            return None
        text = data.decode("utf-8", errors="replace")
        tv = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                          left_margin=6, right_margin=6, top_margin=6)
        tv.set_wrap_mode(Gtk.WrapMode.NONE)
        tv.get_buffer().set_text(text)
        sw = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN)   # fills the media box
        sw.add(tv)
        return sw
