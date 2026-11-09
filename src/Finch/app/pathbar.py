"""The location bar, Nemo style: the path as a row of buttons (one per
folder, the current one pressed), or a text entry for typing a path. The
button at its right edge switches between the two; Ctrl+L shows the entry
just until a path is entered or Escape is pressed."""
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from . import _
from .tab import location_name

# The pencil, which every icon theme draws in its own line weight; Nemo's
# own toggle icon only comes in hicolor and looks heavier than the rest.
TOGGLE_ICON = "document-edit-symbolic"


class PathBar(Gtk.Box):
    __gsignals__ = {
        # a folder button was clicked (gfile, in a new tab?)
        "crumb-clicked": (GObject.SignalFlags.RUN_FIRST, None, (object, bool)),
        # a path was typed into the entry and Enter pressed
        "path-entered": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # the entry was left (Escape, or focus went elsewhere): the caller
        # may want to put the keyboard focus back on the files
        "entry-left": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, entry_mode=False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, hexpand=True)
        self.location = None
        self._crumbs = []           # (gfile, button), root first
        self._temporary = False     # entry shown by Ctrl+L, not by the toggle

        self.stack = Gtk.Stack(hexpand=True, homogeneous=True,
                               transition_type=Gtk.StackTransitionType.NONE)
        self.pack_start(self.stack, True, True, 0)

        # the row of folder buttons; scrolls (without a scrollbar) when the
        # path is deeper than the bar is wide, with arrows at the ends
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class("path-bar")
        self.left = Gtk.Button.new_from_icon_name("pan-start-symbolic", Gtk.IconSize.MENU)
        self.right = Gtk.Button.new_from_icon_name("pan-end-symbolic", Gtk.IconSize.MENU)
        for btn in (self.left, self.right):
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("path-slider")
            btn.set_no_show_all(True)
        self.left.connect("clicked", lambda b: self._slide(-1))
        self.right.connect("clicked", lambda b: self._slide(+1))
        self.crumbs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.crumbs.get_style_context().add_class("linked")
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                           vscrollbar_policy=Gtk.PolicyType.NEVER,
                                           hexpand=True)
        self.scroller.add(self.crumbs)
        self.scroller.connect("size-allocate", lambda w, a: self._update_sliders())
        self.scroller.connect("scroll-event", self._on_scroll)
        bar.pack_start(self.left, False, False, 0)
        bar.pack_start(self.scroller, True, True, 0)
        bar.pack_start(self.right, False, False, 0)
        self.stack.add_named(bar, "crumbs")

        self.entry = Gtk.Entry(hexpand=True, placeholder_text=_("Path"))
        self.entry.connect("activate", self._on_entry_activate)
        self.entry.connect("key-press-event", self._on_entry_key)
        self.entry.connect("focus-out-event", self._on_entry_focus_out)
        self.stack.add_named(self.entry, "entry")

        self.toggle = Gtk.ToggleButton()
        self.toggle.add(Gtk.Image.new_from_icon_name(TOGGLE_ICON, Gtk.IconSize.BUTTON))
        self.toggle.set_tooltip_text(_("Toggle path entry"))
        self.toggle.set_active(entry_mode)
        self.toggle.connect("toggled", self._on_toggled)
        self.pack_start(self.toggle, False, False, 0)
        self.stack.set_visible_child_name("entry" if entry_mode else "crumbs")

    # -- public ------------------------------------------------------------------
    @property
    def entry_mode(self):
        """Is the entry the bar's standing state (the toggle pressed)?"""
        return self.toggle.get_active()

    def set_location(self, gfile):
        """Show `gfile`: the entry's text follows it; the button row keeps
        its deeper folders when `gfile` is one of those already shown (going
        up), otherwise it is rebuilt."""
        self.location = gfile
        self.entry.set_text(gfile.get_parse_name())
        self.entry.set_position(-1)
        if not any(gfile.equal(g) for g, _b in self._crumbs):
            self._rebuild(gfile)
        for g, btn in self._crumbs:
            self._set_active(btn, g.equal(gfile))
        GLib.idle_add(self._scroll_to_current)

    def show_entry(self):
        """Ctrl+L: the entry, with the path selected, until it is left."""
        if not self.entry_mode:
            self._temporary = True
            self.stack.set_visible_child_name("entry")
        self.entry.grab_focus()

    # -- the button row ------------------------------------------------------------
    def _rebuild(self, gfile):
        for child in self.crumbs.get_children():
            child.destroy()
        self._crumbs = []
        # Inside the home folder the row starts at home, like Nemo's.
        home = Gio.File.new_for_path(GLib.get_home_dir())
        chain = []
        g = gfile
        while g is not None:
            chain.append(g)
            g = None if g.equal(home) else g.get_parent()
        for g in reversed(chain):
            btn = Gtk.ToggleButton()
            btn.get_style_context().add_class("crumb")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            icon = self._icon(g)
            if icon is not None:
                box.pack_start(Gtk.Image.new_from_gicon(icon, Gtk.IconSize.MENU), False, False, 0)
            box.pack_start(Gtk.Label(label=self._label(g)), False, False, 0)
            btn.add(box)
            btn.connect("toggled", self._on_crumb_toggled, g)
            btn.connect("button-release-event", self._on_crumb_release, g)
            btn.show_all()
            self.crumbs.pack_start(btn, False, False, 0)
            self._crumbs.append((g, btn))

    @staticmethod
    def _label(gfile):
        home = Gio.File.new_for_path(GLib.get_home_dir())
        if gfile.get_parent() is None and gfile.get_uri_scheme() == "file":
            return _("File System")
        if gfile.equal(home):
            return gfile.get_basename()
        return location_name(gfile)

    @staticmethod
    def _icon(gfile):
        """An icon for the home, the file system root, the special folders
        (Pictures, Desktop...) and the virtual locations; plain folders get
        none, like Nemo."""
        scheme = gfile.get_uri_scheme()
        if scheme != "file":
            return Gio.ThemedIcon.new({"trash": "user-trash-symbolic",
                                       "recent": "document-open-recent-symbolic",
                                       "network": "network-workgroup-symbolic",
                                       "computer": "computer-symbolic",
                                       }.get(scheme, "folder-remote-symbolic"))
        if gfile.get_parent() is None:
            return Gio.ThemedIcon.new("drive-harddisk-symbolic")
        if gfile.equal(Gio.File.new_for_path(GLib.get_home_dir())):
            return Gio.ThemedIcon.new("user-home-symbolic")
        try:
            info = gfile.query_info("standard::symbolic-icon", Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error:
            return None
        icon = info.get_symbolic_icon()
        names = icon.get_names() if isinstance(icon, Gio.ThemedIcon) else []
        if names and names[0] not in ("folder-symbolic", "inode-directory-symbolic"):
            return icon
        return None

    def _set_active(self, btn, active):
        btn.handler_block_by_func(self._on_crumb_toggled)
        btn.set_active(active)
        btn.handler_unblock_by_func(self._on_crumb_toggled)

    def _on_crumb_toggled(self, btn, gfile):
        if not btn.get_active():
            self._set_active(btn, True)     # the current folder stays pressed
            return
        self.emit("crumb-clicked", gfile, False)

    def _on_crumb_release(self, btn, event, gfile):
        if event.button == 2:
            self.emit("crumb-clicked", gfile, True)
            return True
        return False

    def _scroll_to_current(self):
        for g, btn in self._crumbs:
            if btn.get_active():
                alloc = btn.get_allocation()
                adj = self.scroller.get_hadjustment()
                if alloc.x < adj.get_value():
                    adj.set_value(alloc.x)
                elif alloc.x + alloc.width > adj.get_value() + adj.get_page_size():
                    adj.set_value(alloc.x + alloc.width - adj.get_page_size())
        self._update_sliders()
        return False

    def _update_sliders(self):
        adj = self.scroller.get_hadjustment()
        overflow = adj.get_upper() > adj.get_page_size() + 1
        self.left.set_visible(overflow)
        self.right.set_visible(overflow)
        if overflow:
            self.left.set_sensitive(adj.get_value() > 0)
            self.right.set_sensitive(adj.get_value() + adj.get_page_size() < adj.get_upper() - 1)

    def _slide(self, direction):
        """One folder button further left or right."""
        adj = self.scroller.get_hadjustment()
        lo, hi = adj.get_value(), adj.get_value() + adj.get_page_size()
        for g, btn in (self._crumbs if direction > 0 else reversed(self._crumbs)):
            a = btn.get_allocation()
            if direction > 0 and a.x + a.width > hi + 1:
                adj.set_value(a.x + a.width - adj.get_page_size())
                break
            if direction < 0 and a.x < lo - 1:
                adj.set_value(a.x)
                break
        self._update_sliders()

    def _on_scroll(self, widget, event):
        if event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT):
            self._slide(-1)
        elif event.direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT):
            self._slide(+1)
        return True

    # -- the entry -----------------------------------------------------------------
    def _on_toggled(self, toggle):
        self._temporary = False
        if toggle.get_active():
            self.stack.set_visible_child_name("entry")
            self.entry.grab_focus()
        else:
            self.stack.set_visible_child_name("crumbs")
            self.emit("entry-left")

    def _leave_entry(self):
        if self._temporary:
            self._temporary = False
            self.stack.set_visible_child_name("crumbs")
        if self.location is not None:
            self.entry.set_text(self.location.get_parse_name())
        self.emit("entry-left")

    def _on_entry_activate(self, entry):
        text = entry.get_text().strip()
        if text:
            self.emit("path-entered", text)

    def _on_entry_key(self, entry, event):
        if event.keyval == Gdk.KEY_Escape:
            self._leave_entry()
            return True
        return False

    def _on_entry_focus_out(self, entry, event):
        if self._temporary:
            # Not from inside the event: the focus change is still under way.
            GLib.idle_add(self._leave_entry)
        return False
