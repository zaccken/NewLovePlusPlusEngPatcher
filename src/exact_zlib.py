"""Exact-length zlib helpers for NLPP img.bin package elements.

Game slots require ``unused_data == 0`` and exact compressed length — trailing
NUL padding after a short zlib stream soft-locks UI. Prefer zopfli when it
undershoots, then empty-block pad / gap-salt as needed.
"""
from __future__ import annotations

import os
import struct
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import zopfli.zlib as zopfli_zlib
except ImportError:  # pragma: no cover
    zopfli_zlib = None  # type: ignore


def interfile_zero_gaps(data: bytes, min_len: int = 4) -> list[tuple[int, int]]:
    """Return (size, offset) zero pads between DARC files (fallback: raw zero runs)."""
    try:
        from darcutil import DarcArchive

        darc = DarcArchive(data)
        spans = sorted((e.offset, e.offset + e.length) for e in darc.files)
        gaps: list[tuple[int, int]] = []
        for (_a0, a1), (b0, _b1) in zip(spans, spans[1:]):
            if b0 > a1:
                gaps.append((a1, b0))
        if spans and spans[-1][1] < len(data):
            gaps.append((spans[-1][1], len(data)))
        out: list[tuple[int, int]] = []
        for g0, g1 in gaps:
            chunk = data[g0:g1]
            if len(chunk) >= min_len and chunk == b"\x00" * len(chunk):
                out.append((g1 - g0, g0))
        out.sort(reverse=True)
        if out:
            return out
    except Exception:
        pass

    runs: list[tuple[int, int]] = []
    i = 0
    n = len(data)
    while i < n:
        if data[i] != 0:
            i += 1
            continue
        j = i
        while j < n and data[j] == 0:
            j += 1
        if j - i >= min_len:
            runs.append((j - i, i))
        i = j
    runs.sort(reverse=True)
    return runs


def apply_gap_pad(data: bytes, n_bytes: int, pad_rng: bytes) -> bytes:
    if n_bytes <= 0 or not pad_rng:
        return data
    runs = interfile_zero_gaps(data)
    t = bytearray(data)
    left = n_bytes
    off = 0
    for sz, po in runs:
        take = min(left, sz)
        if take:
            t[po : po + take] = pad_rng[off : off + take]
        left -= take
        off += sz
        if left <= 0:
            break
    return bytes(t)


def compress_exact_empty_blocks(
    data: bytes,
    exact_len: int,
    *,
    thorough: bool = False,
) -> bytes | None:
    """Build a zlib stream of exactly exact_len via sync-flush + empty stored blocks.

    Fast path (default): zlib levels 0–9 only.
    ``thorough=True`` also sweeps memLevel/strategy (much slower on large ARCs).
    """
    adler = struct.pack(">I", zlib.adler32(data) & 0xFFFFFFFF)
    strategies = (
        zlib.Z_DEFAULT_STRATEGY,
        zlib.Z_FILTERED,
        zlib.Z_HUFFMAN_ONLY,
        zlib.Z_RLE,
        zlib.Z_FIXED,
    )
    hdrs = (b"\x78\x9c", b"\x78\xda", b"\x78\x5e", b"\x78\x01")

    def try_body(body: bytes) -> bytes | None:
        for hdr in hdrs:
            remain = exact_len - len(hdr) - 4 - len(body)
            if remain < 5 or remain % 5 != 0:
                continue
            n_empty = remain // 5
            out = (
                hdr
                + body
                + b"\x00\x00\x00\xff\xff" * (n_empty - 1)
                + b"\x01\x00\x00\xff\xff"
                + adler
            )
            if len(out) != exact_len:
                continue
            d = zlib.decompressobj()
            try:
                got = d.decompress(out)
            except zlib.error:
                continue
            if got == data and not d.unused_data and d.eof:
                return out
        return None

    for level in range(10):
        co = zlib.compressobj(level, wbits=-15)
        hit = try_body(co.compress(data) + co.flush(zlib.Z_SYNC_FLUSH))
        if hit is not None:
            return hit
    if not thorough:
        return None
    for level in range(10):
        for mem in range(1, 10):
            for strat in strategies:
                try:
                    co = zlib.compressobj(level, zlib.DEFLATED, -15, mem, strat)
                    hit = try_body(co.compress(data) + co.flush(zlib.Z_SYNC_FLUSH))
                except zlib.error:
                    continue
                if hit is not None:
                    return hit
    return None


def _empty_block_candidates(cap: int, preferred_pad: int | None) -> list[int]:
    """Prefer zopfli binary-search pad, then coarse scan — avoid O(cap) full sweeps."""
    ordered: list[int] = []
    seen: set[int] = set()

    def add(p: int) -> None:
        if 0 <= p <= cap and p not in seen:
            seen.add(p)
            ordered.append(p)

    if preferred_pad is not None:
        base = max(0, min(cap, preferred_pad))
        for d in range(0, 96):
            add(base - d)
            add(base + d)
    add(0)
    step = max(1, cap // 80) if cap else 1
    for p in range(cap, -1, -step):
        add(p)
    # Tiny gap budgets can afford a full sweep; large ones cannot.
    if cap and cap <= 512:
        for p in range(cap, -1, -1):
            add(p)
    return ordered


def compress_exact_with_gap_tune(
    data: bytes,
    exact_len: int,
    *,
    preferred_pad: int | None = None,
) -> tuple[bytes, bytes]:
    runs = interfile_zero_gaps(data)
    cap = sum(sz for sz, _ in runs)
    pad_rng = os.urandom(cap) if cap else b""

    _zlib_progress("empty-block try (no gap pad, fast)…")
    slot = compress_exact_empty_blocks(data, exact_len, thorough=False)
    if slot is not None:
        _zlib_progress(f"empty-block hit (no pad) slot={exact_len}", newline=True)
        return data, slot

    candidates = _empty_block_candidates(cap, preferred_pad)
    total = len(candidates)
    t0 = time.monotonic()
    _zlib_progress(
        f"empty-block scan {total} pads "
        f"(preferred={preferred_pad} cap={cap}; fast path)…",
        newline=True,
    )

    for i, n in enumerate(candidates, 1):
        elapsed = time.monotonic() - t0
        frac = i / max(1, total)
        width = 24
        filled = int(width * frac)
        bar = "#" * filled + "-" * (width - filled)
        _zlib_progress(
            f"empty-block [{bar}] {i}/{total} pad={n}/{cap} "
            f"{_format_secs(elapsed)} slot={exact_len}"
        )
        cand = apply_gap_pad(data, n, pad_rng) if cap else data
        slot = compress_exact_empty_blocks(cand, exact_len, thorough=False)
        if slot is not None:
            _zlib_progress(
                f"empty-block hit pad={n} slot={exact_len}", newline=True
            )
            return cand, slot

    # Few thorough retries around the preferred pad only (slow nested zlib).
    thorough_pads: list[int] = []
    if preferred_pad is not None:
        base = max(0, min(cap, preferred_pad))
        thorough_pads = [base + d for d in range(-16, 17) if 0 <= base + d <= cap]
    thorough_pads = list(dict.fromkeys([0, *thorough_pads]))
    _zlib_progress(
        f"empty-block thorough retry ({len(thorough_pads)} pads)…", newline=True
    )
    for i, n in enumerate(thorough_pads, 1):
        _zlib_progress(
            f"empty-block thorough {i}/{len(thorough_pads)} pad={n} slot={exact_len}"
        )
        cand = apply_gap_pad(data, n, pad_rng) if (cap and n) else data
        slot = compress_exact_empty_blocks(cand, exact_len, thorough=True)
        if slot is not None:
            _zlib_progress(
                f"empty-block hit (thorough) pad={n} slot={exact_len}",
                newline=True,
            )
            return cand, slot

    print(flush=True)
    raise RuntimeError(
        f"could not build exact zlib stream (len={len(data)} slot={exact_len})"
    )


def _zlib_progress(msg: str, *, newline: bool = False) -> None:
    print(f"\r  [exact-zlib] {msg}".ljust(96), end="" if not newline else "\n", flush=True)


def _format_secs(sec: float) -> str:
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec}s"
    return f"{sec // 60}m{sec % 60:02d}s"


# Seconds per MB from the last finished zopfli call (calibrates to this machine).
_ZOPFLI_SEC_PER_MB: float | None = None


def _zopfli_compress(data: bytes, *, label: str) -> bytes:
    """Run zopfli.compress with a live elapsed/heartbeat bar (API has no %)."""
    global _ZOPFLI_SEC_PER_MB
    if zopfli_zlib is None:
        raise RuntimeError("zopfli not installed")

    mb = max(0.05, len(data) / (1024 * 1024))
    # First call: no machine calibration yet — show elapsed only.
    # Later calls: ETA = size_MB × measured sec/MB from prior compress on this run.
    eta = (mb * _ZOPFLI_SEC_PER_MB) if _ZOPFLI_SEC_PER_MB else None
    spin = "|/-\\"
    result: list[bytes] = []
    err: list[BaseException] = []

    def _run() -> None:
        try:
            result.append(zopfli_zlib.compress(data))
        except BaseException as exc:  # noqa: BLE001 — surface to caller
            err.append(exc)

    t0 = time.monotonic()
    th = threading.Thread(target=_run, name="zopfli-compress", daemon=True)
    th.start()
    i = 0
    while th.is_alive():
        elapsed = time.monotonic() - t0
        width = 24
        if eta and eta > 0:
            frac = min(0.95, elapsed / eta)
            filled = int(width * frac)
            bar = "#" * filled + "-" * (width - filled)
            eta_txt = f" ~{_format_secs(eta)} left-ish"
        else:
            # Pulse a 3-cell block so it still looks alive with no ETA.
            pos = i % (width - 2)
            bar = "-" * pos + "###" + "-" * max(0, width - pos - 3)
            bar = bar[:width]
            eta_txt = " (calibrating speed…)"
        _zlib_progress(
            f"{label} {spin[i % 4]} [{bar}] {_format_secs(elapsed)} "
            f"{mb:.1f}MB{eta_txt}"
        )
        i += 1
        th.join(timeout=0.25)
    th.join()
    if err:
        print(flush=True)
        raise err[0]
    out = result[0]
    elapsed = time.monotonic() - t0
    # EMA so one weird pass doesn't lock the ETA forever.
    sample = elapsed / mb
    if _ZOPFLI_SEC_PER_MB is None:
        _ZOPFLI_SEC_PER_MB = sample
    else:
        _ZOPFLI_SEC_PER_MB = 0.6 * _ZOPFLI_SEC_PER_MB + 0.4 * sample
    _zlib_progress(
        f"{label} done [{ '#' * 24 }] {_format_secs(elapsed)} → {len(out)} bytes "
        f"({_ZOPFLI_SEC_PER_MB:.1f}s/MB)",
        newline=True,
    )
    return out


def _force_zero_gaps(data: bytes) -> bytes:
    """Ensure DARC inter-file pads are raw zeros (undo prior urandom gap salt)."""
    t = bytearray(data)
    for sz, po in interfile_zero_gaps(data, min_len=1):
        t[po : po + sz] = b"\x00" * sz
    return bytes(t)


def _bounded_near_miss_tune(
    data: bytes,
    target: int,
    preferred_pad: int,
    rng: bytes,
    cap: int,
    *,
    max_tries: int = 256,
    workers: int = 8,
) -> tuple[bytes, bytes] | None:
    """Close a small undershoot (e.g. 42515→42517) via parallel single-byte flips."""
    if zopfli_zlib is None or cap <= 0:
        return None
    base = apply_gap_pad(data, preferred_pad, rng)
    z0 = zopfli_zlib.compress(base)
    if len(z0) == target:
        return base, z0
    if len(z0) > target:
        return None
    deficit = target - len(z0)

    # Independent candidates: each flips one gap byte on a copy of ``base``.
    candidates: list[bytes] = []
    for sz, po in interfile_zero_gaps(base):
        for i in range(sz):
            if base[po + i] != 0:
                continue
            t = bytearray(base)
            t[po + i] = rng[(po + i) % len(rng)] if rng else (1 + (len(candidates) % 254))
            candidates.append(bytes(t))
            if len(candidates) >= max_tries:
                break
        if len(candidates) >= max_tries:
            break
    if not candidates:
        return None

    workers = max(1, min(workers, len(candidates)))
    _zlib_progress(
        f"near-miss +{deficit}B: {len(candidates)} trials, {workers} workers…",
        newline=True,
    )
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(zopfli_zlib.compress, cand): cand for cand in candidates
        }
        for fut in as_completed(futures):
            cand = futures[fut]
            z = fut.result()
            done += 1
            if done == 1 or done % 4 == 0 or len(z) == target:
                _zlib_progress(
                    f"near-miss [{done}/{len(candidates)}] zopfli={len(z)} "
                    f"slot={target}"
                )
            if len(z) == target:
                # Cancel the rest — best-effort.
                for other in futures:
                    other.cancel()
                _zlib_progress(
                    f"near-miss hit at trial {done}/{len(candidates)}",
                    newline=True,
                )
                return cand, z
    _zlib_progress(
        f"near-miss miss ({len(candidates)} trials, still short)",
        newline=True,
    )
    return None


def compress_exact_zopfli(
    data: bytes,
    target: int,
    *,
    fine_tune: bool = False,
) -> tuple[bytes, bytes]:
    if zopfli_zlib is None:
        _zlib_progress("gap-tune (no zopfli)…", newline=True)
        return compress_exact_with_gap_tune(data, target)

    z_blob = _zopfli_compress(
        data, label=f"zopfli pass 1 (dec={len(data)} slot={target})"
    )
    z0 = len(z_blob)
    if z0 > target:
        _zlib_progress(f"zopfli={z0} > slot={target}; gap-tune…", newline=True)
        return compress_exact_with_gap_tune(data, target)
    if z0 == target:
        _zlib_progress(f"exact hit zopfli={z0}", newline=True)
        return data, z_blob

    runs = interfile_zero_gaps(data, min_len=8)
    cap = sum(sz for sz, _ in runs)
    rng = os.urandom(cap) if cap else b""
    lo, hi = 0, cap
    steps = 0
    est = max(1, (cap.bit_length() + 2) if cap else 1)
    best_under_pad: int | None = None
    best_under_len = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = apply_gap_pad(data, mid, rng) if cap else data
        steps += 1
        z = _zopfli_compress(
            cand,
            label=f"binary-search {steps}/~{est} pad={mid}/{cap} slot={target}",
        )
        if len(z) == target:
            return cand, z
        if len(z) < target:
            if len(z) > best_under_len:
                best_under_len = len(z)
                best_under_pad = mid
            lo = mid + 1
        else:
            hi = mid - 1

    preferred = best_under_pad if best_under_pad is not None else max(hi, 0)

    # Auto near-miss: a few bytes under the slot (e.g. 42515 vs 42517). Much
    # cheaper than full fine-tune; empty-block often cannot close a 1–4B gap.
    if (
        best_under_len >= 0
        and 0 < (target - best_under_len) <= 64
        and cap > 0
    ):
        hit = _bounded_near_miss_tune(
            data, target, preferred, rng, cap, max_tries=512
        )
        if hit is not None:
            return hit

    # Opt-in full per-byte fine-tune (hours on large gap runs).
    if fine_tune and len(data) <= 200_000:
        base_n = preferred
        t = bytearray(apply_gap_pad(data, base_n, rng) if cap else data)
        runs2 = interfile_zero_gaps(bytes(t))
        fine = 0
        fine_cap = sum(sz for sz, _ in runs2) or 1
        _zlib_progress(
            f"fine-tune enabled ({fine_cap} gap bytes; slow)…", newline=True
        )
        for sz, po in runs2:
            for i in range(sz):
                if t[po + i] != 0:
                    continue
                t[po + i] = rng[po % len(rng)] if rng else 1
                fine += 1
                z = _zopfli_compress(
                    bytes(t),
                    label=f"fine-tune {fine}/{fine_cap} slot={target}",
                )
                if len(z) == target:
                    return bytes(t), z
                if len(z) > target:
                    t[po + i] = 0
    elif fine_tune and len(data) > 200_000:
        _zlib_progress(
            f"fine-tune skipped (ARC {len(data)} bytes > 200KB)…", newline=True
        )

    _zlib_progress(
        f"falling back to empty-block pad (preferred_pad={preferred}"
        f"{f', best_zopfli={best_under_len}' if best_under_len >= 0 else ''})…",
        newline=True,
    )
    try:
        return compress_exact_with_gap_tune(
            data, target, preferred_pad=preferred
        )
    except RuntimeError:
        pass

    # Second chance: clear gap salt, then empty-block (large-ARC playbook).
    zeroed = _force_zero_gaps(data)
    _zlib_progress("empty-block retry on zeroed DARC gaps…", newline=True)
    try:
        return compress_exact_with_gap_tune(zeroed, target, preferred_pad=0)
    except RuntimeError:
        pass

    # New random salt + short near-miss from pad=0 / preferred.
    if cap > 0 and best_under_len >= 0 and (target - best_under_len) <= 64:
        rng2 = os.urandom(cap)
        for pad in (preferred, 0, max(0, preferred - 1), preferred + 1):
            if pad > cap:
                continue
            hit = _bounded_near_miss_tune(
                data, target, pad, rng2, cap, max_tries=256
            )
            if hit is not None:
                return hit

    raise RuntimeError(
        f"could not build exact zlib stream (len={len(data)} slot={target})"
    )


def compress_to_exact_slot(
    data: bytes,
    exact_len: int,
    *,
    fine_tune: bool = False,
) -> bytes:
    """Return a zlib stream of length exact_len that decompresses to data (or tuned)."""
    tuned, slot = compress_exact_zopfli(data, exact_len, fine_tune=fine_tune)
    d = zlib.decompressobj()
    got = d.decompress(slot)
    if got != tuned or d.unused_data or not d.eof:
        raise RuntimeError("exact zlib verify failed")
    if len(slot) != exact_len:
        raise RuntimeError(f"exact zlib length {len(slot)} != {exact_len}")
    _zlib_progress(f"done slot={exact_len}", newline=True)
    return slot
