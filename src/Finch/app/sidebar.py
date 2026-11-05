"""GtkPlacesSidebar with Finder-style section titles and device usage bars.

The sidebar's rows are a private GTK row class; the only things relied on
here are its "section-type" property (GtkPlacesSectionType, 1..6) and the
"uri" property of device rows.
"""
from gi.repository import Gio, GLib, Gtk, Pango

from . import _

SECTIONS = {1: _("Places"), 2: _("Devices"), 3: _("Cloud"),
            4: _("Bookmarks"), 5: _("Network"), 6: _("Other")}
SECTION_MOUNTS = 2


def _find(widget, cls):
    if isinstance(widget, cls):
        return widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            hit = _find(child, cls)
            if hit is not None:
                return hit
    return None


class PlacesSidebar(Gtk.PlacesSidebar):
    def __init__(self):
        super().__init__()
        self.set_show_desktop(True)
        self._location = None
        self._sync_id = 0
        self._align_id = 0
        # GtkPlacesSidebar only separates its sections with a line; give each
        # one a heading, and every mounted device a thin usage bar.
        self._list = _find(self, Gtk.ListBox)
        if self._list is not None:
            self._list.set_header_func(self._header_func, None)
            # Rows are rebuilt whenever mounts or bookmarks change, so hook
            # every row as it is added.
            self._list.connect("add", self._on_row_added)
            for row in self._list.get_children():
                self._decorate_device_row(row)

    # -- location ------------------------------------------------------------
    def sync_location(self, gfile):
        """Highlight the row matching `gfile` (or nothing), now and whenever
        the rows are rebuilt later."""
        self._location = gfile
        self._apply_location()

    def _apply_location(self):
        self._sync_id = 0
        if self._location is None:
            return False
        try:
            self.set_location(self._location)
        except Exception:  # noqa: BLE001 - sidebar may reject remote URIs
            pass
        if self.get_location() is None and self._list is not None:
            self._list.unselect_all()
        return False

    def _on_row_added(self, listbox, row):
        self._decorate_device_row(row)
        if not self._sync_id:
            self._sync_id = GLib.idle_add(self._apply_location)

    # -- section headings ----------------------------------------------------
    def _header_func(self, row, before, _data):
        try:
            section = row.get_property("section-type")
            prev = before.get_property("section-type") if before is not None else None
        except TypeError:           # not the private row class after all
            row.set_header(None)
            return
        if section == prev or section not in SECTIONS:
            row.set_header(None)
            return
        label = Gtk.Label(label=SECTIONS[section], xalign=0.0,
                          margin_start=14, margin_end=8, margin_bottom=2,
                          margin_top=6 if before is None else 14)
        label.get_style_context().add_class("dim-label")
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
        attrs.insert(Pango.attr_scale_new(0.82))
        label.set_attributes(attrs)
        label.show()
        row.set_header(label)

    # -- usage bars ----------------------------------------------------------
    def _decorate_device_row(self, row):
        """Add a thin space-usage bar under the name of a mounted device."""
        try:
            if row.get_property("section-type") != SECTION_MOUNTS:
                return
            uri = row.get_property("uri")
        except TypeError:
            return
        if not uri or not uri.startswith("file://") or getattr(row, "_usage_bar", None):
            return
        label = _find(row, Gtk.Label)
        if label is None:
            return
        hbox = label.get_parent()
        index = hbox.get_children().index(label)
        bar = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True, valign=Gtk.Align.CENTER)
        bar.get_style_context().add_class("usage-bar")
        bar.set_no_show_all(True)        # shown once the numbers arrive
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                       hexpand=True, valign=Gtk.Align.CENTER)
        hbox.remove(label)
        vbox.pack_start(label, False, False, 0)
        vbox.pack_start(bar, False, False, 0)
        vbox.show()
        hbox.pack_start(vbox, True, True, 0)
        hbox.reorder_child(vbox, index)
        row._usage_bar = bar
        row._usage_vbox = vbox
        row._eject_btn = next((c for c in hbox.get_children() if isinstance(c, Gtk.Button)), None)
        bar._uri = uri
        self._refresh_usage_bar(bar)
        if not self._align_id:
            self._align_id = GLib.idle_add(self._align_usage_bars)

    def _align_usage_bars(self):
        """Give rows without an eject button the same right margin as rows
        with one, so every usage bar ends at the same x."""
        self._align_id = 0
        if self._list is None:
            return False
        rows = [r for r in self._list.get_children() if getattr(r, "_usage_vbox", None)]
        reserve = 0
        for row in rows:
            btn = row._eject_btn
            if btn is not None and btn.get_visible():
                width = btn.get_preferred_width()[1] + btn.get_margin_start() + btn.get_margin_end()
                reserve = max(reserve, width + btn.get_parent().get_spacing())
        for row in rows:
            btn = row._eject_btn
            hidden = btn is None or not btn.get_visible()
            row._usage_vbox.set_margin_end(reserve if hidden else 0)
        return False

    def refresh_usage(self):
        """Re-query the space usage of every device row."""
        if self._list is None:
            return
        for row in self._list.get_children():
            bar = getattr(row, "_usage_bar", None)
            if bar is not None:
                self._refresh_usage_bar(bar)

    def _refresh_usage_bar(self, bar):
        Gio.File.new_for_uri(bar._uri).query_filesystem_info_async(
            "filesystem::size,filesystem::used", GLib.PRIORITY_LOW, None,
            self._on_usage_info, bar)

    @staticmethod
    def _on_usage_info(gfile, result, bar):
        try:
            info = gfile.query_filesystem_info_finish(result)
        except GLib.Error:
            return
        size = info.get_attribute_uint64("filesystem::size")
        used = info.get_attribute_uint64("filesystem::used")
        if not size:
            return
        frac = used / size
        bar.set_value(frac)
        if frac >= 0.9:
            bar.get_style_context().add_class("warn")
        else:
            bar.get_style_context().remove_class("warn")
        # TRANSLATORS: device usage, e.g. "12.3 GB used of 60.0 GB (21%)"
        bar.set_tooltip_text(_("%(used)s used of %(size)s (%(percent).0f%%)") % {
            "used": GLib.format_size(used), "size": GLib.format_size(size),
            "percent": frac * 100})
        bar.show()
