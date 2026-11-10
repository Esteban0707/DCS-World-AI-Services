"""The ground vehicles agent's toolbox (vehicles/desk.py): the squad's orders without boarding, driving instead of
walking, and carrying another squad when its vehicles have a troop compartment."""
from instructor.tools import parameters
from squad.tools import DESTINATION, OFFSET, RESULT, SquadToolBox

NAMESPACE = 'vehicles'
NAMESPACE_DESCRIPTION = 'Orders for your own vehicles: drive on or off the road in a formation, hold, rules of ' \
                        'engagement, smoke, attack an enemy, pick up and drop a squad (with a troop compartment), ' \
                        'tell the pilot when something happens.'
ROAD = {'type': 'string', 'enum': ['off', 'on'],
        'description': 'on: along the roads (DCS finds the way, faster and longer); off: straight across country'}
SPEED_KMH = {'type': 'number', 'description': 'an exact speed in km/h when the pilot gives one (wins over speed); '
                                              'slow is about 18, normal 36, fast 61'}
SQUAD_ONLY =('wait_for_pickup', 'board_helicopter', 'board_vehicle', 'disembark')
CARRIER_ONLY = ('pick_up', 'drop_off')


# Its own tools: driving instead of walking, and carrying a squad (only with a troop compartment).
OWN = [
    {'type': 'function', 'name': 'move_to',
     'description': 'Drive your vehicles to a place, on or off the road, in a formation.' + RESULT,
     'parameters': parameters({'destination': DESTINATION,
                               'speed': {'type': 'string', 'enum': ['slow', 'normal', 'fast']},
                               'road': ROAD,
                               'formation': {'type': 'string', 'enum': ['keep', 'column', 'row', 'wedge', 'vee',
                                                                        'diamond', 'echelon_right',
                                                                        'echelon_left'],
                                             'description': 'keep: as it is now'},
                               'speed_kmh': SPEED_KMH, **OFFSET},
                              ('destination',))},
    {'type': 'function', 'name': 'set_speed',
     'description': 'Change your speed on the move you are making now (same destination and road).' + RESULT,
     'parameters': parameters({'speed': {'type': 'string', 'enum': ['slow', 'normal', 'fast']},
                               'speed_kmh': SPEED_KMH}, ())},
    {'type': 'function', 'name': 'pick_up',
     'description': 'Drive to a squad and take it aboard (the squad is told to wait for you there; it boards by '
                    'itself when you stop).' + RESULT,
     'parameters': parameters({'squad': {'type': 'string', 'description': 'the squad\'s name, as the pilot says '
                                                                           'it'},
                               'where': {'type': 'string', 'description': 'empty: where the squad is now; or a '
                                                                          'place as for move_to'},
                               'road': ROAD, **OFFSET},
                              ('squad',))},
    {'type': 'function', 'name': 'drop_off',
     'description': 'Drive to a place and let the troops you carry get out there ("200 m from the helicopter": '
                    'where helicopter, offset_m 200 and toward the side the pilot says, or "12" if none).' + RESULT,
     'parameters': parameters({'where': DESTINATION, 'road': ROAD, **OFFSET}, ('where',))},
]
BY_NAME = {d['name']: d for d in OWN}


class VehicleToolBox(SquadToolBox):
    # The squad's tools with its own move_to, without boarding; then pick_up and drop_off.
    definitions = [BY_NAME.get(d['name'], d) for d in SquadToolBox.definitions if d['name'] not in SQUAD_ONLY] + \
        [BY_NAME['set_speed']] + [d for d in OWN if d['name'] in CARRIER_ONLY]
    remembered = tuple(d['name'] for d in definitions)
    namespace, namespace_description = NAMESPACE, NAMESPACE_DESCRIPTION

    def __init__(self, desk):
        super().__init__(desk)
        for name in SQUAD_ONLY:
            del self.handlers[name]
        self.handlers.update(pick_up=desk.pick_up, drop_off=desk.drop_off, set_speed=desk.set_speed)

    def current(self) -> list[dict]:
        carry = self.desk.can_carry()
        return [d for d in self.definitions if carry or d['name'] not in CARRIER_ONLY]
