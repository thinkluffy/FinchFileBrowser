"""The main window: two-row title bar, sidebar, tabs of columns, status line."""
import os
import subprocess

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from . import DEFAULT_COL_WIDTH, VERSION, _, clipboard, ngettext
from .column import SORT_MODES, drop_action
from .pathbar import PathBar
from .sidebar import PlacesSidebar
from .archive import (Archive, Extract, SevenZip, ask_archive, ask_password,
                      is_archive, needs_sevenzip)
from .tab import Tab, location_name
from .transfer import Transfer
from .util import confirm, launch_default, open_label, save_state, show_error


VIEW_MODES = ("columns", "grid")


class TabStrip(Gtk.Notebook):
    """Notebook that takes a dragged tab only on its strip of tab labels.

    GtkNotebook accepts a tab of its group anywhere on itself, so a tab
    dropped on the page below the strip would just be re-inserted; here such
    a drop is refused, the drag ends with no target and GTK asks for a new
    window ("create-window"), like a drop outside every window."""

    TAB_TARGET = "GTK_NOTEBOOK_TAB"

    def over_strip(self, x, y):
        """Is the point (x, y), in this widget's coordinates, on the tab
        strip rather than on the current page?"""
        if not self.get_show_tabs():
            return False
        page = self.get_nth_page(self.get_current_page())
        if page is None:
            return True
        top = page.translate_coordinates(self, 0, 0)
        return top is None or y < top[1]

    def do_drag_motion(self, ctx, x, y, time):
        if (any(t.name() == self.TAB_TARGET for t in ctx.list_targets())
                and not self.over_strip(x, y)):
            Gdk.drag_status(ctx, 0, time)
            return True
        return Gtk.Notebook.do_drag_motion(self, ctx, x, y, time)


class FinchWindow(Gtk.ApplicationWindow):
    """`root`: the folder of the first tab, or None for a window that is
    about to receive a tab dragged out of another one."""

    def __init__(self, app, root, state):
        super().__init__(application=app, title="Finch")
        self.get_style_context().add_class("finch-main")
        self.state = state
        self.col_width = int(state.get("column_width", DEFAULT_COL_WIDTH))
        self.show_hidden = bool(state.get("show_hidden", False))
        self.sort_by = state.get("sort_by", "name")
        if self.sort_by not in SORT_MODES:
            self.sort_by = "name"
        self.sort_reversed = bool(state.get("sort_reversed", False))
        self.view_mode = state.get("view_mode", "columns")
        if self.view_mode not in VIEW_MODES:
            self.view_mode = "columns"
        self.set_default_size(int(state.get("width", 1100)),
                              int(state.get("height", 650)))
        self.set_icon_name("system-file-manager")

        # Title bar in two rows, Finder style: a normal-height header bar with
        # the folder name and the window buttons, then a toolbar row below it
        # with the up button, the path bar and the menu.
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
        # The path as folder buttons or, toggled, as text (the toggle's
        # state is kept between runs; Ctrl+L shows the text just once).
        self.pathbar = PathBar(entry_mode=bool(state.get("location_entry", False)))
        self.path_entry = self.pathbar.entry
        self.pathbar.connect("path-entered", self._on_path_entered)
        self.pathbar.connect("crumb-clicked", self._on_crumb_clicked)
        self.pathbar.connect("entry-left", lambda bar: self._focus_files())
        toolbar.set_custom_title(self.pathbar)

        menu = Gio.Menu()
        tabs = Gio.Menu()
        tabs.append(_("New Tab"), "win.new-tab")
        menu.append_section(None, tabs)
        edit = Gio.Menu()
        edit.append(_("Cut"), "win.cut")
        edit.append(_("Copy"), "win.copy")
        edit.append(_("Paste"), "win.paste")
        menu.append_section(None, edit)
        view = Gio.Menu()
        # Shared by the app menu and every context menu: radio items for the
        # modes, a check item for the direction, all driven by the actions.
        sort = Gio.Menu()
        for mode, label in (("name", _("Name")), ("modified", _("Date Modified")),
                            ("size", _("Size")), ("kind", _("Kind"))):
            sort.append(label, "win.sort-by::" + mode)
        reverse = Gio.Menu()
        reverse.append(_("Reversed Order"), "win.sort-reversed")
        sort.append_section(None, reverse)
        self._sort_menu = sort
        view.append_submenu(_("Sort By"), sort)
        view.append(_("Show Hidden Files"), "win.show-hidden")
        view.append(_("Reload"), "win.reload")
        view.append(_("Open in Nemo"), "win.open-nemo")
        view.append(_("Open in Terminal"), "win.open-terminal")
        menu.append_section(None, view)
        about = Gio.Menu()
        about.append(_("About Finch"), "win.about")
        menu.append_section(None, about)
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
        # Columns / icons: two linked toggles on the one stateful action.
        views = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        views.get_style_context().add_class("linked")
        for mode, icon, tip in (("columns", "view-list-symbolic", _("Columns (Ctrl+1)")),
                                ("grid", "view-grid-symbolic", _("Icons (Ctrl+2)"))):
            btn = Gtk.ToggleButton()
            btn.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))
            btn.set_tooltip_text(tip)
            btn.set_action_name("win.view-mode")
            btn.set_action_target_value(GLib.Variant.new_string(mode))
            views.pack_start(btn, False, False, 0)
        toolbar.pack_end(views)

        # actions
        self._up_action = self._add_action("up", self.go_up)
        self._back_action = self._add_action("back", self.go_back)
        self._forward_action = self._add_action("forward", self.go_forward)
        self._add_action("reload", self.reload_all)
        self._add_action("focus-path", lambda *_args: self.pathbar.show_entry())
        self._add_action("open-nemo", lambda *_args: self.open_in_nemo(self.current_dir()))
        self._terminal_action = self._add_action(
            "open-terminal", lambda *_args: self.open_terminal(self.current_dir()))
        self._new_folder_action = self._add_action("new-folder", self.new_folder)
        self._add_action("cut", lambda: self.copy_selection(cut=True))
        self._add_action("copy", self.copy_selection)
        self._paste_action = self._add_action("paste", self.paste)
        self._add_action("new-tab", lambda: self.open_tab(self.current_dir() or self.tab.root))
        self._add_action("about", self.show_about)
        self._add_action("close-tab", self.close_tab)
        self._add_action("next-tab", lambda: self._cycle_tab(1))
        self._add_action("prev-tab", lambda: self._cycle_tab(-1))
        # Paste is offered whenever the clipboard holds files (from us or Nemo).
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).connect(
            "owner-change", lambda *_args: self._update_nav_actions())
        hidden = Gio.SimpleAction.new_stateful(
            "show-hidden", None, GLib.Variant.new_boolean(self.show_hidden))
        hidden.connect("change-state", self._on_toggle_hidden)
        self.add_action(hidden)
        sort_by = Gio.SimpleAction.new_stateful(
            "sort-by", GLib.VariantType.new("s"), GLib.Variant.new_string(self.sort_by))
        sort_by.connect("change-state", self._on_sort_by)
        self.add_action(sort_by)
        sort_rev = Gio.SimpleAction.new_stateful(
            "sort-reversed", None, GLib.Variant.new_boolean(self.sort_reversed))
        sort_rev.connect("change-state", self._on_sort_reversed)
        self.add_action(sort_rev)
        view_mode = Gio.SimpleAction.new_stateful(
            "view-mode", GLib.VariantType.new("s"), GLib.Variant.new_string(self.view_mode))
        view_mode.connect("change-state", self._on_view_mode)
        self.add_action(view_mode)

        # body
        self.sidebar = PlacesSidebar()
        # Middle-click (and the sidebar's own context menu) open a place in
        # a new tab.
        self.sidebar.set_open_flags(Gtk.PlacesOpenFlags.NORMAL | Gtk.PlacesOpenFlags.NEW_TAB)
        self.sidebar.connect("open-location", self._on_sidebar_open)
        self.sidebar.connect("show-error-message",
                             lambda sb, p, s: show_error(self, p, s))
        # Files dropped on a place in the sidebar go there too.
        self.sidebar.connect("drag-action-requested",
                             lambda sb, ctx, dest, files: drop_action(ctx, sb))
        self.sidebar.connect("drag-perform-drop",
                             lambda sb, dest, files, action:
                             self.drop_files(list(files), dest, action == Gdk.DragAction.MOVE))

        # Tabs: the strip of tab labels only appears once there are two.
        # Tabs can be dragged between Finch windows (same group name, onto
        # the strip) and anywhere else, which makes a new window for them.
        self.notebook = TabStrip(show_border=False, scrollable=True, show_tabs=False)
        self.notebook.set_group_name("finch-tabs")
        self.notebook.connect("switch-page", self._on_tab_switched)
        self.notebook.connect("page-added", self._on_page_added)
        self.notebook.connect("page-removed", self._on_page_removed)
        self.notebook.connect("create-window", self._on_tab_detached)

        # Status line: item counts on the left; while files are being copied
        # or moved, a progress bar with a cancel button on the right.
        self.status = Gtk.Label(xalign=0.0, margin_start=12, margin_end=12,
                                margin_top=3, margin_bottom=3, ellipsize=Pango.EllipsizeMode.END)
        self.status.get_style_context().add_class("dim-label")
        self.progress_label = Gtk.Label(xalign=1.0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.progress_label.get_style_context().add_class("dim-label")
        self.progress_bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, width_request=140)
        self.progress_bar.get_style_context().add_class("transfer-progress")
        self.cancel_button = Gtk.Button.new_from_icon_name("process-stop-symbolic",
                                                           Gtk.IconSize.MENU)
        self.cancel_button.set_relief(Gtk.ReliefStyle.NONE)
        self.cancel_button.set_tooltip_text(_("Cancel"))
        self.cancel_button.connect("clicked", lambda *_args: self.cancel_transfer())
        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                                    margin_end=6)
        self.progress_box.pack_start(self.progress_label, True, True, 0)
        self.progress_box.pack_start(self.progress_bar, False, False, 0)
        self.progress_box.pack_start(self.cancel_button, False, False, 0)
        self.progress_box.set_no_show_all(True)     # shown while a job runs
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        status_box.pack_start(self.status, True, True, 0)
        status_box.pack_end(self.progress_box, False, False, 0)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right.pack_start(self.notebook, True, True, 0)
        right.pack_start(Gtk.Separator(), False, False, 0)
        right.pack_start(status_box, False, False, 0)
        # Copy / move jobs run one after another; the first is the one shown.
        self._jobs = []

        paned = Gtk.Paned()
        paned.pack1(self.sidebar, False, False)
        paned.pack2(right, True, False)
        paned.set_position(int(state.get("sidebar_width", 180)))
        self.paned = paned
        self.add(paned)

        self.connect("delete-event", self._on_delete)
        if root is not None:
            self.open_tab(root)

    # -- helpers -------------------------------------------------------------
    def _add_action(self, name, cb):
        act = Gio.SimpleAction.new(name, None)
        act.connect("activate", lambda *_args: cb())
        self.add_action(act)
        return act

    def do_key_press_event(self, event):
        # GtkWindow runs accelerators before the focused widget sees a key, so
        # Ctrl+C/X/V would never reach the path box or the inline rename
        # entry. Let a text entry take the key first; what it declines falls
        # through to the accelerators as usual.
        focus = self.get_focus()
        if isinstance(focus, Gtk.Entry) and focus.event(event):
            return True
        return Gtk.ApplicationWindow.do_key_press_event(self, event)

    # -- tabs ------------------------------------------------------------------
    @property
    def tabs(self):
        return self.notebook.get_children()

    @property
    def tab(self):
        """The tab being looked at."""
        page = self.notebook.get_current_page()
        return self.notebook.get_nth_page(page) if page >= 0 else None

    # The current tab's parts, for code (and tests) that think in one tab.
    @property
    def columns(self):
        return self.tab.columns if self.tab is not None else []

    @property
    def preview(self):
        return self.tab.preview if self.tab is not None else None

    @property
    def _history(self):
        return self.tab._history if self.tab is not None else []

    @property
    def _hist_pos(self):
        return self.tab._hist_pos if self.tab is not None else -1

    def open_tab(self, gfile, select=None, switch=True):
        """Add a tab showing `gfile` (selecting `select` below it) and,
        unless told otherwise, switch to it."""
        tab = Tab(self, gfile)
        if select is not None:
            tab.set_root(gfile, select=select)
        label = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        label.pack_start(tab.title, True, True, 0)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.get_style_context().add_class("tab-close")
        # tab.win, not self: the label travels with a tab dragged elsewhere.
        close.connect("clicked", lambda *_args: tab.win.close_tab(tab))
        label.pack_start(close, False, False, 0)
        label.show_all()
        tab.show_all()
        page = self.notebook.append_page(tab, label)      # the rest: _on_page_added
        if switch:
            self.notebook.set_current_page(page)
            tab.focus()
        return tab

    def close_tab(self, tab=None):
        """Close `tab` (default: the current one); the last tab closes the
        window."""
        tab = tab or self.tab
        if tab is None:
            return
        if len(self.tabs) == 1:
            if not self._on_delete():
                self.destroy()
            return
        self.notebook.remove_page(self.notebook.page_num(tab))
        tab.destroy()
        if self.tab is not None:
            self.tab.focus()

    def _cycle_tab(self, step):
        n = len(self.tabs)
        if n > 1:
            self.notebook.set_current_page((self.notebook.get_current_page() + step) % n)
            self.tab.focus()

    def _update_tab_bar(self):
        self.notebook.set_show_tabs(len(self.tabs) > 1)

    def _on_page_added(self, notebook, tab, page):
        """A tab arrived: new, or dragged in from another window."""
        tab.win = self
        notebook.set_tab_reorderable(tab, True)
        notebook.set_tab_detachable(tab, True)
        # Equal-width tabs across the whole strip, Finder-style.
        notebook.child_set_property(tab, "tab-expand", True)
        notebook.child_set_property(tab, "tab-fill", True)
        self._update_tab_bar()
        if tab is self.tab:
            self._show_location(tab)
            GLib.idle_add(self._update_status)

    def _on_page_removed(self, notebook, tab, page):
        self._update_tab_bar()
        if not self.tabs and not self.in_destruction():
            # The last tab was dragged away: nothing is left to show. (After
            # the drop is over: GTK is still using this notebook for it.)
            GLib.idle_add(self._close_empty)

    def _close_empty(self):
        if not self.tabs and not self.in_destruction():
            self.save_window_state()
            self.destroy()
        return False

    def _on_tab_detached(self, notebook, tab, x, y):
        """A tab was dropped outside every notebook: a new window takes it
        (GTK moves the page into the notebook returned)."""
        win = FinchWindow(self.get_application(), None, self.state)
        win.move(max(x - 40, 0), max(y - 20, 0))
        win.show_all()
        return win.notebook

    def _on_tab_switched(self, notebook, tab, page):
        # Called before the page is current: look at `tab`, not self.tab.
        self._show_location(tab)
        GLib.idle_add(self._update_status)

    def _on_sidebar_open(self, sidebar, location, flags):
        if flags & Gtk.PlacesOpenFlags.NEW_TAB:
            self.open_tab(location)
        else:
            self.set_root(location)

    # -- the current tab -------------------------------------------------------
    def _focused_column(self):
        return self.tab.focused_column() if self.tab is not None else None

    def current_dir(self):
        return self.tab.current_dir() if self.tab is not None else None

    def _column_for(self, gfile):
        """The column showing `gfile`: in the current tab if it has one, else
        in any other tab."""
        tabs = [self.tab] + [t for t in self.tabs if t is not self.tab] if self.tab else []
        for tab in tabs:
            col = tab.column_for(gfile)
            if col is not None:
                return col
        return None

    def set_root(self, gfile, select=None, focus=False):
        self.tab.set_root(gfile, select=select, focus=focus)

    def add_column(self, gfile, select=None, focus=False):
        return self.tab.add_column(gfile, select=select, focus=focus)

    def go_up(self):
        self.tab.go_up()

    def go_back(self):
        self.tab.go_back()

    def go_forward(self):
        self.tab.go_forward()

    def reload_all(self):
        for tab in self.tabs:
            tab.reload()
        self.sidebar.refresh_usage()

    def _on_location_changed(self, tab):
        """A tab moved: the title bar follows the current tab only."""
        if tab is self.tab:
            self._show_location(tab)

    def _show_location(self, tab):
        gfile = tab.location
        self.pathbar.set_location(gfile)
        name = location_name(gfile)
        self.header.set_title(name)
        self.set_title(name)
        self._update_nav_actions(tab)
        if tab.root is not None:
            self.sidebar.sync_location(tab.root)

    def _update_nav_actions(self, tab=None):
        tab = tab or self.tab
        self._back_action.set_enabled(tab is not None and tab.can_go_back())
        self._forward_action.set_enabled(tab is not None and tab.can_go_forward())
        root = tab.root if tab is not None else None
        self._up_action.set_enabled(root is not None and root.get_parent() is not None)
        self._terminal_action.set_enabled(root is not None and root.get_path() is not None)
        writable = root is not None and root.get_uri_scheme() not in (
            "trash", "recent", "network", "computer")
        self._new_folder_action.set_enabled(writable)
        self._paste_action.set_enabled(writable and clipboard.has_files())

    def new_folder(self):
        """Create an untitled folder in the current directory and rename it."""
        dest = self.current_dir()
        if dest is None or dest.get_uri_scheme() == "trash":
            return
        base = _("Untitled Folder")
        child = dest.get_child(base)
        n = 2
        while child.query_exists(None):
            child = dest.get_child(f"{base} {n}")
            n += 1
        try:
            child.make_directory(None)
        except GLib.Error as err:
            show_error(self, _("Could not create folder"), err.message)
            return
        col = self._column_for(dest)
        if col is not None:
            col.select_and_rename(child)

    def _update_status(self):
        col = self._focused_column()
        if col is None:
            return False
        parts = [ngettext("%d item", "%d items", col.item_count) % col.item_count]
        _gfiles, infos = col.selected_all()
        files = [i for i in infos if i.get_file_type() != Gio.FileType.DIRECTORY]
        if len(infos) > 1:
            parts.append(ngettext("%d selected", "%d selected", len(infos)) % len(infos))
        if files:
            parts.append(GLib.format_size(sum(i.get_size() for i in files)))
        try:
            fs = col.gfile.query_filesystem_info("filesystem::free", None)
            if fs.has_attribute("filesystem::free") and not col.in_trash():
                parts.append(_("%s free")
                             % GLib.format_size(fs.get_attribute_uint64("filesystem::free")))
        except GLib.Error:
            pass
        # TRANSLATORS: separator between the parts of the status line
        self.status.set_text(_(", ").join(parts))
        return False

    # -- settings shared by every tab ----------------------------------------
    def set_column_width(self, width, except_for=None):
        self.col_width = width
        for tab in self.tabs:
            tab.apply_column_width(width, except_for)

    def _on_toggle_hidden(self, action, value):
        action.set_state(value)
        self.show_hidden = value.get_boolean()
        for tab in self.tabs:
            for col in tab.columns:
                col.set_show_hidden(self.show_hidden)

    def _on_sort_by(self, action, value):
        mode = value.get_string()
        if mode not in SORT_MODES:
            return
        action.set_state(value)
        self.sort_by = mode
        self._apply_sort()

    def _on_sort_reversed(self, action, value):
        action.set_state(value)
        self.sort_reversed = value.get_boolean()
        self._apply_sort()

    def _apply_sort(self):
        for tab in self.tabs:
            for col in tab.columns:
                col.set_sort(self.sort_by, self.sort_reversed)

    def _on_view_mode(self, action, value):
        mode = value.get_string()
        if mode not in VIEW_MODES or mode == self.view_mode:
            return
        action.set_state(value)
        self.view_mode = mode
        for tab in self.tabs:
            tab.set_view_mode()
        if self.tab is not None:
            self.tab.focus()

    def _focus_files(self):
        if self.columns:
            self.columns[0].view.grab_focus()

    def _on_crumb_clicked(self, bar, gfile, new_tab):
        if new_tab:
            self.open_tab(gfile, switch=False)
        else:
            self.set_root(gfile)
            self._focus_files()

    def _on_path_entered(self, bar, text):
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
        self._focus_files()

    def _on_context_menu(self, col, gfile, info, event):
        menu = Gtk.Menu()
        menu.attach_to_widget(col.view, None)
        # Built afresh on every right-click: drop it once it is closed (the
        # chosen item's activate has already run by the time the idle fires).
        menu.connect("deactivate", lambda m: GLib.idle_add(m.destroy))
        is_dir = gfile is not None and info.get_file_type() == Gio.FileType.DIRECTORY
        # On one of several selected rows the menu acts on all of them.
        gfiles, infos = col.selected_all()
        multi = gfile is not None and len(gfiles) > 1

        def open_all():
            for g, i in zip(gfiles, infos):
                if i.get_file_type() != Gio.FileType.DIRECTORY:
                    launch_default(g, self)

        def item(label, cb, icon):
            # ImageMenuItem is deprecated but still the one widget that keeps
            # a 16 px icon in front of the label whatever the theme setting.
            # `icon`: an icon name, or a GIcon (the opening application's).
            mi = Gtk.ImageMenuItem(label=label)
            if isinstance(icon, str):
                icon = Gio.ThemedIcon.new(icon)
            mi.set_image(Gtk.Image.new_from_gicon(icon, Gtk.IconSize.MENU))
            mi.set_always_show_image(True)
            mi.connect("activate", lambda *_args: cb())
            menu.append(mi)
            return mi

        def open_item(infos, cb):
            label, icon = open_label(infos)
            return label, cb, icon

        def paste(dest):
            mi = item(_("Paste"), lambda: self.paste(dest), "edit-paste-symbolic")
            mi.set_sensitive(clipboard.has_files())

        if col.in_trash():
            # Trash: items can only be restored or destroyed.
            if multi:
                item(*open_item(infos, open_all))
                if col.is_trash_root():
                    menu.append(Gtk.SeparatorMenuItem())
                    item(_("Restore"), col.restore_item, "edit-undo-symbolic")
                    item(_("Delete Permanently"), col.delete_item, "edit-delete-symbolic")
            elif gfile is not None:
                item(*open_item([info], lambda: launch_default(gfile, self)))
                item(_("Open With…"), lambda: self.open_with(gfile), "system-run-symbolic")
                if col.is_trash_root():
                    menu.append(Gtk.SeparatorMenuItem())
                    item(_("Restore"), lambda: col.restore_item(gfile), "edit-undo-symbolic")
                    item(_("Delete Permanently"), lambda: col.delete_item(gfile), "edit-delete-symbolic")
            else:
                item(_("Open in Nemo"), lambda: self.open_in_nemo(col.gfile), "system-file-manager-symbolic")
                if col.is_trash_root():
                    item(_("Empty Trash"), col.empty_trash, "user-trash-full-symbolic")
        elif multi:
            item(*open_item(infos, open_all))
            menu.append(Gtk.SeparatorMenuItem())
            item(_("Cut"), lambda: clipboard.set_files(gfiles, cut=True), "edit-cut-symbolic")
            item(_("Copy"), lambda: clipboard.set_files(gfiles), "edit-copy-symbolic")
            paste(col.gfile)
            if all(g.get_path() for g in gfiles):
                menu.append(Gtk.SeparatorMenuItem())
                # All archives: offer to unpack them; otherwise to pack them.
                if all(is_archive(i.get_content_type()) for i in infos):
                    item(_("Extract Here"), lambda: self.extract(gfiles, col.gfile), "archive-extract-symbolic")
                else:
                    item(_("Compress…"), lambda: self.compress(gfiles, col.gfile), "package-x-generic-symbolic")
            menu.append(Gtk.SeparatorMenuItem())
            item(_("Move to Trash"), col.trash_item, "user-trash-symbolic")
            item(_("Delete"), col.delete_item, "edit-delete-symbolic")
        elif gfile is not None:
            item(*open_item([info], lambda: launch_default(gfile, self)))
            if is_dir:
                item(_("Open in New Tab"), lambda: self.open_tab(gfile, switch=False),
                     "tab-new-symbolic")
            item(_("Open With…"), lambda: self.open_with(gfile), "system-run-symbolic")
            menu.append(Gtk.SeparatorMenuItem())
            item(_("Rename"), col.start_rename, "document-edit-symbolic")
            menu.append(Gtk.SeparatorMenuItem())
            item(_("Cut"), lambda: self.copy_to_clipboard(gfile, cut=True), "edit-cut-symbolic")
            item(_("Copy"), lambda: self.copy_to_clipboard(gfile), "edit-copy-symbolic")
            paste(gfile if is_dir else col.gfile)
            if gfile.get_path():
                menu.append(Gtk.SeparatorMenuItem())
                if not is_dir and is_archive(info.get_content_type()):
                    item(_("Extract Here"), lambda: self.extract([gfile], col.gfile), "archive-extract-symbolic")
                else:
                    item(_("Compress…"), lambda: self.compress([gfile], col.gfile), "package-x-generic-symbolic")
            menu.append(Gtk.SeparatorMenuItem())
            if is_dir:
                item(_("Open in Nemo"), lambda: self.open_in_nemo(gfile), "system-file-manager-symbolic")
                if gfile.get_path():
                    item(_("Open in Terminal"), lambda: self.open_terminal(gfile), "utilities-terminal-symbolic")
            else:
                item(_("Show in Nemo"), lambda: self.open_in_nemo(gfile), "system-file-manager-symbolic")
            item(_("Copy Path"), lambda: self.copy_path(gfile), "insert-link-symbolic")
            menu.append(Gtk.SeparatorMenuItem())
            item(_("Move to Trash"), lambda: col.trash_item(gfile), "user-trash-symbolic")
            item(_("Delete"), lambda: col.delete_item(gfile), "edit-delete-symbolic")
        else:
            paste(col.gfile)
            menu.append(Gtk.SeparatorMenuItem())
            item(_("Open in Nemo"), lambda: self.open_in_nemo(col.gfile), "system-file-manager-symbolic")
            if col.gfile.get_path():
                item(_("Open in Terminal"), lambda: self.open_terminal(col.gfile), "utilities-terminal-symbolic")
            item(_("Copy Path"), lambda: self.copy_path(col.gfile), "insert-link-symbolic")
        menu.append(Gtk.SeparatorMenuItem())
        sort = Gtk.ImageMenuItem(label=_("Sort By"))
        sort.set_image(Gtk.Image.new_from_icon_name("view-sort-descending-symbolic", Gtk.IconSize.MENU))
        sort.set_always_show_image(True)
        sort.set_submenu(Gtk.Menu.new_from_model(self._sort_menu))
        menu.append(sort)
        menu.show_all()
        menu.popup_at_pointer(event)

    def _on_delete(self, *_args):
        if self.transfers_running() and not confirm(
                self, _("A file operation is still running"),
                _("Quitting now will leave it unfinished."), _("Quit Anyway")):
            return True
        self.save_window_state()
        return False

    def save_window_state(self):
        w, h = self.get_size()
        self.state.update({
            "width": w, "height": h, "column_width": self.col_width,
            "show_hidden": self.show_hidden,
            "sort_by": self.sort_by, "sort_reversed": self.sort_reversed,
            "view_mode": self.view_mode,
            "location_entry": self.pathbar.entry_mode,
            "sidebar_width": self.paned.get_position(),
        })
        # Where the user was is deliberately not kept: every start is home.
        for key in ("last_root", "tabs", "current_tab"):
            self.state.pop(key, None)
        save_state(self.state)

    # -- clipboard -----------------------------------------------------------
    def copy_to_clipboard(self, gfile, cut=False):
        clipboard.set_files([gfile], cut=cut)

    def copy_selection(self, cut=False):
        col = self._focused_column()
        gfiles = col.selected_all()[0] if col is not None else []
        if gfiles:
            clipboard.set_files(gfiles, cut=cut)

    def paste(self, dest=None):
        """Paste the clipboard's files into the directory `dest` (default:
        the current directory, see current_dir)."""
        dest = dest or self.current_dir()
        if dest is None or dest.get_uri_scheme() == "trash":
            return
        op, files = clipboard.get_files()
        if not files:
            return
        move = op == "cut"
        if move:
            clipboard.clear()           # a cut is pasted once
        self.start_transfer(files, dest, move, _("Could not paste"))

    def drop_files(self, files, dest, move):
        """Files dropped onto the directory `dest` (a column, a folder row
        or a sidebar place): move or copy them there; dropped on the trash
        they are trashed."""
        if dest.get_uri_scheme() == "trash":
            errors = []
            for gfile in files:
                if gfile.get_uri_scheme() == "trash":
                    continue
                try:
                    gfile.trash(None)
                except GLib.Error as err:
                    errors.append("%s: %s" % (gfile.get_basename(), err.message))
            if errors:
                show_error(self, _("Could not move to trash"), "\n".join(errors[:10]))
            return
        self.start_transfer(files, dest, move,
                            _("Could not move") if move else _("Could not copy"))

    # -- transfers -----------------------------------------------------------
    def start_transfer(self, files, dest, move, error_title):
        """Queue a copy (or `move`) of `files` into `dest`. Jobs run one at a
        time; the status line shows the running one and lets it be cancelled."""
        self._queue_job(Transfer(files, dest, move), error_title)

    def compress(self, files, dest_dir):
        """Ask for a name and format, then pack `files` into an archive in
        `dest_dir` as a background job."""
        picked = ask_archive(self, files, dest_dir)
        if picked is None:
            return
        dest, password = picked
        if needs_sevenzip(dest.get_basename(), password):
            job = SevenZip(files, dest, password)
        else:
            job = Archive(files, dest)
        self._queue_job(job, _("Could not compress"))

    def extract(self, archives, dest_dir, password=""):
        """Unpack each of `archives` into `dest_dir` as background jobs."""
        for archive in archives:
            self._queue_job(Extract(archive, dest_dir, password), _("Could not extract"))

    def _queue_job(self, job, error_title):
        job.error_title = error_title
        self._jobs.append(job)
        if len(self._jobs) == 1:
            self._run_next_job()

    def _run_next_job(self):
        if not self._jobs:
            self.progress_box.hide()
            return
        job = self._jobs[0]
        self.progress_bar.set_fraction(0)
        self._on_job_progress(job)
        # show_all() is a no-op on a no-show-all widget: show the parts by hand.
        for child in self.progress_box.get_children():
            child.show_all()
        self.progress_box.show()
        job.start(self._on_job_progress, self._on_job_done)

    def cancel_transfer(self):
        """Stop the running job; queued ones go ahead afterwards."""
        if self._jobs:
            self._jobs[0].cancel()
            self.progress_label.set_text(_("Cancelling…"))
            self.cancel_button.set_sensitive(False)

    def transfers_running(self):
        return bool(self._jobs)

    def _on_job_progress(self, job):
        if job.cancelled:
            return
        if job.scanning:
            self.progress_bar.pulse()
            text = job.preparing
        else:
            self.progress_bar.set_fraction(job.fraction)
            parts = [job.verb % job.current_name] if job.current_name else []
            if job.total_bytes:
                # TRANSLATORS: progress, e.g. "1.2 GB of 4.5 GB"
                parts.append(_("%s of %s") % (GLib.format_size(job.done_bytes),
                                              GLib.format_size(job.total_bytes)))
            text = _(", ").join(parts)
        queued = len(self._jobs) - 1
        if queued > 0:
            text += " " + ngettext("(%d more waiting)", "(%d more waiting)", queued) % queued
        self.progress_label.set_text(text)

    def _on_job_done(self, job, created, errors, cancelled):
        if job in self._jobs:
            self._jobs.remove(job)
        self.cancel_button.set_sensitive(True)
        if errors and not cancelled:
            show_error(self, job.error_title, "\n".join(errors[:10]))
        col = self._column_for(job.dest_dir) if created else None
        if col is not None:
            col.reload_and_select(created)
        if getattr(job, "password_needed", False) and not cancelled:
            # An encrypted archive (or a wrong password): ask and go again.
            password = ask_password(self, job.archive.get_basename())
            if password is not None:
                self.extract([job.archive], job.dest_dir, password)
        self._run_next_job()

    def show_about(self):
        dlg = Gtk.AboutDialog(transient_for=self, modal=True,
                              program_name="Finch", version=VERSION,
                              logo_icon_name="system-file-manager",
                              comments=_("A Finder-style column view file browser"),
                              copyright="© 2026 DaoCheng",
                              authors=["DaoCheng"])
        # TRANSLATORS: credit line in the About dialog; put your name here
        translators = _("translator-credits")
        if translators != "translator-credits":
            dlg.set_translator_credits(translators)
        dlg.connect("response", lambda d, _r: d.destroy())
        dlg.show()

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
