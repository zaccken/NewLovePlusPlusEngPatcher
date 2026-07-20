# New Love Plus+ — reverse engineering notes

Technical reference from localization / RE work on title `00040000000F4E00` (New Love Plus+), focused on UI text, textures, and the bottom-screen system clock confirm chrome.

Companion tooling lives in the sibling repo:

`../NewLovePlusPlusEngPatcher/`

This file is the long-form source of truth. Cursor rules under `.cursor/rules/` (mirrored in EngPatcher) summarize the same material for agents — keep them in sync when you learn something new.

---

## 0. Start here (do not restart from scratch)

Before hunting strings, re-extracting packages, or inventing a new “global text fix”:

1. Read **this file** end-to-end (especially §§5–6, 9, 11–12).
2. Skim Cursor rules: `read-docs-first`, `patch-safety`, `ghidra-mcp`, `ui-localization-method`, `nlpp-repo-workflow`, `clock-confirm-ui-localization`.
3. Reuse EngPatcher work products before regenerating them:
   - `out/clock_recheck/` — scans, header/date viz, pkg 5238 extract
   - `out/textresource/` — TRB dumps / `translations.json`
   - `assets/images/*.check/` — decoded UI masters (prefer over guessing filenames)

**Already settled (do not rediscover):**

- Clock Back/Next = NCommonIcon pkg **5238** `Com_btn_m01_b` / `Com_btn_t01_b` (pixel-matched to GPU dumps).
- Header `３ＤＳ本体時計` is **not** a contiguous string in romfs/`code.bin`/`img.bin` (any common encoding) — see §9.
- Global MakeStr hook and full `img.bin` rewrite are banned — see §11.

---

## 1. Address spaces and binaries

| Item | Value |
|------|--------|
| Title ID | `00040000000F4E00` |
| Main code | `extracted/exefs/code.bin` |
| Image archive | `extracted/romfs/img.bin` (~712 MB) |
| TextResource | `extracted/romfs/SystemData/TextResource/textresource_jpn.trb` |
| Resident strings | `…/textresource_resident_jpn.trb` (not STRI; custom `TOP` chunks) |
| Ghidra image base | `0` |
| Runtime VA | **file offset + `0x100000`** |

Pointers stored in `code.bin` are usually **runtime VAs**. Convert before seeking:

```text
file_offset = runtime_va - 0x100000
```

### Useful restore points

- `extracted/exefs/code.bin.bak_clocktext` — pre–MakeStr-hook backup (do not redeploy the abandoned global hook).

### 1.1 Ghidra MCP (`user-ghidra`)

1. Call `GetMcpTools` for schemas before invoking tools.
2. `list_open_programs` / `list_instances` + `connect_instance` if disconnected.
3. Prefer current program `code.bin` (also `/codeV2.bin` may exist). Image base **0**.

**Xrefs / pointers**

- Ghidra file address ≈ offset in `code.bin`.
- When `get_xrefs_to` on a string at file `0x006c3ea4` is empty, search LE bytes of the **runtime** pointer `0x007c3ea4` via `search_byte_patterns`.
- Ghidra string search often **misses UTF-8 Japanese** — use Python over `code.bin` / TRB instead.

**Efficient RE pattern**

- Start from ASCII anchors (`OptionAdjustTimeUIOperator`, `Lyt_Clock*`, `Pos_Com_btn_*`) → xrefs → decompile callers.
- Softkey Pos names are duplicated (`Pos_Com_Btn_m` vs `Pos_com_btn_m`) — different UI families; confirm which DAT a function uses.
- Tool schemas matter: e.g. `analyze_function_complete` wants `name`; `batch_decompile` wants `functions` (not ad-hoc keys).

**Known APIs (don’t re-derive)**

| Role | Address / name |
|------|----------------|
| TextResource pack+slot | `FUN_005c0e7c` → `FUN_0056ed00` |
| MakeStr | `FUN_005a1ec8` @ `005A1EC8` |
| DrawTextToPane | `FUN_0054b880` |
| Header pane draw | `FUN_0024842c` |
| Softkey m / o / t enable | `FUN_001d2498` / `001d1c70` / `001d2080` |

---

## 2. Text / draw pipeline (high level)

```text
TextResource TRB (STRI/STRB/INDX)
        │
        ▼
FUN_005c0e7c(dst, maxlen, pack, slot)
        │  pack = (cat << 8) | sub
        ▼
FUN_0056ed00(…, cat, sub, slot, …)  → decode NLP codebook / UTF-8 into buffer
        │
        ▼
FUN_005a1ec8  MakeStr
        │
        ▼
FUN_0054b880  DrawTextToPane  → A8 / alpha pane (often dumped as RGB=0, alpha=glyph)
```

Alternate path for softkeys: **no MakeStr** — BCLIM textures bound to layout panes (`Pos_Com_Btn_*` / `Pos_com_btn_*`).

Header-pane helper used by option/date UIs:

- `FUN_0024842c(ui, paneIndex, stringSrc)` → MakeStr + `DrawTextToPane` into pane slot.

---

## 3. TextResource details

### 3.1 Main TRB (`textresource_jpn.trb`)

Chunks (order matters): `STRI`, `CDEI`, `STRB`, `CDEB`, `CONF`, `INDX`.

| Chunk | Role |
|-------|------|
| STRI | Entry table: `stringindex`, `bytelength`, `flag` per slot |
| STRB | String payloads (NLP codebook indices or UTF-8 when `flag == 1`) |
| INDX | Hierarchy: category → subcategory → slot → STRI entry index |
| CDEI / CDEB | Codebook-related |
| CONF | Small config (`trb` size bookkeeping) |

Codebook file used by EngPatcher:

`NewLovePlusPlusEngPatcher/tools/Trb2xlsx/TrbExport/lookup.txt` (~3445 entries, 1-based indices in NLP encoding).

### 3.2 Pack / hierarchy lookup

```c
// FUN_005c0e7c
FUN_0056ed00(..., (pack & 0xff00) >> 8, pack & 0xff, slot, 1);
```

Example known IDs (from earlier RE):

| String | Hierarchy `(cat, sub, slot)` | Pack |
|--------|------------------------------|------|
| `もどる` | `(3, 0, 0)` | `0x0300` |
| `戻る` | `(18, 0, 40)` | `0x1200` slot 40 |
| `次へ` | `(131, 0, 3)` | `0x8300` slot 3 |
| Flat STRI | 3453=`戻る`, 23509=`次へ`, 157/158/159=`日`/`月`/`年` |

**INDX parse sketch (cat 1 verified):**

- Bytes `0x00..0x3FF`: `u32` offsets per category (0 = unused).
- At `cat_off`: `u32 sub_count`, then `sub_count` relative `u32` offsets.
- Sub at `cat_off + rel`: `u32 slot_count`, then `slot_count` × `u16` STRI entry indices.

**Gotcha:** `FUN_0034cd98` calls `FUN_005c0e7c(..., pack=0x107, slot from +0x84)`.  
`0x107` → `(cat=1, sub=7)` = **prefecture names**, not the clock header. That fill targets object `+0x106` in a broader date/profile UI.

### 3.3 Resident TRB

- Magic/layout starts with `TOP` sections (null-separated UTF-8 fragments).
- Contains date/time building blocks (`０時`…`２３時`, `年`/`月`/`日`, weekdays, etc.).
- Does **not** contain contiguous `３ＤＳ本体時計`.

### 3.4 Translation quirks

- EngPatcher translations: `out/textresource/translations.json`.
- Many UI strings are already EN in the Azahar LayeredFS TRB overlay; if a screen stays JP, **do not assume missing TRB** — check textures / DrawText source.
- Closest TRB string to the clock header: `ＤＳ本体の時計と同じ` (different wording; not the confirm title).
- `３ＤＳ本体時計` was **not** found as UTF-8, UTF-16LE, Shift-JIS, or NLP codebook index sequence in `code.bin`, `img.bin`, main TRB, resident TRB, or BCLYT files.

---

## 4. Image archive (`img.bin`)

### 4.1 Tools

Under `NewLovePlusPlusEngPatcher/tools/nlpp-tools/`:

| Tool | Role |
|------|------|
| `bin/ie` | Unpack package blob(s) from `img.bin` by index |
| `bin/pe` | Unpack/repack a package → `.arc` / `new_NNNN` |
| `opt/bin/png2bclim.exe` | Fallback BCLIM encode (often changes size — avoid when possible) |

These are **Python entrypoints** (invoke with `python ie …`), not Win32 native binaries.

```bash
python ie --src_img <img.bin> --img_dir <out> unpack --idx 5238
python pe <out>/5238 unpack    # → 5238_data/*.arc
python pe <out>/5238 repack    # → new_5238
```

### 4.2 Safe vs unsafe rebuild

| Method | Result |
|--------|--------|
| Same-offset **package splice** (`pack_images.splice_packages_into_img`) | Safe for LayeredFS |
| Full `Image.write()` / `--full-repack` | **Black-screen boot** — do not use |

If `pe` recompresses slightly smaller, pad the spliced blob to the original package slot length.

### 4.3 EngPatcher image map

`src/image_map.py` maps folder keys → `(package_index, arc_name)`.

Important keys for clock / softkeys:

| Key | Pkg | ARC |
|-----|-----|-----|
| `ncommonicon` | 5238 | `NCommonIcon.arc` |
| `dateeditbase01` / `02` | 4195 / 4196 | `DateEditBase01/02.arc` |
| `optionclock` | 5249 | `OptionClock.arc` |
| `myroomheader` | 5575 | `MyroomHeader.arc` |
| `syspopup` | 5259 | `SysPopup.arc` |

Pack CLI:

```bash
python src/pack_images.py --only ncommonicon --img-bin <src> --out out/new_img.bin [--deploy-azahar]
```

Prefer `assets/images/<Name>.check/` over raw trees when both exist.

---

## 5. BCLIM / imaging conversion quirks

### 5.1 Format IDs (this project)

| CLIM fmt | Meaning | Notes |
|----------|---------|--------|
| 1 | A8 | Alpha / text panes |
| 3 | RGB565 | Some Release buttons |
| 8 | RGBA4444 | Common UI labels |
| `0xB` / 11 | ETC1A4 | 16 bytes per 4×4 block; Ohana scramble |

**Azahar dump filenames** use a different “fmt” number (e.g. `_13_`). Never trust dump fmt for encode — always `parse_bclim` on the archive file.

Implementation: `EngPatcher/src/bclimutil.py`

- Morton / Z-order 8×8 tiles: `d2xy`, `gcm`
- ETC1A4: `etc1_scramble`, `encode_etc1a4_pixels` (etcpak + byte-reversed color + scramble)
- Same-size writers: `png_to_bclim_a8_same_size`, `png_to_bclim_rgba4444_same_size`, `png_to_bclim_etc1a4_same_size`

### 5.2 Hard rules for conversion

1. **Output BCLIM byte length must equal original** or DARC inject / in-game panes break (grey panels, wrong alpha).
2. `png2bclim` often expands (e.g. 4 KB → 32 KB) — EngPatcher rejects those.
3. Transparent pixels: encoder may fill RGB with `(86,86,86)` when `a==0` (matches prior png2bclim behavior).
4. ETC1A4 logical size may be smaller than compressed canvas (POT derived from payload length).
5. Alpha-only GPU dumps: RGB channels are 0; visualize via alpha before comparing.

### 5.3 Pixel-matching methodology

When identifying which BCLIM a screen uses:

1. Capture Azahar texture dump at the moment the UI is visible.
2. Decode candidate BCLIMs (correct fmt).
3. Compare with text-region MAD / glyph Jaccard (or full-frame MAD for opaque buttons).
4. **MAD = 0** against `assets/images/NCommonIcon.check/timg/…` confirmed exact masters for clock softkeys.

---

## 6. Softkey system

### 6.1 NCommonIcon (pkg 5238)

Contents:

- `NCommonIcon.arc` — button BCLIMs
- `MenuNComBtnM.dmst` — DMST state (`DMST` magic) listing `Pos_*` / `Pts_*` / `.bclan` tags

Button letter codes (decoded from usage / art):

| Code | Meaning (JP) |
|------|----------------|
| `m` | 戻る (Back) |
| `t` | 次へ (Next) |
| `k` | 決定 |
| `o` | OK |
| `re` | 再検索 |
| `tk` | 投稿 |
| `dl` / `dlr` | DL variants |
| … | `chg`, `hs`, `hz`, `pr`, `bye`, `q`, `plus`, `exp`, … |

Enable helpers (write visibility flags + poke layout Pos):

| Function | Pos | Object flag |
|----------|-----|--------------|
| `FUN_001d2498` | `Pos_Com_Btn_m` | `+0x25` / sticky `+0x38` |
| `FUN_001d1c70` | `Pos_Com_Btn_o` | `+0x28` / `+0x3b` |
| `FUN_001d2080` | `Pos_Com_btn_t` | `+0x37` / `+0x49` |

Init table builder: `FUN_001d14e0`.

### 6.2 Clock confirm softkeys (verified)

| UI | GPU dump | BCLIM | Pkg | Fmt | Size |
|----|----------|-------|-----|-----|------|
| Back `戻る` | `tex1_64x64_F305C9338867CC37_13_mip0.png` | `timg/Com_btn_m01_b.bclim` | 5238 | ETC1A4 (11) | 4136 |
| Next `次へ` | `tex1_64x64_4352FF452CC91909_13_mip0.png` | `timg/Com_btn_t01_b.bclim` | 5238 | ETC1A4 (11) | 4136 |

ON variants: `Com_btn_m01_bON`, `Com_btn_t01_bON` (dumps `42FF2BB2…`, `75A87132…`).

Exact PNG masters:

`NewLovePlusPlusEngPatcher/assets/images/NCommonIcon.check/timg/Com_btn_{m,t}01_b.png`

**Note:** Nested `NCommonIcon.check/NCommonIcon/timg/` copies are different art (poor match). DateEdit `Com_btn_m01_b` is a different style (e.g. hiragana もどる + arrow).

`OptionClockPopSetup` (`0034d214`) enables **m + o**, not m + t. The confirm screen that shows Next may use another state; textures still come from NCommonIcon `t01` when that Pos is shown.

### 6.3 Wrong softkey path (do not patch for clock)

`FUN_00249fd4` / `Tex_Bt%02d` uses `FUN_005c0e7c(..., 0x106, buttonIndex)` → hierarchy `(1,6,*)` = blood types (Ａ型/Ｂ型/…). Profile UI, not clock Back/Next.

---

## 7. Clock / option UI anchors (Ghidra)

| Symbol / item | File address | Notes |
|---------------|--------------|--------|
| `OptionAdjustTimeUIOperator` | `002d2ea0` | Class name string |
| Factory | `FUN_002d2e50` | Alloc / vtable install |
| Caller | `FUN_0039fc64` | Creates operator |
| Vtable (runtime ptr `0x0081c6a8`) | `0071c6a8` | Method slots |
| `Lyt_ClockPop_01.bclyt` | `0073d8ac` | |
| `Lyt_ClockSet_01.bclyt` | `0073d8c2` | |
| Layout desc ClockPop | `~079d2ec` | Points at lyt + handlers |
| Layout desc ClockSet | `~079d30c` | |
| `Com_lyt_rigt_hdr.bclyt` | `0073f63f` | Header chrome layout name |
| Rigt_hdr descriptor | `006c505c` | Loaded with clock UIs via `FUN_001dc494` path |
| `OptionClockPopSetup` | `0034d214` | Softkeys + may copy title buffer |
| `FUN_00350a1c` | `00350a1c` | Multi-pane `FUN_0024842c` draws |
| `Anm_ClockPop_01` / `Anm_ClockSet_01` | `00239298` / `00239b38` | |

MyroomHeader pkg **5575** contains `blyt/Com_lyt_rigt_hdr.bclyt` (tiny ~312 B shell) and `Com_pts_rigt_hdr.bclyt` — **no embedded title string**.

OptionClock pkg **5249**: Cancel/Ok + date spinner assets; **not** the square Back/Next pair.

---

## 8. Clock confirm UI — observed GPU dumps

| Role | Dump pattern | Notes |
|------|--------------|--------|
| Header | `tex1_256x32_2EC64BAE152688B3_8_mip0.png` | Alpha-only `３ＤＳ本体時計` |
| Year | `tex1_16x16_8B9E56BD535BF1C4_11_mip0.png` | Glyph `年` |
| Month | `tex1_16x16_6B2CD1FB667B8DF0_11_mip0.png` | Glyph `月` |
| Day | `tex1_16x16_63A5BD96C326E5FF_11_mip0.png` | Glyph `日` |
| Back / Next | 64×64 dumps above | NCommonIcon BCLIMs |

Dump folder:

`%AppData%\Azahar\dump\textures\00040000000F4E00\`

---

## 9. Header string — negative findings

Searched and **not found** as a contiguous payload:

- `code.bin` (UTF-8 / UTF-16 / SJIS / u16 codepoint run / NLP index run)
- `img.bin` (same)
- Main TRB flat + full INDX walk for `本体時計` / `３ＤＳ本体時計`
- Resident TRB
- BCLYT embedded strings in MyroomHeader header layouts

**Implication:** title is almost certainly **assembled or supplied at runtime** into a DrawText buffer (see `FUN_0024842c` / object `+0x106` / ClockSet-side `+0x3c` copy in `OptionClockPopSetup`). Next probe: debugger breakpoint on those functions while opening the confirm screen.

`optn_tex_optionmenu_05` (MyroomHeader, often 128×16) is a **different** menu title asset — not the 256×32 confirm header dump.

---

## 10. Emulator / LayeredFS

```text
%AppData%\Azahar\load\mods\00040000000F4E00\
  romfs\img.bin
  romfs\SystemData\TextResource\...
  exefs\code.bin
```

| Path | Use |
|------|-----|
| `…\romfs\img.bin` | Texture packages (spliced) |
| `…\romfs\SystemData\TextResource\*.trb` | Strings |
| `…\exefs\code.bin` | Code patches |
| `%AppData%\Azahar\dump\textures\00040000000F4E00\` | GPU dumps |

Custom texture replacements (`pack.json`, `use_new_hash: true`) can briefly remap dumps but caused misbinds/crashes. **OK for RE reconnaissance only** — not a shipping strategy. Prefer archive splice; keep custom/dump/async off unless deliberately testing.

When deploying with `pack_images --deploy-azahar`, prefer splicing onto the **current Azahar mod** `img.bin` if it already has other patches (e.g. CESA), not only vanilla.

Treat dump `extracted/` as mostly **read-only**; write patches through EngPatcher → LayeredFS.

---

## 11. Patch safety and abandoned approaches

### 11.1 Hard bans (never)

- Full-rebuild `img.bin` with `Image.write()` or `pack_images --full-repack` → black-screen boot.
- Redeploy `EngPatcher/src/patch_clock_text.py` global MakeStr hook as-is → crash or blank all text. Restore from `code.bin.bak_clocktext` if needed.
- BCLIM size/format changes — if encode differs from original length, keep the archive entry.
- Broad image packs without `--only` that skip-fail half of NCommonIcon; scope keys and only replace intended PNGs.
- Shipping Azahar custom-texture packs as the real localization fix.

### 11.2 Always

- Same-offset / same-length package splice (`splice_packages_into_img`).
- Backup before code hooks; test one change at a time in LayeredFS.
- Verify asset identity (MAD≈0 vs dump) before patching “similar” filenames.

### 11.3 Abandoned approaches (do not revive blindly)

1. Global MakeStr UTF-8 hook at `FUN_005a1ec8` (`005A1EC8`) — cave outside `.text` crashed; cave in `.text` blanked all text.
2. Patching SysPopup / OptionClock pill buttons for square Back/Next.
3. Assuming TRB EN for `戻る`/`次へ`/`年`/`月`/`日` updates this confirm chrome.
4. Full `img.bin` rewrite.
5. `FUN_00249fd4` blood-type softkey drawer as clock buttons.
6. Treating prefecture pack `0x107` fill as clock title.
7. Treating `optn_tex_optionmenu_05` as the 256×32 confirm header.

---

## 12. UI localization method

### 12.1 Decision tree (screen still JP despite EN TRB)

1. **GPU-dump** the visible chrome. Note W×H and whether RGB is empty (**alpha text**) vs full color (**baked control**).
2. **Opaque/colored control** (softkey, icon label): pixel-match BCLIM in `img.bin` → EngPatcher PNG → same-size splice. TRB edits will not change it.
3. **Alpha-only text pane:** runtime `DrawTextToPane` / MakeStr. Find caller via layout / `FUN_0024842c` / TextResource pack — **do not** assume the on-screen sentence exists as one TRB key.
4. **TRB already EN for the same words:** wrong path (different asset or runtime buffer). Stop re-translating those keys.

### 12.2 Verify identity before patching

- Prefer MAD≈0 against dumps over filename similarity (`OptionClock` ≠ confirm softkeys).
- Confirm package via `image_map.py` + `ie`/`pe`; confirm fmt via `bclimutil.parse_bclim` (ignore Azahar dump “fmt” numbers).
- Shared softkeys (`Com_btn_*` in pkg 5238) affect **all** screens that use them — usually desirable for EN.

### 12.3 Practical loop

1. Hit the screen in Azahar → dump textures.
2. Classify (texture vs DrawText) using §12.1.
3. Patch the smallest surface (one BCLIM set, one TRB slot, or one call site).
4. Same-size splice + LayeredFS deploy → retest.
5. Document new dead ends / wins back into this file and the matching Cursor rule.

### 12.4 Clock confirm — current status

| Piece | Kind | Status |
|-------|------|--------|
| Back / Next | ETC1A4 BCLIM `Com_btn_{m,t}01_b` @ 5238 | Identified; EN splice not verified in-game |
| Header `３ＤＳ本体時計` | Runtime alpha 256×32 | Source buffer unknown — debugger on `FUN_0024842c` / DrawText next |
| 年 / 月 / 日 | 16×16 alpha glyphs | Not flat TRB path; atlas/source still open |

---

## 13. EngPatcher scripts and Cursor rules

### 13.1 Scripts

| Script | Purpose |
|--------|---------|
| `src/pack_images.py` | PNG → BCLIM → DARC same-size → img splice |
| `src/bclimutil.py` | BCLIM parse/encode helpers |
| `src/darcutil.py` | DARC extract / same-size replace |
| `src/image_map.py` | Folder key → package index |
| `src/patch_textresource.py` | TRB dump / translate / rebuild / inplace |
| `src/patch_code.py` | code.bin patches |
| `src/patch_clock_text.py` | **Abandoned** global MakeStr experiment |
| `src/patch_cesa.py` | Boot CESA warning TEX splice |

### 13.2 Cursor rules (`.cursor/rules/`, mirrored in EngPatcher)

| Rule | Mirrors this doc | Role |
|------|------------------|------|
| `read-docs-first.mdc` | §0 | Don’t restart from scratch; read this file + rules first |
| `nlpp-repo-workflow.mdc` | §§1, 3–5, 10 | Addresses, pack/deploy, TRB, tools |
| `patch-safety.mdc` | §11 | Hard bans and LayeredFS layout |
| `ghidra-mcp.mdc` | §1.1 | Ghidra MCP, VA conversion, known APIs |
| `ui-localization-method.mdc` | §12 | Texture vs TRB vs DrawText tree + clock status |
| `clock-confirm-ui-localization.mdc` | §§6–9, 12.4 | Session findings for clock confirm |

When RE discovers something durable, update **both** this file and the relevant rule so agents don’t diverge.

---

*Last updated 2026-07-20 — aligned with Cursor rules (`read-docs-first`, `patch-safety`, `ghidra-mcp`, `ui-localization-method`). Clock confirm softkeys identified at asset level; header string source and in-game EN verify still open.*
