"""The controller agent's toolbox (controller/desk.py): the pilot's check-in, the task board, frequencies on a
need-to-know basis. No tool moves its sensors: the pilot cannot command the AWACS (its route is the editor's)."""
from controller.desk import TASK_STATES
from instructor.tools import parameters
from squad.tools import SquadToolBox

NAMESPACE = 'controller'
NAMESPACE_DESCRIPTION = 'The air controller\'s board: the pilot\'s check-in, tasks for the pilot, handoffs and ' \
                        'frequencies.'
RESULT = ' Returns ok and what was done.'
TASK_ID = {'type': 'string', 'description': 'the task id from your task_board, e.g. "T1"'}


class ControllerToolBox(SquadToolBox):
    definitions = [
        {'type': 'function', 'name': 'check_in',
         'description': 'The pilot checks in with you once airborne: record what he carries and his fuel, as he said '
                        'them (ask him if he did not). Then a task that fits his weapons may be offered.' + RESULT,
         'parameters': parameters({
             'loadout': {'type': 'string', 'description': 'his weapons as he said them, in English (e.g. "2 GBU-12, '
                                                          '4 AGM-65, gun")'},
             'fuel': {'type': 'string', 'description': 'his fuel as he said it (e.g. "6000 lb")'},
             'notes': {'type': 'string', 'description': 'anything else he said that matters (e.g. "no targeting pod")'}},
             ('loadout',))},
        {'type': 'function', 'name': 'assign_task',
         'description': 'Give the checked-in pilot a task from your board, once he accepts it: the answer has where '
                        'it is from him (bearing, range, grid) and who he contacts on which frequency: tell him all '
                        'of it ("contact Axe on 256.000"), that is the handoff. A task he had goes back on the board.'
                        + RESULT,
         'parameters': parameters({'task_id': TASK_ID}, ('task_id',))},
        {'type': 'function', 'name': 'update_task',
         'description': 'Change a task on your board when the pilot reports: en_route, on_station, done (complete), '
                        'refused (he cannot or will not do it: it goes back on the board), cancelled, or open again.'
                        + RESULT,
         'parameters': parameters({'task_id': TASK_ID,
                                   'status': {'type': 'string', 'enum': list(TASK_STATES)},
                                   'note': {'type': 'string', 'description': 'a few words, e.g. "2 tanks destroyed"'}},
                                  ('task_id', 'status'))},
        {'type': 'function', 'name': 'assign_station',
         'description': 'Send the checked-in pilot who has no task (his task done, refused, or none yet) to a station '
                        'to hold and await tasking: a station from your situation, one he asked for, or empty for '
                        'the nearest clear of known threats. The answer has where it is from him and the route '
                        'around the threats: tell him all of it.' + RESULT,
         'parameters': parameters({
             'station': {'type': 'string', 'description': 'a station or mission point by its name, or empty'},
             'altitude_ft': {'type': 'number', 'description': 'the altitude to hold at in feet (20000 = angels 20), '
                                                              'or 0'}}, ())},
        {'type': 'function', 'name': 'give_frequency',
         'description': 'The pilot asks for another station\'s frequency (a unit, a battery): you give it only when '
                        'he needs it, the answer says whether he does (need to know).' + RESULT,
         'parameters': parameters({'agent': {'type': 'string', 'description': 'the station\'s name, e.g. "Tango"'}},
                                  ('agent',))},
    ]
    remembered = tuple(d['name'] for d in definitions)
    namespace, namespace_description = NAMESPACE, NAMESPACE_DESCRIPTION

    def __init__(self, desk):
        self.desk = desk
        self.handlers = {'check_in': desk.check_in, 'assign_task': desk.assign_task,
                         'update_task': desk.update_task, 'assign_station': desk.assign_station,
                         'give_frequency': desk.give_frequency}
