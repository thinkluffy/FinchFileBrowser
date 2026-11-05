"""The right-most pane: image / text preview and details of the selected file."""
from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Pango

from . import MAX_IMAGE_PREVIEW, MAX_PREVIEW_CONTENT, MAX_TEXT_PREVIEW, _
from .util import is_text_like, launch_default


class Preview(Gtk.Box):
    """Right-most pane shown when a file (not a directory) is selected."""

    def __init__(self, width):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.gfile = None
        self._image_path = None
        # The pane takes whatever room is left in the strip. Its content is
        # `width` wide at least, grows with the pane up to MAX_PREVIEW_CONTENT,
        # and sits centred once the pane is wider than that.
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                               margin=12, halign=Gtk.Align.CENTER)
        super().pack_start(self.content, True, True, 0)
        self._content_width = 0
        self._image_box = 0
        self.set_width(width)
        self.connect("size-allocate", self._on_size_allocate)

        self.media = Gtk.Box(valign=Gtk.Align.CENTER)
        self.media.set_size_request(-1, 160)
        self.pack_start(self.media, False, False, 0)

        self.name = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                              justify=Gtk.Justification.CENTER, selectable=True)
        self.name.get_style_context().add_class("title-3")
        self.pack_start(self.name, False, False, 0)

        self.details = Gtk.Grid(row_spacing=4, column_spacing=12,
                                halign=Gtk.Align.CENTER)
        self.pack_start(self.details, False, False, 0)

        self.open_btn = Gtk.Button(label=_("Open"), halign=Gtk.Align.CENTER)
        self.open_btn.connect("clicked", lambda *_args: launch_default(self.gfile, self))
        self.pack_start(self.open_btn, False, False, 6)

    def pack_start(self, child, expand, fill, padding):
        self.content.pack_start(child, expand, fill, padding)

    def set_width(self, width):
        """Minimum width of the pane (and of its content)."""
        self._min_width = width
        self.set_size_request(width, -1)
        self._available = width
        self._refit()

    def set_available(self, width):
        """Room the strip has left for the pane; the content grows into it up
        to MAX_PREVIEW_CONTENT. (The pane's own allocation cannot be used for
        this: inside the horizontal scroller it is always granted its
        request, so the request would only ever feed back into itself.)"""
        self._available = width
        self._refit()

    def _on_size_allocate(self, _widget, _alloc):
        self._refit()

    def _refit(self):
        margins = self.content.get_margin_start() + self.content.get_margin_end()
        width = min(max(self._available, self._min_width), MAX_PREVIEW_CONTENT) - margins
        # Pictures are boxed by the content width and by what is left of the
        # pane's height once the name, details and button have their share.
        image_box = max(160, min(width, self.get_allocated_height() - 200))
        if (width, image_box) == (self._content_width, self._image_box):
            return
        self._content_width, self._image_box = width, image_box
        # Changing size requests from inside size-allocate is not allowed;
        # apply once the current layout pass is over.
        GLib.idle_add(self._apply_layout)

    def _apply_layout(self):
        self.content.set_size_request(self._content_width, -1)
        if self._image_path:
            self._show_media(self._image_widget(self._image_path))
        return False

    def _image_widget(self, path):
        w, h = max(self._content_width, 1), max(self._image_box, 1)
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

    def set_file(self, gfile, info):
        self.gfile = gfile
        self._image_path = None
        for child in self.details.get_children():
            self.details.remove(child)

        ctype = info.get_content_type() or ""
        size = info.get_size()
        self.name.set_text(info.get_display_name() or info.get_name())

        widget = None
        path = gfile.get_path()
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

        rows = [(_("Kind"), Gio.content_type_get_description(ctype) or ctype),
                (_("Size"), GLib.format_size(size))]
        mtime = info.get_modification_date_time()
        if mtime is not None:
            rows.append((_("Modified"), mtime.to_local().format("%Y-%m-%d %H:%M")))
        target = (info.get_symlink_target()
                  if info.has_attribute("standard::symlink-target") else None)
        if target:
            rows.append((_("Links to"), target))
        for i, (k, v) in enumerate(rows):
            kl = Gtk.Label(label=k, xalign=1.0)
            kl.get_style_context().add_class("dim-label")
            vl = Gtk.Label(label=v, xalign=0.0, wrap=True, selectable=True,
                           max_width_chars=28)
            self.details.attach(kl, 0, i, 1, 1)
            self.details.attach(vl, 1, i, 1, 1)
        self.show_all()

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
        sw = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN)
        sw.set_size_request(-1, 220)
        sw.add(tv)
        return sw
