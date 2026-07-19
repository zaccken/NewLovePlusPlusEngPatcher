# Tools used by the CIA patcher

## Present here

| Path | Source | Role |
|------|--------|------|
| `cia/` | [3dstool](https://github.com/dnasdw/3dstool), Batch CIA 3DS Decryptor Redux | Decrypt CIA, split/rebuild NCCH/RomFS, makerom CIA |
| `nlpp-tools/` | **[kiwiz/nlpp-tools](https://github.com/kiwiz/nlpp-tools)** (original) | `img.bin` / package / BCLIM helpers (`ie`, `pe`, `png2bclim`, …) |
| `NLPTextTool/` | [LovePlusProject/NLPTextTool](https://github.com/LovePlusProject/NLPTextTool) | XML ↔ `.dbin2` (needs .NET SDK to build) |
| `NLPUnpacker/` | [LovePlusProject/NLPUnpacker](https://github.com/LovePlusProject/NLPUnpacker) | Older `img.bin` unpacker (C#) |

## Not useful from Makein/NLPPGit alone

[Makein/NLPPGit](https://github.com/Makein/NLPPGit) is the **translation repo** (XML scripts + UI art + PDFs). Releases are **LayeredFS overlays**, not a CIA rebuild toolchain. Packing instructions there point at Discord / external tools.

`nlpp-tools` originates from **[kiwiz/nlpp-tools](https://github.com/kiwiz/nlpp-tools)**. The packing stack listed by [LovePlusProject/NLPPATCH](https://github.com/LovePlusProject/NLPPATCH) also includes:

- NLPTextTool, nlpp-tools, NLPUnpacker, png2texi, trb2xlsx, nlpp-fmt

## Setup

```bash
python src/setup_tools.py
```

Copies `ctrtool` / `makerom` / `decrypt` / `seeddb` from the sibling
`New Love Plus Plus/tools/Batch-CIA-3DS-Decryptor-Redux-*` tree if needed, and
downloads `3dstool`.

## Image packing notes

- In-tree `opt/bin/darctool` is a **Linux ELF**; Windows packing uses `darcutil.py` instead.
- `png2bclim.exe` often expands compressed BCLIM formats (size grows) — DARC rebuild handles that.
- `pack_images.py` does selective `img.bin` rewrite (only patched package indices).
