"""The single entry point for what the pilot says (dictation later, typed text for now).

Short lesson commands ("continue", "repeat", in the pilot's language from the locale file) are handled by the
lesson; anything else is a free question for the brain.
"""
from shared.locale import normalize

MAX_COMMAND_WORDS = 3


def classify(text: str, locale: dict) -> str:
    """'continue', 'repeat' or 'question'."""
    spoken = normalize(text)
    if len(spoken.split()) <= MAX_COMMAND_WORDS:
        for intent, phrases in locale.get('intents', {}).items():
            if any(spoken == normalize(p) or spoken.startswith(normalize(p) + ' ') for p in phrases):
                return intent
    return 'question'
