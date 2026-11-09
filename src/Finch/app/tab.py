"""One tab: a strip of columns with its own location history and preview.

The window owns the title bar, the sidebar, the status line and the file
operations; everything that depends on *where* the user is lives here, so
that several tabs can each be somewhere else. The tab calls back into the
window for what it does not own: column settings (width, hidden files,
sort), the context menu, drops, and to tell it the location changed.
"""
from gi.repository import Gio, GLib, Gtk, Pango

from . import _
from .column import HANDLE_WIDTH, Column
from .preview import Preview
from .util import launch_default

MAX_HISTORY = 100


def _child_towards(parent, target):
    """The child of `parent` that lies on the way to `target` (`target`
    itself when it is a direct child), or None if `target` is not below it."""
    node = target
    while node is not None:
        above = node.get_parent()
        if above is None:
            return None
        if above.equal(parent):
            return node
        node = above
    return None


def location_name(gfile):
    """What the title bar and the tab call `gfile`."""
    name = gfile.get_basename() or gfile.get_parse_name()
    if gfile.get_parent() is None:
        name = {"trash": _("Trash"), "recent": _("Recent"), "network": _("Network"),
                "computer": _("Computer")}.get(gfile.get_uri_scheme(), name)
    return name


class Tab(Gtk.Box):
    def __init__(self, win, root):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.columns = []
        self.preview = None
        # The item the title bar names: the deepest folder or file shown.
        self.location = root
        # Finder-style history of *locations*: (root, deepest item shown).
        # Changing the root and selecting a folder in a column both add an
        # entry; back / forward restore the root and re-open the columns
        # down to the item. `_target` is the item still being re-opened.
        self._history, self._hist_pos = [], -1
        self._target = None

        self.colbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.hsw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                      vscrollbar_policy=Gtk.PolicyType.NEVER)
        self.hsw.add(self.colbox)
        self.hsw.connect("size-allocate", self._on_strip_allocated)
        # Keep the right end in view across window resizes while it was in view.
        self._at_end = True
        self.hsw.get_hadjustment().connect("value-changed", self._on_strip_scrolled)
        self.pack_start(self.hsw, True, True, 0)

        # The label shown on the notebook tab. The tabs share the strip
        # equally (the window sets "tab-expand"): GtkNotebook sizes each tab
        # at its minimum and splits the rest evenly, so every title asks for
        # the same small minimum and the text ellipsizes to whatever it gets.
        self.title = Gtk.Label(label=location_name(root), ellipsize=Pango.EllipsizeMode.END,
                               width_chars=6, max_width_chars=6, xalign=0.5)
        self.set_root(root)

    @property
    def root(self):
        return self.columns[0].gfile if self.columns else None

    # -- queries --------------------------------------------------------------
    def focused_column(self):
        """The column that has keyboard focus, else the last one (or None)."""
        for col in reversed(self.columns):
            # is_focus, not has_focus: the latter is False for every widget
            # while the window itself is not the active one.
            if col.view.is_focus():
                return col
        return self.columns[-1] if self.columns else None

    def current_dir(self):
        """The directory the tab is "in", as the title and path box show it:
        the folder selected in the focused column if one is, else that
        column's own folder."""
        col = self.focused_column()
        if col is None:
            return None
        gfile, info = col.selected()
        if info is not None and info.get_file_type() == Gio.FileType.DIRECTORY:
            return gfile
        return col.gfile

    def column_for(self, gfile):
        return next((c for c in self.columns if c.gfile.equal(gfile)), None)

    def can_go_back(self):
        return self._hist_pos > 0

    def can_go_forward(self):
        return self._hist_pos + 1 < len(self._history)

    # -- navigation ----------------------------------------------------------
    def set_root(self, gfile, select=None, focus=False, record=True):
        """Show `gfile` as the left-most column. `select` may be any item
        below it: the columns are re-opened level by level down to it."""
        if record:
            self._record(gfile, select or gfile)
        self._target = select
        for col in self.columns:
            col.destroy()
        self.columns = []
        self._remove_preview()
        self.add_column(gfile, select=_child_towards(gfile, select) if select else None,
                        focus=focus)
        self._set_location(gfile)

    def add_column(self, gfile, select=None, focus=False):
        win = self.win
        col = Column(gfile, win.col_width, win.show_hidden, win.sort_by, win.sort_reversed)
        col.connect("item-selected", self._on_item_selected)
        col.connect("selection-changed", self._on_selection_changed)
        col.connect("item-activated", self._on_item_activated)
        col.connect("width-changed", self._on_width_changed)
        col.connect("key-nav", self._on_key_nav)
        col.connect("context-menu", win._on_context_menu)
        col.connect("open-new-tab", lambda c, g: win.open_tab(g, switch=False))
        col.connect("loaded", lambda *_args: win._update_status())
        col.connect("files-dropped", lambda c, files, dest, move: win.drop_files(files, dest, move))
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

    def go_up(self):
        old_root = self.root
        parent = old_root.get_parent() if old_root is not None else None
        if parent is None:
            return
        self.set_root(parent, select=old_root, focus=True)

    def go_back(self):
        if self.can_go_back():
            self._restore(self._hist_pos - 1)

    def go_forward(self):
        if self.can_go_forward():
            self._restore(self._hist_pos + 1)

    def reload(self):
        for col in self.columns:
            col._reload_keep_selection()

    def _record(self, root, leaf):
        """Append the location (root, leaf) to the history, dropping any
        forward entries; a repeat of the current location is not added."""
        del self._history[self._hist_pos + 1:]
        if self._history:
            last_root, last_leaf = self._history[-1]
            if last_root.equal(root) and last_leaf.equal(leaf):
                return
        self._history.append((root, leaf))
        del self._history[:-MAX_HISTORY]
        self._hist_pos = len(self._history) - 1

    def _restore(self, pos):
        self._hist_pos = pos
        root, leaf = self._history[pos]
        self.set_root(root, select=None if leaf.equal(root) else leaf, record=False)

    def _set_location(self, gfile):
        self.location = gfile
        self.title.set_text(location_name(gfile))
        self.win._on_location_changed(self)

    # -- preview and layout ----------------------------------------------------
    def _remove_preview(self):
        if self.preview is not None:
            self.preview.destroy()
            self.preview = None

    def show_preview(self, fill):
        """Put a new preview pane at the right end of the strip and `fill` it."""
        # As wide as a column (list plus handle) at least, so that selecting
        # a file or a folder in the last column extends the strip equally.
        self.preview = Preview(self.win.col_width + HANDLE_WIDTH)
        # Takes whatever is left of the strip; its content stays centred.
        # It goes in before it is filled and sized, so that its labels
        # already have their styles and the room it is about to get is
        # known: then the picture comes up at its final size instead of
        # jumping to it after the first layout.
        self.colbox.pack_start(self.preview, True, True, 0)
        self.preview.set_height(self.colbox.get_allocated_height())
        self.fit_preview(defer=False)
        fill(self.preview)
        self.preview.show_all()
        self._scroll_to_end_when_allocated(self.preview)

    def fit_preview(self, defer=True):
        """Tell the preview how much of the strip the columns leave to it."""
        if self.preview is None:
            return
        used = sum(col.get_allocated_width() for col in self.columns)
        self.preview.set_available(self.hsw.get_allocated_width() - used, defer)

    def _on_strip_allocated(self, *_args):
        self.fit_preview()
        if self._at_end:
            GLib.idle_add(self._scroll_to_end)

    def _on_strip_scrolled(self, adj):
        self._at_end = adj.get_value() + adj.get_page_size() >= adj.get_upper() - 1

    def _scroll_to_end_when_allocated(self, widget):
        def on_alloc(w, alloc):
            w.disconnect(handler_id)
            GLib.idle_add(self._scroll_to_end)
        handler_id = widget.connect("size-allocate", on_alloc)

    def _scroll_to_end(self):
        adj = self.hsw.get_hadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    # -- column signals --------------------------------------------------------
    def _on_item_selected(self, col, gfile, info):
        self.truncate_after(col)
        # Are we still re-opening columns towards a history entry?
        target, nxt = self._target, None
        if target is not None:
            if gfile is not None and gfile.equal(target):
                self._target = None             # arrived
            elif gfile is not None:
                nxt = _child_towards(gfile, target)
                if nxt is None:
                    self._target = None         # the user went elsewhere
                    target = None
            else:
                self._target = target = None
        if gfile is None:
            self._set_location(col.gfile)
        elif info.get_file_type() == Gio.FileType.DIRECTORY:
            if target is None:
                self._record(self.root, gfile)
            self.add_column(gfile, select=nxt)
            self._set_location(gfile)
        else:
            if target is None and self._history:
                # Remember the file with the current location, so that
                # coming back here shows its preview again.
                self._history[self._hist_pos] = (self.root, gfile)
            self.show_preview(lambda pv: pv.set_file(gfile, info))
            self._set_location(gfile)
        self.win._update_status()

    def _on_selection_changed(self, col, gfiles, infos):
        """Several items selected in `col`: nothing opens to the right but a
        summary of them, and the location history is left alone."""
        self.truncate_after(col)
        self._target = None
        self.show_preview(lambda pv: pv.set_files(gfiles, infos))
        self._set_location(col.gfile)
        self.win._update_status()

    def _on_item_activated(self, col, gfile, info):
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            self._on_key_nav(col, "right")
        else:
            launch_default(gfile, self.win)

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
        self.win._update_status()

    def _on_width_changed(self, col, width, apply_all):
        if apply_all:
            # Shift-drag: every column everywhere follows and the width
            # becomes the default.
            self.win.set_column_width(width, except_for=col)

    def apply_column_width(self, width, except_for=None):
        for col in self.columns:
            if col is not except_for:
                col.sw.set_size_request(width, -1)
        if self.preview is not None:
            self.preview.set_width(width + HANDLE_WIDTH)

    def focus(self):
        col = self.focused_column()
        if col is not None:
            col.view.grab_focus()
