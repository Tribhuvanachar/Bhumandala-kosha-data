#!/usr/bin/env python3
# =============================================================================
# DGE Kosha importer — core library (pure Python stdlib).
# Turns StarDict dictionaries into DGE 'kosha_entry' data.json + a sharded
# cross-language lookup index, all under a dge/-rooted tree.
#
# Two input kinds, one output path:
#   - 'babylon'  : indic-dict .babylon SOURCE text (cloned from GitHub)
#   - 'stardict' : compiled .ifo/.idx/.dict[.dz]/.syn (your local dict.zip)
# Both yield (headword, html_body); the SAME builder maps them to kosha_entry.
# =============================================================================
import re, json, os, struct, gzip, io, collections

# When True, each item also keeps the verbatim source body (entry_raw) in
# addition to the parsed senses. Off by default to keep the corpus small; the
# build driver flips it with --raw.
KEEP_RAW = False

# ---------- Devanagari -> SLP1 (stdlib) --------------------------------------
_V = {'अ':'a','आ':'A','इ':'i','ई':'I','उ':'u','ऊ':'U','ऋ':'f','ॠ':'F','ऌ':'x','ॡ':'X',
      'ए':'e','ऐ':'E','ओ':'o','औ':'O','ऑ':'O','ऎ':'e','ऒ':'o'}
_M = {'ा':'A','ि':'i','ी':'I','ु':'u','ू':'U','ृ':'f','ॄ':'F','ॢ':'x','ॣ':'X',
      'े':'e','ै':'E','ो':'o','ौ':'O','ॉ':'O','ॆ':'e','ॊ':'o'}
_C = {'क':'k','ख':'K','ग':'g','घ':'G','ङ':'N','च':'c','छ':'C','ज':'j','झ':'J','ञ':'Y',
      'ट':'w','ठ':'W','ड':'q','ढ':'Q','ण':'R','त':'t','थ':'T','द':'d','ध':'D','न':'n',
      'प':'p','फ':'P','ब':'b','भ':'B','म':'m','य':'y','र':'r','ल':'l','व':'v',
      'श':'S','ष':'z','स':'s','ह':'h','ळ':'L',
      'ड़':'q','ढ़':'Q','क़':'k','ख़':'K','ग़':'g','ज़':'z','फ़':'P','य़':'y'}
_S = {'ं':'M','ः':'H','ँ':'~','ऽ':"'"}
_VIRAMA = '्'

def dev2slp1(text):
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in _C:
            base = _C[ch]; nxt = text[i+1] if i+1 < n else ''
            if nxt == _VIRAMA: out.append(base); i += 2; continue
            if nxt in _M:      out.append(base + _M[nxt]); i += 2; continue
            out.append(base + 'a'); i += 1; continue
        if ch in _V: out.append(_V[ch]); i += 1; continue
        if ch in _S: out.append(_S[ch]); i += 1; continue
        if ch == _VIRAMA: i += 1; continue
        out.append(ch); i += 1
    return ''.join(out)

def fold(slp1):
    t = slp1.replace("'", '')
    for a, b in (('A','a'),('I','i'),('U','u'),('F','f'),('X','x')): t = t.replace(a, b)
    for s in ('S','z'): t = t.replace(s, 's')
    t = t.replace('M','n').replace('~','n')
    return re.sub(r'(.)\1+', r'\1', t)

# ---------- HTML body -> senses ----------------------------------------------
FIELD_MAP = {
    'कन्नडार्थः':'gloss','अर्थः':'gloss','हिन्द्यर्थः':'gloss','आङ्ग्लार्थः':'gloss',
    'पदविभागः':'pos','लिङ्गम्':'pos','व्युत्पत्तिः':'etymology','निष्पत्तिः':'derivation',
    'प्रयोगाः':'usage','उदाहरणम्':'usage','उल्लेखाः':'refs','विस्तारः':'note','टिप्पणी':'note',
}
_BOLD = re.compile(r'<b>\s*([^<]+?)\s*[-:]\s*</b>', re.I)

def _clean(html):
    t = re.sub(r'<br\s*/?>', '\n', html, flags=re.I)
    t = re.sub(r'<a\b[^>]*>.*?</a>', '', t, flags=re.I | re.S)  # drop Cologne PDF/correction links
    t = re.sub(r'<[^>]+>', '', t)
    t = (t.replace('&lt;','<').replace('&gt;','>').replace('&amp;','&')
           .replace('&nbsp;',' ').replace('&quot;','"'))
    return re.sub(r'[ \t]+', ' ', t).strip().strip('\n').strip()

def _sense_from_segment(seg, gloss_language):
    fields = collections.defaultdict(list)
    parts = _BOLD.split(seg)
    if len(parts) > 1:
        for lab, val in zip(parts[1::2], parts[2::2]):
            key = FIELD_MAP.get(lab.strip())
            v = _clean(val)
            if key and v: fields[key].append(v)
    sense = {}
    if fields.get('gloss'):
        sense['gloss'] = ' / '.join(fields['gloss']); sense['gloss_language'] = gloss_language
    if fields.get('pos'):  sense['pos'] = fields['pos'][0]
    ety = fields.get('etymology', []) + ['निष्पत्तिः: ' + d for d in fields.get('derivation', [])]
    if ety: sense['etymology'] = '; '.join(ety)
    cites = fields.get('usage', []) + fields.get('refs', [])
    if cites: sense['citations'] = [{'text': c} for c in cites]
    if fields.get('note'): sense['note'] = ' '.join(fields['note'])
    if not sense:                      # unstructured (Cologne etc.): body IS the gloss
        g = _clean(seg)
        if g: sense = {'gloss': g, 'gloss_language': gloss_language}
    return sense or None

def body_to_senses(headword, body, gloss_language):
    """Split a body that packs several homonyms (each led by the repeated
    headword, separated by <br><br>) into one sense per homonym."""
    hw = re.escape(headword)
    b = re.sub(r'^\s*' + hw + r'\s*(?:<br\s*/?>\s*){1,2}', '', body, count=1, flags=re.I)
    segs = re.split(r'(?:<br\s*/?>\s*){1,2}' + hw + r'\s*(?:<br\s*/?>\s*){1,2}', b, flags=re.I)
    senses = [s for s in (_sense_from_segment(seg, gloss_language) for seg in segs if seg.strip()) if s]
    if not senses:
        s = _sense_from_segment(body, gloss_language)
        if s: senses = [s]
    return senses

# ---------- input kind 1: .babylon -------------------------------------------
def iter_babylon(path):
    with open(path, encoding='utf-8') as f:
        raw = f.read()
    lines = raw.split('\n'); start = 0
    for idx, ln in enumerate(lines):
        if ln.startswith('#') and '=' in ln:  continue
        if ln.strip() == '' and idx < 8:      continue
        start = idx; break
    text = '\n'.join(lines[start:])
    for blk in re.split(r'\n[ \t]*\n', text):
        blk = blk.strip('\n')
        if not blk.strip(): continue
        nl = blk.find('\n')
        head_line = blk if nl < 0 else blk[:nl]
        body = '' if nl < 0 else blk[nl+1:]
        heads = [h.strip() for h in head_line.split('|') if h.strip()]
        if heads:
            yield heads[0], heads[1:], body

# ---------- input kind 2: compiled StarDict ----------------------------------
def _read_ifo(ifo_path):
    d = {}
    with open(ifo_path, encoding='utf-8', errors='replace') as f:
        for ln in f:
            if '=' in ln:
                k, _, v = ln.partition('='); d[k.strip()] = v.strip()
    return d

def _dict_bytes(base):
    if os.path.exists(base + '.dict.dz'):
        with gzip.open(base + '.dict.dz', 'rb') as g: return g.read()
    if os.path.exists(base + '.dict'):
        with open(base + '.dict', 'rb') as g: return g.read()
    raise FileNotFoundError(base + '.dict[.dz]')

def iter_stardict(ifo_path):
    base = re.sub(r'\.ifo$', '', ifo_path)
    ifo = _read_ifo(ifo_path)
    offbits = int(ifo.get('idxoffsetbits', '32'))
    off_fmt, off_len = ('>Q', 8) if offbits == 64 else ('>I', 4)
    sts = ifo.get('sametypesequence', '')
    dictdata = _dict_bytes(base)
    idx_path = base + '.idx'
    with open(idx_path, 'rb') as f: idx = f.read()
    words, i, n = [], 0, len(idx)
    while i < n:
        j = idx.index(b'\x00', i)
        word = idx[i:j].decode('utf-8', 'replace')
        off = struct.unpack(off_fmt, idx[j+1:j+1+off_len])[0]
        size = struct.unpack('>I', idx[j+1+off_len:j+1+off_len+4])[0]
        i = j + 1 + off_len + 4
        words.append((word, off, size))
    # synonyms: word\0 + uint32 BE index into `words`
    syn_map = collections.defaultdict(list)
    if os.path.exists(base + '.syn'):
        syn = open(base + '.syn', 'rb').read(); k, m = 0, len(syn)
        while k < m:
            j = syn.index(b'\x00', k)
            sw = syn[k:j].decode('utf-8', 'replace')
            widx = struct.unpack('>I', syn[j+1:j+5])[0]; k = j + 5
            if 0 <= widx < len(words): syn_map[widx].append(sw)
    for wi, (word, off, size) in enumerate(words):
        chunk = dictdata[off:off+size]
        if sts:                                   # single declared type: whole chunk
            body = chunk.decode('utf-8', 'replace')
        else:                                     # typed fields: take first text/html field
            body = ''
            if chunk:
                t = chr(chunk[0])
                if t in 'mlghxtykwn':
                    end = chunk.find(b'\x00', 1)
                    body = chunk[1:(end if end > 0 else len(chunk))].decode('utf-8', 'replace')
                else:
                    body = chunk.decode('utf-8', 'replace')
        yield word, syn_map.get(wi, []), body

# ---------- build kosha_entry items ------------------------------------------
def build_items(entry_iter, slug, headword_language, gloss_language):
    grouped = collections.OrderedDict()   # headword -> {senses, syns, raw}
    order = []
    for headword, syns, body in entry_iter:
        senses = body_to_senses(headword, body, gloss_language)
        if headword not in grouped:
            grouped[headword] = {'senses': [], 'syns': set(), 'raw': []}; order.append(headword)
        grouped[headword]['senses'].extend(senses)
        grouped[headword]['syns'].update(syns)
        if KEEP_RAW:
            grouped[headword]['raw'].append(_clean(body))
    items, seen = [], collections.Counter()
    for hw in order:
        rec = grouped[hw]; slp1 = dev2slp1(hw); seen[slp1] += 1
        item = {'id': slp1 if seen[slp1] == 1 else f'{slp1}~{seen[slp1]}',
                'headword': hw, 'headword_slp1': slp1, 'fold': fold(slp1),
                'headword_language': headword_language, 'source': slug,
                'senses': rec['senses']}
        if KEEP_RAW and rec['raw']:
            item['entry_raw'] = '\n\n'.join(r for r in rec['raw'] if r)
        if rec['syns']:
            item['synonyms'] = sorted(rec['syns'])
            item['synonyms_slp1'] = sorted({dev2slp1(s) for s in rec['syns']})
        items.append(item)
    return items

# ---------- write DGE tree: two-tier sharded layout --------------------------
# Validated shape (tuned so a mobile lookup fetches only small files):
#   data/koshas/_index/<2char>.json      headword index: {fold:[{d,h,s,hl,l}]}
#   data/koshas/_index/manifest.json     buckets + dictionary registry
#   data/koshas/<cat>/<slug>/meta.json   source_meta + entry-bucket list
#   data/koshas/<cat>/<slug>/e/<3char>.json  full entries: {fold:[item]}
def _safe(b):
    return re.sub(r'[^0-9A-Za-z_]', lambda m: '%%%02x' % ord(m.group()), b) or '_'

def _langs_of(item):
    return sorted({s.get('gloss_language', '') for s in item['senses'] if s.get('gloss_language')})

def run_import(dicts, dge_root, clone_root=None, local_root=None,
               index_shard_len=2, entry_shard_len=3):
    koshas = os.path.join(dge_root, 'data', 'koshas')
    os.makedirs(os.path.join(koshas, '_index'), exist_ok=True)
    registry = {}
    tier1 = collections.defaultdict(lambda: collections.defaultdict(list))  # 2char -> fold -> [rec]
    tax = collections.defaultdict(dict)

    for dcfg in dicts:
        slug, kind = dcfg['slug'], dcfg['kind']
        cat = dcfg.get('category', 'misc')
        hlang, glang = dcfg.get('headword_language', 'sa'), dcfg.get('gloss_language', 'en')
        base = clone_root if kind == 'babylon' else local_root
        path = dcfg['path'] if os.path.isabs(dcfg['path']) else os.path.join(base or '', dcfg['path'])
        it = iter_babylon(path) if kind == 'babylon' else iter_stardict(path)
        try:
            items = build_items(it, slug, hlang, glang)
        except Exception as e:
            print(f'  ! FAILED {slug}: {e}'); continue

        folder = os.path.join(koshas, cat, slug)
        edir = os.path.join(folder, 'e'); os.makedirs(edir, exist_ok=True)
        # tier-2: full entries sharded by entry_shard_len
        t2 = collections.defaultdict(lambda: collections.defaultdict(list))
        for item in items:
            f = item['fold']
            t2[(f[:entry_shard_len] or '_')][f].append(item)
            tier1[(f[:index_shard_len] or '_')][f].append(
                {'d': slug, 'h': item['headword'], 's': item['headword_slp1'],
                 'hl': hlang, 'l': _langs_of(item)})
        for b, mp in t2.items():
            with open(os.path.join(edir, _safe(b) + '.json'), 'w', encoding='utf-8') as fo:
                json.dump(mp, fo, ensure_ascii=False)
        sm = {'slug': slug, 'name': dcfg.get('name', slug),
              'headword_language': hlang, 'gloss_language': glang,
              'license': dcfg.get('license', 'Unclear'),
              'attribution': dcfg.get('attribution', ''),
              'source_url': dcfg.get('source_url', '')}
        with open(os.path.join(folder, 'meta.json'), 'w', encoding='utf-8') as fo:
            json.dump({'schema': 'kosha_entry', 'source_meta': sm,
                       'entry_shard_len': entry_shard_len,
                       'buckets': sorted(t2.keys())}, fo, ensure_ascii=False)
        tax[cat][slug] = {}
        registry[slug] = {**sm, 'category': cat, 'headwords': len(items),
                          'senses': sum(len(x['senses']) for x in items)}
        print(f'  ok {slug:<28} {len(items):>7} headwords  ({cat}, {hlang}->{glang})')

    for b, mp in tier1.items():
        with open(os.path.join(koshas, '_index', _safe(b) + '.json'), 'w', encoding='utf-8') as fo:
            json.dump(mp, fo, ensure_ascii=False)
    manifest = {'buckets': sorted(tier1.keys()), 'index_shard_len': index_shard_len,
                'entry_shard_len': entry_shard_len, 'dictionaries': registry,
                'schema': 'kosha_entry'}
    with open(os.path.join(koshas, '_index', 'manifest.json'), 'w', encoding='utf-8') as fo:
        json.dump(manifest, fo, ensure_ascii=False, indent=1)
    with open(os.path.join(koshas, '_taxonomy_koshas.json'), 'w', encoding='utf-8') as fo:
        json.dump({'koshas': {'_schema': 'kosha_entry',
                              **{k: dict(v) for k, v in tax.items()}}}, fo, ensure_ascii=False, indent=1)
    return manifest
