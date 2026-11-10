"""Finch — a Finder-style Miller-column file browser for Linux (GTK 3).

Scope: browse, preview, open, rename, copy / cut / paste, drag and drop and
the trash.
Anything more involved is left to Nemo.

Package layout:
    util         small helpers: sorting, state file, dialogs, launching
    listing      what a directory listing does whatever it looks like (Listing)
    column       the Miller-column look: a list with a drag handle (Column)
    grid         the icon look: a hand-drawn grid (Grid)
    clipboard    copy / cut / paste through the system clipboard
    preview      the right-most file preview pane
    thumbs       preview caches: in-process LRU and the freedesktop thumbnail cache
    pathbar      the location bar: folder buttons or a text entry (PathBar)
    sidebar      GtkPlacesSidebar with section titles and device usage bars
    window       the main window: tool bar, navigation, context menu
    application  Gtk.Application, CSS and accelerators; `main()` lives here
"""
import gettext
import io
import os
import pkgutil

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib  # noqa: E402

APP_ID = "daocheng.Finch"
VERSION = "0.9.0"
GLib.set_prgname("finch")       # WM_CLASS "finch", matched by StartupWMClass
TEXT_DOMAIN = "finch"


def resource(name):
    """Bytes of a file shipped inside this package (works from the zipapp too)."""
    return pkgutil.get_data(__name__, name)


def _user_languages():
    """Language preference the way gettext reads it from the environment."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return [lang for lang in value.split(":") if lang]
    return []


def _load_translations():
    """The catalogue for the user's language from app/locale/ (compiled by
    po/update.sh), or an English pass-through when there is none."""
    for lang in _user_languages():
        if lang in ("C", "POSIX"):
            break
        base = lang.split(".")[0].split("@")[0]        # zh_CN.UTF-8 -> zh_CN
        for candidate in (base, base.split("_")[0]):   # zh_CN, zh
            try:
                data = resource(f"locale/{candidate}/LC_MESSAGES/{TEXT_DOMAIN}.mo")
            except OSError:
                data = None
            if data:
                return gettext.GNUTranslations(io.BytesIO(data))
    return gettext.NullTranslations()


_translations = _load_translations()
_ = _translations.gettext
ngettext = _translations.ngettext

CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "finch")
STATE_FILE = os.path.join(CONFIG_DIR, "state.json")

ATTRS = ",".join([
    "standard::name", "standard::display-name", "standard::type",
    "standard::icon", "standard::is-hidden", "standard::is-backup",
    "standard::content-type", "standard::size", "standard::symlink-target",
    "time::modified", "time::created", "trash::orig-path",
])
DEFAULT_COL_WIDTH = 240
MIN_COL_WIDTH = 140
MAX_PREVIEW_CONTENT = 720       # preview content never grows wider than this
MIN_PREVIEW_MEDIA = 160         # the picture / text box of the preview is this tall at least
MAX_PREVIEW_MEDIA = 720         # ...and grows with the window up to this
MAX_TEXT_PREVIEW = 8 * 1024
MAX_IMAGE_PREVIEW = 40 * 1024 * 1024
TEXT_LIKE = {
    "application/json", "application/xml", "application/x-shellscript",
    "application/javascript", "application/x-yaml", "application/toml",
    "application/x-desktop", "application/x-php",
}

# column indices of the ListStore
C_ICON, C_NAME, C_ISDIR, C_FILE, C_INFO = range(5)
