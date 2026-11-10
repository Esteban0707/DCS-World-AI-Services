"""The export's flight data in pilot units, for the black box (the recorder sees everything; the instructor in the
tower does not: see instructor/tower_view.py)."""

KT_PER_MS, FT_PER_M = 1.943844, 3.280840
MODULATIONS = {0: 'AM', 1: 'FM'}     # DCS radio modulation codes


def flight_context(flight: dict) -> dict:
    """The export's SI flight data in the units a pilot uses, rounded; missing values left out."""
    def value(key, factor=1.0, digits=0):
        raw = flight.get(key)
        if not isinstance(raw, (int, float)):
            return None
        number = round(raw * factor, digits)
        return int(number) if digits == 0 else number

    data = {
        'indicated_airspeed_kt': value('ias_ms', KT_PER_MS),
        'true_airspeed_kt': value('tas_ms', KT_PER_MS),
        'mach': value('mach', digits=2),
        'altitude_msl_ft': value('alt_msl_m', FT_PER_M),
        'altitude_agl_ft': value('alt_agl_m', FT_PER_M),
        'vertical_speed_fpm': value('vvi_ms', FT_PER_M * 60),
        'angle_of_attack_deg': value('aoa_deg', digits=1),
        'g': value('g', digits=1),
        'heading_deg': value('heading_deg'),
        'pitch_deg': value('pitch_deg'),
        'bank_deg': value('bank_deg'),
        'rpm_left_percent': value('rpm_left'),
        'rpm_right_percent': value('rpm_right'),
    }
    return {k: v for k, v in data.items() if v is not None}


def pilot_radio(state) -> dict | None:
    """The radio the pilot has selected (shared.cockpit.CockpitState.radio), readable."""
    radio = state.radio or {}
    radios = radio.get('radios') or []
    selected = radio.get('selected')
    if not (isinstance(selected, int) and 0 <= selected < len(radios)):
        return None
    chosen = radios[selected]
    return {'name': chosen.get('name'), 'frequency_mhz': round(chosen.get('freq', 0) / 1e6, 3),
            'modulation': MODULATIONS.get(chosen.get('modulation'), chosen.get('modulation'))}
