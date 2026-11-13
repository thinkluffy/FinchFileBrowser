"""Entry point: the file browser, or with --service the FileManager1 service."""
import sys


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if argv[1:2] == ["--service"]:
        # Imported here, and nothing of Gtk with it: the service starts
        # before there is a display.
        from .service import run
        return run(argv)
    from .application import FinchApp
    return FinchApp().run(argv)
