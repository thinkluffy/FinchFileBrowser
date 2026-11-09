# Finch

A Finder-style Miller-column file browser for Linux, written in Python with
GTK 3 and made for Linux Mint Cinnamon. It is for **browsing, previewing and
opening files, plus sorting, multi-selection, renaming, copy / cut / paste, drag and
drop, compressing / extracting, tabs and the trash**; anything
more involved is left to Nemo. See [doc/manual.md](doc/manual.md) for how to use it (in Chinese).

## Build and run

The only runtime dependency is PyGObject (`python3-gi`, `gir1.2-gtk-3.0`),
which Mint ships by default. Building also needs the gettext tools
(`sudo apt install gettext`).

```bash
./build.sh
```

This produces, in `output/`:

- `output/finch` — a single-file executable (a Python zipapp containing the
  code and the compiled translations). Run it in place or copy it to
  `~/.local/bin/`.
- `output/finch.desktop` — an application-menu entry whose `Exec` points at
  that executable.

```bash
output/finch              # open the last directory (home on first run)
output/finch ~/Pictures   # open a given directory
cp output/finch.desktop ~/.local/share/applications/
```

For development there is no need to build; run straight from the source tree:

```bash
src/Finch/finch.py
```

## Layout

```
build.sh                    build script: compiles translations, packs output/finch
src/Finch/                  the program
  finch.py                  launcher for running from source
  app/__init__.py           constants, translation loading
  app/util.py               sorting, state file, dialogs, launching other apps
  app/column.py             one directory column: loading, rename, trash, keys
  app/clipboard.py          copy / cut / paste through the system clipboard (Nemo-compatible)
  app/transfer.py           copy / move jobs: background thread, progress, cancel
  app/archive.py            Compress… / Extract Here: zip, 7z, tar.* (Python or the 7z program)
  app/preview.py            the right-most file preview pane
  app/thumbs.py             preview caches: in-process LRU + ~/.cache/thumbnails (shared with Nemo)
  app/sidebar.py            sidebar: section headings, device usage bars
  app/tab.py                one tab: the strip of columns, its preview and history
  app/window.py             main window: title bar, tabs, context menu, status, file jobs
  app/application.py        Gtk.Application, accelerators, command line
  app/style.css             overrides on top of the GTK theme
  app/locale/               compiled translations (made by update.sh, not committed)
  po/                       translation template, per-language .po files, update.sh
  finch.desktop             menu entry template (build.sh fills in the path)
doc/                        documentation
output/                     build products, not committed
```

## Languages

The interface is localised with gettext. The source strings are English and
the language follows the system locale; Simplified Chinese is the only
translation so far, in `src/Finch/po/zh_CN.po`. After changing strings in the
source or in a translation, run:

```bash
src/Finch/po/update.sh   # regenerate finch.pot, merge the .po files, compile to src/Finch/app/locale/
```

`build.sh` runs this step itself before packing the translations into the
executable. To try another language temporarily, set the environment, e.g.
`LANGUAGE=zh_CN output/finch`.

The sidebar entries Recent, Home, Trash and so on are translated by GTK
itself, so they only switch language when the matching system language pack
is installed (`language-pack-gnome-zh-hans` on Mint for Chinese).
