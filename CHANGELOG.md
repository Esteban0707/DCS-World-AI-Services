# Changelog

## 0.2.0-alpha (2026-11-23)

- GUI: Connections screen (default model for every agent, chosen from the models your ChatGPT account offers) and a
  Brain tab per agent (its own model, e.g. a stronger one for a commander, a faster one for a wingman). Changes
  apply to running agents at once.
- Wingmen: a wingman whose mission task allows no attack (none, Transport, Reconnaissance, AWACS, Refueling) or no
  ground attack (CAP, Escort...) now says so on the radio for a strike too, instead of flying an attack that never
  releases its weapons.
- Security: the GUI now answers only when addressed as 127.0.0.1 or localhost (a foreign web page pointed at the
  GUI's port could read the settings and the agents before).
- The usage-limit message names both limits: your ChatGPT plan's, and this app's own (ChatGPT Settings > Usage).

## 0.1.0-alpha (2026-11-10)

First public version.

- Agents on the radio through SRS, one per frequency (shared frequencies: the name you call picks the agent):
  instructor, ATC, wingmen, infantry squads, vehicles, artillery, ships, air controller.
- Instructor in the control tower: sees only what a tower would (radar, window), never the cockpit; teaches from
  the F-5E-3's DCS manuals.
- Training lesson runner (console, `utilities\INSTRUCTOR.cmd`): the official F-5E-3 lessons checked against the
  cockpit switches; tested offline, not flown yet.
- ATC on every airfield's real frequency, with a tower/radar view limited by line of sight, range and aircraft size.
- Scripted wingmen (airplane and helicopter) started by radio; helicopter landing at a point, strafing, troop
  transport; vehicle pick-up and drop.
- GUI: services (agents), missions, triggers and the triggers assistant, live log.
- Black box: every flight and cockpit control recorded in `logs/sessions/`.
- `SETUP.cmd`: one-click setup (own Python, packages, voice model); builds all DCS data on your PC from your own DCS
  install (cockpit tables, lessons, manuals, maps, clouds, radar cross sections), none of it ships; every step checked
  again, ends with "Setup complete" or "Setup INCOMPLETE" and what is missing.
