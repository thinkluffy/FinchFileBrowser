"""Progress reporting, cancel (with cleanup) and the job queue."""
import os, shutil, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gio, Gtk
from app.application import FinchApp
from app import transfer as trmod
from app.transfer import Transfer as Job

tmp = tempfile.mkdtemp(prefix="finch-tr-")
os.makedirs(os.path.join(tmp, "src", "tree", "sub"))
os.mkdir(os.path.join(tmp, "dst"))
with open(os.path.join(tmp, "src", "big.bin"), "wb") as f:
    f.write(os.urandom(1024 * 1024) * 64)           # 64 MB
with open(os.path.join(tmp, "src", "small.txt"), "w") as f:
    f.write("hello")
with open(os.path.join(tmp, "src", "tree", "sub", "deep.txt"), "w") as f:
    f.write("deep")
F = lambda *p: Gio.File.new_for_path(os.path.join(tmp, *p))

fails = []
def check(what, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + what, extra if not ok else "", flush=True)
    if not ok: fails.append(what)

app = FinchApp()
progress_seen = []

def win(): return app.get_active_window()

def s1():
    w = win()
    check("progress box hidden at start", not w.progress_box.get_visible())
    # Local copies finish in milliseconds; stand in a slow disk that copies in
    # 1 MB chunks, honours the cancellable and reports progress like GIO.
    def slow_copy(src, dst, cancellable, progress):
        if os.path.islink(src.get_path()):
            os.symlink(os.readlink(src.get_path()), dst.get_path()); return
        with open(src.get_path(), "rb") as fi, open(dst.get_path(), "wb") as fo:
            total, done = os.fstat(fi.fileno()).st_size, 0
            while True:
                if cancellable.is_cancelled():
                    raise GLib.Error.new_literal(Gio.io_error_quark(), "cancelled", Gio.IOErrorEnum.CANCELLED)
                chunk = fi.read(1024 * 1024)
                if not chunk: break
                fo.write(chunk); done += len(chunk); time.sleep(0.02)
                progress(done, total, None)
    trmod._copy_file = slow_copy
    w.start_transfer([F("src", "big.bin"), F("src", "small.txt"), F("src", "tree")], F("dst"), False, "Could not copy")
    # a second job queued behind the first
    w.start_transfer([F("src", "small.txt")], F("dst"), False, "Could not copy")
    check("two jobs queued", len(w._jobs) == 2)
    GLib.timeout_add(100, sample)
    GLib.timeout_add(700, s2)
def sample():
    w = win()
    if w._jobs:
        progress_seen.append((w.progress_bar.get_fraction(), w.progress_label.get_text()))
        return True
    return False
def s2():
    w = win()
    if not any("Copying" in t for _f, t in progress_seen):
        import faulthandler; faulthandler.dump_traceback(all_threads=True)
        print("JOBS:", [(j.scanning, j.done_bytes, j.total_bytes, j.cancelled) for j in w._jobs], flush=True)
    check("progress box shown while running", w.progress_box.get_visible())
    check("label names the file and sizes", any("Copying “big.bin”" in t and " of " in t for _f, t in progress_seen),
          repr(progress_seen[:3]))
    check("queue shown in label", any("(1 more waiting)" in t for _f, t in progress_seen), repr(progress_seen[:3]))
    fr = [f for f, _t in progress_seen]
    check("fraction grows strictly between 0 and 1", len(fr) >= 3 and all(0 < f < 1 for f in fr[1:]) and fr == sorted(fr), repr(fr))
    check("partial file on disk", os.path.exists(os.path.join(tmp, "dst", "big.bin")))
    w.cancel_transfer()
    check("cancel button disabled while cancelling", not w.cancel_button.get_sensitive())
    GLib.timeout_add(800, s3)
    return False
def s3():
    w = win()
    check("partial copy removed after cancel", not os.path.exists(os.path.join(tmp, "dst", "big.bin")))
    check("rest of the cancelled job not copied", not os.path.exists(os.path.join(tmp, "dst", "tree")))
    check("queued job ran afterwards", os.path.exists(os.path.join(tmp, "dst", "small.txt")))
    check("queue empty and box hidden", not w._jobs and not w.progress_box.get_visible())
    check("cancel button re-enabled", w.cancel_button.get_sensitive())
    # move a tree within the same file system (rename): bytes accounted at once
    j = Job([F("src", "tree")], F("dst"), True)
    def done(job, created, errors, cancelled):
        check("move by rename completed", not errors and not cancelled and len(created) == 1)
        check("move accounted all bytes/files", (job.done_bytes, job.done_files, job.total_files) == (4, 1, 1),
              repr((job.done_bytes, job.done_files, job.total_bytes, job.total_files)))
        check("source gone", not os.path.exists(os.path.join(tmp, "src", "tree")))
        GLib.timeout_add(100, s4)
    j.start(lambda job: None, done)
    return False
def s4():
    # cancelling a move that fell back to copy keeps the source intact
    shm = tempfile.mkdtemp(prefix="finch-tr-", dir="/dev/shm")   # another file system
    os.makedirs(os.path.join(shm, "mtree"))
    with open(os.path.join(shm, "mtree", "big2.bin"), "wb") as f:
        f.write(b"\0" * (32 * 1024 * 1024))
    j = Job([Gio.File.new_for_path(os.path.join(shm, "mtree"))], F("dst"), True)
    def done(job, created, errors, cancelled):
        check("cross-fs move fell back to copy and was cancelled", cancelled and not created and not errors, repr(errors))
        check("source kept after cancelled move", os.path.exists(os.path.join(shm, "mtree", "big2.bin")))
        check("partial destination removed", not os.path.exists(os.path.join(tmp, "dst", "mtree")))
        shutil.rmtree(shm, ignore_errors=True)      # outside the sandbox: not cleaned up for us
        GLib.timeout_add(100, s5)
    def prog(job):
        if not job.scanning and job.done_bytes > 0 and not job.cancelled:
            job.cancel()
    j.start(prog, done)
    return False
def s5():
    # errors are still reported, and a bad item does not stop the others
    j = Job([F("src", "missing"), F("src", "small.txt")], F("dst"), False)
    def done(job, created, errors, cancelled):
        check("missing source reported, other item copied", len(errors) == 1 and len(created) == 1 and not cancelled,
              repr((errors, [c.get_basename() for c in created])))
        print("%d failures" % len(fails), flush=True)
        app.quit()
    j.start(lambda job: None, done)
    return False

GLib.timeout_add(1200, s1)
app.run(["finch", os.path.join(tmp, "dst")])
