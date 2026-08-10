# DGE Kosha corpus — `dist` branch

This branch is **generated** by `.github/workflows/build-koshas.yml`. Do not edit
it by hand — every build force-pushes a fresh single commit here.

It holds the full multilingual Kosha corpus under `data/koshas/`, built from
[`indic-dict/stardict-sanskrit`](https://github.com/indic-dict/stardict-sanskrit)
(Cologne Digital Sanskrit Dictionaries and community sources).

Served to the DGE app over jsDelivr:

```
https://cdn.jsdelivr.net/gh/<owner>/<this-repo>@dist/data/koshas
```

Licences travel with the data: each dictionary's `meta.json` and the top-level
`_index/manifest.json` record its licence (CC-BY-SA 4.0 for the Cologne set;
"Unclear (no LICENSE.xml)" for community dictionaries included, with full
provenance, under the project lead's non-commercial/educational clearance).
See `KOSHA_FULL_BUILD.md` on the app side for the sourcing policy.
