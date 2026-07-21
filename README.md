# NewLovePlusPlusEngPatcher

One-click English patcher for **New Love Plus+** (3DS), plus the finished translation assets it ships.

Translation work-in-progress lives elsewhere ([Makein/NLPPGit](https://github.com/Makein/NLPPGit), localization project). This repo holds **completed** scripts/images and a toolchain that turns an official **CIA or .3ds/.cci** dump into a playable English **CIA**.

---

## What it can do

Drop in a known dump (`.cia` or encrypted/decrypted `.3ds` / `.cci`) and it will:

1. **Verify** the dump (SHA-1) before touching anything  
2. **Decrypt** if needed (CIA or cartridge NCCH via Batch Decryptor tools)  
3. **Inject English scripts** (pre-packed `.dbin2` from finished XML)  
4. **English heroine names** — rewrite dialog tokens (`▲高嶺＊＊▲` → `Takane`, etc.) and patch UI name tables in `textresource_resident_jpn.trb` / `img.bin`  
5. **Optionally patch `code.bin`** — single-pane player-name draw so roman letters aren’t one-glyph-per-box (`--patch-code`)  
6. **Pack translated UI PNGs** from `assets/images` into `cache/new_img.bin` and inject (same-size BCLIM only; on by default — reuse cache on later runs)  
7. **Rebuild** a decrypted **CIA** for FBI / Azahar / Citra (even when the input was `.3ds`)  
8. **Clean up** the scratch work dir afterward (keeps the finished CIA; pass `--keep-work` / `--layeredfs-out` if you also want those)

| Included assets | Approx. count |
|-----------------|--------------:|
| Finished dialog scripts (XML → `.dbin2`) | 480 scripts → 1644 `.dbin2` across `NLP_01` / `NLP_02` / `script` |
| Finished UI PNGs | ~2574 |
| UI packages patched into `img.bin` (last pack) | 49 packages / ~1190 textures applied |

Title ID: `00040000000F4E00`

---

## Quick start (drag and drop)

1. Double-click **`Drop CIA Here to Patch.bat`**
2. Drop your `.cia` / `.3ds` / `.cci` on the window (or use Browse → Patch)  
   — or drag the file directly onto the `.bat`

**First run can take about 2 hours.** UI packing builds `cache/new_img.bin` from thousands of PNGs and runs exact-length zlib (zopfli) on large ARCs. Progress lines (`[convert]`, `[exact-zlib]`) mean it is still working — leave the window open. Later runs reuse the cache and finish in a few minutes. Scripts-only: `set NLPP_WITH_IMAGES=0`.

### Required dump

Only these known New Love Plus+ dumps are accepted (SHA-1):

```
a9fbd2e6d790b6cb6194f7820e1a71f597160f2b  # encrypted CIA
811d2f0f72c2a1437997256f30b18fbb2dea6cda  # decrypted CIA
6af1751f8b4f9d074311f3a7cf2b5d3c5e807cc8
d138d92fd9d522827cb9665bc2c954f1e8ba1f92  # decrypted full .3ds
6428e72eefec31d19282d2c7f0cb5082723a3206  # encrypted trim .3ds
```

Many other decrypted CIAs will fail the hash check (by design). The patcher decrypts encrypted dumps for you after verification. Use `--expect-sha1 <hash>` to require one specific dump, or `--skip-hash` to bypass (not recommended).

### Requirements

- Windows x64  
- Python 3.10+ (the drop bat finds `python`, `py -3`, or common install folders)  
- A few GB free disk (RomFS rebuild is large)  
- First run downloads/wires `3dstool` + CIA tools via `src/setup_tools.py`

If you see **Python not found**: install from [python.org](https://www.python.org/downloads/) with **Add python.exe to PATH** checked, open a **new** Command Prompt, and confirm `py -3 --version` works. Turning off Windows “App execution aliases” for `python.exe` only helps after a real install exists.

### Outputs

| Path | Description |
|------|-------------|
| `out/NewLovePlusPlus-EN.cia` | Patched **decrypted** CIA — install with FBI, or open in Azahar/Citra |
| `out/layeredfs/…` | Optional (`--layeredfs-out`); not written by the drop bat by default |
| `cache/new_img.bin` | Your packed UI `img.bin` (built from `assets/images`; reused on later runs) |
| `out/cia_work/` | Scratch only — deleted after a successful CIA unless `--keep-work` |

**LayeredFS install**

- **Luma (3DS):** copy `00040000000F4E00` to `SD:/luma/titles/` and enable *Enable game patching*  
- **Azahar / Citra:** copy that folder into the emulator’s `load/mods/` directory  

---

## Pipeline (what runs under the hood)

```
encrypted/decrypted .cia  OR  encrypted/decrypted .3ds/.cci
  → SHA-1 check
  → decrypt if needed (Batch CIA 3DS Decryptor tools)
       CIA  → decrypted CIA → content0 CXI
       .3ds → tmp.Main.ncch (CXI) [+ Manual]
       already-decrypted .3ds → 3dstool partition0/1
  → extract RomFS (3dstool)
  → inject rebuild_dbin2/*.dbin2 into script/bin/{NLP_01,NLP_02,script}/
  → name patches (plain Takane/Rinko/Nene in scripts + resident/img tables)
  → inject packed img.bin (UI, optional)
  → rebuild RomFS → CXI → CIA (makerom, decrypted)
  → delete scratch work dir (keep finished CIA)
```

CLI example (cartridge dump → English CIA):

```bash
python src/patch_cia.py --cia "C:\path\to\00040000000F4E00_v00.3ds" --out out/NewLovePlusPlus-EN.cia
```

**Heroine names**

- Finished XML under `assets/scripts` uses plain **Takane** / **Rinko** / **Nene** (not `▲高嶺＊＊▲`).  
  Player tokens like `▲主人公＊▲` are unchanged.  
- At patch time, `src/patch_names.py` also rewrites any leftover tokens inside `.dbin2` and patches the resident TRB + `img.bin` name table.  
- Standalone:  
  `python src/patch_names.py --romfs path\to\romfs`  
  `python src/patch_names.py --xml assets/scripts`  
  `python src/patch_names.py --dbin rebuild_dbin2`  
- Skip with `--skip-name-patches`. For LayeredFS without a full UI pack but with the name table: `--name-img`.

**Player name UI (`code.bin`)**

- Opt-in: `--patch-code` rewrites `SetNameCharsToPanes` / clear / backspace so the whole name draws in one pane (max still 8).  
- Standalone: `python src/patch_code.py path\to\code.bin`  
- LayeredFS installs `code.bin` next to `romfs/` (Azahar/Luma ExeFS overlay).  
- CIA builds unpack/repack ExeFS via `3dstool`.

**Encryption notes**

- Input must be the hashed encrypted dump; decryption is automatic.  
- Output is a **decrypted** CIA (`Crypto Key: None`) — correct for CFW and emulators.  
- True retail NCCH re-encryption is **not** done here; use Decrypt9WIP *CIA Encryptor (NCCH)* on a console if you specifically need that.

**UI packing (build your own `img.bin`)**

- Put translated UI PNGs under `assets/images` (same folder layout the repo already ships).  
- Drop-bat / `patch_cia.py` **packs them by default** into `cache/new_img.bin`, then injects that into the CIA. Later runs **reuse** the cache.  
- **Expect about 2 hours on the first pack** (CPU-bound zopfli on big packages like `map_layout`). A spinning `[exact-zlib]` line with rising elapsed time is normal — not a hang.  
- Only **exact same-size** BCLIM swaps are applied — oversized `png2bclim` output is skipped (avoids grey/broken panels). Upstream [nlpp-tools](https://github.com/kiwiz/nlpp-tools): *check that the new bclim is the correct size*.  
- Rebuild after editing PNGs: `set NLPP_REPACK_IMAGES=1` or `python src/patch_cia.py ... --repack-images`.  
- Scripts-only: `set NLPP_WITH_IMAGES=0` or `--no-images`.  
- Standalone pack: `python src/pack_images.py` → `cache/new_img.bin`.  
- Parallel convert: `python src/pack_images.py --workers 16` (default = CPU count, max 32). CIA path: `--image-workers N`.  
- Per-byte zopfli **fine-tune is off by default.** After binary-search, the packer uses empty-block pad to hit the exact compressed slot (images still load). Opt in with `--fine-tune` / `--image-fine-tune`.  
  - **Cost:** can add **many hours** (tens of thousands of full zopfli passes on large gap runs).  
  - **Advantage:** when zopfli lands a few bytes off the slot, fine-tune flips individual gap bytes to land an **exact** `len(zopfli) == slot` match without relying on empty-block padding — useful if a package fails empty-block alignment or you want the tightest same-algorithm fit.  
- The drop bat does **not** mutate your RomFS dump in-place.

---

## Credits

Image packing (`tools/nlpp-tools/`) uses **[kiwiz/nlpp-tools](https://github.com/kiwiz/nlpp-tools)** — thank you to **kiwiz** for `ie`, `pe`, `png2bclim`, `png2texi`, and the packing workflow this project wraps.

Other community references: [LovePlusProject/NLPPATCH](https://github.com/LovePlusProject/NLPPATCH), [NLPTextTool](https://github.com/LovePlusProject/NLPTextTool), [Makein/NLPPGit](https://github.com/Makein/NLPPGit).

An offline copy of NLPPATCH (git tree + `NLPPATCH.2017.08.15` release scripts/TRB/code) lives in `vendor/NLPPATCH/`. Deploy dialogue with `python src/deploy_nlppatch_scripts.py`.

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
  patch_names.py             heroine names (dbin2 / resident TRB / img.bin)
  patch_code.py              single-pane name draw (ExeFS code.bin)
  pack_images.py             PNG → img.bin
  darcutil.py                DARC extract / rebuild
  image_map.py               folder → img.bin package index
  patcher.py                 asset QA / staging helper
  setup_tools.py             fetch 3dstool + wire decryptor bins
  drop_zone.ps1              WinForms drop window
tools/                       cia binaries, nlpp-tools, optional clones
vendor/NLPPATCH/             offline NLPPATCH snapshot (scripts/TRB/code)
rebuild_dbin2/               finished English .dbin2 scripts (NLP_01/NLP_02/script)
cache/                       packed img.bin (new_img.bin; gitignored)
out/                         build products (gitignored)
```

Finished `.dbin2` scripts used at patch time live in `rebuild_dbin2/` (generated from `assets/scripts`).

---

## Advanced CLI

```bash
# Tool setup
python src/setup_tools.py

# Full CIA patch (scripts + UI pack → cache/new_img.bin; same as the .bat)
python src/patch_cia.py --cia "path\to\game.cia"

# Rebuild UI cache after editing assets/images
python src/patch_cia.py --cia "path\to\game.cia" --repack-images

# Scripts only (skip UI pack)
python src/patch_cia.py --cia "path\to\game.cia" --no-images

# LayeredFS only (reuses cache/new_img.bin when present)
python src/patch_cia.py --cia "path\to\game.cia" --layeredfs-only

# UI bank only
python src/pack_images.py
python src/pack_images.py --only title mail

# Skip SHA-1 (not recommended)
python src/patch_cia.py --cia "..." --skip-hash

# Name patches only (scripts / resident / img table)
python src/patch_names.py --romfs "path\to\romfs"
python src/patch_names.py --dbin rebuild_dbin2
python src/patch_names.py --xml assets/scripts

# LayeredFS with name-table img.bin but without UI texture packing
python src/patch_cia.py --cia "path\to\game.cia" --layeredfs-only --name-img --skip-hash

# LayeredFS + single-pane name code.bin patch
python src/patch_cia.py --cia "path\to\game.cia" --layeredfs-only --patch-code --skip-hash

# Patch code.bin only
python src/patch_code.py "..\New Love Plus Plus\extracted\exefs\code.bin"

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
