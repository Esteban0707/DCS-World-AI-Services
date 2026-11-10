"""Aircraft modules: one independent folder per aircraft under modules/. The folder whose module.json lists the DCS
aircraft name is the active module; everything the instructor knows about an aircraft comes from that folder only."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / 'modules'


def find_module(aircraft: str | None, modules: Path = MODULES) -> Path | None:
    if not aircraft:
        return None
    for module_json in sorted(modules.glob('*/module.json')):
        if aircraft in json.loads(module_json.read_text(encoding='utf-8')).get('dcs_aircraft_names', []):
            return module_json.parent
    return None
