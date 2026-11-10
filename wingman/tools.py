"""The wingman agent's toolbox (wingman/desk.py): start up, formation, cover, attack, leave alone, strike a point (weapon,
altitude, heading, dive), fly to a place (altitude above ground or sea, speed), orbit, return to base,
weapons rules, "tell me when", its playbook (wingman/playbook.py) and its memory of the pilot (remember, forget:
wingman/memory.py). Same interface as the other toolboxes; the model chooses, DCS's own AI flies it, and
the desk refuses an order that does not fit the aircraft's state (situation "your_state").
"""
import json

from instructor.tools import NO_ARGUMENTS, parameters
from wingman.playbook import Playbook

NAMESPACE = 'wingman'
NAMESPACE_DESCRIPTION = 'Your own actions as the pilot\'s wingman: an AI aircraft of the mission that you fly.'
RESULT = ' Returns ok and what DCS answered.'


def enum(values) -> dict:
    return {'type': 'string', 'enum': list(values)}


class WingmanToolBox:
    definitions = [
        {'type': 'function', 'name': 'start_up',
         'description': 'Start your aircraft when the leader tells you to ("start up, we are leaving", "meet me up '
                        'there"): cold = on your parking spot with engines off (you start them, taxi and take off: '
                        'it takes some minutes), hot = on your parking spot with engines running, runway = on the '
                        'runway ready to roll, air = appear in the air beside the leader. If the leader does not say '
                        'how, cold on the ground, air when the leader is already flying and wants you there now. '
                        'If you are parked uncontrolled (your_state says so), any ground start wakes you up as '
                        'placed in the mission. Once airborne you join the leader by yourself.' + RESULT,
         'parameters': parameters({'how': enum(['cold', 'hot', 'runway', 'air'])}, ('how',))},
        {'type': 'function', 'name': 'follow',
         'description': 'Fly formation on the leader at a clock position from the leader\'s nose (3 = right, '
                        '4-5 = right and behind, 6 = behind, 7-8 = left and behind, 9 = left; "on my three" = 3), a '
                        'distance in metres (default 200) and a height in metres above (+) or below (-) the leader '
                        '(default 0). Only 3 to 9 o\'clock: a wingman never flies ahead.' + RESULT,
         'parameters': parameters({'clock_position': {'type': 'integer', 'minimum': 3, 'maximum': 9},
                                   'distance_m': {'type': 'number'}, 'height_m': {'type': 'number'}},
                                  ('clock_position', 'distance_m', 'height_m'))},
        {'type': 'function', 'name': 'cover_me',
         'description': 'Escort the leader: stay with them and engage enemy aircraft that come for them.' + RESULT,
         'parameters': NO_ARGUMENTS},
        {'type': 'function', 'name': 'attack',
         'description': 'Attack a target: an enemy group you detect (its name from enemies_detected), or a mission '
                        'zone (everything you find there). how: all = pass after pass until the targets are gone or '
                        'you are out of weapons; one = one target, then you wait for the leader\'s clearance (when '
                        'cleared, call attack again with the same target for the next one). If the leader does not '
                        'say how, choose and say which. against: ground or air targets (for a zone; a detected group '
                        'is what it is). A zone attack skips what the leader told you to leave alone (leave_alone). '
                        'When the attack is over you rejoin the leader by yourself.' + RESULT,
         'parameters': parameters({'target': {'type': 'string'}, 'how': enum(['all', 'one']),
                                   'against': enum(['ground', 'air'])}, ('target', 'how', 'against'))},
        {'type': 'function', 'name': 'leave_alone',
         'description': 'When the leader tells you to leave a target alone ("ignore those six soldiers", "not the '
                        'trucks, leave them for later"): target = its name from enemies_detected. how: ignore = '
                        'never attack it and do not call it again; later = attack the others first, then ask the '
                        'leader about it; attack = the leader now wants it attacked again. If you are attacking it '
                        'now, you stop.' + RESULT,
         'parameters': parameters({'target': {'type': 'string'}, 'how': enum(['ignore', 'later', 'attack'])},
                                  ('target', 'how'))},
        {'type': 'function', 'name': 'strike',
         'description': 'Attack a point on the ground: an enemy you see (its name from enemies_detected, a SAM site '
                        'too: DCS will not let you attack a SAM group without anti-radiation missiles, but it lets '
                        'you strike its point), a mission zone, or another agent\'s group (where a squad popped '
                        'smoke). weapon: auto, bombs, rockets or guns. altitude_ft: the altitude of the attack run '
                        '(0 = DCS chooses). attack_heading: the true heading of the attack run in degrees, e.g. to '
                        'come in from behind a hill or out of the sun (-1 = DCS chooses). attack: dive, level or '
                        'auto. Afterwards you go back to what you were doing.' + RESULT,
         'parameters': parameters({'target': {'type': 'string'}, 'weapon': enum(['auto', 'bombs', 'rockets', 'guns']),
                                   'altitude_ft': {'type': 'number'}, 'attack_heading': {'type': 'number'},
                                   'attack': enum(['auto', 'dive', 'level'])},
                                  ('target', 'weapon', 'altitude_ft', 'attack_heading', 'attack'))},
        {'type': 'function', 'name': 'fly_to',
         'description': 'Fly yourself to a place and orbit there: a mission zone, an enemy you see (beware of its '
                        'range), another agent\'s group, "leader" or "here". altitude_ft above the ground (above = '
                        'ground: low level, DCS\'s AI follows the terrain, 100 ft at the lowest) or above the sea. '
                        'speed_kt: 0 = your speed now. Use it to place yourself for an attack (low level under a '
                        'SAM\'s radar, behind a hill) before strike or attack.' + RESULT,
         'parameters': parameters({'where': {'type': 'string'}, 'altitude_ft': {'type': 'number'},
                                   'above': enum(['ground', 'sea']), 'speed_kt': {'type': 'number'}},
                                  ('where', 'altitude_ft', 'above', 'speed_kt'))},
        {'type': 'function', 'name': 'orbit',
         'description': 'Orbit (circle) over a point: "here" (where you are), "leader", or a mission zone; at an '
                        'altitude in feet (default your altitude now).' + RESULT,
         'parameters': parameters({'where': {'type': 'string'}, 'altitude_ft': {'type': 'number'}},
                                  ('where', 'altitude_ft'))},
        {'type': 'function', 'name': 'land',
         'description': 'Land your helicopter at a place: "here" (where you are), "leader", a mission zone, one of the '
                        'leader\'s map marks (leader_map_marks), another agent\'s group, or an airfield. offset_m and '
                        'toward move the point: toward a compass direction ("north", "south-west") or a clock '
                        'position from the leader\'s nose ("3" = their right): "land at my three, 200 m" = where '
                        'leader, offset_m 200, toward "3". You land on the nearest spot you can near that point. '
                        'minutes: how long you stay on the ground, 0 = until the next order (any flying order lifts '
                        'you off). how: normal, or combat (a quick tactical landing).' + RESULT,
         'parameters': parameters({'where': {'type': 'string'}, 'offset_m': {'type': 'number'},
                                   'toward': {'type': 'string'}, 'minutes': {'type': 'number'},
                                   'how': enum(['normal', 'combat'])},
                                  ('where', 'offset_m', 'toward', 'minutes', 'how'))},
        {'type': 'function', 'name': 'pick_up',
         'description': 'Pick up a squad (another agent\'s ground group, e.g. "Tango") with your helicopter: you '
                        'land at the place and the troops walk in by themselves. where: "" = where the squad is now, '
                        'or a place as for land (a zone, a map mark, "leader", "here"), with the same offset_m and '
                        'toward. The squad should be waiting there for pickup: if they are not, the leader tells '
                        'them. You lift off once they are aboard.' + RESULT,
         'parameters': parameters({'squad': {'type': 'string'}, 'where': {'type': 'string'},
                                   'offset_m': {'type': 'number'}, 'toward': {'type': 'string'}},
                                  ('squad', 'where', 'offset_m', 'toward'))},
        {'type': 'function', 'name': 'drop_off',
         'description': 'Take the troops you carry (situation "troops") to a place, land and let them out. where: a '
                        'place as for land, with the same offset_m and toward.' + RESULT,
         'parameters': parameters({'where': {'type': 'string'}, 'offset_m': {'type': 'number'},
                                   'toward': {'type': 'string'}}, ('where', 'offset_m', 'toward'))},
        {'type': 'function', 'name': 'return_to_base',
         'description': 'Fly to an airfield and land: its name, or "" for the nearest friendly one.' + RESULT,
         'parameters': parameters({'airfield': {'type': 'string'}}, ('airfield',))},
        {'type': 'function', 'name': 'weapons',
         'description': 'Rules of engagement: free (attack enemies you find), return (only return fire), hold '
                        '(never fire).' + RESULT,
         'parameters': parameters({'rules': enum(['free', 'return', 'hold'])}, ('rules',))},
        {'type': 'function', 'name': 'tell_pilot_when',
         'description': 'When the leader asks you to tell them when something happens ("call when airborne", "call '
                        'bingo", "tell me when you are winchester", "call when you see bandits"): you will be called '
                        'back at that moment, once. Use it instead of promising.' + RESULT,
         'parameters': parameters({'condition': enum(['airborne', 'enemies_seen', 'fuel_low', 'out_of_weapons',
                                                      'damaged']),
                                   'request': {'type': 'string', 'description': 'what the leader asked, in a few '
                                                                                'words'}},
                                  ('condition', 'request'))},
        {'type': 'function', 'name': 'playbook',
         'description': 'Open a page of your playbook (its name from the playbook index in your instructions) when '
                        'its situation starts, before you act: it tells you how we fly that situation. The page then '
                        'stays in your instructions while it applies; no need to open it again.' + RESULT,
         'parameters': parameters({'page': {'type': 'string'}}, ('page',))},
        {'type': 'function', 'name': 'remember',
         'description': 'Keep something in your memory for later flights (wingman/memory.py): about = pilot for '
                        'what you learn about the pilot as a pilot (name or nickname, habits, jokes you share), '
                        'flight for one short line about this flight after landing. One short sentence; never '
                        'private matters.' + RESULT,
         'parameters': parameters({'text': {'type': 'string'}, 'about': enum(['pilot', 'flight'])},
                                  ('text', 'about'))},
        {'type': 'function', 'name': 'forget',
         'description': 'Remove a line from your memory (its words, or part of them): when the pilot asks you to '
                        'forget something, or a line is wrong.' + RESULT,
         'parameters': parameters({'text': {'type': 'string'}}, ('text',))},
    ]
    remembered = tuple(d['name'] for d in definitions)   # every order is kept in the brain's history
    # A helicopter's own words for the tools whose use differs (user, 2026-11-05: a helicopter wingman gets its own
    # tools); the desk's numbers (wingman/desk.py PROFILES) and refusals follow the kind too.
    by_kind = {'helicopter': {
        'start_up': 'Start your helicopter when the leader tells you to: cold = on your spot with engines off (you '
                    'start them and lift off: it takes some minutes), hot = on your spot with engines running, runway '
                    '= ready to lift off, air = appear in the air beside the leader (only when the leader also flies a '
                    'helicopter). If the leader does not say how, cold. If you are parked uncontrolled (your_state '
                    'says so), any ground start wakes you up as placed in the mission. Once airborne you join the '
                    'leader by yourself if they fly a helicopter; if they fly an airplane you hold over the field and '
                    'wait for orders.' + RESULT,
        'follow': 'Fly formation on the leader\'s helicopter at a clock position from the leader\'s nose (3 = right, '
                  '4-5 = right and behind, 6 = behind, 7-8 = left and behind, 9 = left), a distance in metres '
                  '(default 100) and a height in metres above (+) or below (-) the leader (default 0). Only 3 to 9 '
                  'o\'clock, and only with a leader in a helicopter.' + RESULT,
        'strike': 'Strafe a point on the ground with your helicopter (a strafing run with guns and rockets: the '
                  'only way your helicopter attacks the ground, attack too): an enemy you see (its name from '
                  'enemies_detected), a mission zone, or another agent\'s group. weapon: auto (guns and rockets), '
                  'rockets or guns. altitude_ft: the height of the run (0 = DCS chooses; keep it low, 150 to 300 ft, '
                  'below the threats). attack_heading: the true heading of the run in degrees, e.g. from behind a '
                  'hill (-1 = DCS chooses). attack: auto (a strafing run has no dive or level choice). Afterwards '
                  'you go back to what you were doing.' + RESULT,
        'fly_to': 'Fly your helicopter to a place and circle there: a mission zone, an enemy you see (beware of its '
                  'range), another agent\'s group, "leader" or "here". altitude_ft above the ground (above = ground: '
                  'nap of the earth, 150 ft at the lowest to clear the trees and wires) or above the sea. speed_kt: '
                  '0 = your speed now. Use it to move low behind the terrain before strike or attack.' + RESULT,
    }}

    only_for = {'land': 'helicopter', 'pick_up': 'helicopter', 'drop_off': 'helicopter'}   # tools of one kind only

    def kind_definitions(self) -> list[dict]:
        kind = self.desk.kind()
        overrides = self.by_kind.get(kind, {})
        return [{**d, 'description': overrides[d['name']]} if d['name'] in overrides else d for d in self.definitions
                if self.only_for.get(d['name'], kind) == kind]

    def __init__(self, desk, memory=None, playbook=None):
        self.desk, self.memory = desk, memory
        self.playbook = playbook if playbook is not None else Playbook(kind=desk.kind)
        self.handlers = {
            'playbook': self.playbook.open,
            'start_up': desk.start_up,
            'follow': desk.follow,
            'cover_me': desk.cover_me,
            'attack': desk.attack,
            'leave_alone': self.leave_alone,
            'strike': desk.strike,
            'fly_to': desk.fly_to,
            'orbit': desk.orbit,
            'land': desk.land,
            'pick_up': desk.pick_up,
            'drop_off': desk.drop_off,
            'return_to_base': desk.return_to_base,
            'weapons': desk.weapons_rules,
            'tell_pilot_when': self.tell_pilot_when,
            'remember': self.remember,
            'forget': self.forget,
        }

    def leave_alone(self, target: str, how: str = 'ignore') -> dict:
        refused = self.desk.refuse('leave_alone')
        return refused or self.desk.leave_alone(target, how)

    def remember(self, text: str, about: str = 'pilot') -> dict:
        if self.memory is None:
            return {'ok': False, 'detail': 'you have no memory file'}
        return self.memory.remember(text, about)

    def forget(self, text: str) -> dict:
        if self.memory is None:
            return {'ok': False, 'detail': 'you have no memory file'}
        return self.memory.forget(text)

    def tell_pilot_when(self, condition: str, request: str) -> dict:
        refused = self.desk.refuse('tell_pilot_when')
        return refused or self.desk.tell_pilot_when(condition, request)

    def tools(self) -> list[dict]:
        return [{'type': 'namespace', 'name': NAMESPACE, 'description': NAMESPACE_DESCRIPTION,
                 'tools': self.kind_definitions()}]

    def guide(self) -> str:
        return '\n'.join(f'- {d["name"]}: {d["description"]}' for d in self.kind_definitions())

    def call(self, name: str, arguments: str) -> str:
        handler = self.handlers.get(name.split('.')[-1])
        if not handler:
            return json.dumps({'error': f'unknown tool {name}'})
        try:
            return json.dumps(handler(**json.loads(arguments or '{}')), ensure_ascii=False)
        except Exception as exc:   # a tool failure is reported to the model, never raised into the radio loop
            return json.dumps({'error': f'{type(exc).__name__}: {exc}'})
