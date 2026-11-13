"""Icon view: a directory as a grid of icons with the names beneath, drawn
by hand on a GtkLayout.

GtkIconView lays every item out again on any change (a rename, a resize,
a row gone), which takes seconds for a few thousand files. Here every cell
is the same size, so where a cell is comes from arithmetic, and only the
cells in view are ever measured or drawn: the cost of a frame is the same
for ten files and for thirty thousand. The price is doing the selection,
the keyboard, the rename box and the drag icon ourselves.

Coordinates: events arrive on the layout's bin window, in *content*
coordinates (the scroll offset already added); drags arrive in widget
coordinates. `_item_at` takes the latter, `_item_at_content` the former.
"""
import math
import time

import cairo
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from . import C_FILE, C_INFO, C_ISDIR, C_NAME, archive, thumbs
from .listing import DND_TARGETS, Listing, _dragging

ICON_SIZE = 64
CELL_MIN_WIDTH = 112        # cells widen to share each row evenly
CELL_BOTTOM = 6             # below the name's last line
CELL_INSET = 4              # the dead edge of a cell: not part of the item
MARGIN = 8                  # around the whole grid
ICON_TOP = 8
TEXT_TOP = ICON_TOP + ICON_SIZE + 6
TEXT_LINES = 3              # a name wraps onto this many lines before "…"
TEXT_INSET = 6              # the name's box is this much narrower than the cell
PILL_PAD = 4                # the selected name's background reaches this far out
SEARCH_TIMEOUT = 1.0        # typed letters this close together form one search
# Badges the way Nemo (that is, GTK rendering a GEmblemedIcon) puts them:
# half the icon's size, the first flush with the icon's bottom-right
# corner, the next ones top-right, bottom-left, top-left. Nemo marks files
# it cannot write (padlock), cannot read, and symbolic links; Finch adds
# encrypted archives, with a padlock of their own where the theme has one.
LOCK_EMBLEM = Gio.ThemedIcon.new_from_names(["emblem-locked", "emblem-readonly"])
READONLY_EMBLEM = Gio.ThemedIcon.new("emblem-readonly")
UNREADABLE_EMBLEM = Gio.ThemedIcon.new("emblem-unreadable")
SYMLINK_EMBLEM = Gio.ThemedIcon.new("emblem-symbolic-link")
EMBLEM_SIZE = ICON_SIZE // 2
EMBLEM_CORNERS = ((1, 1), (1, 0), (0, 1), (0, 0))   # (right?, bottom?) per emblem

_icons = {}                 # (icon string, size, scale) -> cairo surface or None


class Grid(Listing):
    def __init__(self, gfile, show_hidden, sort_by="name", sort_reversed=False):
        self.items = []         # one tuple per store row, for drawing without the store
        self._selected = set()  # indices
        self._cursor = None     # index the keyboard moves from
        self._anchor = None     # index a Shift-range starts at
        self._cols = 1
        self._cell_w = CELL_MIN_WIDTH
        self._cell_h = TEXT_TOP + TEXT_LINES * 17 + CELL_BOTTOM   # until the font is measured
        self._width = 0
        # A press on an item, kept until it turns out to be a click or a
        # drag: (index, x, y, was the item already selected?)
        self._press = None
        self._drag_from = None  # (index, x, y) the drag that is starting
        self._band = None       # rubber band: (x0, y0, x1, y1, selection before it)
        self._drop_hit = None   # the folder cell a drop would land in
        self._entry = None      # the rename box while it is up
        self._search = ("", 0.0)
        self._thumbs = None
        self._locks = None      # the worker that finds encrypted archives
        super().__init__(gfile, show_hidden, sort_by, sort_reversed)

    def _make_view(self):
        view = Gtk.Layout()
        view.set_can_focus(True)
        ctx = view.get_style_context()
        ctx.add_class("view")
        ctx.add_class("finch-grid")
        view.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        view.connect("draw", self._on_draw)
        view.connect("size-allocate", self._on_allocate)
        view.connect("button-press-event", self._on_button_press)
        view.connect("button-release-event", self._on_button_release)
        view.connect("motion-notify-event", self._on_motion)
        view.connect_after("drag-begin", self._on_drag_begin_icon)
        view.set_size_request(2 * CELL_MIN_WIDTH, -1)
        self.sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                     vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.sw.add(view)
        return view

    # -- geometry --------------------------------------------------------------
    def _on_allocate(self, view, alloc):
        if alloc.width != self._width:
            self._width = alloc.width
            self._relayout()

    def _relayout(self):
        # The cell is as tall as the icon plus TEXT_LINES lines of the
        # widget's font, whatever size the theme gives it.
        self._cell_h = TEXT_TOP + TEXT_LINES * self._line_height() + CELL_BOTTOM
        width = max(self._width - 2 * MARGIN, 1)
        self._cols = max(1, width // CELL_MIN_WIDTH)
        self._cell_w = width / self._cols
        rows = math.ceil(len(self.items) / self._cols)
        self.view.set_size(self._width, max(rows * self._cell_h + 2 * MARGIN, 1))
        if self._entry is not None:
            self._place_entry()
        self.view.queue_draw()

    def _line_height(self):
        layout = self.view.create_pango_layout("Xg")
        return max(layout.get_pixel_size()[1], 1)

    def _cell_pos(self, idx):
        row, col = divmod(idx, self._cols)
        return MARGIN + int(col * self._cell_w), MARGIN + row * self._cell_h

    def _item_at_content(self, x, y):
        x, y = x - MARGIN, y - MARGIN
        if x < 0 or y < 0:
            return None
        col, row = int(x // self._cell_w), int(y // self._cell_h)
        if col >= self._cols:
            return None
        idx = row * self._cols + col
        if idx >= len(self.items):
            return None
        lx, ly = x - col * self._cell_w, y - row * self._cell_h
        if (lx < CELL_INSET or lx > self._cell_w - CELL_INSET
                or ly < CELL_INSET or ly > self._cell_h - CELL_INSET):
            return None
        return idx

    def _item_at(self, x, y):
        return self._item_at_content(x + self.sw.get_hadjustment().get_value(),
                                     y + self.sw.get_vadjustment().get_value())

    def _to_widget(self, x, y):
        return (x - self.sw.get_hadjustment().get_value(),
                y - self.sw.get_vadjustment().get_value())

    def _scroll_to(self, idx):
        adj = self.sw.get_vadjustment()
        _x, y = self._cell_pos(idx)
        if y < adj.get_value():
            adj.set_value(y)
        elif y + self._cell_h > adj.get_value() + adj.get_page_size():
            adj.set_value(y + self._cell_h - adj.get_page_size())

    def _rows_per_page(self):
        return max(1, int(self.sw.get_vadjustment().get_page_size() // self._cell_h))

    # -- view hooks ------------------------------------------------------------
    def _rows_changed(self):
        self.items = [tuple(row) for row in self.store]
        self._selected, self._cursor, self._anchor = set(), None, None
        self._relayout()

    def _selected_handles(self):
        return sorted(self._selected)

    def selected_all(self):
        # From the mirror list, not the store: Ctrl+A over thirty thousand
        # files would otherwise spend most of a second fetching store rows.
        rows = [self.items[i] for i in sorted(self._selected)]
        return [row[C_FILE] for row in rows], [row[C_INFO] for row in rows]

    def _is_selected(self, idx):
        return idx in self._selected

    def _set_selection(self, selected, cursor, anchor=None):
        selected = set(selected)
        changed = selected != self._selected
        self._selected, self._cursor = selected, cursor
        if anchor is not None:
            self._anchor = anchor
        elif self._anchor is None:
            self._anchor = cursor
        self.view.queue_draw()
        if changed:
            self._emit_selection()

    def _select_only(self, idx):
        self._set_selection({idx}, idx, anchor=idx)
        self._scroll_to(idx)

    def _select_add(self, idx):
        self._set_selection(self._selected | {idx},
                            self._cursor if self._cursor is not None else idx)

    def _select_range(self, idx):
        """Select from the anchor to `idx`, which becomes the cursor."""
        anchor = self._anchor if self._anchor is not None else idx
        lo, hi = sorted((anchor, idx))
        self._set_selection(range(lo, hi + 1), idx, anchor=anchor)
        self._scroll_to(idx)

    def _highlight_drop(self, idx):
        self._drop_hit = idx
        ctx = self.view.get_style_context()
        if idx is None:
            ctx.add_class("drop-target")
        else:
            ctx.remove_class("drop-target")
        self.view.queue_draw()

    def _clear_drop(self):
        self._drop_hit = None
        self.view.get_style_context().remove_class("drop-target")
        self.view.queue_draw()

    def select_first(self):
        if self.items and not self._selected:
            self._select_only(0)

    def _on_destroy(self, *args):
        if self._thumbs is not None:
            self._thumbs.stop()
        if self._locks is not None:
            self._locks.stop()
        super()._on_destroy(*args)

    # -- drawing ---------------------------------------------------------------
    def _on_draw(self, view, cr):
        window = view.get_bin_window()
        if window is None:
            return False
        Gtk.cairo_transform_to_window(cr, view, window)
        ctx = view.get_style_context()
        x0, y0, x1, y1 = cr.clip_extents()
        Gtk.render_background(ctx, cr, x0, y0, x1 - x0, y1 - y0)
        if self.items:
            layout = self._text_layout(view)
            wanted, locks = [], []
            first = max(0, int((y0 - MARGIN) // self._cell_h))
            last = max(0, int((y1 - MARGIN) // self._cell_h))
            for row in range(first, last + 1):
                for col in range(self._cols):
                    idx = row * self._cols + col
                    if idx >= len(self.items):
                        break
                    self._draw_cell(cr, ctx, layout, idx, *self._cell_pos(idx), wanted, locks)
            if wanted or self._thumbs is not None:
                self._thumbnailer().want(wanted)
            if locks or self._locks is not None:
                self._lock_checker().want(locks)
        if self._band is not None:
            bx0, by0, bx1, by1, _base = self._band
            ok, rgba = ctx.lookup_color("theme_selected_bg_color")
            if ok:
                cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.2)
                cr.rectangle(min(bx0, bx1), min(by0, by1), abs(bx1 - bx0), abs(by1 - by0))
                cr.fill_preserve()
                cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.8)
                cr.set_line_width(1)
                cr.stroke()
        # False: the layout's own handler then draws its child (the rename
        # box) on top.
        return False

    def _text_layout(self, widget):
        layout = widget.create_pango_layout("")
        layout.set_width(int(self._cell_w - 2 * TEXT_INSET) * Pango.SCALE)
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_height(-TEXT_LINES)
        return layout

    def _draw_cell(self, cr, ctx, layout, idx, x, y, wanted, locks, plain=False):
        """One cell at (x, y). `plain`: no selection, for the drag icon.
        Files whose picture is still to be made are appended to `wanted`,
        archives not yet known to be encrypted or not to `locks`."""
        gicon, name, is_dir, gfile, info = self.items[idx]
        selected = not plain and idx in self._selected
        cell_w = int(self._cell_w)

        # the picture or icon, centred in the icon box
        pixbuf = None if is_dir else self._thumbnail(idx, gfile, info, wanted)
        box_x, box_y = x + (cell_w - ICON_SIZE) // 2, y + ICON_TOP
        if selected:
            ok, fg = ctx.lookup_color("theme_fg_color")
            if ok:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.1)
                _rounded_rect(cr, box_x - 6, box_y - 6, ICON_SIZE + 12, ICON_SIZE + 12, 6)
                cr.fill()
        if pixbuf is not None:
            scale = self.view.get_scale_factor()
            w, h = pixbuf.get_width() / scale, pixbuf.get_height() / scale
            px, py = box_x + (ICON_SIZE - w) / 2, box_y + (ICON_SIZE - h) / 2
            cr.save()
            cr.scale(1 / scale, 1 / scale)
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, px * scale, py * scale)
            cr.paint()
            cr.restore()
        else:
            surface = self._icon_surface(gicon)
            if surface is not None:
                cr.set_source_surface(surface, box_x, box_y)
                cr.paint()
        for emblem, (right, bottom) in zip(self._emblems(gfile, info, locks), EMBLEM_CORNERS):
            surface = self._icon_surface(emblem, EMBLEM_SIZE, fallback=None)
            if surface is not None:
                cr.set_source_surface(surface, box_x + right * (ICON_SIZE - EMBLEM_SIZE),
                                      box_y + bottom * (ICON_SIZE - EMBLEM_SIZE))
                cr.paint()
        if self._drop_hit == idx:
            ok, rgba = ctx.lookup_color("theme_selected_bg_color")
            if ok:
                cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.9)
                cr.set_line_width(2)
                _rounded_rect(cr, box_x - 6, box_y - 6, ICON_SIZE + 12, ICON_SIZE + 12, 6)
                cr.stroke()

        # the name, on a pill in the selection colour when selected (not
        # while the rename box sits over it)
        if self._entry is not None and idx == self._entry_idx and not plain:
            return
        layout.set_text(name, -1)
        _ink, logical = layout.get_pixel_extents()
        tx, ty = x + TEXT_INSET, y + TEXT_TOP
        if selected:
            ctx.save()
            ctx.set_state(ctx.get_state() | Gtk.StateFlags.SELECTED)
            Gtk.render_background(ctx, cr, tx + logical.x - PILL_PAD, ty + logical.y - 1,
                                  logical.width + 2 * PILL_PAD, logical.height + 2)
            Gtk.render_layout(ctx, cr, tx, ty, layout)
            ctx.restore()
        else:
            Gtk.render_layout(ctx, cr, tx, ty, layout)

    def _icon_surface(self, gicon, size=ICON_SIZE, fallback="text-x-generic"):
        """`gicon` rendered at `size`, or the `fallback` icon when the theme
        has nothing for it (None: nothing at all, for an emblem)."""
        scale = self.view.get_scale_factor()
        key = (Gio.Icon.to_string(gicon) if gicon is not None else "", size, scale)
        if key in _icons:
            return _icons[key]
        theme = Gtk.IconTheme.get_default()
        flags = Gtk.IconLookupFlags.FORCE_SIZE
        info = theme.lookup_by_gicon_for_scale(gicon, size, scale, flags) if gicon else None
        if info is None and fallback:
            info = theme.lookup_icon_for_scale(fallback, size, scale, flags)
        try:
            surface = info.load_surface(self.view.get_window()) if info is not None else None
        except GLib.Error:
            surface = None
        _icons[key] = surface
        return surface

    def _thumbnailer(self):
        if self._thumbs is None:
            self._thumbs = thumbs.Thumbnailer(ICON_SIZE * self.view.get_scale_factor(),
                                              self._on_thumb_ready)
        return self._thumbs

    def _on_thumb_ready(self):
        if self.get_realized():
            self.view.queue_draw()
        return False

    def _lock_checker(self):
        if self._locks is None:
            self._locks = archive.LockChecker(self._on_thumb_ready)
        return self._locks

    def _emblems(self, gfile, info, locks):
        """The badges a file gets, first (bottom-right) first: one padlock
        at most, for an encrypted archive or else a file that cannot be
        written (nothing can be written in the trash: no padlocks there),
        the "unreadable" mark, the symbolic link arrow."""
        emblems = []
        is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
        if not is_dir and self._locked(gfile, info, locks):
            emblems.append(LOCK_EMBLEM)
        if info.has_attribute("access::can-read") and not info.get_attribute_boolean("access::can-read"):
            emblems.append(UNREADABLE_EMBLEM)
        elif (not emblems and not self.in_trash() and info.has_attribute("access::can-write")
                and not info.get_attribute_boolean("access::can-write")):
            emblems.append(READONLY_EMBLEM)
        if info.get_is_symlink():
            emblems.append(SYMLINK_EMBLEM)
        return emblems

    def _locked(self, gfile, info, locks):
        """Whether the file is an archive known to want a password; one
        not looked at yet is appended to `locks` and counts as not."""
        ctype = info.get_content_type() or ""
        if not archive.can_be_locked(ctype):
            return False
        key = thumbs.stamp(gfile, info.get_attribute_uint64("time::modified"), info.get_size())
        known, encrypted = archive.lock_get(key)
        if not known:
            locks.append((key, gfile.get_path(), ctype))
        return encrypted

    def _thumbnail(self, idx, gfile, info, wanted):
        """The file's small picture if there is one (yet); asks for it to be
        made otherwise."""
        ctype = info.get_content_type() or ""
        if not (ctype.startswith("image/") or ctype.startswith("video/")
                or ctype == "application/pdf"):
            return None
        mtime, size = info.get_attribute_uint64("time::modified"), info.get_size()
        key = thumbs.stamp(gfile, mtime, size)
        known, pixbuf = thumbs.thumb_get(key)
        if not known:
            wanted.append((key, gfile, gfile.get_path(), ctype, mtime, size))
        return pixbuf

    # -- rename ----------------------------------------------------------------
    def start_rename(self):
        if len(self._selected) != 1 or self.in_trash() or self._entry is not None:
            return
        idx = next(iter(self._selected))
        row = self.store[idx]
        self._renaming = True
        # GtkLayout gives a child its minimum size, and an entry's is 150 px
        # unless width-chars says less: then the size request below rules.
        entry = Gtk.Entry(text=row[C_NAME], width_chars=1)
        entry.get_style_context().add_class("grid-rename")
        entry.connect("activate", lambda e: self._finish_rename(idx, e.get_text()))
        entry.connect("key-press-event", self._on_entry_key, idx)
        entry.connect("focus-out-event", lambda e, _ev: self._finish_rename(idx, e.get_text()))
        self._entry, self._entry_idx = entry, idx
        self._place_entry()
        entry.show()
        entry.grab_focus()
        if not row[C_ISDIR]:
            self._preselect_stem(entry, row[C_NAME])

    def _place_entry(self):
        x, y = self._cell_pos(self._entry_idx)
        width = int(self._cell_w) - 2 * TEXT_INSET + 2 * PILL_PAD
        self._entry.set_size_request(width, -1)
        if self._entry.get_parent() is None:
            self.view.put(self._entry, x + TEXT_INSET - PILL_PAD, y + TEXT_TOP - PILL_PAD)
        else:
            self.view.move(self._entry, x + TEXT_INSET - PILL_PAD, y + TEXT_TOP - PILL_PAD)

    def _on_entry_key(self, entry, event, idx):
        if event.keyval == Gdk.KEY_Escape:
            self._finish_rename(idx, None)
            return True
        return False

    def _finish_rename(self, idx, new_name):
        """Close the rename box; `new_name` None cancels."""
        entry = self._entry
        if entry is None:
            return False
        self._entry = None
        self._renaming = False
        # Removing the box from inside its own focus-out handler is asking
        # for trouble; do it once the event is over.
        GLib.idle_add(lambda: (self.view.remove(entry), False)[1])
        self.view.grab_focus()
        if new_name is not None:
            self._commit_rename(idx, new_name)
        return False

    # -- keyboard --------------------------------------------------------------
    def _view_key(self, event):
        key, n = event.keyval, len(self.items)
        if n == 0:
            return False
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        if ctrl and key in (Gdk.KEY_a, Gdk.KEY_A):
            self._set_selection(range(n), self._cursor if self._cursor is not None else 0)
            return True
        if ctrl:
            return False                    # Ctrl+Down opens, Ctrl+O too: Listing's
        page = self._rows_per_page() * self._cols
        moves = {Gdk.KEY_Left: -1, Gdk.KEY_Right: 1, Gdk.KEY_Up: -self._cols,
                 Gdk.KEY_Down: self._cols, Gdk.KEY_Page_Up: -page, Gdk.KEY_Page_Down: page}
        if key in moves or key in (Gdk.KEY_Home, Gdk.KEY_End):
            if self._cursor is None:
                self._select_only(0 if key != Gdk.KEY_End else n - 1)
                return True
            if key == Gdk.KEY_Home:
                target = 0
            elif key == Gdk.KEY_End:
                target = n - 1
            else:
                target = self._cursor + moves[key]
                if not 0 <= target < n:
                    if key in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right):
                        return True         # off the edge: stay put
                    target = min(max(target, 0), n - 1)
            if shift:
                self._select_range(target)
            else:
                self._select_only(target)
            return True
        if key == Gdk.KEY_space and self._cursor is not None:
            self._set_selection(self._selected ^ {self._cursor}, self._cursor)
            return True
        # Type-ahead: letters typed in quick succession find the first name
        # that starts with them.
        char = Gdk.keyval_to_unicode(key)
        # (Delete maps to the DEL control character: not a letter.)
        if char and chr(char).isprintable() and not (event.state & Gdk.ModifierType.MOD1_MASK):
            now = time.monotonic()
            prefix, last = self._search
            prefix = (prefix if now - last < SEARCH_TIMEOUT else "") + chr(char)
            self._search = (prefix, now)
            wanted = prefix.casefold()
            for i, item in enumerate(self.items):
                if item[C_NAME].casefold().startswith(wanted):
                    self._select_only(i)
                    break
            return True
        return False

    # -- mouse -----------------------------------------------------------------
    def _on_button_press(self, view, event):
        idx = self._item_at_content(event.x, event.y)
        if event.button == 2:
            self._press_middle(idx)
            return True
        if event.button == 3:
            view.grab_focus()
            self._press_context(idx, event)
            return True
        if event.button != 1:
            return False
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            if idx is not None:
                item = self.items[idx]
                self.emit("item-activated", item[C_FILE], item[C_INFO])
            return True
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return True
        view.grab_focus()
        self._press = self._band = None
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        if idx is None:
            if not (ctrl or shift):
                self._set_selection(set(), None)
            # A drag from here sweeps out a rubber band.
            self._band = (event.x, event.y, event.x, event.y, set(self._selected))
            return True
        if ctrl:
            self._set_selection(self._selected ^ {idx}, idx, anchor=idx)
        elif shift:
            self._select_range(idx)
        elif idx in self._selected:
            # Click or drag of the (possibly whole) selection: decided later.
            self._press = (idx, event.x, event.y, True)
        else:
            self._select_only(idx)
            self._press = (idx, event.x, event.y, False)
        return True

    def _on_button_release(self, view, event):
        if event.button != 1:
            return False
        if self._press is not None:
            idx, _x, _y, was_selected = self._press
            self._press = None
            if was_selected:
                self._select_only(idx)      # it was a click after all
            return True
        if self._band is not None:
            self._band = None
            view.queue_draw()
            return True
        return False

    def _on_motion(self, view, event):
        if self._press is not None:
            idx, x0, y0, _was = self._press
            if view.drag_check_threshold(int(x0), int(y0), int(event.x), int(event.y)):
                self._press = None
                self._drag_from = (idx, event.x, event.y)
                wx, wy = self._to_widget(event.x, event.y)
                view.drag_begin_with_coordinates(
                    Gtk.TargetList.new(DND_TARGETS),
                    Gdk.DragAction.MOVE | Gdk.DragAction.COPY, 1, event, int(wx), int(wy))
            return True
        if self._band is not None:
            x0, y0, _x1, _y1, base = self._band
            self._band = (x0, y0, event.x, event.y, base)
            self._set_selection(base | self._cells_in(x0, y0, event.x, event.y), self._cursor)
            return True
        return False

    def _cells_in(self, x0, y0, x1, y1):
        """Indices of the cells the (content coordinates) rectangle touches."""
        left, right = sorted((x0 - MARGIN, x1 - MARGIN))
        top, bottom = sorted((y0 - MARGIN, y1 - MARGIN))
        c0 = max(0, int((left - CELL_INSET) // self._cell_w))
        c1 = min(self._cols - 1, int((right + CELL_INSET) // self._cell_w))
        r0 = max(0, int((top - CELL_INSET) // self._cell_h))
        r1 = int((bottom + CELL_INSET) // self._cell_h)
        hits = set()
        for row in range(r0, r1 + 1):
            cy = row * self._cell_h
            if cy + CELL_INSET > bottom or cy + self._cell_h - CELL_INSET < top:
                continue
            for col in range(c0, c1 + 1):
                cx = col * self._cell_w
                if cx + CELL_INSET > right or cx + self._cell_w - CELL_INSET < left:
                    continue
                idx = row * self._cols + col
                if idx < len(self.items):
                    hits.add(idx)
        return hits

    # -- drag icon -------------------------------------------------------------
    def _on_drag_begin_icon(self, view, ctx):
        """The pressed cell as the drag icon, with a badge giving the count
        when several items go together."""
        drag = self._drag_from
        self._drag_from = None
        if drag is None:
            return
        idx, px, py = drag
        cx, cy = self._cell_pos(idx)
        w, h = int(self._cell_w), self._cell_h
        scale = view.get_scale_factor()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * scale, h * scale)
        surface.set_device_scale(scale, scale)
        cr = cairo.Context(surface)
        self._draw_cell(cr, view.get_style_context(), self._text_layout(view), idx, 0, 0, [], [],
                        plain=True)
        count = len(_dragging)
        if count > 1:
            r = 10
            bx, by = (w + ICON_SIZE) // 2 + 2, ICON_TOP + 4
            cr.arc(bx, by, r, 0, 2 * math.pi)
            cr.set_source_rgb(0.21, 0.52, 0.89)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(11)
            text = str(count)
            ext = cr.text_extents(text)
            cr.move_to(bx - ext.width / 2 - ext.x_bearing, by - ext.height / 2 - ext.y_bearing)
            cr.show_text(text)
        surface.set_device_offset(-(px - cx) * scale, -(py - cy) * scale)
        Gtk.drag_set_icon_surface(ctx, surface)


def _rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
