"""NATO reporting names of DCS unit types ("S-300PS 40B6MD sr" -> "SA-10 Clam Shell radar"), read from DCS's own table
for its AI's radio calls (Scripts/Aircrafts/_Common/Cockpit/HelperAI_reporting_names.lua, lines like
["Kub 1S91 str"] = _("pAi:SA-6 Straight Flush radar"),). User, 2026-11-08: the controller said "S-300PS 40B6MD sr";
a real controller says the NATO name. Read once at first use; a type not listed keeps its DCS name.
"""
import re
import threading
from pathlib import Path

TABLE = Path('Scripts') / 'Aircrafts' / '_Common' / 'Cockpit' / 'HelperAI_reporting_names.lua'
ENTRY = re.compile(r'''\[\s*"([^"]+)"\s*\]\s*=\s*_\(\s*"(?:pAi:)?([^"]+)"\s*\)''')


def parse(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in ENTRY.finditer(text)}


class ReportingNames:
    def __init__(self, install: Path | str | None):
        self.install = Path(install) if install else None
        self.names: dict[str, str] | None = None
        self.lock = threading.Lock()

    def load(self) -> dict[str, str]:
        with self.lock:
            if self.names is None:
                try:
                    self.names = parse((self.install / TABLE).read_text(encoding='utf-8', errors='replace')) \
                        if self.install else {}
                except OSError:
                    self.names = {}
            return self.names

    def name(self, type_name: str) -> str:
        return self.load().get(type_name, type_name)
