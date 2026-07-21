#!/usr/bin/env python3
"""
Swap specific Japanese UTF-8 UI labels at MakeStr (FUN_005a1ec8).

*** DO NOT DEPLOY *** Abandoned. Header 「３ＤＳ本体時計」 does not pass through
MakeStr as this UTF-8 (hook left header JP). Any MakeStr cave here also blanked
other DrawText (system help). Restore from code.bin.bak_clocktext if deployed.

Softkeys are BCLIM; prefer texture / caller-specific patches for the header.
"""
from __future__ import annotations

import argparse
import shutil
import struct
from pathlib import Path

ADDR_MAKE_STR = 0x005A1EC8
ADDR_MAKE_STR_CONT = 0x005A1ECC  # after original stmdb
# Padding after ARM stubs (still inside .text, before BSS @ ~0x7881a8).
# Trailing file zeros @ 0x7BFB80 are NOT executable — that crash-booted title.
CAVE_ADDR = 0x0068F800

ORIG_MAKESTR_HEAD = bytes.fromhex("70402de9")  # stmdb sp!,{r4,r5,r6,lr} = e92d4070

# Softkeys are BCLIM now — only DrawText titles here.
# Keep EN shorter/equal UTF-8 length when possible (pane is 256px).
PAIRS: list[tuple[bytes, bytes]] = [
    ("３ＤＳ本体時計".encode("utf-8"), "3DS System Clock".encode("utf-8")),
    ("3DS本体時計".encode("utf-8"), "3DS System Clock".encode("utf-8")),
]


def u32(x: int) -> bytes:
    return struct.pack("<I", x & 0xFFFFFFFF)


def encode_imm12(value: int) -> int:
    value &= 0xFFFFFFFF
    for rot in range(16):
        if rot == 0:
            imm8 = value
        else:
            r = (rot * 2) & 31
            imm8 = ((value << r) | (value >> (32 - r))) & 0xFFFFFFFF
        if imm8 <= 0xFF:
            r = (rot * 2) & 31
            got = (
                imm8
                if r == 0
                else ((imm8 >> r) | (imm8 << (32 - r))) & 0xFFFFFFFF
            )
            if got == value:
                return (rot << 8) | imm8
    raise ValueError(f"cannot encode imm {value:#x}")


def mov_imm(rd: int, imm: int) -> bytes:
    return u32(0xE3A00000 | (rd << 12) | encode_imm12(imm))


def add_imm(rd: int, rn: int, imm: int) -> bytes:
    return u32(0xE2800000 | (rn << 16) | (rd << 12) | encode_imm12(imm))


def ldrb_imm(rd: int, rn: int, imm: int = 0) -> bytes:
    return u32(0xE5D00000 | (rn << 16) | (rd << 12) | (imm & 0xFFF))


def cmp_imm(rn: int, imm: int) -> bytes:
    return u32(0xE3500000 | (rn << 16) | encode_imm12(imm))


def cmp_reg(rn: int, rm: int) -> bytes:
    return u32(0xE1500000 | (rn << 16) | rm)


def mov_reg(rd: int, rm: int) -> bytes:
    return u32(0xE1A00000 | (rd << 12) | rm)


def push(mask: int) -> bytes:
    return u32(0xE92D0000 | mask)


def pop(mask: int) -> bytes:
    return u32(0xE8BD0000 | mask)


def b_ins(here: int, target: int) -> bytes:
    return u32(0xEA000000 | (((target - here - 8) >> 2) & 0xFFFFFF))


def b_eq(here: int, target: int) -> bytes:
    return u32(0x0A000000 | (((target - here - 8) >> 2) & 0xFFFFFF))


def b_ne(here: int, target: int) -> bytes:
    return u32(0x1A000000 | (((target - here - 8) >> 2) & 0xFFFFFF))


def assemble_cave(base: int = CAVE_ADDR) -> bytes:
    """
    r0 = string object (preserved)
    r1 = cstring in (may be replaced)
    """
    code = bytearray()
    fixups: list[tuple[int, str, int]] = []  # (off, tag, rd)
    to_done: list[int] = []
    bne_patches: list[tuple[int, int]] = []  # (off, next_pair_off) filled later
    pending_bne: list[int] = []
    pending_beq: list[int] = []
    pending_b_loop: list[tuple[int, int]] = []

    def emit(b: bytes) -> int:
        off = len(code)
        code.extend(b)
        return off

    def emit_ldr(rd: int, tag: str) -> None:
        off = emit(u32(0))
        fixups.append((off, tag, rd))

    # Original MakeStr prolog — frees r4,r5,r6 for us.
    emit(ORIG_MAKESTR_HEAD)
    emit(push(0x0001))  # save r0

    # MakeStr is often called with r1 == NULL; never deref that.
    emit(cmp_imm(1, 0))
    beq_null = emit(u32(0))  # beq done

    for i in range(len(PAIRS)):
        emit_ldr(4, f"jp{i}")  # r4 = jp
        emit_ldr(5, f"en{i}")  # r5 = en
        emit(mov_reg(6, 1))  # r6 = cand cursor
        loop = len(code)
        emit(ldrb_imm(12, 6, 0))  # r12 = *cand
        emit(ldrb_imm(0, 4, 0))  # r0  = *jp (scratch; real r0 on stack)
        emit(cmp_reg(12, 0))  # cmp *cand, *jp  (was missing — blanked all text)
        bne_off = emit(u32(0))
        emit(cmp_imm(12, 0))
        beq_off = emit(u32(0))  # both NULs → match
        emit(add_imm(6, 6, 1))
        emit(add_imm(4, 4, 1))
        b_loop_off = emit(u32(0))
        next_pair = len(code)
        code[bne_off : bne_off + 4] = b_ne(base + bne_off, base + next_pair)
        code[b_loop_off : b_loop_off + 4] = b_ins(
            base + b_loop_off, base + loop
        )
        matched = len(code)
        code[beq_off : beq_off + 4] = b_eq(base + beq_off, base + matched)
        emit(mov_reg(1, 5))  # r1 = english
        to_done.append(emit(u32(0)))

    done = len(code)
    code[beq_null : beq_null + 4] = b_eq(base + beq_null, base + done)
    for off in to_done:
        code[off : off + 4] = b_ins(base + off, base + done)

    emit(pop(0x0001))  # restore r0
    back = emit(u32(0))
    code[back : back + 4] = b_ins(base + back, ADDR_MAKE_STR_CONT)

    while len(code) & 3:
        code.append(0)

    pool_base = base + len(code)
    pool = bytearray()
    tag_addr: dict[str, int] = {}

    def add_cstr(tag: str, s: bytes) -> None:
        while len(pool) & 3:
            pool.append(0)
        tag_addr[tag] = pool_base + len(pool)
        pool.extend(s + b"\x00")

    for i, (jp, en) in enumerate(PAIRS):
        add_cstr(f"jp{i}", jp)
        add_cstr(f"en{i}", en)
    while len(pool) & 3:
        pool.append(0)

    for off, tag, rd in fixups:
        lit = tag_addr[tag]
        insn_addr = base + off
        imm = lit - (insn_addr + 8)
        if not (0 <= imm < 4096 and imm % 4 == 0):
            raise ValueError(
                f"literal too far for {tag}: imm={imm} "
                f"at {insn_addr:#x} -> {lit:#x}"
            )
        code[off : off + 4] = u32(0xE59F0000 | (rd << 12) | imm)

    return bytes(code) + bytes(pool)


def is_hooked(data: bytes) -> bool:
    return data[ADDR_MAKE_STR : ADDR_MAKE_STR + 4] == b_ins(
        ADDR_MAKE_STR, CAVE_ADDR
    )


def patch_clock_text(data: bytearray) -> bool:
    if is_hooked(data):
        return False
    head = bytes(data[ADDR_MAKE_STR : ADDR_MAKE_STR + 4])
    if head != ORIG_MAKESTR_HEAD:
        raise ValueError(
            f"unexpected MakeStr head at {ADDR_MAKE_STR:#x}: {head.hex()} "
            f"(expected {ORIG_MAKESTR_HEAD.hex()})"
        )

    cave = assemble_cave(CAVE_ADDR)
    end = CAVE_ADDR + len(cave)
    if end > len(data):
        raise ValueError(f"cave does not fit: need {end:#x}, file {len(data):#x}")
    region = data[CAVE_ADDR:end]
    if any(region):
        raise ValueError(f"cave region at {CAVE_ADDR:#x} is not empty")

    data[CAVE_ADDR:end] = cave
    data[ADDR_MAKE_STR : ADDR_MAKE_STR + 4] = b_ins(ADDR_MAKE_STR, CAVE_ADDR)
    return True


def patch_file(path: Path, *, force: bool = False) -> bool:
    raw = path.read_bytes()
    data = bytearray(raw)
    bak = path.with_suffix(path.suffix + ".bak_clocktext")

    if is_hooked(data) and not force:
        print(f"[clock-text] already patched: {path}")
        return False

    if force and is_hooked(data):
        if not bak.is_file():
            raise SystemExit("cannot --force without .bak_clocktext")
        data = bytearray(bak.read_bytes())

    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"[clock-text] backup: {bak}")

    # Sanity: cave must be clear in the bytes we're patching
    if is_hooked(data):
        # restored from bak above when force
        pass

    changed = patch_clock_text(data)
    if changed:
        path.write_bytes(data)
        print(
            f"[clock-text] hooked MakeStr @ {ADDR_MAKE_STR:#x} "
            f"-> cave {CAVE_ADDR:#x} ({len(assemble_cave())} bytes)"
        )
        print(f"[clock-text] wrote {path}")
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("code_bin", type=Path)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    patch_file(args.code_bin.resolve(), force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
