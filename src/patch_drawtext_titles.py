#!/usr/bin/env python3
"""Remap UI DrawText titles at DrawTextToPane only (CIA-safe).

Hooks FUN_0054b880 (DrawTextToPane). On entry r3 = MakeStr object; [r3+4] is
the cstring pointer that DrawText re-MakeStr's from. Exact-match remap of known
JP titles (UTF-8 / NLP / SJIS) to EN; all other DrawText calls pass through.

Unlike the abandoned global MakeStr hook and the FUN_0024842c-only remapper
(missed clock header path), this is the shared draw site for Options/Gallery/
clock alpha headers.

Cave: 0x68F7FC (~2052 bytes zero pad after .text). Bake via patch_cia --inject-code.
"""
from __future__ import annotations

import argparse
import shutil
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOOKUP_PATH = ROOT / "tools" / "Trb2xlsx" / "TrbExport" / "lookup.txt"

ADDR_DRAW = 0x0054B880
ADDR_CONT = 0x0054B890  # after first 4 instructions
CAVE_ADDR = 0x0068F7FC
CAVE_MAX = 2052
# code.bin file offset == Ghidra addr; CPU maps .text at +0x100000
RUNTIME_BASE = 0x00100000

ORIG_HEAD = bytes.fromhex("ff5f2de959de4de20110a0e30060a0e1")

# (jp, en, nlp?, sjis?)
TITLE_PAIRS: list[tuple[str, str, bool, bool]] = [
    ("３ＤＳ本体時計", "3DS System Clock", True, True),
    ("オプション", "Options", True, True),
    ("ギャラリー", "Gallery", True, True),
    ("イベントギャラリー", "Event Gallery", True, True),
    ("イラストギャラリー", "Illustration Gallery", True, True),
    ("ギャラリーオプション", "Gallery Options", True, True),
    ("画面設定", "Display Settings", True, True),
    ("表示設定", "Display Settings", False, True),
    ("サウンド設定", "Sound Settings", False, True),
    ("ネットワーク設定", "Network Settings", True, True),
    ("通信設定", "Network Settings", False, True),
    ("パスワード入力", "Password Entry", False, True),
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


def ldr_imm(rd: int, rn: int, imm: int = 0) -> bytes:
    return u32(0xE5900000 | (rn << 16) | (rd << 12) | (imm & 0xFFF))


def str_imm(rd: int, rn: int, imm: int = 0) -> bytes:
    return u32(0xE5800000 | (rn << 16) | (rd << 12) | (imm & 0xFFF))


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
    for jp, en, also_nlp, also_sjis in TITLE_PAIRS:
        en_b = en.encode("utf-8")
        variants = [jp.encode("utf-8")]
        if also_nlp:
            variants.append(nlp_encode(jp, lookup))
        if also_sjis:
            try:
                variants.append(jp.encode("cp932"))
            except UnicodeEncodeError:
                pass
        for jp_b in variants:
            if jp_b in seen_jp:
                continue
            seen_jp.add(jp_b)
            pairs.append((jp_b, en_b))
    return pairs


def assemble_cave(pairs: list[tuple[bytes, bytes]], base: int = CAVE_ADDR) -> bytes:
    """Entered with original r0..r3 (pane,x,y,MakeStr). Remap [r3+4] then trampoline.

    Compact table walk: [jp_ptr, en_ptr]... null-terminated. EN strings are shared.
    """
    code = bytearray()

    def emit(b: bytes) -> int:
        off = len(code)
        code.extend(b)
        return off

    # Save caller regs except r3. Mask: r0-r2,r4-r12,lr
    save_mask = 0x1 | 0x2 | 0x4 | 0x1FF0 | 0x4000
    emit(push(save_mask))

    emit(ldr_imm(1, 3, 4))  # r1 = cand cstring
    emit(cmp_imm(1, 0))
    beq_null = emit(u32(0))

    # r4 = &table via add r4, pc, #imm (filled after layout known)
    adr_table = emit(u32(0))

    # table loop
    tab_loop = len(code)
    emit(ldr_imm(5, 4, 0))  # r5 = jp_ptr
    emit(add_imm(4, 4, 4))
    emit(cmp_imm(5, 0))
    beq_end = emit(u32(0))  # no more entries
    emit(ldr_imm(6, 4, 0))  # r6 = en_ptr
    emit(add_imm(4, 4, 4))

    # strcmp r1 vs r5
    emit(mov_reg(2, 1))  # cand cursor
    emit(mov_reg(7, 5))  # jp cursor
    cmp_loop = len(code)
    emit(ldrb_imm(12, 2, 0))
    emit(ldrb_imm(0, 7, 0))
    emit(cmp_reg(12, 0))  # cand vs jp byte in r0
    bne_next = emit(u32(0))
    emit(cmp_imm(12, 0))
    beq_match = emit(u32(0))
    emit(add_imm(2, 2, 1))
    emit(add_imm(7, 7, 1))
    emit(b_ins(base + len(code), base + cmp_loop))

    next_ent = len(code)
    code[bne_next : bne_next + 4] = b_ne(base + bne_next, base + next_ent)
    emit(b_ins(base + len(code), base + tab_loop))

    matched = len(code)
    code[beq_match : beq_match + 4] = b_eq(base + beq_match, base + matched)
    emit(str_imm(6, 3, 4))  # [r3+4] = EN

    done = len(code)
    code[beq_null : beq_null + 4] = b_eq(base + beq_null, base + done)
    code[beq_end : beq_end + 4] = b_eq(base + beq_end, base + done)

    emit(pop(save_mask))
    emit(ORIG_HEAD)
    emit(b_ins(base + len(code), ADDR_CONT))

    while len(code) & 3:
        code.append(0)

    # ---- pool: unique strings, then pointer table ----
    # Dedup EN; each pair is (jp_bytes, en_bytes)
    en_unique: dict[bytes, None] = {}
    jp_list: list[bytes] = []
    en_for_jp: list[bytes] = []
    seen_jp: set[bytes] = set()
    for jp, en in pairs:
        if jp in seen_jp:
            continue
        seen_jp.add(jp)
        jp_list.append(jp)
        en_for_jp.append(en)
        en_unique[en] = None

    pool = bytearray()
    str_addr: dict[bytes, int] = {}

    def add_str(s: bytes) -> int:
        if s in str_addr:
            return str_addr[s]
        while len(pool) & 3:
            pool.append(0)
        # Absolute pointer as seen by the guest CPU (file offset + load bias)
        addr = base + len(code) + len(pool) + RUNTIME_BASE
        str_addr[s] = addr
        pool.extend(s + b"\x00")
        return addr

    for s in en_unique:
        add_str(s)
    for s in jp_list:
        add_str(s)

    while len(pool) & 3:
        pool.append(0)

    table_off = len(pool)
    table_addr = base + len(code) + table_off
    for jp, en in zip(jp_list, en_for_jp):
        pool.extend(u32(str_addr[jp]))
        pool.extend(u32(str_addr[en]))
    pool.extend(u32(0))  # sentinel

    # add r4, pc, #imm -> table
    imm = table_addr - (base + adr_table + 8)
    code[adr_table : adr_table + 4] = add_imm(4, 15, imm)  # rn=15 = pc

    blob = bytes(code) + bytes(pool)
    if len(blob) > CAVE_MAX:
        raise ValueError(f"cave too large: {len(blob)} > {CAVE_MAX}")
    return blob


def patch_code_bin(path: Path, *, force: bool = False) -> bool:
    data = bytearray(path.read_bytes())
    head = bytes(data[ADDR_DRAW : ADDR_DRAW + 16])
    hooked = head[:4] == b_ins(ADDR_DRAW, CAVE_ADDR)
    if hooked and not force:
        print(f"[drawtext] already hooked: {path}")
        return False
    if not hooked and head != ORIG_HEAD and not force:
        raise ValueError(
            f"unexpected DrawTextToPane head at {ADDR_DRAW:#x}: {head.hex()}"
        )

    pairs = build_match_pairs()
    cave = assemble_cave(pairs)
    print(f"[drawtext] {len(pairs)} match keys, cave {len(cave)} bytes @ {CAVE_ADDR:#x}")

    bak = path.with_suffix(path.suffix + ".bak_drawtext")
    if not bak.is_file():
        # Prefer clean original over a previously hooked titles binary
        titles_bak = path.with_suffix(path.suffix + ".bak_titles")
        clock_bak = path.with_suffix(path.suffix + ".bak_clocktext")
        src_bak = titles_bak if titles_bak.is_file() else clock_bak
        if src_bak.is_file():
            shutil.copy2(src_bak, bak)
            data = bytearray(bak.read_bytes())
            print(f"[drawtext] seeded backup from {src_bak.name}")
        else:
            shutil.copy2(path, bak)
        print(f"[drawtext] backup -> {bak}")

    # Ensure we patch a clean head if force-replacing a prior hook
    if force or hooked:
        clean = bak.read_bytes()
        data = bytearray(clean)

    if bytes(data[ADDR_DRAW : ADDR_DRAW + 16]) != ORIG_HEAD:
        raise ValueError("backup does not have clean DrawTextToPane head")

    # Clear any leftover FUN_0024842c / MakeStr experiments in cave region
    data[CAVE_ADDR : CAVE_ADDR + CAVE_MAX] = b"\x00" * CAVE_MAX
    # Restore FUN_0024842c if it was branched into the cave
    draw_title = 0x0024842C
    titles_orig = bytes.fromhex("f04f2de93cd04de20150a0e10090a0e1")
    if bytes(data[draw_title : draw_title + 16]) != titles_orig:
        data[draw_title : draw_title + 16] = titles_orig
        print("[drawtext] restored FUN_0024842c head")

    data[CAVE_ADDR : CAVE_ADDR + len(cave)] = cave
    data[ADDR_DRAW : ADDR_DRAW + 4] = b_ins(ADDR_DRAW, CAVE_ADDR)
    for i in range(1, 4):
        data[ADDR_DRAW + i * 4 : ADDR_DRAW + i * 4 + 4] = u32(0xE320F000)  # nop

    path.write_bytes(data)
    print(f"[drawtext] patched {path}")
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
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        pairs = build_match_pairs()
        cave = assemble_cave(pairs)
        print(f"pairs={len(pairs)} cave={len(cave)}")
        for jp, en in pairs:
            print(f"  {jp.hex()} -> {en!r}")
        return 0
    patch_code_bin(args.code_bin.resolve(), force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
