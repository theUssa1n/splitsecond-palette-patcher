#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VehicleColors palette limit (9 -> N) patcher for SplitSecond.exe.

Targets the Cracked Retail / DC builds of Split/Second. It verifies that every
patch site matches the expected stock bytes and aborts on any mismatch, so it
is safe to point at an unknown exe: it either reports "OK" for every site and
writes, or it leaves the file untouched.

Root cause (all inside SplitSecond.exe .text):

    struct Vehicle::cColorPalette {
        Entry entries[9];   // offset 0x000, sizeof(Entry) = 0x34 (52)
        int   Size;         // offset 0x1D4   (how many swatches the menu shows)
        char *Name;         // offset 0x1D8   ("Musclecar_09", ...)
    };                      // sizeof = 0x1DC (476)

    Three independent hard limits are compiled into the executable:
      1. the palette array only holds 9 entries and sizeof() is 476
      2. the tweak-registration loops are `cmp esi, 9`
      3. the "Size" tweak node is clamped to [1..9]

    Because entry[9] would land exactly on top of Size/Name, simply raising the
    loop bounds corrupts the struct. The patch therefore:
        - grows the array to N entries,
        - relocates Size/Name behind the array,
        - rewrites every access to them,
        - raises the loops and the Size clamp to N.

Usage:
    python ppl.py "D:/Game/SplitSecond.exe"                 # dry run (verify build + offsets)
    python ppl.py "D:/Game/SplitSecond.exe" -n 24           # dry run with N = 24
    python ppl.py "D:/Game/SplitSecond.exe" -n 24 --apply   # patch

N is the new palette capacity. The stock game uses 9. Use N > 9 to expand.
Range is validated to 1..255 (loop bounds are single bytes).
"""

import argparse
import hashlib
import os
import struct
import sys

ENTRY = 52   # sizeof(one palette entry) == 0x34


def u32(v):
    return struct.pack("<I", v)


# ---------------------------------------------------------------------------
# PE section map: virtual address -> raw file offset
# ---------------------------------------------------------------------------
class PE:
    def __init__(self, data):
        self.data = data
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise SystemExit("not a PE file")
        nsec = struct.unpack_from("<H", data, pe + 6)[0]
        opt = pe + 24
        self.imagebase = struct.unpack_from("<I", data, opt + 28)[0]
        self.sizeofimage = struct.unpack_from("<I", data, opt + 56)[0]
        sof = opt + struct.unpack_from("<H", data, pe + 20)[0]
        self.secs = []
        for i in range(nsec):
            o = sof + i * 40
            name = data[o:o + 8].rstrip(b"\0").decode("latin-1")
            vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", data, o + 8)
            self.secs.append((name, va, vsize, rawptr, rawsize))

    def va2off(self, va):
        rva = va - self.imagebase
        for name, sva, vsize, rawptr, rawsize in self.secs:
            if sva <= rva < sva + max(vsize, rawsize):
                delta = rva - sva
                if delta >= rawsize:
                    raise ValueError(f"{va:#x} lies in zero-filled tail of {name}")
                return rawptr + delta
        raise ValueError(f"{va:#x} outside all sections")

    def off2va(self, off):
        for name, sva, vsize, rawptr, rawsize in self.secs:
            if rawptr <= off < rawptr + rawsize:
                return self.imagebase + sva + (off - rawptr)
        raise ValueError(f"{off:#x} outside all sections")


def build_patches(n):
    new_size_off = n * ENTRY
    new_name_off = new_size_off + 4
    new_struct = new_name_off + 4
    return [
        # --- struct layout: Size / Name move behind the enlarged array ---
        (0x41E5D3, u32(0x1D4), u32(new_size_off), "palette ctor: default Size field"),
        (0x41E9F6, u32(0x1D4), u32(new_size_off), "palette ctor: Size tweak target"),
        (0x4729F5, u32(0x1D4), u32(new_size_off), "vehicle select: Size read"),

        (0x41E6D5, u32(0x1D8), u32(new_name_off), "reload: Name read (Size key)"),
        (0x41E706, u32(0x1D8), u32(new_name_off), "reload: Name read (Paint key)"),
        (0x41E733, u32(0x1D8), u32(new_name_off), "reload: Name read (Lacquer key)"),
        (0x41E760, u32(0x1D8), u32(new_name_off), "reload: Name read (Flake key)"),
        (0x41E78D, u32(0x1D8), u32(new_name_off), "reload: Name read (Swatch key)"),
        (0x41E996, u32(0x1D8), u32(new_name_off), "palette ctor: Name write"),

        # --- allocation size ---
        (0x41E8E1, u32(0x1DC), u32(new_struct), "palette alloc: sizeof(cColorPalette)"),

        # --- loop bounds ---
        (0x41E7C0, b"\x09", bytes([n]), "reload loop: 9 -> N"),
        (0x41EAE9, b"\x09", bytes([n]), "tweak registration loop: 9 -> N"),

        # --- Size tweak clamp: max 9 -> max N ---
        (0x41E9F1, b"\x09", bytes([n]), "Size tweak upper bound: 9 -> N"),
    ]


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(
        description="Patch the VehicleColors palette limit in SplitSecond.exe.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("exe", help="path to SplitSecond.exe (Cracked Retail / DC build)")
    ap.add_argument("-n", "--count", type=int, default=9,
                    help="new palette capacity, 1..255 (stock is 9)")
    ap.add_argument("--backup", help="backup path (default: <exe>.orig)")
    ap.add_argument("--apply", action="store_true",
                    help="write the result (default is a dry run)")
    args = ap.parse_args()

    if not 1 <= args.count <= 255:
        raise SystemExit("N must be between 1 and 255")

    exe = args.exe
    n = args.count
    backup = args.backup or (os.path.splitext(exe)[0] + ".orig")

    raw = open(exe, "rb").read()
    pe = PE(raw)
    data = bytearray(raw)

    new_size_off = n * ENTRY
    new_name_off = new_size_off + 4
    new_struct = new_name_off + 4

    print(f"target  : {exe}  ({len(raw)} bytes)")
    print(f"imagebase {pe.imagebase:#x}  sizeofimage {pe.sizeofimage:#x}")
    for name, sva, vsize, rawptr, rawsize in pe.secs:
        print(f"  {name:8} va={pe.imagebase + sva:#010x} vsize={vsize:#08x} "
              f"raw={rawptr:#08x} rawsize={rawsize:#08x}")
    print(f"\nN = {n} entries -> Size@{new_size_off:#x}  Name@{new_name_off:#x}  "
          f"sizeof={new_struct:#x}\n")

    errors = 0
    resolved = []
    for va, old, new, desc in build_patches(n):
        try:
            off = pe.va2off(va)
        except ValueError as e:
            print(f"BAD  {va:#08x}  {desc}: {e}")
            errors += 1
            continue
        cur = bytes(data[off:off + len(old)])
        ok = cur == old
        if not ok:
            errors += 1
        resolved.append((off, new, desc))
        print(f"{'OK ' if ok else 'BAD'}  va={va:#08x} off={off:#08x}  {desc}")
        if not ok:
            print(f"       have={cur.hex(' ')}  want={old.hex(' ')}")

    if errors:
        print(f"\n{errors} site(s) mismatch - exe is not in the expected state. Abort.")
        return 1

    print(f"\nAll {len(resolved)} sites verified.")

    if not args.apply:
        print("DRY RUN - nothing written. Re-run with --apply to patch.")
        return 0

    if not os.path.exists(backup):
        if os.path.dirname(backup):
            os.makedirs(os.path.dirname(backup), exist_ok=True)
        with open(backup, "wb") as f:
            f.write(raw)
        print(f"backup  -> {backup}")
    else:
        print(f"backup  -> already exists: {backup}")

    for off, new, desc in resolved:
        data[off:off + len(new)] = new

    with open(exe, "wb") as f:
        f.write(data)
    print(f"patched -> {exe}")
    print(f"sha256  : {sha(exe)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
