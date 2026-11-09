"""What every directory listing does, whatever it looks like: reading the
directory, sorting, hidden files, the selection, renaming, the trash, drag
and drop, the directory monitor.

The two looks are Column (a list: one of the Miller columns) and Grid
(icons). Each supplies its widget in `_make_view` and the handful of
operations that depend on it, listed under "view hooks": selecting rows,
finding the row under a point, highlighting a drop, starting a rename. A
row is addressed by a *handle* the view understands (a tree path for the
list, an index for the grid); the ListStore accepts either.
"""
import os

from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from . import ATTRS, C_FILE, C_INFO, C_ISDIR, C_NAME, _, ngettext
from .util import confirm, delete_recursive, natural_key, show_error

# Sort modes a listing understands; the window keeps the current one.
SORT_MODES = ("name", "modified", "size", "kind")

URI_LIST = "text/uri-list"
DND_TARGETS = [Gtk.TargetEntry.new(URI_LIST, 0, 0)]
# Files being dragged out of one of our own listings (empty for drags that
# come from other programs); lets a listing refuse pointless drops.
_dragging = []


def drag_has_files(context):
    """Does the drag `context` carry files at all? (A notebook tab being
    dragged, say, does not: such a drag is none of a listing's business.)"""
    return any(t.name() == URI_LIST for t in context.list_targets())


def drop_action(context, widget):
    """The action a drop of `context` should take: move, or copy with Ctrl
    held (or when the source does not allow moving). GTK itself suggests
    copying whenever no modifier is down, so the destination decides."""
    actions = context.get_actions()
    state = 0
    device, window = context.get_device(), widget.get_window()
    if device is not None and window is not None:
        state = window.get_device_position(device)[3]
    if state & Gdk.ModifierType.CONTROL_MASK and actions & Gdk.DragAction.COPY:
        return Gdk.DragAction.COPY
    if actions & Gdk.DragAction.MOVE:
        return Gdk.DragAction.MOVE
    return actions & Gdk.DragAction.COPY


class Listing(Gtk.Box):
    """A directory, shown by the widget `view` inside the scroller `sw`."""

    __gsignals__ = {
        # (gfile, info) of the selected row, or (None, None) when cleared
        "item-selected": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
        # ([gfiles], [infos]) when several rows are selected at once
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
        "item-activated": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
        # (new width, apply to every column?)  Shift-drag resizes all columns.
        "width-changed": (GObject.SignalFlags.RUN_FIRST, None, (int, bool)),
        "key-nav": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "context-menu": (GObject.SignalFlags.RUN_FIRST, None, (object, object, object)),
        "loaded": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # (files, directory, move?) dropped onto this listing
        "files-dropped": (GObject.SignalFlags.RUN_FIRST, None, (object, object, bool)),
        # a folder was middle-clicked
        "open-new-tab": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, gfile, show_hidden, sort_by="name", sort_reversed=False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.gfile = gfile
        self.show_hidden = show_hidden
        self.sort_by = sort_by if sort_by in SORT_MODES else "name"
        self.sort_reversed = sort_reversed
        self._gen = 0
        self._quiet = False
        self._renaming = False
        self._reload_id = 0
        self._focus_after_load = False
        self.item_count = 0

        self.store = Gtk.ListStore(Gio.Icon, str, bool, object, object)
        self.view = self._make_view()
        view = self.view
        view.connect("key-press-event", self._on_key_press)
        # Drag source: the selection leaves as a URI list, to another
        # listing, the sidebar or another program.
        view.connect("drag-begin", self._on_drag_begin)
        view.connect("drag-end", self._on_drag_end)
        view.connect("drag-data-get", self._on_drag_data_get)
        view.connect("drag-data-delete", self._on_drag_data_delete)
        # Drop target: files land in the folder under the pointer, or in
        # this listing's directory.
        view.drag_dest_set(0, DND_TARGETS, Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        view.connect("drag-motion", self._on_drag_motion)
        view.connect("drag-leave", self._on_drag_leave)
        view.connect("drag-drop", self._on_drag_drop)
        view.connect("drag-data-received", self._on_drag_data_received)
        # Finder style: only the focused listing shows its selection in the
        # accent colour; the others fall back to a neutral grey.
        view.get_style_context().add_class("inactive")
        view.connect("focus-in-event", self._on_focus_change, False)
        view.connect("focus-out-event", self._on_focus_change, True)

        self.message = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                                 margin=12)
        self.message.get_style_context().add_class("dim-label")
        # Same "view" background as the list, so an empty listing still reads
        # as one next to the sidebar instead of blending into the window.
        message_bg = Gtk.EventBox()
        message_bg.get_style_context().add_class("view")
        message_bg.add(self.message)
        message_bg.connect("button-press-event", self._on_message_press)
        self.stack = Gtk.Stack()
        self.stack.add_named(self.sw, "list")
        self.stack.add_named(message_bg, "message")
        self.pack_start(self.stack, True, True, 0)

        try:
            self.monitor = gfile.monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.monitor.connect("changed", self._on_monitor_changed)
        except GLib.Error:
            self.monitor = None

        self.connect("destroy", self._on_destroy)

    # -- view hooks (what a look has to supply) --------------------------------
    def _make_view(self):
        """Build the widget showing `self.store`, put it in `self.sw` (a
        scrolled window) and return it."""
        raise NotImplementedError

    def _selected_handles(self):
        """Handles of the selected rows, in list order."""
        raise NotImplementedError

    def _is_selected(self, handle):
        raise NotImplementedError

    def _select_only(self, handle):
        """Make `handle` the cursor and the only selected row."""
        raise NotImplementedError

    def _select_add(self, handle):
        raise NotImplementedError

    def _item_at(self, x, y):
        """Handle of the row at widget coordinates (x, y), or None."""
        raise NotImplementedError

    def _highlight_drop(self, handle):
        """Show that a drop would land in the row `handle` (None: in this
        listing's own directory)."""
        raise NotImplementedError

    def _clear_drop(self):
        raise NotImplementedError

    def _view_key(self, event):
        """Keys the look handles itself (moving the cursor); True if taken."""
        return False

    def _rows_changed(self):
        """The store was rebuilt."""

    def start_rename(self):
        raise NotImplementedError

    def set_width(self, width):
        """A new column width; only the list look has one."""

    # -- loading -------------------------------------------------------------
    def load(self, select=None, focus=False):
        """Initial load. `select` is a Gio.File to select (and open) afterwards."""
        self._quiet = True
        self.store.clear()
        self._quiet = False
        self._focus_after_load = focus
        self._enumerate(select, emit=True)

    def _reload_keep_selection(self):
        """Reload after a change, restoring the selection without re-opening it."""
        self._reload_id = 0
        if self._renaming:
            # Rebuilding the list would cancel the edit; try again later.
            self._reload_id = GLib.timeout_add(400, self._reload_keep_selection)
            return False
        current, _infos = self.selected_all()
        self._enumerate(current, emit=False)
        return False

    def reload_and_select(self, gfiles):
        """Reload with `gfiles` (one Gio.File or a list) selected and shown
        to the right, as after a rename: used for items just pasted here."""
        self._enumerate(gfiles, emit=True)

    def select_and_rename(self, gfile):
        """Reload with `gfile` selected, then open the inline rename editor."""
        def on_loaded(col):
            col.disconnect(handler)
            col.view.grab_focus()
            # Let the view validate the new rows before editing starts.
            GLib.idle_add(col.start_rename)
        handler = self.connect("loaded", on_loaded)
        self._enumerate(gfile, emit=True)

    def _enumerate(self, select, emit):
        self._gen += 1
        gen = self._gen
        self.gfile.enumerate_children_async(
            ATTRS, Gio.FileQueryInfoFlags.NONE, GLib.PRIORITY_DEFAULT, None,
            self._on_enumerated, (gen, select, emit))

    def _on_enumerated(self, gfile, result, data):
        gen, select, emit = data
        if gen != self._gen:
            return
        try:
            enumerator = gfile.enumerate_children_finish(result)
        except GLib.Error as err:
            self._show_message(err.message)
            return
        infos = []

        def next_cb(en, res, _data):
            if gen != self._gen:
                return
            try:
                batch = en.next_files_finish(res)
            except GLib.Error as err:
                self._show_message(err.message)
                return
            if batch:
                infos.extend(batch)
                en.next_files_async(256, GLib.PRIORITY_DEFAULT, None, next_cb, None)
            else:
                en.close_async(GLib.PRIORITY_DEFAULT, None, lambda *a: None, None)
                self._populate(infos, select, emit)

        enumerator.next_files_async(256, GLib.PRIORITY_DEFAULT, None, next_cb, None)

    def _populate(self, infos, select, emit):
        """Fill the list from `infos`, selecting `select` (a Gio.File, a list
        of them, or None); `emit` says whether the resulting selection should
        drive what is shown to the right."""
        selects = select if isinstance(select, list) else ([select] if select else [])
        rows = []
        for info in infos:
            if not self.show_hidden and (info.get_is_hidden() or info.get_is_backup()):
                continue
            is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
            name = info.get_display_name() or info.get_name()
            rows.append((not is_dir, self._sort_key(info, is_dir, name), info, is_dir, name))
        # Folders always come first; "reversed" flips the order within the
        # folders and within the files, not the two groups.
        rows.sort(key=lambda r: r[1], reverse=self.sort_reversed)
        rows.sort(key=lambda r: r[0])

        # Rebuild silently; selection signals are emitted explicitly below.
        self._quiet = True
        self.store.clear()
        for _is_file, _key, info, is_dir, name in rows:
            child = self.gfile.get_child(info.get_name())
            self.store.append([info.get_icon(), name, is_dir, child, info])
        self._rows_changed()
        found = []
        for i, row in enumerate(self.store):
            if any(row[C_FILE].equal(want) for want in selects):
                if not found:
                    self._select_only(i)
                else:
                    self._select_add(i)
                found.append((row[C_FILE], row[C_INFO]))
        self._quiet = False
        self.item_count = len(rows)

        if not rows:
            self._show_message(_("Empty folder"))
        else:
            self.stack.set_visible_child_name("list")

        if len(found) == 1:
            if emit:
                self.emit("item-selected", *found[0])
        elif found:
            if emit:
                self.emit("selection-changed", [f for f, _i in found], [i for _f, i in found])
        elif emit or selects:
            # Nothing is selected now (the entry to select is gone, or there
            # was none to begin with): drop the columns and preview to the
            # right. On a silent reload with nothing selected before there is
            # nothing to the right, so there is nothing to tell.
            self.emit("item-selected", None, None)
        if self._focus_after_load and rows:
            self._focus_after_load = False
            self.view.grab_focus()
        self.emit("loaded")

    def _show_message(self, text):
        self.message.set_text(text)
        self.stack.set_visible_child_name("message")

    def set_show_hidden(self, show):
        if show == self.show_hidden:
            return
        self.show_hidden = show
        self._reload_keep_selection()

    def _sort_key(self, info, is_dir, name):
        """Ordering of one entry under the current sort mode. Every mode falls
        back to the name, so entries that tie keep a stable, natural order."""
        by_name = natural_key(name)
        if self.sort_by == "modified":
            # newest first
            return (-info.get_attribute_uint64("time::modified"), by_name)
        if self.sort_by == "size":
            # largest first; a folder's size is meaningless, so folders stay by name
            return (0 if is_dir else -info.get_size(), by_name)
        if self.sort_by == "kind":
            ctype = info.get_content_type() or ""
            kind = Gio.content_type_get_description(ctype) if ctype else ""
            return (natural_key(kind), by_name)
        return (by_name,)

    def set_sort(self, sort_by, sort_reversed):
        if sort_by not in SORT_MODES:
            sort_by = "name"
        if (sort_by, sort_reversed) == (self.sort_by, self.sort_reversed):
            return
        self.sort_by, self.sort_reversed = sort_by, sort_reversed
        self._reload_keep_selection()

    # -- selection -----------------------------------------------------------
    def selected_all(self):
        """Every selected row, as two lists: ([gfiles], [infos])."""
        rows = [self.store[handle] for handle in self._selected_handles()]
        return [row[C_FILE] for row in rows], [row[C_INFO] for row in rows]

    def selected(self):
        """(gfile, info) of the selected row when exactly one is selected,
        else (None, None): what single-item features (opening to the right,
        renaming, the current directory) act on."""
        gfiles, infos = self.selected_all()
        if len(gfiles) != 1:
            return None, None
        return gfiles[0], infos[0]

    def _targets(self, gfile=None):
        """Items an action applies to: the whole selection, or `gfile` alone
        when one is given that is not part of it."""
        gfiles = self.selected_all()[0]
        if gfile is not None and not any(g.equal(gfile) for g in gfiles):
            return [gfile]
        return gfiles

    def _emit_selection(self):
        """The view's selection changed: tell the tab, unless it is being
        rebuilt (then _populate speaks for it)."""
        if self._quiet:
            return
        gfiles, infos = self.selected_all()
        if len(gfiles) > 1:
            self.emit("selection-changed", gfiles, infos)
        else:
            self.emit("item-selected", gfiles[0] if gfiles else None,
                      infos[0] if infos else None)

    def select_first(self):
        if len(self.store) and not self.selected_all()[0]:
            self._select_only(0)

    # -- rename --------------------------------------------------------------
    def _commit_rename(self, handle, new_name):
        row = self.store[handle]
        new_name = new_name.strip()
        if not new_name or new_name == row[C_NAME]:
            return
        try:
            renamed = row[C_FILE].set_display_name(new_name, None)
        except GLib.Error as err:
            show_error(self.get_toplevel(), _("Could not rename"), err.message)
            return
        # Reload with the renamed entry selected; emitting re-opens the
        # column (or preview) to the right under the new name.
        self._enumerate(renamed, emit=True)

    @staticmethod
    def _preselect_stem(entry, name):
        """Like Finder: preselect the name without its extension. The entry
        selects everything when it grabs focus, which happens after it is
        set up; narrow the selection once that is done."""
        stem, ext = os.path.splitext(name)
        if stem and ext:
            GLib.idle_add(entry.select_region, 0, len(stem))

    # -- trash / delete ------------------------------------------------------
    def in_trash(self):
        return self.gfile.get_uri_scheme() == "trash"

    def is_trash_root(self):
        return self.in_trash() and self.gfile.get_parent() is None

    def _neighbour_of(self, gfiles):
        """The row to select once `gfiles` are gone: the first one after the
        last of them, else the last one before the first of them."""
        going = [i for i, row in enumerate(self.store)
                 if any(row[C_FILE].equal(g) for g in gfiles)]
        if not going:
            return None
        for i in range(going[-1] + 1, len(self.store)):
            if i not in going:
                return self.store[i][C_FILE]
        for i in range(going[0] - 1, -1, -1):
            if i not in going:
                return self.store[i][C_FILE]
        return None

    def _finish_batch(self, gfiles, title, errors):
        if errors:
            show_error(self.get_toplevel(), title, "\n".join(errors[:10]))
        self._enumerate(self._neighbour_of(gfiles), emit=True)

    def trash_item(self, gfile=None):
        """Move the selection (or `gfile`) to the trash."""
        gfiles = self._targets(gfile)
        if not gfiles:
            return
        if self.in_trash():
            # Already in the trash: the only way out is restore or delete.
            if self.is_trash_root():
                self.delete_item(gfile)
            return
        errors = []
        for g in gfiles:
            try:
                g.trash(None)
            except GLib.Error as err:
                errors.append("%s: %s" % (g.get_basename(), err.message))
        self._finish_batch(gfiles, _("Could not move to trash"), errors)

    def delete_item(self, gfile=None):
        """Delete the selection (or `gfile`) for good, after confirmation."""
        gfiles = self._targets(gfile)
        if not gfiles:
            return
        n = len(gfiles)
        title = (_("Permanently delete “%s”?") % gfiles[0].get_basename() if n == 1
                 else ngettext("Permanently delete %d item?",
                               "Permanently delete %d items?", n) % n)
        if not confirm(self.get_toplevel(), title,
                       ngettext("This item will be deleted immediately, bypassing the "
                                "trash. This cannot be undone.",
                                "These items will be deleted immediately, bypassing the "
                                "trash. This cannot be undone.", n),
                       _("_Delete")):
            return
        errors = []
        for g in gfiles:
            try:
                if self.is_trash_root():
                    g.delete(None)      # the trash backend removes the whole item
                else:
                    delete_recursive(g)
            except GLib.Error as err:
                errors.append("%s: %s" % (g.get_basename(), err.message))
        self._finish_batch(gfiles, _("Could not delete"), errors)

    def restore_item(self, gfile=None):
        """Move top-level trash entries back to where they were deleted from."""
        gfiles = self._targets(gfile)
        if not gfiles or not self.is_trash_root():
            return
        errors = []
        for g in gfiles:
            info = next((row[C_INFO] for row in self.store if row[C_FILE].equal(g)), None)
            orig = info.get_attribute_byte_string("trash::orig-path") if info else None
            if not orig:
                errors.append(_("The original location of “%s” is unknown.")
                              % g.get_basename())
                continue
            dest = Gio.File.new_for_path(orig)
            try:
                parent = dest.get_parent()
                if parent is not None and not parent.query_exists(None):
                    parent.make_directory_with_parents(None)
                g.move(dest, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, None, None)
            except GLib.Error as err:
                errors.append("%s → %s: %s" % (g.get_basename(), orig, err.message))
        self._finish_batch(gfiles, _("Could not restore"), errors)

    def empty_trash(self):
        if not self.is_trash_root():
            return
        if not confirm(self.get_toplevel(), _("Empty trash?"),
                       _("All items in the trash will be permanently deleted. "
                         "This cannot be undone."),
                       _("_Empty Trash")):
            return
        failed = []
        try:
            for child in self.gfile.enumerate_children(
                    "standard::name", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None):
                try:
                    self.gfile.get_child(child.get_name()).delete(None)
                except GLib.Error as err:
                    failed.append(f"{child.get_name()}: {err.message}")
        except GLib.Error as err:
            failed.append(err.message)
        if failed:
            show_error(self.get_toplevel(), _("Some items could not be deleted"),
                       "\n".join(failed[:10]))
        self._enumerate(None, emit=True)

    # -- monitor -------------------------------------------------------------
    def _on_monitor_changed(self, monitor, f, other, event):
        if event in (Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.DELETED,
                     Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.MOVED_OUT,
                     Gio.FileMonitorEvent.RENAMED, Gio.FileMonitorEvent.ATTRIBUTE_CHANGED,
                     Gio.FileMonitorEvent.CHANGES_DONE_HINT):
            if self._reload_id:
                GLib.source_remove(self._reload_id)
            self._reload_id = GLib.timeout_add(400, self._reload_keep_selection)

    # -- events --------------------------------------------------------------
    def _on_focus_change(self, view, _event, inactive):
        ctx = view.get_style_context()
        if inactive:
            ctx.add_class("inactive")
        else:
            ctx.remove_class("inactive")
        view.queue_draw()
        return False

    def _on_key_press(self, view, event):
        if self._view_key(event):
            return True
        key = event.keyval
        if key == Gdk.KEY_BackSpace:
            self.emit("key-nav", "up")
            return True
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if key in (Gdk.KEY_F2, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not ctrl:
            # Finder style: Enter renames; opening is Ctrl+O / Ctrl+Down.
            self.start_rename()
            return True
        if key in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            if event.state & Gdk.ModifierType.SHIFT_MASK:
                self.delete_item()
            else:
                self.trash_item()
            return True
        if ctrl and key in (Gdk.KEY_o, Gdk.KEY_O, Gdk.KEY_Down):
            # Open the selection: with several items, the files among them.
            gfiles, infos = self.selected_all()
            for gfile, info in zip(gfiles, infos):
                if len(gfiles) == 1 or info.get_file_type() != Gio.FileType.DIRECTORY:
                    self.emit("item-activated", gfile, info)
            return True
        return False

    def _on_message_press(self, widget, event):
        if event.button == 1:
            self.view.grab_focus()
            return True
        if event.button != 3:
            return False
        self.emit("context-menu", None, None, event)
        return True

    def _press_middle(self, handle):
        """Middle-click on a folder: open it in a new tab, leave the
        selection alone."""
        if handle is not None and self.store[handle][C_ISDIR]:
            self.emit("open-new-tab", self.store[handle][C_FILE])

    def _press_context(self, handle, event):
        """Right-click: on one of several selected rows the selection stays
        (the menu acts on all of them); elsewhere that row gets selected."""
        if handle is None:
            self.emit("context-menu", None, None, event)
            return
        if not self._is_selected(handle):
            self._select_only(handle)
        row = self.store[handle]
        self.emit("context-menu", row[C_FILE], row[C_INFO], event)

    # -- drag and drop -------------------------------------------------------
    def _on_drag_begin(self, view, ctx):
        _dragging[:] = self.selected_all()[0]

    def _on_drag_end(self, view, ctx):
        _dragging.clear()

    def _on_drag_data_get(self, view, ctx, data, info, time):
        gfiles = self.selected_all()[0]
        if gfiles:
            data.set_uris([g.get_uri() for g in gfiles])

    def _on_drag_data_delete(self, view, ctx):
        # A move was accepted. The destination (ours or another program's)
        # has moved the file itself; GtkTreeView would now delete the row,
        # but the directory monitor keeps the listing right.
        view.stop_emission_by_name("drag-data-delete")

    def accepts_drops(self):
        """Can files be dropped into this listing's directory at all? The
        trash root takes them (into the trash); folders inside the trash and
        the virtual locations do not."""
        if self.in_trash():
            return self.is_trash_root()
        return self.gfile.get_uri_scheme() not in ("recent", "computer", "network")

    def drop_target(self, x, y):
        """Where a drop at widget position (x, y) would go: (directory, the
        handle of the folder row it lands in or None for this listing's
        directory), or (None, None) when a drop here makes no sense."""
        if not self.accepts_drops():
            return None, None
        dest, handle = self.gfile, None
        hit = self._item_at(x, y)
        if hit is not None and not self.in_trash():
            row = self.store[hit]
            if row[C_ISDIR]:
                dest, handle = row[C_FILE], hit
        for src in _dragging:              # one of our own: refuse no-ops
            if dest.equal(src) or dest.has_prefix(src):
                return None, None          # into itself
            parent = src.get_parent()
            if parent is not None and parent.equal(dest) and not self.in_trash():
                return None, None          # where it already is
        return dest, handle

    def _on_drag_motion(self, view, ctx, x, y, time):
        if not drag_has_files(ctx):
            # Not a drop site for this drag: GTK goes on to the widgets
            # above (the notebook, for a tab being dragged).
            self._clear_drop()
            return False
        dest, handle = self.drop_target(x, y)
        if dest is None:
            Gdk.drag_status(ctx, 0, time)
            self._on_drag_leave(view, ctx, time)
            return True
        Gdk.drag_status(ctx, drop_action(ctx, view), time)
        self._highlight_drop(handle)
        return True

    def _on_drag_leave(self, view, ctx, time):
        self._clear_drop()

    def _on_drag_drop(self, view, ctx, x, y, time):
        if not drag_has_files(ctx):
            return False
        dest, _handle = self.drop_target(x, y)
        if dest is None:
            return False
        self._drop_dest = dest
        view.drag_get_data(ctx, Gdk.Atom.intern(URI_LIST, False), time)
        return True

    def _on_drag_data_received(self, view, ctx, x, y, data, info, time):
        view.stop_emission_by_name("drag-data-received")
        dest = getattr(self, "_drop_dest", None)
        files = [Gio.File.new_for_uri(u) for u in (data.get_uris() or [])]
        ok = dest is not None and bool(files)
        move = ctx.get_selected_action() == Gdk.DragAction.MOVE
        # Report success before doing the work: the move is ours to make
        # (the source only drops its row on "delete", see above).
        Gtk.drag_finish(ctx, ok, ok and move, time)
        self._on_drag_leave(view, ctx, time)
        if ok:
            self.emit("files-dropped", files, dest, move)

    def _on_destroy(self, *_args):
        self._gen += 1
        if self._reload_id:
            GLib.source_remove(self._reload_id)
            self._reload_id = 0
        if self.monitor is not None:
            self.monitor.cancel()
