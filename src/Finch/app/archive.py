"""Compressing files into an archive and extracting one, as background jobs.

Archive packs a list of local files and folders into one zip, tar.gz or
tar.xz next to them, with Python's own zipfile / tarfile, so nothing has to
be installed. Files are streamed in chunks: progress is per byte and cancel
takes effect mid-file, after which the half-written archive is deleted.
SevenZip drives the 7z program instead, for the 7z format and for archives
with a password (AES-256; zipfile cannot write encrypted entries), parsing
the percentages it prints. Extract unpacks an archive next to itself.
ask_archive() and ask_password() are the small dialogs that go with them.
is_encrypted() and LockChecker tell the icon grid which archives want a
password, so it can mark them with a lock.
"""
import os
import re
import shutil
import stat
import subprocess
import tarfile
import threading
import uuid
import zipfile
from collections import OrderedDict

from gi.repository import Gio, GLib, Gtk, Pango

from . import _
from .thumbs import Worker
from .transfer import Job, _unique_child

# (suffix, label, tarfile mode or None), first is the default. ".zip" and
# ".7z" can be made by 7z, which is what a password needs; ".7z" only by it.
FORMATS = (
    (".zip", "zip", None),
    (".7z", "7z", None),
    (".tar.gz", "tar.gz", "w:gz"),
    (".tar.xz", "tar.xz", "w:xz"),
)
ENCRYPTABLE = (".zip", ".7z")
CHUNK = 1024 * 1024
_PERCENT = re.compile(rb"(\d{1,3})%")


def sevenzip():
    """Path of the 7z program, or None if it is not installed."""
    return shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")


def needs_sevenzip(name, password):
    """Whether an archive called `name` with `password` must be made by 7z."""
    return name.endswith(".7z") or bool(password)


def default_name(gfiles):
    """What the dialog proposes: one item's name without its extension, or
    "Archive" for several."""
    if len(gfiles) == 1:
        name = gfiles[0].get_basename() or ""
        path = gfiles[0].get_path()
        if path and not os.path.isdir(path):
            stem = os.path.splitext(name)[0]
            name = stem or name
        if name:
            return name
    # TRANSLATORS: default name of an archive made from several items
    return _("Archive")


def unique_archive(parent, name, suffix):
    """`name + suffix` inside `parent`, or "name 2", "name 3", … if taken."""
    child = parent.get_child(name + suffix)
    n = 2
    while child.query_exists(None):
        child = parent.get_child("%s %d%s" % (name, n, suffix))
        n += 1
    return child


class _Reader:
    """A file object that reports what tarfile reads from it, and stops the
    read when the job is cancelled."""

    def __init__(self, job, fobj):
        self.job, self.fobj, self.done = job, fobj, 0

    def read(self, size=-1):
        self.job._check_cancelled()
        data = self.fobj.read(size)
        self.done += len(data)
        self.job._file_progress(self.done)
        return data


class Archive(Job):
    """Pack `gfiles` into `dest`, a Gio.File ending in one of FORMATS'
    suffixes; `created` on completion is [dest]."""

    preparing = _("Preparing to compress…")
    verb = _("Compressing “%s”")

    def __init__(self, gfiles, dest):
        super().__init__(gfiles, dest.get_parent())
        self.dest = dest
        path = dest.get_path()
        self.mode = next((mode for suffix, _label, mode in FORMATS
                          if path.endswith(suffix)), None)

    def _run(self, created, errors):
        path = self.dest.get_path()
        try:
            if self.mode is None:
                with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for src in self.gfiles:
                        self._add_to_zip(zf, src, errors)
            else:
                with tarfile.open(path, self.mode) as tf:
                    for src in self.gfiles:
                        self._add_to_tar(tf, src, errors)
        except BaseException:
            self._discard(self.dest)
            raise
        if errors and not self.total_files:
            self._discard(self.dest)
        else:
            created.append(self.dest)

    # Each top-level source goes in under its own name; everything below it
    # keeps its relative path. Symlinks are stored as links, not followed.
    def _add_to_zip(self, zf, src, errors):
        name = src.get_basename()
        self.current_name = name
        self._report(force=True)
        for path, arcname in self._walk(src.get_path(), name, errors):
            self._check_cancelled()
            try:
                st = os.lstat(path)
                if stat.S_ISDIR(st.st_mode):
                    zf.writestr(zipfile.ZipInfo.from_file(path, arcname + "/"), b"")
                    continue
                if stat.S_ISLNK(st.st_mode):
                    info = zipfile.ZipInfo(arcname)
                    info.create_system = 3                # unix
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    zf.writestr(info, os.readlink(path))
                elif stat.S_ISREG(st.st_mode):
                    info = zipfile.ZipInfo.from_file(path, arcname)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with open(path, "rb") as fi, zf.open(info, "w") as fo:
                        self._pump(fi, fo)
                else:
                    continue                              # socket, device…
            except OSError as err:
                errors.append("%s: %s" % (arcname, err.strerror or err))
                st = None
            # The scan counted every non-directory (a symlink by its length).
            self._file_done(st.st_size if st is not None else 0)

    def _add_to_tar(self, tf, src, errors):
        name = src.get_basename()
        self.current_name = name
        self._report(force=True)
        for path, arcname in self._walk(src.get_path(), name, errors):
            self._check_cancelled()
            try:
                info = tf.gettarinfo(path, arcname)
                if info is None:                          # socket, etc.
                    continue
                if info.isreg():
                    with open(path, "rb") as fi:
                        tf.addfile(info, _Reader(self, fi))
                else:
                    tf.addfile(info)
                    if info.isdir():
                        continue
                size = os.lstat(path).st_size
            except OSError as err:
                errors.append("%s: %s" % (arcname, err.strerror or err))
                size = 0
            self._file_done(size)

    def _walk(self, path, arcname, errors):
        """Yield (path, archive name) for `path` and, if it is a directory,
        everything inside it, parents before children."""
        yield path, arcname
        if os.path.isdir(path) and not os.path.islink(path):
            try:
                names = sorted(os.listdir(path))
            except OSError as err:
                errors.append("%s: %s" % (arcname, err.strerror or err))
                return
            for child in names:
                yield from self._walk(os.path.join(path, child), arcname + "/" + child, errors)

    def _pump(self, fi, fo):
        done = 0
        while True:
            self._check_cancelled()
            chunk = fi.read(CHUNK)
            if not chunk:
                return
            fo.write(chunk)
            done += len(chunk)
            self._file_progress(done)


class _Uses7z:
    """Mix-in for jobs that run the 7z program: start it, feed its printed
    percentages into done_bytes, kill it on cancel."""

    _proc = None

    def cancel(self):
        super().cancel()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def _run_7z(self, cmd, cwd):
        """Run `cmd` to completion; return (exit code, stderr text). Raises
        the cancelled error if the job was cancelled meanwhile."""
        self._check_cancelled()
        self._proc = subprocess.Popen(
            cmd, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            if self.cancelled:                            # cancelled before Popen
                self._proc.terminate()
            self._follow(self._proc.stdout)
            stderr = self._proc.stderr.read().decode(errors="replace")
            code = self._proc.wait()
        finally:
            self._proc.stdout.close()
            self._proc.stderr.close()
        self._check_cancelled()
        return code, stderr

    def _follow(self, stream):
        while True:
            chunk = stream.read1(4096)
            if not chunk:
                return
            found = _PERCENT.findall(chunk)
            if found:
                pct = min(100, int(found[-1]))
                self.done_bytes = self.total_bytes * pct // 100
                self._report()

    @staticmethod
    def _7z_message(stderr, code):
        lines = [ln for ln in stderr.splitlines() if ln.strip() and "ERROR" not in ln]
        return "7z: " + ("\n".join(lines[-3:]) if lines else "exit %d" % code)


class SevenZip(_Uses7z, Job):
    """Pack `gfiles` into `dest` (.zip or .7z) with the 7z program, encrypted
    with AES-256 when `password` is given (7z archives also hide their file
    list). Progress comes from the percentages 7z prints."""

    preparing = _("Preparing to compress…")
    verb = _("Compressing “%s”")

    def __init__(self, gfiles, dest, password=""):
        super().__init__(gfiles, dest.get_parent())
        self.dest, self.password = dest, password

    def _run(self, created, errors):
        path = self.dest.get_path()
        kind = "7z" if path.endswith(".7z") else "zip"
        cmd = [sevenzip(), "a", "-y", "-t" + kind, "-bsp1", "-bso0", "-bse2"]
        if self.password:
            # The password is visible to other users of this machine in the
            # process list while 7z runs; file-roller does the same.
            cmd.append("-p" + self.password)
            cmd.append("-mhe=on" if kind == "7z" else "-mem=AES256")
        cmd.append("--")
        cmd.append(path)
        # Run inside the folder and name the items relative to it, so the
        # archive holds "folder/…" rather than any part of the full path.
        cmd.extend(g.get_basename() for g in self.gfiles)
        self.current_name = self.gfiles[0].get_basename() if self.gfiles else ""
        self._report(force=True)
        try:
            code, stderr = self._run_7z(cmd, self.dest_dir.get_path())
        except GLib.Error:
            self._discard(self.dest)
            raise
        if code in (0, 1):                                # 1: some items skipped
            created.append(self.dest)
            self.done_bytes, self.done_files = self.total_bytes, self.total_files
            self._report(force=True)
        else:
            self._discard(self.dest)
        if code != 0:
            errors.append(self._7z_message(stderr, code))


# ---------------------------------------------------------------- extract ----
# tar and compressed tar: Python unpacks these in one go (7z would need two
# passes for tar.gz); everything else is left to 7z, which also reads rar,
# single compressed files (.gz, .xz, …) and encrypted archives.
_TAR_TYPES = ("application/x-tar", "application/x-compressed-tar",
              "application/x-bzip2-compressed-tar", "application/x-bzip-compressed-tar",
              "application/x-xz-compressed-tar", "application/x-lzma-compressed-tar")
_7Z_TYPES = ("application/zip", "application/x-7z-compressed", "application/vnd.rar",
             "application/x-rar", "application/gzip", "application/x-bzip2",
             "application/x-bzip", "application/x-xz", "application/x-lzma",
             "application/zstd", "application/x-zstd-compressed-tar",
             "application/x-cpio", "application/x-lzip")
_TAR_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.lzma", ".tar.zst",
                 ".tgz", ".tbz2", ".txz")


def _is_type(content_type, kinds):
    return any(Gio.content_type_is_a(content_type, kind) for kind in kinds)


def is_tar(content_type):
    return _is_type(content_type or "", _TAR_TYPES)


def is_archive(content_type):
    """Whether "Extract Here" applies to a file of this type on this machine."""
    content_type = content_type or ""
    if is_tar(content_type):
        return True
    if Gio.content_type_is_a(content_type, "application/zip"):
        return True
    return sevenzip() is not None and _is_type(content_type, _7Z_TYPES)


def archive_stem(name):
    """"photos" for "photos.tar.gz", "photos.zip", "photos.txt.gz"…"""
    lower = name.lower()
    for suffix in _TAR_SUFFIXES:
        if lower.endswith(suffix) and len(name) > len(suffix):
            return name[:-len(suffix)]
    stem = os.path.splitext(name)[0]
    return stem or name


# ---------------------------------------------------------- encryption ----
# Archive kinds that can carry a password: zip (and what is built on it),
# 7z and rar. tar and single compressed files cannot.
_LOCKABLE_7Z_TYPES = ("application/x-7z-compressed", "application/vnd.rar",
                      "application/x-rar")
_LOCK_ENTRIES = 4000                 # answers kept in memory
_locks = OrderedDict()               # stamp -> encrypted?
_locks_lock = threading.Lock()


def can_be_locked(content_type):
    """Whether a file of this type is an archive that might want a password
    (worth asking is_encrypted about)."""
    content_type = content_type or ""
    if Gio.content_type_is_a(content_type, "application/zip"):
        return True
    return sevenzip() is not None and _is_type(content_type, _LOCKABLE_7Z_TYPES)


def is_encrypted(path, content_type):
    """Whether the archive at `path` wants a password. A zip's entries carry
    a flag that zipfile reads from the central directory in-process; 7z
    and rar are asked of the 7z program, whose listing says "Encrypted = +"
    for protected entries and refuses outright an archive whose file list
    is hidden too (7z -mhe). Anything unreadable counts as not encrypted."""
    if not path:
        return False
    content_type = content_type or ""
    if Gio.content_type_is_a(content_type, "application/zip"):
        try:
            with zipfile.ZipFile(path) as zf:
                return any(info.flag_bits & 0x1 for info in zf.infolist())
        except (OSError, zipfile.BadZipFile, RuntimeError, ValueError, EOFError):
            return False
    if not _is_type(content_type, _LOCKABLE_7Z_TYPES) or sevenzip() is None:
        return False
    try:
        proc = subprocess.run([sevenzip(), "l", "-slt", "-p", "--", path],
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    out = proc.stdout.decode(errors="replace")
    if "Encrypted = +" in out:
        return True
    err = proc.stderr.decode(errors="replace")
    return proc.returncode != 0 and "password" in (out + err).lower()


def lock_get(key):
    """(known?, encrypted?) for the archive with cache key `key`."""
    with _locks_lock:
        if key not in _locks:
            return False, False
        _locks.move_to_end(key)
        return True, _locks[key]


def _lock_put(key, encrypted):
    with _locks_lock:
        _locks[key] = encrypted
        _locks.move_to_end(key)
        while len(_locks) > _LOCK_ENTRIES:
            _locks.popitem(last=False)


class LockChecker(Worker):
    """Finds out on a worker thread which archives are encrypted; requests
    are (key, path, content type) tuples."""

    def _known(self, key):
        return lock_get(key)[0]

    def _make(self, request):
        key, path, content_type = request
        _lock_put(key, is_encrypted(path, content_type))


class Extract(_Uses7z, Job):
    """Unpack `archive` into `dest_dir` ("Extract Here"): into a hidden
    temporary folder first, then one top-level item is moved out as it is
    and several are kept together in a folder named after the archive, so
    nothing is scattered and nothing already there is overwritten. If the
    archive wants a password that was not given, `password_needed` is set
    and nothing is created; the caller may ask and try again."""

    preparing = _("Preparing to extract…")
    verb = _("Extracting “%s”")

    def __init__(self, archive, dest_dir, password=""):
        super().__init__([archive], dest_dir)
        self.archive, self.password = archive, password
        self.password_needed = False

    def _run(self, created, errors):
        name = self.archive.get_basename()
        path = self.archive.get_path()
        self.current_name = name
        self._report(force=True)
        info = self.archive.query_info("standard::content-type", 0, self.cancellable)
        content_type = info.get_content_type() or ""
        # A fresh hidden folder beside the archive, same mode as a new folder.
        tmpdir = os.path.join(self.dest_dir.get_path(), ".finch-extract-" + uuid.uuid4().hex[:8])
        os.mkdir(tmpdir)
        try:
            if is_tar(content_type):
                self._extract_tar(path, tmpdir, errors)
            elif sevenzip() is not None:
                self._extract_7z(path, tmpdir, errors)
            else:
                self._extract_zip(path, tmpdir, errors)
            if self.password_needed or not os.listdir(tmpdir):
                return
            created.extend(self._relocate(tmpdir, name))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _relocate(self, tmpdir, name):
        entries = os.listdir(tmpdir)
        if len(entries) == 1:
            src = os.path.join(tmpdir, entries[0])
            dst = _unique_child(self.dest_dir, entries[0],
                                os.path.isdir(src) and not os.path.islink(src))
        else:
            src = tmpdir
            dst = _unique_child(self.dest_dir, archive_stem(name), True)
        os.rename(src, dst.get_path())
        return [dst]

    def _extract_7z(self, path, tmpdir, errors):
        # "-p" with an empty password stops 7z from waiting for one on stdin.
        cmd = [sevenzip(), "x", "-y", "-bsp1", "-bso0", "-bse2", "-o" + tmpdir,
               "-p" + self.password, "--", path]
        code, stderr = self._run_7z(cmd, self.dest_dir.get_path())
        if code == 0:
            self.done_bytes = self.total_bytes
            self._report(force=True)
            return
        if "password" in stderr.lower():
            self.password_needed = True
            return
        errors.append(self._7z_message(stderr, code))

    def _extract_tar(self, path, tmpdir, errors):
        data_filter = getattr(tarfile, "data_filter", None)   # Python ≥ 3.12
        with open(path, "rb") as raw:
            reader = _Reader(self, raw)          # progress = bytes of the archive read
            with tarfile.open(fileobj=reader, mode="r|*") as tf:
                for member in tf:
                    self._check_cancelled()
                    try:
                        if data_filter is not None:
                            tf.extract(member, tmpdir, filter=data_filter)
                        else:
                            tf.extract(member, tmpdir)
                    except (OSError, tarfile.TarError) as err:
                        errors.append("%s: %s" % (member.name, err))

    def _extract_zip(self, path, tmpdir, errors):
        pwd = self.password.encode() if self.password else None
        done = 0
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                self._check_cancelled()
                try:
                    zf.extract(info, tmpdir, pwd=pwd)
                except RuntimeError as err:               # "… is encrypted, password required"
                    if "password" in str(err).lower():
                        self.password_needed = True
                        return
                    errors.append("%s: %s" % (info.filename, err))
                except NotImplementedError:
                    errors.append(_("%s: install p7zip-full to extract this kind of zip.")
                                  % info.filename)
                except (OSError, zipfile.BadZipFile) as err:
                    errors.append("%s: %s" % (info.filename, err))
                done += info.compress_size
                self._file_progress(done)


def ask_password(parent, name):
    """Ask for the password of the archive `name`; None if cancelled."""
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True,
                            message_type=Gtk.MessageType.QUESTION,
                            # TRANSLATORS: %s is the archive's file name
                            text=_("“%s” is password protected") % name)
    dlg.format_secondary_text(_("Enter the password to extract it."))
    dlg.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
    ok = dlg.add_button(_("Extract"), Gtk.ResponseType.OK)
    ok.get_style_context().add_class("suggested-action")
    dlg.set_default_response(Gtk.ResponseType.OK)
    entry = Gtk.Entry(visibility=False, activates_default=True,
                      input_purpose=Gtk.InputPurpose.PASSWORD, margin_start=12, margin_end=12)
    entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic")
    entry.set_icon_activatable(Gtk.EntryIconPosition.SECONDARY, True)
    entry.connect("icon-press", lambda e, *_a: e.set_visibility(not e.get_visibility()))
    dlg.get_message_area().pack_end(entry, False, False, 0)
    entry.show()
    entry.grab_focus()
    password = entry.get_text() if dlg.run() == Gtk.ResponseType.OK else None
    dlg.destroy()
    return password


def ask_archive(parent, gfiles, dest_dir):
    """Ask for the archive's name, format and (optional) password; return
    (Gio.File to create, password) or None if cancelled."""
    dlg = Gtk.Dialog(title=_("Compress"), transient_for=parent, modal=True,
                     use_header_bar=False)
    dlg.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
    ok = dlg.add_button(_("Create"), Gtk.ResponseType.OK)
    ok.get_style_context().add_class("suggested-action")
    dlg.set_default_response(Gtk.ResponseType.OK)

    have_7z = sevenzip() is not None
    formats = [f for f in FORMATS if have_7z or f[0] != ".7z"]
    grid = Gtk.Grid(row_spacing=8, column_spacing=8, margin=16)
    entry = Gtk.Entry(text=default_name(gfiles), hexpand=True, activates_default=True,
                      width_chars=30)
    combo = Gtk.ComboBoxText()
    for _suffix, label, _mode in formats:
        combo.append_text(label)
    combo.set_active(0)
    grid.attach(Gtk.Label(label=_("Name"), xalign=1.0), 0, 0, 1, 1)
    grid.attach(entry, 1, 0, 1, 1)
    grid.attach(combo, 2, 0, 1, 1)
    # TRANSLATORS: %s is the folder the archive is created in
    where = Gtk.Label(label=_("Created in %s") % (dest_dir.get_parse_name()),
                      xalign=0.0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=50)
    where.get_style_context().add_class("dim-label")
    grid.attach(where, 1, 1, 2, 1)
    password = Gtk.Entry(visibility=False, activates_default=True, hexpand=True,
                         input_purpose=Gtk.InputPurpose.PASSWORD,
                         placeholder_text=_("Optional"))
    password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic")
    password.set_icon_activatable(Gtk.EntryIconPosition.SECONDARY, True)
    password.connect("icon-press", lambda e, *_a: e.set_visibility(not e.get_visibility()))
    grid.attach(Gtk.Label(label=_("Password"), xalign=1.0), 0, 2, 1, 1)
    grid.attach(password, 1, 2, 2, 1)
    hint = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=50)
    hint.get_style_context().add_class("dim-label")
    grid.attach(hint, 1, 3, 2, 1)
    dlg.get_content_area().add(grid)
    dlg.show_all()
    entry.grab_focus()

    def update(*_args):
        suffix = formats[combo.get_active()][0]
        can_encrypt = have_7z and suffix in ENCRYPTABLE
        password.set_sensitive(can_encrypt)
        if not have_7z:
            hint.set_text(_("Install p7zip-full to make 7z or password-protected archives."))
        elif suffix not in ENCRYPTABLE:
            hint.set_text(_("tar archives cannot have a password."))
        elif password.get_text():
            hint.set_text(_("AES-256; the file names are hidden too.") if suffix == ".7z"
                          else _("AES-256; the file names stay visible."))
        else:
            hint.set_text("")
        ok.set_sensitive(bool(entry.get_text().strip()) and "/" not in entry.get_text())
    entry.connect("changed", update)
    combo.connect("changed", update)
    password.connect("changed", update)
    update()

    result = None
    if dlg.run() == Gtk.ResponseType.OK:
        name = entry.get_text().strip()
        suffix = formats[combo.get_active()][0]
        if name.endswith(suffix):
            name = name[:-len(suffix)]
        if name:
            pw = password.get_text() if suffix in ENCRYPTABLE else ""
            result = unique_archive(dest_dir, name, suffix), pw
    dlg.destroy()
    return result
