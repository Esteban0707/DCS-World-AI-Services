"""The wingman's playbook (user's idea, 2026-11-04: "a manual of instructions by situation", from what the flights
taught us): one page per situation in wingman/playbook/*.md, the same for every wingman agent (our mechanics, like
the memory; the personality stays the user's).

Only the index (each page's title and when it applies) is in the model's instructions on every turn, so the pages
cost no tokens until needed. The model opens a page with its playbook tool when the situation starts; an open page
stays in its instructions for PAGE_KEPT_S (the brain's history keeps only a short note of a tool's answer), the last
PAGES_OPEN at most.

A page: first line "# Title", second line "When: ...", then the instructions.

One folder per kind of aircraft (user, 2026-11-05: a helicopter wingman gets only the helicopter's pages):
wingman/playbook/airplane, wingman/playbook/helicopter; the kind comes from the desk (DCS's group category).
"""
import time
from pathlib import Path

FOLDER = Path(__file__).resolve().parent / 'playbook'
PAGE_KEPT_S = 900.0
PAGES_OPEN = 2


def read_page(path: Path) -> dict | None:
    try:
        lines = path.read_text(encoding='utf-8').strip().splitlines()
    except OSError:
        return None
    if len(lines) < 2 or not lines[0].startswith('# ') or not lines[1].startswith('When:'):
        return None
    return {'name': path.stem, 'title': lines[0][2:].strip(), 'when': lines[1][5:].strip(),
            'text': '\n'.join(lines[2:]).strip()}


class Playbook:
    def __init__(self, root: Path = FOLDER, kind=lambda: 'airplane'):
        """kind(): airplane / helicopter, asked on every use (known once DCS reports the group)."""
        self.root, self.kind = root, kind
        self.opened: dict[str, float] = {}   # page name -> when it was opened

    @property
    def folder(self) -> Path:
        return self.root / self.kind()

    def pages(self) -> list[dict]:
        """Read on every use: a page may have been edited meanwhile."""
        found = (read_page(p) for p in sorted(self.folder.glob('*.md')))
        return [p for p in found if p]

    def open(self, page: str) -> dict:
        pages = {p['name']: p for p in self.pages()}
        wanted = (page or '').strip().lower().replace(' ', '-').removesuffix('.md')
        found = pages.get(wanted)
        if not found:
            return {'ok': False, 'detail': f'no page {page} (pages: {", ".join(pages) or "none"})'}
        self.opened.pop(found['name'], None)
        self.opened[found['name']] = time.monotonic()
        while len(self.opened) > PAGES_OPEN:
            self.opened.pop(next(iter(self.opened)))
        return {'ok': True, 'detail': f'page "{found["title"]}" open: it stays in your instructions while it '
                                      f'applies', 'page': found['text']}

    def prompt(self) -> str:
        pages = self.pages()
        if not pages:
            return ''
        now = time.monotonic()
        self.opened = {n: at for n, at in self.opened.items() if now - at <= PAGE_KEPT_S}
        lines = ['Your playbook (what we learned flying together). When one of these situations starts, open its '
                 'page with the playbook tool before you act, then follow it; an open page stays below while it '
                 'applies:']
        lines += [f'- {p["name"]}: {p["title"]}. When: {p["when"]}' for p in pages]
        for page in pages:
            if page['name'] in self.opened:
                lines += ['', f'Open playbook page "{page["title"]}":', page['text']]
        return '\n'.join(lines)
