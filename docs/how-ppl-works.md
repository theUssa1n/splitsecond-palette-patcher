# `ppl.py` — how the executable patch works

## Root cause

All of the relevant code lives in `SplitSecond.exe` `.text`. The colour palette
of a vehicle is stored in a fixed-size struct:

```
struct Vehicle::cColorPalette {
    Entry entries[9];   // offset 0x000, sizeof(Entry) = 0x34 (52)
    int   Size;         // offset 0x1D4   (how many swatches the menu shows)
    char *Name;         // offset 0x1D8   ("Musclecar_09", ...)
};                      // sizeof = 0x1DC (476)
```

Three independent hard limits are compiled into the executable:

1. the palette array only holds **9** entries and `sizeof()` is 476;
2. the tweak-registration loops are `cmp esi, 9`;
3. the "Size" tweak node is clamped to `1..9`.

Because `entry[9]` would land exactly on top of `Size`/`Name`, simply raising the
loop bounds corrupts the struct. The patch therefore:

- grows the array to `N` entries,
- relocates `Size`/`Name` behind the array,
- rewrites every access to them,
- raises the loops and the `Size` clamp to `N`.

## The 13 patch sites

All sites are listed below with the **stock** bytes the tool expects to find.
If any byte differs, the tool reports `BAD` and aborts without writing.

| VA | Stock bytes | Meaning |
| --- | --- | --- |
| `0x41E5D3` | `D4 01 00 00` | palette ctor: default `Size` field |
| `0x41E9F6` | `D4 01 00 00` | palette ctor: `Size` tweak target |
| `0x4729F5` | `D4 01 00 00` | vehicle select: `Size` read |
| `0x41E6D5` | `D8 01 00 00` | reload: `Name` read (Size key) |
| `0x41E706` | `D8 01 00 00` | reload: `Name` read (Paint key) |
| `0x41E733` | `D8 01 00 00` | reload: `Name` read (Lacquer key) |
| `0x41E760` | `D8 01 00 00` | reload: `Name` read (Flake key) |
| `0x41E78D` | `D8 01 00 00` | reload: `Name` read (Swatch key) |
| `0x41E996` | `D8 01 00 00` | palette ctor: `Name` write |
| `0x41E8E1` | `DC 01 00 00` | palette alloc: `sizeof(cColorPalette)` |
| `0x41E7C0` | `09` | reload loop: `9 -> N` |
| `0x41EAE9` | `09` | tweak registration loop: `9 -> N` |
| `0x41E9F1` | `09` | `Size` tweak upper bound: `9 -> N` |

For a given `N` the new offsets are:

```
Size   = N * 52
Name   = Size + 4
sizeof = Name + 4
```

The loop/clamp sites are single-byte `09` values replaced with `N` (hence the
1–255 range).

## PE handling

The script parses the PE section table to translate these virtual addresses into
raw file offsets before reading/writing. It validates the file signature, image
base and section map first.

## Verification and backups

- Every site is checked against the expected stock bytes **before** anything is
  written. Any mismatch → abort (dry run or not).
- Default mode is a dry run (`DRY RUN - nothing written`).
- With `--apply`, a backup is written to `<exe>.orig` (or `--backup <path>`)
  before the first patch. If the backup already exists it is not overwritten.
- The final SHA-256 of the patched exe is printed.

## Compatibility notes

- Designed for **Cracked Retail / DC** builds.
- Running it on an already-patched exe reports `BAD` at every site — that is
  expected and confirms the file is not stock.
- Always keep the backup; this is a structural struct change, so be sure to
  test in-game before distributing the result.
- This `N` is a **capacity** ceiling (how many palettes can be stored without
  truncation). It is independent of how many swatches the menu shows, which is
  set separately by `gfx_swatches.py` (12–70 is the comfortable display range).
  Going to 255 here is safe even if the GFX shows far fewer slots.
