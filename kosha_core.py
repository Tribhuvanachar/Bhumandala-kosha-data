#!/usr/bin/env python3
# =============================================================================
# DGE Kosha importer — core library (pure Python stdlib).
# Turns StarDict dictionaries into DGE 'kosha_entry' data.json + a sharded
# cross-language lookup index, all under a dge/-rooted tree.
#
# Two input kinds, one output path:
#   - 'babylon'  : indic-dict .babylon SOURCE text (cloned from GitHub)
#   - 'cdsl'     : Cologne sanskrit-lexicon/csl-orig v02 .txt (SLP1 + pseudo-XML)
#   - 'stardict' : compiled .ifo/.idx/.dict[.dz]/.syn (your local dict.zip)
# Both yield (headword, html_body); the SAME builder maps them to kosha_entry.
# =============================================================================
import re, json, os, struct, gzip, io, collections

# When True, each item also keeps the verbatim source body (entry_raw) in
# addition to the parsed senses. Off by default to keep the corpus small; the
# build driver flips it with --raw.
KEEP_RAW = False

# Per-dictionary text-handling knobs, refreshed by set_opts() before each
# dictionary is parsed (same module-global style as KEEP_RAW). The defaults
# reproduce the original behaviour exactly for the Cologne-era 63.
#   link_text 'drop'   : delete <a>…</a> whole — right for Cologne, whose
#                        anchors are PDF-page and correction links.
#   link_text 'unwrap' : keep each anchor's text and drop only navigation
#                        anchors (href starting with '_' or '#') — right for
#                        the encyclopaedias, where the cross-references ARE
#                        the prose ("son of <a …>arjuna</a>").
#   block_breaks       : treat </div>, </p>, </li>, </h1-6>, </tr> as line
#                        breaks. The Cologne set separates with <br>; the
#                        encyclopaedias nest divs, and without this the heading
#                        runs straight into the first word of the article.
#   strip              : regexes replaced by a line break in the cleaned text,
#                        for the page furniture ("word6", "P1L", catalogue ids)
#                        those sources carry.
#   json_body          : the entry body is a JSON object of Sanskrit field
#                        names (the ashtadhyayi.com-derived dhatu sets) —
#                        render it as "field: value" prose instead of braces.
#   no_homonym_split   : do not split the body on repetitions of the headword.
#                        Needed wherever the headword legitimately recurs mid
#                        entry, as in the Astadhyayi sets keyed on sutra text.
OPTS = {'link_text': 'drop', 'block_breaks': False, 'strip': (),
        'json_body': False, 'no_homonym_split': False}

_BLOCK_END = re.compile(r'</(?:div|p|li|h[1-6]|tr|blockquote|table)\s*>', re.I)

def set_opts(dcfg):
    OPTS['link_text'] = dcfg.get('link_text', 'drop')
    OPTS['block_breaks'] = bool(dcfg.get('block_breaks', False))
    OPTS['strip'] = tuple(re.compile(p, re.M) for p in dcfg.get('strip', ()))
    OPTS['json_body'] = bool(dcfg.get('json_body', False))
    OPTS['no_homonym_split'] = bool(dcfg.get('no_homonym_split', False))

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

# ---------- SLP1 -> Devanagari ------------------------------------------------
# The Cologne source files key every entry on an SLP1 string and mark Devanagari
# spans as SLP1 too, so the reader needs the inverse of dev2slp1 above. Built by
# inverting the same tables, so the two can never drift apart.
# setdefault throughout, never a dict comprehension: several Devanagari letters
# share one SLP1 code (ए/ऎ both 'e', क/क़ both 'k'), and a comprehension lets the
# LAST spelling win — which silently wrote the southern short ऎ for every 'e',
# turning veda into वॆद. First spelling is the canonical one.
def _invert(d):
    out = {}
    for k, v in d.items(): out.setdefault(v, k)
    return out
_SLP_V = _invert(_V)
_SLP_M = _invert(_M)
_SLP_C = _invert(_C)
_SLP_S = _invert(_S)
# Longest-first so 'A'/'I' are not eaten by a shorter key, and consonants are
# tried before vowels because every consonant carries an inherent 'a'.
_SLP_TOK = re.compile('|'.join(sorted(
    (re.escape(k) for k in list(_SLP_C) + list(_SLP_V) + list(_SLP_S)),
    key=len, reverse=True)))

def slp12dev(text):
    out, i, n = [], 0, len(text)
    while i < n:
        m = _SLP_TOK.match(text, i)
        if not m:
            out.append(text[i]); i += 1; continue
        t = m.group(0); i = m.end()
        if t in _SLP_C:
            out.append(_SLP_C[t])
            # what follows decides the vowel sign: another consonant or the end
            # means virama, a bare 'a' means the inherent vowel and no sign.
            m2 = _SLP_TOK.match(text, i)
            nxt = m2.group(0) if m2 else ''
            if nxt == 'a':
                i = m2.end()
            elif nxt in _SLP_V:
                out.append(_SLP_M.get(nxt, '')); i = m2.end()
            else:
                out.append(_VIRAMA)
        elif t in _SLP_V:
            out.append(_SLP_V[t])
        else:
            out.append(_SLP_S[t])
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

_NAV_A = re.compile(r'<a\b[^>]*\bhref\s*=\s*["\']?[_#][^>]*>.*?</a>', re.I | re.S)

def _clean(html):
    # <style>/<script> bodies are not prose: strip the element AND its content
    # before tag-stripping, or the encyclopaedias' CSS lands inside the gloss.
    t = re.sub(r'<(style|script)\b[^>]*>.*?</\1>', '', html, flags=re.I | re.S)
    t = re.sub(r'<!--.*?-->', '', t, flags=re.S)
    t = re.sub(r'<br\s*/?>', '\n', t, flags=re.I)
    if OPTS['block_breaks']:
        t = _BLOCK_END.sub('\n', t)
    if OPTS['link_text'] == 'unwrap':
        t = _NAV_A.sub('', t)                                   # page/footnote navigation only
    else:
        t = re.sub(r'<a\b[^>]*>.*?</a>', '', t, flags=re.I | re.S)  # Cologne PDF/correction links
    t = re.sub(r'<[^>]+>', '', t)
    t = (t.replace('&lt;','<').replace('&gt;','>').replace('&amp;','&')
           .replace('&nbsp;',' ').replace('&quot;','"'))
    if OPTS['strip'] or OPTS['block_breaks']:
        # A line break, not '', so removing "…word8" between a heading and its
        # article does not weld them together. Tidying the resulting blank runs
        # is confined to this branch so the Cologne-era output stays identical.
        for rx in OPTS['strip']:
            t = rx.sub('\n', t)
        t = re.sub(r'[ \t]*\n[ \t]*', '\n', t)
        t = re.sub(r'\n{3,}', '\n\n', t)
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
        if g and OPTS['json_body']: g = _json_to_prose(g)
        if g: sense = {'gloss': g, 'gloss_language': gloss_language}
    return sense or None


def _json_to_prose(text):
    """"{"धातुः":["लर्ब्"],"गणः":["भ्वादिः"]}" -> "धातुः: लर्ब्; गणः: भ्वादिः".
    Left untouched if it is not in fact a JSON object."""
    if not text.startswith('{'): return text
    try:
        obj = json.loads(text)
    except ValueError:
        return text
    if not isinstance(obj, dict): return text
    out = []
    for k, v in obj.items():
        if isinstance(v, list): v = ', '.join(str(x) for x in v if str(x).strip())
        v = str(v).strip()
        if v: out.append('%s: %s' % (k, v))
    return '; '.join(out) or text

def body_to_senses(headword, body, gloss_language):
    """Split a body that packs several homonyms (each led by the repeated
    headword, separated by <br><br>) into one sense per homonym."""
    hw = re.escape(headword)
    b = re.sub(r'^\s*' + hw + r'\s*(?:<br\s*/?>\s*){1,2}', '', body, count=1, flags=re.I)
    segs = ([b] if OPTS['no_homonym_split'] else
            re.split(r'(?:<br\s*/?>\s*){1,2}' + hw + r'\s*(?:<br\s*/?>\s*){1,2}', b, flags=re.I))
    senses = [s for s in (_sense_from_segment(seg, gloss_language) for seg in segs if seg.strip()) if s]
    if not senses:
        s = _sense_from_segment(body, gloss_language)
        if s: senses = [s]
    return senses

# ---------- input kind 1: .babylon -------------------------------------------
def iter_babylon(path, head_pick=0, head_strip=None, syn_drop=None):
    """head_pick   which '|'-separated alternative is the real headword. The
                   aShTAdhyAyI dictionaries key on the sūtra NUMBER (१.१.१) and
                   carry the sūtra text second; head_pick=1 makes the text the
                   headword (the index only searches headwords) and demotes the
                   number to a synonym.
       head_strip  regex peeled off the headword — the cultural index prefixes
                   every entry with a list marker ("➤ 01)  अकर्कर").
       syn_drop    regex marking alternatives that are internal machinery
                   (page anchors like "_pe_@6", "p1_mci"), not real synonyms."""
    head_strip = re.compile(head_strip) if head_strip else None
    syn_drop = re.compile(syn_drop) if syn_drop else None
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
        if head_strip:
            heads = [h for h in (head_strip.sub('', h).strip() for h in heads) if h]
        if syn_drop:
            heads = [h for h in heads if not syn_drop.match(h)]
        if not heads:
            continue
        pick = head_pick if head_pick < len(heads) else 0
        yield heads[pick], heads[:pick] + heads[pick+1:], body

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

# ---------- input kind 3: Cologne (sanskrit-lexicon/csl-orig) -----------------
# The canonical Cologne sources, one .txt per dictionary under v02/<code>/. An
# entry runs from <L> to <LEND>; the <L> line carries the keys, everything after
# it is the body. Devanagari is stored as SLP1 inside {#...#} or <s>...</s>, so
# it has to be transcoded before the shared HTML cleaner ever sees it.
_CDSL_ENTRY = re.compile(r'<L>(.*?)<LEND>', re.S)
_CDSL_KEYS = re.compile(r'<k1>(.*?)<k2>([^<\n]*)', re.S)

def _cdsl_body(t):
    # SLP1 spans -> Devanagari. {%...%} is italic (the German/English gloss) and
    # {@...@} bold; both are kept as plain text, since the shared sense-splitter
    # works on text, not on typography.
    t = re.sub(r'\{#(.*?)#\}', lambda m: slp12dev(m.group(1)), t, flags=re.S)
    t = re.sub(r'<s>(.*?)</s>', lambda m: slp12dev(m.group(1)), t, flags=re.S)
    t = re.sub(r'\{[%@](.*?)[%@]\}', r'\1', t, flags=re.S)
    t = re.sub(r'<ls>(.*?)</ls>', r'[\1]', t, flags=re.S)      # literature citation
    t = re.sub(r'<(?:div|sup|hom|lex|ab|vlex|etym|bot|bio|info)\b[^>]*>', ' ', t, flags=re.I)
    t = re.sub(r'</(?:div|sup|hom|lex|ab|vlex|etym|bot|bio|info)>', ' ', t, flags=re.I)
    t = t.replace('¦', ' ').replace('〉', ') ').replace('〈', ' (')
    t = re.sub(r'\[Page[^\]]*\]', ' ', t)                     # page furniture
    t = re.sub(r'<pc>[^<\n]*', ' ', t)
    return t.strip()

def iter_cdsl(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        raw = f.read()
    for blk in _CDSL_ENTRY.findall(raw):
        nl = blk.find('\n')
        head_line = blk if nl < 0 else blk[:nl]
        body = '' if nl < 0 else blk[nl + 1:]
        km = _CDSL_KEYS.search(head_line)
        if not km:
            continue
        k1 = km.group(1).strip()
        k2 = (km.group(2) or '').strip()
        if not k1:
            continue
        hw = slp12dev(k1)
        syns = []
        if k2 and k2 != k1:
            d2 = slp12dev(k2)
            if d2 and d2 != hw: syns.append(d2)
        yield hw, syns, _cdsl_body(body)

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
        base = local_root if kind == 'stardict' else clone_root
        path = dcfg['path'] if os.path.isabs(dcfg['path']) else os.path.join(base or '', dcfg['path'])
        set_opts(dcfg)
        if kind == 'babylon':
            it = iter_babylon(path, head_pick=dcfg.get('head_pick', 0),
                              head_strip=dcfg.get('head_strip'),
                              syn_drop=dcfg.get('syn_drop'))
        elif kind == 'cdsl':
            it = iter_cdsl(path)
        else:
            it = iter_stardict(path)
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
        # the licence caveat has to travel with the data, not only sit in the
        # build config — it is the whole basis on which an Unclear dict ships.
        if dcfg.get('license_note'): sm['license_note'] = dcfg['license_note']
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
