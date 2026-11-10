"""Sign in with ChatGPT (plan usage for open-source / locally hosted apps): the pilot's own ChatGPT plan pays for the
instructor's model calls, no API key.

Follows OpenAI's documented flow (developers.openai.com/siwc/token-sharing-open-source, read 2026-11-01):
- First sign-in registers a user-defined agent with client_id=dynamic_agent_client, agent_name_hint and a stable
  ext_agent_host_id; the callback returns the issued client_id (oaiapp_...), saved and reused for later sign-ins.
- Authorization code + PKCE (S256) + state + nonce, loopback callback http://127.0.0.1:<port>/auth/callback.
- The ID token is checked (RS256 signature against OpenAI's JWKS, issuer, audience, expiry, nonce) and the granted
  scopes must include chatgpt.tokens.use.direct before any inference.
- Access tokens last 1 h, refresh tokens 30 days (rotating); refreshes are serialized.
Credentials live in secrets/chatgpt/ (never shared, never logged): host.json and accounts/<client_id>.json.
Standard library only.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets as random_tokens
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / 'secrets' / 'chatgpt'

APP_NAME = 'DCS-Instructor'
ISSUER = 'https://auth.openai.com'
AUTHORIZE_URL = 'https://auth.openai.com/api/accounts/authorize'
TOKEN_URL = 'https://auth.openai.com/api/accounts/oauth/token'
JWKS_URL = 'https://auth.openai.com/.well-known/jwks.json'
DISCOVERY_URL = 'https://auth.openai.com/.well-known/openid-configuration'
RESOURCE = 'https://api.openai.com/v1'
NEW_CLIENT = 'dynamic_agent_client'
PLAN_SCOPE = 'chatgpt.tokens.use.direct'
SCOPES = f'openid profile email offline_access resource.invoke {PLAN_SCOPE}'
CALLBACK_PATH = '/auth/callback'
CALLBACK_PORT = 1455            # the documented example port; any free port works on later sign-ins
CLOCK_SKEW_S = 5
REFRESH_BEFORE_S = 300          # refresh when the access token has less than this left
SIGN_IN_TIMEOUT_S = 300
# DER prefix of an RS256 (PKCS#1 v1.5, SHA-256) DigestInfo.
SHA256_DIGEST_INFO = bytes.fromhex('3031300d060960864801650304020105000420')


class AuthError(Exception):
    def __init__(self, message: str, code: str = '', status: int = 0):
        super().__init__(message)
        self.code, self.status = code, status


# --- local storage ------------------------------------------------------------------------------------------------
def write_private(path: Path, data: dict) -> None:
    """Atomic write, owner-only where the OS supports it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    if os.name == 'posix':
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def host_id(folder: Path = FOLDER) -> str:
    """This PC's stable, opaque ext_agent_host_id (urn:uuid, created once before the first sign-in)."""
    path = folder / 'host.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))['ext_agent_host_id']
    value = f'urn:uuid:{uuid.uuid4()}'
    write_private(path, {'ext_agent_host_id': value})
    return value


def account_path(folder: Path, client_id: str) -> Path:
    return folder / 'accounts' / f'{client_id}.json'


def saved_accounts(folder: Path = FOLDER) -> list[dict]:
    return [json.loads(p.read_text(encoding='utf-8')) for p in sorted((folder / 'accounts').glob('*.json'))]


def load_account(folder: Path = FOLDER, client_id: str = '') -> dict | None:
    """The saved account with this issued client_id, or the only saved one when none is named."""
    if client_id:
        path = account_path(folder, client_id)
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    accounts = saved_accounts(folder)
    return accounts[0] if len(accounts) == 1 else None


# --- OAuth pieces ---------------------------------------------------------------------------------------------------
def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def pkce_pair() -> tuple[str, str]:
    verifier = b64url(random_tokens.token_bytes(48))
    return verifier, b64url(hashlib.sha256(verifier.encode('ascii')).digest())


def authorize_url(redirect_uri: str, state: str, nonce: str, challenge: str, host: str,
                  account: dict | None = None) -> str:
    params = {
        'client_id': account['client_id'] if account else NEW_CLIENT,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'scope': SCOPES,
        'resource': RESOURCE,
        'state': state,
        'nonce': nonce,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
        'ext_agent_host_id': host,
    }
    if account:
        # Reauthorization of a saved account: no agent_name_hint; the hints pick the same ChatGPT account.
        if account.get('id_token'):
            params['id_token_hint'] = account['id_token']
        if account.get('email'):
            params['login_hint'] = account['email']
    else:
        params['agent_name_hint'] = APP_NAME
    return f'{AUTHORIZE_URL}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}'


def read_callback(query: dict, state: str, account: dict | None) -> tuple[str, str]:
    """(code, issued client_id) from the loopback callback, after checking state and errors."""
    def one(name):
        values = query.get(name) or ['']
        return values[0]

    if not hmac.compare_digest(one('state'), state):
        raise AuthError('sign-in callback with a wrong state (not from this attempt)', 'bad_state')
    if one('error'):
        raise AuthError(f'sign-in not completed: {one("error")} {one("error_description")}'.strip(), one('error'))
    code = one('code')
    if not code:
        raise AuthError('sign-in callback without an authorization code', 'no_code')
    returned = one('client_id')
    if account:
        if returned and returned != account['client_id']:
            raise AuthError('sign-in returned a different client than the selected account', 'client_mismatch')
        return code, account['client_id']
    if not returned or returned == NEW_CLIENT:
        raise AuthError('registration incomplete: the callback carried no issued client_id', 'no_client_id')
    return code, returned


def post_form(url: str, fields: dict, opener=urllib.request.urlopen) -> dict:
    request = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode('ascii'), method='POST',
                                     headers={'Content-Type': 'application/x-www-form-urlencoded',
                                              'Accept': 'application/json'})
    try:
        with opener(request, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        raise http_failure(exc, url) from exc


def http_failure(exc: urllib.error.HTTPError, url: str) -> AuthError:
    raw = exc.read().decode('utf-8', 'replace')
    try:
        body = json.loads(raw)
    except ValueError:
        body = {}
    error = body.get('error')
    if isinstance(error, dict):
        code, text = error.get('code') or error.get('type') or '', error.get('message', '')
    else:
        code, text = error or '', body.get('error_description') or body.get('detail') or raw[:200]
    request_id = exc.headers.get('x-request-id', '') if exc.headers else ''
    return AuthError(f'{url} -> HTTP {exc.code} {code} {text} (request {request_id})'.strip(), code, exc.code)


def get_json(url: str, token: str = '', opener=urllib.request.urlopen) -> dict:
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        with opener(urllib.request.Request(url, headers=headers), timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise http_failure(exc, url) from exc


# --- ID token -------------------------------------------------------------------------------------------------------
def rs256_valid(signing_input: bytes, signature: bytes, jwk: dict) -> bool:
    """RSASSA-PKCS1-v1_5 / SHA-256 verification: the whole encoded message must match exactly."""
    n = int.from_bytes(b64url_decode(jwk['n']), 'big')
    e = int.from_bytes(b64url_decode(jwk['e']), 'big')
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    encoded = pow(int.from_bytes(signature, 'big'), e, n).to_bytes(size, 'big')
    digest = SHA256_DIGEST_INFO + hashlib.sha256(signing_input).digest()
    expected = b'\x00\x01' + b'\xff' * (size - len(digest) - 3) + b'\x00' + digest
    return hmac.compare_digest(encoded, expected)


def validate_id_token(id_token: str, client_id: str, nonce: str | None, jwks: dict, now: float | None = None) -> dict:
    """The verified claims; nonce None skips the nonce check (refresh responses)."""
    try:
        header_b64, payload_b64, signature_b64 = id_token.split('.')
        header = json.loads(b64url_decode(header_b64))
        claims = json.loads(b64url_decode(payload_b64))
        signature = b64url_decode(signature_b64)
    except ValueError as exc:
        raise AuthError('malformed ID token', 'bad_id_token') from exc
    if header.get('alg') != 'RS256':
        raise AuthError(f'unexpected ID token algorithm {header.get("alg")}', 'bad_id_token')
    keys = [k for k in jwks.get('keys', []) if k.get('kid') == header.get('kid') and k.get('kty') == 'RSA']
    if not keys or not rs256_valid(f'{header_b64}.{payload_b64}'.encode('ascii'), signature, keys[0]):
        raise AuthError('ID token signature not valid', 'bad_id_token')
    now = time.time() if now is None else now
    audience = claims.get('aud')
    audiences = audience if isinstance(audience, list) else [audience]
    expiry = claims.get('exp')
    checks = {
        'issuer': claims.get('iss') == ISSUER,
        'audience': client_id in audiences,
        'expired': isinstance(expiry, (int, float)) and expiry > now - CLOCK_SKEW_S,
        'nonce': nonce is None or hmac.compare_digest(str(claims.get('nonce', '')), nonce),
        'subject': isinstance(claims.get('sub'), str) and bool(claims['sub']),
    }
    problems = [name for name, ok in checks.items() if not ok]
    if problems:
        raise AuthError(f'ID token rejected: {", ".join(problems)}', 'bad_id_token')
    return claims


# --- token responses ------------------------------------------------------------------------------------------------
def account_record(tokens: dict, claims: dict, client_id: str, host: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    scopes = sorted(tokens.get('scope', '').split())
    return {
        'email': claims.get('email', ''),
        'name': claims.get('name', ''),
        'issuer': claims['iss'],
        'subject': claims['sub'],
        'client_id': client_id,
        'ext_agent_host_id': host,
        'id_token': tokens['id_token'],
        'access_token': tokens['access_token'],
        'refresh_token': tokens.get('refresh_token', ''),
        'token_type': tokens.get('token_type', 'Bearer'),
        'expires_in': tokens.get('expires_in', 3600),
        'expires_at': now + tokens.get('expires_in', 3600),
        'earliest_refresh_at': tokens.get('earliest_refresh_at', 0),
        'scopes': scopes,
        'plan_usage': PLAN_SCOPE in scopes,
        'saved_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
    }


class CallbackServer(HTTPServer):
    """One loopback listener for one sign-in attempt; keeps the first callback on the right path."""

    def __init__(self, port: int, page: dict):
        self.query, self.page = None, page
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                if url.path != CALLBACK_PATH:
                    self.send_error(404)
                    return
                server.query = urllib.parse.parse_qs(url.query)
                ok = 'code' in server.query
                body = (f'<!doctype html><meta charset="utf-8"><title>{APP_NAME}</title>'
                        f'<body style="font-family:sans-serif;background:#111;color:#eee;padding:3em">'
                        f'<h2>{server.page["done" if ok else "failed"]}</h2><p>{server.page["close"]}</p>').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass   # the callback URL carries the authorization code: never log it

        try:
            super().__init__(('127.0.0.1', port), Handler)
        except OSError:
            super().__init__(('127.0.0.1', 0), Handler)   # 1455 busy: only the port may change
        self.timeout = 1

    @property
    def redirect_uri(self) -> str:
        return f'http://127.0.0.1:{self.server_address[1]}{CALLBACK_PATH}'


def sign_in(page: dict, account: dict | None = None, folder: Path = FOLDER, open_browser=webbrowser.open,
            timeout: float = SIGN_IN_TIMEOUT_S, opener=urllib.request.urlopen) -> dict:
    """Browser sign-in (new registration, or reauthorization of a saved account); saves and returns the account.
    page: the callback page texts (locale "chatgpt_login")."""
    host = host_id(folder)
    state, nonce = random_tokens.token_urlsafe(32), random_tokens.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    server = CallbackServer(CALLBACK_PORT, page)
    try:
        url = authorize_url(server.redirect_uri, state, nonce, challenge, host, account)
        open_browser(url)
        deadline = time.monotonic() + timeout
        while server.query is None:
            if time.monotonic() > deadline:
                raise AuthError('sign-in timed out (no answer from the browser)', 'timeout')
            server.handle_request()
        code, client_id = read_callback(server.query, state, account)
        redirect_uri = server.redirect_uri
    finally:
        server.server_close()
    tokens = post_form(TOKEN_URL, {'grant_type': 'authorization_code', 'client_id': client_id, 'code': code,
                                   'code_verifier': verifier, 'redirect_uri': redirect_uri, 'resource': RESOURCE},
                       opener)
    if not tokens.get('id_token') or not tokens.get('access_token'):
        raise AuthError('token response without ID or access token', 'bad_token_response')
    claims = validate_id_token(tokens['id_token'], client_id, nonce, get_json(JWKS_URL, opener=opener))
    if account and claims['sub'] != account['subject']:
        raise AuthError('signed in as a different ChatGPT account than the selected one', 'account_mismatch')
    record = account_record(tokens, claims, client_id, host)
    write_private(account_path(folder, client_id), record)
    return record


class Session:
    """Access tokens for one saved account, refreshed when close to expiry. Refreshes are serialized within this
    process (lock) and across processes (lock file, then the saved record is re-read)."""

    def __init__(self, account: dict, folder: Path = FOLDER, opener=urllib.request.urlopen):
        self.account, self.folder, self.opener = account, folder, opener
        self.lock = threading.Lock()

    @property
    def path(self) -> Path:
        return account_path(self.folder, self.account['client_id'])

    def access_token(self, now: float | None = None) -> str:
        if not self.account.get('plan_usage'):
            raise AuthError('ChatGPT plan usage was not granted for this account (sign in again and allow it)',
                            'plan_usage_disabled')
        with self.lock:
            now = time.time() if now is None else now
            if self.account['expires_at'] - now > REFRESH_BEFORE_S:
                return self.account['access_token']
            with FileLock(self.path.with_suffix('.lock')):
                if self.path.exists():
                    self.account = json.loads(self.path.read_text(encoding='utf-8'))
                if self.account['expires_at'] - now <= REFRESH_BEFORE_S:
                    self._refresh(now)
            return self.account['access_token']

    def _refresh(self, now: float) -> None:
        if not self.account.get('refresh_token'):
            raise AuthError('no refresh token saved: sign in again', 'invalid_grant')
        if now < self.account.get('earliest_refresh_at', 0) and self.account['expires_at'] > now:
            return   # the server asked us not to refresh yet; the current token is still valid
        tokens = post_form(TOKEN_URL, {'grant_type': 'refresh_token', 'client_id': self.account['client_id'],
                                       'refresh_token': self.account['refresh_token'], 'resource': RESOURCE},
                           self.opener)
        if not tokens.get('access_token'):
            raise AuthError('refresh response without an access token', 'bad_token_response')
        scopes = sorted(tokens.get('scope', ' '.join(self.account['scopes'])).split())
        self.account.update({
            'access_token': tokens['access_token'],
            'refresh_token': tokens.get('refresh_token', self.account['refresh_token']),
            'expires_in': tokens.get('expires_in', 3600),
            'expires_at': now + tokens.get('expires_in', 3600),
            'earliest_refresh_at': tokens.get('earliest_refresh_at', 0),
            'scopes': scopes,
            'plan_usage': PLAN_SCOPE in scopes,
            'saved_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
        })
        if tokens.get('id_token'):
            self.account['id_token'] = tokens['id_token']
        write_private(self.path, self.account)


class FileLock:
    def __init__(self, path: Path, timeout: float = 30):
        self.path, self.timeout = path, timeout

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    self.path.unlink(missing_ok=True)   # left behind by a process that died mid-refresh
                    deadline = time.monotonic() + self.timeout
                    continue
                time.sleep(0.05)

    def __exit__(self, *_):
        self.path.unlink(missing_ok=True)


def sign_out(account: dict, folder: Path = FOLDER, opener=urllib.request.urlopen) -> bool:
    """Revoke the refresh token, then clear this account's tokens (the account/client mapping is kept for a later
    sign-in). Returns whether the remote revocation was confirmed."""
    confirmed = False
    if account.get('refresh_token'):
        try:
            endpoint = get_json(DISCOVERY_URL, opener=opener)['revocation_endpoint']
            post_form(endpoint, {'token': account['refresh_token'], 'token_type_hint': 'refresh_token',
                                 'client_id': account['client_id']}, opener)
            confirmed = True
        except (AuthError, OSError, KeyError):
            confirmed = False
    cleared = {**account, 'id_token': '', 'access_token': '', 'refresh_token': '', 'expires_at': 0,
               'plan_usage': False}
    write_private(account_path(folder, account['client_id']), cleared)
    return confirmed


def list_models(token: str, opener=urllib.request.urlopen) -> list[dict]:
    """The signed-in account's model catalog, models meant for display, in the server's order."""
    data = get_json(f'{RESOURCE}/models', token, opener)
    return [{'slug': m['slug'], 'display_name': m.get('display_name', m['slug'])}
            for m in data.get('models', []) if m.get('visibility') == 'list']
