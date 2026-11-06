"""One directory listing: a ListView inside a ScrolledWindow plus a drag handle."""
import os

import cairo
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from . import ATTRS, C_FILE, C_ICON, C_INFO, C_ISDIR, C_NAME, MIN_COL_WIDTH, _, ngettext
from .util import confirm, delete_recursive, natural_key, show_error

URI_LIST = "text/uri-list"
_DND_TARGETS = [Gtk.TargetEntry.new(URI_LIST, 0, 0)]
# Files being dragged out of one of our own columns (empty for drags that
# come from other programs); lets a column refuse pointless drops.
_dragging = []


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
        # ([gfiles], [infos]) when several rows are selected at once
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
        "item-activated": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
        # (new width, apply to every column?)  Shift-drag resizes all columns.
        "width-changed": (GObject.SignalFlags.RUN_FIRST, None, (int, bool)),
        "key-nav": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "context-menu": (GObject.SignalFlags.RUN_FIRST, None, (object, object, object)),
        "loaded": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # (files, directory, move?) dropped onto this column
        "files-dropped": (GObject.SignalFlags.RUN_FIRST, None, (object, object, bool)),
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
        # Several rows may be selected (Ctrl / Shift click, Shift + arrows,
        # Ctrl+A); a single selection drives the navigation, a wider one is
        # just a set of items to act on. No rubber banding: with it GtkTreeView
        # turns a drag that starts on an unselected row into a rubber band
        # instead of dragging the row.
        sel = self.view.get_selection()
        sel.set_mode(Gtk.SelectionMode.MULTIPLE)
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
        self.view.connect("button-release-event", self._on_button_release)
        self.view.connect("motion-notify-event", self._on_motion)
        # A plain press on one of several selected rows, held back until it
        # turns out to be a click or a drag (see _on_button_press).
        self._press = None
        self._drag_row = None
        # Drag source: the selected row leaves as a URI list, to another
        # column, the sidebar or another program.
        self.view.enable_model_drag_source(
            Gdk.ModifierType.BUTTON1_MASK, _DND_TARGETS,
            Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        self.view.connect("drag-begin", self._on_drag_begin)
        self.view.connect_after("drag-begin", self._on_drag_begin_icon)
        self.view.connect("drag-end", self._on_drag_end)
        self.view.connect("drag-data-get", self._on_drag_data_get)
        self.view.connect("drag-data-delete", self._on_drag_data_delete)
        # Drop target: files land in the directory row under the pointer, or
        # in this column's directory. GtkTreeView's own model-row DnD is not
        # used (it would insert and delete rows itself); only the plain
        # widget drop signals are, and the view's handlers are kept out.
        self.view.drag_dest_set(0, _DND_TARGETS,
                                Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        self.view.connect("drag-motion", self._on_drag_motion)
        self.view.connect("drag-leave", self._on_drag_leave)
        self.view.connect("drag-drop", self._on_drag_drop)
        self.view.connect("drag-data-received", self._on_drag_data_received)
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
            rows.append((not is_dir, natural_key(name), info, is_dir, name))
        rows.sort(key=lambda r: (r[0], r[1]))

        # Rebuild silently; selection signals are emitted explicitly below.
        self._quiet = True
        self.store.clear()
        for _is_file, _key, info, is_dir, name in rows:
            child = self.gfile.get_child(info.get_name())
            self.store.append([info.get_icon(), name, is_dir, child, info])
        found = []
        for row in self.store:
            if any(row[C_FILE].equal(want) for want in selects):
                if not found:
                    self.view.set_cursor(row.path, None, False)
                else:
                    self.view.get_selection().select_path(row.path)
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

    def selected_all(self):
        """Every selected row, as two lists: ([gfiles], [infos])."""
        model, paths = self.view.get_selection().get_selected_rows()
        rows = [model[path] for path in paths]
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

    def select_first(self):
        if len(self.store) and not self.selected_all()[0]:
            self.view.set_cursor(Gtk.TreePath.new_first(), None, False)

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
    def _on_selection_changed(self, selection):
        if self._quiet:
            return
        gfiles, infos = self.selected_all()
        if len(gfiles) > 1:
            self.emit("selection-changed", gfiles, infos)
        else:
            self.emit("item-selected", gfiles[0] if gfiles else None,
                      infos[0] if infos else None)

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
        if event.button != 3:
            return False
        if hit is None:
            self.emit("context-menu", None, None, event)
        else:
            path = hit[0]
            # Right-clicking one of several selected rows keeps the selection
            # (the menu acts on all of them); elsewhere it selects that row.
            if not view.get_selection().path_is_selected(path):
                view.set_cursor(path, None, False)
            row = self.store[path]
            self.emit("context-menu", row[C_FILE], row[C_INFO], event)
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
                Gtk.TargetList.new(_DND_TARGETS),
                Gdk.DragAction.MOVE | Gdk.DragAction.COPY, 1, event,
                int(event.x), int(event.y))
        return True

    # -- drag and drop -------------------------------------------------------
    def _on_drag_begin(self, view, ctx):
        _dragging[:] = self.selected_all()[0]

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
        """Can files be dropped into this column's directory at all? The
        trash root takes them (into the trash); folders inside the trash and
        the virtual locations do not."""
        if self.in_trash():
            return self.is_trash_root()
        return self.gfile.get_uri_scheme() not in ("recent", "computer", "network")

    def drop_target(self, x, y):
        """Where a drop at widget position (x, y) would go: (directory, row
        path of the folder it lands in or None for this column's directory),
        or (None, None) when a drop here makes no sense."""
        if not self.accepts_drops():
            return None, None
        dest, path = self.gfile, None
        bx, by = self.view.convert_widget_to_bin_window_coords(int(x), int(y))
        hit = self.view.get_path_at_pos(bx, by)
        if hit is not None and not self.in_trash():
            row = self.store[hit[0]]
            if row[C_ISDIR]:
                dest, path = row[C_FILE], hit[0]
        for src in _dragging:              # one of our own: refuse no-ops
            if dest.equal(src) or dest.has_prefix(src):
                return None, None          # into itself
            parent = src.get_parent()
            if parent is not None and parent.equal(dest) and not self.in_trash():
                return None, None          # where it already is
        return dest, path

    def _on_drag_motion(self, view, ctx, x, y, time):
        dest, path = self.drop_target(x, y)
        if dest is None:
            Gdk.drag_status(ctx, 0, time)
            self._on_drag_leave(view, ctx, time)
            return True
        Gdk.drag_status(ctx, drop_action(ctx, view), time)
        view.set_drag_dest_row(path, Gtk.TreeViewDropPosition.INTO_OR_BEFORE)
        ctx_class = view.get_style_context()
        if path is None:
            ctx_class.add_class("drop-target")
        else:
            ctx_class.remove_class("drop-target")
        return True

    def _on_drag_leave(self, view, ctx, time):
        view.set_drag_dest_row(None, Gtk.TreeViewDropPosition.INTO_OR_BEFORE)
        view.get_style_context().remove_class("drop-target")

    def _on_drag_drop(self, view, ctx, x, y, time):
        dest, _path = self.drop_target(x, y)
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

