#!/usr/bin/env python3
# =============================================================================
# DGE Kosha — FULL corpus build driver.
#
# Clones nothing itself; it expects the indic-dict source tree to already be on
# disk (the GitHub Action clones it; locally you can clone it yourself). It then
# runs the shared importer (kosha_core.run_import) over every dictionary listed
# in dicts_config.json and writes the two-tier sharded DGE tree under <out>.
#
#   python build_koshas.py --sources /path/to/stardict-sanskrit \
#                          --out . [--only slug1,slug2] [--raw]
#
# Licence handling: dicts_config.json carries a best-effort licence, but the
# build re-checks for a LICENSE.xml next to each .babylon and, if present,
# stamps the dictionary as CC-BY-SA 4.0 (Cologne) — so the manifest always
# reflects what is actually in the source repo at build time, not a stale guess.
# "No LICENSE.xml" stays flagged Unclear and is included only because the
# project lead has cleared this build for non-commercial/educational use with
# full provenance stamped into every entry.
# =============================================================================
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kosha_core as K


def load_config():
    with open(os.path.join(HERE, 'dicts_config.json'), encoding='utf-8') as f:
        return json.load(f)


def refresh_licences(dicts, sources_root):
    """Upgrade-only licence check: if a LICENSE.xml sits next to the .babylon,
    mark the dict cleared. Never DOWNGRADE a curated CC licence — some Cologne
    redistributions (mw-cologne, apte-english-sanskrit-cologne) ship without the
    XML in the indic-dict mirror yet are CC-BY-SA 4.0 upstream, and the config
    records that."""
    for d in dicts:
        folder = os.path.dirname(os.path.join(sources_root, d['path']))
        has_lic = os.path.exists(os.path.join(folder, 'LICENSE.xml'))
        cur = d.get('license', '')
        if has_lic and not cur.startswith('CC'):
            d['license'] = 'CC-BY-SA 4.0'
            d.setdefault('attribution', 'Cologne Digital Sanskrit Dictionaries')
        elif not cur:
            d['license'] = 'Unclear (no LICENSE.xml)'
            d.setdefault('attribution', 'indic-dict/stardict-sanskrit')
    return dicts


def main():
    ap = argparse.ArgumentParser(description='Build the full DGE Kosha corpus.')
    ap.add_argument('--sources', required=True, help='path to a checkout of indic-dict/stardict-sanskrit')
    ap.add_argument('--out', default='.', help='output root (data/koshas/** is written under here)')
    ap.add_argument('--only', default='', help='comma-separated slugs to build (default: all)')
    ap.add_argument('--skip', default='', help='comma-separated slugs to skip')
    ap.add_argument('--raw', action='store_true', help='also keep verbatim entry text (larger output)')
    args = ap.parse_args()

    dicts = refresh_licences(load_config(), args.sources)
    only = set(s for s in args.only.split(',') if s)
    skip = set(s for s in args.skip.split(',') if s)
    if only:  dicts = [d for d in dicts if d['slug'] in only]
    if skip:  dicts = [d for d in dicts if d['slug'] not in skip]

    # keep only dicts whose source file actually exists (a sparse checkout may
    # not have fetched every blob) — report what is missing rather than crashing.
    present, missing = [], []
    for d in dicts:
        (present if os.path.exists(os.path.join(args.sources, d['path'])) else missing).append(d)
    if missing:
        print('! %d source(s) not found on disk (skipping): %s'
              % (len(missing), ', '.join(d['slug'] for d in missing)))

    if args.raw:
        K.KEEP_RAW = True

    print('Building %d dictionaries → %s' % (len(present), os.path.abspath(args.out)))
    t0 = time.time()
    manifest = K.run_import(present, args.out, clone_root=args.sources)
    dt = time.time() - t0

    reg = manifest['dictionaries']
    hw = sum(m['headwords'] for m in reg.values())
    se = sum(m['senses'] for m in reg.values())
    print('\n=== BUILD COMPLETE in %.0fs ===' % dt)
    print('dictionaries: %d   headwords: %d   senses: %d   buckets: %d'
          % (len(reg), hw, se, len(manifest['buckets'])))
    # a compact per-dict report the Action prints to its log
    for slug, m in sorted(reg.items(), key=lambda kv: -kv[1]['headwords']):
        print('  %-32s %8d hw  %8d se  [%s]' % (slug, m['headwords'], m['senses'], m['license']))


if __name__ == '__main__':
    main()
