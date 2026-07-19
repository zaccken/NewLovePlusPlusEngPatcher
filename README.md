# NewLovePlusPlusEngPatcher

Small toolkit for **finished** New Love Plus+ English patch assets.

Translation work-in-progress stays in [NLPPGit](https://github.com/). This repo holds completed scripts/images plus a tiny helper program to check and package them.

## Layout

```
assets/
  scripts/   # finished DBIN2 XML (from NLPP_scripts-Done)
  images/    # finished UI art (from Images-Done; png + source psd/pdn)
patcher.py   # CLI helper
out/         # build output (gitignored)
```

## Requirements

- Python 3.10+

No third-party packages.

## Usage

```bash
python patcher.py status
python patcher.py validate
python patcher.py dialogs --script a002
python patcher.py dialogs --out out/dialogs.txt
python patcher.py build --clean
```

`build` copies game-ready files only (`*.xml` scripts + `*.png` images) into `out/patch/`. Source editors files (`.psd`, `.pdn`, `.xcf`) are left in `assets/images` and skipped.

Packing those files into in-game DBIN/ARC containers still uses your existing NLPP packing tools — this program prepares a clean folder for that step.

## Workflow

1. Finish a script/image in the translation repo.
2. Move it into `assets/scripts` or `assets/images` here.
3. Run `validate` / `dialogs` to spot leftovers.
4. Run `build` when assembling a release drop.
