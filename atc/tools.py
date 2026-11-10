"""The ATC agent's toolbox: the decisions the model asks the desk for (atc/desk.py), as function tools.

Same interface as the instructor's (instructor/tools.py: definitions grouped in one namespace for the ChatGPT plan
route, a guide for the system prompt, call() always returns JSON). The weather and the runway in use are on the board
sent with every message; the tools are for what must be decided at the moment the pilot asks.
"""
import json

from instructor import geo
from instructor.tools import NO_ARGUMENTS, parameters

NAMESPACE = 'atc'
NAMESPACE_DESCRIPTION = "The control tower's decisions and information: clearances, radar vectors, traffic, charts."
DECISION = (' Returns the decision: status, the reason, and "transmission", the exact words to transmit (already in '
            'the radio language).')


class AtcToolBox:
    definitions = [
        {'type': 'function', 'name': 'request_taxi',
         'description': 'The pilot asks to taxi (for departure, or to parking after landing).' + DECISION,
         'parameters': NO_ARGUMENTS},
        {'type': 'function', 'name': 'request_takeoff',
         'description': 'The pilot is ready for departure and asks for takeoff.' + DECISION,
         'parameters': NO_ARGUMENTS},
        {'type': 'function', 'name': 'request_landing',
         'description': 'The pilot asks to land, reports inbound or on final. Also arms your own calls: the landing '
                        'clearance once on final, vectors in instrument weather.' + DECISION,
         'parameters': NO_ARGUMENTS},
        {'type': 'function', 'name': 'request_vectors',
         'description': 'The pilot asks for radar vectors or guidance to the runway in use.' + DECISION,
         'parameters': NO_ARGUMENTS},
        {'type': 'function', 'name': 'get_traffic',
         'description': 'Other traffic within 30 nm of your airfield: type, callsign, position, altitude, track, on '
                        'final or on the runway.',
         'parameters': NO_ARGUMENTS},
        {'type': 'function', 'name': 'get_airfield',
         'description': 'Another airfield of the map by name: its tower frequencies, ILS, TACAN.',
         'parameters': parameters({'name': {'type': 'string', 'description': 'Airfield name'}}, ('name',))},
    ]

    def __init__(self, desk):
        self.desk = desk
        self.handlers = {'request_taxi': desk.request_taxi, 'request_takeoff': desk.request_takeoff,
                         'request_landing': desk.request_landing, 'request_vectors': desk.request_vectors,
                         'get_traffic': desk.traffic_report, 'get_airfield': self.get_airfield}

    def tools(self) -> list[dict]:
        return [{'type': 'namespace', 'name': NAMESPACE, 'description': NAMESPACE_DESCRIPTION,
                 'tools': self.definitions}]

    def guide(self) -> str:
        return '\n'.join(f'- {d["name"]}: {d["description"]}' for d in self.definitions)

    def call(self, name: str, arguments: str) -> str:
        handler = self.handlers.get(name.split('.')[-1])
        if not handler:
            return json.dumps({'error': f'unknown tool {name}'})
        try:
            return json.dumps(handler(**json.loads(arguments or '{}')), ensure_ascii=False)
        except Exception as exc:   # a tool failure is reported to the model, never raised into the radio loop
            return json.dumps({'error': f'{type(exc).__name__}: {exc}'})

    def get_airfield(self, name: str) -> dict:
        _, _, station = self.desk.tuned()
        return geo.airfield_info(name, station.map if station else None)
