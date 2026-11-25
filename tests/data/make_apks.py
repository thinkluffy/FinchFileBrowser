#!/usr/bin/env python3
"""Rebuild the tiny Android packages test_apk.py reads (tests/data/*.apk).

They are checked in; this is only needed to change them. Uses aapt2 and
android.jar from the Android SDK ($ANDROID_HOME, else ~/Android/Sdk), so the
files are what the real tool chain writes, not what Finch's reader expects.
"""
import glob, os, shutil, struct, subprocess, sys, tempfile, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SDK = os.environ.get("ANDROID_HOME") or os.path.expanduser("~/Android/Sdk")
AAPT2 = sorted(glob.glob(SDK + "/build-tools/*/aapt2"))[-1]
JAR = sorted(glob.glob(SDK + "/platforms/android-*/android.jar"))[-1]


def png(path, size, rgb):
    """A plain `size` square of one colour."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    row = b"\0" + bytes(rgb) * size
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))


def text(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def manifest(icon, version):
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="finch.test"%s>\n'
            '    <application android:label="Finch test"%s/>\n</manifest>\n'
            % (version, ' android:icon="%s"' % icon if icon else ""))


RED, BLUE = (255, 0, 0), (0, 0, 255)
VECTOR = '''<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android" xmlns:aapt="http://schemas.android.com/aapt"
    android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24">
    <path android:pathData="M0,0h24v24h-24z">
        <aapt:attr name="android:fillColor">
            <gradient android:type="linear" android:startX="0" android:startY="0" android:endX="24" android:endY="0">
                <item android:offset="0" android:color="#FFFF0000"/>
                <item android:offset="1" android:color="#FF0000FF"/>
            </gradient>
        </aapt:attr>
    </path>
    <group android:translateX="8" android:translateY="8">
        <path android:pathData="M0,0h8v8h-8z" android:fillColor="#FFFFFFFF"/>
    </group>
</vector>
'''
ADAPTIVE = '''<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/launcher_background"/>
    <foreground android:drawable="@drawable/launcher_foreground"/>
</adaptive-icon>
'''
FOREGROUND = '''<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">
    <path android:pathData="M42,42h24v24h-24z" android:fillColor="#FFFF0000"/>
</vector>
'''
COLORS = '''<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="launcher_background">#FF00FF00</color>
    <string name="version">2.0-beta</string>
</resources>
'''


def build(name, icon, fill, version=""):
    work = tempfile.mkdtemp(prefix="finch-apk-")
    try:
        res = os.path.join(work, "res")
        fill(res)
        text(os.path.join(work, "AndroidManifest.xml"), manifest(icon, version))
        args = [AAPT2, "link", "-o", os.path.join(HERE, name), "-I", JAR,
                "--manifest", os.path.join(work, "AndroidManifest.xml")]
        if os.path.isdir(res):
            compiled = os.path.join(work, "compiled.zip")
            subprocess.run([AAPT2, "compile", "--dir", res, "-o", compiled], check=True)
            args.append(compiled)
        subprocess.run(args, check=True)
        print(name, os.path.getsize(os.path.join(HERE, name)), "bytes")
    finally:
        shutil.rmtree(work)


def bitmaps(res):               # the densest one is the icon
    png(res + "/mipmap-mdpi/ic_launcher.png", 48, BLUE)
    png(res + "/mipmap-xxhdpi/ic_launcher.png", 144, RED)


def adaptive(res):              # green behind a red square, and a bitmap for old Androids
    text(res + "/mipmap-anydpi-v26/ic_launcher.xml", ADAPTIVE)
    png(res + "/mipmap-mdpi/ic_launcher.png", 48, BLUE)
    text(res + "/drawable/launcher_foreground.xml", FOREGROUND)
    text(res + "/values/colors.xml", COLORS)


def vector(res):                # red-to-blue gradient with a white square in the middle
    text(res + "/drawable/ic_launcher.xml", VECTOR)


if __name__ == "__main__":
    # Versions: name and number, a name kept in the resources, a number
    # only, none.
    build("bitmap.apk", "@mipmap/ic_launcher", bitmaps,
          ' android:versionCode="7" android:versionName="1.2.3"')
    build("adaptive.apk", "@mipmap/ic_launcher", adaptive,
          ' android:versionCode="42" android:versionName="@string/version"')
    build("vector.apk", "@drawable/ic_launcher", vector)
    build("noicon.apk", None, lambda res: None, ' android:versionCode="5"')
