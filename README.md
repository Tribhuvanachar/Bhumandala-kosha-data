# bhumandala-kosha-data

Full-corpus build pipeline for the Kosha (Sanskrit dictionary) feature in
[Bhumandala DGE](https://github.com/Tribhuvanachar/bhumandala). Kept as a
separate repo because the full 63-dictionary corpus is ~1-1.3GB — over the
main app repo's 1GB budget — and is served to the app over jsDelivr instead
of being committed there.

## What's here

- `kosha_core.py` — the importer (Devanagari→SLP1, HTML-body→senses, StarDict
  `.babylon` reader, two-tier sharder).
- `dicts_config.json` — catalogue of all 63 dictionaries in
  `indic-dict/stardict-sanskrit`, with category, language direction, licence,
  and source path (30 licence-cleared CC-BY-SA, 33 Unclear — see the app
  repo's `dge/kosha_toolkit/LICENSING.md` for the licence posture).
- `build_koshas.py` — the driver script.
- `.github/workflows/build-koshas.yml` — the GitHub Action that runs the
  build and publishes the result.
- `README_dist.md` — lands as `README.md` on the generated `dist` branch.

## Running the build

**Via GitHub Actions (recommended):** Actions tab → **Build Kosha corpus** →
**Run workflow**. Leave `only` blank to build all 63 dictionaries, or give a
comma-separated list of slugs for a smaller test run. The Action clones
`indic-dict/stardict-sanskrit`, builds the corpus, and force-pushes it as a
single commit to a `dist` branch (so this repo's own history never bloats).

**Locally (to verify before running the Action):**
```bash
git clone --depth 1 https://github.com/indic-dict/stardict-sanskrit.git sources
python build_koshas.py --sources sources --out ./build
python build_koshas.py --sources sources --out ./build --only vachaspatyam,shabdakalpadruma
```

## Serving to the app

Once the `dist` branch exists, jsDelivr serves it at:
```
https://cdn.jsdelivr.net/gh/Tribhuvanachar/bhumandala-kosha-data@dist/data/koshas
```
Point the DGE app at it by setting, before `kosha.js` loads:
```html
<script>window.KOSHA_DATA_BASE =
  "https://cdn.jsdelivr.net/gh/Tribhuvanachar/bhumandala-kosha-data@dist/data/koshas";</script>
```
