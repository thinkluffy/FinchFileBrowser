"""Copying and moving files as a cancellable background job with progress.

A Job copies (or moves) a list of files into a directory on a worker thread.
It first walks the sources to learn how many files and bytes are coming, then
transfers them one by one, reporting progress on the main loop no more often
than every PROGRESS_INTERVAL seconds. cancel() stops it between files or in
the middle of one; a partially written destination is removed, so a cancelled
job never leaves half a file behind, and a cancelled move never loses the
source (the source is only deleted once its copy is complete).
"""
import os
import threading
import time

from gi.repository import Gio, GLib

from . import _
from .util import delete_recursive

_NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS
_SCAN_ATTRS = "standard::name,standard::type,standard::size"
PROGRESS_INTERVAL = 0.08


def _unique_child(parent, name, is_dir):
    """`name` inside `parent`, or "name (copy)", "name (copy 2)", … if taken."""
    child = parent.get_child(name)
    if not child.query_exists(None):
        return child
    stem, ext = (name, "") if is_dir else os.path.splitext(name)
    if not stem:                     # ".bashrc" has no stem to speak of
        stem, ext = name, ""
    n = 1
    while True:
        # TRANSLATORS: name given to a copy pasted next to the original
        label = _("%s (copy)") % stem if n == 1 else _("%s (copy %d)") % (stem, n)
        child = parent.get_child(label + ext)
        if not child.query_exists(None):
            return child
        n += 1


def _is_cancelled(err):
    return err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)


# The two GIO calls that do the actual work, kept as functions so tests can
# stand in slow or failing storage for them.
def _copy_file(src, dst, cancellable, progress):
    src.copy(dst, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, cancellable, progress, None)


def _move_file(src, dst, cancellable, progress):
    src.move(dst, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.ALL_METADATA,
             cancellable, progress, None)


class Job:
    """One copy or move operation. Read-only for the UI while it runs:
    `scanning`, `total_files`, `total_bytes`, `done_files`, `done_bytes`,
    `current_name` (the top-level item being transferred) and `move`."""

    def __init__(self, gfiles, dest_dir, move):
        self.gfiles, self.dest_dir, self.move = list(gfiles), dest_dir, move
        self.cancellable = Gio.Cancellable()
        self.scanning = True
        self.total_files = self.total_bytes = 0
        self.done_files = self.done_bytes = 0
        self.current_name = ""
        self._file_base = 0          # done_bytes before the file in progress
        self._last_report = 0.0
        self._report_pending = False
        self._on_progress = self._on_done = None
        self._lock = threading.Lock()

    # -- public ---------------------------------------------------------------
    def start(self, on_progress, on_done):
        """Run on a worker thread. on_progress(job) is called on the main loop
        while it works, on_done(job, created, errors, cancelled) once at the
        end: `created` are the new Gio.Files, `errors` messages for the items
        that failed."""
        self._on_progress, self._on_done = on_progress, on_done
        threading.Thread(target=self._work, daemon=True).start()

    def cancel(self):
        self.cancellable.cancel()

    @property
    def cancelled(self):
        return self.cancellable.is_cancelled()

    @property
    def fraction(self):
        if self.scanning or not self.total_bytes:
            return 0.0
        return min(1.0, self.done_bytes / self.total_bytes)

    # -- worker ---------------------------------------------------------------
    def _work(self):
        created, errors = [], []
        cancelled = False
        try:
            self._scan()
            self.scanning = False
            self._report(force=True)
            for src in self.gfiles:
                if self.cancelled:
                    cancelled = True
                    break
                name = src.get_basename() or src.get_uri()
                parent = src.get_parent()
                if self.move and parent is not None and parent.equal(self.dest_dir):
                    continue                 # cut and pasted where it already is
                if self.dest_dir.equal(src) or self.dest_dir.has_prefix(src):
                    errors.append((_("“%s” cannot be moved into itself.") if self.move
                                   else _("“%s” cannot be copied into itself.")) % name)
                    continue
                self.current_name = name
                self._report(force=True)
                dst = None
                try:
                    info = src.query_info("standard::type", _NOFOLLOW, self.cancellable)
                    is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
                    dst = _unique_child(self.dest_dir, name, is_dir)
                    (self._move if self.move else self._copy_tree)(src, dst)
                    created.append(dst)
                except GLib.Error as err:
                    self._discard(src, dst)
                    if _is_cancelled(err):
                        cancelled = True
                        break
                    errors.append(f"{name}: {err.message}")
                except OSError as err:
                    self._discard(src, dst)
                    errors.append(f"{name}: {err}")
        except GLib.Error as err:
            if _is_cancelled(err):
                cancelled = True
            else:
                errors.append(err.message)
        if self.cancelled:
            cancelled = True
        GLib.idle_add(self._deliver, created, errors, cancelled)

    def _scan(self):
        """Count what is about to be transferred (sources that will be skipped
        or refused are counted too; the error is reported later)."""
        for src in self.gfiles:
            self._scan_one(src)
            self._report()

    def _scan_one(self, gfile):
        try:
            info = gfile.query_info(_SCAN_ATTRS, _NOFOLLOW, self.cancellable)
            if info.get_file_type() == Gio.FileType.DIRECTORY:
                children = list(gfile.enumerate_children(_SCAN_ATTRS, _NOFOLLOW, self.cancellable))
            else:
                self.total_files += 1
                self.total_bytes += info.get_size()
                return
        except GLib.Error as err:
            if _is_cancelled(err):
                raise
            return                       # unreadable: the transfer will say so
        for child in children:
            self._scan_one(gfile.get_child(child.get_name()))

    # The GIO progress callback: `current` bytes of the file in progress.
    def _file_progress(self, current, _total, _data):
        self.done_bytes = self._file_base + current
        self._report()

    def _file_done(self, size):
        self._file_base += size
        self.done_bytes = self._file_base
        self.done_files += 1
        self._report()

    def _copy_tree(self, src, dst):
        info = src.query_info("standard::type,standard::size", _NOFOLLOW, self.cancellable)
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            dst.make_directory(self.cancellable)
            for child in src.enumerate_children("standard::name", _NOFOLLOW, self.cancellable):
                self._copy_tree(src.get_child(child.get_name()), dst.get_child(child.get_name()))
            try:
                src.copy_attributes(dst, Gio.FileCopyFlags.NONE, self.cancellable)
            except GLib.Error as err:
                if _is_cancelled(err):
                    raise
        else:
            _copy_file(src, dst, self.cancellable, self._file_progress)
            self._file_done(info.get_size())

    def _move(self, src, dst):
        try:
            _move_file(src, dst, self.cancellable, self._file_progress)
        except GLib.Error as err:
            # A directory across file systems: GIO will not recurse for us.
            if not err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.WOULD_RECURSE):
                raise
            self._copy_tree(src, dst)
            delete_recursive(src)
            return
        # Renamed or copied whole: account for everything underneath.
        size, files = self._tree_size(dst)
        self._file_base += size
        self.done_bytes = self._file_base
        self.done_files += files
        self._report()

    def _tree_size(self, gfile):
        try:
            info = gfile.query_info(_SCAN_ATTRS, _NOFOLLOW, None)
        except GLib.Error:
            return 0, 0
        if info.get_file_type() != Gio.FileType.DIRECTORY:
            return info.get_size(), 1
        size = files = 0
        try:
            for child in gfile.enumerate_children(_SCAN_ATTRS, _NOFOLLOW, None):
                s, f = self._tree_size(gfile.get_child(child.get_name()))
                size, files = size + s, files + f
        except GLib.Error:
            pass
        return size, files

    def _discard(self, src, dst):
        """Remove what a failed or cancelled transfer left at `dst`. A move by
        rename is atomic, so once the source is gone `dst` is complete and
        stays; a move that fell back to copying has not deleted its source
        yet, so dropping the partial copy loses nothing."""
        if dst is None or not dst.query_exists(None):
            return
        if self.move and not src.query_exists(None):
            return
        try:
            delete_recursive(dst)
        except GLib.Error:
            pass

    # -- reporting -------------------------------------------------------------
    def _report(self, force=False):
        now = time.monotonic()
        with self._lock:
            if self._report_pending:
                return
            if not force and now - self._last_report < PROGRESS_INTERVAL:
                return
            self._report_pending = True
            self._last_report = now
        GLib.idle_add(self._emit_progress)

    def _emit_progress(self):
        with self._lock:
            self._report_pending = False
        if self._on_progress is not None:
            self._on_progress(self)
        return False

    def _deliver(self, created, errors, cancelled):
        self._on_progress = None
        self._on_done(self, created, errors, cancelled)
        return False
