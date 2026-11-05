"""The main window: two-row title bar, sidebar, the strip of columns, status line."""
import os
import subprocess

from gi.repository import Gdk, Gio, GLib, Gtk

from . import DEFAULT_COL_WIDTH, PREVIEW_EXTRA, _, ngettext
from .column import Column
from .preview import Preview
from .sidebar import PlacesSidebar
from .util import launch_default, save_state, show_error


class FinchWindow(Gtk.ApplicationWindow):
    def __init__(self, app, root, state):
        super().__init__(application=app, title="Finch")
        self.get_style_context().add_class("finch-main")
        self.state = state
        self.col_width = int(state.get("column_width", DEFAULT_COL_WIDTH))
        self.show_hidden = bool(state.get("show_hidden", False))
        self.set_default_size(int(state.get("width", 1100)),
                              int(state.get("height", 650)))
        self.set_icon_name("system-file-manager")
        self.columns = []
        self.preview = None

        # Title bar in two rows, Finder style: a normal-height header bar with
        # the folder name and the window buttons, then a toolbar row below it
        # with the up button, the editable path and the menu.
        # Both rows are header bars so the theme paints them alike; style.css
        # removes the border between them.
        self.header = Gtk.HeaderBar(show_close_button=True, has_subtitle=False)
        self.header.get_style_context().add_class("title-row")
        toolbar = Gtk.HeaderBar(show_close_button=False, has_subtitle=False)
        toolbar.get_style_context().add_class("tool-row")
        titlebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        titlebar.pack_start(self.header, False, False, 0)
        titlebar.pack_start(toolbar, False, False, 0)
        self.set_titlebar(titlebar)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        nav.get_style_context().add_class("linked")
        for icon, tip, action in (("go-previous-symbolic", _("Back (Alt+←)"), "win.back"),
                                  ("go-next-symbolic", _("Forward (Alt+→)"), "win.forward"),
                                  ("go-up-symbolic", _("Up (Alt+↑)"), "win.up")):
            btn = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            btn.set_tooltip_text(tip)
            btn.set_action_name(action)
            nav.pack_start(btn, False, False, 0)
        toolbar.pack_start(nav)
        self.path_entry = Gtk.Entry(hexpand=True, placeholder_text=_("Path"))
        self.path_entry.connect("activate", self._on_path_activate)
        toolbar.set_custom_title(self.path_entry)

        menu = Gio.Menu()
        menu.append(_("Show Hidden Files"), "win.show-hidden")
        menu.append(_("Reload"), "win.reload")
        menu.append(_("Open in Nemo"), "win.open-nemo")
        menu.append(_("Open in Terminal"), "win.open-terminal")
        new_folder = Gtk.Button.new_from_icon_name("folder-new-symbolic", Gtk.IconSize.BUTTON)
        new_folder.set_tooltip_text(_("New Folder (Ctrl+Shift+N)"))
        new_folder.set_action_name("win.new-folder")
        # A plain drop-down menu (check marks on the left) rather than the
        # popover with an arrow that a menu model would give. GtkMenuButton
        # reads the *menu's* halign to decide which edge to hang it from:
        # END keeps it inside the window.
        self.app_menu = Gtk.Menu.new_from_model(menu)
        self.app_menu.set_halign(Gtk.Align.END)
        # Never pop up with a row still highlighted from the previous use.
        self.app_menu.connect("show", lambda m: m.deselect())
        menu_btn = Gtk.MenuButton()
        menu_btn.set_popup(self.app_menu)
        menu_btn.add(Gtk.Image.new_from_icon_name("open-menu-symbolic",
                                                  Gtk.IconSize.BUTTON))
        toolbar.pack_end(menu_btn)
        toolbar.pack_end(new_folder)

        # actions
        self._up_action = self._add_action("up", self.go_up)
        self._history, self._hist_pos = [], -1
        self._back_action = self._add_action("back", self.go_back)
        self._forward_action = self._add_action("forward", self.go_forward)
        self._add_action("reload", self.reload_all)
        self._add_action("focus-path", lambda *_args: self.path_entry.grab_focus())
        self._add_action("open-nemo", lambda *_args: self.open_in_nemo(self.current_dir()))
        self._terminal_action = self._add_action(
            "open-terminal", lambda *_args: self.open_terminal(self.current_dir()))
        self._new_folder_action = self._add_action("new-folder", self.new_folder)
        self._update_nav_actions()
        hidden = Gio.SimpleAction.new_stateful(
            "show-hidden", None, GLib.Variant.new_boolean(self.show_hidden))
        hidden.connect("change-state", self._on_toggle_hidden)
        self.add_action(hidden)

        # body
        self.sidebar = PlacesSidebar()
        self.sidebar.connect("open-location", lambda sb, loc, flags: self.set_root(loc))
        self.sidebar.connect("show-error-message",
                             lambda sb, p, s: show_error(self, p, s))

        self.colbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.hsw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                      vscrollbar_policy=Gtk.PolicyType.NEVER)
        self.hsw.add(self.colbox)
        self.hsw.connect("size-allocate", self._on_strip_allocated)
        # Keep the right end in view across window resizes while it was in view.
        self._at_end = True
        self.hsw.get_hadjustment().connect("value-changed", self._on_strip_scrolled)

        self.status = Gtk.Label(xalign=0.0, margin_start=12, margin_end=12,
                                margin_top=3, margin_bottom=3)
        self.status.get_style_context().add_class("dim-label")
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right.pack_start(self.hsw, True, True, 0)
        right.pack_start(Gtk.Separator(), False, False, 0)
        right.pack_start(self.status, False, False, 0)

        paned = Gtk.Paned()
        paned.pack1(self.sidebar, False, False)
        paned.pack2(right, True, False)
        paned.set_position(int(state.get("sidebar_width", 180)))
        self.paned = paned
        self.add(paned)

        self.connect("delete-event", self._on_delete)
        self.set_root(root)

    # -- helpers -------------------------------------------------------------
    def _add_action(self, name, cb):
        act = Gio.SimpleAction.new(name, None)
        act.connect("activate", lambda *_args: cb())
        self.add_action(act)
        return act

    def current_dir(self):
        """Deepest directory column that has keyboard focus, else the last one."""
        for col in reversed(self.columns):
            if col.view.has_focus():
                return col.gfile
        return self.columns[-1].gfile if self.columns else None

    # -- navigation ----------------------------------------------------------
    def set_root(self, gfile, select=None, focus=False, record=True):
        if record:
            # Drop any forward entries, then append (skipping duplicates).
            del self._history[self._hist_pos + 1:]
            if not self._history or not self._history[-1].equal(gfile):
                self._history.append(gfile)
            self._hist_pos = len(self._history) - 1
            self._update_nav_actions()
        for col in self.columns:
            col.destroy()
        self.columns = []
        self._remove_preview()
        self.add_column(gfile, select=select, focus=focus)
        self._update_path(gfile)
        self._update_nav_actions()
        self.sidebar.sync_location(gfile)

    def add_column(self, gfile, select=None, focus=False):
        col = Column(gfile, self.col_width, self.show_hidden)
        col.connect("item-selected", self._on_item_selected)
        col.connect("item-activated", self._on_item_activated)
        col.connect("width-changed", self._on_width_changed)
        col.connect("key-nav", self._on_key_nav)
        col.connect("context-menu", self._on_context_menu)
        col.connect("loaded", lambda *_args: self._update_status())
        self.colbox.pack_start(col, False, False, 0)
        col.show_all()
        self.columns.append(col)
        col.load(select=select, focus=focus)
        self._scroll_to_end_when_allocated(col)
        return col

    def truncate_after(self, col):
        idx = self.columns.index(col)
        for extra in self.columns[idx + 1:]:
            extra.destroy()
        self.columns = self.columns[:idx + 1]
        self._remove_preview()

    def _remove_preview(self):
        if self.preview is not None:
            self.preview.destroy()
            self.preview = None

    def _on_strip_allocated(self, *_args):
        self._fit_preview()
        if self._at_end:
            GLib.idle_add(self._scroll_to_end)

    def _on_strip_scrolled(self, adj):
        self._at_end = adj.get_value() + adj.get_page_size() >= adj.get_upper() - 1

    def _fit_preview(self):
        """Tell the preview how much of the strip the columns leave to it."""
        if self.preview is None:
            return
        used = sum(col.get_allocated_width() for col in self.columns)
        self.preview.set_available(self.hsw.get_allocated_width() - used)

    def new_folder(self):
        """Create an untitled folder in the focused column and rename it."""
        col = None
        for c in reversed(self.columns):
            if c.view.has_focus():
                col = c
                break
        col = col or (self.columns[-1] if self.columns else None)
        if col is None or col.in_trash():
            return
        base = _("Untitled Folder")
        child = col.gfile.get_child(base)
        n = 2
        while child.query_exists(None):
            child = col.gfile.get_child(f"{base} {n}")
            n += 1
        try:
            child.make_directory(None)
        except GLib.Error as err:
            show_error(self, _("Could not create folder"), err.message)
            return
        col.select_and_rename(child)

    def go_back(self):
        if self._hist_pos > 0:
            self._hist_pos -= 1
            self._update_nav_actions()
            self.set_root(self._history[self._hist_pos], record=False)

    def go_forward(self):
        if self._hist_pos + 1 < len(self._history):
            self._hist_pos += 1
            self._update_nav_actions()
            self.set_root(self._history[self._hist_pos], record=False)

    def _update_nav_actions(self):
        self._back_action.set_enabled(self._hist_pos > 0)
        self._forward_action.set_enabled(self._hist_pos + 1 < len(self._history))
        root = self.columns[0].gfile if self.columns else None
        self._up_action.set_enabled(root is not None and root.get_parent() is not None)
        self._terminal_action.set_enabled(root is not None and root.get_path() is not None)
        self._new_folder_action.set_enabled(
            root is not None and root.get_uri_scheme() not in ("trash", "recent", "network", "computer"))

    def go_up(self):
        if not self.columns:
            return
        old_root = self.columns[0].gfile
        parent = old_root.get_parent()
        if parent is None:
            return
        self.set_root(parent, select=old_root, focus=True)

    def reload_all(self):
        for col in self.columns:
            col._reload_keep_selection()
        self.sidebar.refresh_usage()


    def _scroll_to_end_when_allocated(self, widget):
        def on_alloc(w, alloc):
            w.disconnect(handler_id)
            GLib.idle_add(self._scroll_to_end)
        handler_id = widget.connect("size-allocate", on_alloc)

    def _scroll_to_end(self):
        adj = self.hsw.get_hadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _update_path(self, gfile):
        text = gfile.get_parse_name()
        self.path_entry.set_text(text)
        self.path_entry.set_position(-1)
        name = gfile.get_basename() or text
        if gfile.get_parent() is None:
            name = {"trash": _("Trash"), "recent": _("Recent"), "network": _("Network"),
                    "computer": _("Computer")}.get(gfile.get_uri_scheme(), name)
        self.header.set_title(name)
        self.set_title(name)

    def _update_status(self):
        col = None
        for c in reversed(self.columns):
            if c.view.has_focus():
                col = c
                break
        col = col or (self.columns[-1] if self.columns else None)
        if col is None:
            return
        parts = [ngettext("%d item", "%d items", col.item_count) % col.item_count]
        gfile, info = col.selected()
        if info is not None and info.get_file_type() != Gio.FileType.DIRECTORY:
            parts.append(GLib.format_size(info.get_size()))
        try:
            fs = col.gfile.query_filesystem_info("filesystem::free", None)
            if fs.has_attribute("filesystem::free") and not col.in_trash():
                parts.append(_("%s free")
                             % GLib.format_size(fs.get_attribute_uint64("filesystem::free")))
        except GLib.Error:
            pass
        # TRANSLATORS: separator between the parts of the status line
        self.status.set_text(_(", ").join(parts))

    # -- signal handlers -----------------------------------------------------
    def _on_item_selected(self, col, gfile, info):
        self.truncate_after(col)
        if gfile is None:
            self._update_path(col.gfile)
        elif info.get_file_type() == Gio.FileType.DIRECTORY:
            self.add_column(gfile)
            self._update_path(gfile)
        else:
            self.preview = Preview(self.col_width + PREVIEW_EXTRA)
            self.preview.set_file(gfile, info)
            # Takes whatever is left of the strip; its content stays centred.
            self.colbox.pack_start(self.preview, True, True, 0)
            self.preview.show_all()
            self._fit_preview()
            self._scroll_to_end_when_allocated(self.preview)
            self._update_path(gfile)
        self._update_status()

    def _on_item_activated(self, col, gfile, info):
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            self._on_key_nav(col, "right")
        else:
            launch_default(gfile, self)

    def _on_key_nav(self, col, direction):
        idx = self.columns.index(col)
        if direction == "left" and idx > 0:
            self.columns[idx - 1].view.grab_focus()
        elif direction == "right" and idx + 1 < len(self.columns):
            nxt = self.columns[idx + 1]
            if nxt.item_count == 0:
                return  # never park the focus inside an empty column
            nxt.view.grab_focus()
            nxt.select_first()
        elif direction == "up":
            self.go_up()
        self._update_status()

    def _on_width_changed(self, col, width, apply_all):
        if not apply_all:
            return
        # Shift-drag: every column follows and the width becomes the default.
        self.col_width = width
        for other in self.columns:
            if other is not col:
                other.sw.set_size_request(width, -1)
        if self.preview is not None:
            self.preview.set_width(width + PREVIEW_EXTRA)

    def _on_toggle_hidden(self, action, value):
        action.set_state(value)
        self.show_hidden = value.get_boolean()
        for col in self.columns:
            col.set_show_hidden(self.show_hidden)

    def _on_path_activate(self, entry):
        text = entry.get_text().strip()
        if not text:
            return
        gfile = Gio.File.new_for_commandline_arg(os.path.expanduser(text))
        try:
            info = gfile.query_info("standard::type", Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error as err:
            show_error(self, _("Could not open path"), err.message)
            return
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            self.set_root(gfile)
        else:
            self.set_root(gfile.get_parent() or gfile, select=gfile)
        if self.columns:
            self.columns[0].view.grab_focus()

    def _on_context_menu(self, col, gfile, info, event):
        menu = Gtk.Menu()
        is_dir = gfile is not None and info.get_file_type() == Gio.FileType.DIRECTORY
        target_dir = gfile if (gfile is None or is_dir) else None

        def item(label, cb):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda *_args: cb())
            menu.append(mi)

        if col.in_trash():
            # Trash: items can only be restored or destroyed.
            if gfile is not None:
                item(_("Open"), lambda: launch_default(gfile, self))
                item(_("Open With…"), lambda: self.open_with(gfile))
                if col.is_trash_root():
                    menu.append(Gtk.SeparatorMenuItem())
                    item(_("Restore"), lambda: col.restore_item(gfile))
                    item(_("Delete Permanently"), lambda: col.delete_item(gfile))
            else:
                item(_("Open in Nemo"), lambda: self.open_in_nemo(col.gfile))
                if col.is_trash_root():
                    item(_("Empty Trash"), col.empty_trash)
        elif gfile is not None:
            item(_("Open"), lambda: launch_default(gfile, self))
            item(_("Open With…"), lambda: self.open_with(gfile))
            menu.append(Gtk.SeparatorMenuItem())
            item(_("Rename"), col.start_rename)
            menu.append(Gtk.SeparatorMenuItem())
            if is_dir:
                item(_("Open in Nemo"), lambda: self.open_in_nemo(gfile))
                if gfile.get_path():
                    item(_("Open in Terminal"), lambda: self.open_terminal(gfile))
            else:
                item(_("Show in Nemo"), lambda: self.open_in_nemo(gfile))
            item(_("Copy Path"), lambda: self.copy_path(gfile))
            menu.append(Gtk.SeparatorMenuItem())
            item(_("Move to Trash"), lambda: col.trash_item(gfile))
            item(_("Delete"), lambda: col.delete_item(gfile))
        else:
            item(_("Open in Nemo"), lambda: self.open_in_nemo(col.gfile))
            if col.gfile.get_path():
                item(_("Open in Terminal"), lambda: self.open_terminal(col.gfile))
            item(_("Copy Path"), lambda: self.copy_path(col.gfile))
        menu.show_all()
        menu.attach_to_widget(col.view, None)
        menu.popup_at_pointer(event)

    def _on_delete(self, *_args):
        self.save_window_state()
        return False

    def save_window_state(self):
        w, h = self.get_size()
        self.state.update({
            "width": w, "height": h, "column_width": self.col_width,
            "show_hidden": self.show_hidden,
            "sidebar_width": self.paned.get_position(),
            "last_root": self.columns[0].gfile.get_uri() if self.columns else None,
        })
        save_state(self.state)

    # -- external apps -------------------------------------------------------
    def open_with(self, gfile):
        dlg = Gtk.AppChooserDialog.new(self, Gtk.DialogFlags.MODAL, gfile)
        if dlg.run() == Gtk.ResponseType.OK:
            app_info = dlg.get_app_info()
            if app_info is not None:
                ctx = self.get_display().get_app_launch_context()
                try:
                    app_info.launch([gfile], ctx)
                except GLib.Error as err:
                    show_error(self, _("Could not open"), err.message)
        dlg.destroy()

    def open_in_nemo(self, gfile):
        if gfile is None:
            return
        if GLib.find_program_in_path("nemo"):
            # `nemo <file>` opens the parent folder with the file selected
            subprocess.Popen(["nemo", gfile.get_uri()])
        else:
            target = gfile
            try:
                info = gfile.query_info("standard::type", Gio.FileQueryInfoFlags.NONE, None)
                if info.get_file_type() != Gio.FileType.DIRECTORY:
                    target = gfile.get_parent() or gfile
            except GLib.Error:
                pass
            launch_default(target, self)

    def open_terminal(self, gfile):
        path = gfile.get_path() if gfile is not None else None
        if not path:
            show_error(self, _("Could not open terminal"),
                       _("Only local folders can be opened in a terminal."))
            return
        if GLib.find_program_in_path("gnome-terminal"):
            subprocess.Popen(["gnome-terminal", f"--working-directory={path}"])
        else:
            subprocess.Popen(["x-terminal-emulator"], cwd=path)

    def copy_path(self, gfile):
        text = gfile.get_path() or gfile.get_uri()
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
