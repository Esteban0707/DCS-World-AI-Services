# DCS-Instructor blueprint

Agreed with the user on 2026-10-31. Design only; nothing here is built yet unless marked **done**.

A modular AI flight instructor for DCS World. The pilot talks to the instructor with a push-to-talk button; the
instructor reads the cockpit and flight state, answers or corrects, and its voice arrives on the pilot's SRS radio.
First aircraft module: F-5E-3 Tiger (the newest version, `E:/DCS World/Mods/aircraft/F-5E 2024`).
A later idea on the same base: an AI that roleplays a commander (orders to units through radio commands and
mission flags). Out of scope until the instructor works.

## Rules

- All code, identifiers, comments, logs and file names in English. No Spanish in code.
- User-facing text comes from locale JSON files (`locales/<lang>.json`), or from the model when it is large enough
  to answer in the pilot's language directly.
- Fully independent: own Python runtime, own venv, own Supertonic model. No imports, paths or files from
  DCS-ATC, GemmaVoice or any other project. Only external dependency: SRS (and DCS itself).
- Modular: each piece is replaceable behind a small interface (speech recognition, brain, TTS, voice output,
  aircraft module).
- Python code runs on Windows and Linux. The DCS-side Lua runs only on Windows, because DCS does.

## The two parts

```
PILOT PC (client, always)                         INSTRUCTOR (same PC, a Pi, or a squadron server)
-------------------------                         -----------------------------------------------
DCS Export.lua hook: cockpit + flight state --+
Mic, only while the PTT button is held        +-- text + state --> rules engine (always on, no AI)
Speech recognition: audio -> text ------------+                     brain (model, only when needed)
                                                                    aircraft module (only the active one)
                                                                    Supertonic TTS
Pilot's SRS radio  <------------------------------- SRS ExternalAudio on the instructor frequency
```

### Part 1: client (pilot PC)

- **Cockpit/flight export**: a Lua hook loaded from DCS's `Export.lua`. Reads `LoGetSelfData()` (aircraft name,
  position, attitude), speed/altitude/AoA/G, and cockpit switch positions with
  `GetDevice(0):get_argument_value(arg)`. Sends JSON over localhost UDP to the client process.
  Must run on the pilot's PC: in multiplayer only the pilot's machine can read its own cockpit.
- **Push-to-talk**: a joystick button (the user's Logitech X56), chosen in the GUI. The mic is recorded only while
  it is held.
- **Speech recognition** (pluggable):
  1. Google Cloud Speech-to-Text API (first). It accepts audio over the network, unlike keyboard/browser dictation.
  2. Later: Whisper (local), other external APIs.
- Sends `{pilot, aircraft, text, state}` to the instructor (local call or network).

### Part 2: instructor (anywhere)

- **Module loader**: picks the folder in `modules/` that matches the aircraft name reported by DCS
  (e.g. `F-5E-3`). The model only ever sees the active module.
- **Rules engine** (plain Python, no AI, runs every state update): compares numbers against the module's
  procedure limits (approach speed, gear down before landing, etc.). Quick corrections are prewritten phrases from
  the locale file, spoken directly. This keeps model calls rare: cost and latency stay low.
- **Brain** (pluggable, OpenAI-compatible Responses/Chat interface):
  1. OpenAI "Sign in with ChatGPT" OAuth on the pilot's own Plus/Pro plan (self-serve for open-source/local apps
     per OpenAI's docs; re-check the official terms before building).
  2. API key (OpenAI or other).
  3. Local OpenAI-compatible endpoint (llama.cpp etc.).
  Called only when the pilot asks something or a correction needs explaining.
- **TTS**: Supertonic 3, own copy in `models/supertonic3`. Spanish first; voice and language from settings, so other
  languages are a locale file plus a voice choice.
- **Voice output**: SRS `ExternalAudio` transmits the synthesized audio on the instructor frequency. Fallback:
  PC speakers.

## Multiplayer and squadron servers

- Every pilot who wants the instructor installs the client (cockpit export cannot happen elsewhere).
- The instructor can be shared by a squadron. Each request is independent and runs in parallel; one model call
  per pilot, only when needed. Cost is per token, so separate calls cost no more than a shared one.
- **Accounts**: each client brings its own brain credentials (OAuth, API key or local endpoint). One personal Plus
  plan must not serve a whole squadron (plan usage is bound to the authorizing user and has a shared limit).
  A squadron that wants to pay for everyone uses an API key.
- **Frequencies**: each pilot sets a personal instructor frequency in the client, or the instructor addresses
  pilots by callsign on a shared one. Both are settings.
- A future "server mode" (an SRS bot that listens, so pilots install nothing) is possible but not planned:
  it cannot read cockpits anyway.

## Aircraft module format

```
modules/F-5E-3/
  module.json         aircraft names it matches, version, language of the source manual
  controls.json       cockpit controls: id, name, DCS argument number, value meaning
                      (generated from the module's clickabledata.lua; 232 clickable elements in F-5E 2024)
  autostart.json      DCS's own autostart sequence (Macro_sequencies.lua), each step linked to its control and
                      cockpit argument where one exists; the base for the cold-start procedure
  procedures.json     steps per phase (cold start, taxi, takeoff, landing...): which control or value to check,
                      expected value, phrase key
  limits.json         numeric limits for the rules engine (speeds, AoA, gear/flap limits)
  training/*.json     the aircraft's official DCS training missions converted by tools/build_training.py: trigger
                      rules in order, cockpit checks (arg + range), flags/timers/zones, instructor text in every
                      shipped language (F-5E: en, es, de, ru, cn), voice clip names, cockpit highlights
  reference.md        condensed text from the flight manual for the model (sources noted per section)
```

Source documents for the F-5E: `Mods/aircraft/F-5E 2024/Doc/` (flight manual in English and Spanish, QFM).
Numbers are copied from the manual with page references, never invented.

## GUIs

### Client
- SRS: path to an existing install, or download it.
- Instructor frequency and modulation; coalition; display name.
- Instructor location: local, or server address.
- PTT button (joystick + button picker).
- Audio devices (user request 2026-11-01): pick the microphone (e.g. headset or Oculus) and the output from lists of
  the PC's devices, switchable at any time.
- Speech recognition provider (chrome / google_cloud / whisper) and language; credentials.
- Brain connection (OAuth sign-in, API key, local endpoint) when the client carries the account.
- Status: DCS connected, aircraft detected, active module, last heard text.

### Instructor
- Brain connection and model.
- Voice: Supertonic voice, speed, radio effect, volume.
- Language.
- Installed modules.
- Logs.

## Folder layout (target)

```
DCS-Instructor/
  BLUEPRINT.md  README.md  requirements.txt
  client/          ptt, mic capture, speech recognition, state receiver, client GUI
  instructor/      module loader, rules engine, brain providers, tts, voice output, instructor GUI
  shared/          message formats, settings loading, logging
  dcs/             Lua export hook installed into Saved Games/DCS/Scripts
  modules/         one folder per aircraft
  locales/         en.json, es.json
  runtime/python   own CPython 3.12 for Windows (Linux uses the system python3)    [ignored by sharing]
  models/          Supertonic model files                                         [ignored by sharing]
  secrets/         API keys (google_speech_api_key.txt...)                        [never shared]
  logs/
```

## Build order (each step proves itself before the next)

1. **Setup**: own runtime, venv and Supertonic model; a Spanish test phrase synthesizes. **done 2026-10-31**:
   model downloaded fresh from Hugging Face by `tools/setup_tts.py`; 10 voices (F1-F5, M1-M5) each produced a
   Spanish test file in `logs/tts_test/`.
2. **Voice loop**: hold PTT, speak, Google Speech returns text, Supertonic repeats it on the SRS instructor
   frequency. Proves mic, PTT, speech recognition, TTS and SRS output together.
   **SRS output done 2026-11-01, heard live**: the user heard the Spanish test phrase on the F-5E UHF radio at
   251.0 AM (SRS server and client 2.4.1.0 on this PC, client connected to 127.0.0.1:5002; transmission about 6.7 s,
   no ExternalAudio error, no speaker fallback). `instructor/srs.py` finds the SRS install
   wherever it is ("srs.path" setting, else the installer's registry value
   `HKCU\Software\DCS-SR-Standalone\SRPathStandalone`, else Program Files), writes each Supertonic clip as OGG
   Vorbis and runs SRS's own `ExternalAudio\DCS-SR-ExternalAudio.exe` (frequencies, modulations, coalition, port,
   name, volume from `settings.json` "srs"). One clip at a time, in order. A missing SRS or a failed transmission
   (ExternalAudio logs `|ERROR|`, e.g. "Could not connect to server") plays that clip on the PC speakers instead.
   Live check: `SRS_TEST.cmd` (`tools/srs_test.py`): opens the SRS server and client of the detected install if
   they are not running, brings the client window to the front (with SRS's "start minimised" the client creates no
   main window, only a tray icon; `instructor/srs_window.py` posts the tray icon's double-click, verified live), then
   transmits the test phrase on each Enter. Tests: `tests/test_srs.py`. Limitation seen in ExternalAudio 2.4.1.0: it only
   connects to an SRS server on 127.0.0.1, so the instructor must run on the PC that hosts the SRS server.
   **Dictation built 2026-11-01 (offline-verified; mic and joystick read live; not yet dictated live)**:
   `client/ptt.py` reads the push-to-talk button with the Windows joystick API (winmm, no extra package; joystick
   chosen by product name, buttons numbered from 1; `PTT_PICK.cmd` saves the pressed button to settings "ptt").
   `client/mic.py` records the "mic.device" input (by name; the PC default is the virtual Voicemod mic) only while
   the button is held (`sounddevice` added to requirements). `client/dictation.py` picks the provider
   ("speech.provider", to be chosen in the GUI) and feeds the same pilot input as the console in
   `instructor/main.py`:
   - "chrome" (default, free; user's choice 2026-11-01): Chrome's built-in Google dictation (Web Speech API).
     `client/chrome_dictation.py` serves `web/dictation.html` on 127.0.0.1:"speech.port" (47821) to a Chrome app
     window with this project's own profile (`runtime/chrome-profile`, `--use-fake-ui-for-media-stream`: no mic
     prompt); PTT presses go to the page as server-sent events (Chrome throttles timers of a page hidden behind
     DCS), the page dictates from "mic.device" while held and posts the text to /heard on release; instructor lines
     are shown in the window. Design ported, with the user's explicit permission, from what already worked live in
     DCS-ATC (read once for reference; nothing imported or referenced at runtime), including its fixes for stuck
     recognitions, headset-muted microphones and failed track restarts. Page texts: locale "dictation_page".
   - "google_cloud": Cloud Speech-to-Text v1 REST on our own recording, API key from GOOGLE_SPEECH_API_KEY or
     `secrets/google_speech_api_key.txt` (sent as a header, never in settings.json), intent words as hints.
   - "whisper" (local): planned; needs the user's approval to download a model. "none" = off.
   Voice loop check: `DICTATION_TEST.cmd` (repeats what it understood on SRS). Tests: `tests/test_dictation.py`.
   **Chrome dictation verified live 2026-11-01**: 44 s of Spanish speech recognized correctly (mic "Wireless Gaming
   Headset", audio started 11 ms after the press). PTT = X-56 Rhino Stick button 6, the same button as SRS's PTT
   (SRS counts it from 0 as 5). The stick also reports a "button 15" that switches on by itself and stays on, so
   `PTT_PICK` only accepts a button that is pressed and released (`tests/test_ptt_pick.py`).
   **Instructor frequency gate (user's idea 2026-11-01)**: with the shared PTT, the instructor only listens when the
   radio selected in SRS is on the instructor frequency and modulation ("ptt.instructor_frequency_only"), like a real
   radio. `dcs/instructor_export.lua` wraps SRS's own per-aircraft radio exporter in memory (no SRS file changed;
   SRS gets its result untouched) and sends a copy as "radio" ({selected, ptt, radios[{name, freq Hz, modulation}]});
   `shared/radio.py` decides (5 kHz tolerance; intercom never counts); `client/dictation.py` FrequencyGate checks on
   each press. Without SRS radio data (DCS or SRS not running) it listens anyway and says so. Tests:
   `tests/test_radio_gate.py`, including the export run by DCS's own `bin/luae.exe` with fake DCS/SRS globals
   (`tests/lua/export_harness.lua`).
   First live run (2026-11-01) did NOT gate: SRS 2.4.1.0 keeps `local SR = {}`, so the global lookup found nothing
   (no "watching SRS radios" line in dcs.log) and the gate fell back to "no SRS data: listening anyway"; the
   harness had used a global SR, which hid it. Fixed: SR is found as an upvalue of SRS's global export functions
   (`debug.getupvalue`, LuaExportBeforeNextFrame uses SR); the harness now keeps SR local like SRS. Still to check
   live: dcs.log shows "SRS radio data found" and "watching SRS radios of F-5E-3", and off-frequency presses are
   ignored. If DCS's export environment lacks `debug`, the log says "SRS radio data not found yet".
   Second live run (2026-11-01): still "SRS radio data not found yet"; the user noticed the instructor listened with
   the aircraft's radio off. Fallback added that does not need SRS: each module lists its radios' DCS devices
   (`modules/F-5E-3/radios.lua`: AN/ARC-164 = device 23, as SRS's own F5E.lua reads it) and the export reads
   `is_on()`, `get_frequency()` and `get_modulation()`; a radio that is off reports 0 Hz, so the gate stays closed.
   SRS's data is still preferred when reachable. dcs.log now says which source is used ("radios from SRS / cockpit
   devices") and whether the debug library is present. Harness covers srs/no_srs x radio on/off.
   Third live run (2026-11-01): radio off -> ignored, 251 -> heard, other frequency -> ignored, all correct; dcs.log
   "radios from cockpit devices" (SRS still not reachable, although the debug library is present). Back on 251 it
   no longer listened: not the gate. The process had died: Windows logged python.exe crashes in libsndfile_x64.dll
   1.2.2, 0xC00000FD (stack overflow), at 07:37:52 and 08:07:20, each right after a long dictated phrase was
   repeated. One soundfile.write of OGG Vorbis of about 12 s or more crashes (reproduced: 5 s ok, 12 s and 30 s
   crash) - the same bug DCS-ATC hit and fixed (the user pointed at it; read there, rewritten here).
   `instructor/srs.py` `write_ogg` now writes in 4096-sample blocks (12/30/60 s verified); regression test in a
   child process (`tests/test_srs.py`). Every press is now logged with the radio seen (`logs/dictation.jsonl`).
   **Gate verified live 2026-11-01** (logs/dictation.jsonl, source "cockpit"): 351.000 ignored, 251.000 heard,
   261.000 ignored, 251.000 heard, 251.025 ignored, radio off ignored. An earlier "listens on any frequency" report
   was the cockpit itself still on 251.000 (DCS reported 251.000 Hz on every accepted press; the user then found
   they had been changing it wrongly). `DICTATION_TEST` logs every radio change with the UHF panel arguments
   (300, 307, 311, 327-331). Found then: a reader started while already in the aircraft never got the one-time
   'aircraft' message (knobs were not logged, and INSTRUCTOR would never start its lesson); `CockpitState.apply`
   now treats a new aircraft name in the flight data as the aircraft message.
3. **Cockpit export**: the Lua hook streams F-5E state; flipping a switch in the cockpit changes its value in the
   client log. **done 2026-10-31, verified live**: `dcs/instructor_export.lua` (installed by
   `tools/install_export.py`, chained after SRS's line) streamed 1102 messages over 223 s of model time from the
   F-5E 2024 (reported as `F-5E-3`, 236 args). The cold start matched the module's autostart order: oxygen, fuel
   covers, IFF, battery 387, generators 388/389, boost pumps 380/382, start buttons 357/358, RPM 0 -> 50 %,
   dampers, altimeter, canopy, master caution. Notes: values captured mid-animation (e.g. 0.32) before settling,
   so the rules engine must judge settled values; arg 0 is shared by the canopy handle and the mirrors.
4. **F-5E module, first procedure**: cold start, step by step, checked against live switch positions.
   Module data **done 2026-10-31**: `tools/build_module.py` runs `tools/dcs/dump_cockpit.lua` with DCS's own
   `bin/luae.exe` on the aircraft's real cockpit scripts (read-only) and writes `controls.json` (232 controls; the
   two canopy mirrors have no command name) and `autostart.json` (202 steps: 180 commands, 109 linked to a
   clickable control and its argument, the other 71 named circuit-breaker commands; plus messages and engine-start
   conditions). Still to do: `procedures.json` checked against the Spanish/English flight manual, `limits.json`.
   **Training lessons done 2026-10-31** (user's idea: take procedures from DCS's own training, not invented):
   `tools/build_training.py` converted the 10 F-5E training missions (cold start/taxi/takeoff: 90 rules, 46
   cockpit checks; landing: 72 rules, 39 checks; navigation; 7 weapons lessons). Decision: the lessons are the
   primary source for procedures; the autostart and the manual complement them. Caveat: zone, speed and altitude
   conditions refer to the training mission's own map/zones, so outside that mission only the cockpit checks and
   texts carry over directly. Two texts have no Spanish version (English fallback).
   **Lesson engine done 2026-10-31 (offline-verified, not yet live)**: `instructor/lesson.py` runs a converted
   lesson against the cockpit stream (arg ranges, flags, timers, or-groups, speed/altitude; zone conditions pause
   the lesson and are reported). `instructor/main.py` (`INSTRUCTOR.cmd`) = cockpit stream + lesson + Supertonic
   voice (PC speakers for now) + pilot input. Pilot input has one entry point (`instructor/pilot_input.py`): short
   lesson commands from the locale ("listo", "repite") drive the lesson; anything else goes to the brain with the
   lesson context (`instructor/brain.py`, NullBrain until a provider is configured). Typed in the console until
   dictation exists. Tests: `tests/test_lesson.py` (a virtual pilot completes the cold start up to the taxi call,
   41/90 rules, then pauses at the first zone condition). Found by the tests: lessons check gauge arguments (e.g.
   arg 52, trim indicator), so `export_args.lua` now includes every argument the lessons check (267).
5. **Brain**: free questions answered from the module reference; OAuth, API key, local.
   **ChatGPT plan (Sign in with ChatGPT) built 2026-11-01 (offline-verified; not yet signed in live)**, user's choice:
   their own ChatGPT/Codex plan, models GPT-6.1 Terra (default, fast) or GPT-6.1 Sol (stronger, slower); a
   connection and model picker comes with the GUI. Terms checked 2026-11-01 in OpenAI's docs
   (developers.openai.com/siwc/token-sharing-open-source): plan usage is offered to open-source and locally hosted
   apps; paid or remotely hosted apps need OpenAI's interest form. Plus plans share one five-hour limit across all
   apps; Pro has none. `instructor/chatgpt_auth.py` (standard library only) implements the documented flow: first
   sign-in registers a user-defined agent (client_id=dynamic_agent_client, agent_name_hint "DCS-Instructor", stable
   ext_agent_host_id urn:uuid), loopback callback 127.0.0.1:1455/auth/callback, PKCE S256, state, nonce; ID token
   verified (RS256 against OpenAI's JWKS, issuer, audience, expiry, nonce); inference only when the granted scopes
   include chatgpt.tokens.use.direct; 1 h access tokens refreshed 5 min before expiry (rotating refresh token,
   serialized with a lock file); sign-out revokes the refresh token. Endpoints match OpenAI's live discovery document.
   Credentials: `secrets/chatgpt/host.json` and `secrets/chatgpt/accounts/<client_id>.json` (never shared or
   logged). `instructor/brain.py` ChatGptBrain: `POST /v1/responses` with store false, stream true, instructions
   (`instructor/brain_instructions.md`: short spoken radio answers in the pilot's language, never invent values),
   the last 6 exchanges in input, and a developer message with the cockpit data in pilot units (kt, ft, fpm, the
   selected radio); no temperature/max_output_tokens (rejected on this route). Errors are spoken from the locale:
   usage limit, sign in again, other. `instructor/voice_loop.py` Responder answers one phrase at a time off the
   dictation thread; DCS-Instructor.cmd now answers with the brain (with provider "none" it still repeats);
   DICTATION_TEST.cmd keeps repeating; BRAIN_TEST.cmd = voice loop with the brain in a console. `CHATGPT_LOGIN.cmd`
   (`tools/chatgpt_login.py`) signs in, lists the account's models, saves settings "brain" {provider chatgpt, model,
   account} and asks one test question. Tests: `tests/test_brain.py` (fake OpenAI, test RSA key; full loopback
   sign-in, refresh, stream events, request rules). Not in the context yet: cockpit switch positions, lesson steps,
   the F-5E manual (`reference.md`).
6. **Rules engine in flight**: landing corrections from `limits.json`.
7. **GUIs** and packaging.
8. More aircraft modules, more languages, Whisper, squadron mode.

## Brain information flow (design agreed with the user, 2026-11-01; not built yet)

Role and realism (user's words, paraphrased): the instructor sits in a control tower, talks by radio, sees the
aircraft through the window and follows it on the airport radar once it leaves, with the aircraft's manuals in front
of them; talks normally, as an instructor talks to a novice. It must not feel like cheating: what the instructor
cannot see, it asks the pilot. It checks attitude, speed, altitude, heading and so on against the manual.

- **System prompt**: the role, editable later in the GUI (where triggers and wider plans beyond instructing come
  later); for now only "instructor in a control tower, your radio is X, the aircraft is Y" plus the speech format
  (`instructor/brain_instructions.md`). No language is imposed: the model answers in the language the pilot speaks.
  A short guide to the tools is generated by code from the tools that exist.
- **Tools (the model asks when it needs them; "a librarian")**: telemetry and geographic data (what tower and radar
  see); the aircraft manual split into sections with an index; the DCS training procedures by phase (cold start only
  during the cold start; taxi/takeoff when that phase comes); a library folder per module where the user drops
  documents downloaded from the internet. Later: radio orders, explained in the system prompt.
- **Cockpit switches**: summarized by code (not an AI), e.g. "fuel boost pump ON, generator ON", from the export's
  changed arguments. Needs a position table per module (control + value -> label). How much of it the instructor
  may "see" during the flight is decided with the realism rule above.
- **Memory**: the last few exchanges in each request, plus a session file with everything that happened; at the end
  the pilot can ask for a debriefing: the model reads a timeline made from that file and discusses what went well or
  badly according to the manual.
- All code, identifiers, comments, tests and logs in English; Spanish only in locales/*.json.
- Everything per aircraft lives in its own independent module folder (`modules/<id>/`); the instructor only gets the
  folder of the aircraft the pilot is flying (`shared/modules.py` find_module by DCS aircraft name).

Decision (user, 2026-11-01): cockpit switches go to the **black box only**. The tower instructor does not see the
cockpit during the flight (it asks the pilot); the debriefing reads the black box. positions.json marks
`outside_visible` controls (gear, flaps, speed brake, canopy, lights...) in case a live "visible from the tower" view
is wanted later.

**Black box built 2026-11-01 (offline-verified; replayed on the real cold-start capture)**:
- `tools/build_positions.py` -> `modules/F-5E-3/positions.json`: 191 arguments (128 switches, 43 buttons, 20 not
  parsed: radio/IFF/TACAN wheels and a few odd hints). Labels from the DCS hints; value order by rules seen in DCS's
  data (toggles: first label = highest value; rotary selectors: first label = lowest; circuit breakers: ON = 0).
  `positions_overrides.json` holds hand-checked entries with evidence (11 verified from the training lesson checks
  and the live autostart). Suspect: fuel shutoff switches (the autostart opens their covers but leaves the switch at
  0, which suggests 0 = OPEN, against the toggle rule).
- `POSITIONS_CHECK.cmd` (`tools/positions_check.py`): flip switches live and answer per unverified label (Enter =
  right, n = reversed, s = skip); answers go to the overrides and the table is rebuilt at once.
- `shared/cockpit_summary.py`: settled values only (no mid-animation reports), buttons as presses, starting state.
- `instructor/blackbox.py`: `logs/sessions/<start>_<aircraft>.jsonl` with start, start_switches, switch changes,
  telemetry every 5 s of model time (pilot units + lat/lon), radio changes, pilot / instructor lines, end. Wired
  into the voice loop (DCS-Instructor.cmd, BRAIN_TEST.cmd). Replaying logs/cockpit.jsonl reproduced the DCS
  autostart in order (battery BATT, generators, boost pumps, start buttons, speed brake test, flaps AUTO, dampers,
  canopy CLOSE). Tests: `tests/test_blackbox.py`. `pypdf` 6.21.4 installed for the library step.

**Tower view, user's rule of 2026-11-01 (supersedes the "radar picture" and the flight/location tools below)**:
the instructor gets from the game only what a control tower sees. `instructor/tower_view.py` picture, sent with each
message: aircraft type, callsign (only from the GUI hook; the settings/pilot-name fallbacks were removed from the
project at the user's request), the tower (departure field: nearest airfield to the first position, with its map),
the window view only within 5 nm of the tower (on the ground or airborne, and the outside configuration: canopy,
gear, flaps, speed brake, drag chute, exterior lights, nose strut, named as seen, e.g. "landing gear"), and "no radar
contact" until a mission radar is linked. No instruments, no switches inside, no position/speed/heading numbers.
Toolbox: only get_airfield (the tower's charts: frequencies, ILS, TACAN; no range or bearing). Removed:
get_flight_data, get_location. The black box still records everything (flight_context, pilot_radio in
`instructor/brain_context.py`). Live check (simulated, Batumi runway): radio check -> "Enfield One One, Batumi Tower,
loud and clear"; airspeed/heading -> no radar contact, check your instruments; battery -> cannot tell from the tower;
"what do you see" -> on the ground, canopy open, gear down. Legacy: INSTRUCTOR.cmd's lesson path
(`instructor/lesson.py` context) still passes flight data; it is to be redone.
**Airfield radars built 2026-11-01 (offline-verified with DCS's luae and a fake scripting API; model checked live
with simulated radar data; not yet run in DCS)**. User's rule: a radar is named after the airfield it belongs to
(not "instructor"), so the same unit serves triggers too: any ground unit or ship with a radar sensor whose unit or
group name starts with the airfield's DCS name, spaces/dashes/case ignored ("Senaki-Kolkhi", "Senaki-Kolkhi-2",
"Kutaisi EWR"); all of them form that airfield's network. No mission trigger is needed.
- `dcs/instructor_radar.lua` runs in the mission scripting environment (world.getAirbases, coalition.getGroups,
  Unit:hasSensors(RADAR), Controller:getDetectedTargets(RADAR), coord.LOtoLL); radars re-found every 30 s; returns
  per airfield the radars alive and the aircraft they detect (player flagged).
- `dcs/instructor_hook.lua` sends it there with net.dostring_in (tries the "server" state, then "mission"; which one
  answers is to be confirmed live: dcs.log 'radar script loaded in the "..." environment'), polls once per second and
  forwards {type: radar, airfields} or {type: radar, unavailable} (multiplayer client) to the cockpit port.
- `instructor/tower_view.py` radar: only the tower's own network (by name); status contact / no radar contact /
  the tower has no radar / radar out of service / no radar data from the mission (stale after 5 s); a contact gives
  range and bearing from the field, altitude, ground speed, track, vertical speed; traffic: other aircraft those
  radars see within 30 nm (5 closest).
- Live model check (simulated contact 10 nm north of Senaki + an Su-25): "radar contact, ten nautical miles north of
  the field, three thousand feet, tracking one two two"; traffic call with position, altitude and track; 2.6 s.
- Tests: `tests/test_hook.py` (radar harness in `tests/lua/hook_harness.lua`), radar states in `tests/test_tools.py`.
- Multiplayer: only where the mission scripting environment answers (single player, or a server running it).

**Tools built 2026-11-01 (verified live against the ChatGPT plan route)**:
- Per message the model now gets only the radar picture (`instructor/brain_context.py`: IAS, altitude MSL/AGL,
  vertical speed, heading, on the ground) and the pilot's selected radio; the rest through tools.
- `instructor/tools.py` ToolBox, one namespace "tower" (the plan route wants function tools grouped in a
  namespace): get_flight_data (full telemetry), get_location (map, lat/lon, 3 nearest airfields with range, bearing,
  where the tower sees the aircraft, tower frequencies, ILS, TACAN), get_airfield(name). The system prompt's tool
  guide is generated from the definitions (`{tool_guide}` in brain_instructions.md).
- `instructor/brain.py` tool loop: up to 3 tool rounds, then tool_choice none; function calls and reasoning items
  (encrypted content only, ids dropped since store is false) are replayed with their function_call_output; the
  history keeps only question and answer. Found live: on this route response.completed has an empty output list;
  the items arrive in response.output_item.done events (read_stream collects them).
- Live check (scratch script, simulated aircraft 10 nm south of Batumi at 3000 ft): "where am I / tower frequency"
  -> get_location -> "ten nautical miles south of Batumi, tower 260.0 AM" in 4.9 s; "angle of attack" ->
  get_flight_data -> 3.2 degrees in 4.4 s; "radio check" without tools 2.5 s.
- Maps: `tools/build_terrains.py` + `tools/dcs/dump_terrain.lua` (DCS's luae, read-only) -> `terrains/<map>/
  navigation.json` from each map's Beacons.lua and Radio.lua. Good coverage: Caucasus 19/21 airfields placed,
  Persian Gulf 26/27, Nevada 6/9; partial: Syria, Sinai, Kola, Afghanistan, Iraq; none: Normandy, The Channel,
  Marianas WWII (no beacons). Complete airfield data (every airfield, runways) would need DCS's runtime
  Terrain.GetTerrainConfig("Airdromes") from a GUI hook in Saved Games/DCS/Scripts/Hooks: needs the user's approval.
- Tests: `tests/test_tools.py`, tool loop tests in `tests/test_brain.py`.
- Callsign (user request 2026-11-01): the system prompt names the aircraft's callsign ("Enfield 1-1"), how the
  instructor addresses the pilot. DCS's export API has no callsign (DCS-ATC, read with the user's permission, gets it
  from the mission scripting API, unit:getCallsign(), through a hook). For now: settings "pilot.callsign", else the
  pilot's DCS name, which the export now sends (LoGetPilotName, plus unit_name/group_name; harness test).
  **GUI hook built and installed 2026-11-01 with the user's approval** ("exporta solo el indicativo y el numeral"):
  `dcs/instructor_hook.lua`, loaded by the one-line `Saved Games/DCS/Scripts/Hooks/DCSInstructorHook.lua`
  (`tools/install_hook.py install|uninstall|status`). Once per second it reads the player's unit from the mission
  (DCS.getPlayerUnit, DCS.getCurrentMission) and sends {type: callsign, callsign, unit_name} to the cockpit port:
  western callsigns spoken as "Enfield 1-1", eastern ones as their number, DCS.UNIT_CALLSIGN as fallback. Priority in
  the prompt: mission callsign, then settings "pilot.callsign", then the DCS pilot name. The black box records it.
  Tests: `tests/test_hook.py` (DCS's luae with a fake hook API, `tests/lua/hook_harness.lua`). Not yet seen live:
  dcs.log should say "DCS-Instructor: hook loaded" and "player callsign ...".
- Where the instructor's numbers come from (user asked 2026-11-01): the pilot's own aircraft through the export
  (LoGetSelfData etc.), filtered to what a radar shows; the map beacons only give airfield positions. A real
  mission radar (next): from the hook, net.dostring_in('mission', ...) can ask a radar unit's controller for its
  detected targets (getDetectedTargets), so the tower loses contact behind terrain and sees other traffic. It needs
  the mission scripting environment: single player, or a server whose admin runs it; a client in someone else's
  server cannot read the mission's radars.

First live run of the hook (2026-11-01): it never started. dcs.log: "hook failed: instructor_hook.lua:16: attempt to
concatenate global 'INSTRUCTOR_DIR' (a nil value)": DCS runs each hook file in its own environment, so the global
set by DCSInstructorHook.lua did not reach the dofile'd script (the harness had used a real global, which hid it).
Fixed: the loader passes the project folder as the chunk's argument (loadfile(...)(dir)); the harness now loads it
the same way and asserts there is no global; hook file reinstalled. The same run confirmed the tower view live:
"Senaki-Kolkhi" picked as the tower, window configuration seen on the ground, and the model asked for the callsign
it did not have.

Second live run (2026-11-01): hook loaded, callsign "Colt 1-1" (unit "Aéreo-1-1") reached the model, radar script
loaded in the "server" state, the Senaki-Kolkhi radar was found, but the status stayed "no radar contact" airborne.
Cause: Controller:getDetectedTargets() is the AI's target list and reports enemies only; the user's radar is
friendly. Replaced by our own radar model in dcs/instructor_radar.lua: airborne aircraft of any coalition within the
radar's own air detection range (Unit:getSensors, default 100 km) and in line of sight from the antenna (+10 m,
land.isVisible). The hook now reloads the radar script once per session (a mission may keep an older version).
BRAIN_TEST/DCS-Instructor print and log (dictation.jsonl "radar") every change of the tower radar status with each
radar's range. Needs a DCS restart (hook code is read at DCS start).
**Verified live 2026-11-01 (third run): radar contact works** - the user flew with the Senaki-Kolkhi radar and the
instructor tracked him ("you are a genius bro, it is working really well"). The user wants this radar design kept as
is: do not go back to the AI target list; keep the luae harness tests green.

## Brain flow, agreed 2026-11-01 (late) - supersedes "sent with each message" above

User's design: the instructor is not an ATC. Each message carries only the situation; everything else is a tool the
model calls when it wants it.
- **System prompt** (`instructor/brain_instructions.md`): military flight instructor in the control tower of {tower};
  strict about instructions and procedures, relaxed in speech, patient explaining; sees only what a tower sees, asks
  what it cannot see. Answers in the pilot's language. Editable in the GUI later.
- **Situation per message** (`TowerView.situation()`): aircraft, callsign, tower, and the **phase** computed by code
  (user's option B, `instructor/phase.py`): parked, taxiing, takeoff roll, airborne, approach (within 10 nm, below
  3000 ft AGL, descending), landing roll, taxiing in after landing, parked after landing. Ground speed comes from the
  change of position over 1 s of model time (a parked aircraft showed 17 kt IAS from the wind).
- **Tools**: tower info (`get_tower_view`: window + radar + traffic; wind/QNH/runway next), the airfield charts
  (`get_airfield`); next: the F-5E manual library (index, search, read section), DCS procedures by phase, and the
  black box only after the mission and only if asked (the code withholds the tool in flight).
- Build order: (1) prompt + phase **done 2026-11-01**; (2) tower wind/QNH/runway in use from the hook (DCS-ATC may be
  read for reference, never imported: this project will run on a Raspberry Pi); (3) manual library; (4) procedures
  by phase; (5) debriefing + checker.
- Live check (scratch script, simulated F-5E parked at Senaki, gpt-6.1-terra): radio check answered without tools in
  3.6 s; "do you see me" -> get_tower_view -> "a la vista en tierra" in 4.6 s; "what do I do first to start" gave
  generic advice without the manual: the reason for steps 3 and 4. 88 offline tests pass.
- **(2) Tower weather built 2026-11-01 (offline-verified with luae; model checked live with simulated data; not yet
  run in DCS)**: `dcs/instructor_weather.lua` (own file, loaded by the hook with the radar script, radar untouched)
  reads every airdrome's ground wind (atmosphere.getWind 10 m above the field), temperature and field pressure
  (getTemperatureAndPressure) and runways (Airbase:getRunways); the hook sends {type: weather} every 10 s.
  `instructor/weather.py`: wind from (DCS gives where the air goes), runway ends from course (= minus the true heading,
  radians; conventions read in DCS-ATC for reference, nothing imported), runway in use = most headwind, kept until
  its tailwind exceeds 5 kt, QNH from QFE (ICAO formula), hPa and inHg. Headings magnetic once the local variation is
  known: the export now sends mag_heading_deg (LoGetMagneticYaw), used only for true - magnetic. Tool `get_weather`
  (the tower's own field). Live model check: "wind 240 at 10, runway 27, crosswind 5 from the left", "QNH 1011 hPa /
  29.84 inHg" (6-7 s). 94 offline tests pass. Live in DCS: needs a DCS restart; dcs.log should say "airfield weather
  read".
- **(3) Library built 2026-11-01 (offline-verified; model checked live)**. English manuals only (user's choice).
  - Sources: module.json "library" (DCS Flight Manual ENG, Quick Flight Manual ENG, paths relative to the DCS install)
    plus any .pdf/.md/.txt the user drops in `modules/F-5E-3/documents/` (one book each, id "user-<name>").
    `LIBRARY_BUILD.cmd` (`tools/build_library.py`, pypdf, ~7 s) rebuilds `modules/F-5E-3/library/`: `index.json`
    (books -> sections: id, title, depth, parent, pages, words) and one cleaned `.txt` per section with `[page N]`
    markers (the PDF's page numbers, so the instructor can cite them). Derived from ED's manuals: not to be shared.
  - Sections from the PDF outline: each runs from its heading (found in the page text) to the next heading at any
    depth, so nothing repeats; ids are the manual's own numbers ("8.1"), unnumbered entries get parent + letter
    ("15a") or "s<n>"; figure captions are not sections. Flight manual: 93 sections, 290 pages, ~43k words; QFM: 40.
  - Cleaning (checked on the real text): running header and the symbol-font chapter banners removed, letter-spaced
    headings rejoined ("B e f o r e  S t a r t", "C A D C / P i t o t- S t a t i c ..."), Symbol-font bullets and
    degree signs mapped, Cyrillic look-alike letters made Latin ("МК-82", "140С" -> MK-82, 140C: searchable), wrapped
    lines rejoined into sentences. Figures are images: their text is lost (captions remain).
  - `instructor/library.py` (pure Python, Pi-ready): BM25 over ~120-word passages (431), title counted twice, light
    plural stemming, bonus for query words side by side; one result per section with book, id, page and a snippet.
    Sections longer than 1500 words are read in parts. Tools (namespace now "instructor"): `search_library(query,
    book?)`, `read_section(section, book?, part?)` (id, "id title" or title), `library_contents(book?)`; only the
    library of the aircraft being flown (`Shelf` by module). The prompt tells the model to teach from the manual,
    citing pages, a few steps at a time.
  - Live check (simulated F-5E parked at Senaki): "how do I start" -> search + read 8.1 -> battery, generators, boost
    pumps, then "tell me when done" (8.9 s); "done, what next" -> compressed air via F8/F5/F1 (7.1 s); "rotation
    speed" -> 8.5 table: 10 KIAS before liftoff, 143-145 kt at 15,000 lb clean, asks for the loadout (12.8 s); "max
    EGT" -> 685 C, 325-650 continuous, 925 transient (6.3 s); all checked against the text. 104 offline tests pass.

## One launcher and the cockpit recorder (2026-11-01, late)

- **One file to double-click** (user's request): `DCS-Instructor.bat` (DCS is opened by the user). `tools/launcher.py`
  first runs `tools/preflight.py`: export line and mission hook present in Saved Games/DCS (only checked, never
  installed from there), every module's library up to date (`build_library.stale`: not built, module.json changed,
  a document added/changed/removed -> rebuilt automatically, DCS install from settings "dcs.install"). Problems show
  in a Windows message box (texts in locales "checks") and the instructor starts anyway. Then SRS, Chrome dictation
  and the voice loop as before. Every other launcher moved to `utilities\` (BRAIN_TEST, CHATGPT_LOGIN, COCKPIT_TEST,
  DICTATION_TEST, INSTRUCTOR, LIBRARY_BUILD, POSITIONS_CHECK, PTT_PICK, SRS_TEST); older notes in this file use
  their bare names.
- **Cockpit recorder** (user: "a recorder in the aircraft for everything I touch"): inside the black box
  (`instructor/blackbox.py` ControlRecorder), every clickable control of the module (controls.json, 232 in the F-5E,
  including radio/TACAN/IFF wheels without labels) is recorded raw: `start_controls` (all values), then `control`
  {arg, control, old, new, label?, verified?} once a value has stayed put 0.3 s of model time; push buttons at once
  (a press is shorter). Replayed on the live cold-start capture of 2026-10-31: 34 records, the whole autostart in
  order (oxygen, diluter, fuel shutoff covers, IFF, battery, generators, pumps, both start buttons pressed/released,
  trim knob, speed brake, flaps, dampers, canopy), no noise. This replaces answering POSITIONS_CHECK in a console
  (the user's answers there were never saved; likely the console prompt while in VR): labels can now be verified
  from a flight's recording. 108 offline tests pass.

## First full live flights with tools, library and recorder (2026-11-01, 17:55 and 18:14)

Worked live: weather read by the hook (dcs.log "airfield weather read"; "wind 263 at 8, runway 27" on the radio),
the library teaching the cold start step by step from 8.1 (6-12 s per answer with search + read), radar contact
airborne, the recorder (both sessions: battery, generators incl. RESET cycles, boost pumps, start buttons, lights,
canopy, nosewheel steering presses, TACAN knobs, nose strut, gear up). The recorder also caught mistakes the tower
could not see (external fuel CL/pylon switches instead of the boost pumps, crossfeed OPEN, speed brake OUT, a
flare button press): material for the debriefing. One HTTP 503 subscription_sharing_user_unavailable (transient).
Found and fixed: first-seen controls were recorded as touches ("None -> value") because the export's full snapshot
comes every 5 s, after the 2 s starting span; first-seen values are now starting values only (controls and switches).
Found, to fix next:
- The phase stayed "parked" for every call, also holding short of runway 27: the pilot always talked while stopped,
  and "stopped before first takeoff" = parked. The tower cannot tell where on the field the aircraft is (apron,
  taxiway, holding point, runway), so the instructor never cleared the takeoff ("I cannot confirm the runway is
  clear") and the pilot took off without clearance. Plan: runway geometry from the hook (getRunways position, length,
  width -> lat/lon), the window view says "on runway 27 near its threshold" / "near the threshold of runway 27" /
  "elsewhere on the field"; phase "stopped after taxiing" instead of parked; the prompt says the instructor gives
  taxi/takeoff/landing clearances when what it sees, the radar and the pilot's report agree.
- Manual inconsistency: 8.1 says start overheat must not exceed 845 C; chapter 11 gives 925 C for start and
  acceleration. The model first said 845 and then "corrected" it to 925; it should cite both with their pages.
- Engine gauges (EGT, RPM, oil) are not in the black box: the debriefing cannot check the pilot's "800 degrees at
  idle" report (the instructor had the engine shut down on it). Candidate: record the gauge arguments too.

**Fixed the same day (offline-verified; model checked live with simulated data; needs a DCS restart)**. User's rule:
the instructor is not the tower and gives no clearances, but teaches ("it is an instruction, so it is fine"):
- The hook's weather now sends each runway's centre (lat/lon from getRunways position). `instructor/field_position.py`
  (geometry adapted from DCS-ATC's taxi code, read for reference only): on runway N near its threshold / on runway
  between its ends / near the threshold of runway N off the runway (holding point area) / elsewhere on the field. The
  window view (get_tower_view) adds it on the ground, with moving: stopped / taxiing / rolling fast.
- Phase "stopped after taxiing" instead of "parked" once the aircraft has moved.
- Prompt: not ATC, never gives clearances; when asked, says they come from ATC, tells the student whether they look
  ready from what it sees and hears, reminds the checks and traffic lookout, and lets them go ahead. When the manual
  gives different numbers for the same thing, it gives each with its context and page.
- Live check (simulated holding point of 27 at Senaki after taxiing): "clearance belongs to ATC; I see you stopped at
  the holding point of two seven; you look ready; wind 250 at 8; check runway and approach are clear; when cleared, go
  ahead" (7.1 s, get_tower_view + get_weather). 110 offline tests pass.

OpenAI voice through the ChatGPT plan (asked 2026-11-01): not available. The preview-limitations page
(developers.openai.com/siwc/token-sharing-open-source/preview-limitations, read 2026-11-01) supports only the
Responses API on this route; "the transcription API [is] not supported", audio/video input is excluded; Realtime,
text-to-speech and audio output are not documented for it. Voice stays Supertonic (local, free, Pi-ready) and Chrome
dictation; OpenAI voice would need a paid API key (a separate provider), not the plan.

## GUI design, agreed with the user (2026-11-01, night) - supersedes "GUIs" above

Approved as drawn ("I would not change a thing"): mockup https://claude.ai/artifact/88RgBSCysEcYhjrNUPVNoy (private, 4 screens:
Estado, Voz salida (TTS), Servicios, Conexiones). Build it next session.
- **Local web GUI** served by the program itself (like the dictation page): same on the PC and the Raspberry Pi,
  can be opened from a phone/tablet; all texts from locales/*.json.
- **Look**: dark midnight navy (#070C18 ground, #0A1222 side menu, #0F1A2E panels, #1C2B47/#24406E borders),
  electric blue accent #3DA9FF, Inter + JetBrains Mono, status lights that glow like a cockpit at night.
- **Concept: radio characters.** Reusable profiles, combined per service:
  - Connections (brains): Local web (server URL, e.g. LM Studio/Ollama/llama.cpp), API (provider + key), Codex (Sign in
    with ChatGPT); "Detect models" lists what each offers (all expose a model list). Several saved at once.
  - Voices: per profile engine (Supertonic now; Kokoro and API later, downloads only on the user's click), language,
    voice (F1-F5, M1-M5), and only what Supertonic really has: speed, quality (total_steps), silence between phrases
    (silence_duration), volume. No pitch in Supertonic (would be post-processing; to try). A "Test" button by SRS or
    by the PC speakers.
  - Services (Instructor now; ATC and Commander later; "+ new"): each with its own connection, voice and
    **frequency, which is its on-switch** (the instructor frequency gate, per service). Tabs: Frecuencia (active,
    MHz, modulation, connection, voice), Mission (system prompt editor, restore original), Triggers (inactive for now:
    later, actions the model may choose from what it hears: mission flags, unit orders, next phase; role rules stay in
    code). An ATC would be mostly code with locale phrases and a small model.
- **Radio effect**: SRS already applies its radio effect in each listener's own client (per radio model:
  RadioModels/*.json filters, saturation, compression; client settings RadioEffectsRatio, background noise, NATO
  tone; checked in the installed SRS 2026-11-01). So the voice goes to SRS clean; our own radio effect only for the
  PC-speaker fallback (switch "radio effect only on speakers").
- **Screens** (side menu): Estado (lights for DCS, callsign, SRS, radio, dictation, brain, radar, weather; last radio
  exchanges with latency; services), Conexiones, Servicios, SRS (control server/client; official download link if
  missing), Voz entrada (STT: Chrome dictation, Google Cloud API, Whisper download), Voz salida (TTS), Controles y
  audio (PTT picker, microphone and output device lists), Aviones y biblioteca (modules, documents, drop a PDF ->
  rebuild), Vuelos (black box sessions, timeline, later the debriefing), DCS (export/hook status, install/uninstall
  only on the user's click), Ajustes (languages, DCS folder).
- **Build order**: Estado + Voz (TTS with Test), so the user can tune Supertonic; then Conexiones, Servicios, SRS,
  STT, Controles y audio, Aviones y biblioteca, Vuelos, DCS, Ajustes.
- Later (user, 2026-11-01): the app has two parts, a bridge and the program: either everything on the PC, or the
  bridge on the PC and the program on a server (the Pi). To be designed later.

## GUI, first screens built (2026-11-02; offline-verified, voice heard on the PC; not yet run with DCS)

User's rules this session: the GUI is **English only** (texts in locales/en.json "gui", no Spanish GUI texts, even
though the mockup was drawn in Spanish); add an **audio device selector** (headset or VR goggles).
- `gui/server.py`: local web server on 127.0.0.1:"gui.port" (47822), page in `web/gui/` (index.html, app.css, app.js,
  plain JS, no libraries: Pi-ready). JSON API: GET /api/text, /api/status, /api/settings, /api/devices; POST
  /api/voice, /api/audio, /api/voice/test, /api/stop (POST only from its own page's origin). 127.0.0.1 only: it can
  change settings and has no sign-in yet; LAN/tablet access needs that first.
- Runs inside the instructor: `voice_loop.run(gui=True)` (DCS-Instructor.bat) shares a `gui.status.Live` (cockpit
  state, tower, brain, dictation, voice, last 20 exchanges, stop event) and opens the GUI in a Chrome app window
  ("gui.open_on_start"). Alone: `utilities\GUI.cmd` (`tools/open_gui.py`; only opens the window if the instructor
  already serves it); voice tests then load their own Supertonic.
- `shared/settings.py`: settings.json written by deep merge through a temp file (unknown keys kept).
- **Status**: 8 lights (DCS, callsign, SRS + the device the SRS client plays on, radio on/off frequency, dictation,
  brain + last answer time, radar, weather), the last radio exchanges with latency, services (Instructor active; ATC and
  Commander "coming soon"), Stop button. Screens not built show "coming soon" in the menu.
- **Voice output (TTS)**: Supertonic voice F1-M5, language (Supertonic's list, "voice.language", else "language"),
  speaks by SRS or PC device ("voice.output"), speed, quality ("voice.steps" = total_steps), silence between phrases
  ("voice.silence_s"), volume ("voice.volume", gain on the samples). Save applies at once to the running voice
  (`Voice.configure`). Test by SRS or on the PC device with the unsaved values (`Voice.test`, timings shown).
  Kokoro/API shown as coming soon. Radio-effect switch for speakers not built yet.
- **Controls and audio**: microphone and PC audio device from the PC's device lists (`shared/audio_devices.py`:
  DirectSound names on Windows = full names; default alias left out; a saved device that is not connected stays shown).
  Changes save and apply at once: the Chrome dictation page reconnects to the new microphone (event "microphone"), the
  recorded dictation uses it from the next press, the voice plays on the new device (`SpeakerOutput` now plays with
  sounddevice on the chosen device, else the default). `client/mic.py` also matches full names against MME's
  31-character names. PTT shown read-only (PTT_PICK.cmd changes it).
- **Found**: on the radio the pilot hears the instructor through the **SRS client's own output device**
  (`Client/global.cfg` AudioOutputDeviceId, a Windows endpoint id), not ours. The GUI shows it read-only
  (`instructor/srs.client_audio`, names from the registry's MMDevices). Changing it from the GUI would mean writing
  SRS's config in Program Files with the client closed: not done, needs the user's approval.
- Checked: API on the real PC (devices listed: Wireless Gaming Headset, Oculus Virtual Audio Device...; SRS client
  output read as "Headphones (Wireless Gaming Headset)"); voice test on the PC speakers (0.45 s synthesis, 1.9 s audio);
  SRS test with SRS closed returns ExternalAudio's "Could not connect to server"; screenshots of the three screens
  (headless Chrome). Tests: `tests/test_gui.py` (17); 127 offline tests pass. Phone width not checked.
- Next GUI screens in the agreed order: Connections, Services, SRS, Voice input (STT), Aircraft and library, Flights,
  DCS, Settings.

## Radar realism by aircraft size (2026-11-02; offline-verified with DCS's luae harness; needs a DCS restart)

User's request: every aircraft looked the same to the radar; make detection depend on distance and aircraft, from
DCS's own data, and give the agent that reading. Kept the working design (own model: range + line of sight; not the
AI target list) and added, in `dcs/instructor_radar.lua`:
- Each radar's air detection distances from `Unit:getSensors()` by **hemisphere** (target above/below the antenna)
  and **aspect** (head-on when flying towards the radar, tail-on when away), instead of the largest of them.
- Scaled by the target's **RCS** with the radar equation, distance ~ (RCS / 5 m2)^(1/4). RCS from DCS at run time
  (`Unit:getDesc().RCS`, if DCS gives it: not confirmed yet), else `dcs/aircraft_rcs.lua` (37 types, built by
  `tools/build_rcs.py` read-only from CoreMods/Mods aircraft definitions: F-5E-3 3 m2, F-16C 4, MiG-21 3, C-130J 80,
  KC-135 80, MQ-9 0.5, Mi-24P 12...; AI-only types such as Su-25T or C-130 are in DCS's compiled database, not in
  text), else 5 m2. Each contact carries rcs_m2, rcs_source (dcs/table/default), range_km (how far this radar sees it
  now) and dist_km. Example: a radar seeing 100 km for 5 m2 sees an F-5E at 88 km, a C-130J at 200 km, an MQ-9 at 56.
- **Not known from DCS's files**: the target size DCS's detection distances are given for (REFERENCE_RCS_M2 = 5 is
  an assumption). Calibration built in: each network reports, for enemy aircraft within twice the radar's range,
  what the radar's own AI detects (`getDetectedTargets`, which works for enemies) next to our model;
  `logs/radar_calibration.jsonl` gets a line whenever either changes. A flight with an enemy aircraft (known type)
  flying towards and away from an airfield radar will show the distance where DCS's AI gains/loses it -> fit the
  reference.
- The agent reads it through `get_tower_view`: contacts appear and disappear by size and aspect, and a contact beyond
  85 % of its detection distance comes with "return: weak, near the limit of the radar for an aircraft this size".
- Hook loads `dcs/aircraft_rcs.lua` before the radar script: **DCS must be restarted** (hook code is read at start).
- Still to do: primary radar does not know type or callsign in reality (that is secondary radar/IFF): part of the
  Access tab work; radar clutter for low-flying targets; confirm `getDesc().RCS` live (rcs_source "dcs").

## The ATC agent: plan (handoff written 2026-11-02; built the same day, see "ATC agent built" above)

User's request (2026-11-02): build the ATC as an agent folder (`agents/atc/`, role "atc") with **the same brain**;
only the information it reasons from changes. **Permission given**: go to `E:/Laboratorio_AI/projects/DCS-ATC`, copy
whatever is useful, Python logic included, and adapt it here (copied and rewritten into this project; no import of,
path to or runtime dependency on DCS-ATC; English code, texts in locales). The user chose a fresh session for it.

DCS-ATC survey (README, module headers; read 2026-11-02). Its design: clearances and safety calls are decided in code
and spoken as fixed locale phrases; the brain only phrases free conversation. Facts come from its own capture hook.
- **Port (core ATC logic)**: `atc/facts.py` (runway in use with hysteresis, wind, QFE/QNH, spoken forms; compare with
  our `instructor/weather.py` and keep one), `atc/taxi.py` (taxi to the holding point / vacate and taxi to parking,
  runway geometry), `atc/takeoff.py` (takeoff clearance; keep the player off the runway while an arrival is inside
  runway_protect_nm), `atc/approach.py` (landing clearance on final, number two, go-around inside 2 nm, radar
  vectors to a 30-degree intercept at >= 4 nm with 3-degree heights on QFE), `atc/tower.py` (situations the tower
  calls on its own + Ledger memory of its decisions), `atc/traffic.py` (AI traffic), `atc/weather.py`
  (visibility/ceiling from the mission weather and clouds.lua: instrument conditions -> vectors), `atc/airfields.py`
  (station callsigns, e.g. Senaki-Kolkhi answers as "Senaki"), `atc/i18n.py` + its locales' `phrases`, `numbers`,
  `requests` (regexes for taxi/takeoff/landing/vectors in en/es/fr) -> our locales "atc" section. Their tests
  (tests/test_taxi, test_takeoff, test_approach, test_tower, test_facts, test_weather, test_traffic) come along.
- **Data it needs from DCS**: runways (we have them from the hook's weather), wind/pressure (have), the player's
  position/phase (have), **AI traffic around the field every second** (we only have what the airfield radars see:
  decide whether the ATC sees all traffic or only radar + window, per the realism rule), mission weather visibility
  and clouds (DCS-ATC's capture `atc_providers` "weather" line), the airfields' ATC frequencies (DCS-ATC reads
  `atc_providers`; we have tower frequencies in terrains/<map>/navigation.json).
- **Do not port** (we already have it or it is another product's shell): gui.py (tkinter), launcher.py, controller.py
  orchestration (becomes our Router + an ATC role), joystick.py, audio_devices.py, voice_out.py, brain.py (we keep the
  ChatGPT brain; the user said "the same brain").
- **Needs the user's decision/approval first**: `speech_patch.py` mutes DCS's own ATC voice by editing
  `DCS World/Scripts/Speech/speech.lua` (outside this folder); `native.py`, `inbound_trigger.py`, `keys.py`,
  `menu_keys.py` drive DCS's comms menu with keystrokes (abort takeoff so AI arrivals land, night Inbound for runway
  lights); `srs.py` copies the PTT and audio devices into SRS's config (would also solve "SRS plays on which device").
- **Questions to settle at the start**: (1) the ATC's frequency: one fixed agent frequency, or answer on each
  airfield's real tower frequency as DCS-ATC did ("nobody answers on a frequency no airfield uses")? The agent model
  allows "frequency_mhz" = "airfield" as a mode. (2) Who decides clearances: code (DCS-ATC, safe and exact) with the
  model voicing them, or the model deciding from tools ("same brain, other information")? Suggested: tools compute
  the decision (runway free, number two, go-around) and the model voices it. (3) Which DCS traffic the ATC may see.
- Plan: agent folder + role "atc" with its own ToolBox (atc facts/decisions as tools) -> port facts/weather/taxi ->
  takeoff -> approach/vectors -> tower calls of its own -> locales -> tests -> live flight.

**User's answers (2026-11-02, later), supersede the questions above:**
- Frequency: the ATC answers on each airfield's **real** frequency (agent frequency mode "airfield"; the station is
  the airfield whose frequency the pilot tuned).
- What it sees: **all mission traffic plus the radar** when it needs it.
- Clearances: **mixed**, some from code, some from the agent; depends on the airfield (a big civil airport follows
  every procedure strictly; a small military field may just say "wind ..., land runway 07"). Details to define later.
- Realism by era (user's idea): what each agent may know depends on the technology of the time. Transponder: the
  user asked how to make it realistic. Agreed direction to propose: primary radar = the mission radar units (position,
  no identity; no altitude without a height finder); secondary radar = only while the aircraft's transponder/IFF is
  on (code + Mode C altitude; the F-5E has an IFF panel: controls.json "IFF MODE 1/3A Code Selector Wheel", args
  209-214, so its state can be read from the cockpit); datalink/GPS (ADS-B style) only for aircraft that have it;
  "all traffic" = the god view, a choice per agent. Era presets (e.g. 1950s: window + primary radar; modern: all).
- GUI: every service gets two more tabs: **Brain/Connection** (its own model connection) and **Access** (which data
  sources the agent may use: window, primary radar, secondary radar, all traffic, datalink, weather, charts, library,
  black box...). Agent folder keys: "connection", "access".

## NEXT SESSION: Triggers tab + "add agent" (design agreed with the user, 2026-11-02 night; nothing built yet)

The user's big idea: agents for anything. Code gives each ROLE its tools; the GUI decides who each agent is.
- **Name = identity, editable** (also what a trigger refers to: "you are Tango", "you are Buenos"). Renaming the
  instructor to "Mechanic" or "Supervisor" = change name + system prompt (Mission tab). The **ATC stays fixed**: its
  name, role, tools and clearances are not editable (keep it as built).
- **"+ Add agent"** in Services: pick a role (squad now; wingman, commander later), give it a name and a
  **frequency, which is what switches it on** in whatever aircraft the pilot flies (`shared/agents.py` checks,
  folder `agents/<id>/`, mission.md from the role's default). Every aircraft flown needs its radios.lua for the gate.
- **Triggers tab** (per agent), the agreed mix:
  - **Events (mission -> agent)**: "when flag N turns on (or a DO SCRIPT event) -> the agent calls the pilot about
    <description>"; the description is for the model, which phrases it in character (like the ATC's own calls:
    Responder.call). The hook reads flags in the mission env (trigger.misc.getUserFlag) for the flags agents listen to.
  - **Actions (agent -> mission)**: "<name the model sees> + <description of when to use it> -> set flag M"; the code
    generates a tool per action (role tools stay fixed; these are added per agent). The hook sets flags with
    trigger.action.setUserFlag, only the flags listed for that agent (same fixed-list rule as the task port).
  - The mission maker then uses normal editor triggers (FLAG M ON -> AI TASK PUSH ...).
- Later: a DO SCRIPT one-liner for missions (`if DCSInstructorTasks then ... event(...) end`) with free text; wingman
  role (flight orders through the radio-menu bridge, sendRawMessage to the flight) ; automate role setup more.
- **Confirmed by the user (2026-11-02 night), flags it is** (not pressing F10 items for him): his whole editor job is
  a zone + a trigger ("unit Paco in Zone 1 -> FLAG 10 ON"); in our Triggers tab, flag 10 -> instructions in plain
  words ("if you see the helicopter, call and tell the pilot you see it and where you are relative to it"). When the
  flag turns on, the agent reads the instructions with its situation (realism: only what it sees) and makes the call
  in character, deciding itself (no helicopter in sight -> says so, never invents). The user is a casual player:
  keep the editor side to zones + "FLAG ON", explain in the GUI with an example.
- Build order proposed: hook flags (read watched / set listed) + tests -> agent.json "events"/"actions" + generated
  tools + event calls -> GUI Triggers tab + editable name + "+ Add agent" -> a test mission (Buenos: flag 10 when the
  helicopter enters Z1 -> they call; action "smoke ready" -> flag 20).

### Step 1 built: mission flags in the hook (2026-11-02, night; offline-verified with DCS's luae; needs a DCS restart)

- Task port (47824) gained two requests. `flags\t<watched>\t<settable>` ('|' separated names, `[A-Za-z0-9_-]`, max 40
  chars; either list may be empty) replaces the hook's single list for all agents (the program sends the union and
  resends it now and then, like `watch`). `set_flag\t<name>\t<value>` works only for a flag in the settable list
  ("flag not listed: N" otherwise); a watched flag is not settable unless listed as such.
- `dcs/instructor_tasks.lua`: `T.flags(names)` (trigger.misc.getUserFlag) and `T.set_flag(name, value)`
  (trigger.action.setUserFlag). The hook sends `{type: flags, flags: {name: value}}` every second while flags are
  watched; `CockpitState.flags` / `flags_at` (an empty Lua table arrives as a list: kept as {}).
- `client/dcs_tasks.TaskChannel.flags(watched, settable)` / `set_flag(name, value=1)`, names checked as the hook does.
- Tests: test_hook (flags read, only listed set, bad names refused, nothing reported until listed; TaskChannel lines;
  state). 214 pass. Not yet: who decides the lists (step 2: agent.json "events"/"actions", edge detection off -> on,
  the agent's call, generated action tools).

### Step 2 built: events, actions, generated tools (2026-11-02, night; offline-verified, model checked; not flown)

- agent.json (any role but the ATC, which stays fixed):
  `"events": [{"flag": "10", "instructions": "<plain words for the model>"}]`,
  `"actions": [{"name": "smoke_ready", "description": "<when to use it>", "flag": "20", "value": 1}]` (value 1 by
  default). Flag numbers are free: the mission maker picks any (number or name), the same in the editor and the tab.
- `shared/triggers.py`: `events()` / `actions()` (malformed entries left out; an action cannot take its role's tool
  names or repeat), `check()` for the GUI (ValueError "events.N" / "actions.N"). `FlagWatch`: sends the hook the union
  of every active agent's lists (again every 5 s or when they change) and fires an event when a watched flag goes from
  0 to anything else; the first value seen is only the baseline (a flag already on at start does not fire); stale
  flags (> 5 s) ignored. `TriggerToolBox`: wraps the role's toolbox and adds namespace "mission" with one tool per
  action of that agent (read live from agent.json); calling it sends set_flag and waits up to 3 s for the hook.
- `instructor/voice_loop.py`: every non-ATC agent's toolbox is wrapped; `mission_events()` runs with the own calls
  (every 2 s, now also without desks); `Responder.event()` queues the event with the pilot's phrases; `event_call()`
  waits while the pilot transmits (max 20 s), asks the brain with EVENT_PROMPT (instructions + "never invent what you
  cannot see" + language: the pilot's, else the agent's voice language, now passed as the Responder's language) and
  transmits; a brain error stays silent (logged). Logs: flag_on, mission_event. The Responder thread now survives an
  exception in one answer.
- Live model check (scratch, simulated, gpt-6.1-terra): flag 10 with the helicopter 800 m north -> "Colt one one, this
  is Los Buenos. We are eight hundred metres south of your helicopter" 2.5 s; pilot "I see your green smoke" -> smoke_ready ->
  set_flag 20, 4.2 s; helicopter 30 km away -> "We do not have the helicopter in sight..." 2.0 s. First try answered in
  English (no pilot words yet): fixed with the voice language. Tests: tests/test_triggers.py (15); 229 pass.
- Next: step 3, GUI (Triggers tab with an example, editable name, "+ Add agent"), then the test mission.

### Step 3a built: editable name and "+ Add agent" (2026-11-02, night; offline-verified, screenshots; not run live)

- **Name = identity**: editable in the Frequency tab (already was) for every agent but the ATC (`FIXED_ROLES`: the
  server refuses "fixed_name", the field is disabled with a note). New mission field `{name}` (ChatGptBrain.agent_name);
  `Router.refresh` gives the running agent its new name (brain, Responder name in logs/Status, a squad desk's name);
  renaming a squad also sets "squad.name" (who it says it is).
- **"+ Add agent"** (Services): role (instructor or squad, `ADDABLE_ROLES`; the ATC is one and fixed), name, frequency,
  modulation, and for a squad the infantry group's name in the Mission Editor. `POST /api/agents` ->
  `AgentStore.add` (role defaults, active, channel checked against the others; folder id from the name, unique:
  "Tango 2" -> tango-2, tango-2-2) + the role's default mission.md. A running program makes its responder at once
  (desks are now made on demand, `make_desk`, so a squad added while running has its squad tools; own_calls walks a
  copy of the desks). Then the GUI opens the new agent.
- CSS: disabled inputs look disabled; `main` no longer forces 560 px (overflowed narrow screens). Checked at 1280 and
  520 px (headless Chrome on Windows cannot go below ~500 px wide).
- Tests: test_gui (add, refusals: atc role / no group / taken frequency / empty name / no frequency; rename, ATC fixed,
  squad name). 232 pass. Not yet: removing an agent from the GUI; the Triggers tab.
- **Delete agent** (Frequency tab, red button, confirm; not for the ATC: "fixed_agent"): `POST /api/agents/<id>/delete`
  -> `AgentStore.remove` moves the folder to `backups/agents/<id>-<YYYYmmdd-HHMMSS>` (move it back to agents/ to
  restore); `Router.remove` drops its responder and, through `router.forget`, its desk (a new agent may reuse the
  folder name). Off the air at once; its frequency is free. Tests: test_gui (3). 234 pass.

### Step 3b built: the Triggers tab (2026-11-02, night; offline-verified, screenshots; not run live)

- Tab "Triggers" (no longer "coming soon"): **events** (flag + "what the agent does") and **actions** (action name,
  "turns on flag", "when to use it"), rows added/removed freely, one Save; an **Example** panel with the four steps
  (editor: zone + UNIT INSIDE ZONE -> FLAG ON 10; here: event 10; here: action smoke_ready -> 20; editor: FLAG IS
  TRUE 20 -> next thing) and the note that any flag number or name works if the same on both sides. The ATC shows
  "fixed: no triggers". Action names are turned into tool names on the page ("Smoke ready" -> smoke_ready).
- Server: `POST /api/agents/<id>` takes "events"/"actions" (both lists checked together with
  `triggers.check(..., triggers.role_tool_names(role))`, saved normalized; an ATC with triggers is refused). The
  running program uses them at once (FlagWatch and TriggerToolBox read agent.json live). Errors name the row
  ("Event 2: ...").
- The page's error bar is now sticky at the bottom of the window (it was at the end of long screens, out of sight).
- Tests: test_gui triggers API; 235 pass. Screenshots at 1280 and 520 px, and an invalid save showing the row error.
- **Next: the test mission** (Buenos, flag 10 when the helicopter enters LZ -> their call; action smoke_ready -> flag
  20 -> e.g. a message) and a live flight; needs a DCS restart for the hook's flags (step 1).

### Triggers assistant (user's idea, 2026-11-02 night; offline-verified, model checked live in the page; not flown)

The mission maker tells it in plain words, in any language, what should happen; it fills in the agent's rows.
- `gui/trigger_assistant.py` + `gui/trigger_assistant.md` (English prompt: how events/actions work, DCS Mission Editor
  terms FLAG ON / FLAG IS TRUE / UNIT INSIDE ZONE, events fire 0 -> on so FLAG INCREASE fires only the first time,
  realism: event instructions only about what the agent can see, ask for a missing flag, never saves). Tools
  (namespace "triggers"): add_event, add_action, remove_event, remove_action, only on the draft (the rows the page
  sends, saved or not), checked like the server (role tool names reserved, no repeats). Situation: agent name/role,
  its role's tools with descriptions, the numbered rows, flags other agents use. Same brain connection; one
  conversation per agent while the GUI runs ("New conversation" resets).
- `POST /api/agents/<id>/triggers/assist` {message, draft} -> {reply, draft, tools} or {error: kind}; `/reset`. The
  ATC is refused. Page: "Triggers assistant" panel in the Triggers tab (Enter sends); changed rows are outlined in
  blue; the maker reviews and presses Save triggers (normal checks).
- Live (gpt-6.1-terra): the user's own example in Spanish (Paco in zone 1 -> flag 20, Buenos calls; "I see the smoke"
  -> flag 21 for the convoy) -> add_event 20 + add_action convoy_go 21 and the editor steps, 6.2 s; "warn me when the
  enemy convoy leaves the base?" -> explained Buenos would only relay the mission's signal (cannot see it) and asked
  which flag, suggesting 22, 2.6 s. In the page (headless Chrome, temp copy of the agents): flag 30 row added and
  outlined. Tests: tests/test_trigger_assistant.py (7); 242 pass.
- Open: events fire only on 0 -> on (an "every time it goes up" option for FLAG INCREASE was offered, not built).

### First live trigger test and the squad's group (2026-11-03, morning)

- Flown (session 20261103-065328_Mi-8MT, Caucaso.miz): flag 3 fired the Buenos event live (`EVENT for Buenos: flag
  3`), the call came in character. But the user had renamed the infantry group in the Mission Editor to "Tango"
  (units still Buenos-1..3) while agent.json kept `squad.groups: ["Buenos"]`: the hook reported no such group and
  the squad said "the group no longer appears in the mission". Renaming the agent never changed its group, and the
  group of an existing squad was not editable.
- Fix: Frequency tab of a squad has "Group name in the mission" (`POST /api/agents/<id>` {"group"} ->
  `AgentStore.update` -> `squad.groups = [group]`, same check as "+ Add agent"; refused for other roles).
  `Router.refresh` -> `SquadDesk.configure(agent)`: the running squad follows the new group at once.
- Latent bug fixed before adding a second squad: the hook keeps ONE watch list and each squad sent only its own, so
  two squads would have overwritten each other. `TaskChannel.watch(groups, owner)` now sends the union of every
  squad's groups; a squad that stops asking (off, deleted) drops out after 15 s. Tests: 245 pass.
- Flown 07:35-07:42 (session 20261103-073539_Mi-8MT): Buenos renamed to Tango with group Tango, a new squad Victor
  (256.000) added from the GUI; flag 3 -> Tango's call, flag 4 -> Victor's call, move_to and hold used. All worked.
- SRS client crashed at 07:40:55: SR-ClientRadio 2.4.1 AccessViolationException in
  `ClientAudioProvider.AddCockpitAmbientAudio` (its cockpit ambient effect, Mi-8 loaded 07:37) on the mic capture
  thread: an SRS bug, not ours. Workaround: SRS client settings, cockpit ambient noise off
  (`AmbientCockpitNoiseEffect=true` in its default.cfg). Its `MinimiseToTray=true` hides the client in the tray
  when minimised. New "Open SRS" button on Status (`POST /api/srs/open`): starts what is closed and brings the
  client window out of the tray (instructor/srs.open_apps + show_client). 246 pass.
- With the user's approval (2026-11-03 07:51, client closed) the SRS client's config was changed:
  `AmbientCockpitNoiseEffect=false` (Client/default.cfg), `MinimiseToTray=false`, `StartMinimised=false`
  (Client/global.cfg). Originals kept in backups/srs-client-20261103-075057.

### Shared frequencies: several agents on one channel (2026-11-03, user's design; offline-verified; not flown)

- Each agent keeps its own brain (mission, history, tools); a dispatcher in front picks who answers. The gate still
  picks the channel at the press (the first agent on it, for dictation); after recognition `Router.heard` takes
  every active agent on that channel (`AgentStore.on_channel`) and `shared/dispatch.Dispatcher.route` decides:
  - alone on its frequency -> it answers, named or not (as before);
  - named (name or agent.json "aliases") among the first 6 words -> those, in the order named; tolerant of
    dictation errors (no accents/punctuation, difflib >= 0.8 for words of 4+ letters);
  - no name, "all stations" words -> every agent, each in turn (Voice speaks one clip at a time); the phrase is
    shown and recorded once. A name wins over the words ("Tango, is everyone ready?" -> Tango);
  - no name -> the agent that last talked on this channel within 60 s (pilot to it, or its own transmission:
    event calls too); else nobody answers, as on the air.
- No language in code: the "all stations" words are in locales/*.json "radio.all_stations", every locale at once.
- Everyone hears everything: the others' brains get `Brain.overheard` ("Pilot to Tango: ...", "Tango: ...") as a
  developer note in their history (OVERHEARD_KEPT = 6 extra items); each `Responder.transmit` calls `on_air`.
- The channel check no longer refuses a shared frequency (two "airfield" agents are still refused). GUI: Frequency
  tab "Other names (comma separated)" (max 8, 32 chars; not for the ATC), "How it works" text updated. Log event
  "dispatch" for shared channels. Tests: tests/test_dispatch.py (7) + test_gui (shared routing, aliases API,
  channel checks); 254 pass. Screenshot of the Frequency tab checked (`?nowatch`).
- Next: a live flight with Tango and Victor both on 255.000 AM.

### Internal flags: agent to agent without the Mission Editor (2026-11-03, user's idea; offline-verified; not flown)

- A Triggers row (event or action) marked **Internal** (`"internal": true` in agent.json) uses a flag that lives in
  the program only (`shared/triggers.InternalFlags`, one per run, all start at 0). Tango's action
  `confirm_in_z1 -> tango_z1 (internal)` turns it on; Victor's event `tango_z1 (internal)` fires on the next
  mission_events tick (<= 2 s), on any frequency. Fires on each 0 -> on (no baseline, unlike DCS flags); an action
  with value 0 turns it off. An internal flag and a DCS flag of the same name are different flags; internal ones
  never go to the hook's lists. Log: flag_on with "internal"; console "EVENT for Victor: internal flag tango_z1".
- GUI: "Internal" checkbox per row, hint, an example line; texts say "When a flag turns on".
- Triggers assistant: add_event / add_action take "internal"; the situation now shows the other agents' rows
  ("other_agents", not only their flags), so in Victor's tab it links to Tango's action or says what to ask for in
  Tango's tab. Live (gpt-6.1-terra, temp copy of the agents), the user's own example in Spanish: Tango tab ->
  add_action confirm_z1 tango_z1 internal, and it told what to ask in Victor's tab; Victor tab -> add_event
  tango_z1 internal, "nothing in the Mission Editor". Tests: test_triggers Internal (3); 257 pass.
- First flight with a shared frequency (2026-11-03 08:39-09:05, session 20261103-083922_Mi-8MT): Tango and Victor
  both on 255.000 AM. Every call went to the right squad (name 9x, follow-up 9x), "todos pueden ir a Z2" -> both
  (all), "todo Victor y todo Tango" -> both by name, each move_to; Victor popped smoke on an unnamed follow-up. One
  unnamed report ("veo un malo detras del edificio") got no answer: 2.7 min since the last exchange (> 60 s).
  The internal flag was not exercised: Tango had no confirm_z1 action saved (only Victor's internal event), and Tango
  never reached Z1 (it boarded the helicopter). Model answers with tools took 9-16 s.
- Tests were writing into the real logs/dictation.jsonl (dispatch and mission_event lines dated during test runs):
  tests/quiet_log.py redirects it; imported by test_gui and test_triggers.
- Next (agreed idea, not built): "When you hear..." events (the model decides whether something overheard on its
  frequency matters; one model call per overheard line per agent with such rules).

### Internal flag flown; what reaches the squad agents (2026-11-03 midday; offline-verified; not flown)

- Flight 2026-11-03 ~12:1x-12:32 (Mi-8MT, Tango + Victor on 255.000 AM): the internal flag worked live. Tango's DCS
  event (flag 3) -> it called confirm_z1 -> Victor's internal event fired at once. Dispatch again always right.
- Problems seen in the log, and fixes:
  - Victor popped smoke (pop_smoke ok), and on "veo el humo" said it "could not confirm its squad had popped smoke":
    the brain's history kept only words, never the tool calls. Now `ChatGptBrain.ask` keeps the orders given
    (tools listed in `toolbox.remembered`: every squad tool and every Triggers action; read-only tools of the
    instructor/ATC are not kept, they would be stale data) as a developer note between the pilot's words and the
    reply: "(What you did with your tools for that: pop_smoke {"color": "green"}: ok, green smoke)".
    HISTORY_ITEMS = 3 * HISTORY_TURNS + OVERHEARD_KEPT.
  - Victor's internal event answered "I cannot confirm Tango is in Z1": the event carried only the instructions.
    Now an internal flag remembers what turned it on (`InternalFlags.set(..., cause)`: "Tango used its action
    confirm_z1, which it uses when: <description>"); FlagWatch puts it in the event as "cause" and the event prompt
    adds `EVENT_CAUSE` + cause. Logged in mission_event "cause".
  - Speech recognition heard "C1" and "si dos" for Z1 / Z2; the squads asked to confirm. squad/mission.md now says
    the pilot's words may be misheard: take the zone or group that sounds like it, act, and say its real name.
  - Victor went from 4 to 1 soldiers and never said so; "Victor, attack group M1" got "I have not identified
    group M1": the squad saw no enemies and had no attack order. Now:
    - dcs/instructor_tasks.lua status adds per group "enemies" (up to 5 enemy groups its living units' AI detects,
      `Controller:getDetectedTargets()` - enemies only, realistic: what the soldiers see or hear): group, living
      units, type, lat/lon of the detected unit.
    - situation: "enemies_in_sight" (group, units, type, distance_m, direction; or "none") and
      "squad.killed_since_your_last_message" (counted once per drop, SquadDesk.losses).
    - New squad tool `attack(enemy_group)`: only a group in enemies_in_sight (loose name match); Lua `T.attack`:
      ROE open fire + off-road route to the target's first living unit; hook TASKS and client ACTIONS gained
      "attack". pop_smoke now refuses while aboard.
  - The squad mission prompt says to report losses first and enemies when it matters, and attack only on the
    pilot's order. squad/mission.md copied over agents/buenos and agents/victor mission.md (they were unedited
    copies of it).
- Not yet verified in DCS: getDetectedTargets on infantry and the attack route (Lua syntax checked with luae).
  DCS must be restarted for the hook/task Lua changes. Tests: test_squad Combat (3), test_brain orders kept,
  test_triggers cause; 261 pass.
- Seen, not changed: the mission's default trigger zones ("Nueva zona de disparador-N", 50-145 km away) fill
  mission_zones (the user's old triggers on that map); Tango re-called confirm_z1 when only mentioned in a call to
  Victor (harmless, flag already on).

### Condition events: "when this happens" without flags (2026-11-03 afternoon, user's design; offline-verified)

- The user asked whether squad damage could be a "When you hear" event. No: hearing is for radio lines (a model
  call per line); damage is in the mission data our code already reads. So a third kind of event row, in the same
  Triggers tab: `{"when": casualties | enemies_seen | helicopter_seen | zone_reached | squad_lost, "zone"
  (zone_reached), "about": "<squad agent id>" (missing: its own squad), "instructions"}`.
- `SquadDesk.facts()`: lost / alive / size / enemies (bool) / helicopter in sight / zones inside (radius from the
  mission, else ZONE_RADIUS_M 300), None while unknown or aboard (keeps the baseline).
  `shared/triggers.ConditionWatch(agents, desks.get)` compares with the last facts per squad and fires on a change
  only (first reading = baseline), each row at most once per CONDITION_COOLDOWN_S (60 s). The event gets "cause"
  ("your squad lost 2 soldiers (1 of 4 left)", "Tango's squad was wiped out"), so the agent knows what happened.
  voice_loop ticks it with the flags (`mission_events(condition_watch, router)`); log "condition".
- `triggers.events()` returns flag rows and condition rows; `flag_events()` / `condition_events()` split them
  (FlagWatch uses only flag rows). GUI: each event row has a "When" select (A flag turns on / the five conditions),
  "Whose squad" (squads from GET /api/agents/<id> "squads"; list "conditions") and "Zone"; flag and Internal only
  for flag rows; example text. Triggers assistant: tool add_condition_event, situation "squad_agents", prompt
  explains them and prefers them over flags when they fit.
- Tests: test_triggers Conditions (4), test_squad facts, test_trigger_assistant condition, test_gui saving a
  condition row; 267 pass. Screenshot of the tab checked. Not flown.
- Still the agreed idea for later: "When you hear..." (model decides on overheard lines).

### First flight with condition events, and fixes (2026-11-03 13:41-14:03, session 20261103-134112_Mi-8MT)

- Flown with the rows I wrote (backups/agents-triggers-20261103-133304 has the previous ones). Worked live:
  Victor zone_reached Z2; flag 3 -> Tango confirm_z1 -> Victor's internal event, now saying "Tango confirmed Z1"
  (cause works); Tango enemies_seen ("six enemy soldiers, group M1, north, 470 m"); Victor casualties; Victor's
  attack on M1 (Lua T.attack ok, getDetectedTargets works on infantry); "C2" -> Z2 understood.
- Problems and fixes:
  - Victor's casualties and enemies_seen fired in the same tick: two calls back to back. ConditionWatch now merges
    one agent's conditions of a tick into one call (`merged`: when "a+b", instructions as a list, causes joined).
  - The second call said "we detected enemies but none in sight now", and Victor, still losing men, refused
    "attack" twice ("no enemies in sight"): DCS's detection flickers. SquadDesk remembers enemies (ENEMY_MEMORY_S
    180): situation "enemies_out_of_sight" (last seen s ago, last known distance/direction), attack accepts them,
    facts "enemies" stays true ENEMY_HOLD_S 30 (no re-fire on a flicker). Prompt: out-of-sight enemies are still
    there in a firefight.
  - At 14:03:43, leaving the mission, both squads "were wiped out" at once (groups stop existing). squad_lost now
    needs LOST_CONFIRM_S 8 s of "not existing" with the mission still running (the player's aircraft in fresh
    traffic); facts are None meanwhile (baseline kept).
  - The user's hint: "Victor, Tango is on transport, tell me when you see the helicopter" -> Victor promised ("I'll
    tell you") with no way to do it. New squad tool `tell_pilot_when(condition, request, zone?)`: a one-shot condition
    row of this run (desk.requests, REQUESTS_MAX 5, REQUEST_TTL_S 30 min) that ConditionWatch fires once and removes;
    refused when already true ("tell the pilot now"); situation lists "pilot_asked_you_to_tell_when". Prompt: never
    promise a call you cannot make. Kept in history like every order.
- Seen: Victor's first call (zone_reached right after start) said "aeronave sin indicativo": the callsign had not
  arrived yet. Not changed.
- Tests: test_squad (wiped out confirm, enemy memory, tell_pilot_when), test_triggers (merged, request once);
  272 pass. squad/mission.md copied again over agents/buenos and agents/victor (still unedited copies).

### Radios of any aircraft (2026-11-03 evening; harness-verified, not flown)

- The user's son flew an F-16C_50 on Syria (2026-11-02 18:36-18:43, Akrotiri): the ATC was never heard. Log: every
  press "no radio data: listening anyway" -> the default agent (Instructor, 251.0) answered on its own frequency.
  Cause: SRS's table is still not reachable from our export (dcs.log 2026-11-03: "SRS radio data not found yet
  (debug library present)"; never found since 2026-11-01), so radios came only from modules/<aircraft>/radios.lua,
  which exist for F-5E-3 and Mi-8MT only. The voice itself is fine for any aircraft (SRS external audio on the
  agent's frequency); what failed is knowing which frequency the pilot is on.
- Fix 1, any aircraft and mods (the user installed the A-4E-C mod to try): without SRS data and without a radios.lua,
  the export scans GetDevice(1..100) once per aircraft for devices that answer get_frequency(); each tick reads them
  (is_on if it has it, get_modulation 0/1), communication bands only (30-400 MHz, not 108-118: ILS/VOR). dcs.log
  "radio devices found ..." and "radios from scanned devices"; devices on a navigation band at scan time are left
  out, so radio 1 / 2 are the communication radios in device order. The selected radio is unknown: "selected" is
  left out (first version picked the radio tuned to an agent; the user rejected that as unrealistic, see below).
- One push-to-talk button per radio (user's choice over "pick by the name", 2026-11-03: "otherwise what are the
  frequencies and radios for"): settings "ptt" {joystick, button} = the main button: the radio the aircraft has
  selected (SRS / module selector) or radio 1; optional "ptt.radio2" {joystick?, button} = always radio 2.
  client/ptt.RadioButtons.held() -> 0 / 1 / 2; press_agent and FrequencyGate.check(ptt_radio) use
  `shared/radio.transmit_radio(radio, ptt_radio)`; the gate keeps ptt_radio, and voice_loop applies it to
  state.radio after every export message, so the ATC desk sees the radio last transmitted on. tools/ptt_pick.py
  --radio2 [--clear], utilities/PTT_PICK_RADIO2.cmd; GUI Controls shows "Push-to-talk, radio 2". Press log has
  "ptt_radio". Listening is unaffected: every agent's voice goes to SRS on its own frequency, heard on any radio.
- Fix 2, SRS lookup: SR is now recognised by its shape (exporters table + exporter function), not only by the
  upvalue name; dcs.log says how many upvalues were seen (0 = the debug library gives nothing in DCS's export).
- Harness: f16 mode (devices 36 305 MHz, 38 127 MHz, 40 a 110.3 MHz nav receiver); fake SR got `exporter`.
  test_radio_gate +1; 273 pass. To check live: F-16 / A-4E-C, dcs.log lines, ATC heard on the tower frequency.
- Tests: test_radio_gate scan (+ transmit_radio, Mi-8 selection kept) and two buttons; 274 pass. Not flown.

### Radio range for the squads, terrain included (2026-11-03 evening; offline-verified, not flown)

- The user asked whether SRS gives the radio range. Not for us: the pilot's words never go through SRS (dictation),
  and the agents' voices go out through SRS ExternalAudio with no position (frequency, modulation, coalition, name
  only), so SRS applies no range or line of sight to them (as far as known of SRS: not checked). Our only range was
  the ATC's radio horizon (atc/desk.radio_range_nm, no terrain); squads had none (heard at 200 km).
- Now, like the airfield radars: dcs/instructor_tasks.lua status(names, player_name) adds per group "radio"
  {line_of_sight (land.isVisible from the leader + 2 m to the player's unit + 1 m), distance_m}; the hook passes
  the player's unit (last_unit_name, from the callsign lookup). SquadDesk.radio_link: in range = line of sight and
  distance <= min(radio horizon 4.12 * (sqrt(2 m) + sqrt(aircraft height above the squad)), SQUAD_RADIO_MAX_KM 30);
  unknown (no "radio") or aboard = reachable. Out of range: `SquadDesk.prefilter` -> '' (the squad does not hear
  the pilot; logged "unanswered"), and an event call is held (`Responder.hold`, log "event_held") and made once
  back in range (EVENT_HOLD_S 600, else "event_dropped"). The situation shows "radio_with_pilot".
- Not done: terrain for the ATC (still horizon only); the voices themselves are not cut by SRS.
- Then, after talking it over: SRS's ExternalAudio does take --latitude/--longitude/--altitude, but the user's SRS
  server has LOS_ENABLED=false and DISTANCE_ENABLED=false, and SRS knows nothing of a ground unit's radio. Decided:
  our own model only, no position to SRS. Made asymmetric and per radio: Lua "radio" also gives kind (infantry
  leader -> "manpack", antenna 2 m; else "vehicle", 3 m). `radio_link` -> hears_pilot (line of sight within the
  horizon: the aircraft's radio) and pilot_hears (also within the ground radio's power, RADIOS manpack 30 km /
  vehicle 60 km). prefilter uses hears_pilot; event calls wait on pilot_hears; an answer given while pilot_hears is
  false is not transmitted (the squad heard and acted; log "reply_not_heard"). 280 pass.

### Wingman agents (2026-11-03 night, user's plan; offline- and harness-verified, not flown)

- Real life (told the user): a flight has its own intra-flight frequency; two-radio jets keep one radio on it and
  the other on ATC. User's design: every wingman of the flight on one frequency (e.g. 316.000), called by number or
  name, "Colt flight" for all (each answers in turn) - the shared-frequency dispatch.
- Orders: DCS's F1 menu (Scripts/UI/RadioCommandDialogPanel/Config/LockOnAirplane.lua, read 2026-11-03): each item
  is `sendMessage.new(Message.wMsgLeaderX, extra...)`; CommandMenu.performCommands passes the path's "parameter"
  first: Wingman N -> N - 1, Flight -> 5. dcs/patches/radio_bridge.lua WINGMAN table (34 orders: engage my target /
  my enemy / bandits / air defences / ground targets / ships, primary then rejoin / RTB, pincer x4, RTB, route,
  anchor, tanker, radar/ECM/smoke on-off, cover me, jettison, rejoin, 6 formations + close/open/close group), sent
  as `data.pComm:sendRawMessage(Message[name], {N-1 or 5, extra...})`; formations always to the flight (as the
  menu); the player's radio is NOT retuned (selectAndTuneCommunicator skipped: our own PTT talks to them; check live
  that DCS obeys without it). WINGMAN_REPEAT_S 2. client/dcs_bridge.py COMMANDS += wing_<order>, target "2"-"4" or
  "flight". bridge_harness: wingman 2 -> "1,false", formation -> "5", missing #4 unavailable, #7 refused.
- Knowledge: dcs/instructor_tasks.lua `flight_of(player)` in status ("flight"): units 2..initial size of the
  player's group: exists, type, life/life0, fuel, in_air, position, velocity, weapons (getAmmo: name, count, kind
  gun/missile/rocket/bomb), enemies its own controller detects (enemies_seen_by, now with alt_m), radio line of
  sight to the player. The hook now sends the groups message whenever the player is in an aircraft (not only with
  watched groups). CockpitState.wingmen.
- wingman/desk.py WingmanDesk (agent.json "wingman": {number}): situation you_are "Viper, Colt 1-2 (number 2)",
  you (damage %, fuel %, weapons), you_from_leader (distance nm, clock from the leader's true heading, height),
  enemies_detected (clock from the leader's heading, distance, altitude; remembered ENEMY_MEMORY_S 120 with
  last_seen_s_ago), radio_with_leader (line of sight + horizon of both heights, no power cap: aircraft radios).
  prefilter / can_reach_pilot as the squads. facts: damage, fuel / fuel_low (< 25 %), out_of_weapons (no
  non-gun weapons), enemies (held 30 s), lost. UNIT = "aircraft" (causes say "your aircraft ...").
- wingman/tools.py: engage, complete_mission, pincer, go_to, formation (flight), rejoin, cover_me, radar, ecm,
  smoke, jettison_weapons, tell_pilot_when (enemies_seen, fuel_low, out_of_weapons, damaged); all remembered.
  wingman/mission.md: brevity words, number first, clock positions, report hits at once.
- shared/triggers.py: CONDITIONS += damaged (a new hit >= DAMAGE_STEP 5 %), fuel_low, out_of_weapons; the
  "tell me when" logic moved to `PilotRequests` (SquadDesk and WingmanDesk inherit it).
- Dispatch: wingmen also answer to their number (locales "radio.numbers": two/dos..., and the digit: "Colt 1-2"
  is heard "colt 1 2"); "flight" / "escuadrilla" added to all_stations; whichever comes first decides ("Colt
  flight, two bandits north" = all, "Tango, are you all ready" = Tango).
- agents.py: role wingman (ADDABLE_ROLES, default_wingman 316.000 AM voice M4, checked_number, update "number");
  GUI: "+ Add" number select, Frequency tab "Number in your flight"; Triggers "Whose unit" lists squads and
  wingmen; new condition labels. Trigger assistant knows the wingman conditions.
- speech_mute.lua: once our bridge has given a wingman order (DCSAIServiceBridge.wingmen_ordered), DCS's leader
  calls (wMsgLeader range) and wingman replies (wMsgWingmen range) are silenced; before that DCS's wingmen keep
  their voices. Needs DCS screen "Update" (patch files changed) and a DCS restart. Unknown until flown: whether
  speech.lua runs in the same Lua state as the radio panel (if not, the flag is never seen: double voices).
- Tests: test_wingman (13), bridge harness; 293 pass. Screenshot of a wingman's Frequency tab checked.
- First flight (2026-11-03 evening, Batumi, Chevy 1-1 + AI #2 "Viper"): the wingman answered on its frequency
  ("Chevy 2, do you read me" -> "Two, I read you"; "follow me" -> rejoin, sent "wMsgLeaderJoinUp to wingman 2"). But our
  ATC's abort_takeoff (sent to release DCS's AI arrivals) cancelled the whole flight's departure in DCS: the AI
  wingman never took off. Fix in atc/desk.py: when the player leads AI wingmen (`leads_flight`: state.wingmen
  fresh, one exists) no abort_takeoff; `dcs_flight_clearances` (ground_calls) sends DCS's own request_taxi when our
  ledger has the taxi clearance and request_takeoff with the takeoff clearance, once per departure (reset when
  airborne; not after landing). test_atc +1. Then, user's design: pause and resume instead of the trade-off: while
  an AI arrival is on final within ARRIVAL_RELEASE_NM 10 and the flight is not cleared for takeoff yet, the
  flight's departure is aborted in DCS (so DCS lets it land; our own takeoff clearance already holds the player
  inside 5 nm); after ARRIVAL_CLEAR_S 20 with the final clear, DCS's request_taxi again, then request_takeoff with
  our takeoff clearance. Other AI departures are DCS's own (we only touch the player's flight). test_atc +1;
  295 pass. Not flown yet: check that the AI wingmen follow a re-asked taxi after an abort.
- Second flight (2026-11-03 evening): orders "sent" (close formation, engage ground targets, RTB), DCS showed them
  as subtitles, but wingman 2 did nothing. The pilot's radio was on 361.000 (the agent's); DCS's AI wingmen hear
  their leader only on the flight's Mission Editor frequency, and the bridge had skipped the menu's
  selectAndTuneCommunicator. Fixes: the bridge selects the wingman's communicator first, as the menu does (pcall;
  retunes only with easy comms); instructor_tasks.lua sends "flight_radio" {mhz, modulation} from env.mission
  (the player's group frequency); CockpitState.flight_radio; WingmanDesk.off_channel(): when the agent's channel
  is not the flight's, orders are refused with "the flight's radio in the mission is 305.000 AM ... set your
  frequency to it" (the model tells the pilot) and the situation has "radio_problem". GUI text says to use the
  group's mission frequency. Formation tool description says where each formation puts the wingman ("a mis tres"
  = echelon right; no "go ahead" order exists). 296 pass. Needs DCS "Update" + restart. Not flown.

### The F1 wingman does not work; next: a scripted wingman (2026-11-03 night, ideas only)

- Third flight (A-4E-C mod, Batumi, frequency fixed): every order "sent" (logged in dcs.log by the bridge), the
  wingman obeyed nothing, and the user's own F1 menu orders did not work either: a DCS / mod matter, not ours (the
  A-4E-C has its own radio; its AI also detected no ground targets: "enemies_detected: none"). User's verdict: the
  F1-menu wingman will not do. The role "wingman" (wingman/, F1 orders through the bridge) stays in the code for now;
  it will be replaced by the scripted one.
- ATC back as before (user's decision): our ATC manages only the player's departure (abort_takeoff once per
  departure); the flight clearances (leads_flight / dcs_flight_clearances / ARRIVAL_RELEASE_NM) were removed. AI
  departures and arrivals are DCS's own. 294 pass.
- Next session, user's design: the wingman is a SEPARATE AI group placed in the Mission Editor (like a squad), with
  LATE ACTIVATION; the pilot starts it by radio ("Manolo, start up, we are leaving" -> a tool activates the group:
  Group:activate()). Controlled by script tasks through the hook, as the squads (works with any aircraft, mods too;
  no DCS wingman voices). Tasks from DCS's own editor data (MissionEditor/modules/me_action_db.lua, read
  2026-11-03): Follow {groupId, pos = {x = -500 distance, y = 0 elevation, z = 200 interval}} for formation at a
  chosen offset ("a mis tres a 200 m"); Escort (follow + protect, engagementDistMax) for "cover me"; AttackGroup /
  AttackUnit (an enemy it detects); Bombing (a point: a mission zone, or where a squad popped smoke); Orbit; Land
  (RTB); CAS / CAP / SEAD en-route tasks; options ROE, reaction to threat; pushTask so it goes back to following
  after an attack. Reuse: squad desk pattern (watch group, enemies, radio_path, conditions, tell_pilot_when,
  number/name on the flight frequency). Accepted: AI following loosely is DCS's.

### ATC review of the 2026-11-03 evening flights (2026-11-04)

The user reported: at Batumi the ATC would not speak Spanish; their son said the F-5 did not work well and the A-10
did not work at all. From logs/dictation.jsonl and the black box:
- **Batumi, English only** (A-4E-C, 16:39-17:27): agents/atc/agent.json voice.language is "en" since 2026-11-02
  18:00:55 (right after the son's English F-5 flight at 17:56, so most likely set in the GUI for him); its dictation
  stayed es-US. The ATC's fixed phrases follow the voice language (atc/desk.py speech_for(language)), so every
  clearance came in English, also after "can you repeat it in Spanish". Not a code regression; a design gap: one
  agent has three languages (dictation, phrases, Supertonic) that can disagree. To decide with the user.
- **F-5E, son speaking English** (19:32-19:41): dictation es-US turned English into noise ("Walmart"). Real bug too:
  he held short facing runway 27 beside the runway past its threshold; the board said "near the threshold ... (holding
  point area)" (instructor/field_position.py: 400 m along, runway half width + 200 m across) while the takeoff check
  (atc/ground.at_departure_end) used a smaller box (300 m / 150 m), so six takeoff requests got "taxi to holding point
  runway two seven" and he finally entered the runway without clearance. **Fixed**: at_departure_end uses the same
  area as the window view (intersection near the threshold accepted); the hold reason tells the model where the
  aircraft is from the threshold ("350 m past the threshold of runway 27 along it, 190 m to its right") so it can
  guide the pilot. test_atc intersection test; 295 pass.
- **A-10C_2** (19:12-19:27): radio found by the device scan ("radio 54" 261.000 AM, gate correct, ATC own calls
  made), but all 23 presses came back empty from Chrome dictation with no error (Oculus headset mic, track unmuted
  since 19:07; the F-5 five minutes later did recognize words). The page hid "no-speech"; it now sends the
  recognition error and the mic track's muted state with every press (heard info "error", "mic_muted").

### Scripted wingman built (2026-11-04; harness- and offline-verified, model checked live with simulated data; not flown)

User's design: the wingman is its own AI aircraft group of the Mission Editor (one aircraft, LATE ACTIVATION), and
"a table of orders that lets the agent take off cold or appear in the air, and the agent knows what state the
aircraft is in". Replaces the F1-menu wingman (same role "wingman", same number calls on the flight frequency;
client/dcs_bridge.py wing_* commands and the speech_mute wingman silencing are now unused).
- Lua (`dcs/instructor_tasks.lua`, task tables from me_action_db.lua): `wing_start(group, cold|hot|runway|air,
  leader)` re-spawns the group from its own env.mission entry with coalition.addGroup (same name replaces the inactive
  group; lateActivation off, start_time 0): cold/hot/runway change the first waypoint's type/action
  (TakeOffParking / TakeOffParkingHot / TakeOff; refused when the editor placed it in the air), air puts it 1 km
  behind and 300 m right of the leader at the leader's height and speed; either way the first waypoint carries a
  Follow task on the leader's group (joins after takeoff). `wing_follow` (Follow pos x/y/z), `wing_escort` (Escort,
  engagementDistMax 60000, Air), `wing_attack` (pushTask AttackGroup, ROE open fire), `wing_bomb` (pushTask Bombing),
  `wing_orbit` (Orbit Circle), `wing_land` (route to a Land waypoint at an airbase), `wing_roe` (air ROE). An event
  handler (world.addEventHandler, replaced on reload) records birth, engine_startup, takeoff, land,
  engine_shutdown, crash, ejection, dead, pilot_dead, hit per started wingman. status(): aircraft groups get "air"
  {active, in_air, type, life, fuel, velocity, heading, ground height, weapons, started, last_event, events (s ago),
  radio line of sight to the leader}.
- Hook: TASKS wing_*; argument kind 'player' = the hook inserts the player's unit (never sent by the program).
- `wingman/desk.py` (agent.json "wingman": {group, number}): phase from events + motion: not started (late
  activation) / on the parking spot starting the engines (cold) / engines running, stopped / on the runway / taxiing /
  takeoff roll / airborne / landing roll / taxiing in / stopped after landing / engines off / lost. Situation:
  your_state, orders_you_can_carry_out_now, you (started how, altitude, speed, fuel, damage, weapons),
  you_from_leader (clock), enemies_detected, radio_with_leader, mission_zones. Orders refused by state with a
  reason the model voices ("you are taxiing to the runway: that order is only for the air; you join the leader by
  yourself once airborne"). Targets: mission zones, other agents' groups (a squad), "here", "leader"; airfield =
  named or nearest friendly. New condition `airborne` (shared/triggers; GUI label; trigger assistant).
- `wingman/tools.py`: start_up, follow(clock 3-9, distance m, height m), cover_me, attack, bomb, orbit,
  return_to_base, weapons, tell_pilot_when. `wingman/mission.md` rewritten (copied over agents/viper/mission.md).
- GUI: "+ Add" and the Frequency tab show "Group name in the mission" for wingmen too (hint: late activation).
- Tests: harness wing mode (start cold, double start refused, air start position, editor-air group refused cold,
  follow/bomb/ROE, takeoff event in status), test_wingman rewritten (states, orders by state, tools, formation,
  events incl. airborne, store with group); test_hook squad radio now present. 302 pass.
- Live model check (gpt-6.1-terra, simulated): "Manolo, start up, we are leaving" -> start_up cold, "Two, starting up"
  5.9 s; taxiing + "get on my three" -> "taxiing to the runway, I will get on your three once airborne" (no
  tool); airborne "on my nine, 300 m" -> follow 9 o'clock; "I am waiting for you up here, show up now" -> start_up air; "bomb
  where Tango is" first failed (the model said "Tango squad"): place names now also match when one known name is
  inside the words.
- **UNCONTROLLED groups** (user, same day: late activation hides the aircraft, they wanted it visible on its spot):
  DCS's own "Start" command (me_action_db.lua ActionId.START, declareCommand('Start', "Start the assigned task")),
  sent as Controller:setCommand({id = 'Start', params = {}}), wakes it up as placed (cold or hot as the editor set its
  first waypoint); not re-spawned ("air" still re-spawns it beside the leader). Status "air.uncontrolled" until
  started -> phase "parked on your spot, engines off, waiting for the order to start (uncontrolled)". On DCS's
  takeoff event every ground-started wingman gets Follow on the leader (an uncontrolled one flies its own route
  otherwise). Harness: group Paco, Start command, no re-spawn, second start refused; 302 pass.
- **First flight (2026-11-04 07:49-08:03, Senaki, Viper uncontrolled F-5E-3)**: "start your engines" -> wing_start ->
  Start: engines started, but it never taxied (still "engines running, stopped" 13 min later, after the pilot's own
  takeoff). The mission (Caucaso.miz, read only) gave Viper ONE waypoint: TakeOffParking with its CAP ComboTask.
  DCS has no "take off" command: the AI takes off to fly to its next waypoint. Fixed: a group whose route is only the
  departure point gets a second one (20 km towards the leader when the leader is more than 5 km away, else 20 km
  north, 2000 m, 200 m/s): uncontrolled -> setTask Mission (departure point + that point) before Start; re-spawned
  ground starts -> the same route. Follow on the leader is now ADDED to the first waypoint's own ComboTask (the
  editor's CAP and options stay); a route with 2+ points in the editor is kept as is (Follow at takeoff from the
  event handler). Harness: Paco with a CAP task -> "Mission points 2 first tasks EngageTargets+Follow"; 302 pass.
  Needs a DCS restart. Not yet known: whether setTask on an uncontrolled group before Start is kept by DCS.
- **Second flight (08:10-08:17)**: same result, "retenido" (held): engines on, never taxied; the mission still had
  one waypoint, so the 2-point route + Start path ran. Suspects: (1) DCS drops a task set while uncontrolled ->
  now the route is given again on its engine_startup event; dcs.log lines "DCS-Instructor: wingman <name> started /
  <event> / route given again"; (2) our ATC's abort_takeoff (sent 08:10:55, 9 s after Viper's Start, for the
  player's departure) - on 2026-11-03 it cancelled an AI wingman's departure. To test: a flight with the ATC agent
  switched off (or not tuning the tower), and one with a second waypoint set in the editor.
- **Third flight (08:25-08:51): it works** (user: "funciona"). dcs.log: "started (uncontrolled), route of 2 points
  given" 13:25:02 UTC, "engine_startup" + "route given again: ok" 13:25:24, "takeoff" 13:30:52 (our ATC's
  abort_takeoff at 13:26:46 did not stop it). So DCS drops a task set while the group is uncontrolled; the route
  given again at engine start is what made it taxi. Then follow (6 and 3 o'clock, 100-200 m) and bomb on zone L1
  obeyed. User's notes: a group whose Mission Editor task is CAP does not do ground attack like CAS (DCS fixes the
  group's main task in the editor; to say in the GUI / agent); the agent should know its pilot, fuel and damage and
  say (or ask) when it keeps attacking ground targets by itself; the mission prompt sounds like "a calculator":
  make it organic (jokes, asks, a known and trusted wingman), give it a simple long-term memory of the basics, and
  it must obey orders. **The user asked to think the wingman's personality and memory through slowly before any
  code** (2026-11-04).
- **ATC: abort_takeoff waits for AI departures** (user's idea, 2026-11-04: an AI departure cuts in front of DCS's AI
  arrivals anyway, a DCS fault to live with): `Desk.ai_departure` = a scripted wingman started on the ground with no
  takeoff event yet (groups "air"), or an AI aircraft on the ground at the field moving >= 3 kt that was not seen
  arriving (`note_ai_landings`: low and within 3 nm, then on the ground = taxiing in). While one exists the release
  waits (console "[atc] DCS abort_takeoff waits: ..."), tried every tick until the player is airborne; a player alone
  sends it as before. The engine-start release no longer needs the player stopped. test_atc +3; 305 pass.
- agents/viper: group "Viper" set by the user in the GUI (2026-11-04). Needs a DCS restart (hook and
  task Lua). To check live: the re-spawn replaces the late-activation group; the waypoint Follow task makes it join
  after takeoff; AI taxi time from cold.

### Wingman companion: design agreed with the user (2026-11-04, talk; then built)

The user's words: the wingman is "a chatbot that flies a plane": any number of them, each user writes its
personality when creating the agent; **how memory and calls work is ours**. Agreed points:
- **Personality vs mechanics**: agents/<id>/mission.md is only the personality (user's, GUI Mission tab, default
  wingman/mission.md is an example persona); the fixed rules (wingman/rules.md: obey orders first, brevity in combat,
  never invent the aircraft's state, how reports / memory / quiet time work) are always appended in code and not
  editable.
- **Editor task and loadout**: it knows its group's Mission Editor task (CAP, CAS, Ground Attack...) and its weapons
  (air-to-air or ground) and says when they do not fit ("they gave me CAP and I carry bombs: what is today's task?").
  DCS fixes the task in the editor: a ground attack with an air-only task is refused with that reason.
- **Combat orders**: "attack the targets of L1": either all of them, pass after pass, or one at a time, waiting for
  the pilot's clearance after each; **it decides and says what it will do, the pilot may correct it**. When done
  (targets destroyed, winchester, bingo, damaged) it rejoins the leader by itself and says so.
- **Reports (callouts)**, from DCS events, phrased by the model in character, short: airborne, weapon released
  (bomb/missile/rockets), kill, hit taken, fuel low, out of weapons, new contacts (ground or air), attack finished,
  landed. Never while the pilot transmits.
- **Quiet time**: after a quiet spell, in a calm moment (airborne, no threats), it gets its memory and may make a
  comment or question about the game/mission (proactive: "where are we heading?", weapons advice from the model's own
  knowledge), or stay silent. Per-agent setting: off / rare / normal.
- **Memory**: a small file per agent (agents/<id>/memory.json): facts about the pilot AS a pilot (name, nickname,
  habits, flights together, house jokes) saved with a remember tool; a one-line summary after each flight. Capped;
  the oldest flight lines go first, never the pilot's name. Visible and editable in the GUI. Personal topics only if
  the pilot starts them, never saved or brought back by the wingman (the user: calling to ask "how did it go at the
  clinic" would be weird).
- **Longer conversation history** for the wingman (it spends the most time with the pilot); the user will measure
  the tokens and decide whether the agent gets a note about cost.

Built the same day (offline- and harness-verified, model checked live with simulated data; not flown):
- Lua: `wing_attack(group, target, all|one)` (one = AttackUnit on its first alive unit), `wing_engage_zone(group,
  lat, lon, radius, ground|air, alt, speed)` (setTask ComboTask EngageTargetsInZone + Orbit); the event handler now
  also takes S_EVENT_SHOT / S_EVENT_KILL / S_EVENT_HIT (hit = the wingman was hit: event.target) into a numbered
  combat log (last 20) sent as air.combat; air.editor_task (the group's env.mission task, cached); weapons get
  "against" (air for AAM missiles, ground for other missiles, bombs, rockets; gun both); enemies get "air". Hook:
  the callsign message carries "pilot" (net.get_player_info name) -> CockpitState.pilot -> situation
  pilot_name_in_dcs.
- `instructor/brain.py`: ChatGptBrain appendix() (text after the mission, not editable) and history_turns.
- `wingman/rules.md` (fixed rules, always appended), `wingman/mission.md` = personality example only (copied to
  agents/viper), `wingman/memory.py` (memory.json pilot <= 40 lines never trimmed by itself, flights <= 12 dated;
  brain_appendix = rules + memory; HISTORY_TURNS 20); tools remember / forget.
- `wingman/desk.py`: task_note / refuse_target by editor task (AIR_TASKS refuse ground attacks with the reason);
  attack(target, how, against) jobs: group or zone; all = engage zone / AttackGroup; one = orbit the zone then
  AttackUnit when its sensors find a target there, after a kill "waiting for clearance", the same order again =
  next; ends on winchester (if it had weapons), fuel low, damage >= 50 %, no target in the zone for 90 s (after 2
  min), group gone -> wing_follow 4 o'clock 200 m + report. Follow / cover / bomb / orbit / RTB end a job.
  Reports from the combat log and the flight (airborne, landed -> one flight line for memory, fuel low, winchester,
  new contacts once per group, hit, kill, weapons released gathered 1.5 s), not made when the pilot asked
  tell_pilot_when for the same thing. Quiet time: chatter off / rare (15 min) / normal (5 min), only airborne with
  the leader > 300 m AGL, no enemies for 2 min, no job, fuel fine.
- `instructor/voice_loop.py`: own_calls hands desk.reports(busy, quiet_s) to Responder.event; REPORT_PROMPT /
  QUIET_PROMPT; the answer "SILENT" is not transmitted; Responder.last_activity (heard or transmitted).
- GUI Mission tab of a wingman: "Wingman: memory and chatter" panel (chatter select, the two memory lists, Save);
  API /api/agents/<id>/memory; agent "chatter" checked by AgentStore.
- Tests: harness (editor task, combat log, AttackUnit, ComboTask zone), test_wingman +17 (editor task, jobs,
  reports, quiet time, memory, Responder report/SILENT). 322 pass.
- Live model check (simulated): "start up, we are leaving" -> start_up, "Two, cold start. See you up there,
  Chamaco" (name from memory); CAP + bombs + "practise attacking ground targets in Z1" -> explains the editor
  task, asks to confirm the day's task, no tool; "attack the targets in Z1" -> chose all and said so; kill report
  -> "pickle two, one T-72 destroyed, request clearance"; "remember I like you to call me Chamaco" -> not
  saved twice. First run sounded dry: rules now say talk like a friend outside combat, brevity only in combat,
  takeoff and landing. Needs a DCS restart (hook + task Lua).
- **First flight with the companion (2026-11-04): "todo bien", but the agent did not see the targets**: DCS's AI
  detection of an aircraft almost never reports ground units. User's idea: our own vision cone. Built
  (`dcs/instructor_tasks.lua` eyes_of, harness-verified, not flown): every second, for a started aircraft group,
  the other coalition's and its own coalition's units (airplanes, helicopters, ground, ships; neutrals ignored) are
  seen within 15 km (airplane) / 10 km (helicopter, ship, any SAM element) / 6 km (ground vehicle), not more than
  150 deg off the nose (the blind sector behind), with terrain line of sight (land.isVisible, target + 2 m), and
  within the mission's visibility or, for a target below the fog top, the fog visibility. Nearest unit per group,
  8 groups at most; enemies merged after DCS's own detection into "enemies" (so contacts, attack one at a time and
  zone jobs see them), friends as air.friends_in_sight -> situation "friends_in_sight" (clock, distance; never a
  contact call). Harness: T-72s 3 km beside it seen, a BTR behind and a BMP at 8 km not, a SAM at 9 km hidden by
  the test mission's 3 km fog. 324 pass.
- **Flights 2026-11-04 12:08, 12:35, 12:53 (Caucaso.miz, read only)**: start, takeoff, formation, fuel, RTB to
  Kobuleti fine; the eyes saw the infantry group M1 ("tally, six soldiers, 12 o'clock, 3 miles"). But "attack the
  targets of L-1" failed twice in CAS ("no more targets found in the zone, 0 destroyed"): **every target at L-1 is a
  static object** (T-72B, BTR-80, ZIL-135, r11_volvo, containers, buildings, comms tower, all within 1 km of its
  centre), which neither coalition.getGroups nor DCS's EngageTargetsInZone covers. Fixed (harness- and
  offline-verified, not flown): the eyes also look at coalition.getStaticObjects (live, ground range, each static its
  own entry "static": true, 14 entries at most); `wing_attack` on a static's name = pushTask Bombing on its exact
  point (expend Two); a zone attack "all" now also bombs the statics seen in the zone one after another, nearest
  the centre first (each again after 2 min if it survives), "one" takes the nearest the centre too; a static is
  forgotten when its kill comes in the combat log (kill entries now carry the victim's name) or when it is within 3
  km in front and no longer seen; new contacts are one call per kind (6 listed, "and N more"). 326 pass.
- **The agent's voice went silent in the second flight** (dictation fine; reconnecting SRS did not help). The logs
  show no voice error at all: no "[voice error]", no "SRS failed" fallback, no clip left in logs/srs_clips. So
  either ExternalAudio reported success while nobody heard it, or the voice thread was blocked without an error.
  Each clip is now logged (logs/dictation.jsonl "voice": synthesis_s, audio_s, srs_s, what ExternalAudio printed
  last, errors, clips waiting). Next time: the time it stopped, and whether the ATC was silent too.
- **No flight calls at all** (user: "it calls nothing at all: not when it taxis, takes off or lands"; only
  the contact he asked for with tell_pilot_when). dcs.log had "Viper takeoff" 17:39:45 and "land" 17:51:49 UTC, but
  watch_flight looked for the phase change itself (ground -> airborne with a takeoff event), and DCS's takeoff event
  comes seconds after inAir() is already true, its land event after the landing roll began: both moments were
  missed. Now each step is said once from DCS's events whenever they arrive (flight_said): engines running (cold or
  uncontrolled start), taxiing to the runway, airborne (takeoff event newer than any land), landed (land event newer
  than the takeoff, after an airborne call); nothing for what happened before the program started. Bombs were never
  called because none was released (5 Mk-82 all flight: the statics). test_wingman +2; 328 pass.
- **Flight 13:20-13:33: "we made a huge leap"**: the eyes saw the statics of L-1 (contacts called in two
  calls: BTR, trucks, buildings, T-72), "attack them" -> zone attack, the statics bombed one after another (5 Mk-82 and
  2 rockets released between 13:26 and 13:32; the SRS voice log shows every clip transmitted, 2-6 s each). But no
  flight call and no weapon/kill call, and it kept orbiting: the user had set Viper to a HOT start with neither late
  activation nor uncontrolled (Caucaso.miz, task CAS), so the mission started it and it was never in T.wings: no
  DCS events, no combat log, no Follow at takeoff, and the job never learnt of a kill (12 rockets left: not
  winchester). Fixed (harness- and offline-verified): an active aircraft group the agents watch that is not
  uncontrolled is adopted (mode "mission", joins the leader on its takeoff if not already flying; dcs.log "wingman
  <name> adopted (started by the mission)"); an attack job ends when no weapon has been released for 3 minutes
  (weapon counts dropping, combat log or not) and it rejoins with that reason. Harness group Halcon; 329 pass.
- **Leave a target alone** (user's idea, same day: "ignore those six soldiers", or keep them on a list and ask
  after the others): tool `leave_alone(target, ignore|later|attack)`; enemies_detected marks it ("leave_alone"), it
  is never called again as a contact, attack on it is refused with the reason, and one being attacked right now is
  called off (zone: orbit again; a group attack: rejoin). So that it holds, a zone attack "all" no longer uses DCS's
  EngageTargetsInZone (which picks its own targets and ignores statics): it orbits the zone and the desk gives it the
  targets seen there one after another, nearest the centre first (a static: Bombing on its point; a group:
  AttackGroup), skipping those left alone; when it ends, the ones left "later" are named and it asks the leader.
  Weapons free with ignored targets: the tool result warns that DCS's AI may attack them too. Live model check
  (simulated): "ignore those six soldiers" -> leave_alone M-1 ignore; "attack the targets in Z1" -> attack the
  zone, "ignoring the soldiers" (it first asked to confirm: rules now say the zone attack skips them by itself);
  "leave the truck for later" -> later. test_wingman +2; 331 pass.
- **Flight 13:56-14:08 ("todo va viento en popa")**: engines, taxi and airborne called for the first time; a new
  contact (Osa 9A33, 19 miles) called; "lo atacamos" -> attack; then the job ended "no weapon released for 3
  minutes" while still flying to it; shot down by the Osa (dcs.log 19:08:12 engine_shutdown, :14 ejection, :16
  crash): no hit call, and it still answered "my aircraft is lost" afterwards (user: "it answers from the beyond").
  The desk skipped everything once lost, so the hit of the same second was never read. Now: `going_down` reads the
  combat log, then one last call marked final (ejecting / crashed, after a hit), none when the pilot is dead or
  the group is gone; afterwards `silent()`: prefilter '' (no answer), Responder.event_call drops every other call
  (log "silent"), no reports or quiet time. The 3 idle minutes of an attack count only within 10 km of the target
  (group or zone centre). test_wingman +3; 334 pass.
- **The aircraft as a remote control** (user's idea, same day, a comment, not flown: "the model should fly
  that aircraft like a remote control, as precisely as possible... like an MCP for the aircraft"; DCS gave
  the SA-8 attack up because the F-5 has no anti-radiation missiles, and it could not fly low). Built (harness- and
  offline-verified, model checked with simulated data): `strike(target, weapon, altitude_ft, attack_heading,
  attack)` replaces bomb: pushTask Bombing on the point of anything (an enemy it sees, a SAM included, a static, a
  zone, a squad), weaponType from DCS's weapon flags as MOOSE's ENUMS.WeaponFlag (auto 3221225470, unguided bombs
  2147485680, rockets 30720, guns 805306368), altitude/altitudeEnabled, direction/directionEnabled (radians),
  attackType Dive; `fly_to(where, altitude_ft, above ground|sea, speed_kt)`: Mission route (here -> there, alt_type
  RADIO above the ground, 100 ft lowest, or BARO) with an Orbit at the end. point() now also knows the enemies it
  sees. Rules: fly it like a pilot who cares for the aircraft (the target's reach, the weapons, terrain, fuel), give a
  plan when asked and fly it step by step. Live check: "the best strategy to attack that SA-8 with what
  we carry?" -> low level using the terrain, one fast pass with the Mk-82, rockets need to get closer, no
  anti-radiation missiles so strike its position, "shall I set up the run-in?"; "go ahead" -> fly_to here at 100 ft above
  the ground. 335 pass. Needs a DCS restart. To see live: whether DCS's AI keeps a low Bombing altitude with Mk-82
  (frag), and the attack heading.
- **Flight 2026-11-04 16:50 (Viper, uncontrolled start): it only obeyed the start-up.** Engines, taxi and takeoff
  were called and flown, but it took off 14 s before the leader, ahead of him, and then flew straight on at 1500 ft
  and 226 kt; "follow me" twice (follow called both times, the model said "following you") changed nothing: 1.6 -> 8.5 nm
  away. The 14:00 flight the same Follow worked with the wingman taking off behind the leader. No error in dcs.log
  (setTask in a pcall, never logged; the tool's answer was not logged either). Fix (offline-verified, 337 pass):
  desk keep_formation: while it keeps a formation (follow ok, the airborne join, the rejoin after an attack; any
  other order clears it) and the leader is airborne, out of position (> 2.5 nm, or > 0.8 nm and 10 to 2 o'clock) it
  is flown with wing_fly to a point 1.5 km behind the leader at his altitude (BARO), renewed every 15 s (the
  leader's speed when ahead, +50 m/s up to 260 when behind), and given Follow again once within 1.5 nm and not in
  front; the situation says "rejoining". Logged now: each tool call with arguments and answer (brain/report records
  "calls"), and in dcs.log "wingman X follows ..." / "joins the leader: ok|problem". To see live: that it turns back
  and joins when it takes off first, and whether Follow then holds.
- **RWR contacts: direction only** (user's question, same day: "how did it see the Osa?"). The Osa (Suelo-3) is ~19 nm
  north-west of Kobuleti; the wingman called it at 21.3 miles with its exact place, from DCS's own detection
  (getDetectedTargets with no filter includes RWR; the eyes reach 10 km for a SAM and the F-5's radar is air-to-air),
  i.e. its RWR picking up the Osa's radar, but a real RWR gives only a direction. Now (harness- and offline-verified,
  338 pass): enemies_seen_by asks DCS twice (all methods, and VISUAL/OPTIC/RADAR/IRST/DLINK; matched by unit name,
  DCS hands new tables) and a group only on the RWR comes as {group, type, air, rwr, bearing_deg}, no position;
  merge_enemies puts those last and only when nothing else sees the group. Desk: kept apart (desk.rwr, 30 s),
  situation "radar_warning_receiver" (note + type and clock), one urgent call per emitter ("your radar warning
  receiver picks up ... direction only"), and attack/strike/fly_to refuse it ("only on your radar warning receiver:
  you know its direction, not where it is"); once eyes or radar see it, it is an ordinary contact. Rules: an RWR
  emitter is called with type and clock, never a range ("Two, mud spike, SA-8, 9 o'clock"). Squads skip such
  entries (no position). Needs a DCS restart.
- **Flights 2026-11-04 17:23 and 17:30** (both: uncontrolled start at Kobuleti). Worked: the RWR call ("Two, mud
  spike, SA-8, twelve o'clock"), "attack it" refused while only on the RWR, its eyes then saw it at 5.3 nm; after
  the rejoin it held formation at 0.1 nm (17:37-17:41); the loss call ("Two, I'm going down") and silence afterwards (user:
  "it came out like a film, dramatic"). Not fixed: airborne before the leader, the takeoff Follow on a leader still
  on the ground made it fly off and come back round. Changes (offline- and harness-verified, 340 pass):
  - Airborne before the leader (user's idea): the takeoff join in instructor_tasks.lua gives no Follow while the
    leader is on the ground ("joins the leader: the leader is still on the ground: the program holds it");
    keep_formation sends wing_orbit over the leader's field, 600 m above it, until the leader is 60 m up, then
    Follow (and the steering if far). The airborne call says it waits; the situation says "waiting for the leader".
    Steering/holding state now resets off the air (it said "rejoining" on the parking spot after a mission restart).
  - A SAM group (a unit with the 'SAM elements' attribute) attacked with wing_attack is bombed on its point (expend
    Two) instead of AttackGroup: DCS released nothing on the Osa for 3 minutes (14:05 flight).
  - Calls: engines -> "your engines are running" only (it said "running, taxiing" before taxiing); taxi stays its own
    call. Ground contacts gathered (5 s without a new one, 15 s at most) into one report: how many, of what, clock
    span (clock_span: "10 to 11 o'clock", round the dial) and miles, "call them as one group; name them one by one
    only if the leader asks"; aircraft contacts still at once, each one.
  - No Spanish in the repository (user, again, "very important"): the Spanish quotes in this file translated to
    English; one Spanish test input (test_trigger_assistant) translated. Left as data: DCS's own names
    ("Aéreo-1-1", "Suelo-3" from the mission), the training missions' "es" texts (DCS's translations), and the
    agents' instructions the user wrote in the GUI (agents/*/agent.json).
- **Flight 2026-11-04 18:17-18:42 (L-1, then Batumi): the best one yet** (user: "tenemos material para un skill").
  Worked: engines and taxi as separate calls; airborne first -> "orbiting the field, I join when you take off",
  joined; the 17 statics of L-1 called as one group ("contacts, your twelve to one, three miles"); strike one by one
  on the pilot's word: T-72B, BTR-80, ZIL-135, Volvos, the comms tower destroyed; "ignore them" -> leave_alone M-1.
  Problems and fixes (offline-verified, 340 pass; needs a DCS restart):
  - The zone attack answered only "orbiting" and the model told the pilot DCS would not attack (the job had in fact
    started on the watchtower): the detail now says the attack started and orbiting is part of it.
  - Return to base after a strike left it orbiting until "follow, then return to base": Bombing was pushed with
    attackQtyLimit false (it never ends) and setTask changes only the main task under it. Now every Bombing (strike,
    statics, SAM point) is one attack (attackQtyLimit true, attackQty 1), and orders (follow, escort, orbit, fly_to,
    land) go through order_task: resetTask, then setTask.
  - Kills of scenery hit by the bombs ("SMALL_CRASH" x5, "BAK_NEW_CRUSH") are no longer combat-log kills
    (Object.Category.SCENERY).
- **Wingman playbook** (user's idea, same day: "a manual of instructions by situation", like a skill). Built
  (offline-verified, 342 pass; model checked with simulated data): wingman/playbook/*.md, one page per situation,
  the same for every wingman agent ("# Title", "When: ...", the instructions): sam-attack, target-area,
  takeoff-and-join, return-and-landing, air-threat, fuel-damage-weapons, written from the flights' lessons.
  wingman/playbook.py: only the index (name, title, when) is in the instructions on every turn (brain_appendix:
  rules, playbook, memory); the tool playbook(page) opens one, and an open page stays in the instructions for
  15 minutes, the last two at most (the history keeps only a short note of a tool's answer). Live check: RWR report
  of an Osa -> it opened sam-attack by itself, "mud spike, SA-8, twelve o'clock, don't climb toward it, recommend
  we go low"; "what do we do with that SA-8?" -> only on the RWR, low at 200 ft using the terrain, one pass with
  bombs, out low; once seen, "go as you said" -> strike Ground-3, bombs, 200 ft, level. Later: pages editable in
  the GUI, more pages as the flights teach us.
- **Flight 2026-11-05 06:34-06:41 (Kobuleti, Viper uncontrolled): it obeyed nothing after takeoff and went to land**
  (user: "the aircraft did not obey the agent"). Start, engines, taxi and takeoff fine (the leader took off first;
  dcs.log "takeoff", "joins the leader: ok"). Then keep_formation sent wing_fly every 16 s and one Follow (06:38:27,
  within 1.5 nm); 16 s later it was out of position again and kept drifting away (11.6 nm at 06:40:27) while every
  wing_fly pointed 1.5 km behind the leader; the agent's orbit "here" (41.946, 41.996) shows it ~6 nm east of
  Kobuleti, on the approach to runway 25: landing at its home field, the end of its Mission Editor route (one
  TakeOffParking point). The only change since the flights where steering and Follow worked (2026-11-04 17:23-17:41)
  is order_task's resetTask before setTask (18:17 fixes, first flown today): every order was lost. Fixed: order_task
  = setTask only (pushed attacks are one attack each and end by themselves). 342 pass. Needs a DCS restart. To see
  live: Follow holds after the rejoin; "return to base" right after a strike (the reason resetTask was added).
- **The aircraft not obeying, kept inside the role-play** (user's design, same day: "a watchdog that tells the
  leader kills the role-play"). DCS gives no way to read its AI's current task or waypoint, so it is seen from the
  outside. Built (offline-verified, 347 pass; Lua checked by the harness; not flown; needs a DCS restart):
  - Lua: no_auto_rtb (AI.Option.Air RTB_ON_BINGO false, RTB_ON_OUT_OF_AMMO 0) at birth, engine start, takeoff
    and adoption: the agent decides when to go home. air.gear_down from the nose gear's draw argument 0.
  - Desk: the last main task sent (follow, escort, fly, orbit, engage zone, land) is kept. Gear down in the air
    with no landing expected (wing_land not the last order; not within 60 s of takeoff; an aircraft never ordered
    anything flies its own route) -> the order is given again at once and every 20 s; the situation says
    aircraft_not_obeying ("do not tell the leader"); after the second try a "fix" report goes to the model alone
    (FIX_PROMPT: fix it with your tools, answer SILENT; a fix is never transmitted, whatever it answers). Rejoin
    steering that gets further away at 4 steers in a row -> the same fix report. Landed anyway -> the landing call
    becomes an emergency landing in character (a believable fault, why it did not call: the leader busy with the
    tower). rules.md: "Your aircraft not obeying".
- **DCS's own voice of the AI wingman muted** (user, same day: "airborne", "on station", "in" on its frequency, over
  our agent). Those are DCS's flight reports (Scripts/Speech/common_events.lua, wMsgFlight range), never covered by
  speech_mute; the wMsgLeader / wMsgWingmen ranges waited for DCSAIServiceBridge.wingmen_ordered, set only by the old
  F1 bridge. dcs/patches/speech_mute.lua now always mutes ATC, to-ATC, Flight, Leader and Wingmen (subtitles kept;
  AWACS, tanker and the rest keep their voices). test_dcs_patches updated; 347 pass. Applied by the user (Update).
- **Flights 2026-11-05 07:44 (F-5E-3) and 08:22 (F-16C_50), both with Viper uncontrolled at Kobuleti: "perfection"**
  (user). First flight with order_task = setTask, no auto RTB, the gear watch and the full mute. 07:44: start, engines,
  taxi, airborne and join called; "on my three, 200 m" held; L-1's 17 contacts as one call; strike one by one on the
  pilot's word (T-72, BTR, ZIL; it kept clear of friendly troops when told); at 25 % fuel it called bingo and ASKED to
  go home (DCS no longer took it) -> RTB Kobuleti, changed to Batumi, landed (ordered: the normal landing call); no
  answer while the leader was behind the mountains (radio line of sight). 08:22: airborne before the leader ->
  holding over the field; MiG-21 contact at 58 nm; "attack it" -> AIM-120 (Fox three), splash, then rejoin and
  formation at 3 o'clock 100 m. aircraft_not_obeying never came up; no DCS wingman voice heard.
- **Helicopter wingman** (user's design, 2026-11-05: the user flies the Mi-8 with a Mi-8 wingman first; it must work
  for every helicopter; the program detects the kind and the model gets only that kind's rules, playbook and tools;
  an aircraft of the other kind cannot follow but can be sent anywhere by radio, "orders are by radio" until the
  commander exists). Built (offline- and harness-verified, 352 pass; model not checked; not flown; needs a DCS
  restart):
  - Lua: KIND numbers (air start 200 m behind / 100 m right / 30 m/s / 50 m up; departure point 5 km, 150 m RADIO,
    50 m/s); air.kind (group category) and air.leader_kind (the player unit's desc.category 1 = helicopter);
    gear_down for airplanes only; can_follow: no Follow added to the departure point nor at takeoff, wing_follow
    and the air start refused, when the kinds differ; wing_land uses helipadId for a FARP.
  - Desk: PROFILES per kind (helicopter: orbit 40 m/s, formation 30-1000 m, rejoin 4 o'clock 100 m, out of
    formation 1 nm / 0.4 nm ahead / back 0.5 nm, steer 300 m behind at +15 up to 80 m/s, hold 100 m above the
    field, fly_to 10 m lowest, no takeoff roll, leader airborne at 10 m AGL; zone attack orbit 100 m above the ground
    at least). Other kind: follow / cover_me / air start refused with the reason, situation you.formation, on takeoff
    it orbits over its field and the airborne call says it waits for orders. A helicopter's uncommanded landing:
    below 15 m AGL and 8 m/s in the air (going_to_land), not the gear.
  - Model: wingman/kinds/<kind>.md after rules.md; playbook pages moved to wingman/playbook/airplane, new
    wingman/playbook/helicopter (takeoff-and-join, target-area, air-defence, air-threat, fuel-damage-weapons,
    return-and-landing); WingmanToolBox.by_kind: helicopter descriptions of start_up, follow, strike, fly_to.
  - Harness: Hip, an uncontrolled Mi-8 with an airplane leader. To see live: DCS's AI with a Mission route at
    150 m RADIO / 50 m/s for a helicopter; Follow between two helicopters; strike (Bombing task) with rockets from a
    helicopter; the takeoff event of a helicopter lifting off.
- **First flights with Cobra (Mi-8 wingman, user's Mi-8): "perfection"** (user, 2026-11-05). Role-play correction
  (user): the agent never says "Mission Editor": the editor task reaches the model as "tasking_from_base" ("not
  cleared for ground attack, base gave me CAP; next mission we ask for CAS"; Transport and Reconnaissance are cleared
  to attack nothing); Lua answers no longer name the editor; rules.md: never talk about the Mission Editor, DCS, the
  AI, scripts or the program on the radio. 353 pass.
- **Changing frequency by radio** (user's idea, 2026-11-05: "Tango, change to 353" to join the helicopters'
  frequency; built the same day, offline-verified, 363 pass, not flown): shared/radio_change.py RadioToolBox wraps
  every agent's toolbox but the ATC's (after TriggerToolBox) with the tool change_frequency(mhz, AM|FM), checked
  against the store's band (30-400 MHz) and modulations; the change waits (pending) until the agent's answer has
  been handed to the voice, so the confirmation goes out on the old frequency; voice_loop.move_frequency then saves
  it with AgentStore.update (as a GUI change: the GUI shows it, the next press reaches it there) and refreshes the
  brain's radio line. Log event "frequency_change".
- **DCS's AI helicopter options** (me_action_db.lua, read 2026-11-05; nothing built): tasks Land ("land on the ground
  temporary": point, duration, combatLandingFlag), Embarking / Disembarking (infantry with EmbarkToTransport;
  needs InternalCargo, the Mi-8 has it), CargoTransportation (sling load, helicopter_hook_pos), Strafing (guns and
  rockets on a point, attackType, length: better than Bombing for a helicopter), AttackMapObject, GroundEscort
  (follow and protect a ground group), FAC tasks (western countries); options ALT_RESTRICTION_MIN/MAX,
  LANDING_OPTIONS, REACTION_ON_THREAT, FLARE_USING, FORMATION, SILENCE (radio silence), PROHIBIT_WP_PASS_REPORT.
- **A helicopter lands at a point** (user's design, same day: the model gives a place by name and the code turns it
  into coordinates; "land here" and DCS finds a spot; relative positions, F10 map marks, airfields). Built
  (offline- and harness-verified, 355 pass; not flown; needs a DCS restart):
  - Lua `wing_land_at(group, lat, lon, seconds, combat|normal)`: DCS's Land task (point, durationFlag/duration,
    combatLandingFlag) through order_task; refused for airplanes. T.status sends the pilot's F10 marks
    (world.getMarkPanels, the first line of the text, the pilot's side or all) as zones with mark = true.
  - Desk: point() also knows airfields (weather list) and the marks (zones); offset(place, metres, toward):
    compass or a clock position from the leader's nose; land(where, offset_m, toward, minutes, how) for
    helicopters; tool `land` shown to helicopters only (WingmanToolBox.only_for). On the ground at the point: the
    landed call says it waits; any flying order is allowed there (DCS lifts it off); the lift-off for an order keeps
    that order; when its minutes are up it rejoins (same kind) or holds there. Situation leader_map_marks apart
    from mission_zones. Playbook helicopter/landing-at-a-point.md.
  - Found on the way: an error inside T.status (the harness player unit had no getCoalition) dropped the whole
    groups message; the player's side is now read inside a pcall.
- **A helicopter attacks the ground only by strafing** (user, same day: the Mi-8's guns fired when its rockets did
  not; also to test whether a group with the editor task "Nothing" attacks this way). Lua point_attack: a
  helicopter gets DCS's Strafing task (point, weaponType, attackQtyLimit/attackQty 1, length 0 = DCS chooses; no
  attackType) where an airplane gets Bombing: strike (wing_bomb), statics and SAM points (wing_attack), and a ground
  or ship group = its first living unit's point (no AttackGroup / AttackUnit for a helicopter on the ground; air
  targets unchanged). Helicopter strike description and kinds/helicopter.md say so. Harness: Hip strafes a point,
  Red Armor and a static. 355 pass. Not flown. Note: the editor task "Transport" is refused by the desk
  (NO_ATTACK_TASKS); "Nothing" is not.
- **Flight 2026-11-05 10:31-10:50 (Mi-8, Cobra Mi-8 + Tango infantry on one frequency): "a marvel"** (user):
  several agents on one frequency answer by name, and know their orders even when the pilot misspeaks. Problems
  and fixes (offline-verified, 357 pass; needs a program restart):
  - Tango waited for pickup beside Cobra landed at "Punto de extraccion" and said it saw no helicopter: the squad saw
    only the pilot's aircraft. squad/desk.py aircraft_in_sight: the other agents' aircraft groups (same groups
    message) within VISUAL_RANGE_NM -> situation other_aircraft_in_sight (name, type, kind, distance, direction,
    landed/airborne, height); place() takes their names ("move to Cobra"). Boarding an AI helicopter is not built:
    Tango's wait_pickup is already DCS's EmbarkToTransport; Cobra would need DCS's Embarking task (next).
  - Cobra flew into the bushes: fly_to at 100 ft above the ground, as the helicopter playbook said (30-100 ft); DCS's
    AI follows the terrain, not the trees. Helicopter low_level_min_m 45 m (150 ft); texts say 150-300 ft.
  - "Winchester" at 10:44:57 was right: the rocket pods were torn off in the bushes (user); the 40 rockets came back
    with the mission restart. Kept anyway: called only after WINCHESTER_CONFIRM_S 5 s without weapons (a single odd
    reading is not enough).
  - Rockets: not fired by the AI Mi-8 (its guns did); the user tries a UH-1 next.
- **An AI helicopter carries the squads** (user, same day: "DCS has an option for troops to board AI transport").
  Built (offline- and harness-verified, 359 pass; not flown; needs a DCS restart):
  - Lua `wing_pickup` / `wing_dropoff(heli, lat, lon, squad)`: DCS's Embarking (x, y, groupsForEmbarking = {squad
    id}, durationFlag false) / Disembarking (x, y, groupsForEmbarking) through order_task; helicopters only. The
    squad side was already DCS's EmbarkToTransport (wait_pickup).
  - state.transport (shared): squad group -> {carrier, stage pickup | aboard | drop, point}. Wingman desk: tools
    pick_up(squad, where = '' the squad's place, offset) and drop_off(where, offset), helicopters only; DCS does not
    say who is aboard, so watch_transport decides: on the ground within 400 m of the point, then airborne -> aboard
    (if the squad was within 400 m of it, or gone from the mission) or "lifted off without Tango" (urgent); at a drop
    -> out. Calls: on the ground at the pickup / drop, lifted off with / without them, dropped. Situation "troops".
    Lost wingman: its transport entries are cleared.
  - Squad desk: carrier(); aboard also when carried; situation "aboard Cobra's helicopter" or squad.pickup ("Cobra
    is coming to pick you up"); never "wiped out" while picked up or carried (DCS may report the group gone);
    disembark / board refuse with the reason (the AI helicopter does it).
  - Names (user's question): state.agent_names (group -> agent name, set by the wingman and squad desks): the pilot
    may use the agent's name or the group's name in the mission for pick_up, places, aircraft in sight, and the
    calls use the agent's name. 360 pass.
  - **First transport flight (2026-11-05 11:17-11:23, Cobra + Victor): "almost works"** (user: the helicopter must
    wait on the ground until the infantry has boarded). dcs.log: Embarking Victor 16:18:11 UTC, Cobra land
    16:22:06, no takeoff event; our desk said "lifted off with Victor aboard" 9 s after landing ("in the air"
    flickered on touchdown) and Cobra then accepted drop_off to Kobuleti without them. Victor had been told
    move_to, not wait_for_pickup, so DCS's Embarking had nobody to board. Fixes (360 pass): pick_up also sends the
    squad wait_pickup (EmbarkToTransport) to the same point (DCS keeps the helicopter down until the waiting squad
    is aboard); watch_transport counts a lift-off only on DCS's takeoff event newer than the land event; the landed
    call and the playbook say to stay on the ground until all are aboard and never to call them aboard before the
    situation shows it.
  - **Second transport flight (11:33-11:38): the pickup worked** (Victor boarded, DCS lifted Cobra off), but neither
    knew: dcs.log has Cobra's land at 16:36:35 UTC and no takeoff event after it, although it flew on. Fixes (360
    pass): a lift-off also counts when in the air at TRANSPORT_LIFTED_AGL_M 15 m above the ground (a touchdown
    bounce never is), in watch_transport and in watch_flight's takeoff after a point landing (then the landed call
    is not made again while high); the squad desk got calls of its own: watch_carrier turns state.transport changes
    into reports() ("your squad boarded Cobra's helicopter", "got out at the drop point", "left without you"),
    which own_calls already hands to its model.
  - **Third transport flight (11:46-11:52): it works end to end** (user: "coronamos"). "Cobra, go to the extraction
    zone and pick up Victor" -> Cobra landed 11:49:02 and waited; Victor boarded; 11:49:35 Cobra "Victor aboard,
    joining you" and Victor "aboard Cobra's helicopter, we lifted off" at the same moment; "take them to Batumi" ->
    on the ground 11:52:28, "Victor got out" 11:52:31, rejoining. Victor's "got out" call is not in the log: most
    likely held by its radio range (Batumi, far from the pilot; Responder.hold), to check next time.
  - Playbook helicopter/troop-transport.md. To see live: whether DCS waits on the ground until the squad boards;
    what the mission reports of a squad inside an AI helicopter; the troops walking out at the drop.

## Ground vehicles (2026-11-05, afternoon; offline- and harness-verified, 377 pass; not flown; needs a DCS restart)

User's plan this session: vehicles, then artillery (fire missions with corrections), then ships (same fire tool plus
naval moves); tanker and AWACS later (AWACS with the commander, on our radar model). User's design for vehicles:
"the same as a squad": move to a point on or off the road; on seeing enemies attack or report and wait for orders;
some DCS vehicles carry infantry: detect the hold, then give them Cobra's pickup.
- **Its own role "vehicles"** (GUI "Ground vehicles"; user, same day: "a new role is easier", first built inside the
  squad role and then moved out): vehicles/desk.py VehicleDesk on the squad's desk (SquadDesk CONFIG_KEY / KIND /
  UNIT_KEY / unit_view), vehicles/tools.py VehicleToolBox (namespace "vehicles"), vehicles/mission.md; agent.json
  "vehicles": {groups, name}; shared/agents.py GROUND_ROLES / GROUP_ROLES, used by the GUI, the triggers and the
  voice loop. The squad role is infantry again. Lost vehicles in the condition events say "vehicles", not soldiers.
  Lua `ground_details` in T.status (ground groups): {types, infantry, vehicles} and, when DCS answers
  (pcall; undocumented in the mission environment, but DCS's radio menu for a ground unit calls them:
  RadioCommandDialogPanel/Config/groundUnit.lua), descent_capacity / descent_on_board.
- **The hold, from DCS's data**: the Mission Editor offers a vehicle Embarking / Disembarking only for a type with
  GT.InternalCargo (me_action_db.lua 'vehicle' tasks, verifyGroupCapability); shared/cargo.py reads that field from
  the unit files under CoreMods and Mods (31 files 2026-11-05: BTR-60/70/80/82A, BMP-3, MaxxPro MRAP, M2A1 half-track,
  Sd.Kfz.251, trucks...; nominalCapacity or unit_point * 100), in a background thread, cached in
  logs/cargo_capacity.json. The live answer wins. Core vehicles such as M-113, BMP-1/2 have no InternalCargo in any
  Lua file: then only the live answer can make them carriers.
- **Lua** `drive(group, lat, lon, speed, on/off road, formation)`: a Mission route "On Road" / "Off Road"
  (me_mission.lua), slow 5 / normal 10 / fast 17 m/s; formation by DCS's radio-menu option (OPTION_FORMATION 5:
  column 1, row 2, wedge 3, vee 4, diamond 5, echelon_right 6, echelon_left 7; keep = unchanged).
  `carry_pickup` / `carry_dropoff(vehicle, lat, lon, squad, road)`: a route whose last point carries DCS's Embarking
  / Disembarking (ComboTask, as the editor puts a waypoint action); refused for infantry. T.attack drives vehicles
  at 10 m/s.
- **Desk** (vehicles/desk.py): situation "vehicles" (left, types, moving, lost since last
  message, troop_compartment yes / none / not known yet, troops it carries); move_to(destination, speed, road,
  formation); pick_up(squad, where, road) also tells the squad wait_pickup there (state.transport entry with
  ride = vehicle); drop_off(where, road). Who is aboard: descent_on_board when DCS counts them, else stopped within
  CARRY_NEAR_M 200 m of the point, then driving off; a drop_off ordered while stopped at the pickup with the squad
  there counts them aboard (the Embarking point is the end of the route: the vehicles may just wait there). Calls:
  stopped at the pickup, drove off with / without them, got out. The carried squad says "Bison's vehicle" instead of
  "helicopter". Cobra's pick_up only finds infantry groups.
- **Tools / prompt**: the squad's tools without boarding, move_to with road and formation, pick_up / drop_off only
  with a troop compartment (VehicleToolBox.current); vehicles/mission.md (driving, report and wait before attacking,
  troops).
- **To see live**: whether getDescentCapacity / getDescentOnBoard answer in the mission environment; whether a BTR
  drives to the Embarking point and waits until the squad is in; what the mission reports of a squad inside a vehicle;
  on-road routes over long distances; the formation option on an AI group.

## First vehicles flight and shared sight (2026-11-05, 17:16-17:21, UH-1H, Bison = one AAV7)

- **Worked**: "Bison, cerca de punto caliente" -> drive on road, column (the model chose both), it drove. The AAV7
  is in no Lua file with InternalCargo, yet its situation said troop_compartment yes: DCS answers
  getDescentCapacity in the mission scripting environment (the cargo scan never ran: the live answer came first).
- **Found**: Bison saw neither Tango (another agent's squad beside it) nor Tango's green smoke; it asked where Tango
  was (honest). DCS cannot report smoke (trigger.action.smoke returns nothing, no event, no query).
- **Built** (offline-verified, 380 pass; needs a program restart only): shared/smoke.py keeps the smoke the agents
  pop (colour, who, where, when; 5 minutes like DCS's), seen within 5 km from the ground and 10 km from the air
  (terrain not checked yet); situation smoke_in_sight for squads, vehicles and wingmen; a place for all of them
  ("the green smoke", "Tango's smoke"). Squad and vehicles desks: friendly_units_in_sight (the other agents' ground
  units within VISUAL_RANGE_NM: name, infantry / vehicles, distance, direction, moving), also a place ("Tango's
  position"). Wingmen already knew every agent's group as a place.
- **Open, user's question (era)**: units sharing coordinates ("Tango, mark your position" as a GPS point) would be a
  cheat in old eras. Discussed next: a position report whose accuracy follows the mission's date.

## Vehicle transport flights and fixes (2026-11-05, 17:41-17:58 and 18:15-18:23, UH-1H, Bison = BTR, Tango aboard)

- **17:41 flight**: Bison picked Tango up (DCS log 22:44 "Bison Embarking Tango"); "drove off with Tango aboard" came
  from the stop-and-go guess, DCS did not count. Drop at Punto Caliente: DCS got "Bison Disembarking Tango" (22:53),
  then Bison stopped and moved on and the desk said "Tango got out there"; the user never saw Tango on the F10 map
  and later confirmed it stayed aboard. Why DCS's vehicle Disembarking did not let them out is NOT known yet.
- **18:15 flight**: DCS counted Tango aboard (getDescentOnBoard > 0: "Tango is aboard your vehicles"). "Desembarca
  Tango" went to Tango, which had no tool for a vehicle and only talked: Bison never got a drop_off (no Disembarking
  in the DCS log). Tango "(nobody answers)" several times with the helicopter landed beside it: the line-of-sight
  check between antennas 2 m and 1 m above the ground was cut by small terrain.
- **Built** (offline-verified, 387 pass; program restart only, no Lua change):
  - Getting out is told as done only when DCS counts the compartment empty (vehicles/desk.py watch_drop, for a squad
    DCS counted aboard). Still counted after DISEMBARK_WAIT_S 60 s stopped there: Bison says so once. Driving off
    with them counted: aboard again ("drop_off again"), and the squad hears it. Without a count: "cannot confirm"
    for Bison and for the squad (state.unconfirmed_drops), never "got out".
  - The squad asks its ride (user's idea: "Tango asks Bison, Bison does it on its side"): state.carriers lists the
    vehicles agents by group; squad tool board_vehicle(vehicle) -> that vehicle's pick_up where the squad is;
    disembark aboard a vehicle -> its drop_off "here". VehicleDesk.asked_by queues Bison's own call. squad mission
    (template and agents victor, buenos) tells the model to use them.
  - Radio: within RADIO_NEAR_KM 2 km both hear whatever the terrain line says (squad/desk.py radio_link).
  - Black box "groups" record every 10 s: each agent group's exists, alive, lat/lon, speed, descent_on_board, and
    state.transport.
- **Next flight, to see**: with the new groups records, does DCS's vehicle Disembarking empty the count? If it never
  does, the user approved the fallback (2026-11-05): respawn the squad beside the vehicle (as CTLD-style scripts
  do). Risk the user named: DCS may still count the compartment full and refuse the next Embarking; check
  descent_on_board after a respawn before relying on it.

## Vehicle drop WORKS (2026-11-05, 19:08-19:14, UH-1H, Bison = BTR-80, Tango = 2 soldiers)

- **Flown**: "Bison, ve a la posición de Tango y recógelo" -> pick_up; boarded; "ve al punto y desembarca Tango" ->
  drop_off; the user's screenshots (DCS Screenshots 191244, 191305) show both soldiers out beside the BTR. DCS's
  vehicle Disembarking works: no respawn fallback needed.
- **Black box "groups" told why the calls were wrong**: DCS's count (getDescentOnBoard) works for the BTR: 0, then 2
  ten seconds AFTER Bison drove off from the pickup (so the entry was never marked counted), back to 0 about 20 s
  after Bison stopped at the drop. While aboard, the mission reports the squad at exactly the vehicle's position;
  once out, at its own. At the drop the BTR shuffled (1.7-2.8 m/s within 20 m) while the troops got out: taken as
  "drove off", so both said "cannot confirm" (and Tango spoke of itself in the third person).
- **Fixed** (offline-verified, 392 pass; the Lua part needs DCS restarted):
  - counted whenever DCS counts them aboard (aboard or drop stage); leaving the drop point means beyond CARRY_NEAR_M,
    not moving at it (test_the_live_drop_of_2026_11_05 replays the recorded sequence).
  - No echo: on a vehicle ride the squad does not call boarding or a failed drop (the vehicles agent does); once
    out it says what it does now and is told not to repeat the drop. REPORT_PROMPT and the squad / vehicles missions
    ask for own words, no status readout, callsign only to open a call, "over" only when an answer is expected.
  - Hits (user: "if it loses a soldier or takes damage it reports it; if Tango is killed it stops answering"):
    Lua ground_details sends life / life0 of the living units; SquadDesk.watch_hits calls a loss at once and wounds
    / damage when the life drops HIT_STEP 10 % with no loss; dead() (gone or nobody alive, not riding anything):
    prefilter '' (no answer), silent() (no event calls), reports() empty. Vehicles agents get the same.

## Second vehicle flight: the squad asks its ride, and offsets (2026-11-05, 19:31, UH-1H)

- **Flown, "works at a million"** (user): "Recoge a Tango y llévalo al punto" reached Tango, which used board_vehicle;
  Bison drove there, DCS counted Tango aboard, drop_off at the point: "Tango got out at the drop point" (counted),
  Tango "mantiene posición" with no echo. Picked up again later. Calls sounded natural.
- **Found**: "Bison, déjalo a 200 metros del helicóptero" -> "Bison cannot set a point 200 m from the helicopter":
  its places had no offset (the wingman's had).
- **Built** (offline-verified, 393 pass; program restart only): instructor/geo.py COMPASS, offset_point and shift()
  (moved from wingman/desk.py, which now uses them); SquadDesk.place_off(destination, offset_m, toward) (compass or
  clock from the pilot's nose, state.flight heading_deg); offset_m / toward (squad/tools.py OFFSET) on the squad's
  move_to and wait_for_pickup and the vehicles' move_to, pick_up (where) and drop_off.

## The internal net: agents message each other (2026-11-07; offline-verified, model checked live; not flown)

Agreed with the user this session: the internal net first (the base for officers, the commander and the pilot
handoff), then the artillery role, named **Archer**, as its first real user (call for fire from Axe or the pilot,
FireAtPoint, "shot / splash"), then a first branch officer. Ships later (another unit role, little new).
- `shared/net.py`: `Net` (one per run) and `NetToolBox` (namespace "net", tool `send_message(to, text)`), wrapped
  around every agent's toolbox but the ATC's (after TriggerToolBox and RadioToolBox). Text only: no voice, no SRS,
  the pilot never hears it, **no radio range for now** (a command net; could follow the units' radios later). On
  the net: active agents, not the ATC. `to` matched like a radio call (id, name, aliases, dictation-tolerant
  same_word); the tool's description lists who is on the net now, with their role.
- **Message or reply, decided by the net**: what an agent sends to one that asked it something (ANSWER_WINDOW_S
  600 s, once) is a reply; anything else is a message. Responder.net_call (instructor/voice_loop.py): a message ->
  NET_PROMPT, the agent may act with its tools and its words go back on the net (reply), or SILENT; a reply ->
  NET_REPLY_PROMPT, its words are a radio call to the pilot (waits for the PTT, range check like an event call),
  or SILENT. A killed agent (desk.silent) answers nothing. Loops: hops (+1 per answer or message sent while
  answering, MAX_HOPS 6) and PER_MINUTE 6 per sender.
- Seen: logs/dictation.jsonl "net", "net_answer", "net_unanswered", "net_refused"; the black box "net" lines
  (sender, to, net kind, text); the GUI's Status shows them dashed, "AXE > ARCHER"; console "NET Axe -> Archer: ...".
- Live model check (scratch, simulated situations, gpt-6.1-terra): "Bison, pregúntale a Tango dónde está y si ve
  enemigos" -> Bison send_message, told the pilot it asked; Tango answered from its situation only; Bison called the
  pilot in Spanish with Tango's position and the three enemies, 11 s in all. The first try showed the reason for
  "decided by the net": Tango answered with send_message (a new message for Bison), so Bison's call to the pilot went
  back to Tango on the net, plus a "Roger" on the net; prompts now say answer with your words, no acknowledgements.
- Tests: tests/test_net.py (14); 408 pass. Needs a program restart only (no Lua change).
- Not yet: a target or point attached to a message (needed for Archer's call for fire: the sender's own sighting,
  never more accurate than what it sees); messages to several agents; who may order whom (the commander's powers).

## Artillery: Archer (2026-11-07; offline-verified, model checked with simulated DCS; not flown; Update + DCS restart)

User, same day: "Archer, the same as the others but with the tool to fire at the point; the problem is who gives it
the point and how: we have zones, but artillery needs coordinates"; first a BM-21; the salvo needs its dispersion
area. The net has no radio range on purpose (user: background simulation of the theatre, the pilot does not care).
- **Who gives the point, as a real call for fire** (agreed): the battery never sees its targets. (1) An observer
  agent (squad, vehicles, wingman) uses `call_for_fire(battery, target, description, offset_m, toward)` (shared/net.py,
  offered only with eyes - desk.target_point - and a battery on the net): our code turns its own sighting (an enemy
  it sees or saw lately, smoke, zone, map mark, "here" + offset, coordinates) into the point, attached to the net
  message; the battery keeps it as "<observer>-N" (ArtilleryDesk.receive_target, from Responder.net_call; NET_TARGET
  tells the model the id). The model never handles coordinates. (2) Zones and the pilot's F10 map marks (already
  places) as pre-planned targets. (3) Coordinates the pilot reads out: shared/coords.py parses MGRS (spaced or not, 1-5
  digit pairs) and lat/lon (decimal, DDM, DMS, hemisphere before or after); WGS84 UTM by the USGS series, round trip
  < 1 m; to_mgrs for the read-back. (4) Corrections: target "last" + offset_m toward add / drop / left / right along
  the observer-target line (the observer's position from the call; for the pilot, the aircraft's) or a compass point.
- **Lua** (dcs/instructor_tasks.lua): `T.fire(group, lat, lon, radius, rounds)` = DCS's FireAtPoint as the Mission
  Editor gives it (Scripts/GeneratedTasks/modules/artillery_battery_60_blue.lua: x, y, zoneRadius, expendQty,
  expendQtyEnabled; also point/radius of the scripting docs), ROE open fire; radius = the salvo's dispersion; rounds 0
  = as the AI decides. `T.cease_fire` (resetTask). ground_details sends "ammo" {weapon: count} of the living units.
  Hook TASKS fire / cease_fire; client ACTIONS. luae syntax ok.
- **artillery/** (role "artillery", GUI "Artillery"; built on VehicleDesk: drives, holds, sees, losses; not a ride
  for squads): fire_mission(target, rounds, spread_m 0-2000 default 100, offset_m, toward) -> detail with the grid
  and distance; refuses with no rounds left, rounds > 200, unknown target (lists observer targets, asks for
  coordinates). Situation: battery rounds_left, fire_mission state, targets_from_observers (id, what, sent_by, grid,
  distance), last_aim_point. **Rounds from the ammo count**: a drop = "Shot", no drop SALVO_QUIET_S 12 s = "Rounds
  complete: N fired, M left. Your crews cannot see where they fell", nothing in FIRE_WAIT_S 60 s = "has not fired"
  (out of range etc.: detected live, no invented range tables). Reports go to the observer on the net as a
  **notice** (new net kind: answered like a reply, to the pilot or SILENT, never back on the net), else to the pilot.
  artillery/mission.md: fire direction centre; read back the grid; never say rounds are on the way without a report.
- agents/archer created (group "Archer" in the Mission Editor, 257.000 AM, voice M3; change in the GUI).
- Live model check (scratch, simulated DCS feeding groups every second, gpt-6.1-terra): "Axe, pídele fuego a Archer
  sobre los tanques que ves" -> Axe call_for_fire on M1 -> Archer fire_mission Axe-1 (point within 1 m of M1),
  replied with the grid; ammo drop -> "Shot" and "Rounds complete" notices -> Axe told the pilot each in Spanish.
  The pilot's MGRS grid, 10 rockets, 200 m -> sent exactly, read back. "Alarga trescientos" with no aircraft in the
  simulation -> Archer said honestly it does not know the observer's position (test added with an aircraft: works).
  Seen: a fake DCS answer text ("20 rounds") was repeated by the model as fact: the real Lua answer describes the
  real order.
- Tests: tests/test_artillery.py (13); 421 pass.
- To see live: does FireAtPoint fire the BM-21 with the spread and expendQty; does getAmmo count drop per rocket; out
  of range behaviour; the impact itself (S_EVENT_SHOT + weapon tracking could give real "splash" and where rounds
  fell, for an observer who sees the area: next).

## First Archer flight and fixes (2026-11-07 ~18:30-18:48, UH-1H, Archer = one Grad-URAL, Tango observer)

- **Flown**: the pilot read lat/lon (DMS) by radio; Archer asked east or west, then fire_mission "N41 50.600 E041
  47.850" (DMS turned to DDM correctly), 7.4 km south, read back the grid. DCS's ammo count works ("GRAD": 40). The
  battery fired, but well over a minute after the order. The user chose Tango as observer: "Axe" and "Archer" sound
  alike to dictation ("Axel", "hacher", "hacker", "alche" were heard).
- **Problems**: at 60 s Archer called "has not fired" and dropped the mission, so the real shot was never called; the
  pilot's "dispara cuando quieras" made a second identical order, which resets DCS's task (laying starts again).
  Tango never saw the statics: Lua statics_seen_by was for vehicles only.
- **Fixed** (423 pass; Lua: Update + DCS restart): FIRE_WAIT_S 600 s and silent while laying (situation "laying the
  guns"); the same order again (aim within SAME_POINT_M 10 m, same rounds and spread) is not sent ("already laying...");
  a new point says the laying restarts. Calls as a fire direction centre (user's wish): "shot" + "waiting for
  corrections or end of mission", "rounds complete" + rounds left + the same; mission.md explains the run of a fire
  mission (corrections on "last", repeat, end of mission, adjust with few rounds, fire for effect) and copied to
  agents/archer. Lua: infantry see enemy statics too, from SOLDIER_EYES_M 1.7 m (vehicles 2.5 m), 4 km, visibility.
- Model check (simulated): "Colt uno uno, disparo. Esperamos correcciones o fin de misión, cambio." / "salva completa,
  cuarenta cohetes disparados, munición agotada..."; "¿ya disparaste?" while laying -> "aún estamos preparando el tiro".
- Not known yet: DCS's laying time for each type; whether expendQty / zoneRadius are obeyed by the Grad.

## Second Archer flight: "la viva perfección" (2026-11-07 19:10-19:18, UH-1H, Tango observer) and the fire queue

- **Flown**: Tango saw the statics now (BTR-80 at 830 m, two tankers, a ZIL-135, a watchtower) and, asked to pass
  them to Archer, sent five call_for_fire at once; Archer fired 3 rockets on Tango-1 (laying about 2 min, then "shot",
  "rounds complete: 3 fired, 37 left", relayed by Tango in Spanish, honest that it cannot see the impacts). The
  pilot's own DDM coordinates: 3 rockets, shot after 90 s, "salva completa, quedan 34"; "fin de misión" -> Archer
  asked the effect on target. One pilot call unanswered: out of Archer's radio range (terrain), as designed.
- **Problem**: the other four targets were never fired (Archer: "guns laying on Tango-1, stand by"; nobody came back).
- **Built** (425 pass; program restart only): a fire queue in ArtilleryDesk. A target an observer sends while the
  battery is busy is queued at once with the current mission's rounds and spread (NET_TARGET_QUEUED tells the model:
  do not call fire_mission, tell the observer it is queued); fire_mission(queue=true) for the pilot's own extra
  targets; when a salvo ends (or a mission never fires) the next one starts by itself and the "rounds complete" report
  says so; no rounds left drops the queue; cease_fire drops it too (QUEUE_MAX 10). Situation "fire_queue".
- Kept by the user's choice: the default spread of 100 m ("magic": the user notices the spread and learns to ask for
  another one).
- Model check (simulated): "Axe, pásale a Archer los blancos que ves..., tres cohetes a cada uno" -> two calls for
  fire; Axe-1 fired, Axe-2 "queued"; after the first salvo Archer laid on Axe-2 by itself; Axe relayed each step.

## Ships, the carrier too (2026-11-07 night; offline-verified, model checked; not flown; Update + DCS restart)

User: "ships are mobile artillery that sail to the coordinates we give, fire their missiles at the point, with speed
control"; can the Supercarrier be controlled; "from that frequency one can ask for a TACAN"; can the agent set a TACAN
or ICLS the mission maker did not; later (a fine job): the Supercarrier's ATC. The user knows little of the navy: the
mission prompt and the answers explain it.
- **From DCS's data** (MissionEditor/modules/me_action_db.lua): a ship's tasks FireAtPoint, AttackGroup, GoToWaypoint,
  ShipHoldPoint, Embarking/Disembarking; options ROE (ground values), alarm state; naval commands (actionsForNavalUnits)
  ActivateBeacon (TACAN), ActivateICLS, ActivateLink4, ActivateACLS and their Deactivate ones. These are commands, so
  they can be given at any time by script: the agent switches them on even when the mission set none. TACAN for a
  ship as fill_TACAN: type 4, system 3, AA false, frequency = calcTACANFrequencyMHz (X: 962 + ch - 1 below 64, 1151 +
  ch - 64 above; Y: 1088 / 1025); ICLS type 131584, channel; Link 4 331-399 MHz (the editor's range); ACLS. Link 4 /
  ACLS / carrier from the attributes the editor checks ("Aircraft Carriers", "Link4", "ACLS"). No turn-into-wind task
  exists in DCS: our code works it out from atmosphere.getWind at the ship.
- **Lua**: surface_group (ground or ship) for fire / cease_fire; T.roe uses AI.Option.Naval for ships; T.sail (route
  of two Turning Points, refused if land.getSurfaceType is not water every SEA_CHECK_M 500 m along the course),
  T.ship_hold (a one-point route at speed 0: to see live that it stops), T.ship_attack (AttackGroup), T.ship_aid
  (kind, on/off, channel, X/Y, callsign, MHz). Status for ships: ground_details (types, ammo, life) + ship {heading,
  carrier, link4, acls, wind_to_deg, wind_ms}; radio kind "ship" (squad/desk RADIOS: 25 m antenna, 200 km).
  Hook TASKS sail / ship_hold / ship_attack / ship_aid (kind is "text": "link4" has a digit). luae ok.
- **ships/** (role "ships", GUI "Ships"; ShipDesk on ArtilleryDesk: fire missions, queue, calls for fire, shot /
  complete): move_to (coordinates, zone, mark; speed_kt 1-32, default 15; detail course and nautical miles),
  set_speed, hold (all stop), attack (detected group), navigation_aid (tacan any ship, icls/link4/acls carrier only),
  turn_into_wind (carrier only: course = wind from + ANGLED_DECK_DEG 9 for a recovery, speed = wind over deck asked -
  wind, 10-180 min, a point that far along the course; ends with a call). Situation: speed_kt, heading, carrier
  wind and wind_over_deck_kt, into_the_wind, navigation_aids_on. ships/mission.md: naval officer; TACAN custom = hull
  number (71X for CVN-71); air boss for the ship's movements only (deck crew, LSO, the carrier's ATC are DCS's);
  never turn with an aircraft on final.
- Model check (simulated carrier, gpt-6.1-terra): "enciende el TACAN y el ICLS" -> TACAN 71X (1158 MHz) + ICLS 1, told
  the channels; "pon proa al viento" -> asked first that nobody is on final; wind over the deck answered from the data.
- Tests: tests/test_ships.py (9); 434 pass.
- To see live: does the one-point route stop a ship; do the aids light (TACAN in the Hornet, ICLS needles); the
  Supercarrier type's attributes; FireAtPoint with ship guns / cruise missiles; ship ammo counts SAMs too, so a SAM
  launch during a fire mission would count as "shot".
- Later (user): the Supercarrier's ATC (Marshal, CASE I/III) with our ATC: "a fine job".
- Then (user: "if the editor assigns a TACAN, does the agent see it?" - it did not): Lua mission_ship_aids reads the
  naval commands in the ship's route waypoint actions (env.mission ... ship.group[].route.points[].task.params.tasks[]
  .params.action, as the editor lists them; once per group) -> ship.mission_aids; ShipDesk.aids_on shows them as
  "(set in the mission)" unless the agent switched that aid off or set it again. 435 pass.
- Names (user): the Ticonderoga agent is **Trident**; the Supercarrier **Mother**.
- Helicopters on a ship's deck (user: "ask the Ticonderoga to land"; DCS raises the small deck net by itself when
  the helicopter is aboard, nothing to script): tool flight_deck(open, speed_kt 3-20 default 10, minutes) on any ship
  = "green deck": steady course with the real wind HELO_WIND_OFF_BOW_DEG 30 on the port bow, slow; while green,
  move_to / set_speed / turn_into_wind are refused by the code (deck_busy); open false = "red deck"; ends by itself
  with a call. Model check (simulated): "solicito aterrizaje en cubierta" -> "Cubierta verde. Rumbo 030, 10 nudos,
  viento del norte a 10, autorizado a aterrizar"; a move ordered meanwhile refused in character; "en cubierta y
  asegurado" -> red deck and then the move. 437 pass. To see live: which DCS ships take helicopters.
- **Trident flown** (2026-11-07 20:10-20:26, UH-1H, a Ticonderoga; user: "la perfección", screenshots Screen_261107_
  202554 / 202622 / 202638 show the approach, the Huey on the deck, the deck nets): sailed to DMS coordinates (course
  036, 15 kt, 7.1 nm); TACAN 71X TRID; one Tomahawk at 115 km by FireAtPoint, "shot" 8 s after the order, "fire
  complete"; green deck: course 265 at 10 kt with the wind from 235 at 9.3 kt on the port bow, cleared to land.
  Fixed: "rounds complete" said "33633 left" (every round aboard) and the model miscounted its Tomahawks: now by
  weapon ("1 BGM-109B fired, 25 BGM-109B left"; mission start_weapons, fired_words). 438 pass.
  The "33633" came from a restarted mission (a new ship counted from zero): the by-weapon words stay anyway (user).
- **Course with no point** (user: "Trident, rumbo 027 a 15 nudos"): tool steer(course_deg, speed_kt, nautical_miles
  default 30, max 200); Lua T.steer walks the course every 500 m and ends 1 km short of land ahead (SHORE_MARGIN_M),
  refused if land is closer; the distance travels in the detail words ("for 41.3 km") because the hook passes only ok
  and detail; the end point is kept so set_speed goes on with it; refused with a green deck. 439 pass. Trident is
  done for now (user).
- Ships see enemy statics too (user: "the same as the others"): Lua statics_seen_by for SHIP groups from
  BRIDGE_EYES_M 25 m out to SHIP_STATIC_SEEN_M 8 km (visibility respected). A static is engaged with fire_mission
  (FireAtPoint at its point), not attack (AttackGroup takes groups). The net and call_for_fire they already had.
- **Ship radio and radar** (user: "what range would a ship's radio have in real life; the radar contacts, enemies or
  friends, should reach the agent"): UHF/VHF go by line of sight, so the mast height is the range: Lua radio_path kind
  "ship" with ANTENNA_M 25 m (it was measured from a vehicle's 3 m), RADIOS ship max_km 420 (the horizon governs:
  about 92 km to a helicopter at 300 m, 246 km at 3000 m, 410 km at 9000 m, 41 km ship to ship). Radar: new
  DCSInstructorRadar.own_contacts(unit, player) beside scan() (the airfield radars untouched, "como oro"): the same
  model (DCS's sensor distances, RCS, terrain) from SHIP_RADAR_M 30 m for every airborne aircraft, plus ships within
  the radar horizon between masts (20 m) and the range; each contact with side friend / enemy / neutral (IFF), kind
  air / surface. ship.radar -> ShipDesk.radar_view: bearing and range (nm) from the ship, altitude ft, heading, speed
  kt, "the pilot you talk to"; mission.md: report like a CIC ("bogey, bearing 270, 40 miles, angels 20"). Copied to
  agents/trident. 441 pass. To see live: what getSensors gives a Ticonderoga / the Supercarrier.
- **Mother flown** (2026-11-08 06:46-06:50, FA-18C from Ras Al Khaimah, session 20261108-063323_FA-18C_hornet; user:
  everything fine except the radar): radio check; distance to the pilot from the radio link (64.8 km -> "35 nm", no
  bearing invented); steer 048 / 15 kt; "tell Trident to follow you" -> net message, Trident steered 048 / 15 kt
  and replied; the mission's TACAN 73X TKR / ICLS 1 given for the approach. Nothing of the Supercarrier's own ATC
  (left for later, user). **Radar**: getSensors gave Trident 174 km but the CVN_73 ("carrier search radar") only
  20 km (its unit file: airFindDist 50 km), so the pilot at 65 km was never a contact. User: put the carrier's radar
  at its real range and explain why in the code: instructor_radar.lua REAL_SHIP_RADAR_M, CVN_71/72/73/75 and Stennis
  400 km (AN/SPS-48E, ~220 nm), only in own_contacts, only when larger than DCS's data; the airfield radars are
  untouched. Also: a ship in friendly_units_in_sight was "vehicles", now "ship" (test). 442 pass. Read by the hook at
  mission start (no Update needed). To see live: Mother reports the Hornet on her radar.

## Controller "Magic": design agreed (2026-11-08, talk with the user; nothing built yet)

- **A function, not an aircraft** (user): role "controller" (callsign e.g. Magic), real-world AWACS / CRC / GCI. Its
  eyes are a list of sensors set in the GUI: an AWACS or E-2, ground EWRs, airfield radars, ships. Its air picture
  is the union of what each sees, with the existing radar model (instructor_radar.lua, own_contacts style). A lost
  sensor shrinks the picture; low flying hides the pilot. It transmits from the sensor/site with the best line of
  sight to the pilot.
- **Not the router of the whole chain** (decided): officers talk on the internal net; Magic is the gate between air
  tasking and the pilots only. Until an air officer exists, agents ask Magic for air support straight on the net.
- **Task board (its "ATO") kept by code**: id, kind (CAS, CAP, recon, strike, transport), where, requested by,
  agent + frequency to hand the pilot to, priority, status (open -> assigned -> en route -> on station -> done).
  Sources: the mission maker in the GUI first; agents by the net (new tool request_air_support for squads/vehicles).
- **With the pilot**: the pilot checks in airborne with his loadout (Magic's trigger; asks for it if missing) and
  Magic picks a task that fits it; Magic's own calls on events (new bandit near him, new task, threat on his way);
  on request picture, bogey dope, vector home; tasking + handoff ("contact Tango on 255.0"); RTB when short.
  Only what its sensors see and what is reported. Guard (243.0) only for serious threats.
- **User's rules**: the pilot cannot command Magic or the AWACS (the AWACS route is the editor's; asked to move,
  Magic declines in character). Frequencies on a need-to-know basis: per-agent setting "public" or "task only"
  (default: given only when that agent is part of the pilot's task or asked for him). Answers from the user's
  questions: one Magic per side to start; the pilot may refuse or ask for another task (Magic insists if priority);
  human pilots only at first, AI flights later; Guard yes for serious threats.
- **SAMs**: the AWACS radar does not see ground units: Magic learns of a SAM (a) passively when its radar emits
  (ESM: DCS tells whether a unit's radar is on) within range, (b) from ground agents' reports. A silent SAM is
  unknown to everyone.
- **First test** (user): a mission E-3 as Magic's sensor (orbit from the editor); enemy fighters far away with ROE
  weapon hold + no reaction to threats (so they do not attack the AWACS); Axe or Tango sees enemies and asks Magic
  for air support on the net; Magic tasks the pilot at check-in and hands him to Axe; Magic calls the enemy
  fighters; a far SAM battery: does Magic learn of it.
- **Later** (user): tasking from the ground before takeoff, by the mission and the aircraft chosen (a briefing).

**First pass built** (2026-11-08, user: "metele la primera pasada y despues que probemos vamos ajustando"; not flown):
- **Lua**: instructor_tasks.lua T.status takes "sensor:<group>" lines in the watched list: sensor_status gives only
  where, alive, type, in_air, radar (own_contacts, up to SENSOR_CONTACTS 40, the pilot always kept) and emitters;
  never the wingman adoption. instructor_radar.lua: air contacts carry track_group (formations), the surface horizon
  uses the antenna's real height above ground (an AWACS), new R.emitters(unit) = ESM: enemy ground units / ships whose
  radar DCS says is on (Unit:getRadar), within ESM_RANGE_M 450 km and line of sight. No hook change (the hook passes
  the watched names through). Checked with a scratch luae harness: E-3 sees the pilot and a MiG; only the SAM with
  its radar on is heard.
- **controller/** (role "controller", GUI "Air controller"; agents/magic created, 270.000 AM, voice M5, sensor group
  "Magic"): desk.py (sensors from CockpitState.sensors, kept apart from groups so no unit "sees" an AWACS; picture as
  numbered tracks, a formation one track, no names or types, BRAA from the pilot when he is on the radar else from
  the sensor; emitters remembered 15 min; radio by the radio horizon from the best sensor, RADIO_MAX_KM 450; task
  board T1..; frequencies need to know via "frequency_sharing"; own calls to a checked-in pilot: hostile track within
  40 nm, an enemy radar within 60 nm of him or a task, an open task when he has none, a sensor lost), tools.py
  (check_in, assign_task, update_task, give_frequency; nothing moves the sensors), mission.md. Mission maker tasks:
  agent.json "controller.tasks" [{kind, what, contact, priority}] (no GUI field yet).
- **Net**: request_air_support (shared/net.py) for any agent with eyes while a controller is on the net: the unit's
  sighting attached as target {kind air_support, urgent}; the controller's receive_target puts it on the board with
  the unit as contact; Responder.net_call uses the desk's target_words. Axe's enemies_seen row now also asks Magic.
- **Model check** (simulated, gpt-6.1-terra): check-in with GBU-12 / Maverick -> check_in, called the bandits (2,
  000, 40 nm, angels 20, hot) and the Kub radar; Axe's request -> own call offering the CAS task; "acepto" ->
  assign_task with bearing, range, MGRS and "contacte con Axe en 256.000 AM"; picture; "move the AWACS" -> "negative,
  the AWACS stays on station". tests/test_controller.py (15); 457 pass.
- Not yet: Guard 243.0 for serious threats (needs a per-call channel in the voice), the GUI fields for more sensors,
  frequency_sharing and the mission tasks, NATO names for emitters (DCS type names now), fuel / RTB calls.
- To fly: an E-3 group named "Magic" (its route an orbit), Axe near enemies, enemy fighters far (ROE weapon hold,
  reaction to threat none), an enemy SAM with its radar on; restart the mission (the Lua is read at mission start).
- **First flight** (2026-11-08 08:27-08:35, F-5E-3, Colt 1-1; user: "empingaisimo... esta perfecto por ahora"):
  check-in (asked for weapons and fuel, check_in), Axe saw a container -> request_air_support -> T1 on the board ->
  Magic offered it by itself -> "acepto, dile que se mantenga alejado" -> send_message to Axe (Axe hold) and
  assign_task with bearing, range, MGRS and "contacta Axe en 256.000 AM". The E-3 saw 256 km: the pilot, 2 friends,
  2 hostile tracks at 63 and 100 nm, and heard an S-300 search radar at ~100 nm, but Magic never called them (calls
  only within 40 / 60 nm) and the check-in said only "no tasks". At 08:35, leaving the mission, a false "sensor lost".
  DCS's own AWACS was still talking. Fixed: check_in's answer asks for the picture (what it tells is not called again
  as new); every new hostile group and every new radar heard is called once at any range, the threat call again
  within THREAT_NM 40; a sensor is lost only after LOST_CONFIRM_S 8 s with the pilot still in the mission's traffic;
  radars by their NATO name from DCS's own table (shared/reporting_names.py: Scripts/Aircrafts/_Common/Cockpit/
  HelperAI_reporting_names.lua, "S-300PS 40B6MD sr" -> "SA-10 Clam Shell radar"); speech_mute also mutes DCS's AWACS
  (wMsgAWACS and wMsgLeaderToAWACS ranges; status "outdated" until the GUI's Update). 460 pass. Model check: the
  check-in now gives "two bandits, 000, 40 miles, angels 20, hot; Kub radar active, 010, 25 miles".
- **Second flight** (2026-11-08, after the AWACS mute; user: "muy bien, lo que le falta es... cuando finaliza una
  tarea que te mande a un sector a esperar o te asigne una ruta"). Built (offline-verified, not flown): stations and
  routes. A station is a mission point the mission maker made for it: agent.json "controller.stations" [zone names],
  else the trigger zones whose name says so (STATION_WORDS: CAP, station, hold, orbit, marshal, push, CP, IP; never
  the pilot's marks unless named). New tool assign_station(station, altitude_ft): only with no live task; empty picks
  the nearest station not within THREAT_RING_NM 25 of a radar heard; none in the mission -> a holding point 20 nm
  from the pilot back towards its nearest sensor (or his present position when already close); the answer gives
  bearing / range from him, altitude (or asks for one fitting the aircraft), and the route: a leg passing within
  25 nm of a known radar is rerouted via the mission point that clears every ring with the shortest path, else "keep
  well clear", and a station inside a ring is warned. assign_task routes the same way to the task. check_in with no
  open task and update_task closing the pilot's task (done/cancelled/refused) both say to offer another or send him
  to a station (RTB when low on fuel or Winchester). Situation: stations (nearest 6) and pilot.holding_at (reached
  within 5 nm). mission.md: never leave the pilot without instructions. 465 pass.

## First public version: release copy and one-click setup (2026-11-10; offline-verified, setup run in a scratch copy)

The user's request: a clean copy ready for GitHub, a license that lets anyone do anything, documented and clearly
marked alpha, and one file to click that downloads what is needed. An audit by another agent (Astra) follows before
anything is uploaded; nothing is published by us.

- **License**: The Unlicense (public domain), the user's "total freedom".
- **`tools/make_release.py`** (development folder only, never copied): builds `release/DCS-Instructor/` from an allow
  list of folders and files, skips personal and generated parts, then scans every text file for credentials (ChatGPT
  client ids, API keys, JWTs, refresh tokens, mail addresses, paths into a Windows user's home folder) and refuses the copy if any is
  found (checked against the real settings.json: caught). It empties the copy but keeps its `.git`, so each new
  version is one commit in GitHub Desktop.
- **Not shipped**: settings.json (account, devices, paths), secrets/, agents/ (the user's agents and wingman
  memory), logs/, backups/, runtime/, .venv/, models/, CLAUDE.md, AGENTS.md, the SRS cfg files; and the Eagle
  Dynamics content built from the DCS install: `modules/*/library/` (manual text) and `modules/*/training/`
  (training missions with ED's instructor texts). Shipped: cockpit interface data (controls, positions, autostart,
  export args, radios) and terrain/cloud/RCS numbers, as community tools like SRS do.
- **`SETUP.cmd`** -> `tools/bootstrap.ps1` (Python 3.12.14 from python-build-standalone 20260901, SHA-256 checked;
  .venv; pinned requirements) -> `tools/setup.py`: settings.json from `settings.example.json` (language from the
  Windows display language: this PC shows `es-MX` while the region is `en-US`, so the region alone was wrong),
  DCS install from the registry (or asked), voice model, training lessons and library built from the user's DCS,
  then asked: export + hook (also re-installed when the project folder was moved), the DCS install patches
  (default no, integrity-check warning), PTT, ChatGPT sign-in; last, SRS and Chrome found or not. Idempotent.
- **Verified**: bootstrap from nothing in a scratch copy (33 s: download, checksum, venv, packages, imports); the
  real setup.py in that copy against E:/DCS World with "no" to every DCS change: 10 F-5E lessons and the library
  (2 books) built, SRS and Chrome found. 468 tests pass in the development folder and in the release copy (4 skipped
  there: they need DCS's luae or data built from DCS).
- Smaller fixes: `LIBRARY_BUILD.cmd` / `build_library.py` take the DCS folder from settings (no fixed `E:/DCS World`);
  `DCS-Instructor.bat` says "run SETUP.cmd first" when not set up; the launcher shows its start-up errors (no PTT,
  port in use) in a message box instead of only in the log; test_lesson skips without the built lessons.
- README (English, public), CHANGELOG (0.1.0-alpha), .gitignore, bug report template.
- **Astra's audit (same day), all four findings confirmed and fixed**:
  1. Partial installs taken as done (voice model ready when `voice_styles/` existed; all lessons ready when one JSON
     existed). Now: `setup_tts.ready()` = Supertonic's own required files (its `*_REL_PATH` list) present and non-empty,
     no `*.incomplete` download, and `ready.json` written only after every voice spoke; `build_training.missing()` =
     every training .miz has a lesson that parses and has rules (lessons written to .tmp then moved);
     `build_library.stale()` also catches an unreadable index, a manual missing from it, a section file lost (index
     written last via .tmp).
  2. "Setup finished" printed after failures. Now every step is checked again after it ran; `Report` ends with "Setup
     complete" or "Setup INCOMPLETE" + what failed (exit 1, SETUP.cmd says so), and "Before flying" for what the user
     skipped or must install.
  3. README claimed no DCS content shipped: wrong (cockpit tables, autostart, terrain, clouds, RCS ship). Now a "Data
     from DCS" table says what ships, from where, that the Unlicense does not cover it and that ED has not confirmed
     redistribution; the manuals text and training missions stay built on the user's PC.
  4. README said the instructor "watches your cockpit switches" and runs the lessons: wrong for the main program (the
     tower instructor never sees the cockpit, by the user's realism rule; the lesson engine runs only in the console
     `INSTRUCTOR.cmd`, never flown live). README, CHANGELOG and the status table now say exactly that.
  Found on the way: a pilot without the F-5E would have had "INCOMPLETE" forever and a library error box at every
  start: `preflight.libraries` and setup now skip aircraft not installed in DCS. Astra's encoding error reproduced
  (test_lesson printed ED's rule names, which hold a Cyrillic 'С', to a cp1252 console): printed with `!a`;
  bootstrap sets `PYTHONUTF8=1`. Its 25 test errors in the release copy were its sandbox blocking local sockets.
  Verified: 473 pass (also with a cp1252 console); full SETUP.cmd in an empty scratch copy (51 s, Spanish picked,
  "Setup complete" + 3 before-flying items); then a cut lesson and a lost library file were rebuilt by re-running;
  then a lesson that could not be built gave "Setup INCOMPLETE", exit 1.
- **Astra's second pass**: one gap left, confirmed in Supertonic's loader: `auto_download` fetches the model only
  when an ONNX file is absent (`has_all_onnx_modules` checks existence), so an empty ONNX or a missing tts.json was
  detected by `ready()` but never repaired. Now `setup_tts.damaged()` (files missing or empty, cut download, an
  expected voice missing: M1/M2 defaults plus every voice in settings.json and agents/) makes `setup_tts.py` call
  Supertonic's `download_model` (temp folder, replaced only when complete), then checks again. Verified in a scratch
  copy with a real model copy: vocoder.onnx emptied, tts.json and M2 deleted -> whole model downloaded from Hugging
  Face (26 files, 42 s), all 10 voices spoke, `ready()` None. 474 pass.
- **No Eagle Dynamics data ships any more** (Astra's last open point; the user: fix it before uploading). On GitHub
  "upload and remove if asked" does not remove: the files stay in the history. Now SETUP.cmd builds all of it:
  per aircraft owned `build_module` (controls, autostart, export_args) -> `build_positions` (from controls +
  positions_overrides.json, which holds every live answer, so nothing is lost) -> lessons -> library; then
  `build_terrains` for the missing maps, `build_clouds`, `build_rcs`. Validators next to each builder
  (`build_module.missing`, `build_terrains.missing`, `build_clouds.built`, `build_rcs.built`: absent or cut files).
  `make_release.py` skips those files and terrains/ entirely (200 files, 1.9 MB). The hook reads aircraft_rcs.lua
  with `read_optional` (the radar already used DCS's own RCS first). The launcher's preflight says "run SETUP.cmd"
  (`dcs_data_missing`, en/es) when data is missing (a map or aircraft installed since); the export/hook messages now
  say SETUP.cmd too. Tests: `tests/dcs_data.py` `needs()` skips the tests that check against real data (Senaki's
  chart, F-5E cockpit, RCS) until SETUP built it, and takes DCS's folder (luae) from settings.json instead of a fixed
  E:/DCS World. Verified: full SETUP.cmd from an empty copy (44 s): 12 of 12 maps, F-5E cockpit, lessons, library,
  clouds, RCS; all 28 built files byte-identical to the development ones; 474 pass there with nothing skipped (85
  skipped in the bare release copy, which has no data and no settings).
- Open: a second brain provider (API key / local model) for pilots without a ChatGPT plan; a check of a full
  English setup (setup picks English on a non-Spanish Windows; not checked in this pass); a short demo video.

## Future: commanders for both sides and dynamic missions (ideas agreed in talk, 2026-11-02 night; nothing built)

- **Blue commander** (role play): talks to the pilot, gives the mission, reports, reacts (like Buenos: heard ->
  answers and acts). **Red commander** (opponent): no radio with the pilot, no frequency; its own decision cycle
  (every 1-2 min plus events: a flag, a lost unit, a radar contact); objectives and options written in plain words
  in the GUI ("defend the bridge; if half the armour is lost, fall back to zone 3; launch the two reserve helicopters
  if an enemy helicopter is detected near Z1"). Same command tools (move, attack, ROE, activate reserves, flags).
- **Realism rule for red**: it knows only what red detects (its radars, its units, Controller:getDetectedTargets,
  which works for enemies). A pilot flying low and unseen is unknown to it.
- **Must add first**: a fixed **side per agent**; the hook accepts orders only for groups of that agent's side (today
  any group by name is accepted).
- **Per-agent brain connection** (Connections screen, already planned): e.g. GPT commands one side and Claude the
  other (user's idea).
- **Multiplayer on our own server**: red can have human pilots too; the red commander gives them tasks on red's SRS
  frequencies. The program must then run on the server host (clients cannot reach the mission scripting
  environment).
- **Dynamic missions** (the user's long-standing wish: "a beautiful but empty world"): the mission maker places
  units and zones (the "toy box"); the commander reads its side's order of battle, the zones and its intel, and
  invents tasks. Code keeps it honest: tasks only for units that exist and can do them, progress and success from
  the world (unit dead, zone entered, flag), then the next task. Steps: (1) the commander picks and phrases tasks from
  a pool the maker describes; (2) it invents them freely; (3) later, spawning (coalition.addGroup) and a campaign
  state kept between missions. Cost: one model call per decision; cycles of 1-2 min are fine, seconds are not.
- **More from the user (talk, 2026-11-05 night; nothing built, "hay que esperar")**: the commander is "the jewel":
  agents report to each other and to it, it plans and organizes the theatre. The human picks a role and the
  commander grants that role certain powers, no more: with a commander present, a pilot without the permission
  cannot order a tank. The human may also choose to be the commander ("it is a game: limit nobody"). A briefing
  belongs to the commander (GUI needs one): it decides what to hand out to each agent. It can make DCS dynamic:
  assigns missions, moves the theatre.
- **Finishing pass the user sees before / with the commander** (same talk): one front-to-back review of everything;
  instructor: a few more manuals, not all, and the README explains how Opus can add more and how a user adds their
  own; ATC: DCS's airfield charts in a simplified form, and organizing human traffic (AI traffic only via the
  mission, not worth it); squads: fine as they are; wingman: connect its memory (needs some tuning); ground
  vehicles: little more to do; GUI: a pass, plus the briefing. Later, a separate future project: the same idea for
  Arma 3 / Arma Reforger (the user owns both).

## ATC agent built (2026-11-02, midday; offline-verified, model checked live with simulated data; not yet flown)

User's direction this session: start the ATC now, test everything together later; DCS's comms menu will be driven
later by a native command bridge (VAICOM style: code inside RadioCommandDialogsPanel.lua, sendRawMessage), not by
keystrokes; no Spanish in code; no dependency on the DCS-ATC folder (the program must move to the Pi and to GitHub).
Ported by copying and rewriting DCS-ATC's logic (comments say "Adapted from DCS-ATC"; nothing imported, no path).
- **Data from DCS** (hook, needs a DCS restart): `dcs/instructor_traffic.lua` (new) sends every aircraft of the
  mission once per second ({type: traffic, aircraft: [unit, type, callsign, side, helicopter, player, lat, lon, alt_m,
  vx/vy/vz, in_air]}); `dcs/instructor_weather.lua` now also gives each airfield's coalition and ATC radios
  (communicator frequencies) and `mission()` (visibility, fog, clouds, dust, time of day), sent in the weather message
  as "mission". CockpitState keeps traffic/traffic_at/mission_weather. Radar untouched. Harness tests in test_hook.
- **atc/** (role code; the agent folder `agents/atc/` holds agent.json + mission.md): `geometry.py` (lat/lon ->
  metres around the field; runways, thresholds, approach frame), `phrases.py` (Speech from locales "atc": spoken
  digits, runway, wind, QNH/QFE, heights; the pilot's callsign spoken digit by digit, "Colt uno uno"), `ground.py`
  (taxi/takeoff clearances, runway occupant, arrivals on final, protect 5 nm), `approach.py` (landing clearance,
  number two, go-around inside 2 nm, radar vectors: 30-degree intercept >= 4 nm, 3-degree heights on QFE),
  `tower.py` (Ledger, Watch: runway_free, number_two, unannounced_taxi, runway_incursion, departed; called_station by
  sound), `conditions.py` (IMC: visibility < 5 km or ceiling < 1500 ft; cloud presets in `terrains/clouds.json` built
  by `tools/build_clouds.py` from DCS's clouds.lua, so no DCS install at run time), `desk.py` (stations, tuned
  station, radio range, runway in use with hysteresis via instructor/weather.py, board, decisions, own calls),
  `tools.py` (namespace "atc": request_taxi, request_takeoff, request_landing, request_vectors, get_traffic,
  get_airfield), `mission.md`.
- **Frequency**: agent "frequency_mode": "airfield" (`shared/agents.py`): the ATC answers on every airfield's real
  frequency (the mission's communicator frequencies, else the map chart's), own coalition + neutral; fixed-frequency
  agents are matched first; only one airfield agent may be active. Station callsign from the chart ("Kolkhi"),
  override in agent.json "atc.station_names" (DCS-ATC used {"Senaki-Kolkhi": "Senaki"}). Pressure "inhg" or "hpa".
- **Brain flow**: each message carries the ATC's board (runway in use, wind, QNH/QFE with their spoken forms,
  visibility/ceiling, where the pilot is, what was given). Clearances: the model calls the tool and transmits its
  "transmission" word for word (code decides; mixed per the user). Before the brain, `Desk.prefilter`: no airfield on
  the frequency or out of radio range -> nobody answers; pilot names another airfield -> fixed "wrong frequency".
  The voice loop calls `Desk.tick` every 2 s (not while the pilot transmits or the agent answers): the tower's own
  calls, kept in the brain's history (`Brain.remember`). The ATC transmits on the tuned frequency, named after the
  station. GUI: Services lists the ATC ("Airfield frequencies"; frequency fields disabled for it), instructor first.
- **Live model check** (scratch script, simulated F-5E at Senaki, gpt-6.1-terra): radio check 6.5 s; "request taxi"
  -> request_taxi -> "ruede al punto de espera de la pista dos siete, QNH dos nueve decimal nueve tres" 4.3 s; ready
  for takeoff with a C-130 at 3 nm on final -> "mantenga posicion, trafico C-130 en final, a tres millas" 6.2 s;
  "Kutaisi, ..." on Kolkhi's frequency -> fixed wrong-frequency reply. Found: on a tool error the model invented
  "rodaje no autorizado"; the mission now says unable/stand by. 173 offline tests pass (tests/test_atc.py: 31).
- **Checked**: `E:/DCS World/Scripts/Speech/speech.lua` has NO DCS-ATC mute block (the user believed DCS's ATC was
  silenced; maybe muted another way). Nothing written outside this folder.
- **Next**: fly it (restart DCS: dcs.log "mission traffic read"; tune 261.0 at Senaki); then the native command
  bridge (VAICOM-style, needs the user's approval: it edits a DCS install file) so DCS's own ATC releases AI
  arrivals and turns runway lights on; Access tab (what each agent may see: window, primary/secondary radar, all
  traffic...); per-airfield clearance styles (big civil vs small military); the ATC's own STT language per agent.
  `terrains/clouds.json` is derived from ED's file (like the maps' navigation.json): check before publishing on GitHub.

## First ATC flight (2026-11-02, 12:25-12:52, Senaki -> Batumi, session 20261102-122506_F-5E-3)

User: "esta funcionando brutal"; F4 voice good. Worked: start-up, taxi (after the second request), unannounced-taxi
call, takeoff held for an An-30M landing then cleared, departure release, Batumi approach data on 260.0, landing
clearance by the tower's own call on final, instructor on 251.0 in between (station by frequency both ways). DCS's own
ATC was still talking (speech.lua not patched).
**Bug found and fixed (needs a DCS restart): grid north.** DCS's runway course, wind and velocities are relative to
the map's grid north (about 7 degrees from true at Senaki: heading read 274 on the takeoff roll while lat/lon moved on
281.7). Taken as true with lat/lon positions, the far end of the runway moved ~120 m sideways: a parked aircraft
(330 m from the 27 threshold, north of the runway) was "on runway 27", so the tower called a runway incursion at
start, held the first taxi request and called another incursion at the holding point. Fixed: the hook sends both
runway ends in lat/lon (instructor_weather.lua start_/end_lat/lon), instructor/weather.py true_heading/convergence/
to_true; the desk turns DCS velocities, the export heading and the wind to true and uses true variation (grid
variation + convergence) for magnetic values. Also fixes the instructor's "where on the field". Tests: test_atc
GridNorth (checked to fail with the old geometry). Not converted yet: the instructor's radar track (tower_view.track,
~7 degrees). Note: Windows lists an open black box file as 0 bytes; it is being written.

## DCS install patches and the native command bridge (2026-11-02, afternoon; applied to E:/DCS World, not yet flown)

User's approval (2026-11-02): patch the DCS install; "Update" in our program re-applies everything after a DCS update;
when DCS changed a file so a patch no longer fits, it is reported and fixed by hand. The program goes to the Pi; the
hook, export, patches and bridge stay on the PC (client/).
- `client/dcs_patches.py` (CLI `utilities\DCS_PATCHES.cmd status|update|remove`; GUI screen **DCS** with Update and
  Remove, plus the export/hook status): one marked block per patch (`-- DCS_AI_Service BEGIN <name> <hash>` ...
  `END`), latin-1 round trip (apply + remove = the exact original bytes, checked on copies of the real files), a
  backup per distinct original labelled with the DCS version (`backups/dcs/`, ED's files: never share), syntax check
  with DCS's bin/luae.exe before writing, "outdated" when our block's source changed, "unsupported" (nothing written)
  when the anchor or the panel locals it needs are gone. Applied 2026-11-02 to DCS 2.9.31.29434: both patched.
- `speech_mute` (Scripts/Speech/speech.lua, after `local result = protocol:make(message)`):
  `dcs/patches/speech_mute.lua`, DCS-ATC's mute (ATC and to-ATC messages lose their sound; subtitles and DCS's ATC
  logic stay).
- `radio_bridge` (appended to Scripts/UI/RadioCommandDialogPanel/RadioCommandDialogsPanel.lua, VAICOM style):
  `dcs/patches/radio_bridge.lua`. Polled from dxgui's update callback (registered once in _G.DCSAIServiceBridge; the
  tick closure is replaced on every reload of the panel), UDP 127.0.0.1:47823, lines "id\ttime\tcommand\tairfield";
  whitelist abort_takeoff, request_taxi, request_takeoff, inbound, abort_inbound, request_landing; requests older than
  10 s refused, one per 10 s per command+airfield; the airfield's communicator from coalition.getServiceProviders
  (neutral + own side), then selectAndTuneCommunicator + `data.pComm:sendRawMessage(Message.X, {communicator})`, as
  the menu does. Answers on the cockpit port {type: native, id, status, detail}; dcs.log "DCS_AI_Service: bridge
  loaded / bridge listening / sent ...". If "loaded" appears without "listening", the update callback does not fire in
  that Lua state (try Gui.SetupApplicationUpdateCallback as VAICOM does). Harness: tests/lua/bridge_harness.lua.
- `client/dcs_bridge.py` NativeBridge.send; the ATC desk sends `abort_takeoff` once per departure (on our taxi
  clearance for departure, or when the engines run stopped at the field), so DCS releases the AI arrivals it holds;
  `inbound` on a landing request at night (19-06 h mission time) for the runway lights. Logged: native_request /
  native_result in logs/dictation.jsonl. Tests: test_dcs_patches (10), test_atc NativeCommands (5); 192 pass.
- To check live: dcs.log lines above; DCS's ATC silent; after our taxi clearance, AI arrivals land instead of
  holding; at night the runway lights come on after a landing request.
- **First live run (2026-11-02, 14:00)**: dcs.log "DCS_AI_Service (Main): bridge loaded" but never "listening": the
  dxgui update callback does not run in a mission, so our abort_takeoff (sent at engine start, 14:00:44) was never
  read. Fixed: the GUI hook (same "Main" Lua state) drives the bridge's tick every 0.1 s, finding it as the global
  DCSAIServiceBridge or in package.loaded.RadioCommandDialogsPanel (hook harness test); dcs.log then says "driving
  the native command bridge" and "bridge listening". Patch refreshed (Update). Needs a DCS restart.
- **Second live run (14:09)**: still only "bridge loaded"; the hook never found the bridge (no "driving" line): the
  radio panel lives in another Lua state or environment than the hooks (as DCS-ATC had found). Read VAICOM's
  Append.Core.RadioCommandDialogsPanel.lua (github.com/Penecruz/VAICOM-Community, MIT): it starts its loop at the end
  of the panel's initialize() with Gui.SetupApplicationUpdateCallback(), Gui.EnableHighSpeedUpdate(true),
  Gui.AddUpdateCallback(loop) (its port 33334, LuaSocket path added to package.path/cpath). Ours now wraps
  initialize() the same way (dcs.log "bridge started" per mission; old callback removed first). Note: VAICOM's own
  bridge runs any Lua it receives ("exec"); ours never does. The hook driver stays as a second path.
  PowerShell's Set-Content -Encoding utf8 writes a BOM that DCS's Lua rejects: strip it (done for all files).
- **Verified live 2026-11-02, 14:20-14:31** (user: "it works bro"): dcs.log "bridge loaded" (19:19:21 UTC), "bridge
  started" (19:20:49, the panel's initialize), "bridge listening on 127.0.0.1:47823", "sent abort_takeoff to
  Senaki-Kolkhi" (our engine-start release) and "sent inbound to Kobuleti" (landing request at night); both answered
  {type: native, status: sent} in logs/dictation.jsonl. The VAICOM-style start inside initialize() is what works;
  keep it.

## Squad agents: the action layer, first role (2026-11-02, evening; offline-verified, model checked; not yet flown)

User's design: the ATC stays fixed; mission agents act through a layer our code prepares ("the model decides, the
code acts"). Agreed mix: **direct tasks** for generic orders (no editor work) + **flags** for the mission's story
(next, with the Triggers tab). First role: **squad** (`squad/`, agent `agents/buenos`, 255.000 AM, voice M3).
- DCS facts (read in DCS's files 2026-11-02): "Embarking" is a task for an AI transport; for a PLAYER helicopter the
  infantry gets **EmbarkToTransport {x, y, zoneRadius}** (MissionEditor/modules/me_action_db.lua) and the radio menu
  embarks it: `coalition.getAllDescents(heli:getObjectID(), true)` -> `descent:embarking(heliId)`; disembark:
  `coalition.getDescentsOnBoard(heliId)` -> `heli:disembarking(descent:getID())` (RadioCommandDialogsPanel.lua
  makeItemEmbarkDescent / makeItemDisembarkDescent). So the squad needs both hands.
- Mission hand: `dcs/instructor_tasks.lua` (loaded by the hook with the radar): move (off road, slow/normal/fast),
  wait_pickup (EmbarkToTransport), hold (StopRoute), roe (free/return/hold), smoke, status (watched groups: alive/size,
  position, speed; the mission's trigger zones). The hook listens on 127.0.0.1:47824 (`client/dcs_tasks.py`), lines
  "id\ttime\taction\targs..."; fixed action list with argument kinds (strings %q-quoted, numbers re-formatted, words
  [a-z_]); never runs received code; answers {type: task}; {type: groups} every second.
- Radio-menu hand: the bridge gained embark_troops / disembark_troops / troops_on_board (target = group name; when not
  found it reports the names DCS offers). Patch refreshed with Update.
- `squad/desk.py` (knowledge + orders; replies awaited up to 3 s by request id in CockpitState.replies),
  `squad/tools.py` (namespace "squad": move_to, wait_for_pickup, board_helicopter, disembark, hold,
  set_rules_of_engagement, pop_smoke; destinations "here", "helicopter" only in sight, or a mission zone by name),
  `squad/mission.md`. Realism: the squad sees the helicopter only within 3 nm. agent.json "squad": {groups, name}.
- Live model check (simulated DCS answers, gpt-6.1-terra): extraction at LZ North -> wait_for_pickup(LZ North) 5.8 s;
  green smoke -> pop_smoke 6.7 s; "landed, board" -> wait_for_pickup + board_helicopter 9.1 s. Found by it: a tool
  argument named differently in the definition and the code (test now calls every tool as the model does). 207 tests.
- To fly: a mission with an infantry group named "Buenos" (infantry only), a player helicopter that carries troops
  (UH-1H, Mi-8, CH-47...), optionally trigger zones (e.g. "LZ North"); radio 255.0 AM. Open questions for the live
  test: does DCS need a moment after wait_pickup before embarking; what does a group report while aboard.

**First squad flight attempt (2026-11-02, 15:57-16:08, Mi-8MT in VR)**: the pilot heard nothing. Every press said
"no radio data: listening anyway": the Mi-8 had no modules/<aircraft>/radios.lua (only the F-5E had one) and SRS's
table is still unreachable, so the gate sent everything to the default agent (instructor), which answered on 251.0
AM while the pilot was tuned elsewhere. Also the Oculus headset microphone reported "muted" several times (empty
dictation). Fixed: `modules/Mi-8MT/radios.lua` (R-863 device 38 with AM/FM by arg 369, JADRO-1A 37, R-828 39; SPU-9
selector arg 550, Radio/ICS 553, PTT 182/225, as SRS's Mi8.lua) and the export reads those optional keys (export
harness test). Rule: every aircraft flown needs its radios.lua, or the frequency gate cannot work.

**First squad flight (2026-11-02, 16:20-16:36, Mi-8MT, Z1 near Batumi)** - user: "esto es magia... esto es otro
DCS". Worked: the gate picked Buenos on 255.000 and the ATC on 260.001/261.001; move_to Z1; "can you see me" (in
sight at 1670 m west); wait_for_pickup + board_helicopter -> DCS "embarking Buenos" (16:29); disembark far away ->
"disembarking Buenos" (16:35); ATC Batumi landing data + night Inbound sent; Senaki out of radio range from Batumi on
the ground -> nobody answered (correct). Found and fixed:
- ATC not heard in the Mi-8: the R-863 reads 260.001 MHz and the tower transmitted on that; SRS rounds the pilot's
  radio to 260.000. The ATC now transmits on the station's own frequency (Desk.channel; test).
- Squad lost track when aboard: DCS keeps an embarked group "existing" where it embarked, alive and still, so the
  model thought it had not boarded, re-sent wait_pickup and embark ("offered: " empty). Aboard now comes from DCS's
  radio menu (troops_on_board every 3 s, quiet in the logs); while aboard, move/hold refuse and board says "already
  aboard". Tests: test_squad.

## Agents by frequency, Services GUI, app name (2026-11-02, morning; offline-verified, not yet flown)

User's design: the app is called **DCS_AI_Service** (GUI title, SRS transmitter name "DCS_AI_Service Instructor";
the project folder keeps its name). Services in the GUI lists agents (Instructor; ATC and Commander "coming soon").
Each agent is configured in its own tabs: Frequency, Mission (system prompt), Triggers (coming soon), TTS, STT. The
frequency is what switches an agent on: tuning the aircraft radio to an agent's frequency makes that agent hear and
answer, with everything from its own folder. Closing the GUI window closes the whole program.
- `shared/agents.py` AgentStore: `agents/<id>/agent.json` (name, role, active, frequency_mhz, modulation, connection,
  voice {name, speed, steps, silence_s, volume, language, output}, speech {language}) and `agents/<id>/mission.md`.
  First run creates `agents/instructor` from the old settings (frequency 251.0 AM from "srs", the user's tuned voice
  F4 / 1.15 / 10 steps from "voice", "speech.language"); the original mission stays in
  `instructor/brain_instructions.md` ("Restore original"). Mission fields filled per message: {tower} {radio}
  {aircraft} {callsign} {tool_guide}; only those, so other braces typed by the user stay (`fill_mission`). Checks:
  30-400 MHz, AM/FM, two active agents cannot share a channel, agent ids are folder-safe.
- Frequency gate (`client/dictation.FrequencyGate`) now returns the agent on the selected radio's channel (5 kHz
  tolerance; intercom never). No radio data, or "ptt.instructor_frequency_only" off: the default agent (instructor).
- Chrome dictation: each press gets a number and the agent's dictation language (event "ptt" {press, lang}); the page
  sends the number back with the text, so the answer comes from the agent the pilot spoke to. RecordedDictation keeps
  the agent with its clip. `on_text(text, agent)`.
- `instructor/voice_loop.Router`: one Responder per agent, each with its own brain (its mission, its channel as
  {radio}, its own history); `refresh()` after a GUI change gives the running brains the new mission and channel.
  `Voice.say(text, agent)`: the agent's voice settings, and by SRS the agent's own frequency
  (`SrsOutput.play(..., channel)`).
- Closing: the GUI page holds `/api/events` open; `WindowWatch` stops the program once the GUI (or dictation) window
  has been closed for 1 s; on stop a "close" event makes the other window close itself (navigation uses
  history.replaceState so Chrome allows window.close()). Verified: the standalone GUI exited by itself (code 0) each
  time its page went away. `utilities\GUI.cmd` stops the same way.
- GUI: menu Status, Services, Connections, SRS, Controls and audio, Aircraft and library, Flights, DCS, Settings
  (Voice input/output moved into each agent). Page JS errors now show in the error bar. `?nowatch` skips the event
  stream (headless screenshots only: their virtual time never advances while it is open).
- Not yet: STT provider per agent (Chrome/Google Cloud is still program-wide, "speech.provider"); brain connection per
  agent ("connection": "default" = settings "brain" until the Connections screen); ATC/Commander roles and their
  tools; creating new agents from the GUI. 138 offline tests pass.

## Full flight with the GUI (2026-11-02, 07:06-07:49, Senaki, session 20261102-070602_F-5E-3)

Cold start taught step by step from the manual, taxi, takeoff, approach, go-around, landing. The user: "this gives
life to the game ... it makes you want to learn". 23 answers, 8.3 s average (5.6-12.7), about 90 words each (~36 s of
radio); tools per answer: search_library 25, get_tower_view 18, read_section 13, get_weather 12. Radar reports matched
the telemetry (the pilot's altitude really swung by +-2000 fpm).
- **Fixed: speed brake reversed.** The tower kept seeing the speed brakes out while the pilot had them in. The
  Speed Brake Switch (arg 101) labels came from the toggle rule (1 = OUT); DCS's landing lesson shows -1 = OUT
  ("ABRK ID 10" asks to extend them while the switch is not at -1; the next rule goes on at -1). Now an override:
  1 = IN, 0 = OFF, -1 = OUT. Nose strut (250) verified from the lesson (1 = EXTEND). Still unverified outside items:
  drag chute, flaps lever ends, beacon, landing light, canopy. Note: the tower sees switch positions, not the surfaces
  (a failed hydraulic system would still show the switch).
- Open: answers are long for a radio (~36 s); the manual is searched on almost every answer, also in flight.

## Status and next session (2026-11-01, end of session)

2026-11-08 morning: Mother flown (fine, but DCS's carrier radar sees only 20 km): real carrier radar range set (see
"Mother flown"); 442 pass. Then the controller "Magic" designed with the user and its first pass built (see
"Controller Magic"; 457 pass, not flown). Next: fly Magic with the test mission, adjust; then the land sub-officer.
2026-11-07 end of session (441 pass): built and flown the internal net, Archer (artillery) and Trident (ships); Mother
(Supercarrier, agents/mother, 259.000 AM, ME group "Mother") created, to be flown by the user in VR (Update + DCS
restart first). Agreed next (user's view of real C2): the commander and "generals" never talk to pilots (no radio);
a **dispatcher** (AWACS-like controller) links the air officer and the pilot, calling him with tasking and handing
him over ("contact Tango on 255"); a JTAC-like link joins a squad and the pilot; these agents call the pilot first.
Order proposed: dispatcher -> a land sub-officer over 2-3 squads asking it for air support -> JTAC -> branch officers
-> commander.
2026-11-07: the internal net built (see "The internal net" above; 408 pass, not flown). The user removed an agent
"Paco" (backups/agents/paco-20261107-172051). Then Archer, the artillery role (see "Artillery: Archer"; 421 pass,
not flown; Update button + DCS restart). Next: fly Archer (a BM-21 group named "Archer"), then a branch officer.

2026-11-06 (short session, not flown): Axe never called contacts because it had no "enemies_seen" event row (calls
of its own come only from event rows or "tell me when"); added enemies_seen + casualties to agents/axe. In that
session's log DCS never reported enemies for Axe at all (no "enemies" in its status): check it was within sight of
one. Vehicles speed control: move_to speed_kmh and new tool set_speed (re-sends the last drive with a new speed;
Lua drive takes km/h) - needs the Update button to re-apply dcs/instructor_tasks.lua. Radio range already differs:
vehicle radio 3 m antenna / 60 km vs manpack 2 m / 30 km (squad/desk.py RADIOS, kind from Lua radio_path).
Then the user said Axe's targets were statics (DCS's ground AI detection never reports them; Viper sees them with
its own eyes): Lua statics_seen_by adds enemy statics to a vehicle group's enemies (4 km, terrain line of sight from
2.5 m, mission visibility; infantry unchanged), and attack on a static uses FireAtPoint at its point. Flown: Axe
called "a 20 ft container, south, 4 km" by itself. Then Axe "disconnected": not so, 256 held Axe, Bison, Buenos and
Viper; "Axel ..." matched nobody (names under 4 letters had to match exactly) and went to the last speaker, Viper
(its "airborne" report came right after Axe's question). Fixed in shared/dispatch.same_word: a 3-letter name
takes one letter more. Open: an agent's own question (expects an answer) could keep the follow-up over another
agent's report on the channel. Attack on the static not tried yet.

Working live: ChatGPT plan brain (gpt-6.1-terra) on SRS 251.0 AM with Chrome dictation and the X-56 PTT; the
tower-only picture (instructor/tower_view.py); the mission callsign through the GUI hook ("Colt 1-1"); the airfield
radar (unit named after its airfield, our own range + line-of-sight model). 85 offline tests pass.
Pending live checks: POSITIONS_CHECK.cmd (switch labels, fuel shutoff suspect); a black box session file after a
flight (logs/sessions/).
Next, in the agreed order: (3) DCS training procedures split by phase, fetched by the model as a tool (only the
current phase), plus a checker that compares the black box state with the lesson's cockpit checks. Decided by the
user (2026-11-01): the checker's result is used **only in the debriefing** for the F-5E. What the instructor may know
during the flight depends on each module's equipment and is set per module: an aircraft with GPS/datalink (F-16,
Apache...) could be followed by a command centre through it; the F-5E has nothing of that, so its instructor knows
only what it sees through the window, what the radar shows and what the pilot tells it; (4) library per module (manual PDFs with pypdf + the user's own documents, index + search tool);
(5) debriefing from the black box; then the GUI (system prompt editor, connection/model picker, devices).
Ideas for later: radar realism (emitting state, jamming, RCS, position error), linked radar stations, radio orders
and mission triggers, full airfield/runway data from the hook (Terrain config).

## Status 2026-11-01, evening

Black box built (see "Brain information flow" above), 65 offline tests pass. Next: user runs POSITIONS_CHECK.cmd in
the F-5E to verify the switch labels (at least the cold-start ones); a flight with DCS-Instructor.cmd should leave a
session file in logs/sessions/. Step 2 (tools + telemetry/geography) done, see above. Next: procedures by phase
(DCS training split per phase, fetched by the model) + checker; library (manual + user's folder, pypdf);
debriefing from the black box. Open: airfield hook for complete map data (needs approval).

## Status 2026-11-01, afternoon

Brain connected to the user's ChatGPT plan (step 5 above), 58 offline tests pass. **Sign-in verified live
2026-11-01**: `CHATGPT_LOGIN.cmd` registered the agent, all six scopes granted including chatgpt.tokens.use.direct;
the account's catalog lists gpt-6.1-sol, gpt-6-astra, gpt-6.1-terra, gpt-6-sol, gpt-6-luna, gpt-5.6-sol/terra/luna;
settings "brain" saved with gpt-6.1-terra; the Spanish radio check was answered correctly in 2.4 s. Next, live:
`BRAIN_TEST.cmd` or `DCS-Instructor.cmd` in the F-5E on 251.0 and ask by voice; check latency in
`logs/dictation.jsonl` ("brain" events with seconds). Then: richer context (switch positions by name from
controls.json, lesson steps as guidance, `reference.md` from the manual), the GUI picker for connection and model.

## Status earlier on 2026-11-01

Done and verified live today: voice on the SRS radio (F-5E UHF 251.0 AM), Chrome dictation with the X-56 PTT
(also with Chrome hidden behind DCS), the instructor frequency gate (only the selected radio on the instructor
frequency is heard; radio off is never heard), long phrases no longer crash the process. 35 offline tests pass.

One launcher without console windows (user request): `DCS-Instructor.cmd` -> `pythonw tools/launcher.py` opens the
SRS server and client (client window brought to the front), the Chrome dictation window and the voice loop
(`instructor/voice_loop.py`, shared with DICTATION_TEST: the instructor repeats what it understood, standing in for
the brain); closing the dictation window stops it; log `logs/launcher.log`. Verified live: start (SRS, page,
radio data with the UHF knobs); a second launch while one runs reopens the running one's dictation window instead
of failing (first user try failed: they relaunched while the old one was still stopping, and the cockpit port clash
killed the new one after SRS had opened - ports are now opened before SRS/voice); closing the window stops it in
3.8 s (page stream keepalive 2 s, was 15 s).
The SRS client still needs its Connect button after the server starts. Option for later: the windowless
`ServerCommandLine-Windows/SRS-Server-Commandline.exe`.

The user's direction for the instruction itself (2026-11-01): not 100% scripted. The DCS exports (cockpit, lessons,
radio) give the model data and confirmations, and the brain teaches organically. So the lesson engine and
`INSTRUCTOR.cmd` will change; they are not to be tested live as they are.

Not yet tested live: `INSTRUCTOR.cmd` with the dictation (lesson commands "listo"/"repite" spoken on 251); the
cold-start lesson in DCS. Next pieces, in the user's order: brain providers (local endpoint, API key, OAuth Sign in
with ChatGPT; check terms first), then the GUIs (device lists for mic/output, provider choice), Whisper later (needs
approval to download a model). Known: SRS's own radio table is not reachable from our export in DCS (the debug
library is present but SR was not found); the cockpit-device fallback is what works. ExternalAudio only connects to
an SRS server on 127.0.0.1. Chrome shows an "unsupported command-line flag" bar (from the mic auto-grant), harmless.

## Status at end of 2026-10-31

Done and verified: setup (own runtime/venv/Supertonic, 10 voices), module data (controls, autostart), live
cockpit export (installed in Saved Games/DCS/Scripts/Export.lua after SRS's line; verified live with the F-5E),
10 training lessons converted, lesson engine + pilot input + NullBrain + local voice (offline tests and a simulated
end-to-end run pass).

Not yet tested live: `INSTRUCTOR.cmd` in DCS. Planned test: close `COCKPIT_TEST.cmd` (same port 47820), open
`INSTRUCTOR.cmd`, fly a QuickStart F-5E cold start (not the training mission, which has its own instructor), type
"listo"/"repite"/questions. Expect the lesson to pause at "hold F98" (zone condition). Voice plays on the PC default
audio device; check whether it reaches the user's VR headset (Oculus Link).

Known rough edges: key hints like "[RCTRL +RSHIFT +B]" are dropped from speech, leaving "pressing the keys ." (in Spanish);
two lesson texts have no Spanish version; zone conditions unsupported outside the training mission.

Next pieces, in the order the user agreed: voice through SRS ExternalAudio (**built 2026-11-01**, default output in
`settings.json`; still to hear live: run the SRS server on this PC, SRS client connected, F-5E UHF on 251.0 AM, then
`tools/srs_test.py` or `INSTRUCTOR.cmd`), dictation/speech recognition (Google Speech API; check pricing first),
brain providers in the free-question slot (local endpoint, API key, OAuth Sign in with ChatGPT; check terms first).

Changes outside this folder (2026-11-01): Saved Games/DCS/Scripts/Hooks/DCSInstructorHook.lua (one line, remove
with `tools/install_hook.py uninstall`).
Changes outside this folder: Saved Games/DCS/Scripts/Export.lua got one marked line (backup
`Export.lua.backup-20261031-200751`; remove with `tools/install_export.py uninstall`). The DCS-ATC capture hook
`Saved Games/DCS/Scripts/Hooks/LabATCCapture.lua` is still installed from the paused DCS-ATC project.

## Open questions

- Google Speech-to-Text pricing and free tier: check before step 2.
- OpenAI Sign in with ChatGPT: terms checked 2026-11-01 (local/open-source plan usage is self-serve). Before
  distributing a packaged or paid version, or running the instructor as a remote squadron server, re-check: those
  need OpenAI's interest form.
- Whether the server-side flight data (without the client export) is enough for an in-flight-only instructor.
