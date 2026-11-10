# DCS World AI Services

> **Alpha, in active development.** It works and has been flown many times, but expect rough edges, changes between
> versions and features marked "not flown yet". Bug reports with a log are very welcome.

AI characters for **DCS World** that you talk to **on the radio**, through **SRS**. Tune a frequency, hold your
push-to-talk, speak naturally, and the agent on that frequency answers with its own voice, sees what it would really
see, and acts in the mission.

- **Instructor**: an instructor in the control tower. Like a real one, it never sees inside your cockpit: it knows
  what the tower's radar and window show (position, altitude, speed; gear, flaps, lights; where you are on the field)
  and asks you for the rest. It teaches from your aircraft's own DCS manuals, with page numbers (F-5E-3 for now).
- **ATC**: a tower on every airfield's real frequency: taxi, takeoff, landing and vectors, with wind, QNH, runway in
  use and traffic. It sees you the way a tower and its radar would (line of sight, range, aircraft size), nothing more.
- **Wingmen**: your own AI flight (airplane or helicopter), started cold, hot, on the runway or in the air by radio,
  with state-aware orders: join up, attack, land at a point, carry troops.
- **Ground and sea units**: infantry squads, vehicles (pick-up and drop by helicopter), artillery fire missions,
  ships and the carrier, each on its own frequency.
- **Agents talk to each other** over an internal net, and react to mission events through **Triggers** you define in
  the GUI, without touching the Mission Editor.

What the agents know about aircraft, airfields and procedures comes from **DCS's own files**, not from invention.
None of it ships with this project: `SETUP.cmd` reads it from **your** DCS install (see
[Data from DCS](#data-from-dcs)).

## What you need

| | |
|---|---|
| Windows 10 or 11 | the program uses Windows audio, joystick and window APIs |
| [DCS World](https://www.digitalcombatsimulator.com/) | any edition; the instructor's manuals are set up for the F-5E-3 only so far |
| [SRS (DCS-SimpleRadio-Standalone)](https://github.com/ciribob/DCS-SimpleRadio-Standalone) | the agents talk on SRS |
| [Google Chrome](https://www.google.com/chrome/) | speech-to-text (dictation window) |
| A ChatGPT account | the agents' brain, through OpenAI's *Sign in with ChatGPT*; answers count toward your plan usage |
| A joystick button | the push-to-talk for the agents (not the same button as your SRS PTT) |
| About 1 GB of disk | own Python, packages and voice model, all inside this folder |

## Install

1. Download the latest **Release** zip (or the green **Code** button > **Download ZIP**) and unzip it where it will
   stay, e.g. `C:\DCS-World-AI-Services`. DCS loads the export from that folder: if you move it later, run `SETUP.cmd`
   again.
2. Double-click **`SETUP.cmd`**. It:
   - downloads its own Python 3.12 (checked by SHA-256) and the pinned packages, all inside this folder;
   - downloads the voice model (Supertonic 3) from Hugging Face;
   - finds your DCS install and builds from it what the agents need: cockpit tables, training lessons and manuals of
     the aircraft you own, airfields and frequencies of the maps you own, cloud presets, aircraft radar sizes;
   - **asks first**, then installs one line in `Saved Games\DCS\Scripts\Export.lua` (a backup is made, SRS's line is
     kept) and one hook file in `Saved Games\DCS\Scripts\Hooks\`;
   - **asks first** (optional, off by default) about two patches inside the DCS install, see below;
   - lets you press your push-to-talk button and sign in with ChatGPT.
3. At the end it says **Setup complete** or **Setup INCOMPLETE** with what failed, and lists what you still have to
   do before flying (a step you skipped, SRS or Chrome not found). Run `SETUP.cmd` again whenever you like: it checks
   every step and only redoes what is missing or was left unfinished (a cut download, an interrupted build).
   Aircraft you do not own in DCS are skipped.

The language follows Windows: Spanish if Windows is in Spanish, English otherwise (change `language` in
`settings.json` or in the GUI).

## Fly

1. Start DCS yourself.
2. Double-click **`DCS-Instructor.bat`**. It checks the DCS export and hook, opens SRS (server and client), the GUI
   and the dictation window.
3. Connect the SRS client, get in your aircraft, tune the radio to an agent's frequency (the instructor's default is
   **251.0 AM**; the ATC listens on every airfield's real frequency), hold your push-to-talk and speak.
4. The GUI (`Services`, `Triggers`, ...) shows who is listening on which frequency and what they said. Close the GUI
   window to stop everything.

Every flight is recorded in `logs/sessions/` (the "black box"): attach it when you report a problem.

## Tools

In `utilities/`:

| File | What it does |
|---|---|
| `PTT_PICK.cmd` / `PTT_PICK_RADIO2.cmd` | choose the push-to-talk button (radio 2: a second button) |
| `CHATGPT_LOGIN.cmd` | sign in, choose the model, test one answer; `--sign-out` revokes the sign-in |
| `DCS_PATCHES.cmd status / update / remove` | the optional DCS install patches |
| `LIBRARY_BUILD.cmd` | rebuild the F-5E-3 library, e.g. after adding your own `.pdf`/`.md`/`.txt` to `modules\F-5E-3\documents\` |
| `SRS_TEST.cmd`, `DICTATION_TEST.cmd`, `BRAIN_TEST.cmd`, `COCKPIT_TEST.cmd`, `POSITIONS_CHECK.cmd` | diagnostics with a console |

## What changes on your PC

- **Inside this folder**: Python, packages, voice model, the data built from your DCS install, your settings
  (`settings.json`), your agents (`agents/`), logs and the ChatGPT sign-in (`secrets/`). Delete the folder and it
  is gone. After installing a new map or aircraft in DCS, run `SETUP.cmd` again (the program reminds you at start).
- **Saved Games\DCS\Scripts** (asked first): one marked line in `Export.lua` and `Hooks\DCSInstructorHook.lua`.
  Remove them with `.venv\Scripts\python.exe tools\install_export.py uninstall` and `tools\install_hook.py uninstall`.
- **DCS install** (optional, asked first, off by default):
  - `Scripts/Speech/speech.lua`: mutes DCS's own ATC voice, so only this program's ATC talks;
  - `Scripts/UI/RadioCommandDialogPanel/RadioCommandDialogsPanel.lua`: a bridge so agents can use the native radio
    menu.

  Backups go to `backups\dcs\`; `utilities\DCS_PATCHES.cmd remove` restores them. A DCS update undoes the patches
  (`DCS_PATCHES.cmd update` re-applies them). **Modified DCS files may fail the integrity check of some multiplayer
  servers**: use them in single player or remove them before joining such a server.

## Status

| Part | State |
|---|---|
| Instructor (tower view, F-5E-3 manuals) | flown |
| Training lesson runner (the official F-5E-3 lessons checked against your cockpit switches; console only, `utilities\INSTRUCTOR.cmd`) | tested offline, not flown yet |
| ATC (tower, ground, approach, radar view) | flown |
| Scripted wingman (airplane and helicopter), landing at a point, strafing, troop transport | flown |
| Vehicles: pick-up and drop by helicopter | flown |
| Artillery fire missions | flown |
| Triggers, internal net between agents | flown in parts |
| Ships and carrier, air controller ("Magic"), radio frequency changes by order | built, not flown yet |

Design notes and the full build log, flight by flight: [`BLUEPRINT.md`](BLUEPRINT.md).

## For developers

- The internal name is still `DCS-Instructor` (launcher, hook file, the markers in `Export.lua`), so existing
  installs keep being recognised.
- Python 3.12, standard library plus the pinned `requirements.txt`; Lua for the DCS side (`dcs/`).
- Tests: `.venv\Scripts\python.exe -m unittest discover tests`. Tests that check against real DCS data (Senaki's
  chart, the F-5E cockpit) or run DCS's own Lua interpreter are skipped until `SETUP.cmd` has built that data, so the
  tests carry no copy of it either (`tests/dcs_data.py`).
- Code, comments and logs are in English; user-facing text lives in `locales/*.json`.
- A new aircraft is one folder under `modules/` (see `modules/F-5E-3/module.json` and the `tools/build_*.py` scripts
  that read DCS's own files).

## License and credits

Released into the public domain under [The Unlicense](LICENSE): use it, change it, share it, sell it, no conditions.

The Unlicense covers this project's own code and text only. It does not cover the DCS data the setup builds on your
PC (below), and it does not cover the third-party software this project downloads, but does not include, under its
own licenses:
[python-build-standalone](https://github.com/astral-sh/python-build-standalone) (Python),
the packages in `requirements.txt`, and the [Supertonic](https://github.com/supertone-inc/supertonic) voice model.

Not affiliated with or endorsed by Eagle Dynamics, OpenAI or the SRS authors. DCS World is a trademark of Eagle
Dynamics SA.

### Data from DCS

**No Eagle Dynamics data ships with this project.** `SETUP.cmd` builds it on your PC, read-only, from your own DCS
install, with the `tools/build_*.py` scripts (DCS's own Lua interpreter reads DCS's files), only for the aircraft and
maps you own. It stays in your folder and is never uploaded:

| Built file | What it holds | Read from |
|---|---|---|
| `modules/<aircraft>/controls.json`, `autostart.json`, `export_args.lua` | cockpit control ids and hints; the autostart sequence | the aircraft's cockpit scripts |
| `modules/<aircraft>/positions.json` | switch position labels | the control hints, plus `positions_overrides.json` (ours, hand-checked) |
| `modules/<aircraft>/training/` | the official training lessons, converted | the aircraft's training missions |
| `modules/<aircraft>/library/` | the manuals' text, split into sections | the aircraft's PDF manuals |
| `terrains/<map>/navigation.json` | airfields, their frequencies and navaids | each map's `Beacons.lua` and `Radio.lua` |
| `terrains/clouds.json` | cloud base and coverage per weather preset | `Config/Effects/clouds.lua` |
| `dcs/aircraft_rcs.lua` | radar cross section per aircraft type | the aircraft definition files |

What ships under `modules/` is ours: `module.json` (where an aircraft's files are in DCS), `radios.lua` (which cockpit
devices are the radios) and `positions_overrides.json` (switch positions checked by hand and in flight).
