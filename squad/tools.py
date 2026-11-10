"""The squad agent's toolbox: the orders the model may give its own group (squad/desk.py), as function tools.

Same interface as the instructor's and the ATC's (one namespace for the ChatGPT plan route, a guide for the system
prompt, call() always returns JSON). The model chooses; the desk checks and sends; the mission does it.
"""
import json

from instructor.tools import NO_ARGUMENTS, parameters

NAMESPACE = 'squad'
NAMESPACE_DESCRIPTION = 'Orders for your own squad: move, wait for pickup, board or leave the helicopter, hold, ' \
                        'rules of engagement, smoke, attack an enemy, tell the pilot when something happens.'
DESTINATION = {'type': 'string', 'description': '"here", "helicopter" (the pilot\'s, only when you see it), the name '
                                                'of another aircraft or friendly unit you see (other_aircraft_in_sight, '
                                                'friendly_units_in_sight), a smoke you see ("green smoke", from '
                                                'smoke_in_sight) or the name of a mission zone from your situation'}
RESULT = ' Returns ok and what the mission answered.'
# A place moved from where it is (user, 2026-11-05: "Bison, drop them 200 m from the helicopter"), as the wingman's.
OFFSET = {'offset_m': {'type': 'number', 'description': 'move the place this many metres (0: the place itself)'},
          'toward': {'type': 'string', 'description': 'which way offset_m goes: a compass direction ("north", '
                                                      '"south-west") or a clock position from the pilot\'s nose ("3" '
                                                      '= their right, "12" = ahead): "200 m to the right of the '
                                                      'helicopter" = destination helicopter, offset_m 200, toward '
                                                      '"3"'}}


class SquadToolBox:
    definitions = [
        {'type': 'function', 'name': 'move_to',
         'description': 'Move the squad to a place.' + RESULT,
         'parameters': parameters({'destination': DESTINATION,
                                   'speed': {'type': 'string', 'enum': ['slow', 'normal', 'fast']}, **OFFSET},
                                  ('destination',))},
        {'type': 'function', 'name': 'wait_for_pickup',
         'description': 'Go to a place and wait there to be picked up by the helicopter (needed before boarding).'
                        + RESULT,
         'parameters': parameters({'destination': DESTINATION, **OFFSET})},
        {'type': 'function', 'name': 'board_helicopter',
         'description': 'Board the pilot\'s helicopter, once it has landed next to you and the pilot says so.'
                        + RESULT,
         'parameters': NO_ARGUMENTS},
        {'type': 'function', 'name': 'board_vehicle',
         'description': 'Ask another agent\'s vehicles (with a troop compartment) on the radio to come and take you '
                        'aboard where you are; they drive to you and you board by yourselves when they stop.'
                        + RESULT,
         'parameters': parameters({'vehicle': {'type': 'string',
                                               'description': 'the vehicles\' name, as the pilot says it'}},
                                  ('vehicle',))},
        {'type': 'function', 'name': 'disembark',
         'description': 'Leave the helicopter once it has landed where the pilot drops you; aboard another agent\'s '
                        'vehicles, ask them to stop and let you out where they are.' + RESULT,
         'parameters': NO_ARGUMENTS},
        {'type': 'function', 'name': 'hold',
         'description': 'Stop where you are.' + RESULT, 'parameters': NO_ARGUMENTS},
        {'type': 'function', 'name': 'set_rules_of_engagement',
         'description': 'Weapons free, return fire only, or weapons hold.' + RESULT,
         'parameters': parameters({'rules': {'type': 'string', 'enum': ['free', 'return', 'hold']}}, ('rules',))},
        {'type': 'function', 'name': 'pop_smoke',
         'description': 'Mark your position with smoke for the pilot.' + RESULT,
         'parameters': parameters({'color': {'type': 'string',
                                             'enum': ['green', 'red', 'white', 'orange', 'blue']}}, ('color',))},
        {'type': 'function', 'name': 'attack',
         'description': 'Weapons free and advance on an enemy group you see or saw lately (enemies_in_sight or '
                        'enemies_out_of_sight in your situation).' + RESULT,
         'parameters': parameters({'enemy_group': {'type': 'string',
                                                   'description': 'the group name from your situation'}},
                                  ('enemy_group',))},
        {'type': 'function', 'name': 'tell_pilot_when',
         'description': 'When the pilot asks you to tell them when something happens (e.g. "tell me when you see the '
                        'helicopter"): you will be called back at that moment, once. Use it instead of promising.'
                        + RESULT,
         'parameters': parameters({
             'condition': {'type': 'string', 'enum': ['helicopter_seen', 'enemies_seen', 'zone_reached',
                                                      'casualties']},
             'request': {'type': 'string', 'description': 'what the pilot asked, in a few words'},
             'zone': {'type': 'string', 'description': 'zone name, for zone_reached only'}},
             ('condition', 'request'))},
    ]
    remembered = tuple(d['name'] for d in definitions)   # every order is kept in the brain's history
    namespace, namespace_description = NAMESPACE, NAMESPACE_DESCRIPTION

    def __init__(self, desk):
        self.desk = desk
        self.handlers = {'move_to': desk.move_to, 'wait_for_pickup': desk.wait_for_pickup,
                         'board_helicopter': desk.board_helicopter, 'board_vehicle': desk.board_vehicle,
                         'disembark': desk.disembark, 'hold': desk.hold,
                         'set_rules_of_engagement': desk.set_rules_of_engagement, 'pop_smoke': desk.pop_smoke,
                         'attack': desk.attack, 'tell_pilot_when': desk.tell_pilot_when}

    def current(self) -> list[dict]:
        """The tools offered now (a vehicle unit's depend on its troop compartment)."""
        return self.definitions

    def tools(self) -> list[dict]:
        return [{'type': 'namespace', 'name': self.namespace, 'description': self.namespace_description,
                 'tools': self.current()}]

    def guide(self) -> str:
        return '\n'.join(f'- {d["name"]}: {d["description"]}' for d in self.current())

    def call(self, name: str, arguments: str) -> str:
        handler = self.handlers.get(name.split('.')[-1])
        if not handler:
            return json.dumps({'error': f'unknown tool {name}'})
        try:
            return json.dumps(handler(**json.loads(arguments or '{}')), ensure_ascii=False)
        except Exception as exc:   # a tool failure is reported to the model, never raised into the radio loop
            return json.dumps({'error': f'{type(exc).__name__}: {exc}'})
