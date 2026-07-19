# NewLovePlusPlusEngPatcher

One-click English patcher for **New Love Plus+** (3DS), plus the finished translation assets it ships.

Translation work-in-progress lives elsewhere ([Makein/NLPPGit](https://github.com/Makein/NLPPGit), localization project). This repo holds **completed** scripts/images and a toolchain that turns an official CIA dump into a playable English build.

---

## What it can do

Drop in the correct encrypted CIA and it will:

1. **Verify** the dump (SHA-1) before touching anything  
2. **Decrypt** the CIA if needed  
3. **Inject English scripts** (pre-packed `.dbin2` from finished XML)  
4. **Inject English UI art** into `romfs/img.bin` (when a packed image bank is available)  
5. **Rebuild** a decrypted CIA for FBI / Azahar / Citra  
6. **Also emit** a Luma/Azahar **LayeredFS** overlay (same style as community NLPPATCH releases)

| Included assets | Approx. count |
|-----------------|--------------:|
| Finished dialog scripts (XML → `.dbin2`) | 480 scripts → 1644 `.dbin2` across `NLP_01` / `NLP_02` / `script` |
| Finished UI PNGs | ~2574 |
| UI packages patched into `img.bin` (last pack) | 49 packages / ~1190 textures applied |

Title ID: `00040000000F4E00`

---

## Quick start (drag and drop)

1. Double-click **`Drop CIA Here to Patch.bat`**
2. Drop your `.cia` on the window (or use Browse → Patch)  
   — or drag the `.cia` directly onto the `.bat`

### Required dump

Only this **encrypted** New Love Plus+ CIA is accepted:

```
SHA-1: a9fbd2e6d790b6cb6194f7820e1a71f597160f2b
```

Decrypted CIAs will fail the hash check (by design). The patcher decrypts for you after verification.

### Requirements

- Windows x64  
- Python 3.10+ on `PATH`  
- A few GB free disk (RomFS rebuild is large)  
- First run downloads/wires `3dstool` + CIA tools via `src/setup_tools.py`

### Outputs

| Path | Description |
|------|-------------|
| `out/NewLovePlusPlus-EN.cia` | Patched **decrypted** CIA — install with FBI, or open in Azahar/Citra |
| `out/layeredfs/00040000000F4E00/` | LayeredFS drop: `romfs/script/bin/...` + `romfs/img.bin` |
| `out/new_img.bin` | Packed English UI bank (reused on later runs) |
| `out/img_work/image_pack_report.txt` | Per-texture pack log |

**LayeredFS install**

- **Luma (3DS):** copy `00040000000F4E00` to `SD:/luma/titles/` and enable *Enable game patching*  
- **Azahar / Citra:** copy that folder into the emulator’s `load/mods/` directory  

---

## Pipeline (what runs under the hood)

```
encrypted CIA
  → SHA-1 check
  → decrypt (Batch CIA 3DS Decryptor tools)
  → extract NCCH / RomFS (ctrtool + 3dstool)
  → inject rebuild_dbin2/*.dbin2 into script/bin/{NLP_01,NLP_02,script}/
  → inject packed img.bin (UI)
  → rebuild RomFS → CXI → CIA (makerom, decrypted)
  → also write LayeredFS overlay
```

**Encryption notes**

- Input must be the hashed encrypted dump; decryption is automatic.  
- Output is a **decrypted** CIA (`Crypto Key: None`) — correct for CFW and emulators.  
- True retail NCCH re-encryption is **not** done here; use Decrypt9WIP *CIA Encryptor (NCCH)* on a console if you specifically need that.

**UI packing notes**

- PNGs are converted with `png2bclim`, then DARCs are rebuilt (`src/darcutil.py` — Windows-friendly; upstream `darctool` in nlpp-tools is Linux ELF).  
- Packages go back into `img.bin` via [LovePlusProject/nlpp-tools](https://github.com/LovePlusProject/nlpp-tools) (`ie` / `pe`).  
- Some BCLIM formats cannot round-trip; those textures are **skipped** and the Japanese original is kept. See the image pack report.

Community packing stack (reference): [NLPPATCH](https://github.com/LovePlusProject/NLPPATCH), [NLPTextTool](https://github.com/LovePlusProject/NLPTextTool), [nlpp-tools](https://github.com/LovePlusProject/nlpp-tools).

---

## Layout

```
Drop CIA Here to Patch.bat   ← only user-facing entry point
README.md
assets/
  scripts/                   finished DBIN2 XML
  images/                    finished UI PNGs (+ editor sources)
src/
  patch_cia.py               CIA decrypt → inject → rebuild
  pack_images.py             PNG → img.bin
  darcutil.py                DARC extract / rebuild
  image_map.py               folder → img.bin package index
  patcher.py                 asset QA / staging helper
  setup_tools.py             fetch 3dstool + wire decryptor bins
  drop_zone.ps1              WinForms drop window
tools/                       cia binaries, nlpp-tools, optional clones
out/                         build products (gitignored)
```

Finished `.dbin2` scripts used at patch time live next to this repo at `../rebuild_dbin2/` (generated from `assets/scripts`).

---

## Advanced CLI

```bash
# Tool setup
python src/setup_tools.py

# Full CIA patch (same as the .bat)
python src/patch_cia.py --cia "path\to\game.cia" --with-images

# LayeredFS only (no CIA rebuild)
python src/patch_cia.py --cia "path\to\game.cia" --layeredfs-only --with-images --reuse-packed-img

# UI bank only
python src/pack_images.py
python src/pack_images.py --only title mail

# Skip SHA-1 (not recommended)
python src/patch_cia.py --cia "..." --skip-hash

# Asset helpers
python src/patcher.py status
python src/patcher.py validate
python src/patcher.py dialogs --script a002
python src/patcher.py build --clean
```

`patcher.py build` only stages loose `assets/` → `out/patch/`. CIA / LayeredFS work is `Drop CIA Here to Patch.bat` / `src/patch_cia.py`.

---

## What this is not

- A full 100% translation of every line and texture (skipped UI formats stay Japanese).  
- A dump of the game — you must supply your own matching CIA.  
- An on-console retail re-encryptor.
