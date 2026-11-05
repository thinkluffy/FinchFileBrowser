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
        self.set_accels_for_action("app.quit", ["<Primary>q", "<Primary>w"])
        self.set_accels_for_action("win.up", ["<Alt>Up"])
        self.set_accels_for_action("win.back", ["<Alt>Left"])
        self.set_accels_for_action("win.forward", ["<Alt>Right"])
        self.set_accels_for_action("win.new-folder", ["<Primary><Shift>n"])
        self.set_accels_for_action("win.reload", ["F5", "<Primary>r"])
        self.set_accels_for_action("win.focus-path", ["<Primary>l"])
        self.set_accels_for_action("win.show-hidden", ["<Primary>h"])
        self.set_accels_for_action("win.open-terminal", ["<Primary>t"])

    def do_shutdown(self):
        # Ctrl+Q goes through app.quit(), which never emits delete-event
        for win in self.get_windows():
            win.save_window_state()
        Gtk.Application.do_shutdown(self)

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        if args:
            root = Gio.File.new_for_commandline_arg_and_cwd(args[0], cmdline.get_cwd())
        elif self.state.get("last_root"):
            root = Gio.File.new_for_uri(self.state["last_root"])
        else:
            root = Gio.File.new_for_path(GLib.get_home_dir())
        try:
            if root.query_info("standard::type", Gio.FileQueryInfoFlags.NONE,
                               None).get_file_type() != Gio.FileType.DIRECTORY:
                root = root.get_parent() or Gio.File.new_for_path(GLib.get_home_dir())
        except GLib.Error:
            root = Gio.File.new_for_path(GLib.get_home_dir())
        if root.get_path():
            root = Gio.File.new_for_path(root.get_path())   # normalise "dir/" → "dir"
        win = FinchWindow(self, root, self.state)
        if win.columns:
            win.columns[0].view.grab_focus()
        win.show_all()
        return 0


def main(argv=None):
    return FinchApp().run(sys.argv if argv is None else argv)
