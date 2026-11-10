"""Build a module's switch position table (modules/<id>/positions.json): cockpit argument value -> position label.

Usage: python tools/build_positions.py [module_id]   (default F-5E-3)
Input: the module's controls.json (generated from DCS's clickabledata.lua by tools/build_module.py) and its
positions_overrides.json (hand-checked entries, each with its evidence). Output: one entry per cockpit argument of a
switch, selector, button or circuit breaker; knobs and levers that move continuously are left out.

The labels come from the DCS hint ("Left Generator Switch, L GEN/OFF/RESET"). DCS does not say which value each label
has, so the order follows rules seen in DCS's own data for the F-5E (the training lessons' cockpit checks and the live
cold-start capture of 2026-10-31):
- two/three-position toggles: the first label is the highest value (BATT = 1, L GEN = 1 / OFF = 0 / RESET = -1);
- multi-position rotary selectors (steps below 0.5): the first label is the lowest value (radar OFF = 0, STBY = 0.1);
- circuit breakers ("CB ..., ON/OFF"): pushed in (ON) = 0, pulled (OFF) = 1.
Rule-made entries are marked "verified": false until an override (or a live check) confirms them.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Switches whose effect is seen from outside the aircraft (from the control tower): gear, flaps, speed brake,
# canopy, drag chute, exterior lights, nose strut. Matched on the control name (the hint before the comma).
OUTSIDE_VISIBLE = re.compile(r'^(canopy handle|landing gear lever|drag chute t-handle|speed brake switch|flaps lever|'
                             r'auto flap system thumb switch|exterior lights|landing & taxi light|nose strut switch)',
                             re.I)


def split_hint(hint: str) -> tuple[str, list[str]]:
    """'Left Generator Switch, L GEN/OFF/RESET' -> ('Left Generator Switch', ['L GEN', 'OFF', 'RESET'])."""
    name, _, positions = hint.rpartition(', ')
    if not name or '/' not in positions:
        return hint.split(' - ')[0].strip(), []
    return name.strip(), [p.strip() for p in positions.split('/')]


def fit_labels(labels: list[str], count: int) -> list[str] | None:
    """Labels that themselves contain '/' ('A/A1 GUNS') split into too many pieces: rejoin single letters with the
    next piece until the count matches."""
    if len(labels) == count:
        return labels
    merged = list(labels)
    while len(merged) > count:
        for i, label in enumerate(merged[:-1]):
            if len(label) == 1:
                merged[i:i + 2] = [f'{label}/{merged[i + 1]}']
                break
        else:
            return None
    return merged if len(merged) == count else None


def round_value(value: float) -> float:
    return round(value, 3) + 0.0


def position_values(lims: list, steps: list) -> list[float] | None:
    low = min(lim[0] for lim in lims)
    high = max(lim[1] for lim in lims)
    step = min(abs(s) for s in steps if s) if any(steps) else 0
    if not step or high <= low:
        return None
    count = round((high - low) / step) + 1
    if count > 12:
        return None   # continuous knob, not a selector
    return [round_value(low + i * step) for i in range(count)]


def entries_for(control: dict) -> list[dict]:
    classes = control['class']
    name, labels = split_hint(control['hint'])
    switch_parts = [i for i, c in enumerate(classes) if c in ('class_type.TUMB', 'class_type.BTN')]
    if not switch_parts:
        return []
    arg = control['arg'][switch_parts[0]]
    parts = [i for i in switch_parts if control['arg'][i] == arg]
    lims = [control['arg_lim'][i] for i in parts]
    steps = [control['arg_value'][i] for i in parts]
    base = {'arg': arg, 'control': control['id'], 'name': name,
            'outside_visible': bool(OUTSIDE_VISIBLE.search(name))}

    if all(classes[i] == 'class_type.BTN' for i in parts) and len(labels) < 2:
        return [{**base, 'kind': 'button', 'positions': [{'value': 0.0, 'label': 'RELEASED'},
                                                         {'value': 1.0, 'label': 'PRESSED'}],
                 'source': 'rule:button', 'verified': False}]
    values = position_values(lims, steps)
    if values and labels and len(values) != len(labels) and len(labels) == 2:
        values = [values[0], values[-1]]   # e.g. a T-handle moved in small steps but with two named ends
    fitted = fit_labels(labels, len(values)) if values and labels else None
    if not fitted:
        return [{**base, 'kind': 'unknown', 'positions': [], 'source': 'rule:unparsed', 'verified': False,
                 'hint': control['hint']}]
    step = (values[-1] - values[0]) / max(len(values) - 1, 1)
    if control['hint'].startswith('CB '):
        order, source = values, 'rule:circuit_breaker'          # ON (pushed in) = lowest
    elif step < 0.5:
        order, source = values, 'rule:selector'                 # first label = lowest
    else:
        order, source = list(reversed(values)), 'rule:toggle'   # first label = highest
    return [{**base, 'kind': 'switch', 'positions': [{'value': v, 'label': l} for v, l in zip(order, fitted)],
             'source': source, 'verified': False}]


def build(module_dir: Path) -> dict:
    controls = json.loads((module_dir / 'controls.json').read_text(encoding='utf-8'))['controls']
    table: dict[str, dict] = {}
    for control in controls:
        for entry in entries_for(control):
            table.setdefault(str(entry['arg']), entry)
    overrides_path = module_dir / 'positions_overrides.json'
    overrides = json.loads(overrides_path.read_text(encoding='utf-8')) if overrides_path.exists() else {}
    for arg, override in overrides.get('args', {}).items():
        if arg in table:
            table[arg] = {**table[arg], **override, 'verified': True}
    return {'source': 'tools/build_positions.py from controls.json + positions_overrides.json',
            'args': dict(sorted(table.items(), key=lambda item: int(item[0])))}


def main() -> None:
    module_dir = ROOT / 'modules' / (sys.argv[1] if len(sys.argv) > 1 else 'F-5E-3')
    data = build(module_dir)
    (module_dir / 'positions.json').write_text(json.dumps(data, indent=1, ensure_ascii=False) + '\n',
                                               encoding='utf-8')
    entries = data['args'].values()
    kinds = {}
    for entry in entries:
        kinds[entry['kind']] = kinds.get(entry['kind'], 0) + 1
    print(f'{module_dir.name}: {len(data["args"])} arguments {kinds}, '
          f'{sum(e["verified"] for e in entries)} verified')
    for entry in entries:
        if entry['kind'] == 'unknown':
            print(f'  unparsed: arg {entry["arg"]} {entry["hint"]}')


if __name__ == '__main__':
    main()
