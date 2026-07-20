# NewLovePlusPlusEngPatcher

One-click English patcher for **New Love Plus+** (3DS), plus the finished translation assets it ships.

Translation work-in-progress lives elsewhere ([Makein/NLPPGit](https://github.com/Makein/NLPPGit), localization project). This repo holds **completed** scripts/images and a toolchain that turns an official CIA dump into a playable English build.

---

## What it can do

Drop in the correct encrypted CIA and it will:

1. **Verify** the dump (SHA-1) before touching anything  
2. **Decrypt** the CIA if needed  
3. **Inject English scripts** (pre-packed `.dbin2` from finished XML)  
4. **English heroine names** — rewrite dialog tokens (`▲高嶺＊＊▲` → `Takane`, etc.) and patch UI name tables in `textresource_resident_jpn.trb` / `img.bin`  
5. **Optionally patch `code.bin`** — single-pane player-name draw so roman letters aren’t one-glyph-per-box (`--patch-code`)  
6. **Optionally inject English UI art** into `romfs/img.bin` (same-size BCLIM swaps only; off by default)  
7. **Rebuild** a decrypted CIA for FBI / Azahar / Citra  
8. **Also emit** a Luma/Azahar **LayeredFS** overlay (same style as community NLPPATCH releases)

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
- Python 3.10+ (the drop bat finds `python`, `py -3`, or common install folders)  
- A few GB free disk (RomFS rebuild is large)  
- First run downloads/wires `3dstool` + CIA tools via `src/setup_tools.py`

If you see **Python not found**: install from [python.org](https://www.python.org/downloads/) with **Add python.exe to PATH** checked, open a **new** Command Prompt, and confirm `py -3 --version` works. Turning off Windows “App execution aliases” for `python.exe` only helps after a real install exists.

### Outputs

| Path | Description |
|------|-------------|
| `out/NewLovePlusPlus-EN.cia` | Patched **decrypted** CIA — install with FBI, or open in Azahar/Citra |
| `out/layeredfs/00040000000F4E00/` | LayeredFS drop: scripts + resident name TRB (+ `img.bin` with `--with-images` / `--name-img`) |
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
  → name patches (plain Takane/Rinko/Nene in scripts + resident/img tables)
  → inject packed img.bin (UI, optional)
  → rebuild RomFS → CXI → CIA (makerom, decrypted)
  → also write LayeredFS overlay
```

**Heroine names**

- Finished XML under `assets/scripts` uses plain **Takane** / **Rinko** / **Nene** (not `▲高嶺＊＊▲`).  
  Player tokens like `▲主人公＊▲` are unchanged.  
- At patch time, `src/patch_names.py` also rewrites any leftover tokens inside `.dbin2` and patches the resident TRB + `img.bin` name table.  
- Standalone:  
  `python src/patch_names.py --romfs path\to\romfs`  
  `python src/patch_names.py --xml assets/scripts`  
  `python src/patch_names.py --dbin ../rebuild_dbin2`  
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

**UI packing notes**

- **Default patch is scripts-only.** `png2bclim` often changes BCLIM size/format and breaks UI alpha (solid grey panels). Upstream [nlpp-tools](https://github.com/kiwiz/nlpp-tools) documents the same: *check that the new bclim is the correct size*.  
- The drop bat does **not** mutate your RomFS dump in-place (an earlier in-place run had overwritten `img.bin` with a bad pack).  
- To enable UI packing later: `NLPP_WITH_IMAGES=1` or `--with-images` — only **exact same-size** BCLIM swaps are kept.  
- Standalone: `python src/pack_images.py` (writes `out/new_img.bin`).

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

# Name patches only (scripts / resident / img table)
python src/patch_names.py --romfs "path\to\romfs"
python src/patch_names.py --dbin ../rebuild_dbin2
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
