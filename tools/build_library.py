"""Build an aircraft module's library: the module's DCS manuals and the user's own documents, split into sections
with an index, for the instructor's library tools (instructor/library.py).

Usage:  python tools/build_library.py --module F-5E-3 [--dcs "C:/Program Files/Eagle Dynamics/DCS World"]
        (default --dcs: settings "dcs.install")

Sources:
- module.json "library": the DCS manuals, paths relative to the DCS install (English only, user's choice 2026-11-01).
- modules/<id>/documents/: whatever the user drops there (.pdf, .md, .txt), one book per file.
Output, modules/<id>/library/ (rebuilt from scratch):
- index.json: {built, books: [{id, title, source, pages, sections: [{id, title, depth, parent, page_start, page_end,
  words, file}]}]}
- <book>/<section file>.txt: the section's cleaned text, with "[page N]" markers (the PDF's own page numbers).

Sections come from the PDF outline (bookmarks): each runs from its heading to the next outline heading, at any depth,
so no text is repeated; a chapter's file holds its introduction and its subsections are listed in the index. Figure and
table captions in the outline are not sections. A PDF without an outline is split every PAGES_PER_PART pages; a
Markdown file by its headings; a text file is one section.
"""
import argparse
import bisect
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PAGES_PER_PART = 4
CAPTION = re.compile(r'^(figure|fig\.|table|image|picture)\s*\d', re.I)
SKIPPED_TITLES = {'tableofcontents', 'contents'}
NUMBERED = re.compile(r'^(\d+(?:\.\d+)*)\.?\s+\S')
PAGE_HEADER = re.compile(r'^\s*DIGITAL COMBAT SIMULATOR\b.*$', re.I)
# The manuals' chapter banners use a symbol font that extracts as garbage containing this character.
BANNER_CHAR = '\u0168'
# List bullets drawn with a symbol font extract as private-use characters (e.g. U+F0B7).
BULLET = re.compile(r'^\s*[\ue000-\uf8ff\u2022\u25aa]\s*')
# Other Symbol-font characters seen in the F-5E manuals; any other private-use character is dropped.
SYMBOLS = {'\uf0b0': '\u00b0', '\uf02a': '*', '\uf0b2': '\u2033', '\uf0b7': '-'}
# The English manuals contain Cyrillic look-alike letters ("\u041c\u041a-82", "\u041a\u0412-26\u0410", "140\u0421"): searches need the Latin ones.
HOMOGLYPHS = str.maketrans('\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0425\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u0456\u0406', 'ABEKMHOPCTXaeopcyxiI')


def plain(text: str) -> str:
    """Cyrillic look-alikes to Latin, Symbol-font characters to Unicode."""
    text = text.translate(HOMOGLYPHS)
    return re.sub(r'[\ue000-\uf8ff]', lambda m: SYMBOLS.get(m.group(), ''), text)
DOCUMENT_TYPES = ('.pdf', '.md', '.txt')


# Text cleaning --------------------------------------------------------------------------------------------------
def unspace(line: str) -> str:
    """Letter-spaced headings: 'B e f o r e  S t a r t' -> 'Before Start' (words split by double spaces);
    'C A D C / P i t o t- S t a t i c S y s t e m' -> 'CADC/Pitot-Static System' (single spaces: words are cut
    where a lowercase letter meets an uppercase one). Other lines unchanged."""
    stripped = line.strip()
    tokens = stripped.split()
    if (len(tokens) < 5 or sum(len(t) == 1 for t in tokens) < 0.8 * len(tokens)
            or sum(len(t) == 1 and t.isalpha() for t in tokens) < 0.5 * len(tokens)):   # not a row of numbers
        return line
    words = re.split(r'\s{2,}', stripped)
    if len(words) > 1:
        return ' '.join(w.replace(' ', '') for w in words)
    return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', stripped.replace(' ', ''))


def clean_page(text: str) -> str:
    """One page's extracted text: no running header or symbol-font banner, headings unspaced, wrapped lines joined."""
    lines = []
    for raw in text.replace('\r', '').split('\n'):
        if PAGE_HEADER.match(raw) or BANNER_CHAR in raw:
            continue
        line = plain(BULLET.sub('- ', unspace(raw))).strip()
        if line:
            lines.append(re.sub(r'[ \t]+', ' ', line))
    out: list[str] = []
    for line in lines:
        if out and joins(out[-1], line):
            out[-1] = out[-1][:-1] + line if out[-1].endswith('-') and line[:1].islower() else out[-1] + ' ' + line
        else:
            out.append(line)
    return '\n'.join(out)


def joins(previous: str, line: str) -> bool:
    """A layout line break inside a sentence: the next line continues it."""
    if re.match(r'^(\d+[.)]|[•▪\-–]\s)', line):
        return False
    return line[:1].islower() or line[:1] in '(|' or previous.endswith((',', '-', '–', ' or', ' and', ' the'))


def normalized(text: str) -> tuple[str, list[int]]:
    """Lowercase letters and digits only, with each kept character's index in the original text."""
    keep = [(c.lower(), i) for i, c in enumerate(text) if c.isalnum()]
    return ''.join(c for c, _ in keep), [i for _, i in keep]


# PDF -----------------------------------------------------------------------------------------------------------
def outline_entries(reader) -> list[dict]:
    """Flattened outline: [{title, depth, page (0-based)}], captions and empty titles dropped."""
    entries = []

    def walk(items, depth):
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            title = plain(re.sub(r'\s+', ' ', str(item.title or ''))).strip()
            if not title or CAPTION.match(title) or normalized(title)[0] in SKIPPED_TITLES:
                continue
            try:
                page = reader.get_destination_page_number(item)
            except Exception:
                continue
            entries.append({'title': title, 'depth': depth, 'page': page})
    walk(reader.outline, 0)
    return entries


def pdf_book(path: Path, book_id: str, title: str, source: str) -> dict:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    pages = [clean_page(page.extract_text() or '') for page in reader.pages]
    # One document with page markers; page_at[i] = character offset where page i starts.
    document, page_at = '', []
    for number, text in enumerate(pages, 1):
        page_at.append(len(document))
        document += f'[page {number}]\n{text}\n'
    entries = outline_entries(reader)
    if not entries:
        entries = [{'title': f'Pages {p + 1}-{min(p + PAGES_PER_PART, len(pages))}', 'depth': 0, 'page': p}
                   for p in range(0, len(pages), PAGES_PER_PART)]
    flat, offsets = normalized(document)
    starts, previous = [], 0
    for entry in entries:
        page_start = page_at[entry['page']]
        # The heading is looked for on its outline page and the next one, never before the previous section.
        window_end = page_at[entry['page'] + 2] if entry['page'] + 2 < len(page_at) else len(document)
        start = max(page_start, previous)
        key = normalized(entry['title'])[0]
        found = -1
        if key:
            lo = bisect.bisect_left(offsets, start)
            hit = flat.find(key, lo)
            if hit >= 0 and offsets[hit] < window_end:
                found = offsets[hit]
        position = found if found >= 0 else start
        starts.append(position)
        previous = position
    sections = []
    for k, entry in enumerate(entries):
        end = starts[k + 1] if k + 1 < len(entries) else len(document)
        text = document[starts[k]:end].strip()
        if not text.startswith('[page'):   # carry the page number of a section starting mid-page
            text = f'[page {page_of(starts[k], page_at)}]\n{text}'
        sections.append({'title': entry['title'], 'depth': entry['depth'], 'text': text,
                         'page_start': page_of(starts[k], page_at), 'page_end': page_of(max(starts[k], end - 1), page_at)})
    return finish_book(book_id, title, source, len(pages), sections)


def page_of(offset: int, page_at: list[int]) -> int:
    return max(i for i, at in enumerate(page_at) if at <= offset) + 1


# Text documents -----------------------------------------------------------------------------------------------
def markdown_book(path: Path, book_id: str) -> dict:
    sections, current = [], None
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        heading = re.match(r'^(#{1,6})\s+(.*)', line)
        if heading or current is None:
            current = {'title': heading.group(2).strip() if heading else path.stem,
                       'depth': len(heading.group(1)) - 1 if heading else 0, 'lines': []}
            sections.append(current)
            if heading:
                continue
        current['lines'].append(line)
    sections = [{'title': s['title'], 'depth': s['depth'], 'text': '\n'.join(s['lines']).strip(),
                 'page_start': None, 'page_end': None} for s in sections]
    return finish_book(book_id, path.stem, f'documents/{path.name}', None, sections)


def text_book(path: Path, book_id: str) -> dict:
    text = path.read_text(encoding='utf-8', errors='replace').strip()
    return finish_book(book_id, path.stem, f'documents/{path.name}', None,
                       [{'title': path.stem, 'depth': 0, 'text': text, 'page_start': None, 'page_end': None}])


# Book assembly -------------------------------------------------------------------------------------------------
def finish_book(book_id: str, title: str, source: str, pages, sections: list[dict]) -> dict:
    """Section ids: the heading's own number ("8.1"), else parent id + letter ("6.7a"), else a running number;
    parents from depth."""
    used, stack, counters = set(), [], {}
    for section in sections:
        while stack and stack[-1]['depth'] >= section['depth']:
            stack.pop()
        parent = stack[-1] if stack else None
        number = NUMBERED.match(section['title'])
        if number and number.group(1) not in used:
            sid = number.group(1)
        else:
            base = parent['id'] if parent else 's'
            counters[base] = counters.get(base, 0) + 1
            sid = f'{base}{chr(96 + counters[base])}' if parent and counters[base] <= 26 else f'{base}{counters[base]}'
        while sid in used:
            sid += '_'
        used.add(sid)
        section['id'], section['parent'] = sid, parent['id'] if parent else None
        stack.append(section)
    return {'id': book_id, 'title': title, 'source': source, 'pages': pages, 'sections': sections}


def slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-') or 'document'


def write_library(books: list[dict], out: Path) -> dict:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    index = {'built': time.strftime('%Y-%m-%d %H:%M:%S'), 'books': []}
    for book in books:
        folder = out / book['id']
        folder.mkdir()
        entries = []
        for section in book['sections']:
            name = re.sub(r'[^A-Za-z0-9._-]', '_', section['id']) + '.txt'
            (folder / name).write_text(section['text'] + '\n', encoding='utf-8')
            entries.append({'id': section['id'], 'title': section['title'], 'depth': section['depth'],
                            'parent': section['parent'], 'page_start': section['page_start'],
                            'page_end': section['page_end'], 'words': len(section['text'].split()),
                            'file': f'{book["id"]}/{name}'})
        index['books'].append({'id': book['id'], 'title': book['title'], 'source': book['source'],
                               'pages': book['pages'], 'sections': entries})
    temp = out / 'index.json.tmp'   # the index last and whole: a build cut short leaves no index, so it is redone
    temp.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, out / 'index.json')
    return index


def user_documents(module: Path) -> list[Path]:
    folder = module / 'documents'
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in DOCUMENT_TYPES) if folder.is_dir() else []


def stale(module: Path) -> str | None:
    """Why the module's library must be rebuilt (None when it is up to date): never built, unreadable or incomplete
    (a build interrupted, a section file lost), module.json changed, or a document added, changed or removed."""
    index_path = module / 'library' / 'index.json'
    if not index_path.exists():
        return 'not built yet'
    built = index_path.stat().st_mtime
    if (module / 'module.json').stat().st_mtime > built:
        return 'module.json changed'
    try:
        index = json.loads(index_path.read_text(encoding='utf-8'))
        books = {b['id'] for b in index['books']}
        files = [s['file'] for b in index['books'] for s in b['sections']]
    except (ValueError, KeyError, TypeError):
        return 'index unreadable'
    manuals = {e['id'] for e in json.loads((module / 'module.json').read_text(encoding='utf-8')).get('library', [])}
    if not manuals <= books:
        return 'a manual is missing'
    if not all((module / 'library' / f).is_file() for f in files):
        return 'section files missing'
    indexed = {b['source'] for b in index['books'] if b['id'].startswith('user-')}
    present = {f'documents/{p.name}' for p in user_documents(module)}
    if indexed != present:
        return 'documents added or removed'
    if any(p.stat().st_mtime > built for p in user_documents(module)):
        return 'a document changed'
    return None


def build(dcs: Path, module: Path) -> dict:
    config = json.loads((module / 'module.json').read_text(encoding='utf-8'))
    books = []
    for entry in config.get('library', []):
        path = dcs / entry['source']
        print(f'{entry["id"]}: {path}')
        books.append(pdf_book(path, entry['id'], entry['title'], entry['source']))
    for path in user_documents(module):
        book_id = 'user-' + slug(path.stem)
        print(f'{book_id}: {path}')
        if path.suffix.lower() == '.pdf':
            books.append(pdf_book(path, book_id, path.stem, f'documents/{path.name}'))
        elif path.suffix.lower() == '.md':
            books.append(markdown_book(path, book_id))
        else:
            books.append(text_book(path, book_id))
    return write_library(books, module / 'library')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dcs', help='DCS World install folder (default: settings "dcs.install")')
    parser.add_argument('--module', required=True, help='module folder name under modules/')
    args = parser.parse_args()
    dcs = args.dcs or json.loads((ROOT / 'settings.json').read_text(encoding='utf-8'))['dcs']['install']
    index = build(Path(dcs), ROOT / 'modules' / args.module)
    for book in index['books']:
        words = sum(s['words'] for s in book['sections'])
        print(f'{book["id"]}: {len(book["sections"])} sections, {book["pages"] or "-"} pages, {words} words')


if __name__ == '__main__':
    main()
