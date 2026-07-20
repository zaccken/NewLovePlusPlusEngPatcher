#!/usr/bin/env python3
"""
Single-pane player-name draw patch for NLPP ``code.bin`` (ExeFS).

Ghidra (image base 0 / file offset == address):
  ClearNameCharPanes    @ 0x00190054  (size 0xB8)
  SetNameCharsToPanes   @ 0x00190168  (size 0x1F4)
  BackspaceNameCharPane @ 0x001908d8  (size 0xE8)

Original behavior draws one UTF-8 glyph per pane (8×3 grid). English names
look wrong because ASCII is half-width. This rewrite:
  - clears all panes
  - copies up to 8 UTF-8 characters into a contiguous buffer at obj+0x1AC
  - draws the full string on column 0 (rows 0..2 for shadow layers)
  - keeps max length at 8

Related bookmarks in the NLPP Ghidra DB (category ``localization``):
  GetCharWidthCells, InitTxtNamePanes_4Slots, SetupGirlfriendNameTagAndTextbox,
  SetNameCharsToPanes.
"""
from __future__ import annotations

import argparse
import shutil
import struct
from pathlib import Path

ADDR_CLEAR = 0x00190054
ADDR_SET = 0x00190168
ADDR_BACKSPACE = 0x001908D8
ADDR_UPDATE_CURSOR = 0x00190B48

# CesaLogo duration constant (Ghidra DAT_0016f3d4). Stock = 1_000_000_000.
ADDR_CESA_DURATION = 0x0016F3D4
ORIG_CESA_DURATION = 0x3B9ACA00
SHORT_CESA_DURATION = 1_000_000

# CesaLogo factory FUN_0016f274 — return NULL immediately (skip warning screen).
ADDR_CESA_FACTORY = 0x0016F274
ORIG_CESA_FACTORY_HEAD = bytes.fromhex("f0412de90050a0e1")  # push {r4-r8,lr}; mov r5,r0
SKIP_CESA_FACTORY = bytes.fromhex("0000a0e31eff2fe1")  # mov r0,#0; bx lr

ADDR_CLEAR_PANE = 0x0054B5FC
ADDR_MAKE_STR = 0x005A1EC8
ADDR_DRAW_TEXT = 0x0054B880
ADDR_FREE_STR = 0x005A2024
ADDR_UTF8_LEN = 0x005BF4E8
ADDR_MEMCPY = 0x00202B20

# Runtime VA of an empty C string (file offset = VA - 0x100000)
EMPTY_STR_PTR = 0x007C2F0C

SIZE_CLEAR = 0xB8
SIZE_SET = 0x1F4
SIZE_BACKSPACE = 0xE8

MAX_CHARS = 8
COUNT_OFF = 0x1EC
PANE_OFF = 0x14C

# Original function prologs (used to refuse double-patch / wrong binary)
ORIG_CLEAR_HEAD = bytes.fromhex("f04f2de93cd04de2")
ORIG_SET_HEAD = bytes.fromhex("f34f2de93cd04de2")
ORIG_BACK_HEAD = bytes.fromhex("f04f2de90060a0e1")


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
            got = imm8 if r == 0 else ((imm8 >> r) | (imm8 << (32 - r))) & 0xFFFFFFFF
            if got == value:
                return (rot << 8) | imm8
    raise ValueError(f"cannot encode imm {value:#x}")


def mov_imm(rd: int, imm: int) -> bytes:
    return u32(0xE3A00000 | (rd << 12) | encode_imm12(imm))


def add_imm(rd: int, rn: int, imm: int) -> bytes:
    return u32(0xE2800000 | (rn << 16) | (rd << 12) | encode_imm12(imm))


def sub_imm(rd: int, rn: int, imm: int) -> bytes:
    return u32(0xE2400000 | (rn << 16) | (rd << 12) | encode_imm12(imm))


def add_reg(rd: int, rn: int, rm: int, shift: int = 0) -> bytes:
    return u32(0xE0800000 | (rn << 16) | (rd << 12) | ((shift & 0x1F) << 7) | rm)


def mov_reg(rd: int, rm: int) -> bytes:
    return u32(0xE1A00000 | (rd << 12) | rm)


def cmp_imm(rn: int, imm: int) -> bytes:
    return u32(0xE3500000 | (rn << 16) | encode_imm12(imm))


def ldr_imm(rd: int, rn: int, imm: int) -> bytes:
    assert 0 <= imm <= 4095
    return u32(0xE5900000 | (rn << 16) | (rd << 12) | imm)


def str_imm(rd: int, rn: int, imm: int) -> bytes:
    assert 0 <= imm <= 4095
    return u32(0xE5800000 | (rn << 16) | (rd << 12) | imm)


def ldrb_imm(rd: int, rn: int, imm: int = 0) -> bytes:
    return u32(0xE5D00000 | (rn << 16) | (rd << 12) | imm)


def strb_imm(rd: int, rn: int, imm: int = 0) -> bytes:
    return u32(0xE5C00000 | (rn << 16) | (rd << 12) | imm)


def strb_reg(rd: int, rn: int, rm: int) -> bytes:
    return u32(0xE7C00000 | (rn << 16) | (rd << 12) | rm)


def and_imm(rd: int, rn: int, imm: int) -> bytes:
    return u32(0xE2000000 | (rn << 16) | (rd << 12) | encode_imm12(imm))


def push(mask: int) -> bytes:
    return u32(0xE92D0000 | mask)


def pop(mask: int) -> bytes:
    return u32(0xE8BD0000 | mask)


def bl(here: int, target: int) -> bytes:
    return u32(0xEB000000 | (((target - here - 8) >> 2) & 0xFFFFFF))


def b(here: int, target: int) -> bytes:
    return u32(0xEA000000 | (((target - here - 8) >> 2) & 0xFFFFFF))


def b_cond(cond: int, here: int, target: int) -> bytes:
    return u32((cond << 28) | 0x0A000000 | (((target - here - 8) >> 2) & 0xFFFFFF))


def nop() -> bytes:
    return u32(0xE320F000)


def ldr_pc_rel(rd: int, here: int, lit: int) -> bytes:
    imm = lit - (here + 8)
    assert 0 <= imm < 4096 and (imm % 4) == 0
    return u32(0xE59F0000 | (rd << 12) | imm)


def _assemble_stream(base: int, stream: list) -> bytes:
    labs: dict[str, int] = {}
    addr = base
    for it in stream:
        if it[0] == "label":
            labs[it[1]] = addr
        elif it[0] == "lit":
            if addr & 3:
                addr = (addr + 3) & ~3
            labs[it[1]] = addr
            addr += 4
        else:
            addr += 4

    out = bytearray()
    addr = base
    for it in stream:
        kind = it[0]
        if kind == "label":
            continue
        if kind == "lit":
            while len(out) & 3:
                out.append(0)
                addr += 1
            out.extend(u32(it[2]))
            addr += 4
            continue
        if kind == "op":
            out.extend(it[1])
        elif kind == "bl":
            out.extend(bl(addr, it[1]))
        elif kind == "b":
            cond = it[2]
            tgt = labs[it[1]]
            out.extend(b(addr, tgt) if cond is None else b_cond(cond, addr, tgt))
        elif kind == "ldr":
            out.extend(ldr_pc_rel(it[1], addr, labs[it[2]]))
        else:
            raise ValueError(it)
        addr += 4
    return bytes(out)


def assemble_set_name(base: int = ADDR_SET) -> bytes:
    stream: list = []

    def OP(b: bytes) -> None:
        stream.append(("op", b))

    def L(name: str) -> None:
        stream.append(("label", name))

    def BL(addr: int) -> None:
        stream.append(("bl", addr))

    def B(name: str, cond: int | None = None) -> None:
        stream.append(("b", name, cond))

    def LDR(rd: int, name: str) -> None:
        stream.append(("ldr", rd, name))

    def LIT(name: str, val: int) -> None:
        stream.append(("lit", name, val))

    OP(push(0x4FF0))  # r4-r11, lr
    OP(mov_reg(4, 0))  # obj
    OP(mov_reg(5, 1))  # src
    OP(sub_imm(13, 13, 0x60))

    OP(add_imm(8, 4, 0x1AC))  # buf

    OP(mov_imm(7, 0))
    L("zero")
    OP(mov_imm(0, 0))
    OP(strb_reg(0, 8, 7))
    OP(add_imm(7, 7, 1))
    OP(cmp_imm(7, 64))
    B("zero", cond=0xB)

    OP(mov_imm(0, 0))
    OP(str_imm(0, 4, COUNT_OFF))

    # clear 8 cols × 3 rows
    OP(mov_imm(7, 0))
    L("ccol")
    OP(mov_imm(6, 0))
    L("crow")
    OP(add_reg(0, 4, 6, shift=5))
    OP(add_reg(0, 0, 7, shift=2))
    OP(ldr_imm(9, 0, PANE_OFF))
    OP(mov_reg(0, 9))
    BL(ADDR_CLEAR_PANE)
    LDR(1, "empty")
    OP(add_imm(0, 13, 0x08))
    BL(ADDR_MAKE_STR)
    OP(mov_reg(3, 0))
    OP(mov_imm(2, 0))
    OP(mov_imm(1, 0))
    OP(mov_reg(0, 9))
    OP(mov_imm(10, 1))
    OP(mov_imm(11, 0))
    OP(u32(0xE1CDA0F0))  # strd r10, r11, [sp]
    BL(ADDR_DRAW_TEXT)
    OP(add_imm(0, 13, 0x08))
    BL(ADDR_FREE_STR)
    OP(add_imm(6, 6, 1))
    OP(cmp_imm(6, 3))
    B("crow", cond=0xB)
    OP(add_imm(7, 7, 1))
    OP(cmp_imm(7, 8))
    B("ccol", cond=0xB)

    OP(cmp_imm(5, 0))
    B("after_copy", cond=0x0)

    OP(mov_imm(7, 0))  # bytes
    OP(mov_imm(6, 0))  # chars
    L("copy")
    OP(ldrb_imm(0, 5, 0))
    OP(cmp_imm(0, 0))
    B("copy_done", cond=0x0)
    OP(cmp_imm(6, MAX_CHARS))
    B("copy_done", cond=0xA)
    BL(ADDR_UTF8_LEN)
    OP(cmp_imm(0, 1))
    B("copy_done", cond=0xB)
    OP(cmp_imm(0, 7))
    B("copy_done", cond=0xC)
    OP(add_reg(1, 7, 0))
    OP(cmp_imm(1, 63))
    B("copy_done", cond=0xA)
    OP(mov_reg(2, 0))
    OP(mov_reg(1, 5))
    OP(add_reg(0, 8, 7))
    OP(mov_reg(9, 2))
    BL(ADDR_MEMCPY)
    OP(add_reg(7, 7, 9))
    OP(add_reg(5, 5, 9))
    OP(add_imm(6, 6, 1))
    OP(mov_imm(0, 0))
    OP(strb_reg(0, 8, 7))
    B("copy")

    L("copy_done")
    OP(str_imm(6, 4, COUNT_OFF))

    L("after_copy")
    OP(mov_imm(6, 0))
    L("drow")
    OP(add_reg(0, 4, 6, shift=5))
    OP(ldr_imm(9, 0, PANE_OFF))
    OP(cmp_imm(9, 0))
    B("drow_next", cond=0x0)
    OP(mov_reg(0, 9))
    BL(ADDR_CLEAR_PANE)
    OP(mov_reg(1, 8))
    OP(add_imm(0, 13, 0x08))
    BL(ADDR_MAKE_STR)
    OP(mov_reg(3, 0))
    OP(mov_imm(2, 0))
    OP(mov_imm(1, 0))
    OP(mov_reg(0, 9))
    OP(mov_imm(10, 1))
    OP(mov_imm(11, 0))
    OP(u32(0xE1CDA0F0))
    BL(ADDR_DRAW_TEXT)
    OP(add_imm(0, 13, 0x08))
    BL(ADDR_FREE_STR)
    L("drow_next")
    OP(add_imm(6, 6, 1))
    OP(cmp_imm(6, 3))
    B("drow", cond=0xB)

    OP(mov_reg(0, 4))
    BL(ADDR_UPDATE_CURSOR)
    OP(add_imm(13, 13, 0x60))
    OP(pop(0x8FF0))  # r4-r11, pc

    LIT("empty", EMPTY_STR_PTR)

    code = _assemble_stream(base, stream)
    if len(code) > SIZE_SET:
        raise ValueError(f"SetName too large: {len(code)} > {SIZE_SET:#x}")
    return code + nop() * ((SIZE_SET - len(code)) // 4)


def assemble_clear(base: int = ADDR_CLEAR, set_addr: int = ADDR_SET) -> bytes:
    code = bytearray()
    code.extend(u32(0xE59F1004))  # ldr r1, [pc, #4]
    code.extend(b(base + 4, set_addr))
    code.extend(u32(EMPTY_STR_PTR))
    while len(code) < SIZE_CLEAR:
        code.extend(nop())
    return bytes(code[:SIZE_CLEAR])


def assemble_backspace(base: int = ADDR_BACKSPACE, set_addr: int = ADDR_SET) -> bytes:
    stream: list = []

    def OP(b: bytes) -> None:
        stream.append(("op", b))

    def L(name: str) -> None:
        stream.append(("label", name))

    def BL(addr: int) -> None:
        stream.append(("bl", addr))

    def B(name: str, cond: int | None = None) -> None:
        stream.append(("b", name, cond))

    OP(push(0x4010))  # r4, lr
    OP(mov_reg(4, 0))
    OP(add_imm(0, 4, 0x1AC))
    OP(mov_imm(2, 0))
    L("len")
    OP(ldrb_imm(3, 0, 0))
    OP(cmp_imm(3, 0))
    B("len_done", 0x0)
    OP(add_imm(0, 0, 1))
    OP(add_imm(2, 2, 1))
    OP(cmp_imm(2, 64))
    B("len", 0xB)
    L("len_done")
    OP(cmp_imm(2, 0))
    B("do_redraw", 0x0)
    OP(sub_imm(0, 0, 1))
    OP(sub_imm(2, 2, 1))
    L("walk")
    OP(cmp_imm(2, 0))
    B("cut", 0x0)
    OP(ldrb_imm(3, 0, 0))
    OP(and_imm(3, 3, 0xC0))
    OP(cmp_imm(3, 0x80))
    B("cut", 0x1)  # NE → char start
    OP(sub_imm(0, 0, 1))
    OP(sub_imm(2, 2, 1))
    B("walk")
    L("cut")
    OP(mov_imm(3, 0))
    OP(strb_imm(3, 0, 0))
    L("do_redraw")
    OP(mov_reg(0, 4))
    OP(add_imm(1, 4, 0x1AC))
    BL(set_addr)
    OP(pop(0x8010))  # r4, pc

    code = bytearray(_assemble_stream(base, stream))
    if len(code) > SIZE_BACKSPACE:
        raise ValueError(f"Backspace too large: {len(code)} > {SIZE_BACKSPACE:#x}")
    while len(code) < SIZE_BACKSPACE:
        code.extend(nop())
    return bytes(code[:SIZE_BACKSPACE])


def is_vanilla_code(data: bytes) -> bool:
    return (
        data[ADDR_CLEAR : ADDR_CLEAR + 8] == ORIG_CLEAR_HEAD
        and data[ADDR_SET : ADDR_SET + 8] == ORIG_SET_HEAD
        and data[ADDR_BACKSPACE : ADDR_BACKSPACE + 8] == ORIG_BACK_HEAD
    )


def is_patched_code(data: bytes) -> bool:
    """Patched Clear starts with ldr r1, [pc, #4]."""
    return data[ADDR_CLEAR : ADDR_CLEAR + 4] == bytes.fromhex("04109fe5")


def patch_cesa_duration(
    data: bytearray,
    *,
    duration: int = SHORT_CESA_DURATION,
) -> bool:
    """Shorten CesaLogo on-screen time. Returns True if bytes changed."""
    cur = struct.unpack_from("<I", data, ADDR_CESA_DURATION)[0]
    if cur == duration:
        return False
    struct.pack_into("<I", data, ADDR_CESA_DURATION, duration & 0xFFFFFFFF)
    return True


def patch_skip_cesa_logo(data: bytearray) -> bool:
    """Make CesaLogo factory return NULL (skip the warning screen entirely)."""
    head = bytes(data[ADDR_CESA_FACTORY : ADDR_CESA_FACTORY + 8])
    if head == SKIP_CESA_FACTORY:
        return False
    if head != ORIG_CESA_FACTORY_HEAD:
        raise ValueError(
            f"unexpected CesaLogo factory head at {ADDR_CESA_FACTORY:#x}: {head.hex()}"
        )
    data[ADDR_CESA_FACTORY : ADDR_CESA_FACTORY + 8] = SKIP_CESA_FACTORY
    return True


def patch_code_bin(
    path: Path,
    *,
    force: bool = False,
    name_panes: bool = True,
    cesa_duration: int | None = None,
    skip_cesa_logo: bool = False,
) -> bool:
    """
    Patch ``code.bin`` in place. Returns True if bytes were written.
    Creates ``code.bin.bak`` on first run from vanilla.
    """
    data = bytearray(path.read_bytes())
    bak = path.with_suffix(path.suffix + ".bak")
    changed = False

    if name_panes:
        if is_patched_code(data) and not force:
            print(f"[code] name-pane already patched: {path}")
        else:
            if not is_vanilla_code(data):
                if bak.is_file() and is_vanilla_code(bak.read_bytes()):
                    print(f"[code] restoring vanilla from {bak.name} before re-patch")
                    data = bytearray(bak.read_bytes())
                elif not force:
                    raise ValueError(
                        f"{path} does not look like vanilla NLPP code.bin "
                        f"(unexpected prologs at name-pane functions)"
                    )

            if not bak.exists():
                shutil.copy2(path, bak)
                print(f"[code] backup: {bak}")

            clear = assemble_clear()
            set_name = assemble_set_name()
            back = assemble_backspace()
            assert len(clear) == SIZE_CLEAR
            assert len(set_name) == SIZE_SET
            assert len(back) == SIZE_BACKSPACE

            data[ADDR_CLEAR : ADDR_CLEAR + SIZE_CLEAR] = clear
            data[ADDR_SET : ADDR_SET + SIZE_SET] = set_name
            data[ADDR_BACKSPACE : ADDR_BACKSPACE + SIZE_BACKSPACE] = back
            changed = True
            print(f"[code] patched single-pane name draw -> {path}")

    if skip_cesa_logo:
        if not bak.exists():
            shutil.copy2(path, bak)
            print(f"[code] backup: {bak}")
        if patch_skip_cesa_logo(data):
            changed = True
            print(f"[code] CesaLogo factory -> return NULL @ {ADDR_CESA_FACTORY:#x}")
        else:
            print("[code] CesaLogo already skipped")

    if cesa_duration is not None:
        if not bak.exists():
            shutil.copy2(path, bak)
            print(f"[code] backup: {bak}")
        if patch_cesa_duration(data, duration=cesa_duration):
            changed = True
            print(
                f"[code] CesaLogo duration {ORIG_CESA_DURATION} -> {cesa_duration} "
                f"@ {ADDR_CESA_DURATION:#x}"
            )
        else:
            print(f"[code] CesaLogo duration already {cesa_duration}")

    if changed:
        path.write_bytes(data)
    return changed


def write_patched_code_bin(src: Path, dest: Path, *, force: bool = False) -> Path:
    """Copy ``src`` to ``dest`` (if needed) and patch ``dest``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.resolve() != src.resolve():
        shutil.copy2(src, dest)
    patch_code_bin(dest, force=force)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "code_bin",
        nargs="?",
        type=Path,
        help="Path to code.bin (default: sibling extracted/exefs/code.bin)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-patch even if already patched / unrecognized",
    )
    ap.add_argument(
        "--out",
        type=Path,
        help="Write patched copy here instead of modifying in place",
    )
    ap.add_argument(
        "--cesa-only",
        action="store_true",
        help="Only apply CESA-related patches (no name-pane rewrite)",
    )
    ap.add_argument(
        "--skip-cesa-logo",
        action="store_true",
        help="Skip CesaLogo factory entirely (recommended; avoids white boot screen)",
    )
    ap.add_argument(
        "--cesa-duration",
        type=int,
        default=None,
        help="Optional CesaLogo duration literal (use with --skip-cesa-logo only if needed)",
    )
    args = ap.parse_args(argv)

    default = (
        Path(__file__).resolve().parents[2]
        / "New Love Plus Plus"
        / "extracted"
        / "exefs"
        / "code.bin"
    )
    src = (args.code_bin or default).resolve()
    if not src.is_file():
        raise SystemExit(f"missing {src}")

    name_panes = not args.cesa_only
    skip_cesa = args.skip_cesa_logo or args.cesa_only
    if args.out:
        dest = args.out.resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.resolve() != src.resolve():
            shutil.copy2(src, dest)
        patch_code_bin(
            dest,
            force=args.force,
            name_panes=name_panes,
            cesa_duration=args.cesa_duration,
            skip_cesa_logo=skip_cesa,
        )
    else:
        patch_code_bin(
            src,
            force=args.force,
            name_panes=name_panes,
            cesa_duration=args.cesa_duration,
            skip_cesa_logo=skip_cesa,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
