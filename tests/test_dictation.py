"""Offline tests for dictation: the Google request/response, and push-to-talk -> recorder -> recognizer -> text.

Run: python -m unittest discover tests
"""
import base64
import io
import json
import socket
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import numpy as np

from client import dictation as dictation_module
from client.chrome_dictation import ChromeDictation
from client.dictation import RecordedDictation, create_dictation
from client.speech import GoogleSpeech, SpeechError, create_recognizer
from shared.locale import load_locale


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Google(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict('os.environ', {'GOOGLE_SPEECH_API_KEY': 'test-key'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_request_body(self):
        speech = GoogleSpeech({'language': 'es-US', 'model': 'latest_short'}, hints=['ready', 'repeat'])
        samples = np.array([0, 1000, -1000], dtype=np.int16)
        body = speech.request_body(samples, 48000)
        self.assertEqual(body['config']['sampleRateHertz'], 48000)
        self.assertEqual(body['config']['languageCode'], 'es-US')
        self.assertEqual(body['config']['model'], 'latest_short')
        self.assertEqual(body['config']['speechContexts'], [{'phrases': ['ready', 'repeat']}])
        self.assertEqual(np.frombuffer(base64.b64decode(body['audio']['content']), '<i2').tolist(), [0, 1000, -1000])

    def test_transcript_and_key_header(self):
        reply = {'results': [{'alternatives': [{'transcript': 'ready', 'confidence': 0.9}]},
                             {'alternatives': [{'transcript': ' what does the damper do'}]}]}
        speech = create_recognizer({'speech': {'provider': 'google_cloud'}})
        with mock.patch('urllib.request.urlopen', return_value=FakeResponse(json.dumps(reply).encode())) as urlopen:
            self.assertEqual(speech.recognize(np.zeros(100, np.int16), 16000), 'ready what does the damper do')
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header('X-goog-api-key'), 'test-key')
        self.assertNotIn('test-key', request.full_url)

    def test_silence_is_empty(self):
        speech = GoogleSpeech({})
        with mock.patch('urllib.request.urlopen', return_value=FakeResponse(b'{}')):
            self.assertEqual(speech.recognize(np.zeros(100, np.int16), 16000), '')

    def test_missing_key(self):
        with mock.patch.dict('os.environ', {'GOOGLE_SPEECH_API_KEY': ''}), \
                mock.patch('client.speech.GOOGLE_KEY_FILE', mock.Mock(exists=lambda: False)):
            with self.assertRaises(SpeechError):
                GoogleSpeech({})


class FakeButton:
    def __init__(self, *_):
        self.down = False

    def held(self):
        return self.down


class FakeRecorder:
    rate = 1000
    device = {'name': 'fake mic'}

    def __init__(self, *_):
        self.started = None

    def start(self):
        self.started = time.time()

    def stop(self):
        return np.zeros(int((time.time() - self.started) * self.rate), dtype=np.int16)


class FakeRecognizer:
    name = 'fake'

    def recognize(self, samples, rate):
        return f'{len(samples) / rate:.0f} s'


class PushToTalk(unittest.TestCase):
    @mock.patch('client.dictation.LOG', Path(tempfile.mkdtemp()) / 'dictation.jsonl')
    def test_hold_speak_release(self):
        texts, statuses = [], []
        dictation = RecordedDictation(FakeButton(), FakeRecorder(), FakeRecognizer(),
                                      lambda text, agent: texts.append(text),
                                      lambda kind, _: statuses.append(kind))
        dictation.start()
        dictation.button.down = True          # a quick tap is ignored
        time.sleep(0.1)
        dictation.button.down = False
        time.sleep(0.1)
        dictation.button.down = True          # a real phrase is recognized after release
        time.sleep(1.0)
        dictation.button.down = False
        for _ in range(100):
            if texts:
                break
            time.sleep(0.02)
        self.assertEqual(texts, ['1 s'])
        self.assertEqual(statuses[:3], ['listening', 'too_short', 'listening'])

    def test_providers(self):
        locale = load_locale('es')
        self.assertIsNone(create_dictation({'speech': {'provider': 'none'}}, locale, print))
        settings = {'speech': {'provider': 'google_cloud'}, 'ptt': {'joystick': 'x', 'button': 1}}
        with mock.patch.object(dictation_module, 'RadioButtons', FakeButton), \
                mock.patch.object(dictation_module, 'Recorder', FakeRecorder), \
                mock.patch.dict('os.environ', {'GOOGLE_SPEECH_API_KEY': 'k'}):
            self.assertEqual(create_dictation(settings, locale, print).name, 'google_cloud')


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Chrome(unittest.TestCase):
    """The local page server, without opening Chrome: PTT events out, heard text in."""

    def setUp(self):
        log_patch = mock.patch('client.dictation.LOG', Path(tempfile.mkdtemp()) / 'dictation.jsonl')
        log_patch.start()
        self.addCleanup(log_patch.stop)
        self.texts = []
        self.button = FakeButton()
        self.port = free_port()
        self.dictation = ChromeDictation(self.button, {'language': 'es-US', 'port': self.port}, 'Headset',
                                         load_locale('es')['dictation_page'],
                                         lambda text, agent: self.texts.append(text), lambda *_: None)
        self.dictation.start(open_window=False)
        self.addCleanup(self.dictation.server.server_close)
        self.addCleanup(self.dictation.server.shutdown)
        self.base = f'http://127.0.0.1:{self.port}'

    def test_page_and_status(self):
        with urllib.request.urlopen(self.base + '/') as page:
            self.assertIn(b'webkitSpeechRecognition', page.read())
        with urllib.request.urlopen(self.base + '/status') as status:
            data = json.loads(status.read())
        self.assertEqual((data['language'], data['microphone']), ('es-US', 'Headset'))
        self.assertIn('ready', data['text'])

    def test_ptt_events(self):
        stream = urllib.request.urlopen(self.base + '/events', timeout=5)
        self.addCleanup(stream.close)
        time.sleep(0.1)
        self.button.down = True
        events = []
        while len(events) < 1:
            line = stream.readline().decode()
            if line.startswith('data: '):
                events.append(json.loads(line[6:]))
        self.assertEqual(events[0], {'event': 'ptt', 'down': True, 'press': 1, 'lang': 'es-US'})

    def test_heard_text(self):
        with mock.patch('client.dictation.LOG', Path(tempfile.mkdtemp()) / 'dictation.jsonl'):
            body = json.dumps({'text': ' ready ', 'info': {'held_ms': 900}}).encode()
            urllib.request.urlopen(urllib.request.Request(self.base + '/heard', data=body, method='POST'))
            for _ in range(50):
                if self.texts:
                    break
                time.sleep(0.02)
        self.assertEqual(self.texts, ['ready'])

    def test_foreign_origin_refused(self):
        request = urllib.request.Request(self.base + '/heard', data=b'{"text": "x"}', method='POST',
                                         headers={'Origin': 'http://evil.example'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 403)

    def test_locales_have_the_same_page_texts(self):
        self.assertEqual(set(load_locale('es')['dictation_page']), set(load_locale('en')['dictation_page']))


if __name__ == '__main__':
    unittest.main()
