#!/usr/bin/env python3
"""Build the GLOSS inverted index (search inside the meanings).

Walks the built corpus's entry shards (data/koshas/<cat>/<slug>/e/*.json),
tokenizes every Devanagari run in each sense's gloss / etymology / citations,
folds each token through the same SLP1 fold as the headword index, and writes

    data/koshas/_gloss/manifest.json      {"buckets": [...], "tokens": N,
                                           "skipped_common": [...]}
    data/koshas/_gloss/<bucket>.json      {fold: [[dict, headword, n], ...]}

so dge/js/kosha.js glossSearch() can answer "which headwords' MEANINGS
mention महाभारते" with two small fetches. Design notes:

- Devanagari tokens only (the lead's use-case is citation words like
  महाभारते); Latin/Kannada gloss tokens are a later increment.
- tokens whose folded form is shorter than 3 chars are noise (ca, hi, na)
  and are skipped; tokens carried by more than MAX_POSTINGS headword-entries
  (iti, ca, tathā…) are unsearchably common — skipped and listed in the
  manifest so the client can say so honestly.
- postings per token are capped at ROW_CAP, most-frequent first — the UI
  shows at most 400 anyway.
- buckets use the same variable-width prefix scheme as the headword index:
  start at 2 chars, split any bucket whose serialized size passes
  SPLIT_BYTES one char deeper (file names go through the importer's %XX
  scheme via safe_name, mirroring kosha.js safeBucket).
"""
import argparse, json, os, re, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kosha_core import dev2slp1, fold

DEVA_RUN = re.compile(r'[ऄ-ह़-्ॐ-ॣ०-ॿ]+')
MIN_FOLD_LEN = 3
MAX_POSTINGS = 5000      # a token in more entries than this is not a search
ROW_CAP = 2000
SPLIT_BYTES = 6 * 1024 * 1024


def safe_name(b):
    out = []
    for c in b:
        if re.match(r'[0-9A-Za-z_]', c):
            out.append(c)
        else:
            out.append('%' + format(ord(c), '02x'))
    return ''.join(out) or '_'


def iter_texts(item):
    for s in item.get('senses') or []:
        if s.get('gloss'):
            yield s['gloss']
        if s.get('etymology'):
            yield s['etymology']
        if s.get('derivation'):
            yield s['derivation']
        for c in s.get('citations') or []:
            t = c.get('text') if isinstance(c, dict) else c
            if t:
                yield t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True, help='path to data/koshas')
    args = ap.parse_args()
    src = args.src

    # token fold -> dict slug -> headword -> count
    post = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    n_files = 0
    for cat in sorted(os.listdir(src)):
        catdir = os.path.join(src, cat)
        if cat.startswith('_') or not os.path.isdir(catdir):
            continue
        for slug in sorted(os.listdir(catdir)):
            edir = os.path.join(catdir, slug, 'e')
            if not os.path.isdir(edir):
                continue
            for fn in sorted(os.listdir(edir)):
                if not fn.endswith('.json'):
                    continue
                n_files += 1
                with open(os.path.join(edir, fn), encoding='utf-8') as f:
                    shard = json.load(f)
                for efold, items in shard.items():
                    for it in items:
                        hw = it.get('headword') or ''
                        if not hw:
                            continue
                        for text in iter_texts(it):
                            for tok in DEVA_RUN.findall(text):
                                fk = fold(dev2slp1(tok))
                                if len(fk) < MIN_FOLD_LEN:
                                    continue
                                post[fk][slug][hw] += 1

    # flatten, drop unsearchably common tokens
    skipped = []
    flat = {}
    for fk, dicts in post.items():
        rows = []
        total = 0
        for slug, hws in dicts.items():
            for hw, n in hws.items():
                rows.append([slug, hw, n])
                total += 1
        if total > MAX_POSTINGS:
            skipped.append(fk)
            continue
        rows.sort(key=lambda r: (-r[2], r[0], r[1]))
        flat[fk] = rows[:ROW_CAP]

    # bucket with variable-width prefixes
    buckets = defaultdict(dict)
    for fk, rows in flat.items():
        buckets[fk[:2]][fk] = rows

    def size_of(d):
        return len(json.dumps(d, ensure_ascii=False, separators=(',', ':')))

    final = {}
    work = list(buckets.items())
    while work:
        pref, d = work.pop()
        if size_of(d) > SPLIT_BYTES and any(len(k) > len(pref) for k in d):
            deeper = defaultdict(dict)
            shallow = {}
            for fk, rows in d.items():
                if len(fk) > len(pref):
                    deeper[fk[:len(pref) + 1]][fk] = rows
                else:
                    shallow[fk] = rows
            if shallow:
                final[pref] = shallow
            work.extend(deeper.items())
        else:
            final[pref] = d

    out = os.path.join(src, '_gloss')
    os.makedirs(out, exist_ok=True)
    for pref, d in final.items():
        with open(os.path.join(out, safe_name(pref) + '.json'), 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(out, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({
            'buckets': sorted(final.keys()),
            'tokens': len(flat),
            'skipped_common': sorted(skipped),
            'min_fold_len': MIN_FOLD_LEN,
            'note': 'inverted index over Devanagari gloss tokens; '
                    'fold(dev2slp1(token)) -> [[dict, headword, n], ...]',
        }, f, ensure_ascii=False, indent=1)
    total_mb = sum(os.path.getsize(os.path.join(out, x))
                   for x in os.listdir(out)) / 1e6
    print(f'gloss index: {len(flat)} tokens, {len(final)} buckets, '
          f'{len(skipped)} too-common skipped, {total_mb:.1f} MB '
          f'from {n_files} entry shards')


if __name__ == '__main__':
    main()
