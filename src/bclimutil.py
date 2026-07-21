#!/usr/bin/env python3
"""Minimal BCLIM helpers for NLPP UI text textures.

CLIM format IDs (GBATEK / this game):
  1 = A8, 8 = RGBA4444, 0xB/11 = ETC1A4, 0xD/13 = A4

Title button labels are RGBA4444 (fmt 8). FileSelect label sheets are
ETC1A4 (fmt 0xB) — 16 bytes per 4x4 block, stored in 8x8-tile Z-order.
Option06 date-unit glyphs (年/月/日) are A4 (fmt 0xD).
"""

from __future__ import annotations

import struct
from pathlib import Path

import etcpak
from PIL import Image


def nlpo2(x: int) -> int:
    if x <= 0:
        return 1
    p = 1
    while p < x:
        p <<= 1
    return p


def gcm(n: int, m: int) -> int:
    return ((n + m - 1) // m) * m


def d2xy(idx: int) -> tuple[int, int]:
    """Morton decode (Z-order) for 8x8 tile index 0..63."""
    x = y = 0
    for i in range(4):
        x |= ((idx >> (2 * i)) & 1) << i
        y |= ((idx >> (2 * i + 1)) & 1) << i
    return x, y


def parse_bclim(data: bytes) -> tuple[bytes, int, int, int, bytes]:
    """Return (pixels, width, height, format, footer_from_clim)."""
    clim = data.rfind(b"CLIM")
    if clim < 0:
        raise ValueError("not a BCLIM")
    imag = data.rfind(b"imag")
    if imag < 0 or imag < clim:
        raise ValueError("missing imag")
    width, height, fmt = struct.unpack_from("<HHI", data, imag + 8)
    return data[:clim], width, height, fmt, data[clim:]


def rectangular_pot(width: int, height: int) -> tuple[int, int]:
    """png2bclim-style pad: keep short UI strips rectangular."""
    if min(width, height) <= 32:
        return nlpo2(width), nlpo2(height)
    side = max(nlpo2(width), nlpo2(height))
    return side, side


def encode_tiled_pixels(
    img: Image.Image,
    pot_w: int,
    pot_h: int,
    write_pixel,
) -> bytes:
    """Walk 3DS Morton 8x8 tiles and call write_pixel(x, y, px) → bytes."""
    src = img.convert("RGBA")
    canvas = Image.new("RGBA", (pot_w, pot_h), (0, 0, 0, 0))
    canvas.paste(src, (0, 0))
    px = canvas.load()
    out = bytearray()
    tiles_x = gcm(pot_w, 8) // 8
    if tiles_x == 0:
        tiles_x = 1
    for i in range(pot_w * pot_h):
        mx, my = d2xy(i % 64)
        tile = i // 64
        x = mx + (tile % tiles_x) * 8
        y = my + (tile // tiles_x) * 8
        if x >= pot_w or y >= pot_h:
            out.extend(write_pixel(0, 0, 0, 0))
        else:
            r, g, b, a = px[x, y]
            # Match png2bclim transparent fill.
            if a == 0:
                r, g, b, a = 86, 86, 86, 0
            out.extend(write_pixel(r, g, b, a))
    return bytes(out)


def encode_a8_pixels(img: Image.Image, pot_w: int, pot_h: int) -> bytes:
    def write(r, g, b, a):
        return bytes([a])

    return encode_tiled_pixels(img, pot_w, pot_h, write)


# Ohana / PICA 8x8 Morton tile index order (same as d2xy linearization).
_TILE_ORDER = [
    0, 1, 8, 9, 2, 3, 10, 11, 16, 17, 24, 25, 18, 19, 26, 27,
    4, 5, 12, 13, 6, 7, 14, 15, 20, 21, 28, 29, 22, 23, 30, 31,
    32, 33, 40, 41, 34, 35, 42, 43, 48, 49, 56, 57, 50, 51, 58, 59,
    36, 37, 44, 45, 38, 39, 46, 47, 52, 53, 60, 61, 54, 55, 62, 63,
]


def encode_a4_pixels(img: Image.Image, pot_w: int, pot_h: int) -> bytes:
    """Pack 4-bit alpha (CLIM fmt 0xD) — low nibble first, Tile_Order, no Y-flip."""
    src = img.convert("RGBA")
    canvas = Image.new("RGBA", (pot_w, pot_h), (0, 0, 0, 0))
    canvas.paste(src, (0, 0))
    px = canvas.load()
    nibs: list[int] = []
    for ty in range(0, pot_h, 8):
        for tx in range(0, pot_w, 8):
            for i in range(64):
                dx = _TILE_ORDER[i] % 8
                dy = _TILE_ORDER[i] // 8
                x, y = tx + dx, ty + dy
                if x >= pot_w or y >= pot_h:
                    nibs.append(0)
                else:
                    nibs.append(px[x, y][3] >> 4)
    out = bytearray()
    for i in range(0, len(nibs), 2):
        out.append((nibs[i] & 0xF) | ((nibs[i + 1] & 0xF) << 4))
    return bytes(out)


def png_to_bclim_a4_same_size(png: Path, orig_bclim: Path) -> bytes:
    """Build A4 (CLIM fmt 0xD) BCLIM matching the original file length."""
    orig = orig_bclim.read_bytes()
    pix, width, height, fmt, footer = parse_bclim(orig)
    if fmt != 0xD:
        raise ValueError(f"expected A4 fmt 0xD, got {fmt:#x}")
    pot_w, pot_h = canvas_for_pixel_bytes(len(pix) * 2, width, height, 1)
    need = (pot_w * pot_h) // 2
    pixels = encode_a4_pixels(Image.open(png), pot_w, pot_h)
    if len(pixels) != need:
        raise ValueError(f"A4 size mismatch {len(pixels)} != {need}")
    if len(pixels) != len(pix):
        raise ValueError(f"A4 payload mismatch {len(pixels)} != {len(pix)}")
    out = pixels + footer
    if len(out) != len(orig):
        raise ValueError(f"BCLIM size changed {len(orig)} -> {len(out)}")
    return out


def encode_rgba4444_pixels(img: Image.Image, pot_w: int, pot_h: int) -> bytes:
    def write(r, g, b, a):
        val = (a // 0x11) + ((b // 0x11) << 4) + ((g // 0x11) << 8) + ((r // 0x11) << 12)
        return struct.pack("<H", val)

    return encode_tiled_pixels(img, pot_w, pot_h, write)


def canvas_for_pixel_bytes(
    pixel_len: int, width: int, height: int, bytes_per_pixel: int
) -> tuple[int, int]:
    px = pixel_len // bytes_per_pixel
    if pixel_len % bytes_per_pixel:
        raise ValueError(
            f"pixel buffer {pixel_len} not divisible by {bytes_per_pixel} bpp"
        )
    # Exact display size (common for already-POT textures like 256x512).
    if width * height == px:
        return width, height
    pot_w, pot_h = rectangular_pot(width, height)
    if pot_w * pot_h == px:
        return pot_w, pot_h
    for cand in (
        (256, 512),
        (512, 256),
        (256, 256),
        (512, 512),
        (256, 64),
        (128, 64),
        (256, 32),
        (128, 32),
        (64, 64),
        (512, 16),
        (128, 16),
    ):
        if cand[0] * cand[1] == px:
            return cand
    raise ValueError(
        f"cannot map pixel buffer {pixel_len} ({bytes_per_pixel} bpp) "
        f"for {width}x{height}"
    )


def _rewrite_footer(
    footer: bytes, width: int, height: int, fmt: int, need: int
) -> bytes:
    new_footer = bytearray(footer)
    imag_off = new_footer.find(b"imag")
    if imag_off < 0:
        raise ValueError("footer missing imag")
    struct.pack_into("<HHI", new_footer, imag_off + 8, width, height, fmt)
    struct.pack_into("<I", new_footer, imag_off + 16, need)
    struct.pack_into("<I", new_footer, 0x0C, need + len(new_footer))
    return bytes(new_footer)


def etc1_scramble(width: int, height: int) -> list[int]:
    """Ohana3DS / PICA200 ETC1 block scramble table."""
    n = (width // 4) * (height // 4)
    tile_scramble = [0] * n
    base_accumulator = 0
    row_accumulator = 0
    base_number = 0
    row_number = 0
    width_blocks = width // 4
    for tile in range(n):
        if tile % width_blocks == 0 and tile > 0:
            if row_accumulator < 1:
                row_accumulator += 1
                row_number += 2
                base_number = row_number
            else:
                row_accumulator = 0
                base_number -= 2
                row_number = base_number
        tile_scramble[tile] = base_number
        if base_accumulator < 1:
            base_accumulator += 1
            base_number += 1
        else:
            base_accumulator = 0
            base_number += 3
    return tile_scramble


def encode_etc1a4_pixels(img: Image.Image, width: int, height: int) -> bytes:
    """Compress RGBA to 3DS ETC1A4 (fmt 0xB) with Ohana scramble + color endian."""
    src = img.convert("RGBA")
    if src.size != (width, height):
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(src, (0, 0))
        src = canvas
    px = src.load()
    etc1 = etcpak.compress_to_etc1(src.tobytes(), width, height)
    bx_n, by_n = width // 4, height // 4
    need = bx_n * by_n * 16
    if len(etc1) != bx_n * by_n * 8:
        raise ValueError(f"ETC1 compress size {len(etc1)} != {bx_n * by_n * 8}")

    # Raster-order ETC1A4 blocks (alpha nibbles + byte-reversed ETC1 color).
    linear = bytearray(need)
    for by in range(by_n):
        for bx in range(bx_n):
            alpha = bytearray(8)
            toggle = False
            ai = 0
            for t_x in range(4):
                for t_y in range(4):
                    a = px[bx * 4 + t_x, by * 4 + t_y][3] // 17
                    if not toggle:
                        alpha[ai] = a & 0xF
                        toggle = True
                    else:
                        alpha[ai] |= (a & 0xF) << 4
                        toggle = False
                        ai += 1
            eoff = (by * bx_n + bx) * 8
            color = bytes(reversed(etc1[eoff : eoff + 8]))
            off = (by * bx_n + bx) * 16
            linear[off : off + 8] = alpha
            linear[off + 8 : off + 16] = color

    # Inverse of Ohana decode remap: file[j] = linear[inv[j]].
    scramble = etc1_scramble(width, height)
    inv = [0] * len(scramble)
    for i, s in enumerate(scramble):
        inv[s] = i
    out = bytearray(need)
    for j, src_i in enumerate(inv):
        out[j * 16 : (j + 1) * 16] = linear[src_i * 16 : (src_i + 1) * 16]
    return bytes(out)


def png_to_bclim_a8_same_size(png: Path, orig_bclim: Path) -> bytes:
    """Build an A8 (CLIM fmt 1) BCLIM matching the original file length."""
    orig = orig_bclim.read_bytes()
    pix, width, height, _fmt, footer = parse_bclim(orig)
    pot_w, pot_h = canvas_for_pixel_bytes(len(pix), width, height, 1)
    need = pot_w * pot_h
    pixels = encode_a8_pixels(Image.open(png), pot_w, pot_h)
    if len(pixels) != need:
        raise ValueError(f"A8 size mismatch {len(pixels)} != {need}")
    out = pixels + _rewrite_footer(footer, width, height, 1, need)
    if len(out) != len(orig):
        raise ValueError(f"BCLIM size changed {len(orig)} -> {len(out)}")
    return out


def png_to_bclim_rgba4444_same_size(png: Path, orig_bclim: Path) -> bytes:
    """Build RGBA4444 (CLIM fmt 8) BCLIM matching the original file length."""
    orig = orig_bclim.read_bytes()
    pix, width, height, fmt, footer = parse_bclim(orig)
    if fmt != 8:
        raise ValueError(f"expected RGBA4444 fmt 8, got {fmt}")
    pot_w, pot_h = canvas_for_pixel_bytes(len(pix), width, height, 2)
    need = pot_w * pot_h * 2
    pixels = encode_rgba4444_pixels(Image.open(png), pot_w, pot_h)
    if len(pixels) != need:
        raise ValueError(f"RGBA4444 size mismatch {len(pixels)} != {need}")
    # Keep format 8; only refresh size fields.
    out = pixels + _rewrite_footer(footer, width, height, 8, need)
    if len(out) != len(orig):
        raise ValueError(f"BCLIM size changed {len(orig)} -> {len(out)}")
    return out


def png_to_bclim_etc1a4_same_size(png: Path, orig_bclim: Path) -> bytes:
    """Build ETC1A4 (CLIM fmt 0xB) BCLIM matching the original file length.

    Pixel payload size is (pot_w/4)*(pot_h/4)*16. For many UI textures the
    compressed canvas is larger than the logical BCLIM width/height (e.g.
    160x72 stored in a 256x128 ETC1A4 canvas), so pot size is derived from
    the original pixel buffer length rather than from width/height alone.
    """
    orig = orig_bclim.read_bytes()
    pix, width, height, fmt, footer = parse_bclim(orig)
    if fmt != 0xB:
        raise ValueError(f"expected ETC1A4 fmt 0xB, got {fmt:#x}")
    # 16 bytes per 4x4 block
    blocks = len(pix) // 16
    # Prefer next-pow2(w) x next-pow2(h) when it matches the buffer.
    def _next_pot(x: int) -> int:
        p = 1
        while p < x:
            p *= 2
        return p

    pot_w, pot_h = _next_pot(width), _next_pot(height)
    if (pot_w // 4) * (pot_h // 4) != blocks:
        # Fall back: find factor pair of blocks matching Ohana rectangular pot.
        pot_w, pot_h = canvas_for_pixel_bytes(len(pix), width, height, 16)
        # canvas_for_pixel_bytes returns pixel dims for bpp; for block codecs
        # reinterpret as block grid * 4 when needed.
        if (pot_w // 4) * (pot_h // 4) != blocks:
            # Last resort: width-major unpack of block count into pot dims.
            bw = _next_pot(width) // 4
            if bw == 0 or blocks % bw:
                raise ValueError(
                    f"cannot derive ETC1A4 pot for {width}x{height} ({len(pix)} bytes)"
                )
            bh = blocks // bw
            pot_w, pot_h = bw * 4, bh * 4
    src = Image.open(png).convert("RGBA")
    if src.size != (pot_w, pot_h):
        canvas = Image.new("RGBA", (pot_w, pot_h), (0, 0, 0, 0))
        canvas.paste(src.crop((0, 0, min(width, src.width), min(height, src.height))), (0, 0))
        src = canvas
    pixels = encode_etc1a4_pixels(src, pot_w, pot_h)
    if len(pixels) != len(pix):
        raise ValueError(f"ETC1A4 size mismatch {len(pixels)} != {len(pix)}")
    out = pixels + footer
    if len(out) != len(orig):
        raise ValueError(f"BCLIM size changed {len(orig)} -> {len(out)}")
    return out


def encode_rgb565_pixels(img: Image.Image, pot_w: int, pot_h: int) -> bytes:
    """Nintendo BCLIM fmt 3 = RGB565 (no alpha; a<16 → black)."""

    def write(r: int, g: int, b: int, a: int) -> bytes:
        if a < 16:
            r = g = b = 0
        val = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        return struct.pack("<H", val)

    return encode_tiled_pixels(img, pot_w, pot_h, write)


def png_to_bclim_rgb565_same_size(png: Path, orig_bclim: Path) -> bytes:
    """Build RGB565 (CLIM fmt 3) BCLIM matching the original file length."""
    orig = orig_bclim.read_bytes()
    pix, width, height, fmt, footer = parse_bclim(orig)
    if fmt != 3:
        raise ValueError(f"expected RGB565 fmt 3, got {fmt}")
    pot_w, pot_h = canvas_for_pixel_bytes(len(pix), width, height, 2)
    need = pot_w * pot_h * 2
    pixels = encode_rgb565_pixels(Image.open(png), pot_w, pot_h)
    if len(pixels) != need:
        raise ValueError(f"RGB565 size mismatch {len(pixels)} != {need}")
    out = pixels + footer
    if len(out) != len(orig):
        raise ValueError(f"BCLIM size changed {len(orig)} -> {len(out)}")
    return out


def png_to_bclim_same_size(png: Path, orig_bclim: Path) -> bytes:
    """Encode PNG into a same-size BCLIM using the original's pixel format."""
    _pix, _w, _h, fmt, _footer = parse_bclim(orig_bclim.read_bytes())
    if fmt == 8:
        return png_to_bclim_rgba4444_same_size(png, orig_bclim)
    if fmt == 0xB:
        return png_to_bclim_etc1a4_same_size(png, orig_bclim)
    if fmt == 1:
        return png_to_bclim_a8_same_size(png, orig_bclim)
    if fmt == 3:
        return png_to_bclim_rgb565_same_size(png, orig_bclim)
    if fmt == 0xD:
        return png_to_bclim_a4_same_size(png, orig_bclim)
    raise ValueError(f"unsupported BCLIM format {fmt:#x} for same-size encode")
