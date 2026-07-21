#!/usr/bin/env python3
"""Remap Options/Gallery/clock DrawText titles inside FUN_0024842c only.

Unlike the abandoned global MakeStr hook, this only runs for the shared
title-pane drawer (slots 0..5). Safe to bake into CIA via BLZ ExeFS inject.

JP titles are matched as UTF-8 and NLP codebook bytes (TRB encoding).
"""
from __future__ import annotations

import argparse
import shutil
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOOKUP_PATH = ROOT / "tools" / "Trb2xlsx" / "TrbExport" / "lookup.txt"

# Ghidra image base 0 / file offset == address
ADDR_DRAW_TITLE = 0x0024842C
ADDR_CONT = 0x0024844C  # after first MakeStr BL
ADDR_MAKE_STR = 0x005A1EC8
CAVE_ADDR = 0x0068F7FC  # start of zero pad after .text (~2052 bytes)
CAVE_MAX = 2052

ORIG_HEAD = bytes.fromhex("f04f2de93cd04de20150a0e10090a0e1")  # first 4 insn

# (jp_display, en, also_nlp)
TITLE_PAIRS: list[tuple[str, str, bool]] = [
    ("３ＤＳ本体時計", "3DS System Clock", True),
    ("オプション", "Options", True),
    ("ギャラリー", "Gallery", True),
    ("イベントギャラリー", "Event Gallery", True),
    ("イラストギャラリー", "Illustration Gallery", True),
    ("ギャラリーオプション", "Gallery Options", True),
    ("画面設定", "Display Settings", True),
    ("表示設定", "Display Settings", False),
    ("サウンド設定", "Sound Settings", False),
    ("ネットワーク設定", "Network Settings", True),
    ("通信設定", "Network Settings", False),
    ("パスワード入力", "Password Entry", False),
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


def bl_ins(here: int, target: int) -> bytes:
    return u32(0xEB000000 | (((target - here - 8) >> 2) & 0xFFFFFF))


def load_lookup() -> list[str]:
    return LOOKUP_PATH.read_text(encoding="utf-8").splitlines()


def nlp_encode(text: str, lookup: list[str]) -> bytes:
    out = bytearray()
    for ch in text:
        idx = lookup.index(ch) + 1
        if idx < 0x80:
            out.append(idx)
        else:
            out.append(0x80 + (idx >> 8))
            out.append(idx & 0xFF)
    return bytes(out)


def build_match_pairs() -> list[tuple[bytes, bytes]]:
    lookup = load_lookup()
    pairs: list[tuple[bytes, bytes]] = []
    seen_jp: set[bytes] = set()
    for jp, en, also_nlp in TITLE_PAIRS:
        en_b = en.encode("utf-8")
        variants = [jp.encode("utf-8")]
        if also_nlp:
            variants.append(nlp_encode(jp, lookup))
        for jp_b in variants:
            if jp_b in seen_jp:
                continue
            seen_jp.add(jp_b)
            pairs.append((jp_b, en_b))
    return pairs


def assemble_cave(pairs: list[tuple[bytes, bytes]], base: int = CAVE_ADDR) -> bytes:
    """
    Entered with: r5=pane, r9=ui, r2=cstring (original regs after 2 prolog insn).
    We ran original first 2 insn via overwritten head branch from entry.
    Actually cave is entered from entry replace of ALL 4 prolog + setup:
    Expect: nothing done yet — cave does full setup through first MakeStr.
    """
    code = bytearray()
    fixups: list[tuple[int, str, int]] = []

    def emit(b: bytes) -> int:
        off = len(code)
        code.extend(b)
        return off

    def emit_ldr(rd: int, tag: str) -> None:
        off = emit(u32(0))
        fixups.append((off, tag, rd))

    # Original prolog (first 4 instructions)
    emit(bytes.fromhex("f04f2de9"))  # stmdb sp!,{r4-r11,lr}
    emit(bytes.fromhex("3cd04de2"))  # sub sp,#0x3c
    emit(bytes.fromhex("0150a0e1"))  # mov r5,r1
    emit(bytes.fromhex("0090a0e1"))  # mov r9,r0
    # r2 still = string
    emit(cmp_imm(2, 0))
    beq_null = emit(u32(0))

    to_use: list[int] = []
    for i in range(len(pairs)):
        emit_ldr(4, f"jp{i}")
        emit_ldr(6, f"en{i}")
        emit(mov_reg(3, 2))  # r3 = cand cursor
        loop = len(code)
        emit(ldrb_imm(12, 3, 0))  # *cand
        emit(ldrb_imm(0, 4, 0))  # *jp
        emit(cmp_reg(12, 0))
        bne_off = emit(u32(0))
        emit(cmp_imm(12, 0))
        beq_off = emit(u32(0))
        emit(add_imm(3, 3, 1))
        emit(add_imm(4, 4, 1))
        b_loop = emit(u32(0))
        next_pair = len(code)
        code[bne_off : bne_off + 4] = b_ne(base + bne_off, base + next_pair)
        code[b_loop : b_loop + 4] = b_ins(base + b_loop, base + loop)
        matched = len(code)
        code[beq_off : beq_off + 4] = b_eq(base + beq_off, base + matched)
        emit(mov_reg(2, 6))  # r2 = english
        to_use.append(emit(u32(0)))

    use = len(code)
    code[beq_null : beq_null + 4] = b_eq(base + beq_null, base + use)
    for off in to_use:
        code[off : off + 4] = b_ins(base + off, base + use)

    # Original setup into first MakeStr
    emit(mov_reg(8, 2))  # r8 = str
    emit(mov_reg(1, 2))  # r1 = str
    emit(add_imm(0, 13, 8))  # r0 = sp+8
    emit(bl_ins(base + len(code), ADDR_MAKE_STR))
    emit(b_ins(base + len(code), ADDR_CONT))

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

    for i, (jp, en) in enumerate(pairs):
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

    blob = bytes(code) + bytes(pool)
    if len(blob) > CAVE_MAX:
        raise ValueError(f"cave too large: {len(blob)} > {CAVE_MAX}")
    return blob


def patch_code_bin(path: Path, *, force: bool = False) -> bool:
    data = bytearray(path.read_bytes())
    head = bytes(data[ADDR_DRAW_TITLE : ADDR_DRAW_TITLE + 16])
    hooked = head[:4] == b_ins(ADDR_DRAW_TITLE, CAVE_ADDR)
    if hooked and not force:
        print(f"[titles] already hooked: {path}")
        return False
    if not hooked and head != ORIG_HEAD and not force:
        raise ValueError(
            f"unexpected FUN_0024842c head at {ADDR_DRAW_TITLE:#x}: {head.hex()}"
        )

    pairs = build_match_pairs()
    cave = assemble_cave(pairs)
    print(f"[titles] {len(pairs)} match keys, cave {len(cave)} bytes @ {CAVE_ADDR:#x}")

    bak = path.with_suffix(path.suffix + ".bak_titles")
    if not bak.is_file():
        shutil.copy2(path, bak)
        print(f"[titles] backup -> {bak}")

    data[CAVE_ADDR : CAVE_ADDR + len(cave)] = cave
    # pad rest of cave region with original zeros if shorter
    data[ADDR_DRAW_TITLE : ADDR_DRAW_TITLE + 4] = b_ins(ADDR_DRAW_TITLE, CAVE_ADDR)
    # NOP the next 3 original prolog insn (unreachable) to make intent clear
    for i in range(1, 4):
        data[ADDR_DRAW_TITLE + i * 4 : ADDR_DRAW_TITLE + i * 4 + 4] = u32(0xE320F000)

    path.write_bytes(data)
    print(f"[titles] patched {path}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "code_bin",
        type=Path,
        nargs="?",
        default=Path.home()
        / "AppData/Roaming/Azahar/load/mods/00040000000F4E00/exefs/code.bin",
    )
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    patch_code_bin(args.code_bin.resolve(), force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
