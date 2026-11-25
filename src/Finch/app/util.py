"""Small helpers shared by the widgets: sorting, state file, dialogs, launching."""
import json
import locale
import os
import re

from gi.repository import Gio, GLib, Gtk

from . import CONFIG_DIR, STATE_FILE, TEXT_LIKE, _

try:
    locale.setlocale(locale.LC_COLLATE, "")
except locale.Error:
    pass
_DIGITS = re.compile(r"(\d+)")


def natural_key(name):
    """Finder-like ordering: case-insensitive, locale-aware, numbers by value."""
    parts = _DIGITS.split(name.casefold())
    return tuple((0, int(part)) if part.isdigit() else (1, locale.strxfrm(part))
                 for part in parts)


# ---------------------------------------------------------------- state ----
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------- files ----
def is_text_like(content_type):
    if not content_type:
        return False
    if content_type in TEXT_LIKE or content_type.startswith("text/"):
        return True
    return Gio.content_type_is_a(content_type, "text/plain")


def default_opener(infos):
    """The application that "Open" would use for every one of the files
    described by `infos` (their standard::content-type), or None when they
    are directories, have no handler or would go to different programs."""
    app = None
    for info in infos:
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            return None
        ctype = info.get_content_type()
        if not ctype:
            return None
        this = Gio.AppInfo.get_default_for_type(ctype, False)
        if this is None or (app is not None and not this.equal(app)):
            return None
        app = this
    return app


def open_label(infos):
    """The context menu's "Open" item: named after the program that will
    open the files, the way Nemo does, when that is known; with its icon."""
    app = default_opener(infos)
    if app is None:
        return _("Open"), "document-open-symbolic"
    return _("Open With %s") % app.get_display_name(), app.get_icon() or "document-open-symbolic"


def launch_default(gfile, widget):
    ctx = widget.get_display().get_app_launch_context()
    try:
        Gio.AppInfo.launch_default_for_uri(gfile.get_uri(), ctx)
    except GLib.Error as err:
        show_error(widget.get_toplevel(), _("Could not open"), err.message)


def is_jump(info):
    """Is the listing entry `info` describes a pointer to another location
    rather than a file or folder of its own? The computers of network:///
    and the shares in a server's list are: entering one means going to
    where it points (which may have to be mounted first), so they open on
    activation like files do, not on selection like folders."""
    return info is not None and info.get_file_type() in (Gio.FileType.SHORTCUT,
                                                         Gio.FileType.MOUNTABLE)


def jump_target(info):
    """Where a pointer entry (see is_jump) leads, or None when it does not
    say (an unmounted drive: it has to be mounted to find out) or is none."""
    if not is_jump(info):
        return None
    uri = info.get_attribute_string("standard::target-uri")
    return Gio.File.new_for_uri(uri) if uri else None


def mount_operation(widget):
    """A GtkMountOperation (the password / question dialogs of mounting)
    for the window `widget` is in."""
    top = widget.get_toplevel() if widget is not None else None
    return Gtk.MountOperation.new(top if isinstance(top, Gtk.Window) else None)


def not_mounted(err):
    return err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_MOUNTED)


def real_file(gfile):
    """The file an entry of recent:/// stands for; any other file is itself.
    The entries are virtual (recent:///<hash>): the backend cannot trash
    them, and deleting one only takes it off the list."""
    if gfile.get_uri_scheme() != "recent":
        return gfile
    try:
        info = gfile.query_info("standard::target-uri", Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        return gfile
    uri = info.get_attribute_string("standard::target-uri")
    return Gio.File.new_for_uri(uri) if uri else gfile


def forget_recent(gfile):
    """The file behind the recent:/// entry `gfile` is gone: take the entry
    off the list now rather than wait for the backend to notice."""
    if gfile.get_uri_scheme() == "recent":
        try:
            gfile.delete(None)
        except GLib.Error:
            pass


def delete_recursive(gfile):
    """Permanently delete a file or directory tree (symlinks are not followed)."""
    info = gfile.query_info("standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        for child in gfile.enumerate_children(
                "standard::name", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None):
            delete_recursive(gfile.get_child(child.get_name()))
    gfile.delete(None)


# -------------------------------------------------------------- dialogs ----
def confirm(parent, title, message, ok_label):
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True,
                            message_type=Gtk.MessageType.QUESTION, text=title)
    dlg.format_secondary_text(message)
    dlg.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
    ok = dlg.add_button(ok_label, Gtk.ResponseType.OK)
    ok.get_style_context().add_class("destructive-action")
    dlg.set_default_response(Gtk.ResponseType.CANCEL)
    response = dlg.run()
    dlg.destroy()
    return response == Gtk.ResponseType.OK


def show_error(parent, title, message):
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True,
                            message_type=Gtk.MessageType.ERROR,
                            buttons=Gtk.ButtonsType.CLOSE, text=title)
    dlg.format_secondary_text(message)
    dlg.run()
    dlg.destroy()
