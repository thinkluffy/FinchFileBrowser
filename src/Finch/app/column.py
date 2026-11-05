"""One directory listing: a ListView inside a ScrolledWindow plus a drag handle."""
import os

from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from . import ATTRS, C_FILE, C_ICON, C_INFO, C_ISDIR, C_NAME, MIN_COL_WIDTH, _
from .util import confirm, delete_recursive, natural_key, show_error


class ListView(Gtk.TreeView):
    """TreeView that does not select the first row when it merely gains focus.

    GtkTreeView's grab_focus implementation moves the cursor to the first row
    (selecting it in SINGLE mode) whenever no cursor is set, so a plain click
    on the blank area of a column would select its first entry."""

    def do_grab_focus(self):
        Gtk.Widget.do_grab_focus(self)


class Column(Gtk.Box):
    """One directory listing plus a drag handle on its right edge."""

    __gsignals__ = {
        # (gfile, info) of the selected row, or (None, None) when cleared
        "item-selected": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
        "item-activated": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
        # (new width, apply to every column?)  Shift-drag resizes all columns.
        "width-changed": (GObject.SignalFlags.RUN_FIRST, None, (int, bool)),
        "key-nav": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "context-menu": (GObject.SignalFlags.RUN_FIRST, None, (object, object, object)),
        "loaded": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, gfile, width, show_hidden):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.gfile = gfile
        self.show_hidden = show_hidden
        self._gen = 0
        self._quiet = False
        self._renaming = False
        self._reload_id = 0
        self.item_count = 0

        self.store = Gtk.ListStore(Gio.Icon, str, bool, object, object)
        self.view = ListView(model=self.store, headers_visible=False,
                             enable_search=True, search_column=C_NAME)
        self.view.set_enable_tree_lines(False)
        sel = self.view.get_selection()
        sel.set_mode(Gtk.SelectionMode.SINGLE)
        sel.connect("changed", self._on_selection_changed)

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
        self.view.append_column(col)
        self.name_col = col
        self.name_renderer = name
        name.connect("editing-started", self._on_editing_started)
        name.connect("edited", self._on_name_edited)
        name.connect("editing-canceled", self._on_editing_canceled)

        self.view.connect("row-activated", self._on_row_activated)
        self.view.connect("key-press-event", self._on_key_press)
        self.view.connect("button-press-event", self._on_button_press)
        self.view.enable_model_drag_source(
            Gdk.ModifierType.BUTTON1_MASK,
            [Gtk.TargetEntry.new("text/uri-list", 0, 0)],
            Gdk.DragAction.COPY)
        self.view.connect("drag-data-get", self._on_drag_data_get)
        # Finder style: only the focused column shows its selection in the
        # accent colour; the other columns fall back to a neutral grey.
        self.view.get_style_context().add_class("inactive")
        self.view.connect("focus-in-event", self._on_focus_change, False)
        self.view.connect("focus-out-event", self._on_focus_change, True)

        self.sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                     vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.sw.add(self.view)
        self.sw.set_size_request(width, -1)

        self.message = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                                 margin=12)
        self.message.get_style_context().add_class("dim-label")
        # Same "view" background as the list, so an empty column still reads
        # as a column next to the sidebar instead of blending into the window.
        message_bg = Gtk.EventBox()
        message_bg.get_style_context().add_class("view")
        message_bg.add(self.message)
        message_bg.connect("button-press-event", self._on_message_press)
        self.stack = Gtk.Stack()
        self.stack.add_named(self.sw, "list")
        self.stack.add_named(message_bg, "message")
        self.pack_start(self.stack, True, True, 0)

        # resize handle
        self.handle = Gtk.EventBox()
        self.handle.set_size_request(8, -1)
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

        try:
            self.monitor = gfile.monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.monitor.connect("changed", self._on_monitor_changed)
        except GLib.Error:
            self.monitor = None

        self.connect("destroy", self._on_destroy)

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
        current, _info = self.selected()
        self._enumerate(current, emit=False)
        return False

    def select_and_rename(self, gfile):
        """Reload with `gfile` selected, then open the inline rename editor."""
        def on_loaded(col):
            col.disconnect(handler)
            col.view.grab_focus()
            # Let the tree view validate the new rows before editing starts.
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
        rows = []
        for info in infos:
            if not self.show_hidden and (info.get_is_hidden() or info.get_is_backup()):
                continue
            is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
            name = info.get_display_name() or info.get_name()
            rows.append((not is_dir, natural_key(name), info, is_dir, name))
        rows.sort(key=lambda r: (r[0], r[1]))

        # Rebuild silently; selection signals are emitted explicitly below.
        self._quiet = True
        self.store.clear()
        for _is_file, _key, info, is_dir, name in rows:
            child = self.gfile.get_child(info.get_name())
            self.store.append([info.get_icon(), name, is_dir, child, info])
        found = None
        if select is not None:
            for row in self.store:
                if row[C_FILE].equal(select):
                    self.view.set_cursor(row.path, None, False)
                    found = (row[C_FILE], row[C_INFO])
                    break
        self._quiet = False
        self.item_count = len(rows)

        if not rows:
            self._show_message(_("Empty folder"))
        else:
            self.stack.set_visible_child_name("list")

        if found is not None:
            if emit:
                self.emit("item-selected", *found)
        elif select is not None and not emit:
            # the previously selected entry disappeared: drop columns to the right
            self.emit("item-selected", None, None)
        if getattr(self, "_focus_after_load", False) and rows:
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

    def selected(self):
        model, it = self.view.get_selection().get_selected()
        if it is None:
            return None, None
        return model[it][C_FILE], model[it][C_INFO]

    # -- rename --------------------------------------------------------------
    def start_rename(self):
        model, it = self.view.get_selection().get_selected()
        if it is None or self.in_trash():
            return
        self._renaming = True
        self.name_renderer.set_property("editable", True)
        # Without an explicit focus cell the cell area may try to activate
        # the (inert) icon or chevron renderer and silently do nothing.
        self.name_col.focus_cell(self.name_renderer)
        self.view.set_cursor(model.get_path(it), self.name_col, True)

    def _on_editing_started(self, renderer, editable, path):
        row = self.store[path]
        if row[C_ISDIR] or not isinstance(editable, Gtk.Entry):
            return
        # Like Finder: preselect the name without its extension.
        stem, ext = os.path.splitext(row[C_NAME])
        if stem and ext:
            # The entry selects everything when it grabs focus, which happens
            # after this signal; narrow the selection once that is done.
            GLib.idle_add(editable.select_region, 0, len(stem))

    def _on_editing_canceled(self, renderer):
        self._renaming = False
        self.name_renderer.set_property("editable", False)

    def _on_name_edited(self, renderer, path, new_name):
        self._renaming = False
        self.name_renderer.set_property("editable", False)
        row = self.store[path]
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

    # -- trash / delete ------------------------------------------------------
    def in_trash(self):
        return self.gfile.get_uri_scheme() == "trash"

    def is_trash_root(self):
        return self.in_trash() and self.gfile.get_parent() is None

    def _neighbour_of(self, gfile):
        """The row to select once `gfile` is gone: the next one, else the previous."""
        for i, row in enumerate(self.store):
            if row[C_FILE].equal(gfile):
                if i + 1 < len(self.store):
                    return self.store[i + 1][C_FILE]
                if i > 0:
                    return self.store[i - 1][C_FILE]
                return None
        return None

    def trash_item(self, gfile=None):
        gfile = gfile or self.selected()[0]
        if gfile is None:
            return
        if self.in_trash():
            # Already in the trash: the only way out is restore or delete.
            if self.is_trash_root():
                self.delete_item(gfile)
            return
        neighbour = self._neighbour_of(gfile)
        try:
            gfile.trash(None)
        except GLib.Error as err:
            show_error(self.get_toplevel(), _("Could not move to trash"), err.message)
            return
        self._enumerate(neighbour, emit=True)

    def delete_item(self, gfile=None):
        gfile = gfile or self.selected()[0]
        if gfile is None:
            return
        name = gfile.get_basename()
        if not confirm(self.get_toplevel(), _("Permanently delete “%s”?") % name,
                       _("This item will be deleted immediately, bypassing the trash. "
                         "This cannot be undone."),
                       _("_Delete")):
            return
        neighbour = self._neighbour_of(gfile)
        try:
            if self.is_trash_root():
                gfile.delete(None)      # the trash backend removes the whole item
            else:
                delete_recursive(gfile)
        except GLib.Error as err:
            show_error(self.get_toplevel(), _("Could not delete"), err.message)
        self._enumerate(neighbour, emit=True)

    def restore_item(self, gfile=None):
        """Move a top-level trash entry back to where it was deleted from."""
        gfile = gfile or self.selected()[0]
        if gfile is None or not self.is_trash_root():
            return
        info = None
        for row in self.store:
            if row[C_FILE].equal(gfile):
                info = row[C_INFO]
                break
        orig = info.get_attribute_byte_string("trash::orig-path") if info else None
        if not orig:
            show_error(self.get_toplevel(), _("Could not restore"),
                       _("The original location of this item is unknown."))
            return
        dest = Gio.File.new_for_path(orig)
        neighbour = self._neighbour_of(gfile)
        try:
            parent = dest.get_parent()
            if parent is not None and not parent.query_exists(None):
                parent.make_directory_with_parents(None)
            gfile.move(dest, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, None, None)
        except GLib.Error as err:
            show_error(self.get_toplevel(), _("Could not restore to %s") % orig, err.message)
            return
        self._enumerate(neighbour, emit=True)

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

    def select_first(self):
        if len(self.store) and self.selected()[0] is None:
            self.view.set_cursor(Gtk.TreePath.new_first(), None, False)

    # -- monitor -------------------------------------------------------------
    def _on_monitor_changed(self, monitor, f, other, event):
        if event in (Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.DELETED,
                     Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.MOVED_OUT,
                     Gio.FileMonitorEvent.RENAMED,
                     Gio.FileMonitorEvent.CHANGES_DONE_HINT):
            if self._reload_id:
                GLib.source_remove(self._reload_id)
            self._reload_id = GLib.timeout_add(400, self._reload_keep_selection)

    # -- events --------------------------------------------------------------
    def _on_selection_changed(self, selection):
        if self._quiet:
            return
        gfile, info = self.selected()
        self.emit("item-selected", gfile, info)

    def _on_focus_change(self, view, _event, inactive):
        ctx = view.get_style_context()
        if inactive:
            ctx.add_class("inactive")
        else:
            ctx.remove_class("inactive")
        return False

    def _on_row_activated(self, view, path, column):
        row = self.store[path]
        self.emit("item-activated", row[C_FILE], row[C_INFO])

    def _on_key_press(self, view, event):
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
            gfile, info = self.selected()
            if gfile is not None:
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

    def _on_button_press(self, view, event):
        if event.button != 3:
            return False
        hit = view.get_path_at_pos(int(event.x), int(event.y))
        if hit is None:
            self.emit("context-menu", None, None, event)
        else:
            path = hit[0]
            view.set_cursor(path, None, False)
            row = self.store[path]
            self.emit("context-menu", row[C_FILE], row[C_INFO], event)
        return True

    def _on_drag_data_get(self, view, ctx, data, info, time):
        gfile, _info = self.selected()
        if gfile is not None:
            data.set_uris([gfile.get_uri()])

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

    def _on_destroy(self, *_args):
        self._gen += 1
        if self._reload_id:
            GLib.source_remove(self._reload_id)
            self._reload_id = 0
        if self.monitor is not None:
            self.monitor.cancel()

