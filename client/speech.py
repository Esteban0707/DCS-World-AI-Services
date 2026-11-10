"""Speech recognition of recorded audio, pluggable: audio samples in, text out (client/dictation.py uses it).

Providers ("speech.provider" setting):
- "google_cloud": Google Cloud Speech-to-Text v1 REST (speech:recognize) with an API key; standard library only.
  The key comes from the GOOGLE_SPEECH_API_KEY environment variable or the file secrets/google_speech_api_key.txt
  (never from settings.json, which is shared). Pricing checked 2026-11-01: first 60 min/month free, then about
  $0.016/min (cloud.google.com/speech-to-text/pricing).
- "none": nothing recognized (dictation off).
"""
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
GOOGLE_URL = 'https://speech.googleapis.com/v1/speech:recognize'
GOOGLE_KEY_FILE = ROOT / 'secrets' / 'google_speech_api_key.txt'


class SpeechError(Exception):
    pass


class NullSpeech:
    name = 'none'

    def recognize(self, samples: np.ndarray, rate: int) -> str:
        return ''


class GoogleSpeech:
    name = 'google_cloud'

    def __init__(self, config: dict, hints: list[str] = ()):
        self.key = os.environ.get('GOOGLE_SPEECH_API_KEY') or (
            GOOGLE_KEY_FILE.read_text(encoding='utf-8').strip() if GOOGLE_KEY_FILE.exists() else '')
        if not self.key:
            raise SpeechError(f'no Google Speech API key: set GOOGLE_SPEECH_API_KEY or write it to {GOOGLE_KEY_FILE}')
        self.language = config.get('language', 'es-US')
        self.model = config.get('model', '')
        self.hints = list(hints)
        self.timeout = config.get('timeout_s', 15)

    def request_body(self, samples: np.ndarray, rate: int) -> dict:
        settings = {'encoding': 'LINEAR16', 'sampleRateHertz': rate, 'languageCode': self.language,
                    'enableAutomaticPunctuation': True}
        if self.model:
            settings['model'] = self.model
        if self.hints:
            settings['speechContexts'] = [{'phrases': self.hints}]
        audio = base64.b64encode(samples.astype('<i2').tobytes()).decode('ascii')
        return {'config': settings, 'audio': {'content': audio}}

    def recognize(self, samples: np.ndarray, rate: int) -> str:
        body = json.dumps(self.request_body(samples, rate)).encode('utf-8')
        request = urllib.request.Request(GOOGLE_URL, data=body,
                                         headers={'Content-Type': 'application/json', 'X-Goog-Api-Key': self.key})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')
            try:
                detail = json.loads(detail)['error']['message']
            except (ValueError, KeyError):
                pass
            raise SpeechError(f'Google Speech HTTP {exc.code}: {detail}') from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SpeechError(f'Google Speech unreachable: {exc}') from exc
        return ' '.join(r['alternatives'][0]['transcript'].strip()
                        for r in result.get('results', []) if r.get('alternatives')).strip()


def create_recognizer(settings: dict, hints: list[str] = ()):
    config = settings.get('speech', {})
    provider = config.get('provider', 'none')
    if provider == 'google_cloud':
        return GoogleSpeech(config, hints)
    if provider == 'none':
        return NullSpeech()
    raise SpeechError(f'unknown speech provider "{provider}"')
