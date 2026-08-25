# bhumandala-kosha-data

Full-corpus build pipeline for the Kosha (Sanskrit dictionary) feature in
[Bhumandala DGE](https://github.com/Tribhuvanachar/bhumandala). Kept as a
separate repo because the full 101-dictionary corpus is ~1.9GB — over the
main app repo's 1GB budget — and is served to the app over jsDelivr instead
of being committed there.

## What's here

- `kosha_core.py` — the importer (Devanagari→SLP1, HTML-body→senses, StarDict
  `.babylon` reader, two-tier sharder).
- `dicts_config.json` — catalogue of all 101 dictionaries, with category,
  language direction, licence, source repo and path (32 CC-BY-SA Cologne,
  8 CC-BY, 60 Unclear — see the app repo's `dge/kosha_toolkit/LICENSING.md`
  for the licence posture). Each entry names the `repo` it comes from:

  | source repo | dictionaries | what it carries |
  |---|---|---|
  | `indic-dict/stardict-sanskrit` | 63 | the Cologne lexicons and the community sa→sa/hi/kn/ta koshas |
  | `indic-dict/stardict-sanskrit-kAvya` | 17 | Purāṇic Encyclopaedia, Purāṇa Index, Mahābhārata indices, Vedic Index, NCC, concordances, padapāṭhas |
  | `indic-dict/stardict-sanskrit-vyAkaraNa` | 13 | Aṣṭādhyāyī (Kāśikā, anuvṛtti, English), gaṇapāṭha, the dhātu literature, Abhyankar's grammar dictionary |
  | `sanskrit-lexicon/csl-orig` | 2 | the only two Cologne dictionaries the indic-dict mirror lacks |
  | `local-dict-zip` | 6 | compiled StarDict binaries from the project lead's phone with no upstream `.babylon`/git source: WHO Ayurveda terms, WHO NIA non-clinical draft, Huet's *Dictionnaire — Héritage du Sanskrit*, Abhidhanamanjari, amara-onto (structured Amarakosha), apte-sa (Apte's unabridged Practical Sanskrit-English Dictionary). Committed directly in this repo (`local-dict-zip/`, ~6.4MB — `.ifo`/`.idx`/`.dict.dz`/`.syn` only, not the built output) since there is no other repo to clone them from; the Action stages them with a symlink rather than a `git clone` step. One more supplied the same way, `apte-bi`, is deliberately **not** in `dicts_config.json` — see the app repo's `dge/PENDING.md` (25 Aug 2026 entry) for why.

  An entry may also carry parser knobs the source needs — `head_pick`,
  `head_strip`, `syn_drop`, `link_text`, `block_breaks`, `strip`, `json_body`,
  `no_homonym_split`. They are documented at the top of `kosha_core.py`; the
  defaults reproduce the original Cologne-era output byte-for-byte.
- `build_koshas.py` — the driver script.
- `.github/workflows/build-koshas.yml` — the GitHub Action that runs the
  build and publishes the result.
- `README_dist.md` — lands as `README.md` on the generated `dist` branch.

## Running the build

**Via GitHub Actions (recommended):** Actions tab → **Build Kosha corpus** →
**Run workflow**. Leave `only` blank to build all 101 dictionaries, or give a
comma-separated list of slugs for a smaller test run. The Action clones the
three `indic-dict` source repos, builds the corpus, and force-pushes it as a
single commit to a `dist` branch (so this repo's own history never bloats).
A full build takes roughly 10 minutes and produces ~1.9GB / ~138,000 files.

**Locally (to verify before running the Action):**
```bash
mkdir -p sources
git clone --depth 1 https://github.com/indic-dict/stardict-sanskrit.git       sources/stardict-sanskrit
git clone --depth 1 https://github.com/indic-dict/stardict-sanskrit-kAvya.git sources/stardict-sanskrit-kAvya
git clone --depth 1 https://github.com/indic-dict/stardict-sanskrit-vyAkaraNa.git sources/stardict-sanskrit-vyAkaraNa
ln -s "$(pwd)/local-dict-zip" sources/local-dict-zip   # already in this checkout, no clone needed
python build_koshas.py --sources-root sources --out ./build
python build_koshas.py --sources-root sources --out ./build --only purana-encyclopedia,kashika
```

Dictionaries whose source repo is not checked out are reported and skipped, so
a partial `sources/` still builds. The pre-multi-repo form
(`--sources path/to/stardict-sanskrit`) still works and builds that repo's 63.

## Index shape

`_index/` is sharded by a **variable-width** fold prefix. It used to be a fixed
two characters, which left `sa.json` at 16.8MB against a 1KB median — one search
on a common prefix downloaded the whole neighbourhood. Any bucket over
`BUCKET_MAX_RECORDS` is now split a character deeper, repeatedly, so bucket
names run 1 to 5 characters and the worst shard is 2.6MB (p99 531KB).

`manifest.json` carries `variable_buckets: true`. **A reader must match buckets
by prefix, not by slicing the query to `index_shard_len`** — in both directions,
since the bucket may be a prefix of the query or the query a prefix of the
bucket. `js/kosha.js` does this and is a superset of the old rule, so it reads
either corpus; an older reader cannot read this one. Ship the app change first.

Synonyms are indexed alongside headwords: a synonym record keeps the synonym as
its display form and carries the headword's fold and text (`f`/`w`) so the entry
is still found under the headword. Latin-script synonyms are skipped — a genuine
transliteration is already reachable, because the query is run through Sanscript
before folding, and most of the rest are fragments of an English gloss that
happened to follow a "|" in the source.

## What is deliberately not ingested

Kept out on purpose, so a later reader does not mistake the gap for an
oversight:

- **`stardict-sanskrit-vyAkaraNa/{vidyut,_deprecated}/**`** — machine-generated
  subanta/tiṅanta/kṛdanta/taddhitānta inflection tables. Millions of generated
  word-forms; they would dwarf the lexicon and bury real headwords in the
  lookup. They belong behind a morphology service, not in a dictionary index.
- **`stardict-sanskrit-kAvya/rAnADe-vedic-rituals`** — the same H. G. Rāṇaḍe
  Vedic-rituals lexicon already loaded as `vedic-rituals-hi` from
  `stardict-sanskrit`, differently segmented.
- **The other 42 dictionaries in `sanskrit-lexicon/csl-orig`** — already loaded
  from the indic-dict mirror, which redistributes the same Cologne data. A code
  by code diff of csl-orig's 44 against the corpus found only `pwkvn` and `krm`
  missing, and both are now ingested; there is no third gap to close.
- **`indic-dict`'s other-language repos** (`stardict-hindi`, `-kannada`,
  `-tamil`, `-pali`, `-english`) — those key on a non-Sanskrit headword and
  are a separate corpus decision, not a Sanskrit-kosha gap.

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
