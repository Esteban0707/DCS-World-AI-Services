"""The artillery agent's toolbox (artillery/desk.py): the vehicles' orders (it drives, holds, sees) without carrying
troops, plus fire_mission and cease_fire."""
from instructor.tools import NO_ARGUMENTS, parameters
from squad.tools import RESULT
from vehicles.tools import CARRIER_ONLY, VehicleToolBox

NAMESPACE = 'artillery'
NAMESPACE_DESCRIPTION = 'Orders for your own battery: fire missions, cease fire, drive, hold, rules of engagement, ' \
                        'smoke, tell the pilot when something happens.'
OWN = [
    {'type': 'function', 'name': 'fire_mission',
     'description': 'Fire your battery at a point. You never see the target: fire where you are told. The rounds fall '
                    'spread over spread_m around the point.' + RESULT,
     'parameters': parameters({
         'target': {'type': 'string',
                    'description': 'a target id an observer sent you (targets_from_observers, e.g. "Axe-1"); a '
                                   'mission zone or F10 map mark from your situation; coordinates the pilot gives, '
                                   'written "38T KM 12345 67890" (MGRS) or "N42 15.300 E041 47.200"; an enemy group '
                                   'you see yourself; or "last" for the last aim point (corrections)'},
         'rounds': {'type': 'integer', 'description': 'how many rounds (a full salvo of a BM-21 is 40 per launcher); '
                                                      '0: as many as your crews decide'},
         'spread_m': {'type': 'number', 'description': 'radius in metres the rounds are spread over (default 100; '
                                                       'wider for an area, narrower for a point)'},
         'offset_m': {'type': 'number', 'description': 'a correction: move the point this many metres (0: none)'},
         'toward': {'type': 'string',
                    'description': 'which way offset_m goes: "add" (farther from the observer), "drop" (closer to '
                                   'the observer), "left" or "right" as the observer sees it, or a compass direction'},
         'queue': {'type': 'boolean',
                   'description': 'true: fire it after the current fire mission (your battery works through its '
                                  'queue by itself); false: now, dropping what it lays on or fires now'}},
         ('target',))},
    {'type': 'function', 'name': 'cease_fire',
     'description': 'Stop firing now; the queued fire missions are dropped too.' + RESULT,
     'parameters': NO_ARGUMENTS},
]


class ArtilleryToolBox(VehicleToolBox):
    definitions = [d for d in VehicleToolBox.definitions if d['name'] not in CARRIER_ONLY] + OWN
    remembered = tuple(d['name'] for d in definitions)
    namespace, namespace_description = NAMESPACE, NAMESPACE_DESCRIPTION

    def __init__(self, desk):
        super().__init__(desk)
        for name in CARRIER_ONLY:
            self.handlers.pop(name, None)
        self.handlers.update(fire_mission=desk.fire_mission, cease_fire=desk.cease_fire)

    def current(self) -> list[dict]:
        return self.definitions
