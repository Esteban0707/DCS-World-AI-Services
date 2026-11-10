"""Which DCS ground vehicles carry infantry, and how much: read from DCS's own unit files.

User, 2026-11-05: "some vehicles in DCS can pick up infantry: detect the hold, then give them Cobra's pickup". DCS's
Mission Editor offers a vehicle the Embarking / Disembarking tasks only when its type has GT.InternalCargo
(MissionEditor/modules/me_action_db.lua verifyGroupCapability), with the size it holds in nominalCapacity, or
unit_point * 100 (me_action_param_panels.lua getTransportCapacity). The same field is read here from the unit files
under CoreMods and Mods (BTR-80, BTR-82A, MaxxPro MRAP, M2A1 half-track... 2026-11-05). The mission may also say it
live (dcs/instructor_tasks.lua ground_details, getDescentCapacity); that wins when it answers.

The scan reads every unit file once (a few thousand), in a background thread; the result is kept in
logs/cargo_capacity.json for the next runs (scanned again when the DCS folder changes).
"""
import json
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'logs' / 'cargo_capacity.json'
FOLDERS = ('CoreMods', 'Mods')
GROUND_NAME = re.compile(r'''^\s*GT\.Name\s*=\s*["']([^"']+)["']''', re.M)
INTERNAL_CARGO = re.compile(r'GT\.InternalCargo\s*=\s*\{(.*?)\}', re.S)
NOMINAL = re.compile(r'nominalCapacity\s*=\s*(\d+)')
UNIT_POINT = re.compile(r'unit_point\s*=\s*(\d+)')


def parse(text: str) -> tuple[str, int] | None:
    """(type name, capacity) of a ground unit file whose type has a troop compartment, else None."""
    cargo, name = INTERNAL_CARGO.search(text), GROUND_NAME.search(text)
    if not cargo or not name:
        return None
    point, nominal = UNIT_POINT.search(cargo.group(1)), NOMINAL.search(cargo.group(1))
    capacity = int(point.group(1)) * 100 if point else int(nominal.group(1)) if nominal else 0
    return (name.group(1), capacity) if capacity > 0 else None


def scan(install: Path) -> dict[str, int]:
    found = {}
    for folder in FOLDERS:
        for path in (install / folder).rglob('*.lua'):
            try:
                text = path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            if 'InternalCargo' in text:
                entry = parse(text)
                if entry:
                    found[entry[0]] = entry[1]
    return found


class CargoCapacities:
    """capacity(type) -> the size the type holds (0: none, None: not known yet, while the scan runs)."""

    def __init__(self, install: Path | str | None, cache: Path = CACHE, log=print):
        self.install, self.cache, self.log = (Path(install) if install else None), cache, log
        self.types: dict[str, int] | None = None
        self.started = False
        self.lock = threading.Lock()

    def load(self) -> None:
        key = str(self.install)
        try:
            saved = json.loads(self.cache.read_text(encoding='utf-8'))
            if saved.get('install') == key and isinstance(saved.get('types'), dict):
                self.types = saved['types']
                return
        except (OSError, ValueError):
            pass
        types = scan(self.install) if self.install and self.install.is_dir() else {}
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(json.dumps({'install': key, 'types': types}, indent=1), encoding='utf-8')
        except OSError:
            pass
        self.types = types
        self.log({'event': 'cargo_capacities', 'install': key, 'types': len(types)})

    def capacity(self, type_name: str) -> int | None:
        with self.lock:
            if not self.started:
                self.started = True
                threading.Thread(target=self.load, name='cargo-scan', daemon=True).start()
        if self.types is None:
            return None
        return int(self.types.get(type_name) or 0)
