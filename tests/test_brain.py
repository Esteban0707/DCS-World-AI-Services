"""Offline tests for the ChatGPT brain: Sign in with ChatGPT (PKCE, callback, ID token, refresh) and the Responses
stream. No network: OpenAI's endpoints are replaced by a fake opener; the ID token is signed with a test RSA key.

Run: python -m unittest discover tests
"""
import email.message
import hashlib
import io
import json
import random
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from instructor import chatgpt_auth as auth
from instructor.brain import MAX_TOOL_ROUNDS, BrainError, ChatGptBrain, NullBrain, read_stream
from instructor.brain_context import flight_context
from instructor.voice_loop import Responder
from shared.cockpit import CockpitState
from shared.locale import load_locale

CLIENT = 'oaiapp_test123'
PAGE = {'done': 'ok', 'failed': 'no', 'close': 'close'}
FORBIDDEN = {'background', 'conversation', 'max_output_tokens', 'max_tool_calls', 'metadata', 'moderation',
             'previous_response_id', 'prompt', 'temperature', 'top_p', 'truncation', 'user', 'safety_identifier'}


# --- a test RSA key and JWT signer --------------------------------------------------------------------------------
def probable_prime(bits: int, rng: random.Random) -> int:
    while True:
        n = rng.getrandbits(bits) | (1 << bits - 1) | 1
        if all(n % p for p in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31)) and miller_rabin(n, rng):
            return n


def miller_rabin(n: int, rng: random.Random, rounds: int = 20) -> bool:
    d, r = n - 1, 0
    while d % 2 == 0:
        d, r = d // 2, r + 1
    for _ in range(rounds):
        x = pow(rng.randrange(2, n - 2), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def make_key():
    rng = random.Random(4711)
    e = 65537
    while True:
        p, q = probable_prime(512, rng), probable_prime(512, rng)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            n = p * q
            return n, e, pow(e, -1, phi)


N, E, D = make_key()
JWK = {'kty': 'RSA', 'kid': 'test-key', 'n': auth.b64url(N.to_bytes(128, 'big')), 'e': auth.b64url(E.to_bytes(3, 'big'))}
JWKS = {'keys': [JWK]}


def jwt(claims: dict, kid: str = 'test-key') -> str:
    head = auth.b64url(json.dumps({'alg': 'RS256', 'kid': kid, 'typ': 'JWT'}).encode())
    body = auth.b64url(json.dumps(claims).encode())
    digest = auth.SHA256_DIGEST_INFO + hashlib.sha256(f'{head}.{body}'.encode()).digest()
    encoded = b'\x00\x01' + b'\xff' * (128 - len(digest) - 3) + b'\x00' + digest
    signature = pow(int.from_bytes(encoded, 'big'), D, N).to_bytes(128, 'big')
    return f'{head}.{body}.{auth.b64url(signature)}'


def claims(nonce='n1', **extra) -> dict:
    return {'iss': auth.ISSUER, 'aud': [CLIENT], 'sub': 'user-sub', 'email': 'pilot@example.com', 'nonce': nonce,
            'iat': int(time.time()), 'exp': int(time.time()) + 3600, **extra}


# --- fake HTTP -------------------------------------------------------------------------------------------------------
class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def http_error(url: str, status: int, body: dict) -> urllib.error.HTTPError:
    headers = email.message.Message()
    headers['x-request-id'] = 'req_1'
    return urllib.error.HTTPError(url, status, 'error', headers, io.BytesIO(json.dumps(body).encode()))


class FakeOpenAI:
    """Routes requests by URL; records each one (URL, form or JSON body, headers)."""

    def __init__(self, routes: dict):
        self.routes, self.calls = routes, []

    def __call__(self, request, timeout=None):
        url = request.full_url.split('?')[0]
        body = request.data.decode() if request.data else ''
        parsed = (json.loads(body) if request.headers.get('Content-type') == 'application/json'
                  else dict(urllib.parse.parse_qsl(body)))
        self.calls.append((url, parsed, dict(request.headers)))
        result = self.routes[url]
        result = result(parsed) if callable(result) else result
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result if isinstance(result, bytes) else json.dumps(result).encode())


def sse(*events) -> bytes:
    return b''.join(f'event: {e["type"]}\ndata: {json.dumps(e)}\n\n'.encode() for e in events)


def saved_account(folder: Path, expires_in: float = 3600, **extra) -> dict:
    record = {'email': 'pilot@example.com', 'issuer': auth.ISSUER, 'subject': 'user-sub', 'client_id': CLIENT,
              'ext_agent_host_id': 'urn:uuid:x', 'id_token': 'idt', 'access_token': 'old-access',
              'refresh_token': 'old-refresh', 'expires_at': time.time() + expires_in, 'earliest_refresh_at': 0,
              'scopes': sorted(auth.SCOPES.split()), 'plan_usage': True, **extra}
    auth.write_private(auth.account_path(folder, CLIENT), record)
    return record


# --- tests -----------------------------------------------------------------------------------------------------------
class IdToken(unittest.TestCase):
    def test_valid_token(self):
        self.assertEqual(auth.validate_id_token(jwt(claims()), CLIENT, 'n1', JWKS)['sub'], 'user-sub')

    def test_rejections(self):
        cases = {
            'nonce': (jwt(claims()), 'other-nonce'),
            'audience': (jwt(claims(aud='oaiapp_other')), 'n1'),
            'expired': (jwt(claims(exp=int(time.time()) - 60)), 'n1'),
            'issuer': (jwt(claims(iss='https://evil.example')), 'n1'),
            'signature': (jwt(claims())[:-6] + 'AAAAAA', 'n1'),
            'unknown key': (jwt(claims(), kid='other'), 'n1'),
        }
        for name, (token, nonce) in cases.items():
            with self.subTest(name), self.assertRaises(auth.AuthError):
                auth.validate_id_token(token, CLIENT, nonce, JWKS)

    def test_tampered_payload(self):
        head, _, signature = jwt(claims()).split('.')
        forged = auth.b64url(json.dumps(claims(sub='someone-else')).encode())
        with self.assertRaises(auth.AuthError):
            auth.validate_id_token(f'{head}.{forged}.{signature}', CLIENT, 'n1', JWKS)


class Authorization(unittest.TestCase):
    def test_pkce(self):
        verifier, challenge = auth.pkce_pair()
        self.assertEqual(challenge, auth.b64url(hashlib.sha256(verifier.encode()).digest()))
        self.assertNotIn('=', challenge)

    def test_new_registration_url(self):
        url = auth.authorize_url('http://127.0.0.1:1455/auth/callback', 's', 'n', 'c', 'urn:uuid:h')
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertTrue(url.startswith(auth.AUTHORIZE_URL + '?'))
        self.assertEqual(query['client_id'], 'dynamic_agent_client')
        self.assertEqual(query['agent_name_hint'], 'DCS-Instructor')
        self.assertEqual(query['ext_agent_host_id'], 'urn:uuid:h')
        self.assertEqual(query['scope'], 'openid profile email offline_access resource.invoke '
                                         'chatgpt.tokens.use.direct')
        self.assertEqual(query['resource'], 'https://api.openai.com/v1')
        self.assertEqual(query['code_challenge_method'], 'S256')
        self.assertNotIn('id_token_hint', query)

    def test_reauthorization_url(self):
        account = {'client_id': CLIENT, 'id_token': 'idt', 'email': 'pilot@example.com'}
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(
            auth.authorize_url('http://127.0.0.1:1455/auth/callback', 's', 'n', 'c', 'urn:uuid:h', account)).query))
        self.assertEqual(query['client_id'], CLIENT)
        self.assertEqual(query['id_token_hint'], 'idt')
        self.assertEqual(query['login_hint'], 'pilot@example.com')
        self.assertNotIn('agent_name_hint', query)

    def test_callback_checks(self):
        ok = {'state': ['s'], 'code': ['c'], 'client_id': [CLIENT]}
        self.assertEqual(auth.read_callback(ok, 's', None), ('c', CLIENT))
        bad = {
            'wrong state': ({**ok, 'state': ['x']}, None),
            'denied': ({'state': ['s'], 'error': ['access_denied']}, None),
            'no issued id': ({'state': ['s'], 'code': ['c']}, None),
            'dynamic id': ({**ok, 'client_id': ['dynamic_agent_client']}, None),
            'other client': ({**ok, 'client_id': ['oaiapp_other']}, {'client_id': CLIENT}),
        }
        for name, (query, account) in bad.items():
            with self.subTest(name), self.assertRaises(auth.AuthError):
                auth.read_callback(query, 's', account)

    def test_host_id_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = auth.host_id(Path(tmp))
            self.assertTrue(first.startswith('urn:uuid:'))
            self.assertEqual(auth.host_id(Path(tmp)), first)


class SignIn(unittest.TestCase):
    def test_full_flow_with_loopback_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pending = {}

            def browser(url):   # stands in for the user approving in the browser
                query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
                pending.update(query)
                back = (f'{query["redirect_uri"]}?code=the-code&state={query["state"]}&client_id={CLIENT}'
                        f'&scope=chatgpt.tokens.use.direct+email+offline_access+openid+profile+resource.invoke')
                threading.Thread(target=lambda: urllib.request.urlopen(back, timeout=5).read(), daemon=True).start()

            def token(form):
                self.assertEqual(form['grant_type'], 'authorization_code')
                self.assertEqual(form['client_id'], CLIENT)
                self.assertEqual(form['redirect_uri'], pending['redirect_uri'])
                self.assertEqual(auth.b64url(hashlib.sha256(form['code_verifier'].encode()).digest()),
                                 pending['code_challenge'])
                return {'access_token': 'access-1', 'refresh_token': 'refresh-1', 'token_type': 'Bearer',
                        'expires_in': 3600, 'scope': auth.SCOPES, 'id_token': jwt(claims(nonce=pending['nonce']))}

            fake = FakeOpenAI({auth.TOKEN_URL: token, auth.JWKS_URL: JWKS})
            record = auth.sign_in(PAGE, folder=folder, open_browser=browser, timeout=10, opener=fake)
            self.assertTrue(record['plan_usage'])
            self.assertEqual(record['client_id'], CLIENT)
            self.assertEqual(auth.load_account(folder)['refresh_token'], 'refresh-1')
            self.assertEqual(pending['ext_agent_host_id'], auth.host_id(folder))

    def test_without_plan_scope_plan_usage_is_off(self):
        record = auth.account_record({'access_token': 'a', 'id_token': 'i', 'scope': 'openid profile email'},
                                     claims(), CLIENT, 'urn:uuid:h')
        self.assertFalse(record['plan_usage'])
        with self.assertRaises(auth.AuthError):
            auth.Session(record).access_token()


class Refresh(unittest.TestCase):
    def test_valid_token_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeOpenAI({})
            session = auth.Session(saved_account(Path(tmp)), Path(tmp), fake)
            self.assertEqual(session.access_token(), 'old-access')
            self.assertEqual(fake.calls, [])

    def test_expiring_token_is_refreshed_and_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeOpenAI({auth.TOKEN_URL: {'access_token': 'new-access', 'refresh_token': 'new-refresh',
                                                'expires_in': 3600, 'scope': auth.SCOPES}})
            session = auth.Session(saved_account(Path(tmp), expires_in=60), Path(tmp), fake)
            self.assertEqual(session.access_token(), 'new-access')
            _, form, _ = fake.calls[0]
            self.assertEqual(form, {'grant_type': 'refresh_token', 'client_id': CLIENT,
                                    'refresh_token': 'old-refresh', 'resource': auth.RESOURCE})
            saved = auth.load_account(Path(tmp))
            self.assertEqual((saved['access_token'], saved['refresh_token']), ('new-access', 'new-refresh'))
            self.assertFalse(auth.account_path(Path(tmp), CLIENT).with_suffix('.lock').exists())

    def test_dead_refresh_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeOpenAI({auth.TOKEN_URL: http_error(auth.TOKEN_URL, 400, {'error': 'invalid_grant'})})
            session = auth.Session(saved_account(Path(tmp), expires_in=-10), Path(tmp), fake)
            with self.assertRaises(auth.AuthError) as caught:
                session.access_token()
            self.assertEqual(caught.exception.code, 'invalid_grant')


class Stream(unittest.TestCase):
    def lines(self, data: bytes):
        return io.BytesIO(data).readlines()

    def test_deltas_until_completed(self):
        data = sse({'type': 'response.created'}, {'type': 'response.output_text.delta', 'delta': 'Loud '},
                   {'type': 'response.output_text.delta', 'delta': 'and clear.'},
                   {'type': 'response.completed', 'response': {}})
        self.assertEqual(read_stream(self.lines(data))['text'], 'Loud and clear.')

    def test_text_from_completed_response(self):
        response = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Roger.'}]}]}
        self.assertEqual(read_stream(self.lines(sse({'type': 'response.completed', 'response': response})))['text'],
                         'Roger.')

    def test_items_from_output_item_events(self):
        # The plan route sends response.completed with an empty output list; items come as output_item.done.
        call = {'type': 'function_call', 'call_id': 'c1', 'name': 'get_location', 'namespace': 'tower',
                'arguments': '{}'}
        data = sse({'type': 'response.output_item.added', 'item': {**call, 'arguments': ''}},
                   {'type': 'response.output_item.done', 'item': call},
                   {'type': 'response.completed', 'response': {'output': []}})
        self.assertEqual(read_stream(self.lines(data)), {'text': '', 'output': [call]})

    def test_usage_limit_after_stream_started(self):
        data = sse({'type': 'response.output_text.delta', 'delta': 'x'},
                   {'type': 'response.failed', 'response': {'error': {
                       'code': 'subscription_sharing_usage_limit_exceeded', 'message': 'limit'}}})
        with self.assertRaises(BrainError) as caught:
            read_stream(self.lines(data))
        self.assertEqual(caught.exception.kind, 'limit')

    def test_interrupted_stream(self):
        with self.assertRaises(BrainError) as caught:
            read_stream(self.lines(sse({'type': 'response.output_text.delta', 'delta': 'x'})))
        self.assertEqual(caught.exception.code, 'interrupted')


class ChatGpt(unittest.TestCase):
    def brain(self, folder: Path, route):
        saved_account(folder)
        fake = FakeOpenAI({'https://api.openai.com/v1/responses': route})
        return ChatGptBrain({'model': 'gpt-6.1-terra'}, '251.0 MHz AM', folder=folder, opener=fake), fake

    def test_request_follows_plan_usage_rules(self):
        answer = sse({'type': 'response.output_text.delta', 'delta': 'Speed is good.'},
                     {'type': 'response.completed', 'response': {}})
        with tempfile.TemporaryDirectory() as tmp:
            brain, fake = self.brain(Path(tmp), answer)
            context = {'aircraft': 'F-5E-3', 'flight': {'indicated_airspeed_kt': 160}}
            self.assertEqual(brain.ask('Is my speed right?', context, 'en'), 'Speed is good.')
            brain.ask('And now?', context, 'en')
            _, body, headers = fake.calls[-1]
            self.assertEqual(headers['Authorization'], 'Bearer old-access')
            self.assertIs(body['store'], False)
            self.assertIs(body['stream'], True)
            self.assertEqual(body['model'], 'gpt-6.1-terra')
            self.assertFalse(FORBIDDEN & body.keys())
            self.assertIn('by radio on\n251.0 MHz AM. The aircraft is F-5E-3, callsign unknown.',
                          body['instructions'])
            self.assertNotIn('system', [item['role'] for item in body['input']])
            self.assertEqual([item['role'] for item in body['input']],
                             ['user', 'assistant', 'developer', 'user'])
            self.assertIn('"indicated_airspeed_kt": 160', body['input'][2]['content'])
            self.assertNotIn('tools', body)   # no toolbox given

    def test_callsign_only_from_the_mission(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved_account(Path(tmp))
            brain = ChatGptBrain({}, folder=Path(tmp))
            self.assertIn('callsign Enfield 1-1.', brain.system_prompt({'aircraft': 'F-5E-3', 'callsign': 'Enfield 1-1'}))
            self.assertIn('callsign unknown.', brain.system_prompt({'aircraft': 'F-5E-3'}))

    def test_tool_round_trip(self):
        call = {'type': 'function_call', 'id': 'fc_1', 'call_id': 'call_1', 'namespace': 'tower',
                'name': 'get_flight_data', 'arguments': '{}'}
        reasoning = {'type': 'reasoning', 'id': 'rs_1', 'summary': [], 'encrypted_content': 'opaque'}
        replies = iter([
            sse({'type': 'response.completed', 'response': {'output': [reasoning, call]}}),
            sse({'type': 'response.output_text.delta', 'delta': 'You are at 160 knots.'},
                {'type': 'response.completed', 'response': {}}),
        ])

        class Tools:
            calls = []

            def tools(self):
                return [{'type': 'namespace', 'name': 'tower', 'description': 'd', 'tools': []}]

            def guide(self):
                return '- get_flight_data: flight data'

            def call(self, name, arguments):
                self.calls.append(name)
                return '{"indicated_airspeed_kt": 160}'

        with tempfile.TemporaryDirectory() as tmp:
            saved_account(Path(tmp))
            fake = FakeOpenAI({'https://api.openai.com/v1/responses': lambda _: next(replies)})
            brain = ChatGptBrain({}, '251.0 MHz AM', Tools(), folder=Path(tmp), opener=fake)
            self.assertEqual(brain.ask('What is my speed?', {'aircraft': 'F-5E-3'}, 'en'), 'You are at 160 knots.')
            self.assertEqual(brain.last_tools, ['get_flight_data'])
            first, second = fake.calls[0][1], fake.calls[1][1]
            self.assertEqual(first['tools'][0]['type'], 'namespace')
            self.assertEqual(first['include'], ['reasoning.encrypted_content'])
            self.assertIn('- get_flight_data: flight data', first['instructions'])
            replayed = second['input'][-3:]
            self.assertEqual(replayed[0], {'type': 'reasoning', 'summary': [], 'encrypted_content': 'opaque'})
            self.assertEqual(replayed[1], {'type': 'function_call', 'call_id': 'call_1', 'name': 'get_flight_data',
                                           'arguments': '{}', 'namespace': 'tower'})
            self.assertEqual(replayed[2], {'type': 'function_call_output', 'call_id': 'call_1',
                                           'output': '{"indicated_airspeed_kt": 160}'})
            # History keeps the exchange only, not the tool traffic.
            self.assertEqual([h['role'] for h in brain.history], ['user', 'assistant'])

    def test_orders_are_kept_in_history(self):
        """Live 2026-11-03: Victor popped smoke, then said it could not confirm its squad had: orders were lost."""
        call = {'type': 'function_call', 'call_id': 'c1', 'namespace': 'squad', 'name': 'pop_smoke',
                'arguments': '{"color": "green"}'}
        replies = iter([sse({'type': 'response.completed', 'response': {'output': [call]}}),
                        sse({'type': 'response.output_text.delta', 'delta': 'Green smoke out.'},
                            {'type': 'response.completed', 'response': {}})])

        class Tools:
            remembered = ('pop_smoke',)

            def tools(self):
                return []

            def guide(self):
                return ''

            def call(self, name, arguments):
                return '{"ok": true, "detail": "green smoke"}'

        with tempfile.TemporaryDirectory() as tmp:
            saved_account(Path(tmp))
            fake = FakeOpenAI({'https://api.openai.com/v1/responses': lambda _: next(replies)})
            brain = ChatGptBrain({}, '255.0 MHz AM', Tools(), folder=Path(tmp), opener=fake)
            brain.ask('Smoke please', {}, 'en')
            self.assertEqual([h['role'] for h in brain.history], ['user', 'developer', 'assistant'])
            self.assertEqual(brain.history[1]['content'],
                             '(What you did with your tools for that: pop_smoke {"color": "green"}: ok, green smoke)')

    def test_tool_rounds_are_limited(self):
        call = {'type': 'function_call', 'call_id': 'c', 'name': 'get_location', 'arguments': '{}'}
        looping = sse({'type': 'response.completed', 'response': {'output': [call]}})

        class Tools:
            def tools(self):
                return []

            def guide(self):
                return ''

            def call(self, name, arguments):
                return '{}'

        with tempfile.TemporaryDirectory() as tmp:
            saved_account(Path(tmp))
            fake = FakeOpenAI({'https://api.openai.com/v1/responses': looping})
            brain = ChatGptBrain({}, toolbox=Tools(), folder=Path(tmp), opener=fake)
            with self.assertRaises(BrainError):
                brain.ask('where am I', {}, 'en')   # never answers: empty_answer after the last round
            self.assertEqual(len(fake.calls), MAX_TOOL_ROUNDS + 1)
            self.assertEqual(fake.calls[-1][1]['tool_choice'], 'none')

    def test_http_errors_are_classified(self):
        url = 'https://api.openai.com/v1/responses'
        cases = {'limit': http_error(url, 429, {'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}),
                 'sign_in': http_error(url, 401, {'detail': 'not accepted'}),
                 'error': http_error(url, 503, {'detail': 'unavailable'})}
        for kind, failure in cases.items():
            with self.subTest(kind), tempfile.TemporaryDirectory() as tmp:
                brain, _ = self.brain(Path(tmp), failure)
                with self.assertRaises(BrainError) as caught:
                    brain.ask('radio check', {}, 'en')
                self.assertEqual(caught.exception.kind, kind)

    def test_no_saved_account(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(BrainError) as caught:
            ChatGptBrain({}, folder=Path(tmp))
        self.assertEqual(caught.exception.kind, 'sign_in')


class Context(unittest.TestCase):
    def test_flight_in_pilot_units(self):
        flight = {'ias_ms': 82.3, 'alt_msl_m': 304.8, 'alt_agl_m': 1.5, 'vvi_ms': -3.81, 'aoa_deg': 7.04,
                  'mach': None}
        self.assertEqual(flight_context(flight),
                         {'indicated_airspeed_kt': 160, 'altitude_msl_ft': 1000, 'altitude_agl_ft': 5,
                          'vertical_speed_fpm': -750, 'angle_of_attack_deg': 7.0})


@mock.patch('instructor.voice_loop.log')   # keep logs/dictation.jsonl free of test entries
class Answers(unittest.TestCase):
    def responder(self, brain):
        said = []
        return Responder(brain, load_locale('en'), 'en', CockpitState(), speak=said.append, show=lambda _: None), said

    def test_without_brain_it_repeats(self, _log):
        responder, said = self.responder(NullBrain())
        responder.answer('radio check')
        self.assertEqual(said, ['radio check'])

    def test_brain_failure_is_spoken_from_the_locale(self, _log):
        class Limited:
            name = 'chatgpt'

            def ask(self, *_):
                raise BrainError('limit', 'limit')

        responder, said = self.responder(Limited())
        responder.answer('radio check')
        self.assertEqual(said, [load_locale('en')['brain_limit']])


if __name__ == '__main__':
    unittest.main()
