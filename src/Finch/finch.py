#!/usr/bin/env python3
"""Finch — a Finder-style Miller-column file browser for Linux (GTK 3).

Launcher; the program lives in the `app` package next to this file.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from app.application import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
