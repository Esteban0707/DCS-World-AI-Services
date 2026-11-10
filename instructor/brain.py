"""Brain providers: answer the pilot with the instructor's current context and tools.

Every provider implements ask(question, context, language) -> answer text, raising BrainError when it cannot answer.
Providers ("brain.provider" setting; the GUI will offer the choice of connection and model):
- "chatgpt": the pilot's own ChatGPT plan through Sign in with ChatGPT (instructor/chatgpt_auth.py), OpenAI
  Responses API. Sign in once with utilities/CHATGPT_LOGIN.cmd.
- "none": no model; the instructor says so.
Planned: API key, local OpenAI-compatible endpoint.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from instructor import chatgpt_auth
from shared.agents import fill_mission

INSTRUCTIONS = Path(__file__).resolve().parent / 'brain_instructions.md'
RESPONSES_URL = f'{chatgpt_auth.RESOURCE}/responses'
DEFAULT_MODEL = 'gpt-6.1-terra'      # fast; "gpt-6.1-sol" is the stronger, slower choice
HISTORY_TURNS = 6                    # pilot/instructor exchanges kept as conversation context
OVERHEARD_KEPT = 6                   # lines heard on a shared frequency, said to other agents
HISTORY_ITEMS = 3 * HISTORY_TURNS + OVERHEARD_KEPT   # an exchange: heard, orders given (DONE), said
OVERHEARD = '(Heard on your frequency, not addressed to you: do not answer it.)'
DONE = '(What you did with your tools for that:'   # orders kept in history, so the agent knows what it did
DONE_DETAIL_MAX = 160
MAX_TOOL_ROUNDS = 3                  # model requests that may call tools before it must answer (each costs time)
REQUEST_TIMEOUT_S = 60
# What the instructor says (locale key) when the brain cannot answer, by BrainError.kind.
BRAIN_ERROR_TEXT = {'none': 'no_brain', 'limit': 'brain_limit', 'sign_in': 'brain_sign_in', 'error': 'brain_error'}


class BrainError(Exception):
    """kind: 'limit' (plan usage limit), 'sign_in' (no valid sign-in / permission), 'error' (anything else)."""

    def __init__(self, message: str, kind: str = 'error', code: str = ''):
        super().__init__(message)
        self.kind, self.code = kind, code


class Brain:
    name = 'base'

    def ask(self, question: str, context: dict, language: str) -> str:
        raise NotImplementedError

    def remember(self, heard: str, said: str, done: list[str] | None = None) -> None:
        """An exchange made without the model (a fixed reply, a call of the agent's own), kept for its next turns."""

    def overheard(self, text: str) -> None:
        """Something said on this agent's frequency to someone else (shared channels): heard, not answered."""


class NullBrain(Brain):
    name = 'none'

    def ask(self, question: str, context: dict, language: str) -> str:
        raise BrainError('no brain provider configured', 'none')


# --- Responses API stream ---------------------------------------------------------------------------------------------
def error_kind(code: str, status: int = 0) -> str:
    if code == 'subscription_sharing_usage_limit_exceeded' or status == 429:
        return 'limit'
    if status == 401 or code in ('subscription_sharing_invalid_user', 'invalid_grant', 'plan_usage_disabled',
                                 'subscription_sharing_user_not_eligible', 'refresh_token_expired',
                                 'refresh_token_invalidated', 'refresh_token_reused', 'invalid_refresh_token'):
        return 'sign_in'
    return 'error'


def read_stream(lines) -> dict:
    """The completed response from a Responses event stream (server-sent events, bytes lines): {'text', 'output'}.
    Success only on response.completed; failures raise BrainError with the server's code. On the ChatGPT plan route
    response.completed carries an empty output list (seen live 2026-11-01): the items (function calls, reasoning,
    messages) are taken from the response.output_item.done events."""
    parts, data, items = [], [], []
    for raw in lines:
        line = raw.decode('utf-8').rstrip('\r\n')
        if line.startswith('data:'):
            data.append(line[5:].lstrip())
            continue
        if line or not data:
            continue
        payload, data = '\n'.join(data), []
        if payload == '[DONE]':
            break
        event = json.loads(payload)
        kind = event.get('type', '')
        if kind == 'response.output_text.delta':
            parts.append(event.get('delta', ''))
        elif kind == 'response.output_item.done' and event.get('item'):
            items.append(event['item'])
        elif kind == 'response.completed':
            output = (event.get('response') or {}).get('output') or items
            return {'text': ''.join(parts).strip() or output_text({'output': output}), 'output': output}
        elif kind == 'response.failed':
            error = (event.get('response') or {}).get('error') or {}
            code = error.get('code', 'unknown_error')
            raise BrainError(f'response failed: {code} {error.get("message", "")}'.strip(), error_kind(code), code)
        elif kind == 'response.incomplete':
            reason = ((event.get('response') or {}).get('incomplete_details') or {}).get('reason', 'unknown')
            raise BrainError(f'response incomplete: {reason}', 'error', reason)
        elif kind == 'error':
            code = event.get('code') or (event.get('error') or {}).get('code') or 'stream_error'
            raise BrainError(f'stream error: {code} {event.get("message", "")}'.strip(), error_kind(code), code)
    raise BrainError('stream ended without response.completed', 'error', 'interrupted')


def output_text(response: dict) -> str:
    return ''.join(c.get('text', '') for item in response.get('output', []) if item.get('type') == 'message'
                   for c in item.get('content', []) if c.get('type') == 'output_text').strip()


def replay_items(output: list[dict]) -> list[dict]:
    """A response's output items as next-request input. Nothing is stored server side (store false), so item ids
    cannot be referenced: they are dropped. Reasoning items travel only with their encrypted content."""
    items = []
    for item in output:
        kind = item.get('type')
        if kind == 'function_call':
            items.append({k: item[k] for k in ('type', 'call_id', 'name', 'arguments', 'namespace') if k in item})
        elif kind == 'reasoning' and item.get('encrypted_content'):
            items.append({k: item[k] for k in ('type', 'summary', 'encrypted_content') if k in item})
        elif kind == 'message':
            text = ''.join(c.get('text', '') for c in item.get('content', []) if c.get('type') == 'output_text')
            if text:
                items.append({'role': 'assistant', 'content': text})
    return items


def done_note(name: str, arguments: str, output: str) -> str:
    """One order for the history, e.g. 'pop_smoke {"color": "green"}: ok, green smoke'."""
    try:
        args = json.loads(arguments or '{}')
    except ValueError:
        args = {}
    try:
        result = json.loads(output)
    except ValueError:
        result = {}
    result = result if isinstance(result, dict) else {}
    if 'error' in result:
        outcome = f'error, {result["error"]}'
    else:
        outcome = f'{"ok" if result.get("ok") else "failed"}, {result.get("detail") or ""}'.rstrip(', ')
    text = f'{name.split(".")[-1]}{" " + json.dumps(args, ensure_ascii=False) if args else ""}: {outcome}'
    return text[:DONE_DETAIL_MAX]


class ChatGptBrain(Brain):
    """The pilot's ChatGPT plan (Sign in with ChatGPT). Requests follow the plan-usage rules: store false, stream
    true, history sent in input, instructions instead of system messages, no temperature / max_output_tokens.
    With a toolbox, the model may call its tools for up to MAX_TOOL_ROUNDS requests before answering."""
    name = 'chatgpt'

    def __init__(self, config: dict, radio: str = 'unknown', toolbox=None, folder: Path = chatgpt_auth.FOLDER,
                 opener=urllib.request.urlopen, instructions: str | None = None, appendix=None,
                 history_turns: int = HISTORY_TURNS):
        """instructions: the agent's mission (agents/<id>/mission.md); default: the original instructor's.
        appendix(): text always added after the mission, not editable by the user (a wingman's fixed rules and its
        memory: wingman/memory.py). history_turns: exchanges kept (a wingman keeps more: it flies the whole
        flight with the pilot)."""
        account = chatgpt_auth.load_account(folder, config.get('account', ''))
        if not account:
            raise BrainError('no ChatGPT sign-in saved: run utilities\\CHATGPT_LOGIN.cmd', 'sign_in', 'no_account')
        self.session = chatgpt_auth.Session(account, folder, opener)
        self.model = config.get('model', DEFAULT_MODEL)
        self.reasoning_effort = config.get('reasoning_effort', '')
        self.opener = opener
        self.radio = radio
        self.agent_name = ''   # the agent's name (its identity, editable in the GUI): {name} in the mission
        self.toolbox = toolbox
        self.instructions = instructions if instructions is not None else INSTRUCTIONS.read_text(encoding='utf-8')
        self.appendix = appendix
        self.history_items = 3 * history_turns + OVERHEARD_KEPT
        self.history: list[dict] = []
        self.last_seconds = 0.0
        self.last_tools: list[str] = []
        self.last_calls: list[dict] = []

    def system_prompt(self, context: dict) -> str:
        # No language is imposed: the model answers in the language the pilot speaks.
        guide = f'Tools you can use when you need them:\n{self.toolbox.guide()}' if self.toolbox else ''
        # The callsign comes only from the mission (dcs/instructor_hook.lua), e.g. "Enfield 1-1".
        callsign = context.get('callsign') or 'unknown'
        tower = (context.get('tower') or {}).get('airfield') or 'an unknown airfield'
        values = {'name': self.agent_name, 'radio': self.radio, 'aircraft': context.get('aircraft', 'unknown'),
                  'callsign': callsign, 'tower': tower, 'tool_guide': guide}
        prompt = fill_mission(self.instructions, values)
        if self.appendix:
            prompt += '\n' + fill_mission(self.appendix(), values)
        return prompt

    def request_body(self, context: dict, items: list[dict], tools_allowed: bool = True) -> dict:
        body = {
            'model': self.model,
            'instructions': self.system_prompt(context),
            'input': items,
            'store': False,
            'stream': True,
        }
        if self.toolbox:
            body['tools'] = self.toolbox.tools()
            body['include'] = ['reasoning.encrypted_content']
            if not tools_allowed:
                body['tool_choice'] = 'none'
        if self.reasoning_effort:
            body['reasoning'] = {'effort': self.reasoning_effort}
        return body

    def ask(self, question: str, context: dict, language: str) -> str:
        start = time.monotonic()
        self.last_tools = []
        self.last_calls = []   # each tool call with its arguments and what it answered, for the logs
        done = []   # the orders given (toolbox.remembered), kept in history: read-only tools would only be stale data
        kept = set(getattr(self.toolbox, 'remembered', ()) or ())
        items = [*self.history,
                 {'role': 'developer', 'content': 'Situation now: ' + json.dumps(context)},
                 {'role': 'user', 'content': question}]
        for round_number in range(MAX_TOOL_ROUNDS + 1):
            result = self.request(self.request_body(context, items, round_number < MAX_TOOL_ROUNDS))
            calls = [o for o in result['output'] if o.get('type') == 'function_call']
            if not calls or not self.toolbox:
                break
            items += replay_items(result['output'])
            for call in calls:
                name, arguments = call.get('name', '?'), call.get('arguments', '{}')
                self.last_tools.append(name)
                output = self.toolbox.call(name, arguments)
                self.last_calls.append({'tool': name, 'arguments': arguments, 'output': output})
                items.append({'type': 'function_call_output', 'call_id': call['call_id'], 'output': output})
                if name.split('.')[-1] in kept:
                    done.append(done_note(name, arguments, output))
        answer = result['text']
        if not answer:
            raise BrainError('the model gave no spoken answer', 'error', 'empty_answer')
        self.last_seconds = time.monotonic() - start
        self.remember(question, answer, done)
        return answer

    def remember(self, heard: str, said: str, done: list[str] | None = None) -> None:
        self.history.append({'role': 'user', 'content': heard})
        if done:
            self.history.append({'role': 'developer', 'content': f'{DONE} {"; ".join(done)})'})
        self.history.append({'role': 'assistant', 'content': said})
        self.history = self.history[-self.history_items:]

    def overheard(self, text: str) -> None:
        self.history += [{'role': 'developer', 'content': f'{OVERHEARD} {text}'}]
        self.history = self.history[-self.history_items:]

    def request(self, body: dict) -> dict:
        try:
            token = self.session.access_token()
        except chatgpt_auth.AuthError as exc:
            raise BrainError(str(exc), error_kind(exc.code, exc.status), exc.code) from exc
        request = urllib.request.Request(
            RESPONSES_URL, data=json.dumps(body).encode('utf-8'), method='POST',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
                     'Accept': 'text/event-stream'})
        try:
            with self.opener(request, timeout=REQUEST_TIMEOUT_S) as response:
                return read_stream(response)
        except urllib.error.HTTPError as exc:
            failure = chatgpt_auth.http_failure(exc, RESPONSES_URL)
            raise BrainError(str(failure), error_kind(failure.code, failure.status), failure.code) from exc
        except OSError as exc:
            raise BrainError(f'no connection to OpenAI: {exc}', 'error', 'network') from exc


class UnavailableBrain(Brain):
    """A configured provider that could not start (e.g. no sign-in yet): every question gets its reason."""

    def __init__(self, name: str, error: BrainError):
        self.name, self.error = name, error

    def ask(self, question: str, context: dict, language: str) -> str:
        raise self.error


def instructor_radio(srs: dict) -> str:
    """The instructor's own radio from settings "srs", e.g. "251.0 MHz AM"."""
    channels = zip(srs.get('frequencies_mhz', []), srs.get('modulations', []))
    return ', '.join(f'{freq} MHz {modulation.upper()}' for freq, modulation in channels) or 'unknown'


def create_brain(settings: dict, toolbox=None, radio: str | None = None, instructions: str | None = None,
                 appendix=None, history_turns: int = HISTORY_TURNS) -> Brain:
    """radio and instructions: the agent's own channel ("251.000 AM") and mission; defaults: settings "srs" and the
    original instructor mission. appendix, history_turns: see ChatGptBrain."""
    config = settings.get('brain', {})
    provider = config.get('provider', 'none')
    if provider == 'none':
        return NullBrain()
    if provider == 'chatgpt':
        try:
            return ChatGptBrain(config, radio or instructor_radio(settings.get('srs', {})), toolbox,
                                instructions=instructions, appendix=appendix, history_turns=history_turns)
        except BrainError as exc:
            print(f'[brain] chatgpt unavailable: {exc}')
            return UnavailableBrain(provider, exc)
    raise ValueError(f'unknown brain provider: {provider}')
