"""Download this project's own Supertonic 3 model and prove it speaks.

Usage: python tools/setup_tts.py [language]   (default: es)
Writes the model to models/supertonic3 and one test file per voice to logs/tts_test/<voice>.wav. Only after every
voice has spoken is models/supertonic3/ready.json written (the voices proven), so an interrupted download or a
broken file is never taken as ready (ready(): checked by tools/setup.py).

Supertonic's own auto_download only fetches the model when an ONNX file is absent: an empty ONNX file or a missing
config or voice is loaded and fails (seen by Astra's audit, 2026-11-10). So when ready() finds the files damaged,
the whole model is downloaded again with Supertonic's download_model (into a temporary folder, moved into place only
when complete).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / 'models' / 'supertonic3'
OUT_DIR = ROOT / 'logs' / 'tts_test'
READY = MODEL_DIR / 'ready.json'
MODEL_NAME = 'supertonic-3'
# The voices the program uses without any choice by the user: shared/agents.py VOICE_DEFAULTS (M1) and default_atc
# (M2); plus every voice settings.json and the agents name (expected_voices()).
DEFAULT_VOICES = ('M1', 'M2')
NEVER_TESTED = 'never tested'


def expected_voices(root: Path = ROOT) -> set[str]:
    names = set(DEFAULT_VOICES)
    paths = [root / 'settings.json', *sorted((root / 'agents').glob('*/agent.json'))]
    for path in paths:
        try:
            name = (json.loads(path.read_text(encoding='utf-8')).get('voice') or {}).get('name')
        except (OSError, ValueError):
            continue
        if name:
            names.add(name)
    return names


def required_files() -> list[Path]:
    """The model files Supertonic loads (its own list, so a new package version stays in step)."""
    from supertonic import config
    return [MODEL_DIR / getattr(config, name) for name in dir(config) if name.endswith('_REL_PATH')]


def damaged() -> str | None:
    """Why the model files cannot be used as they are (None when they can): files missing or empty, a download left
    unfinished, a voice the program uses missing. Repaired only by downloading again."""
    missing = [p.relative_to(MODEL_DIR).as_posix() for p in required_files() if not p.is_file() or not p.stat().st_size]
    if missing:
        return f'model files missing or empty: {", ".join(missing)}'
    if any(MODEL_DIR.rglob('*.incomplete')):
        return 'a download was interrupted'
    voices = MODEL_DIR / 'voice_styles'
    lost = sorted(v for v in expected_voices() if not (voices / f'{v}.json').is_file()
                  or not (voices / f'{v}.json').stat().st_size)
    return f'voices missing: {", ".join(lost)}' if lost else None


def ready() -> str | None:
    """Why the voice model is not ready (None when it is): damaged(), or never proven by a test synthesis."""
    reason = damaged()
    if reason:
        return reason
    if not READY.is_file():
        return NEVER_TESTED
    try:
        voices = json.loads(READY.read_text(encoding='utf-8'))['voices']
    except (ValueError, KeyError):
        return 'ready.json unreadable'
    lost = [v for v in voices if not (MODEL_DIR / 'voice_styles' / f'{v}.json').is_file()]
    return f'voices missing: {", ".join(lost)}' if lost or not voices else None


def main() -> None:
    from supertonic import TTS
    from supertonic.loader import download_model
    language = sys.argv[1] if len(sys.argv) > 1 else 'es'
    text = json.loads((ROOT / 'locales' / f'{language}.json').read_text(encoding='utf-8'))['tts_test']
    READY.unlink(missing_ok=True)
    reason = damaged() if MODEL_DIR.exists() else None   # a first install: Supertonic downloads it below
    if reason:
        print(f'{reason}: downloading the whole model again')
        download_model(MODEL_DIR, MODEL_NAME)
        reason = damaged()
        if reason:
            raise SystemExit(f'model still not usable after a new download: {reason}')
    tts = TTS(model=MODEL_NAME, model_dir=MODEL_DIR, auto_download=True)
    voices = sorted(p.stem for p in (MODEL_DIR / 'voice_styles').glob('*.json'))
    print(f'model: {MODEL_DIR}')
    print(f'voices ({len(voices)}): {", ".join(voices)}')
    if not voices:
        raise SystemExit('no voices downloaded')
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for voice in voices:
        wav, duration = tts.synthesize(text, voice_style=tts.get_voice_style(voice_name=voice), lang=language)
        path = OUT_DIR / f'{voice}.wav'
        tts.save_audio(wav, str(path))
        print(f'{voice}: {path.name} {float(duration[0]) if hasattr(duration, "__len__") else duration:.2f}s')
    READY.write_text(json.dumps({'voices': voices, 'language': language}) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
