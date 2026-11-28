"""One Miller column: a directory as a list, with a drag handle on its right
edge. What a listing does regardless of its look is in listing.py."""
import cairo
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from . import C_FILE, C_ICON, C_INFO, C_ISDIR, C_NAME, MIN_COL_WIDTH, apk, thumbs
from .grid import ICON_SIZE as GRID_ICON_SIZE
from .listing import (DND_TARGETS, SORT_MODES, Listing, _dragging,  # noqa: F401 (re-exported)
                      drop_action)
from .util import shown_file

# The drag handle on a column's right edge: a column takes its width plus this.
HANDLE_WIDTH = 8
ROW_ICON = 16               # what Gtk.IconSize.MENU, the size of a row's icon, comes to


class ListView(Gtk.TreeView):
    """TreeView that does not select the first row when it merely gains focus.

    GtkTreeView's grab_focus implementation moves the cursor to the first row
    (selecting it in SINGLE mode) whenever no cursor is set, so a plain click
    on the blank area of a column would select its first entry."""

    def do_grab_focus(self):
        Gtk.Widget.do_grab_focus(self)


class Column(Listing):
    """One directory listing plus a drag handle on its right edge."""

    def __init__(self, gfile, width, show_hidden, sort_by="name", sort_reversed=False):
        self._width = width
        # Pictures, videos and Android packages show themselves in place of
        # the file type's icon: the icon grid's thumbnails, scaled down, made
        # in the background for the rows in view. What the rows show by now,
        # {file name: (mtime, size, pixbuf)}, to put back when the list is
        # rebuilt.
        self._thumbs = None
        self._pictures = {}
        self._pictures_id = 0
        super().__init__(gfile, show_hidden, sort_by, sort_reversed)
        thumbs.watch(self._queue_pictures)

        # resize handle
        self.handle = Gtk.EventBox()
        self.handle.set_size_request(HANDLE_WIDTH, -1)
        self.handle.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                               | Gdk.EventMask.BUTTON_RELEASE_MASK
                               | Gdk.EventMask.POINTER_MOTION_MASK)
        self.handle.add(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL,
                                      halign=Gtk.Align.CENTER))
        self.handle.connect("realize", self._on_handle_realize)
        # Track the pointer in root coordinates: the handle itself moves as
        # the column grows, so widget-relative offsets would oscillate.
        self._drag_origin = None
        self.handle.connect("button-press-event", self._on_handle_press)
        self.handle.connect("motion-notify-event", self._on_handle_motion)
        self.handle.connect("button-release-event", self._on_handle_release)
        self.pack_start(self.handle, False, False, 0)

    # -- the files' own pictures as row icons -----------------------------------
    def _rows_changed(self):
        """The store was rebuilt: rows that had their picture get it back at
        once (a reload must not flash the file type's icons), the others
        when they come into view."""
        super()._rows_changed()
        had, self._pictures = self._pictures, {}
        if had:
            store = self.store
            for it in _iters(store):
                info = store.get_value(it, C_INFO)
                name = info.get_name()
                picture = had.get(name)
                if picture is not None and picture[:2] == _version(info):
                    store.set_value(it, C_ICON, picture[2])
                    self._pictures[name] = picture
        self._queue_pictures()

    def _queue_pictures(self, *_args):
        """Look at the rows in view once the main loop is idle. Also the
        view's "draw" handler: whatever brings rows into view draws them."""
        if not self._pictures_id:
            self._pictures_id = GLib.idle_add(self._show_pictures)
        return False

    def _show_pictures(self):
        """Give the rows in view their picture, asking the worker thread for
        the ones not made yet (and only for those: scrolling on drops the
        requests for rows left behind)."""
        self._pictures_id = 0
        view, store = self.view, self.store
        span = view.get_visible_range() if view.get_realized() else None
        requests = []
        if span is not None:
            first, last = span[0].get_indices()[0], span[1].get_indices()[0]
            for i in range(first, min(last, len(store) - 1) + 1):
                row = store[i]
                if isinstance(row[C_ICON], GdkPixbuf.Pixbuf):
                    continue
                info = row[C_INFO]
                ctype = info.get_content_type() or ""
                if not (ctype.startswith(("image/", "video/")) or ctype == apk.CONTENT_TYPE):
                    continue
                gfile = shown_file(row[C_FILE], info)    # in Recent: the file itself
                mtime, size = _version(info)
                key = thumbs.stamp(gfile, mtime, size)
                known, pixbuf = thumbs.thumb_get(key)
                if not known:
                    requests.append((key, gfile, gfile.get_path(), ctype, mtime, size))
                elif pixbuf is not None:
                    # A pixbuf is a GIcon, which the renderer shows at its
                    # own size.
                    side = ROW_ICON * view.get_scale_factor()
                    icon = _squared(thumbs._fit(pixbuf, side), side)
                    row[C_ICON] = icon
                    self._pictures[info.get_name()] = (mtime, size, icon)
        if requests and self._thumbs is None:
            # The grid's size, so that both looks share the pictures.
            self._thumbs = thumbs.Thumbnailer(GRID_ICON_SIZE * view.get_scale_factor(),
                                              self._queue_pictures)
        if self._thumbs is not None:
            self._thumbs.want(requests)
        return False

    def _on_destroy(self, *args):
        thumbs.unwatch(self._queue_pictures)
        if self._pictures_id:
            GLib.source_remove(self._pictures_id)
            self._pictures_id = 0
        if self._thumbs is not None:
            self._thumbs.stop()
        super()._on_destroy(*args)

    def _make_view(self):
        view = ListView(model=self.store, headers_visible=False,
                        enable_search=True, search_column=C_NAME)
        view.set_enable_tree_lines(False)
        # Several rows may be selected (Ctrl / Shift click, Shift + arrows,
        # Ctrl+A); a single selection drives the navigation, a wider one is
        # just a set of items to act on. No rubber banding: with it GtkTreeView
        # turns a drag that starts on an unselected row into a rubber band
        # instead of dragging the row.
        sel = view.get_selection()
        sel.set_mode(Gtk.SelectionMode.MULTIPLE)
        sel.connect("changed", lambda _sel: self._emit_selection())

        col = Gtk.TreeViewColumn()
        icon = Gtk.CellRendererPixbuf(stock_size=Gtk.IconSize.MENU, xpad=4)
        name = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
        chev = Gtk.CellRendererText(text="›", xalign=1.0, xpad=6)
        chev.set_property("foreground-rgba", Gdk.RGBA(0.5, 0.5, 0.5, 1))
        col.pack_start(icon, False)
        col.pack_start(name, True)
        col.pack_end(chev, False)
        col.add_attribute(icon, "gicon", C_ICON)
        col.add_attribute(name, "text", C_NAME)
        col.add_attribute(chev, "visible", C_ISDIR)
        view.append_column(col)
        self.name_col = col
        self.name_renderer = name
        name.connect("editing-started", self._on_editing_started)
        name.connect("edited", self._on_name_edited)
        name.connect("editing-canceled", self._on_editing_canceled)

        view.connect("row-activated", self._on_row_activated)
        view.connect("draw", self._queue_pictures)
        view.connect("button-press-event", self._on_button_press)
        view.connect("button-release-event", self._on_button_release)
        view.connect("motion-notify-event", self._on_motion)
        # A plain press on one of several selected rows, held back until it
        # turns out to be a click or a drag (see _on_button_press).
        self._press = None
        self._drag_row = None
        # Single rows are dragged by GtkTreeView's own model drag source;
        # the generic drag signals are wired by Listing.
        view.enable_model_drag_source(
            Gdk.ModifierType.BUTTON1_MASK, DND_TARGETS,
            Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        view.connect_after("drag-begin", self._on_drag_begin_icon)
        # GtkTreeView's own model-row DnD is not used for drops (it would
        # insert and delete rows itself); the view's handlers are kept out
        # by Listing's, which stop the emission.

        self.sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                     vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.sw.add(view)
        self.sw.set_size_request(self._width, -1)
        return view

    def set_width(self, width):
        self.sw.set_size_request(width, -1)

    # -- view hooks ------------------------------------------------------------
    @staticmethod
    def _path(handle):
        return Gtk.TreePath.new_from_indices([handle]) if isinstance(handle, int) else handle

    def _selected_handles(self):
        return self.view.get_selection().get_selected_rows()[1]

    def _is_selected(self, handle):
        return self.view.get_selection().path_is_selected(self._path(handle))

    def _select_only(self, handle):
        self.view.set_cursor(self._path(handle), None, False)

    def _select_add(self, handle):
        self.view.get_selection().select_path(self._path(handle))

    def _unselect_all(self):
        self.view.get_selection().unselect_all()

    def _reveal(self, handle):
        self.view.scroll_to_cell(self._path(handle), None, False, 0, 0)

    def _item_at(self, x, y):
        bx, by = self.view.convert_widget_to_bin_window_coords(int(x), int(y))
        hit = self.view.get_path_at_pos(bx, by)
        return hit[0] if hit is not None else None

    def _highlight_drop(self, handle):
        view = self.view
        view.set_drag_dest_row(handle, Gtk.TreeViewDropPosition.INTO_OR_BEFORE)
        ctx = view.get_style_context()
        if handle is None:
            ctx.add_class("drop-target")
        else:
            ctx.remove_class("drop-target")

    def _clear_drop(self):
        self.view.set_drag_dest_row(None, Gtk.TreeViewDropPosition.INTO_OR_BEFORE)
        self.view.get_style_context().remove_class("drop-target")

    def _view_key(self, event):
        key = event.keyval
        if key in (Gdk.KEY_Down, Gdk.KEY_Up, Gdk.KEY_Home, Gdk.KEY_End,
                   Gdk.KEY_Page_Down, Gdk.KEY_Page_Up) and len(self.store):
            # No cursor yet (focus arrived without selecting anything):
            # start keyboard navigation from the first entry.
            if self.view.get_cursor()[0] is None:
                self.select_first()
                return True
        if key == Gdk.KEY_Left:
            self.emit("key-nav", "left")
            return True
        if key == Gdk.KEY_Right:
            self.emit("key-nav", "right")
            return True
        return False

    # -- rename --------------------------------------------------------------
    def start_rename(self):
        model, paths = self.view.get_selection().get_selected_rows()
        if len(paths) != 1 or self.in_trash():
            return
        self._renaming = True
        self.name_renderer.set_property("editable", True)
        # Without an explicit focus cell the cell area may try to activate
        # the (inert) icon or chevron renderer and silently do nothing.
        self.name_col.focus_cell(self.name_renderer)
        self.view.set_cursor(paths[0], self.name_col, True)

    def _on_editing_started(self, renderer, editable, path):
        row = self.store[path]
        if row[C_ISDIR] or not isinstance(editable, Gtk.Entry):
            return
        self._preselect_stem(editable, row[C_NAME])

    def _on_editing_canceled(self, renderer):
        self._renaming = False
        self.name_renderer.set_property("editable", False)

    def _on_name_edited(self, renderer, path, new_name):
        self._renaming = False
        self.name_renderer.set_property("editable", False)
        self._commit_rename(path, new_name)

    # -- events --------------------------------------------------------------
    def _on_row_activated(self, view, path, column):
        row = self.store[path]
        self.emit("item-activated", row[C_FILE], row[C_INFO])

    def _on_button_press(self, view, event):
        hit = view.get_path_at_pos(int(event.x), int(event.y))
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            # GtkTreeView selects on press, so a plain press on one of several
            # selected rows would collapse the selection before it could be
            # dragged as a whole. Hold such a press back: a release makes it
            # a click (select that row alone), motion makes it a drag.
            mods = event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
            sel = view.get_selection()
            if (hit is not None and not mods and sel.count_selected_rows() > 1
                    and sel.path_is_selected(hit[0])):
                self._press = (hit[0], event.x, event.y)
                return True
            return False
        if event.button == 2:
            self._press_middle(hit[0] if hit is not None else None)
            return True
        if event.button != 3:
            return False
        self._press_context(hit[0] if hit is not None else None, event)
        return True

    def _on_button_release(self, view, event):
        if self._press is None or event.button != 1:
            return False
        path, _x, _y = self._press
        self._press = None
        view.set_cursor(path, None, False)       # it was a click after all
        return True

    def _on_motion(self, view, event):
        if self._press is None:
            return False
        path, x0, y0 = self._press
        if view.drag_check_threshold(int(x0), int(y0), int(event.x), int(event.y)):
            self._press = None
            self._drag_row, self._drag_x, self._drag_y = path, x0, y0
            view.drag_begin_with_coordinates(
                Gtk.TargetList.new(DND_TARGETS),
                Gdk.DragAction.MOVE | Gdk.DragAction.COPY, 1, event,
                int(event.x), int(event.y))
        return True

    def _on_drag_begin_icon(self, view, ctx):
        """Several rows dragged at once: the pressed row as the icon, with
        a badge giving the count. (GtkTreeView draws the single-row icon.)"""
        path = getattr(self, "_drag_row", None)
        self._drag_row = None
        count = len(_dragging)
        if path is None or count < 2:
            return
        surface = view.create_row_drag_icon(path)
        area = view.get_background_area(path, self.name_col)
        width, height = view.get_bin_window().get_width(), area.height
        cr = cairo.Context(surface)
        r = 10
        cx, cy = width - r - 6, height / 2 + 1
        cr.arc(cx, cy, r, 0, 2 * 3.14159)
        cr.set_source_rgb(0.21, 0.52, 0.89)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(11)
        text = str(count)
        ext = cr.text_extents(text)
        cr.move_to(cx - ext.width / 2 - ext.x_bearing, cy - ext.height / 2 - ext.y_bearing)
        cr.show_text(text)
        # Hotspot: where in the row the pointer was when the drag started.
        wx, wy = view.convert_bin_window_to_widget_coords(area.x, area.y)
        surface.set_device_offset(-(self._drag_x - wx) - 1, -(self._drag_y - wy) - 1)
        Gtk.drag_set_icon_surface(ctx, surface)

    # -- the handle ------------------------------------------------------------
    def _on_handle_realize(self, widget):
        cursor = Gdk.Cursor.new_from_name(widget.get_display(), "col-resize")
        widget.get_window().set_cursor(cursor)

    def _on_handle_press(self, widget, event):
        if event.button != 1:
            return False
        self._drag_origin = (event.x_root, self.sw.get_allocated_width())
        return True

    def _on_handle_motion(self, widget, event):
        if self._drag_origin is None:
            return False
        x0, w0 = self._drag_origin
        self.sw.set_size_request(max(MIN_COL_WIDTH, int(w0 + event.x_root - x0)), -1)
        return True

    def _on_handle_release(self, widget, event):
        if event.button != 1 or self._drag_origin is None:
            return False
        self._drag_origin = None
        apply_all = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        self.emit("width-changed", self.sw.get_size_request()[0], apply_all)
        return True


def _squared(pixbuf, side):
    """`pixbuf`, no larger than `side`, in the middle of a transparent
    square: the renderer puts an icon at the top left of the icon's box, so
    a picture that is not square would sit off centre."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if width == side and height == side:
        return pixbuf
    if not pixbuf.get_has_alpha():
        pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
    square = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, side, side)
    square.fill(0)
    pixbuf.copy_area(0, 0, width, height, square, (side - width) // 2, (side - height) // 2)
    return square


def _version(info):
    """(modification time, size): what a picture of the file is good for."""
    return info.get_attribute_uint64("time::modified"), info.get_size()


def _iters(store):
    """Every row of `store` as a Gtk.TreeIter, top to bottom."""
    it = store.get_iter_first()
    while it is not None:
        yield it
        it = store.iter_next(it)
