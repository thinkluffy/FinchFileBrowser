"""`finch --service`: the session's org.freedesktop.FileManager1.

Programs that offer "Show in folder" (browsers, Electron apps such as
Feishu) do not start a file manager themselves: they ask whoever owns that
name on the session bus to show the file. This process owns the name and
starts an ordinary Finch for every request.

It has no window and uses neither Gtk nor the display, so it can be started
before the desktop is: Nemo's desktop process asks for the same name at
log-in and the first to ask keeps it, which is why this runs as a systemd
user service (finch-filemanager.service) rather than as an autostart entry.
Started later than Nemo, it waits in the bus's queue for the name.
"""
import os
import sys

from gi.repository import Gio, GLib

BUS_NAME = "org.freedesktop.FileManager1"
OBJECT_PATH = "/org/freedesktop/FileManager1"
INTERFACE = """
<node>
  <interface name='org.freedesktop.FileManager1'>
    <method name='ShowFolders'>
      <arg type='as' name='URIs' direction='in'/>
      <arg type='s' name='StartupId' direction='in'/>
    </method>
    <method name='ShowItems'>
      <arg type='as' name='URIs' direction='in'/>
      <arg type='s' name='StartupId' direction='in'/>
    </method>
    <method name='ShowItemProperties'>
      <arg type='as' name='URIs' direction='in'/>
      <arg type='s' name='StartupId' direction='in'/>
    </method>
  </interface>
</node>
"""


def _session_environment(bus):
    """The environment for a Finch started from here. A service started
    before the desktop has no DISPLAY of its own; the session hands its
    variables to the systemd user manager once it is up, so they are read
    from there, on every request (the next log-in may have other values)."""
    env = dict(os.environ)
    if "DISPLAY" in env or "WAYLAND_DISPLAY" in env:
        return env
    try:
        reply = bus.call_sync(
            "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
            "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", ("org.freedesktop.systemd1.Manager", "Environment")),
            GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        return env
    for entry in reply.unpack()[0]:
        key, sep, value = entry.partition("=")
        if sep:
            env[key] = value
    return env


def run(argv):
    finch = [sys.executable, os.path.abspath(argv[0])]
    loop = GLib.MainLoop()

    def on_method_call(bus, _sender, _path, _iface, method, params, invocation):
        uris, startup_id = params.unpack()
        if uris:
            # ShowFolders opens the folders; the other two show the items
            # selected in the folder they are in (there is no properties
            # dialog to show instead).
            args = finch + ([] if method == "ShowFolders" else ["--select"]) + uris
            env = _session_environment(bus)
            if startup_id:
                env["DESKTOP_STARTUP_ID"] = startup_id
            try:
                GLib.spawn_async(args, envp=[f"{k}={v}" for k, v in env.items()])
            except GLib.Error as err:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", err.message)
                return
        invocation.return_value(None)

    def on_bus_acquired(bus, _name):
        info = Gio.DBusNodeInfo.new_for_xml(INTERFACE).interfaces[0]
        bus.register_object(OBJECT_PATH, info, on_method_call, None, None)

    def on_name_acquired(_bus, name):
        print(f"finch: serving {name}", file=sys.stderr)

    def on_name_lost(bus, name):
        if bus is None:                 # no session bus (any more)
            loop.quit()
        else:
            print(f"finch: {name} belongs to another program, waiting for it",
                  file=sys.stderr)

    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                     on_bus_acquired, on_name_acquired, on_name_lost)
    loop.run()
    return 0
