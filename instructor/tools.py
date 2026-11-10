"""The instructor's toolbox: what the model may look up when it needs it, as function tools.

Realism rule (user, 2026-11-01): only what an instructor in the control tower can know. The map and its airfields are
the tower's own charts; the aircraft's position, speed and heading come only from the airfield radar, never from the
aircraft's own data; the cockpit is never seen (instructor/tower_view.py, the black box). The tower view is a tool,
not sent with every message: the model looks when it wants to.

The library (instructor/library.py) is the books on the instructor's desk: the manuals of the aircraft being flown
and the user's own documents for it, only that aircraft's module.

Each tool: a JSON-schema definition (sent grouped in one namespace, as the ChatGPT plan route requires) and a
handler returning a JSON-ready dict. The short guide for the system prompt is generated from the definitions.
"""
import json

from instructor import geo
from instructor.library import Shelf

NAMESPACE = 'instructor'
NAMESPACE_DESCRIPTION = ("What the instructor in the control tower can look up: what the tower sees, the weather, "
                         "the tower's charts, and the aircraft's manuals.")
NO_ARGUMENTS = {'type': 'object', 'properties': {}, 'additionalProperties': False}
BOOK = {'type': 'string', 'description': 'Book id (optional; all books when left out)'}


def parameters(properties: dict, required: tuple = ()) -> dict:
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


class ToolBox:
    definitions = [
        {'type': 'function', 'name': 'get_tower_view',
         'description': 'What you see from the tower now: the aircraft through the window (within 5 nm: on the '
                        'ground or airborne; on the ground whether it moves and where on the field it is, e.g. at the '
                        'threshold of a runway or on it; outside configuration such as gear, flaps, canopy, lights) '
                        'and on the airfield radar (position from the field, altitude, ground speed, track, climb, '
                        'traffic).',
         'parameters': NO_ARGUMENTS},
        {'type': 'function', 'name': 'get_weather',
         'description': "Your airfield's weather now: ground wind, runway in use with its headwind and crosswind, "
                        'QNH, QFE, field elevation, temperature.',
         'parameters': NO_ARGUMENTS},
        {'type': 'function', 'name': 'get_airfield',
         'description': "An airfield of the tower's map by name: tower frequencies, ILS courses, TACAN channel.",
         'parameters': parameters({'name': {'type': 'string', 'description': 'Airfield name'}}, ('name',))},
        {'type': 'function', 'name': 'search_library',
         'description': "Search the aircraft's manuals (in English: use English keywords, e.g. 'engine start', "
                        "'landing approach speed') for procedures, limits, systems, controls, emergencies and key "
                        'commands. Returns the best passages with book, section id and page.',
         'parameters': parameters({'query': {'type': 'string', 'description': 'English keywords'}, 'book': BOOK},
                                  ('query',))},
        {'type': 'function', 'name': 'read_section',
         'description': 'Read one section of a manual by its id (from search_library or library_contents), e.g. '
                        '"8.1". Long sections come in parts: ask for the next part when told there is more.',
         'parameters': parameters({'section': {'type': 'string', 'description': 'Section id, e.g. "8.1"'},
                                   'book': BOOK,
                                   'part': {'type': 'integer', 'description': 'Part number, 1 when left out'}},
                                  ('section',))},
        {'type': 'function', 'name': 'library_contents',
         'description': "The table of contents of the aircraft's manuals: section ids, titles and pages.",
         'parameters': parameters({'book': BOOK})},
    ]

    def __init__(self, tower, shelf: Shelf | None = None):
        """tower: the instructor.tower_view.TowerView (which map the tower is on, which aircraft is flown);
        shelf: where the aircraft's library comes from."""
        self.tower = tower
        self.shelf = shelf or Shelf()
        self.handlers = {'get_tower_view': self.get_tower_view, 'get_weather': self.get_weather,
                         'get_airfield': self.get_airfield, 'search_library': self.search_library,
                         'read_section': self.read_section, 'library_contents': self.library_contents}

    def tools(self) -> list[dict]:
        """The Responses API "tools" value."""
        return [{'type': 'namespace', 'name': NAMESPACE, 'description': NAMESPACE_DESCRIPTION,
                 'tools': self.definitions}]

    def guide(self) -> str:
        """One line per tool, for the system prompt."""
        return '\n'.join(f'- {d["name"]}: {d["description"]}' for d in self.definitions)

    def call(self, name: str, arguments: str) -> str:
        """Run one tool call; always returns a JSON string (errors included) for the model."""
        handler = self.handlers.get(name.split('.')[-1])
        if not handler:
            return json.dumps({'error': f'unknown tool {name}'})
        try:
            args = json.loads(arguments or '{}')
            return json.dumps(handler(**args), ensure_ascii=False)
        except Exception as exc:   # a tool failure is reported to the model, never raised into the radio loop
            return json.dumps({'error': f'{type(exc).__name__}: {exc}'})

    def get_tower_view(self) -> dict:
        return self.tower.report()

    def get_weather(self) -> dict:
        return self.tower.weather()

    def get_airfield(self, name: str) -> dict:
        return geo.airfield_info(name, self.tower.tower_map_name())

    def library(self):
        aircraft = self.tower.state.aircraft
        library = self.shelf.for_aircraft(aircraft)
        if library is None:
            raise LookupError(f'no library for {aircraft or "this aircraft (not in an aircraft yet)"}')
        return library

    def search_library(self, query: str, book: str | None = None) -> dict:
        return self.library().search(query, book)

    def read_section(self, section: str, book: str | None = None, part: int = 1) -> dict:
        return self.library().read(section, book, part)

    def library_contents(self, book: str | None = None) -> dict:
        return self.library().contents(book)
