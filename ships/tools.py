"""The ships agent's toolbox (ships/desk.py): the artillery's fire missions, sailing at a speed in knots instead of
driving, attacking an enemy group, navigation aids; and on an aircraft carrier, turning into the wind."""
from artillery.tools import ArtilleryToolBox
from instructor.tools import parameters
from squad.tools import OFFSET, RESULT

NAMESPACE = 'ships'
NAMESPACE_DESCRIPTION = 'Orders for your own ships: sail, speed, all stop, fire missions, attack, rules of ' \
                        'engagement, navigation aids (TACAN, and ICLS / Link 4 / ACLS on a carrier), turning into ' \
                        'the wind on a carrier.'
NOT_FOR_SHIPS = ('move_to', 'set_speed', 'hold', 'attack', 'pop_smoke')
SPEED = {'type': 'number', 'description': 'speed in knots (a carrier steams up to about 30)'}
OWN = [
    {'type': 'function', 'name': 'move_to',
     'description': 'Sail straight to a place: refused when land is in the way (then pick another point).' + RESULT,
     'parameters': parameters({
         'destination': {'type': 'string',
                         'description': 'coordinates the pilot gives ("38T KM 12345 67890" or "N42 15.300 E041 '
                                        '47.200"), a mission zone or F10 map mark from your situation, or "here"'},
         'speed_kt': SPEED, **OFFSET}, ('destination',))},
    {'type': 'function', 'name': 'set_speed',
     'description': 'Change your speed on the course you are sailing now.' + RESULT,
     'parameters': parameters({'speed_kt': SPEED}, ('speed_kt',))},
    {'type': 'function', 'name': 'hold',
     'description': 'All stop: the ships stop where they are.' + RESULT, 'parameters': parameters({})},
    {'type': 'function', 'name': 'attack',
     'description': 'Engage an enemy group your ships detect or detected lately (enemies_in_sight or '
                    'enemies_out_of_sight), e.g. another ship.' + RESULT,
     'parameters': parameters({'enemy_group': {'type': 'string', 'description': 'the group name from your situation'}},
                              ('enemy_group',))},
    {'type': 'function', 'name': 'navigation_aid',
     'description': 'Switch a navigation aid on or off aboard your ship, as the pilot asks (also when the mission set '
                    'none): TACAN (channel 1-126, X or Y, a callsign of up to 4 letters; e.g. 71X), and on an '
                    'aircraft carrier ICLS (channel 1-20), Link 4 (331-399 MHz) and ACLS. Tell the pilot the '
                    'channel.' + RESULT,
     'parameters': parameters({
         'aid': {'type': 'string', 'enum': ['tacan', 'icls', 'link4', 'acls']},
         'on': {'type': 'boolean', 'description': 'false switches it off'},
         'channel': {'type': 'integer', 'description': 'TACAN or ICLS channel'},
         'mode': {'type': 'string', 'enum': ['X', 'Y'], 'description': 'TACAN X or Y'},
         'callsign': {'type': 'string', 'description': 'TACAN Morse callsign, up to 4 letters'},
         'mhz': {'type': 'number', 'description': 'Link 4 frequency'}}, ('aid',))},
]
OWN.append(
    {'type': 'function', 'name': 'steer',
     'description': 'Steer a course with no destination ("course 027 at 15 knots"): along that true course for '
                    'nautical_miles (default 30), stopping 1 km short of land if land is ahead (the answer says how '
                    'far it goes).' + RESULT,
     'parameters': parameters({
         'course_deg': {'type': 'number', 'description': 'true course in degrees, 0 to 359'},
         'speed_kt': SPEED,
         'nautical_miles': {'type': 'number', 'description': 'how far to go on it (default 30, at most 200)'}},
         ('course_deg',))})
OWN.append(
    {'type': 'function', 'name': 'flight_deck',
     'description': 'A helicopter asks to land on your deck (or to take off from it): open true = green deck, your '
                    'ship steadies on a course with the real wind on the port bow at a slow speed and does not '
                    'manoeuvre until you close it; then clear the pilot to land with the wind, your course and speed. '
                    'open false = red deck, once the pilot says it is down and secure, or has left. DCS raises the '
                    'deck net by itself.' + RESULT,
     'parameters': parameters({
         'open': {'type': 'boolean', 'description': 'true: green deck; false: closed'},
         'speed_kt': {'type': 'number', 'description': 'steady speed for the landing, 3 to 20 knots (default 10)'},
         'minutes': {'type': 'number', 'description': 'how long the deck stays green at most (default 30)'}},
         ('open',))})
CARRIER = [
    {'type': 'function', 'name': 'turn_into_wind',
     'description': 'Aircraft carrier only: steam into the wind for flying. Works out the course from the real wind '
                    'where you are (down the angled deck for a recovery) and the speed for the wind over the deck '
                    'asked. Never turn while an aircraft is on final: ask first.' + RESULT,
     'parameters': parameters({
         'wind_over_deck_kt': {'type': 'number', 'description': 'wind over the deck wanted, usually 25 knots'},
         'minutes': {'type': 'number', 'description': 'how long to hold that course (10 to 180, default 30)'},
         'for_recovery': {'type': 'boolean', 'description': 'true: aircraft landing (wind down the angled deck); '
                                                            'false: launching (wind straight down the bow)'}}, ())},
]
BY_NAME = {d['name']: d for d in OWN}


class ShipToolBox(ArtilleryToolBox):
    definitions = [BY_NAME.get(d['name'], d) for d in ArtilleryToolBox.definitions if d['name'] != 'pop_smoke'] + \
        [BY_NAME['steer'], BY_NAME['navigation_aid'], BY_NAME['flight_deck']] + CARRIER
    remembered = tuple(d['name'] for d in definitions)
    namespace, namespace_description = NAMESPACE, NAMESPACE_DESCRIPTION

    def __init__(self, desk):
        super().__init__(desk)
        self.handlers.pop('pop_smoke', None)
        self.handlers.update(move_to=desk.move_to, set_speed=desk.set_speed, hold=desk.hold, attack=desk.attack,
                             navigation_aid=desk.navigation_aid, turn_into_wind=desk.turn_into_wind,
                             flight_deck=desk.flight_deck, steer=desk.steer)

    def current(self) -> list[dict]:
        carrier = self.desk.is_carrier()
        return [d for d in self.definitions if carrier or d not in CARRIER]
