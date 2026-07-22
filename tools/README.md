# Tools used by the CIA patcher

## Present here

| Path | Source | Role |
|------|--------|------|
| `cia/` | [3dstool](https://github.com/dnasdw/3dstool), [Project_CTR](https://github.com/3DSGuy/Project_CTR), vendored decrypt | Decrypt CIA, split/rebuild NCCH/RomFS, makerom CIA |
| `Batch-CIA-3DS-Decryptor-Redux/` | [davidmorom](https://github.com/davidmorom) / [xxmichibxx](https://github.com/xxmichibxx/Batch-CIA-3DS-Decryptor-Redux) | Vendored `decrypt.exe` + `CREDITS.md` |
| `nlpp-tools/` | **[kiwiz/nlpp-tools](https://github.com/kiwiz/nlpp-tools)** (vendored) | `img.bin` / package / BCLIM helpers (`ie`, `pe`, `png2bclim`, …) |
| `mdcutil.py` | EngPatcher | SMS maildic MDC pack / unpack |
| `Trb2xlsx/` | [deaknaew/Trb2xlsx](https://github.com/deaknaew/Trb2xlsx) | `lookup.txt` codebook for `patch_textresource.py` |
| `NLPTextTool/` | [LovePlusProject/NLPTextTool](https://github.com/LovePlusProject/NLPTextTool) | XML ↔ `.dbin2` (needs .NET SDK to build) |
| `NLPUnpacker/` | [LovePlusProject/NLPUnpacker](https://github.com/LovePlusProject/NLPUnpacker) | Older `img.bin` unpacker (C#) |
| `../assets/fonts/MPLUS1p-Regular.ttf` | M PLUS 1p (SIL OFL) | UI glyph renders for deploy scripts |

Full credit list: see root [`README.md`](../README.md#credits).

## Setup

```bash
python src/setup_tools.py
```

Auto-fetches `3dstool`, `ctrtool`, `makerom`, `seeddb.bin`, and copies vendored `decrypt.exe` into `tools/cia/`.

## Notes on Makein/NLPPGit

[Makein/NLPPGit](https://github.com/Makein/NLPPGit) is a **translation asset** repo (XML scripts + UI art). Releases are LayeredFS overlays, not a CIA rebuild toolchain.

`nlpp-tools` originates from **[kiwiz/nlpp-tools](https://github.com/kiwiz/nlpp-tools)**. The packing stack listed by [LovePlusProject/NLPPATCH](https://github.com/LovePlusProject/NLPPATCH) also includes NLPTextTool, nlpp-tools, NLPUnpacker, png2texi, trb2xlsx, nlpp-fmt.

## Image packing notes

- In-tree `opt/bin/darctool` is a **Linux ELF**; Windows packing uses `darcutil.py` instead.
- `png2bclim.exe` often expands compressed BCLIM formats (size grows) — DARC rebuild handles that.
- `pack_images.py` does selective `img.bin` rewrite (only patched package indices).
