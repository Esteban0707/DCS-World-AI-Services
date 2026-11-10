"""Live voice loop check in a console: hold the push-to-talk button, speak, and the instructor voice repeats it.

Usage: python tools/dictation_test.py [--no-voice] [--brain]
Uses settings.json "ptt", "mic", "speech" and "srs" (provider chrome opens its small Chrome dictation window); the
repeat goes out on the configured voice output (SRS or speakers). --brain answers with the configured brain instead
of repeating (utilities/BRAIN_TEST.cmd). Without a console: DCS-Instructor.bat.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from instructor.voice_loop import load_settings, run   # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--no-voice', action='store_true', help='only print what was understood')
    parser.add_argument('--brain', action='store_true', help='answer with the configured brain instead of repeating')
    args = parser.parse_args()
    run(load_settings(), voice=not args.no_voice, repeat=not args.brain)


if __name__ == '__main__':
    main()
