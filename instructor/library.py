"""An aircraft module's library (built by tools/build_library.py): the DCS manuals and the user's documents, split
into sections, for the instructor's tools: the table of contents, a keyword search that returns the best passages with
their page, and reading a section (in parts when it is long).

Pure Python on plain files (modules/<id>/library/), so it runs the same on a Raspberry Pi. The search index (BM25 over
passages of about PASSAGE_WORDS words, the section title counted with each passage) is built in memory when the
library is first opened.
"""
import json
import math
import re
from collections import Counter
from pathlib import Path

from shared.modules import find_module

PASSAGE_WORDS = 120
PART_WORDS = 1500
SNIPPET_WORDS = 90
TITLE_WEIGHT = 2           # the section title counts this many times in each of its passages
K1, B = 1.2, 0.75          # BM25
PHRASE_BONUS = 0.5         # per pair of query words found next to each other
PAGE_MARK = re.compile(r'^\[page (\d+)\]$')
STOPWORDS = set('''a an and are as at be by for from has have if in into is it its of on or that the then this to
was were will with when which while can may must should do does not no your you'''.split())


def stem(word: str) -> str:
    """A light English plural stripper: 'switches' -> 'switch', 'lights' -> 'light', 'batteries' -> 'battery'."""
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    if len(word) > 4 and word.endswith('es') and word[:-2].endswith(('s', 'x', 'ch', 'sh')):
        return word[:-2]
    if len(word) > 3 and word.endswith('s') and not word.endswith(('ss', 'us', 'is')):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    return [stem(w) for w in re.findall(r'[a-z0-9]+', text.lower()) if w not in STOPWORDS]


class Library:
    def __init__(self, folder: Path):
        self.folder = folder
        self.index = json.loads((folder / 'index.json').read_text(encoding='utf-8'))
        self.books = {book['id']: book for book in self.index['books']}
        self.sections = {(book['id'], s['id']): s for book in self.index['books'] for s in book['sections']}
        self.texts = {key: (folder / s['file']).read_text(encoding='utf-8') for key, s in self.sections.items()}
        self.passages = [p for key in self.sections for p in self.split_passages(key)]
        self.document_frequency = Counter(t for p in self.passages for t in set(p['counts']))
        self.average_length = sum(p['length'] for p in self.passages) / max(1, len(self.passages))

    # Index -----------------------------------------------------------------------------------------------------
    def split_passages(self, key) -> list[dict]:
        section = self.sections[key]
        title_tokens = tokens(section['title']) * TITLE_WEIGHT
        passages, lines, words, page, first_page = [], [], 0, section['page_start'], section['page_start']

        def close():
            if lines:
                words_in = tokens(' '.join(lines))
                counts = Counter(words_in + title_tokens)
                pairs = set(zip(words_in, words_in[1:])) | set(zip(title_tokens, title_tokens[1:]))
                passages.append({'key': key, 'page': first_page, 'text': ' '.join(lines), 'counts': counts,
                                 'pairs': pairs, 'length': sum(counts.values())})

        for line in self.texts[key].splitlines():
            mark = PAGE_MARK.match(line)
            if mark:
                page = int(mark.group(1))
                continue
            if not lines:
                first_page = page
            lines.append(line)
            words += len(line.split())
            if words >= PASSAGE_WORDS:
                close()
                lines, words = [], 0
        close()
        return passages

    def score(self, passage: dict, query: list[str]) -> float:
        total, count = 0.0, len(self.passages)
        for term in query:
            frequency = passage['counts'].get(term, 0)
            if not frequency:
                continue
            df = self.document_frequency[term]
            idf = math.log(1 + (count - df + 0.5) / (df + 0.5))
            total += idf * frequency * (K1 + 1) / (
                frequency + K1 * (1 - B + B * passage['length'] / self.average_length))
        # Words of the query found side by side ("battery switch", "drag chute") count extra.
        together = sum(pair in passage['pairs'] for pair in zip(query, query[1:]))
        return total * (1 + PHRASE_BONUS * together)

    # Tools -----------------------------------------------------------------------------------------------------
    def contents(self, book: str | None = None) -> dict:
        """Each book's table of contents: one line per section, indented by depth: "8.1 Engine Start (p. 189-195)"."""
        if book and book not in self.books:
            return {'error': f'no book "{book}"', 'books': list(self.books)}
        listed = []
        for entry in self.index['books']:
            if book and entry['id'] != book:
                continue
            lines = ['  ' * s['depth'] + f'{s["id"]}  {display_title(s)}{pages_text(s)}' for s in entry['sections']]
            listed.append({'book': entry['id'], 'title': entry['title'], 'contents': '\n'.join(lines)})
        return {'books': listed}

    def search(self, query: str, book: str | None = None, limit: int = 5) -> dict:
        """The best passages for the query, one per section, best first."""
        terms = tokens(query)
        if not terms:
            return {'error': 'empty query'}
        scored = sorted(((self.score(p, terms), n) for n, p in enumerate(self.passages)
                         if not book or p['key'][0] == book), reverse=True)
        results, seen = [], set()
        for value, n in scored:
            passage = self.passages[n]
            if value <= 0 or len(results) >= max(1, min(limit, 10)):
                break
            if passage['key'] in seen:
                continue
            seen.add(passage['key'])
            section = self.sections[passage['key']]
            results.append({'book': passage['key'][0], 'section': section['id'], 'title': display_title(section),
                            'page': passage['page'], 'passage': snippet(passage['text'], terms)})
        return {'query': query, 'results': results} if results else {'query': query, 'results': [],
                                                                    'note': 'nothing found: try other English words'}

    def read(self, section: str, book: str | None = None, part: int = 1) -> dict:
        key = self.find_section(section, book)
        if not key:
            return {'error': f'no section "{section}"' + (f' in "{book}"' if book else '') +
                             ': use library_contents or search_library for the ids'}
        entry = self.sections[key]
        parts = split_parts(self.texts[key])
        part = min(max(1, int(part)), len(parts))
        result = {'book': key[0], 'section': entry['id'], 'title': display_title(entry),
                  'pages': pages_text(entry).strip(' ()'), 'part': part, 'parts': len(parts), 'text': parts[part - 1]}
        children = [s for (b, _), s in self.sections.items() if b == key[0] and s['parent'] == entry['id']]
        if children:
            result['subsections'] = [f'{s["id"]}  {display_title(s)}' for s in children]
        if part < len(parts):
            result['more'] = f'part {part + 1} of {len(parts)} continues this section'
        return result

    def find_section(self, section: str, book: str | None):
        """By id ("8.1"), id and title ("8.1 Engine Start") or title, case-insensitive; the given book first."""
        wanted = str(section).strip().lower()
        books = [book] if book in self.books else list(self.books)
        for match in (lambda s: s['id'].lower() == wanted,
                      lambda s: s['id'].lower() == wanted.split()[0] if wanted else False,
                      lambda s: s['title'].lower() == wanted or display_title(s).lower() == wanted):
            for b in books:
                for s in self.books[b]['sections']:
                    if match(s):
                        return b, s['id']
        return None


def display_title(section: dict) -> str:
    """The title without its leading number (the id already says it)."""
    return re.sub(r'^\d+(?:\.\d+)*\.?\s+', '', section['title'])


def pages_text(section: dict) -> str:
    start, end = section.get('page_start'), section.get('page_end')
    if not start:
        return ''
    return f' (p. {start})' if start == end else f' (p. {start}-{end})'


def snippet(text: str, terms: list[str]) -> str:
    """SNIPPET_WORDS words of the passage, starting a little before the first query word."""
    words = text.split()
    if len(words) <= SNIPPET_WORDS:
        return text
    first = next((n for n, w in enumerate(words) if tokens(w) and tokens(w)[0] in terms), 0)
    start = max(0, min(first - 15, len(words) - SNIPPET_WORDS))
    return ('... ' if start else '') + ' '.join(words[start:start + SNIPPET_WORDS]) + ' ...'


def split_parts(text: str) -> list[str]:
    """Parts of up to about PART_WORDS words, cut at line ends."""
    parts, lines, words = [], [], 0
    for line in text.splitlines():
        lines.append(line)
        words += len(line.split())
        if words >= PART_WORDS:
            parts.append('\n'.join(lines))
            lines, words = [], 0
    if lines or not parts:
        parts.append('\n'.join(lines))
    return parts


class Shelf:
    """The library of the aircraft being flown, opened on first use and kept per module."""

    def __init__(self, find=find_module):
        self.find = find
        self.open: dict[Path, Library] = {}

    def for_aircraft(self, aircraft: str | None) -> Library | None:
        module = self.find(aircraft)
        if not module or not (module / 'library' / 'index.json').exists():
            return None
        if module not in self.open:
            self.open[module] = Library(module / 'library')
        return self.open[module]
