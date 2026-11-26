# -*- coding: utf-8 -*-
"""
Vehicle-select colour swatch slots (12 -> N) patcher for Fe_VehicleSelect.gfx.

How the screen works (decompiled ActionScript 2 of the root timeline):

    function AddColourSwatch(zCol) {              // called once per palette colour
        var swatch = eval("_root.blackholder.swatch" + numColourSwatches);
        swatch._visible = true;
        swatch.SetColour(zCol);
        numColourSwatches++;
    }
    function ClearColourSwatches() {              // hides every slot
        for (i = 0; i < 12; i++) eval("_root.blackholder.swatch" + i)._visible = false;
        numColourSwatches = 0;
    }

so the file has exactly two limits:
  1. the `blackholder` clip only contains the instances swatch0 .. swatch11;
     anything above that is silently ignored,
  2. ClearColourSwatches() hides slots with a hard-coded `12` loop bound.

What the patch does for a requested slot count N (1 .. N_max):
  - adds swatch12 .. swatch(N-1) as copies of the last slot,
  - lays the slots out as a grid: `--per-row` slots per row (default 14). The first
    12 slots of the first row keep their stock position, so a vehicle with 12 colours
    or fewer looks exactly like the original screen,
  - grows the black panel behind the swatches only as far as the grid needs:
      width  : only when the first row needs more than the stock 12 slots,
      height : only when the rows no longer fit into the stock panel,
  - changes the loop bound in ClearColourSwatches() from 12 to N,
  - refuses (or warns with --force) when the grid would run into the
    "SELECT VEHICLE" title area below the panel.

Every site is verified before anything is written. Default mode is a dry run.

Usage:
    python gfx_swatches.py "Deferred/UI/frontend/Flash/Fe_VehicleSelect.gfx" -n 24            # dry run
    python gfx_swatches.py "Deferred/UI/frontend/Flash/Fe_VehicleSelect.gfx" -n 48 --apply
"""

import argparse
import hashlib
import math
import os
import re
import struct
import sys
import zlib

STOCK_SLOTS = 12
SLOT_BOX = 35.8          # unscaled size of the swatch square (twips-free pixels)


# ---------------------------------------------------------------------------
# SWF / GFX container helpers
# ---------------------------------------------------------------------------
def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


class Tag:
    def __init__(self, code, start, hdr, length):
        self.code = code        # tag code
        self.start = start      # offset of the tag header
        self.hdr = hdr          # header size (2 or 6)
        self.length = length    # body length

    @property
    def body(self):
        return self.start + self.hdr

    @property
    def end(self):
        return self.body + self.length


def read_tags(buf, pos, end):
    out = []
    while pos < end:
        start = pos
        header = u16(buf, pos)
        pos += 2
        code, length = header >> 6, header & 0x3F
        hdr = 2
        if length == 0x3F:
            length = u32(buf, pos)
            pos += 4
            hdr = 6
        out.append(Tag(code, start, hdr, length))
        pos += length
        if code == 0:
            break
    return out


def tag_header(code, length):
    """Header bytes for a tag. Long form is used whenever the length needs it."""
    if length < 0x3F:
        return struct.pack("<H", (code << 6) | length)
    return struct.pack("<HI", (code << 6) | 0x3F, length)


def read_container(raw):
    sig = raw[:3]
    if sig not in (b"GFX", b"CFX", b"FWS", b"CWS"):
        raise SystemExit("not a GFX/SWF file")
    body = raw[8:]
    compressed = sig in (b"CFX", b"CWS")
    if compressed:
        body = zlib.decompress(body)
    declared = u32(raw, 4)
    if declared != len(body) + 8:
        raise SystemExit(f"declared length {declared} != actual {len(body) + 8}")
    return sig, raw[3], compressed, body


def write_container(sig, version, compressed, body):
    head = sig + bytes([version]) + struct.pack("<I", len(body) + 8)
    return head + (zlib.compress(body, 9) if compressed else body)


def rect_size(body):
    nbits = body[0] >> 3
    return (5 + 4 * nbits + 7) // 8


# ---------------------------------------------------------------------------
# bit reader / writer, MATRIX and RECT
# ---------------------------------------------------------------------------
class BitReader:
    def __init__(self, b, byte_pos):
        self.b, self.pos = b, byte_pos * 8

    def u(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.b[self.pos >> 3] >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def s(self, n):
        v = self.u(n)
        return v - (1 << n) if n and v >> (n - 1) else v

    def end_byte(self):
        return (self.pos + 7) >> 3


class BitWriter:
    def __init__(self):
        self.bits = []

    def u(self, value, n):
        for i in range(n - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def s(self, value, n):
        self.u(value & ((1 << n) - 1), n)

    def to_bytes(self):
        while len(self.bits) % 8:
            self.bits.append(0)
        out = bytearray()
        for i in range(0, len(self.bits), 8):
            v = 0
            for bit in self.bits[i:i + 8]:
                v = (v << 1) | bit
            out.append(v)
        return bytes(out)


def sbits(*values):
    """Smallest signed field width that holds all values (at least 1 bit)."""
    n = 1
    for v in values:
        while not (-(1 << (n - 1)) <= v < (1 << (n - 1))):
            n += 1
    return n


def twips(px):
    return int(round(px * 20))


class Matrix:
    """MATRIX record with raw field values (twips for translation)."""

    def __init__(self, scale=None, rotate=None, tx=0, ty=0):
        self.scale, self.rotate, self.tx, self.ty = scale, rotate, tx, ty

    @staticmethod
    def parse(b, off):
        r = BitReader(b, off)
        scale = rotate = None
        if r.u(1):
            n = r.u(5)
            scale = (r.s(n), r.s(n))
        if r.u(1):
            n = r.u(5)
            rotate = (r.s(n), r.s(n))
        n = r.u(5)
        tx, ty = r.s(n), r.s(n)
        return Matrix(scale, rotate, tx, ty), r.end_byte()

    def encode(self):
        w = BitWriter()
        if self.scale is None:
            w.u(0, 1)
        else:
            n = sbits(*self.scale)
            w.u(1, 1)
            w.u(n, 5)
            w.s(self.scale[0], n)
            w.s(self.scale[1], n)
        if self.rotate is None:
            w.u(0, 1)
        else:
            n = sbits(*self.rotate)
            w.u(1, 1)
            w.u(n, 5)
            w.s(self.rotate[0], n)
            w.s(self.rotate[1], n)
        n = sbits(self.tx, self.ty)
        w.u(n, 5)
        w.s(self.tx, n)
        w.s(self.ty, n)
        return w.to_bytes()


def encode_rect(xmin, xmax, ymin, ymax):
    n = sbits(xmin, xmax, ymin, ymax)
    w = BitWriter()
    w.u(n, 5)
    for v in (xmin, xmax, ymin, ymax):
        w.s(v, n)
    return w.to_bytes()


# ---------------------------------------------------------------------------
# PlaceObject2
# ---------------------------------------------------------------------------
class Place2:
    """Only the PlaceObject2 shapes this patch needs: character + matrix + name."""

    def __init__(self, body, off, length):
        self.flags = body[off]
        self.depth = u16(body, off + 1)
        i = off + 3
        self.char = None
        self.matrix = None
        self.name = None
        if self.flags & 0x02:
            self.char = u16(body, i)
            i += 2
        if self.flags & 0x04:
            self.matrix, i = Matrix.parse(body, i)
        self.unsupported = bool(self.flags & ~0x26 & 0xFF)
        if self.flags & 0x20:
            e = body.index(b"\0", i)
            self.name = body[i:e].decode("latin-1")
            i = e + 1
        self.consumed = i - off
        self.length = length

    def build(self, depth, name, matrix):
        out = bytearray([0x26])
        out += struct.pack("<HH", depth, self.char)
        out += matrix.encode()
        out += name.encode("latin-1") + b"\0"
        return bytes(out)


# ---------------------------------------------------------------------------
# DefineShape (solid fill, straight/curved edges): decode -> transform -> encode
# ---------------------------------------------------------------------------
class Shape:
    SOLID_TAGS = (2, 22, 32)   # DefineShape, DefineShape2, DefineShape3

    def __init__(self, tag_code, shape_id, bounds, prefix, fill_bits, line_bits, records):
        self.tag_code = tag_code
        self.shape_id = shape_id
        self.bounds = bounds        # xmin, xmax, ymin, ymax in twips
        self.prefix = prefix        # raw fill/line style arrays + bit counts
        self.fill_bits = fill_bits
        self.line_bits = line_bits
        self.records = records      # absolute coordinates, see decode()

    @staticmethod
    def parse(body, tag):
        if tag.code not in Shape.SOLID_TAGS:
            raise ValueError(f"unsupported shape tag {tag.code}")
        b = body[tag.body:tag.end]
        shape_id = u16(b, 0)
        r = BitReader(b, 2)
        n = r.u(5)
        bounds = tuple(r.s(n) for _ in range(4))
        i = r.end_byte()
        start_styles = i
        rgba = 4 if tag.code == 32 else 3
        count = b[i]
        i += 1
        if count == 0xFF:
            raise ValueError("extended fill style count")
        for _ in range(count):
            if b[i] != 0:
                raise ValueError("only solid fills are supported")
            i += 1 + rgba
        lines = b[i]
        i += 1
        if lines == 0xFF:
            raise ValueError("extended line style count")
        i += lines * (2 + rgba)
        bits = b[i]
        i += 1
        prefix = b[start_styles:i]
        fb, lb = bits >> 4, bits & 15
        r = BitReader(b, i)
        x = y = 0
        records = []
        while True:
            if r.u(1):
                if r.u(1):          # straight edge
                    nb = r.u(4) + 2
                    if r.u(1):
                        dx, dy = r.s(nb), r.s(nb)
                    elif r.u(1):
                        dx, dy = 0, r.s(nb)
                    else:
                        dx, dy = r.s(nb), 0
                    x += dx
                    y += dy
                    records.append(("line", x, y))
                else:               # curved edge
                    nb = r.u(4) + 2
                    cx, cy, ax, ay = r.s(nb), r.s(nb), r.s(nb), r.s(nb)
                    c = (x + cx, y + cy)
                    x, y = c[0] + ax, c[1] + ay
                    records.append(("curve", c[0], c[1], x, y))
            else:
                flags = r.u(5)
                if flags == 0:
                    break
                if flags & 16:
                    raise ValueError("new styles inside the shape are not supported")
                move = fill0 = fill1 = line = None
                if flags & 1:
                    n = r.u(5)
                    x, y = r.s(n), r.s(n)
                    move = (x, y)
                if flags & 2:
                    fill0 = r.u(fb)
                if flags & 4:
                    fill1 = r.u(fb)
                if flags & 8:
                    line = r.u(lb)
                records.append(("style", move, fill0, fill1, line))
        if r.end_byte() != len(b):
            raise ValueError("trailing bytes after the shape records")
        return Shape(tag.code, shape_id, bounds, prefix, fb, lb, records)

    def points(self):
        pts = []
        for rec in self.records:
            if rec[0] == "style" and rec[1]:
                pts.append(rec[1])
            elif rec[0] == "line":
                pts.append((rec[1], rec[2]))
            elif rec[0] == "curve":
                pts.append((rec[1], rec[2]))
                pts.append((rec[3], rec[4]))
        return pts

    def mapped(self, fn):
        recs = []
        for rec in self.records:
            if rec[0] == "style":
                recs.append(("style", fn(*rec[1]) if rec[1] else None) + rec[2:])
            elif rec[0] == "line":
                recs.append(("line",) + fn(rec[1], rec[2]))
            else:
                recs.append(("curve",) + fn(rec[1], rec[2]) + fn(rec[3], rec[4]))
        return Shape(self.tag_code, self.shape_id, None, self.prefix, self.fill_bits, self.line_bits, recs)

    def encode(self):
        pts = self.points()
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        bounds = (min(xs), max(xs), min(ys), max(ys))
        out = bytearray(struct.pack("<H", self.shape_id))
        out += encode_rect(*bounds)
        out += self.prefix
        w = BitWriter()
        cx = cy = 0
        for rec in self.records:
            if rec[0] == "style":
                _, move, fill0, fill1, line = rec
                flags = (1 if move else 0) | (2 if fill0 is not None else 0) \
                    | (4 if fill1 is not None else 0) | (8 if line is not None else 0)
                w.u(0, 1)
                w.u(flags, 5)
                if move:
                    n = sbits(*move)
                    w.u(n, 5)
                    w.s(move[0], n)
                    w.s(move[1], n)
                    cx, cy = move
                if fill0 is not None:
                    w.u(fill0, self.fill_bits)
                if fill1 is not None:
                    w.u(fill1, self.fill_bits)
                if line is not None:
                    w.u(line, self.line_bits)
            elif rec[0] == "line":
                dx, dy = rec[1] - cx, rec[2] - cy
                nb = max(2, sbits(dx, dy))
                w.u(1, 1)
                w.u(1, 1)
                w.u(nb - 2, 4)
                if dx and dy:
                    w.u(1, 1)
                    w.s(dx, nb)
                    w.s(dy, nb)
                elif dx == 0:
                    w.u(0, 1)
                    w.u(1, 1)
                    w.s(dy, nb)
                else:
                    w.u(0, 1)
                    w.u(0, 1)
                    w.s(dx, nb)
                cx, cy = rec[1], rec[2]
            else:
                ccx, ccy, ax, ay = rec[1] - cx, rec[2] - cy, rec[3] - rec[1], rec[4] - rec[2]
                nb = max(2, sbits(ccx, ccy, ax, ay))
                w.u(1, 1)
                w.u(0, 1)
                w.u(nb - 2, 4)
                for v in (ccx, ccy, ax, ay):
                    w.s(v, nb)
                cx, cy = rec[3], rec[4]
        w.u(0, 1)
        w.u(0, 5)
        out += w.to_bytes()
        return tag_header(self.tag_code, len(out)) + bytes(out)


# ---------------------------------------------------------------------------
def sha(data):
    return hashlib.sha256(data).hexdigest()


def exports_of(body, top):
    export = {}
    for t in top:
        if t.code == 56:
            n = u16(body, t.body)
            i = t.body + 2
            for _ in range(n):
                cid = u16(body, i)
                e = body.index(b"\0", i + 2)
                export[body[i + 2:e].decode("latin-1")] = cid
                i = e + 1
    return export


def find_swatch_sprite(body, top):
    """Returns (sprite_tag, children, swatch placements, mcColourSwatch id)."""
    export = exports_of(body, top)
    swatch_char = export.get("mcColourSwatch")
    if swatch_char is None:
        raise SystemExit("export 'mcColourSwatch' not found - not the expected file")

    hits = []
    for t in top:
        if t.code != 39:
            continue
        kids = read_tags(body, t.body + 4, t.end)
        places = []
        for c in kids:
            if c.code == 26:
                p = Place2(body, c.body, c.length)
                if p.name and re.fullmatch(r"swatch\d+", p.name):
                    places.append((c, p))
        if places:
            hits.append((t, kids, places))
    if len(hits) != 1:
        raise SystemExit(f"expected one clip holding the swatches, found {len(hits)}")
    return hits[0] + (swatch_char,)


def find_panel_shape(body, top, kids, swatch_char):
    """The big solid shape behind the swatches (inside a sprite placed in the same clip)."""
    by_id = {}
    for t in top:
        if t.code in (2, 22, 32, 83, 39):
            by_id[u16(body, t.body)] = t
    found = []
    for c in kids:
        if c.code != 26:
            continue
        p = Place2(body, c.body, c.length)
        t = by_id.get(p.char) if p.char is not None and p.char != swatch_char else None
        if t is None or t.code != 39:
            continue
        for cc in read_tags(body, t.body + 4, t.end):
            if cc.code != 26:
                continue
            q = Place2(body, cc.body, cc.length)
            s = by_id.get(q.char)
            if s is not None and s.code in Shape.SOLID_TAGS:
                found.append((s, q))
    if len(found) != 1:
        raise SystemExit(f"expected exactly one panel shape, found {len(found)}")
    return found[0]


def find_clear_bound(body, top, value):
    """Offset of the loop bound in ClearColourSwatches(): GetVariable, Push int <value>, Less2."""
    pattern = b"\x1c\x96\x05\x00\x07" + struct.pack("<I", value) + b"\x48"
    sites = []
    for t in top:
        if t.code == 12:
            blob = body[t.body:t.end]
            start = 0
            while True:
                k = blob.find(pattern, start)
                if k < 0:
                    break
                marker = b"\x9b\x18\x00ClearColourSwatches\x00"   # DefineFunction + name
                d = blob.rfind(marker, 0, k)
                if d >= 0 and k - d < 120:   # the site belongs to that function's body
                    sites.append(t.body + k + 5)  # the 4-byte integer (after 1c 96 05 00 07)
                start = k + 1
    if len(sites) != 1:
        raise SystemExit(f"loop bound of ClearColourSwatches not found uniquely ({len(sites)} candidates)")
    return sites[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gfx", help="path to Fe_VehicleSelect.gfx")
    ap.add_argument("-n", "--count", type=int, default=24, help="number of colour slots wanted (default 24)")
    ap.add_argument("--per-row", type=int, default=14, help="slots per row (default 14, minimum 12)")
    ap.add_argument("--pitch", type=float, help="distance between slots in px (default: measured from the stock row)")
    ap.add_argument("--bottom-pad", type=float, default=9.0, help="space kept below the last row (default 9 px)")
    ap.add_argument("--right-pad", type=float, default=16.0, help="space kept right of the widest row (default 16 px)")
    ap.add_argument("--max-bottom", type=float, default=276.0,
                    help="lowest allowed panel edge, measured from the panel top (default 276 px; below it "
                         "the grid would run into the SELECT VEHICLE title)")
    ap.add_argument("--force", action="store_true", help="allow a panel that passes --max-bottom")
    ap.add_argument("--out", help="write the patched file here instead of patching in place")
    ap.add_argument("--apply", action="store_true", help="write the result (default is a dry run)")
    args = ap.parse_args()

    n, per_row = args.count, args.per_row
    if n < 1:
        raise SystemExit("count must be at least 1")
    if per_row < STOCK_SLOTS:
        raise SystemExit(f"--per-row must be at least {STOCK_SLOTS}")

    raw = open(args.gfx, "rb").read()
    sig, version, compressed, body = read_container(raw)
    top = read_tags(body, rect_size(body) + 4, len(body))
    if top[-1].code != 0:
        raise SystemExit("top level tag list does not end with End")

    sprite, kids, places, swatch_char = find_swatch_sprite(body, top)
    names = [p.name for _, p in places]
    expected = [f"swatch{i}" for i in range(STOCK_SLOTS)]
    print(f"file     : {args.gfx}  ({len(raw)} bytes, signature {sig.decode()}, version {version})")
    print(f"clip     : DefineSprite id {u16(body, sprite.body)} holds {len(places)} swatch instances")
    if names != expected:
        raise SystemExit(f"unexpected swatch names: {names}")
    if any(p.char != swatch_char or p.unsupported or p.matrix is None for _, p in places):
        raise SystemExit("swatch placements are not plain (character, matrix, name) tags")
    print(f"slots    : swatch0..swatch{STOCK_SLOTS - 1} -> character {swatch_char} (mcColourSwatch)")

    bound_off = find_clear_bound(body, top, STOCK_SLOTS)
    print(f"loop     : ClearColourSwatches bound {u32(body, bound_off)} at body offset {bound_off:#x}")

    panel_tag, panel_place = find_panel_shape(body, top, kids, swatch_char)
    panel = Shape.parse(body, panel_tag)
    # the encoder must reproduce the untouched shape exactly (guards the bit writer)
    enc = panel.mapped(lambda x, y: (x, y)).encode()
    enc_tag = read_tags(enc, 0, len(enc))[0]
    if Shape.parse(enc, enc_tag).records != panel.records:
        raise SystemExit("internal error: shape encoder does not round-trip")
    xmin, xmax, ymin, ymax = (v / 20 for v in panel.bounds)
    pts = panel.points()
    chamfer = sorted({(x, y) for x, y in pts if x == panel.bounds[1] and y != panel.bounds[2]} |
                     {(x, y) for x, y in pts if y == panel.bounds[3] and x != panel.bounds[0]})
    if len(chamfer) != 2:
        raise SystemExit("panel outline is not the expected rectangle with one chamfered corner")
    (cx_x, cx_y), (cb_x, cb_y) = chamfer[0], chamfer[1]   # sorted by x: bottom point first
    if cx_y == panel.bounds[3]:
        (cx_x, cx_y), (cb_x, cb_y) = (cb_x, cb_y), (cx_x, cx_y)
    chamfer_w = (panel.bounds[1] - cb_x) / 20       # horizontal size of the cut
    chamfer_h = (panel.bounds[3] - cx_y) / 20       # vertical size of the cut
    print(f"panel    : DefineShape id {panel.shape_id} x[{xmin:.1f},{xmax:.1f}] y[{ymin:.1f},{ymax:.1f}], "
          f"chamfer {chamfer_w:.1f} x {chamfer_h:.1f}")

    # ---- measure the stock row ------------------------------------------------
    first, last = places[0][1].matrix, places[-1][1].matrix
    x0, y0 = first.tx / 20, first.ty / 20
    pitch = args.pitch or ((last.tx - first.tx) / 20 / (STOCK_SLOTS - 1))
    scale_x = (last.scale[0] / 65536) if last.scale else 1.0
    scale_y = (last.scale[1] / 65536) if last.scale else 1.0
    slot_w, slot_h = SLOT_BOX * scale_x, SLOT_BOX * scale_y
    print(f"stock row: first slot at ({x0:.2f}, {y0:.2f}), pitch {pitch:.2f}px, slot {slot_w:.1f} x {slot_h:.1f}px")

    # ---- the limit -------------------------------------------------------------
    rows_max = int(math.floor((args.max_bottom - args.bottom_pad - slot_h - y0) / pitch)) + 1
    n_max = rows_max * per_row
    print(f"limit    : {rows_max} rows x {per_row} = {n_max} slots fit above y = {args.max_bottom:g}")

    if n <= STOCK_SLOTS:
        print(f"\nN = {n}: the stock screen already has {STOCK_SLOTS} slots - nothing to patch.")
        return 0

    rows = int(math.ceil(n / per_row))
    # ---- positions ---------------------------------------------------------
    def slot_xy(k):
        row, col = divmod(k, per_row)
        if col < STOCK_SLOTS:
            x = places[col][1].matrix.tx / 20
        else:
            x = last.tx / 20 + (col - (STOCK_SLOTS - 1)) * pitch
        return x, y0 + row * pitch

    slots = [slot_xy(k) for k in range(n)]
    bottom_needed = slots[-1][1] + slot_h + args.bottom_pad
    ymax_new = max(ymax, bottom_needed)
    dy = twips(ymax_new) - panel.bounds[3]
    need_x = 0.0
    for x, y in slots:
        inset = max(0.0, (y + slot_h) - (ymax_new - chamfer_h)) * (chamfer_w / chamfer_h)
        need_x = max(need_x, x + slot_w + inset + args.right_pad)
    xmax_new = max(xmax, need_x)
    dx = twips(xmax_new) - panel.bounds[1]
    print(f"\nlayout   : N = {n}, {rows} row(s), {min(n, per_row)} slot(s) in the widest row")
    print(f"panel    : width {xmax - xmin:.1f} -> {(panel.bounds[1] + dx) / 20 - xmin:.1f} px (+{dx / 20:.1f}), "
          f"height {ymax - ymin:.1f} -> {(panel.bounds[3] + dy) / 20 - ymin:.1f} px (+{dy / 20:.1f})")
    over = (panel.bounds[3] + dy) / 20 > args.max_bottom
    if over:
        msg = (f"the panel would end at y = {(panel.bounds[3] + dy) / 20:.1f} (limit {args.max_bottom:g}); "
               f"with {per_row} slots per row at most {n_max} slots fit")
        if not args.force:
            raise SystemExit("ERROR: " + msg + ". Use --force to patch anyway.")
        print("WARNING: " + msg)

    # ---- new placements ------------------------------------------------------
    template = places[-1][1]
    next_depth = max(u16(body, c.body + 1) for c in kids if c.code == 26) + 1
    new_tags = bytearray()
    for k in range(STOCK_SLOTS, n):
        x, y = slots[k]
        m = Matrix(template.matrix.scale, template.matrix.rotate, twips(x), twips(y))
        tag_body = template.build(next_depth, f"swatch{k}", m)
        new_tags += tag_header(26, len(tag_body)) + tag_body
        next_depth += 1
    print(f"slots    : adding swatch{STOCK_SLOTS}..swatch{n - 1}  "
          f"(first new at x={slots[STOCK_SLOTS][0]:.1f} y={slots[STOCK_SLOTS][1]:.1f}, "
          f"last at x={slots[-1][0]:.1f} y={slots[-1][1]:.1f})")

    # ---- assemble the replacements (applied from the end of the file backwards) --
    show = [c for c in kids if c.code == 1]
    if len(show) != 1:
        raise SystemExit("clip does not have exactly one frame")
    new_sprite_len = sprite.length + len(new_tags)
    if sprite.hdr != len(tag_header(39, new_sprite_len)):
        raise SystemExit("sprite header size changed - handled only for long headers")

    split_x = (panel.bounds[0] + panel.bounds[1]) // 2
    split_y = (panel.bounds[2] + panel.bounds[3]) // 2
    new_panel = panel.mapped(lambda x, y: (x + (dx if x > split_x else 0), y + (dy if y > split_y else 0)))
    edits = []  # (start, end, bytes)
    edits.append((bound_off, bound_off + 4, struct.pack("<I", n)))
    if dx or dy:
        edits.append((panel_tag.start, panel_tag.end, new_panel.encode()))
    edits.append((sprite.start, sprite.start + sprite.hdr, tag_header(39, new_sprite_len)))
    edits.append((show[0].start, show[0].start, bytes(new_tags)))
    new_body = bytearray(body)
    for start, end, data in sorted(edits, key=lambda e: -e[0]):
        new_body[start:end] = data

    # ---- verify the result ----------------------------------------------------
    nb = bytes(new_body)
    top2 = read_tags(nb, rect_size(nb) + 4, len(nb))
    assert len(top2) == len(top), "top level tag count changed"
    sprite2, kids2, places2, _ = find_swatch_sprite(nb, top2)
    got = [(p.name, p.depth) for _, p in places2]
    assert [g[0] for g in got] == [f"swatch{i}" for i in range(n)], got
    assert len({g[1] for g in got}) == n, "duplicate depths"
    for k, (_, p) in enumerate(places2):
        x, y = slots[k]
        assert (p.matrix.tx, p.matrix.ty) == (twips(x), twips(y)) or k < STOCK_SLOTS, k
    assert u32(nb, find_clear_bound(nb, top2, n)) == n
    panel2_tag, _ = find_panel_shape(nb, top2, kids2, swatch_char)
    panel2 = Shape.parse(nb, panel2_tag)
    pts2 = panel2.points()
    assert panel2.bounds == (panel.bounds[0], panel.bounds[1] + dx, panel.bounds[2], panel.bounds[3] + dy), panel2.bounds
    assert len(pts2) == len(pts), "panel vertex count changed"
    # every swatch must lie inside the panel
    for x, y in slots:
        assert xmin <= x and x + slot_w <= panel2.bounds[1] / 20 and y + slot_h <= panel2.bounds[3] / 20
    print("verified : tag count unchanged, unique depths, loop bound patched, panel outline intact, "
          "all slots inside the panel")

    result = write_container(sig, version, compressed, nb)
    print(f"result   : {len(result)} bytes ({len(result) - len(raw):+d}), sha256 {sha(result)[:16]}")

    if not args.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply to patch.")
        return 0

    target = args.out or args.gfx
    if not args.out:
        backup = args.gfx + ".orig"
        if not os.path.exists(backup):
            with open(backup, "wb") as f:
                f.write(raw)
            print(f"backup   -> {backup}")
        else:
            print(f"backup   -> already exists: {backup}")
    with open(target, "wb") as f:
        f.write(result)
    print(f"patched  -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
