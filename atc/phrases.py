"""What the tower says, in the radio language: fixed phrases and spoken numbers from locales/<lang>.json "atc".

Adapted from DCS-ATC (atc/i18n.py Speech; copied and rewritten with the user's permission, 2026-11-02). Phrases that
carry a clearance, traffic or an order are built here from the locale, word for word, never by the model. A missing
key falls back to English. Adding a language is adding the "atc" section to its locale file.
"""
import re
from functools import lru_cache

from shared.locale import load_locale

FALLBACK = 'en'


def sentence_case(text: str) -> str:
    """Capitalise each sentence ("... runway two seven. Turn right ...")."""
    text = re.sub(r'([.!?]\s+)(\w)', lambda m: m.group(1) + m.group(2).upper(), text)
    return text[:1].upper() + text[1:]


class Speech:
    def __init__(self, language: str):
        base = load_locale(FALLBACK)['atc']
        own = load_locale(language).get('atc') or {}
        self.language = language
        self.phrases = {**base['phrases'], **(own.get('phrases') or {})}
        self.numbers = {**base['numbers'], **(own.get('numbers') or {})}

    def say(self, key: str, **values) -> str:
        return self.phrases[key].format(**values)

    def digits(self, text: str) -> str:
        """'270' -> 'two seven zero'; '29.87' -> 'two niner point eight seven'. Letters ('27L') use the locale's
        words for L/R/C; other characters are dropped."""
        words = []
        for char in str(text):
            if char.isdigit():
                words.append(self.numbers['digits'][int(char)])
            elif char == '.':
                words.append(self.numbers['point'])
            elif char in self.numbers.get('letters', {}):
                words.append(self.numbers['letters'][char])
        return ' '.join(words)

    def runway(self, number: str) -> str:
        return self.say('runway', digits=self.digits(number))

    def distance(self, nm: float) -> str:
        """Miles to one decimal, digit by digit; whole miles without the decimal."""
        return self.digits(f'{nm:.1f}'.removesuffix('.0'))

    def height(self, feet: int) -> str:
        """2500 -> "two thousand five hundred" (heights in thousands and hundreds, not digit by digit)."""
        thousands, rest = divmod(int(feet), 1000)
        template = self.numbers['thousands_half'] if rest == 500 else self.numbers['thousands']
        return template.format(n=self.numbers['digits'][thousands])

    def heading(self, degrees: int) -> str:
        return self.say('heading', digits=self.digits(f'{degrees:03d}'))

    def wind(self, direction_mag: int | None, speed_kt: float | None) -> str:
        if direction_mag is None or speed_kt is None:
            return self.say('wind_calm')
        return self.say('wind', direction=self.digits(f'{direction_mag:03d}'), speed=self.digits(str(round(speed_kt))))

    def pressure(self, key: str, value: str) -> str:
        """key 'qnh' or 'qfe'; value as the radio says it ("1013", "29.92")."""
        return self.say(key, value=self.digits(value))

    def traffic_on_final(self, type_name: str, distance_nm: float) -> str:
        if distance_nm < 1:
            return self.say('traffic_on_short_final', type=type_name)
        return self.say('traffic_on_final', type=type_name, distance=self.distance(distance_nm))

    def callsign(self, callsign: str) -> str:
        """The pilot's callsign as the voice must say it: "Colt 1-1" -> "Colt one one", "101" -> "one zero one"."""
        return re.sub(r'\d+(?:-\d+)*', lambda m: self.digits(m.group(0)), callsign)

    def call(self, pilot: str | None, station: str, text: str) -> str:
        """A whole transmission: "<pilot>, <station>, <text>." with each sentence capitalised."""
        head = f'{self.callsign(pilot)}, ' if pilot else ''
        return sentence_case(f'{head}{station}, {text}.')


@lru_cache(maxsize=None)
def speech(language: str) -> Speech:
    return Speech(language)
