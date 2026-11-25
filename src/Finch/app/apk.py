"""The launcher icon of an Android package (.apk) and what its manifest says
about it, read without any Android tool.

An APK is a zip. Its AndroidManifest.xml (compiled XML) names the icon as a
resource id; resources.arsc (the resource table) maps that id to one file
per screen density, or to another resource. The file is a bitmap (png /
webp), or compiled XML again: an adaptive icon (a foreground over a
background, each a drawable of its own), a vector drawable, a layer list...

icon(path, size) follows all that and returns a GdkPixbuf, or None when the
package has no icon this module can draw. Vectors are turned into SVG and
rendered by GdkPixbuf's SVG loader. info(path) is the application id and
the version, from the manifest's root element.

Only what is needed is read: the two tables are a few megabytes even in a
package of hundreds, and the resource table is indexed by type, not parsed.
"""
import math
import struct
import zipfile
from xml.sax.saxutils import quoteattr

import cairo
from gi.repository import Gdk, GdkPixbuf, GLib

CONTENT_TYPE = "application/vnd.android.package-archive"

# Chunk types (ResourceTypes.h)
_STRING_POOL, _TABLE_PACKAGE, _TABLE_TYPE = 0x0001, 0x0200, 0x0201
_XML_RESOURCE_MAP, _XML_START, _XML_END = 0x0180, 0x0102, 0x0103
# Value types
_REFERENCE, _STRING, _FLOAT, _DIMENSION, _DYNAMIC_REFERENCE = 0x01, 0x03, 0x04, 0x05, 0x07
_FIRST_INT, _LAST_INT, _FIRST_COLOR, _LAST_COLOR = 0x10, 0x1f, 0x1c, 0x1f
# Densities that are no pixel density
_ANY_DPI, _NO_DPI = 0xfffe, 0xffff
# android: attributes by resource id, for packages whose XML has the names
# stripped (the ids always stay). Those of the framework never change.
_ATTR_NAMES = {
    0x01010002: "icon", 0x0101052c: "roundIcon", 0x01010199: "drawable", 0x01010119: "src",
    0x01010402: "viewportWidth", 0x01010403: "viewportHeight", 0x01010404: "fillColor",
    0x01010405: "pathData", 0x01010406: "strokeColor", 0x01010407: "strokeWidth",
    0x0101040b: "strokeLineCap", 0x0101040c: "strokeLineJoin", 0x010104cb: "strokeAlpha",
    0x010104cc: "fillAlpha", 0x0101051e: "fillType", 0x0101031f: "alpha",
    0x01010326: "rotation", 0x010101b5: "pivotX", 0x010101b6: "pivotY",
    0x01010324: "scaleX", 0x01010325: "scaleY", 0x0101045a: "translateX",
    0x0101045b: "translateY", 0x010101a5: "color", 0x01010514: "offset",
    0x0101019d: "startColor", 0x0101019e: "endColor", 0x0101020b: "centerColor",
    0x010101a1: "type", 0x01010510: "startX", 0x01010511: "startY", 0x01010512: "endX",
    0x01010513: "endY", 0x010101a2: "centerX", 0x010101a3: "centerY",
    0x010101a4: "gradientRadius", 0x010101a0: "angle",
    0x010101ad: "left", 0x010101ae: "top", 0x010101af: "right", 0x010101b0: "bottom",
    0x010101b7: "insetLeft", 0x010101b8: "insetRight", 0x010101b9: "insetTop",
    0x0101021b: "versionCode", 0x0101021c: "versionName",
}
_BITMAPS = (".png", ".webp", ".jpg", ".jpeg", ".gif")
_MAX_DEPTH = 8                      # drawables nest; a cycle must not hang us
_MAX_TABLE = 64 * 1024 * 1024       # a resource table larger than this is not read
_MAX_FILE = 16 * 1024 * 1024        # nor a single drawable


class _Pool:
    """A string pool chunk, decoded one string at a time."""

    def __init__(self, data, start):
        self.data = data
        (_t, header, _size, self.count, _styles, flags, strings_start,
         _styles_start) = struct.unpack_from("<HHIIIIII", data, start)
        self.utf8 = bool(flags & 0x100)
        self.offsets = start + header
        self.base = start + strings_start

    def get(self, index):
        if not 0 <= index < self.count:
            return ""
        data = self.data
        pos = self.base + struct.unpack_from("<I", data, self.offsets + 4 * index)[0]
        try:
            if self.utf8:
                pos += 2 if data[pos] & 0x80 else 1         # length in characters
                n = data[pos]
                if n & 0x80:
                    n = ((n & 0x7f) << 8) | data[pos + 1]
                    pos += 1
                pos += 1
                return data[pos:pos + n].decode("utf-8", "replace")
            n = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            if n & 0x8000:
                n = ((n & 0x7fff) << 16) | struct.unpack_from("<H", data, pos)[0]
                pos += 2
            return data[pos:pos + 2 * n].decode("utf-16-le", "replace")
        except (IndexError, struct.error):
            return ""


class _Value:
    """A typed value of the XML or the table: (type, data), and the string
    pool its strings are in."""
    __slots__ = ("type", "data", "pool")

    def __init__(self, vtype, data, pool):
        self.type, self.data, self.pool = vtype, data, pool

    @property
    def is_reference(self):
        return self.type in (_REFERENCE, _DYNAMIC_REFERENCE) and self.data != 0

    @property
    def is_color(self):
        return _FIRST_COLOR <= self.type <= _LAST_COLOR

    def string(self):
        return self.pool.get(self.data) if self.type == _STRING else None

    def number(self, default=0.0):
        if self.type == _FLOAT:
            return struct.unpack("<f", struct.pack("<I", self.data))[0]
        if self.type == _DIMENSION:
            # mantissa in the top 24 bits, radix in bits 4-5; the unit is ignored
            mantissa = (self.data >> 8) - (1 << 24 if self.data & 0x80000000 else 0)
            return mantissa * (1.0, 1 / (1 << 7), 1 / (1 << 15), 1 / (1 << 23))[(self.data >> 4) & 3]
        if _FIRST_INT <= self.type <= _LAST_INT:
            return float(self.data - (1 << 32 if self.data & 0x80000000 else 0))
        if self.type == _STRING:
            try:
                return float(self.string().rstrip("dpsxtinm "))
            except ValueError:
                return default
        return default


class _Element:
    __slots__ = ("name", "attrs", "children")

    def __init__(self, name, attrs):
        self.name, self.attrs, self.children = name, attrs, []


def _parse_xml(data):
    """The root _Element of a compiled XML file. Attributes are keyed by
    their plain name (android: or not)."""
    pool, resmap, stack, root = None, (), [], None
    pos = struct.unpack_from("<H", data, 2)[0]
    while pos + 8 <= len(data):
        ctype, header, size = struct.unpack_from("<HHI", data, pos)
        if size < 8:
            break
        if ctype == _STRING_POOL and pool is None:
            pool = _Pool(data, pos)
        elif ctype == _XML_RESOURCE_MAP:
            resmap = struct.unpack_from("<%dI" % ((size - header) // 4), data, pos + header)
        elif ctype == _XML_START and pool is not None:
            ext = pos + header
            _ns, name, start, asize, count = struct.unpack_from("<IIHHH", data, ext)
            attrs = {}
            for k in range(count):
                at = ext + start + k * asize
                _ans, aname, _raw, _vsize, _res0, vtype, vdata = struct.unpack_from(
                    "<IIIHBBI", data, at)
                key = pool.get(aname)
                if not key and aname < len(resmap):
                    key = _ATTR_NAMES.get(resmap[aname], "")
                if key:
                    attrs[key] = _Value(vtype, vdata, pool)
            element = _Element(pool.get(name), attrs)
            if stack:
                stack[-1].children.append(element)
            else:
                root = root or element
            stack.append(element)
        elif ctype == _XML_END and stack:
            stack.pop()
        pos += size
    return root


class _Table:
    """resources.arsc: values of a resource id in every configuration."""

    def __init__(self, data):
        self.data = data
        self.values = None
        self.types = {}             # (package id, type id) -> [chunk offset]
        header = struct.unpack_from("<H", data, 2)[0]
        pos = header
        while pos + 8 <= len(data):
            ctype, hsize, size = struct.unpack_from("<HHI", data, pos)
            if size < 8:
                break
            if ctype == _STRING_POOL and self.values is None:
                self.values = _Pool(data, pos)
            elif ctype == _TABLE_PACKAGE:
                self._index_package(pos, hsize, size)
            pos += size

    def _index_package(self, start, hsize, size):
        data = self.data
        package = struct.unpack_from("<I", data, start + 8)[0]
        pos, end = start + hsize, min(start + size, len(data))
        while pos + 8 <= end:
            ctype, _hsize, csize = struct.unpack_from("<HHI", data, pos)
            if csize < 8:
                break
            if ctype == _TABLE_TYPE:
                self.types.setdefault((package, data[pos + 8]), []).append(pos)
            pos += csize

    def lookup(self, resid):
        """[(density, _Value)] of the resource, one per configuration, those
        for the newest Android first: the build tools write cut-down copies
        of a drawable for older versions (a vector without its gradient, a
        gradient without its coordinates), and the full one is the one to
        draw."""
        data, out = self.data, []
        entry = resid & 0xffff
        for pos in self.types.get((resid >> 24, (resid >> 16) & 0xff), ()):
            hsize = struct.unpack_from("<H", data, pos + 2)[0]
            flags = data[pos + 9]
            count, entries_start = struct.unpack_from("<II", data, pos + 12)
            density = struct.unpack_from("<H", data, pos + 20 + 14)[0]
            sdk = struct.unpack_from("<H", data, pos + 20 + 24)[0]
            table = pos + hsize
            offset = None
            if flags & 0x01:                        # sparse: (index, offset / 4) pairs
                for k in range(count):
                    index, off = struct.unpack_from("<HH", data, table + 4 * k)
                    if index == entry:
                        offset = off * 4
                        break
            elif entry < count:
                if flags & 0x02:                    # 16-bit offsets, in units of 4
                    off = struct.unpack_from("<H", data, table + 2 * entry)[0]
                    offset = None if off == 0xffff else off * 4
                else:
                    off = struct.unpack_from("<I", data, table + 4 * entry)[0]
                    offset = None if off == 0xffffffff else off
            if offset is None:
                continue
            at = pos + entries_start + offset
            esize, eflags = struct.unpack_from("<HH", data, at)
            if eflags & 0x0008:                     # compact entry: the value is inline
                out.append((sdk, density, _Value(eflags >> 8,
                                                 struct.unpack_from("<I", data, at + 4)[0],
                                                 self.values)))
            elif not eflags & 0x0001:               # not a bag (style, array...)
                _vsize, _res0, vtype, vdata = struct.unpack_from("<HBBI", data, at + esize)
                out.append((sdk, density, _Value(vtype, vdata, self.values)))
        out.sort(key=lambda found: -found[0])
        return [(density, value) for _sdk, density, value in out]

    def resolve(self, value, depth=0):
        """[(density, _Value)] that `value` stands for, references followed."""
        if not value.is_reference:
            return [(0, value)]
        if depth > _MAX_DEPTH:
            return []
        out = []
        for density, found in self.lookup(value.data):
            if found.is_reference:
                out.extend((density or d, v) for d, v in self.resolve(found, depth + 1))
            else:
                out.append((density, found))
        return out


def _css_color(argb, alpha=1.0):
    a = ((argb >> 24) & 0xff) / 255.0 * alpha
    return "#%06x" % (argb & 0xffffff), max(0.0, min(1.0, a))


class _Package:
    """An open APK: drawing its drawables."""

    def __init__(self, archive):
        self.zip = archive
        self.table = None
        try:
            info = archive.getinfo("resources.arsc")
            if info.file_size <= _MAX_TABLE:
                self.table = _Table(archive.read(info))
        except (KeyError, struct.error, IndexError):
            self.table = None
        self._gradients = 0

    def _read(self, name):
        try:
            info = self.zip.getinfo(name)
        except KeyError:
            return None
        return self.zip.read(info) if info.file_size <= _MAX_FILE else None

    def _choices(self, value):
        """What a drawable value can be: (bitmaps [(density, file)],
        xml files [(density, file)], colors [argb])."""
        bitmaps, xmls, colors = [], [], []
        found = self.table.resolve(value) if self.table is not None else [(0, value)]
        for density, v in found:
            if v.is_color:
                colors.append(v.data)
                continue
            name = v.string()
            if not name:
                continue
            lower = name.lower()
            if lower.endswith(".xml"):
                xmls.append((density, name))
            elif lower.endswith(_BITMAPS) and not lower.endswith(".9.png"):
                bitmaps.append((density, name))
        return bitmaps, xmls, colors

    # -- anything -> a square cairo surface -----------------------------------
    def draw(self, value, size, depth=0):
        """`value` (a reference to a drawable, a file name, a colour) drawn
        on a `size` square surface, or None."""
        if depth > _MAX_DEPTH or value is None:
            return None
        bitmaps, xmls, colors = self._choices(value)
        # An XML drawable for every density (adaptive icons, vectors) is the
        # modern one; bitmaps beside it are for old Android versions.
        for _density, name in sorted(xmls, key=lambda c: c[0] not in (_ANY_DPI, _NO_DPI)):
            surface = self._draw_xml(name, size, depth)
            if surface is not None:
                return surface
        # The densest bitmap, bar the pseudo densities.
        for _density, name in sorted(bitmaps, key=lambda c: (c[0] >= _ANY_DPI, -c[0])):
            surface = self._draw_bitmap(name, size)
            if surface is not None:
                return surface
        if colors:
            return self._draw_color(colors[0], size)
        return None

    @staticmethod
    def _surface(size):
        return cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)

    def _draw_color(self, argb, size):
        surface = self._surface(size)
        cr = cairo.Context(surface)
        cr.set_source_rgba(((argb >> 16) & 0xff) / 255, ((argb >> 8) & 0xff) / 255,
                           (argb & 0xff) / 255, ((argb >> 24) & 0xff) / 255)
        cr.paint()
        return surface

    def _pixbuf(self, data, size=None):
        loader = GdkPixbuf.PixbufLoader()
        if size:
            loader.set_size(size, size)
        try:
            loader.write(data)
            loader.close()
        except GLib.Error:
            try:
                loader.close()
            except GLib.Error:
                pass
            return None
        return loader.get_pixbuf()

    def _draw_pixbuf(self, pixbuf, size):
        """`pixbuf` stretched over the whole square."""
        surface = self._surface(size)
        cr = cairo.Context(surface)
        cr.scale(size / pixbuf.get_width(), size / pixbuf.get_height())
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BEST)
        cr.paint()
        return surface

    def _draw_bitmap(self, name, size):
        data = self._read(name)
        pixbuf = self._pixbuf(data) if data else None
        return self._draw_pixbuf(pixbuf, size) if pixbuf is not None else None

    def _draw_xml(self, name, size, depth):
        data = self._read(name)
        if not data:
            return None
        try:
            root = _parse_xml(data)
        except (struct.error, IndexError):
            return None
        return self._draw_element(root, size, depth) if root is not None else None

    def _draw_element(self, el, size, depth):
        kind = el.name
        if kind == "adaptive-icon":
            return self._draw_adaptive(el, size, depth)
        if kind == "vector":
            return self._draw_vector(el, size)
        if kind == "layer-list":
            return self._draw_layers(el, size, depth)
        if kind in ("bitmap", "nine-patch"):
            return self.draw(el.attrs.get("src"), size, depth + 1)
        if kind in ("inset", "rotate", "scale", "clip", "ripple", "animated-rotate"):
            return self._draw_child(el, size, depth)
        if kind == "color":
            color = self._color(el.attrs.get("color"))
            return self._draw_color(color, size) if color is not None else None
        if kind == "shape":
            return self._draw_shape(el, size)
        if kind in ("selector", "level-list", "animation-list", "transition"):
            # one of several states: the last is the default one
            for item in reversed(el.children):
                surface = self._draw_child(item, size, depth)
                if surface is not None:
                    return surface
        return None

    def _draw_child(self, el, size, depth):
        """The drawable of an <item>, <background>...: its android:drawable,
        or the drawable written inside it."""
        if "drawable" in el.attrs:
            return self.draw(el.attrs["drawable"], size, depth + 1)
        for child in el.children:
            surface = self._draw_element(child, size, depth + 1)
            if surface is not None:
                return surface
        return None

    def _draw_layers(self, el, size, depth):
        surface = self._surface(size)
        cr = cairo.Context(surface)
        drawn = False
        for item in el.children:
            layer = self._draw_child(item, size, depth)
            if layer is not None:
                cr.set_source_surface(layer, 0, 0)
                cr.paint()
                drawn = True
        return surface if drawn else None

    def _draw_adaptive(self, el, size, depth):
        """Background and foreground are 108 units wide of which the middle
        72 show, cut to the launcher's shape: here a rounded square."""
        full = int(math.ceil(size * 108 / 72))
        layers = []
        for part in ("background", "foreground"):
            child = next((c for c in el.children if c.name == part), None)
            layer = self._draw_child(child, full, depth) if child is not None else None
            if layer is not None:
                layers.append(layer)
        if not layers:
            return None
        surface = self._surface(size)
        cr = cairo.Context(surface)
        radius, w = size * 0.225, size
        cr.new_sub_path()
        cr.arc(w - radius, radius, radius, -math.pi / 2, 0)
        cr.arc(w - radius, w - radius, radius, 0, math.pi / 2)
        cr.arc(radius, w - radius, radius, math.pi / 2, math.pi)
        cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.clip()
        offset = -(full - size) / 2
        for layer in layers:
            cr.set_source_surface(layer, offset, offset)
            cr.paint()
        return surface

    # -- colours, shapes, vectors -------------------------------------------------
    def _color(self, value):
        """The ARGB colour `value` is or refers to, or None."""
        if value is None:
            return None
        if value.is_color:
            return value.data
        if value.type == _STRING:
            text = (value.string() or "").lstrip("#")
            try:
                number = int(text, 16)
            except ValueError:
                return None
            if len(text) == 3:
                number = int("".join(c * 2 for c in text), 16)
            return number | (0xff000000 if len(text) in (3, 6) else 0)
        if value.is_reference and self.table is not None:
            for _density, v in self.table.resolve(value):
                if v.is_color:
                    return v.data
                name = v.string() or ""
                if name.lower().endswith(".xml"):
                    # a colour state list: its last (default) item
                    data = self._read(name)
                    try:
                        root = _parse_xml(data) if data else None
                    except (struct.error, IndexError):
                        root = None
                    if root is not None and root.name == "selector":
                        for item in reversed(root.children):
                            color = self._color(item.attrs.get("color"))
                            if color is not None:
                                return color
        return None

    def _draw_shape(self, el, size):
        solid = next((c for c in el.children if c.name == "solid"), None)
        color = self._color(solid.attrs.get("color")) if solid is not None else None
        if color is None:
            gradient = next((c for c in el.children if c.name == "gradient"), None)
            if gradient is not None:
                color = (self._color(gradient.attrs.get("startColor"))
                         or self._color(gradient.attrs.get("endColor")))
        return self._draw_color(color, size) if color is not None else None

    def _gradient(self, value, defs):
        """An SVG paint server for the gradient XML `value` refers to:
        "url(#id)" with its definition appended to `defs`, or None."""
        if value is None or not value.is_reference or self.table is None:
            return None
        for _density, v in self.table.resolve(value):
            name = v.string() or ""
            if not name.lower().endswith(".xml"):
                continue
            data = self._read(name)
            try:
                root = _parse_xml(data) if data else None
            except (struct.error, IndexError):
                root = None
            if root is None or root.name != "gradient":
                continue
            a = root.attrs
            stops = []
            for item in root.children:
                color = self._color(item.attrs.get("color"))
                if color is not None:
                    offset = item.attrs["offset"].number() if "offset" in item.attrs else 0.0
                    stops.append((offset, color))
            if not stops:
                ends = [self._color(a.get(k)) for k in ("startColor", "centerColor", "endColor")]
                ends = [c for c in ends if c is not None]
                stops = [(i / max(1, len(ends) - 1), c) for i, c in enumerate(ends)]
            if not stops:
                continue
            self._gradients += 1
            gid = "g%d" % self._gradients

            def num(key, default=0.0):
                return a[key].number(default) if key in a else default
            kind = int(num("type"))
            if kind == 1:
                head = ('<radialGradient id="%s" gradientUnits="userSpaceOnUse" cx="%g" cy="%g" '
                        'r="%g">' % (gid, num("centerX"), num("centerY"), num("gradientRadius", 1)))
                tail = "</radialGradient>"
            elif kind == 2:                 # sweep: SVG has none, the first colour will do
                return _css_color(stops[0][1])[0]
            else:
                head = ('<linearGradient id="%s" gradientUnits="userSpaceOnUse" x1="%g" y1="%g" '
                        'x2="%g" y2="%g">' % (gid, num("startX"), num("startY"),
                                             num("endX"), num("endY")))
                tail = "</linearGradient>"
            body = "".join('<stop offset="%g" stop-color="%s" stop-opacity="%g"/>'
                           % ((offset,) + _css_color(color)) for offset, color in stops)
            defs.append(head + body + tail)
            return "url(#%s)" % gid
        return None

    def _paint(self, value, alpha, defs):
        """(svg paint, opacity) for a fillColor / strokeColor value."""
        color = self._color(value)
        if color is not None:
            return _css_color(color, alpha)
        paint = self._gradient(value, defs)
        return (paint, alpha) if paint else (None, alpha)

    def _svg_nodes(self, el, defs, out):
        clips = 0
        for child in el.children:
            a = child.attrs

            def num(key, default=0.0):
                return a[key].number(default) if key in a else default
            if child.name == "group":
                px, py = num("pivotX"), num("pivotY")
                out.append('<g transform="translate(%g %g) rotate(%g) scale(%g %g) '
                           'translate(%g %g)">' % (num("translateX") + px, num("translateY") + py,
                                                    num("rotation"), num("scaleX", 1),
                                                    num("scaleY", 1), -px, -py))
                self._svg_nodes(child, defs, out)
                out.append("</g>")
            elif child.name == "clip-path":
                data = a["pathData"].string() if "pathData" in a else None
                if data:
                    self._gradients += 1
                    cid = "c%d" % self._gradients
                    defs.append('<clipPath id="%s"><path d=%s/></clipPath>' % (cid, quoteattr(data)))
                    out.append('<g clip-path="url(#%s)">' % cid)    # for the siblings that follow
                    clips += 1
            elif child.name == "path":
                data = a["pathData"].string() if "pathData" in a else None
                if not data:
                    continue
                fill, fill_opacity = self._paint(a.get("fillColor"), num("fillAlpha", 1), defs)
                stroke, stroke_opacity = self._paint(a.get("strokeColor"),
                                                     num("strokeAlpha", 1), defs)
                attrs = ['d=%s' % quoteattr(data),
                         'fill="%s"' % (fill or "none"), 'fill-opacity="%g"' % fill_opacity]
                if int(num("fillType")) == 1:
                    attrs.append('fill-rule="evenodd"')
                if stroke and num("strokeWidth") > 0:
                    attrs += ['stroke="%s"' % stroke, 'stroke-opacity="%g"' % stroke_opacity,
                              'stroke-width="%g"' % num("strokeWidth"),
                              'stroke-linecap="%s"' % ("butt", "round", "square")[int(num("strokeLineCap")) % 3],
                              'stroke-linejoin="%s"' % ("miter", "round", "bevel")[int(num("strokeLineJoin")) % 3]]
                out.append("<path %s/>" % " ".join(attrs))
        out.extend("</g>" for _i in range(clips))

    def _draw_vector(self, el, size):
        a = el.attrs
        vw = a["viewportWidth"].number() if "viewportWidth" in a else 0
        vh = a["viewportHeight"].number() if "viewportHeight" in a else 0
        if vw <= 0 or vh <= 0:
            return None
        defs, body = [], []
        self._svg_nodes(el, defs, body)
        if not body:
            return None
        alpha = a["alpha"].number(1.0) if "alpha" in a else 1.0
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %g %g" '
               'opacity="%g"><defs>%s</defs>%s</svg>'
               % (size, size, vw, vh, alpha, "".join(defs), "".join(body)))
        pixbuf = self._pixbuf(svg.encode("utf-8"))
        return self._draw_pixbuf(pixbuf, size) if pixbuf is not None else None


def _guess(archive):
    """Launcher icon files by their usual names, for packages without a
    readable resource table: the densest of res/mipmap-*/ic_launcher.*."""
    ranks = {"xxxhdpi": 5, "xxhdpi": 4, "xhdpi": 3, "hdpi": 2, "mdpi": 1}
    best = None
    for name in archive.namelist():
        lower = name.lower()
        parts = lower.split("/")
        if (len(parts) == 3 and parts[0] == "res" and parts[1].startswith(("mipmap", "drawable"))
                and parts[2].rsplit(".", 1)[0] in ("ic_launcher", "icon", "app_icon")
                and lower.endswith(_BITMAPS)):
            rank = (parts[1].startswith("mipmap"),
                    max((r for key, r in ranks.items() if key in parts[1]), default=0))
            if best is None or rank > best[0]:
                best = (rank, name)
    return best[1] if best else None


def icon(path, size=256):
    """The launcher icon of the package at `path` as a GdkPixbuf at most
    `size` pixels square (bitmaps are not enlarged), or None."""
    try:
        with zipfile.ZipFile(path) as archive:
            package = _Package(archive)
            surface, native = None, None
            try:
                manifest = _parse_xml(archive.read("AndroidManifest.xml"))
            except (KeyError, struct.error, IndexError):
                manifest = None
            app = next((c for c in manifest.children if c.name == "application"),
                       None) if manifest is not None else None
            for key in ("icon", "roundIcon"):
                value = app.attrs.get(key) if app is not None else None
                if value is None:
                    continue
                # A plain bitmap icon keeps its own size if that is smaller.
                bitmaps, xmls, _colors = package._choices(value)
                if bitmaps and not xmls:
                    name = sorted(bitmaps, key=lambda c: (c[0] >= _ANY_DPI, -c[0]))[0][1]
                    data = package._read(name)
                    native = package._pixbuf(data) if data else None
                    if native is not None:
                        break
                surface = package.draw(value, size)
                if surface is not None:
                    break
            if native is None and surface is None:
                name = _guess(archive)
                data = package._read(name) if name else None
                native = package._pixbuf(data) if data else None
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, MemoryError,
            struct.error, IndexError, ValueError):
        return None
    if native is not None:
        w, h = native.get_width(), native.get_height()
        if max(w, h) > size:
            scale = size / max(w, h)
            native = native.scale_simple(max(1, round(w * scale)), max(1, round(h * scale)),
                                         GdkPixbuf.InterpType.BILINEAR)
        return native
    if surface is not None:
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
    return None


def info(path):
    """What the manifest of the package at `path` says about it: a dict with
    "package" (the application id), "version" (the version's name, a string)
    and "code" (its number), each only when the manifest has it. Empty for
    a file that cannot be read."""
    out = {}
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = _parse_xml(archive.read("AndroidManifest.xml"))
            if manifest is None or manifest.name != "manifest":
                return out
            package = manifest.attrs.get("package")
            if package is not None and package.string():
                out["package"] = package.string()
            code = manifest.attrs.get("versionCode")
            if code is not None and _FIRST_INT <= code.type <= _LAST_INT:
                out["code"] = code.data
            version = manifest.attrs.get("versionName")
            if version is not None and version.is_reference:
                # "@string/version": the resource table has the text
                table = _Package(archive).table
                found = table.resolve(version) if table is not None else []
                version = next((v for _density, v in found if v.type == _STRING), None)
            if version is not None:
                if version.type == _STRING:
                    text = version.string()
                elif version.type == _FLOAT or _FIRST_INT <= version.type <= _LAST_INT:
                    text = "%g" % version.number()      # written as a bare number
                else:
                    text = None
                if text:
                    out["version"] = text
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError, NotImplementedError,
            MemoryError, struct.error, IndexError, ValueError):
        return {}
    return out
