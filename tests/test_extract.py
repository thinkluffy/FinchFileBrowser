"""Extract Here: formats, relocation rules, password retry, cancel, unsafe members, menu."""
import os, sys, tempfile, subprocess, zipfile, tarfile, gzip, io
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "Finch"))
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gio, Gtk
from app.application import FinchApp
from app import window as winmod
from app.archive import Extract, archive_stem, is_archive, sevenzip
from app import C_NAME

tmp = tempfile.mkdtemp(prefix="finch-ex-")
src = os.path.join(tmp, "src"); os.makedirs(os.path.join(src, "proj", "sub"))
with open(os.path.join(src, "proj", "sub", "a.txt"), "w") as f: f.write("alpha")
with open(os.path.join(src, "proj", "README.md"), "w") as f: f.write("# hi")
dest = os.path.join(tmp, "dest"); os.mkdir(dest)
os.mkdir(os.path.join(dest, "proj"))                       # name already taken in dest
def mkzip(name, items, root=src):
    with zipfile.ZipFile(os.path.join(dest, name), "w") as zf:
        for it in items:
            p = os.path.join(root, it)
            if os.path.isdir(p):
                for d, _dirs, files in os.walk(p):
                    for fn in files:
                        full = os.path.join(d, fn); zf.write(full, os.path.relpath(full, root))
            else: zf.write(p, it)
mkzip("proj.zip", ["proj"])
with open(os.path.join(src, "one.txt"), "w") as f: f.write("one")
with open(os.path.join(src, "two.txt"), "w") as f: f.write("two")
with tarfile.open(os.path.join(dest, "multi.tar.gz"), "w:gz") as tf:
    tf.add(os.path.join(src, "one.txt"), "one.txt"); tf.add(os.path.join(src, "two.txt"), "two.txt")
with gzip.open(os.path.join(dest, "notes.txt.gz"), "wb") as f: f.write(b"notes")
sz = lambda *a: subprocess.run([sevenzip(), *a], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120, cwd=src)
sz("a", "-t7z", "-phunter2", "-mhe=on", os.path.join(dest, "secret.7z"), "proj")
sz("a", "-tzip", "-phunter2", "-mem=AES256", os.path.join(dest, "secret.zip"), "proj")
with open(os.path.join(src, "big.bin"), "wb") as f: f.write(os.urandom(1024 * 1024) * 12)
with tarfile.open(os.path.join(dest, "big.tar.gz"), "w:gz", compresslevel=1) as tf:
    tf.add(os.path.join(src, "big.bin"), "big.bin")
# a tar with a member that escapes the folder
with tarfile.open(os.path.join(dest, "evil.tar"), "w") as tf:
    ti = tarfile.TarInfo("../evil.txt"); data = b"bad"; ti.size = len(data); tf.addfile(ti, io.BytesIO(data))
    ti = tarfile.TarInfo("good.txt"); ti.size = 4; tf.addfile(ti, io.BytesIO(b"good"))
F = lambda *p: Gio.File.new_for_path(os.path.join(tmp, *p))
D = F("dest")
fails = []
def check(what, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + what, extra if not ok else "", flush=True)
    if not ok: fails.append(what)
def leftovers(): return [n for n in os.listdir(dest) if n.startswith(".finch-extract")]

check("archive_stem", [archive_stem(n) for n in ("a.tar.gz", "b.zip", "c.txt.gz", "d.tgz", ".bashrc")] == ["a", "b", "c.txt", "d", ".bashrc"],
      repr([archive_stem(n) for n in ("a.tar.gz", "b.zip", "c.txt.gz", "d.tgz", ".bashrc")]))
ct = lambda n: Gio.content_type_guess(n, None)[0]
check("is_archive", all(is_archive(ct(n)) for n in ("a.zip", "a.tar.gz", "a.7z", "a.rar", "a.gz", "a.tar")) and not is_archive(ct("a.txt")) and not is_archive(ct("a.png")))

app = FinchApp()
def win(): return app.get_active_window()
steps = []
def step(f): steps.append(f); return f
def run(job, then): job.start(lambda j: None, then)

@step
def s_zip():
    def done(job, created, errors, cancelled):
        check("zip: one top-level folder moved out, unique name", not errors and [c.get_basename() for c in created] == ["proj (copy)"], repr((errors, [c.get_basename() for c in created])))
        check("zip: contents intact", open(os.path.join(dest, "proj (copy)", "sub", "a.txt")).read() == "alpha")
        check("zip: no temp folder left", not leftovers())
        next_step()
    run(Extract(F("dest", "proj.zip"), D), done)

@step
def s_targz():
    def done(job, created, errors, cancelled):
        check("tar.gz: several items go into a folder named after it", not errors and [c.get_basename() for c in created] == ["multi"], repr((errors, [c.get_basename() for c in created])))
        check("tar.gz: contents", sorted(os.listdir(os.path.join(dest, "multi"))) == ["one.txt", "two.txt"])
        check("tar.gz: progress reached the end", job.done_bytes == job.total_bytes > 0, repr((job.done_bytes, job.total_bytes)))
        next_step()
    run(Extract(F("dest", "multi.tar.gz"), D), done)

@step
def s_gz():
    def done(job, created, errors, cancelled):
        check("single .gz: inner file extracted", not errors and [c.get_basename() for c in created] == ["notes.txt"] and open(os.path.join(dest, "notes.txt")).read() == "notes", repr(errors))
        next_step()
    run(Extract(F("dest", "notes.txt.gz"), D), done)

@step
def s_7z_nopw():
    def done(job, created, errors, cancelled):
        check("7z without password: asks instead of failing", job.password_needed and not created and not errors, repr(errors))
        check("7z without password: nothing left behind", not leftovers())
        next_step()
    run(Extract(F("dest", "secret.7z"), D), done)

@step
def s_7z_wrongpw():
    def done(job, created, errors, cancelled):
        check("7z wrong password: asks again", job.password_needed and not created and not errors, repr(errors))
        next_step()
    run(Extract(F("dest", "secret.7z"), D, "nope"), done)

@step
def s_7z_pw():
    def done(job, created, errors, cancelled):
        check("7z with password: extracted", not errors and [c.get_basename() for c in created] == ["proj (copy 2)"], repr((errors, [c.get_basename() for c in created])))
        next_step()
    run(Extract(F("dest", "secret.7z"), D, "hunter2"), done)

@step
def s_zip_aes():
    def done(job, created, errors, cancelled):
        check("AES zip with password: extracted", not errors and len(created) == 1, repr(errors))
        next_step()
    run(Extract(F("dest", "secret.zip"), D, "hunter2"), done)

@step
def s_cancel():
    # gunzip of a few MB is over before the first throttled report: slow the reads down
    import time
    from app import archive as armod
    orig_read = armod._Reader.read
    def slow_read(self, size=-1):
        time.sleep(0.002); return orig_read(self, size)
    armod._Reader.read = slow_read
    seen = []
    def prog(job):
        if not job.scanning and 0 < job.done_bytes < job.total_bytes and not job.cancelled:
            seen.append(job.fraction); job.cancel()
    def done(job, created, errors, cancelled):
        check("cancel mid-tar: cancelled with progress seen", cancelled and not created and seen and 0 < seen[0] < 1, repr((cancelled, created, seen)))
        check("cancel: temp folder removed", not leftovers() and not os.path.exists(os.path.join(dest, "big.bin")))
        armod._Reader.read = orig_read
        next_step()
    Extract(F("dest", "big.tar.gz"), D).start(prog, done)

@step
def s_evil():
    def done(job, created, errors, cancelled):
        check("unsafe tar member refused, safe one kept", len(errors) == 1 and "evil" in errors[0] and len(created) == 1 and created[0].get_basename() == "good.txt", repr((errors, [c.get_basename() for c in created])))
        check("nothing escaped the destination", not os.path.exists(os.path.join(tmp, "evil.txt")))
        next_step()
    run(Extract(F("dest", "evil.tar"), D), done)

@step
def s_menu():
    w = win(); w.set_root(D)
    GLib.timeout_add(600, s_menu2)
def s_menu2():
    w = win(); col = w.columns[0]
    def labels(name):
        row = next(r for r in col.store if r[C_NAME] == name)
        col.view.set_cursor(row.path, None, False)
        w._on_context_menu(col, row[3], row[4], None)
        menu = Gtk.Menu.get_for_attach_widget(col.view)[-1]
        out = [i.get_label() for i in menu.get_children() if not isinstance(i, Gtk.SeparatorMenuItem)]
        menu.popdown(); menu.destroy(); return out
    check("menu: Extract Here on a zip", "Extract Here" in labels("proj.zip"))
    check("menu: Extract Here on tar.gz and 7z", "Extract Here" in labels("multi.tar.gz") and "Extract Here" in labels("secret.7z"))
    check("menu: not on a plain file", "Extract Here" not in labels("notes.txt"))
    # window flow: encrypted archive → password prompt (stood in) → retried and extracted
    asked = []
    winmod.ask_password = lambda parent, name: (asked.append(name), "hunter2")[1]
    w.extract([F("dest", "secret.zip")], D)
    check("window: job queued", len(w._jobs) == 1 and w.progress_box.get_visible())
    GLib.timeout_add(2500, lambda: (s_end(asked), False)[1])
    return False
def s_end(asked):
    w = win(); col = w.columns[0]
    check("window: password was asked for the archive", asked == ["secret.zip"], repr(asked))
    sel = col.selected()[0]
    check("window: retried with password, result selected", not w._jobs and sel is not None and sel.get_basename().startswith("proj (copy"), repr(sel and sel.get_basename()))
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
