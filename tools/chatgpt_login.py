"""Connect the instructor to the pilot's ChatGPT plan (Sign in with ChatGPT), pick the model, and test one answer.

Usage: python tools/chatgpt_login.py [--model SLUG] [--new] [--sign-out] [--ask TEXT]
First run opens the browser: sign in to ChatGPT, name and approve the "DCS-Instructor" agent and allow it to use
your plan. Later runs reuse the saved account (secrets/chatgpt/). Then it lists the models your account offers,
saves the choice to settings.json "brain" (provider chatgpt; default gpt-6.1-terra when offered) and asks one test
question. --new registers another ChatGPT account; --sign-out revokes and clears the saved sign-in.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from instructor import chatgpt_auth                              # noqa: E402
from instructor.brain import DEFAULT_MODEL, BrainError, ChatGptBrain, instructor_radio   # noqa: E402
from shared.locale import load_locale                            # noqa: E402

SETTINGS = ROOT / 'settings.json'
USAGE_URL = 'https://chatgpt.com/settings/usage'


def save_brain(settings: dict, **brain) -> None:
    settings['brain'] = {**settings.get('brain', {}), **brain}
    SETTINGS.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def signed_in_account(settings: dict, locale: dict, new: bool) -> dict:
    account = None if new else chatgpt_auth.load_account(client_id=settings.get('brain', {}).get('account', ''))
    if account and account.get('refresh_token'):
        try:
            chatgpt_auth.Session(account).access_token()
            return chatgpt_auth.load_account(client_id=account['client_id'])
        except chatgpt_auth.AuthError as exc:
            print(f'saved sign-in not usable ({exc}); signing in again')
    print('opening the browser: sign in to ChatGPT and allow DCS-Instructor to use your plan...')
    return chatgpt_auth.sign_in(locale['chatgpt_login'], account)


def choose_model(models: list[dict], wanted: str) -> str:
    slugs = [m['slug'] for m in models]
    for candidate in (wanted, DEFAULT_MODEL):
        if candidate and candidate in slugs:
            return candidate
    if wanted:
        print(f'model "{wanted}" is not in your account catalog; keeping it anyway (the test answer will tell)')
        return wanted
    return slugs[0] if slugs else DEFAULT_MODEL


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--model', default='', help='model slug, e.g. gpt-6.1-terra or gpt-6.1-sol')
    parser.add_argument('--new', action='store_true', help='register another ChatGPT account')
    parser.add_argument('--sign-out', action='store_true', help='revoke and clear the saved sign-in')
    parser.add_argument('--ask', default='', help='test question (default: a radio check in the pilot language)')
    args = parser.parse_args()
    settings = json.loads(SETTINGS.read_text(encoding='utf-8'))
    locale = load_locale(settings['language'])

    if args.sign_out:
        account = chatgpt_auth.load_account(client_id=settings.get('brain', {}).get('account', ''))
        if not account:
            print('no saved ChatGPT sign-in')
            return
        confirmed = chatgpt_auth.sign_out(account)
        save_brain(settings, provider='none')
        print('signed out' + ('' if confirmed else f'; remote revocation NOT confirmed: disconnect the app in '
                                                   f'ChatGPT settings ({USAGE_URL})'))
        return

    first_time = not chatgpt_auth.saved_accounts()
    try:
        account = signed_in_account(settings, locale, args.new)
    except chatgpt_auth.AuthError as exc:
        raise SystemExit(f'sign-in failed: {exc}')
    print(f'signed in: {account["email"] or account["subject"]} (client {account["client_id"]})')
    print(f'granted scopes: {" ".join(account["scopes"])}')
    if not account['plan_usage']:
        save_brain(settings, provider='none', account=account['client_id'])
        raise SystemExit('ChatGPT plan usage was NOT allowed. Run this again and allow DCS-Instructor to use your '
                         'plan (or choose another brain later).')
    if first_time:
        print(f"\nYou're using your ChatGPT plan: the instructor's answers count toward your plan usage.\n"
              f'Manage usage: {USAGE_URL}\n')

    token = chatgpt_auth.Session(account).access_token()
    try:
        models = chatgpt_auth.list_models(token)
    except chatgpt_auth.AuthError as exc:
        print(f'model list unavailable ({exc})')
        models = []
    print('models offered to your account:')
    for m in models:
        print(f'  {m["slug"]:<28} {m["display_name"]}')
    model = choose_model(models, args.model or settings.get('brain', {}).get('model', ''))
    save_brain(settings, provider='chatgpt', model=model, account=account['client_id'])
    print(f'saved to settings.json: brain = chatgpt, model {model}  (Using ChatGPT plan; manage usage: {USAGE_URL})')

    brain = ChatGptBrain(settings['brain'], instructor_radio(settings['srs']))
    question = args.ask or locale['brain_test_question']
    print(f'\nPILOT: {question}')
    try:
        answer = brain.ask(question, {'aircraft': 'F-5E-3', 'flight': {}}, settings['language'])
    except BrainError as exc:
        raise SystemExit(f'test answer failed ({exc.kind}): {exc}')
    print(f'INSTRUCTOR: {answer}\n({brain.model}, {brain.last_seconds:.1f} s)')


if __name__ == '__main__':
    main()
