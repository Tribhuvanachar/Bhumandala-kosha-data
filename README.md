# DGE Kosha corpus — `dist` branch

This branch is **generated** by `.github/workflows/build-koshas.yml`. Do not edit
it by hand — every build force-pushes a fresh single commit here.

It holds the full multilingual Kosha corpus under `data/koshas/` — 93
dictionaries, 2,094,525 headwords, 2,436,991 senses — built from
[`indic-dict/stardict-sanskrit`](https://github.com/indic-dict/stardict-sanskrit),
[`-kAvya`](https://github.com/indic-dict/stardict-sanskrit-kAvya) and
[`-vyAkaraNa`](https://github.com/indic-dict/stardict-sanskrit-vyAkaraNa)
(Cologne Digital Sanskrit Dictionaries, the Purāṇic/epic encyclopaedias and
indices, the Aṣṭādhyāyī and dhātu literature, and community sources).

Served to the DGE app over jsDelivr:

```
https://cdn.jsdelivr.net/gh/<owner>/<this-repo>@dist/data/koshas
```

Licences travel with the data: each dictionary's `meta.json` and the top-level
`_index/manifest.json` record its licence (CC-BY-SA 4.0 for the Cologne set;
"Unclear (…)" for community dictionaries and for third-party book titles that
the source repos' own licences do not cover — included, with full provenance
and a per-entry `license_note`, under the project lead's non-commercial/
educational clearance).
See `KOSHA_FULL_BUILD.md` on the app side for the sourcing policy.
