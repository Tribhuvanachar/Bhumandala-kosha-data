#!/usr/bin/env python3
# =============================================================================
# DGE Kosha — FULL corpus build driver.
#
# Clones nothing itself; it expects the indic-dict source tree to already be on
# disk (the GitHub Action clones it; locally you can clone it yourself). It then
# runs the shared importer (kosha_core.run_import) over every dictionary listed
# in dicts_config.json and writes the two-tier sharded DGE tree under <out>.
#
#   python build_koshas.py --sources-root /path/to/sources \
#                          --out . [--only slug1,slug2] [--raw]
#
# `sources/` holds one checkout per indic-dict source repo, each directory named
# after the repo — stardict-sanskrit, stardict-sanskrit-kAvya (the Purāṇic and
# epic encyclopaedias), stardict-sanskrit-vyAkaraNa (Aṣṭādhyāyī and dhātu
# literature). Each dict in dicts_config.json names its repo; the old
# single-repo form (--sources /path/to/stardict-sanskrit) still works and simply
# builds the dictionaries from that one repo.
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


DEFAULT_REPO = 'stardict-sanskrit'


def resolve_paths(dicts, repo_dirs):
    """Rewrite every d['path'] to an absolute path inside its source repo.

    A dict's optional "repo" key names the indic-dict repository it comes from
    (default `stardict-sanskrit`); repo_dirs maps that name to a checkout on
    disk. Dictionaries whose repo was not checked out are dropped here and
    reported, the same way a missing file is."""
    out, unresolved = [], []
    for d in dicts:
        root = repo_dirs.get(d.get('repo', DEFAULT_REPO))
        if not root:
            unresolved.append(d); continue
        d['path'] = os.path.join(root, d['path'])
        out.append(d)
    if unresolved:
        print('! %d dict(s) from un-checked-out source repos (skipping): %s'
              % (len(unresolved), ', '.join('%s [%s]' % (d['slug'], d.get('repo', DEFAULT_REPO))
                                            for d in unresolved)))
    return out


def refresh_licences(dicts):
    """Upgrade-only licence check: if a LICENSE.xml sits next to the .babylon,
    mark the dict cleared. Never DOWNGRADE a curated CC licence — some Cologne
    redistributions (mw-cologne, apte-english-sanskrit-cologne) ship without the
    XML in the indic-dict mirror yet are CC-BY-SA 4.0 upstream, and the config
    records that. Paths must already be absolute (see resolve_paths)."""
    for d in dicts:
        folder = os.path.dirname(d['path'])
        has_lic = os.path.exists(os.path.join(folder, 'LICENSE.xml'))
        cur = d.get('license', '')
        if has_lic and not cur.startswith('CC'):
            d['license'] = 'CC-BY-SA 4.0'
            d.setdefault('attribution', 'Cologne Digital Sanskrit Dictionaries')
        elif not cur:
            d['license'] = 'Unclear (no LICENSE.xml)'
            d.setdefault('attribution', 'indic-dict/' + d.get('repo', DEFAULT_REPO))
    return dicts



def validate(out_root, manifest, sample=400):
    """Structural check on what was just written. The entry-shard splitter once
    shipped an extra nesting level — every shard became {bucket: {fold: [...]}}
    instead of {fold: [...]} — and nothing complained, because the build only
    ever counts headwords. A corpus that cannot be read back is a failed build,
    so say so here rather than on someone's phone."""
    import random, glob
    koshas = os.path.join(out_root, 'data', 'koshas')
    dicts = manifest['dictionaries']
    problems = []

    # 1. entry shards have the documented shape
    files = glob.glob(os.path.join(koshas, '*', '*', 'e', '*.json'))
    random.seed(0)
    for f in random.sample(files, min(sample, len(files))):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception as e:
            problems.append('%s: unreadable (%s)' % (f, e)); continue
        if not isinstance(d, dict):
            problems.append('%s: top level is %s, expected object' % (f, type(d).__name__)); continue
        for fold, items in list(d.items())[:3]:
            if not isinstance(items, list):
                problems.append('%s: %r maps to %s, expected a list of entries '
                                '(extra nesting level?)' % (f, fold, type(items).__name__)); break
            if items and not (isinstance(items[0], dict) and 'headword' in items[0]):
                problems.append('%s: %r holds %r, expected entry objects' % (f, fold, type(items[0]).__name__)); break

    # 2. a sampled index record can actually be resolved to its entry, walking
    #    the deep-bucket list exactly as the app does
    idx = [f for f in glob.glob(os.path.join(koshas, '_index', '*.json'))
           if not f.endswith('manifest.json')]
    checked = resolved = 0
    for f in random.sample(idx, min(40, len(idx))):
        shard = json.load(open(f, encoding='utf-8'))
        for foldkey, recs in list(shard.items())[:5]:
            for r in recs[:2]:
                checked += 1
                d = dicts.get(r['d'])
                if not d: problems.append('index names unknown dictionary %r' % r['d']); continue
                efold = r.get('f') or foldkey
                b = efold[:manifest['entry_shard_len']]
                for deep in [d.get('deep') or []]:
                    while b in deep and len(b) < len(efold): b = efold[:len(b) + 1]
                path = os.path.join(koshas, d['category'], r['d'], 'e', K._safe(b) + '.json')
                if not os.path.exists(path):
                    problems.append('%s/%s -> missing shard %s' % (r['d'], efold, os.path.basename(path))); continue
                sh = json.load(open(path, encoding='utf-8'))
                want = r.get('w') or r['h']
                got = sh.get(efold, [])
                # Defensive: part 1 may already have found a malformed shard, and
                # a validator that raises tells you far less than one that reports.
                if not isinstance(got, list) or any(not isinstance(x, dict) for x in got):
                    problems.append('%s: fold %r in %s is not a list of entry objects'
                                    % (r['d'], efold, os.path.basename(path))); continue
                if any(it.get('headword') == want for it in got): resolved += 1
                else: problems.append('%s: %r not found in %s under fold %r' % (r['d'], want, os.path.basename(path), efold))
    print('validation: %d/%d sampled index records resolved to their entry' % (resolved, checked))
    if problems:
        print('!! %d structural problem(s):' % len(problems))
        for p in problems[:10]: print('   ', p)
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description='Build the full DGE Kosha corpus.')
    ap.add_argument('--sources', default='', help='checkout of indic-dict/stardict-sanskrit (legacy single-repo form)')
    ap.add_argument('--sources-root', default='',
                    help='directory holding one checkout per source repo, each named after the repo '
                         '(stardict-sanskrit, stardict-sanskrit-kAvya, stardict-sanskrit-vyAkaraNa)')
    ap.add_argument('--out', default='.', help='output root (data/koshas/** is written under here)')
    ap.add_argument('--only', default='', help='comma-separated slugs to build (default: all)')
    ap.add_argument('--skip', default='', help='comma-separated slugs to skip')
    ap.add_argument('--raw', action='store_true', help='also keep verbatim entry text (larger output)')
    args = ap.parse_args()
    if not (args.sources or args.sources_root):
        ap.error('one of --sources / --sources-root is required')

    # repo name -> checkout on disk. --sources-root discovers them by directory
    # name; --sources names the stardict-sanskrit checkout directly and still
    # works on its own, which is what every pre-multi-repo invocation passes.
    repo_dirs = {}
    if args.sources_root:
        for name in sorted(os.listdir(args.sources_root)):
            p = os.path.join(args.sources_root, name)
            if os.path.isdir(p): repo_dirs[name] = p
    if args.sources:
        repo_dirs[DEFAULT_REPO] = args.sources
    print('source repos: %s' % (', '.join('%s=%s' % kv for kv in sorted(repo_dirs.items())) or '(none)'))

    dicts = refresh_licences(resolve_paths(load_config(), repo_dirs))
    only = set(s for s in args.only.split(',') if s)
    skip = set(s for s in args.skip.split(',') if s)
    if only:  dicts = [d for d in dicts if d['slug'] in only]
    if skip:  dicts = [d for d in dicts if d['slug'] not in skip]

    # keep only dicts whose source file actually exists (a sparse checkout may
    # not have fetched every blob) — report what is missing rather than crashing.
    present, missing = [], []
    for d in dicts:
        (present if os.path.exists(d['path']) else missing).append(d)
    if missing:
        print('! %d source(s) not found on disk (skipping): %s'
              % (len(missing), ', '.join(d['slug'] for d in missing)))

    if args.raw:
        K.KEEP_RAW = True

    print('Building %d dictionaries → %s' % (len(present), os.path.abspath(args.out)))
    t0 = time.time()
    manifest = K.run_import(present, args.out)   # paths are already absolute
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

    if not validate(args.out, manifest):
        sys.exit('BUILD FAILED validation — refusing to publish a corpus that cannot be read back.')


if __name__ == '__main__':
    main()
