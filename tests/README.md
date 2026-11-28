# Tests

GUI tests for Finch. Each is a plain Python script that starts the real
application, drives it through its own methods and signals, and prints one
`PASS …` or `FAIL …` line per check.

    tests/run.sh                 # every tests/test_*.py
    tests/run.sh pathbar grid    # tests/test_pathbar.py and tests/test_grid.py
    tests/run.sh -v network      # also print the test's whole output
    tests/run.sh -- CMD ARGS…    # run any command in the same environment

The whole suite takes about ten minutes: every test gets a fresh environment.

## What `run.sh` isolates

- **Display.** A nested X server (Xephyr, `:7` unless `FINCH_TEST_DISPLAY`
  says otherwise). Its window is minimised as soon as it is up, so that a
  click of yours cannot land in the application under test.
- **Session.** Its own D-Bus session, hence its own gvfs: the recently used
  list, the trash and anything mounted are private to the test.
- **Files.** Config, data, cache and runtime directories in `~/finch-sandbox.XXXX`
  (`$FINCH_SANDBOX`, removed afterwards). It is in the home folder so that
  trashing a file there stays on one filesystem. `~/.config/finch`, the real
  recently used list and the real trash are never read or written.

The **system bus is not isolated**: real drives and udisks are visible to the
tests. No test opens or mounts a drive; keep it that way.

`TMPDIR` points into the sandbox too, so scratch folders made with
`tempfile` go with it. Some tests make theirs in the home folder instead
(`~/finch-…`) and delete it at the end; one that times out can leave it
behind.

Folders are watched with inotify. When the user's inotify instances are used up
(`fs.inotify.max_user_instances`, 128 by default; a second desktop session is
enough), no monitor can be made and every check that waits for one fails, as
does Recent: look for "Too many open files" in the output of `-v`.

Needs `Xephyr`, `xdotool`, `xdpyinfo`, `dbus-run-session` and `python3-gi`;
the archive tests need `7z`. `FINCH_TEST_TIMEOUT` is the limit per test in
seconds (default 150).

## The tests

| Test | Covers |
| --- | --- |
| `test_pathbar` | path bar: folder buttons, the text entry, Ctrl+L |
| `test_crumb_back` | clicking a path bar level goes back to that column instead of rebuilding |
| `test_stale_crumb` | deleted folders leave the path bar |
| `test_history` | back / forward |
| `test_leaf`, `test_target` | what the location and current folder are with a file or folder selected |
| `test_start_home` | starting without arguments |
| `test_tabs`, `test_tabdrop`, `test_detach` | tabs: opening, switching, dragging between windows |
| `test_grid`, `test_emblems` | icon view and its emblems |
| `test_view_switch` | switching between columns and icon view stays in the current folder, and back / forward across the two |
| `test_apk` | Android packages: launcher icon in the preview pane, the icon view and the rows of the columns; package name and version in the preview's details |
| `test_row_pictures` | thumbnails of pictures and videos as the row icons of the columns: only for the rows in view, kept across reloads, a video's once the preview has rendered it |
| `test_multi`, `test_sort` | multi-selection, sorting |
| `test_dnd` | where drops go and which are refused |
| `test_recent_delete` | trash / delete / remove in Recent |
| `test_recent_open` | a file opened from Finch goes on the recently used list; opened from Recent it is the real file that is launched; thumbnails and preview of Recent's entries |
| `test_network` | sidebar Network row, entering computers and shares, mounting on demand |
| `test_preview`, `test_preview_info`, `test_preview_width`, `test_flash` | preview pane |
| `test_openwith`, `test_about` | context menu "Open With …", About dialog |
| `test_transfer`, `test_archive`, `test_extract`, `test_7z` | copy / move jobs, compress, extract, passwords |

`test_network` uses stand-ins that need no server: the "File System" entry of
`computer:///` for an entry that points elsewhere, and a zip opened through
gvfs' archive backend for a location that has to be mounted. Password
prompts and real SMB shares are not covered.

`test_recent_open` never starts a program: it replaces
`Gio.AppInfo.launch_default_for_uri` with a recorder.

`test_apk` reads four small packages in `data/`, built by the real `aapt2`
(`data/make_apks.py` rebuilds them; needs the Android SDK).
`test_row_pictures` uses `data/clip.avi`, five frames of a test pattern made
with `gst-launch-1.0` (`videotestsrc ! jpegenc ! avimux`); its check of the
preview's frame is skipped without `ffmpegthumbnailer`.

`test_emblems.py` and `test_about.py` save a screenshot when given a file
name: `tests/run.sh -- python3 tests/test_emblems.py /tmp/emblems.png`.

## Not run by default

- `outdated/`: ten tests written against earlier internals (key handling,
  clipboard, preview details for PDF / video / image sizes, caching). They
  fail or crash as they are and need rewriting before they say anything
  about the application.
- `manual/`: two tests that send real pointer events and need a window
  manager. In the nested server they skip themselves.

Either can still be run by path: `tests/run.sh tests/outdated/test_cache.py`.

## History

These scripts lived in a temporary folder until November 2026 and were lost
in a reboot. They were rebuilt from session logs, and several assertions
were then updated to what the application does now. A check that looks
arbitrary may be one of those.
