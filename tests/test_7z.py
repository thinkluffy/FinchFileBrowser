"""Password-protected zip / 7z via the 7z program: contents, encryption, progress, cancel, dialog routing."""
import os, sys, tempfile, subprocess, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gio, Gtk
from app.application import FinchApp
from app import window as winmod
from app.archive import SevenZip, Archive, needs_sevenzip, unique_archive, sevenzip

tmp = tempfile.mkdtemp(prefix="finch-7z-")
os.makedirs(os.path.join(tmp, "proj", "src"))
with open(os.path.join(tmp, "proj", "src", "a.txt"), "w") as f: f.write("alpha")
with open(os.path.join(tmp, "proj", "README.md"), "w") as f: f.write("# hi")
with open(os.path.join(tmp, "big.bin"), "wb") as f: f.write(os.urandom(1024 * 1024) * 80)   # 80 MB
F = lambda *p: Gio.File.new_for_path(os.path.join(tmp, *p))
fails = []
def check(what, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + what, extra if not ok else "", flush=True)
    if not ok: fails.append(what)
def sz(*args):
    return subprocess.run([sevenzip(), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)

check("7z found", sevenzip() is not None)
check("routing: password → 7z program", needs_sevenzip("x.zip", "pw") and needs_sevenzip("x.7z", "") and not needs_sevenzip("x.zip", "") and not needs_sevenzip("x.tar.gz", ""))

app = FinchApp()
def win(): return app.get_active_window()
steps = []
def step(f): steps.append(f); return f

@step
def s_zip_pw():
    def done(job, created, errors, cancelled):
        check("encrypted zip created", len(created) == 1 and not errors and not cancelled, repr(errors))
        p = os.path.join(tmp, "secret.zip")
        with zipfile.ZipFile(p) as zf:
            names = zf.namelist()
            check("zip lists relative paths", sorted(names) == ["proj/", "proj/README.md", "proj/src/", "proj/src/a.txt"], repr(names))
            check("zip entries flagged encrypted", all(i.flag_bits & 0x1 for i in zf.infolist() if not i.is_dir()))
        check("7z tests ok with the password", sz("t", "-phunter2", p).returncode == 0)
        check("7z fails with a wrong password", sz("t", "-pwrong", p).returncode != 0)
        check("progress reached 100%", job.done_bytes == job.total_bytes and job.total_files == 2, repr((job.done_bytes, job.total_bytes, job.total_files)))
        next_step()
    SevenZip([F("proj")], F("secret.zip"), "hunter2").start(lambda j: None, done)

@step
def s_7z_pw():
    def done(job, created, errors, cancelled):
        check("encrypted 7z created", len(created) == 1 and not errors, repr(errors))
        p = os.path.join(tmp, "secret.7z")
        check("7z: names hidden without password", sz("l", "-pwrong", p).returncode != 0)
        out = sz("l", "-phunter2", p)
        check("7z: lists with the password", out.returncode == 0 and "proj/src/a.txt" in out.stdout.replace(os.sep, "/"), out.stdout[-300:])
        next_step()
    SevenZip([F("proj")], F("secret.7z"), "hunter2").start(lambda j: None, done)

@step
def s_7z_plain():
    def done(job, created, errors, cancelled):
        check("plain 7z created and testable", len(created) == 1 and not errors and sz("t", os.path.join(tmp, "plain.7z")).returncode == 0)
        next_step()
    SevenZip([F("proj")], F("plain.7z"), "").start(lambda j: None, done)

@step
def s_cancel():
    seen = []
    def prog(job):
        if not job.scanning and 0 < job.done_bytes < job.total_bytes and not job.cancelled:
            seen.append(job.fraction); job.cancel()
    def done(job, created, errors, cancelled):
        check("7z cancelled mid-way with progress seen", cancelled and not created and seen and 0 < seen[0] < 1, repr((cancelled, created, seen, errors)))
        check("partial archive removed", not os.path.exists(os.path.join(tmp, "big.7z")))
        check("7z process gone", job._proc.poll() is not None)
        next_step()
    SevenZip([F("big.bin")], F("big.7z"), "pw").start(prog, done)

@step
def s_missing():
    def done(job, created, errors, cancelled):
        check("missing item: error reported, archive still made", len(errors) == 1 and len(created) == 1 and "7z:" in errors[0], repr(errors))
        next_step()
    SevenZip([F("nope"), F("proj")], F("part.zip"), "").start(lambda j: None, done)

@step
def s_window():
    w = win()
    winmod.ask_archive = lambda parent, files, dest_dir: (unique_archive(dest_dir, "viawin", ".zip"), "pw")
    w.compress([F("proj")], F())
    check("window routes password to SevenZip", len(w._jobs) == 1 and isinstance(w._jobs[0], SevenZip))
    winmod.ask_archive = lambda parent, files, dest_dir: (unique_archive(dest_dir, "viawin2", ".zip"), "")
    w.compress([F("proj")], F())
    check("window routes plain zip to Archive", len(w._jobs) == 2 and isinstance(w._jobs[1], Archive))
    GLib.timeout_add(2500, next_step)

@step
def s_end():
    w = win()
    check("both window jobs finished", not w._jobs and os.path.exists(os.path.join(tmp, "viawin.zip")) and os.path.exists(os.path.join(tmp, "viawin2.zip")))
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
