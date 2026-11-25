"""Compress: formats, contents, symlinks, cancel, dialog defaults, menu, selection."""
import os, sys, tempfile, tarfile, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gio, Gtk
from app.application import FinchApp
from app import archive as armod, window as winmod
from app.archive import Archive, default_name, unique_archive
from app import C_NAME

tmp = tempfile.mkdtemp(prefix="finch-ar-")
os.makedirs(os.path.join(tmp, "proj", "src", "deep"))
with open(os.path.join(tmp, "proj", "src", "deep", "a.txt"), "w") as f: f.write("alpha")
with open(os.path.join(tmp, "proj", "README.md"), "w") as f: f.write("# hi")
os.symlink("README.md", os.path.join(tmp, "proj", "link"))
with open(os.path.join(tmp, "notes.txt"), "w") as f: f.write("notes " * 1000)
with open(os.path.join(tmp, "big.bin"), "wb") as f: f.write(os.urandom(1024 * 1024) * 24)   # 24 MB, incompressible
F = lambda *p: Gio.File.new_for_path(os.path.join(tmp, *p))

fails = []
def check(what, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + what, extra if not ok else "", flush=True)
    if not ok: fails.append(what)

check("default name: single file drops extension", default_name([F("notes.txt")]) == "notes")
check("default name: single folder keeps name", default_name([F("proj")]) == "proj")
check("default name: several items", default_name([F("proj"), F("notes.txt")]) == "Archive")
open(os.path.join(tmp, "taken.zip"), "w").close()
check("unique name counts up", unique_archive(F(), "taken", ".zip").get_basename() == "taken 2.zip")

app = FinchApp()
def win(): return app.get_active_window()
steps = []
def step(f): steps.append(f); return f
EXPECT = ["proj/", "proj/README.md", "proj/link", "proj/src/", "proj/src/deep/", "proj/src/deep/a.txt", "notes.txt"]

def run_job(job, then):
    job.start(lambda j: None, lambda j, created, errors, cancelled: then(j, created, errors, cancelled))

@step
def s_zip():
    def done(job, created, errors, cancelled):
        check("zip created without errors", len(created) == 1 and not errors and not cancelled, repr(errors))
        with zipfile.ZipFile(os.path.join(tmp, "out.zip")) as zf:
            names = zf.namelist()
            check("zip has the whole tree", sorted(names) == sorted(EXPECT), repr(names))
            check("zip file content", zf.read("proj/src/deep/a.txt") == b"alpha")
            info = zf.getinfo("proj/link")
            check("zip stores symlink as symlink", (info.external_attr >> 16) & 0o170000 == 0o120000 and zf.read("proj/link") == b"README.md")
            check("zip compresses text", zf.getinfo("notes.txt").compress_size < 200)
        check("job counted bytes and files (symlink counts)", (job.total_files, job.done_files, job.done_bytes == job.total_bytes) == (4, 4, True),
              repr((job.total_files, job.done_files, job.done_bytes, job.total_bytes)))
        next_step()
    run_job(Archive([F("proj"), F("notes.txt")], F("out.zip")), done)

@step
def s_tgz():
    def done(job, created, errors, cancelled):
        check("tar.gz created", len(created) == 1 and not errors)
        with tarfile.open(os.path.join(tmp, "out.tar.gz")) as tf:
            names = [m.name + ("/" if m.isdir() else "") for m in tf.getmembers()]
            check("tar.gz has the whole tree", sorted(names) == sorted(EXPECT), repr(names))
            link = tf.getmember("proj/link")
            check("tar keeps symlink", link.issym() and link.linkname == "README.md")
            check("tar file content", tf.extractfile("proj/src/deep/a.txt").read() == b"alpha")
        next_step()
    run_job(Archive([F("proj"), F("notes.txt")], F("out.tar.gz")), done)

@step
def s_txz():
    def done(job, created, errors, cancelled):
        with tarfile.open(os.path.join(tmp, "out.tar.xz")) as tf:
            check("tar.xz readable with same members", len(tf.getmembers()) == len(EXPECT))
        next_step()
    run_job(Archive([F("proj"), F("notes.txt")], F("out.tar.xz")), done)

@step
def s_cancel():
    seen = []
    def prog(job):
        if not job.scanning and 0 < job.done_bytes < job.total_bytes and not job.cancelled:
            seen.append(job.fraction); job.cancel()
    def done(job, created, errors, cancelled):
        check("cancelled mid-file", cancelled and not created and seen and 0 < seen[0] < 1, repr((cancelled, created, seen)))
        check("half-written archive removed", not os.path.exists(os.path.join(tmp, "big.zip")))
        next_step()
    job = Archive([F("big.bin")], F("big.zip"))
    job.start(prog, done)

@step
def s_missing():
    def done(job, created, errors, cancelled):
        check("unreadable item reported, archive still made", len(errors) == 1 and len(created) == 1, repr(errors))
        next_step()
    run_job(Archive([F("nope"), F("notes.txt")], F("part.zip")), done)

@step
def s_menu():
    w = win(); col = w.columns[0]
    def labels(gfile, info):
        w._on_context_menu(col, gfile, info, None)
        menu = Gtk.Menu.get_for_attach_widget(col.view)[-1]
        out = [i.get_label() for i in menu.get_children() if not isinstance(i, Gtk.SeparatorMenuItem)]
        menu.popdown(); menu.destroy(); return out      # or the next call finds this one again
    row = next(r for r in col.store if r[C_NAME] == "notes.txt")
    col.view.set_cursor(row.path, None, False)
    check("single item menu has Compress…", "Compress…" in labels(row[3], row[4]))
    col.view.get_selection().select_all()
    gfiles, infos = col.selected_all()
    check("multi-selection menu has Compress…", len(gfiles) > 1 and "Compress…" in labels(gfiles[0], infos[0]))
    check("empty-area menu has no Compress…", "Compress…" not in labels(None, None))
    # compress through the window: dialog stood in, job queued, result selected
    winmod.ask_archive = lambda parent, files, dest_dir: (unique_archive(dest_dir, "picked", ".zip"), "")
    w.compress([F("proj")], F())
    check("job queued and progress shown", len(w._jobs) == 1 and w.progress_box.get_visible())
    GLib.timeout_add(1500, next_step)

@step
def s_selected():
    w = win(); col = w.columns[0]
    sel = col.selected()[0]
    check("archive exists and is selected afterwards", os.path.exists(os.path.join(tmp, "picked.zip"))
          and sel is not None and sel.get_basename() == "picked.zip", repr(sel and sel.get_basename()))
    check("queue empty, progress hidden", not w._jobs and not w.progress_box.get_visible())
    print("%d failures" % len(fails), flush=True)
    app.quit()

idx = [0]
def next_step(*_a):
    if idx[0] < len(steps):
        f = steps[idx[0]]; idx[0] += 1
        GLib.timeout_add(200, lambda: (f(), False)[1])
    return False
GLib.timeout_add(1200, next_step)
app.run(["finch", tmp])
