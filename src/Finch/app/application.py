"""Gtk.Application: CSS, accelerators and the command line."""
import sys

from gi.repository import Gdk, Gio, GLib, Gtk

from . import APP_ID, resource
from .util import load_state
from .window import FinchWindow


class FinchApp(Gtk.Application):
    def __init__(self):
        # NON_UNIQUE: every launch is its own process, so running it from a
        # terminal behaves like any ordinary program instead of handing the
        # arguments to an already running instance and exiting at once.
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
                         | Gio.ApplicationFlags.NON_UNIQUE)
        self.state = load_state()

    def do_startup(self):
        Gtk.Application.do_startup(self)
        # Application-priority CSS wins over the theme regardless of specificity.
        css = Gtk.CssProvider()
        css.load_from_data(resource("style.css"))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_args: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("win.new-tab", ["<Primary>t"])
        self.set_accels_for_action("win.close-tab", ["<Primary>w"])
        self.set_accels_for_action("win.next-tab", ["<Primary>Page_Down", "<Primary>Tab"])
        self.set_accels_for_action("win.prev-tab", ["<Primary>Page_Up", "<Primary><Shift>Tab",
                                                    "<Primary><Shift>ISO_Left_Tab"])
        self.set_accels_for_action("win.view-mode::columns", ["<Primary>1"])
        self.set_accels_for_action("win.view-mode::grid", ["<Primary>2"])
        self.set_accels_for_action("win.up", ["<Alt>Up"])
        self.set_accels_for_action("win.back", ["<Alt>Left"])
        self.set_accels_for_action("win.forward", ["<Alt>Right"])
        self.set_accels_for_action("win.new-folder", ["<Primary><Shift>n"])
        self.set_accels_for_action("win.reload", ["F5", "<Primary>r"])
        self.set_accels_for_action("win.focus-path", ["<Primary>l"])
        self.set_accels_for_action("win.show-hidden", ["<Primary>h"])
        self.set_accels_for_action("win.open-terminal", ["F4"])
        self.set_accels_for_action("win.cut", ["<Primary>x"])
        self.set_accels_for_action("win.copy", ["<Primary>c"])
        self.set_accels_for_action("win.paste", ["<Primary>v"])

    def do_shutdown(self):
        # Ctrl+Q goes through app.quit(), which never emits delete-event
        for win in self.get_windows():
            win.save_window_state()
        Gtk.Application.do_shutdown(self)

    @staticmethod
    def _folder(gfile):
        """`gfile` if it is a folder that can be read, its parent if it is a
        file, else None."""
        try:
            if gfile.query_info("standard::type", Gio.FileQueryInfoFlags.NONE,
                                None).get_file_type() != Gio.FileType.DIRECTORY:
                gfile = gfile.get_parent()
        except GLib.Error:
            return None
        if gfile is not None and gfile.get_path():
            gfile = Gio.File.new_for_path(gfile.get_path())   # normalise "dir/" → "dir"
        return gfile

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        home = Gio.File.new_for_path(GLib.get_home_dir())
        # Every argument is a tab, the first the current one; with none,
        # a single tab on the home folder (where the user was last time is
        # not remembered).
        roots = [self._folder(Gio.File.new_for_commandline_arg_and_cwd(a, cmdline.get_cwd()))
                 for a in args]
        roots = [r for r in roots if r is not None] or [home]
        win = FinchWindow(self, roots[0], self.state)
        for root in roots[1:]:
            win.open_tab(root, switch=False)
        if win.columns:
            win.columns[0].view.grab_focus()
        win.show_all()
        return 0


def main(argv=None):
    return FinchApp().run(sys.argv if argv is None else argv)
