"""Is the pilot's selected radio on the instructor frequency? From SRS's radio data (dcs/instructor_export.lua).

SRS radio data: "selected" is an index into "radios" (0 is the intercom); each radio has "freq" in Hz and
"modulation" as SRS numbers it: 0 AM, 1 FM, 2 intercom, 3 disabled, 4 HAVE QUICK, 5 SATCOM, 6 MIDS.
"""
MODULATIONS = {'AM': 0, 'FM': 1}
TOLERANCE_HZ = 5000          # radios step in 5/25 kHz; anything closer is the same channel


def selected_radio(radio: dict | None) -> dict | None:
    if not radio:
        return None
    radios, index = radio.get('radios') or [], radio.get('selected')
    if not isinstance(index, int) or not 0 <= index < len(radios):
        return None
    return radios[index]


def transmit_radio(radio: dict | None, ptt_radio: int = 1) -> dict | None:
    """The radio the pilot transmits on, as "selected" (user's design, 2026-11-03: one push-to-talk button per radio,
    as in SRS, no guessing). ptt_radio 2: the second PTT button ("ptt.radio2"), always the aircraft's radio 2. ptt_radio
    1: the radio the aircraft itself has selected when that is known (SRS's data, a module's selector), else radio 1
    (radios found by scanning the cockpit's devices, dcs/instructor_export.lua, do not say which one is selected)."""
    if not radio:
        return radio
    count = len(radio.get('radios') or [])
    if ptt_radio >= 2:
        return {**radio, 'selected': ptt_radio if ptt_radio < count else -1}
    if 'selected' in radio:
        return radio
    return {**radio, 'selected': 1 if count > 1 else -1}


def on_instructor_frequency(radio: dict | None, srs: dict) -> bool | None:
    """True/False from the selected radio; None when SRS's radio data is not known (DCS or SRS not running)."""
    if not radio:
        return None
    selected = selected_radio(radio)
    if not selected or radio.get('selected') == 0:
        return False   # nothing selected, or the intercom
    frequencies = srs['frequencies_mhz']
    modulations = srs.get('modulations') or ['AM'] * len(frequencies)
    for mhz, modulation in zip(frequencies, modulations):
        if abs(selected.get('freq', 0) - mhz * 1e6) <= TOLERANCE_HZ and \
                selected.get('modulation') == MODULATIONS.get(modulation.upper()):
            return True
    return False


def describe(radio: dict | None) -> str:
    selected = selected_radio(radio)
    if not selected:
        return 'no radio selected'
    modulation = {v: k for k, v in MODULATIONS.items()}.get(selected.get('modulation'), str(selected.get('modulation')))
    return f'{selected.get("name") or "radio"} {selected.get("freq", 0) / 1e6:.3f} {modulation}'
