"""Offline tests for the agents' Triggers (shared/triggers.py): events (mission flag on -> the agent calls) and actions
(a tool per action -> the agent sets a flag), and the Responder's event call.

Run: python -m unittest discover tests
"""
import json
import time
import unittest

import quiet_log  # noqa: F401  (the program's log goes to a temporary file)
from instructor.brain import Brain
from instructor.voice_loop import EVENT_PROMPT, Responder
from shared import triggers
from shared.cockpit import CockpitState
from shared.triggers import FlagWatch, TriggerToolBox
from squad.desk import SquadDesk
from squad.tools import SquadToolBox

SQUAD = {'id': 'buenos', 'name': 'Buenos', 'role': 'squad', 'active': True,
         'events': [{'flag': '10', 'instructions': 'If you see the helicopter, tell the pilot where you are.'}],
         'actions': [{'name': 'smoke_ready', 'description': 'Use it when the pilot sees your smoke.', 'flag': '20'}]}
MECHANIC = {'id': 'mechanic', 'name': 'Mechanic', 'role': 'instructor', 'active': True,
            'events': [{'flag': '10', 'instructions': 'Tell the pilot the convoy is moving.'},
                       {'flag': 'convoy_left', 'instructions': 'Say the convoy has left.'}]}
ATC = {'id': 'atc', 'name': 'ATC', 'role': 'atc', 'active': True,
       'events': [{'flag': '5', 'instructions': 'x'}], 'actions': [{'name': 'a', 'description': 'b', 'flag': '6'}]}


class FakeAgents:
    def __init__(self, *agents):
        self.agents = {a['id']: a for a in agents}

    def active(self):
        return [a for a in self.agents.values() if a.get('active')]

    def get(self, agent_id):
        return self.agents.get(agent_id)


class FakeTasks:
    """TaskChannel: records what would go to the hook; answers set_flag through the state like the hook does."""

    def __init__(self, state=None, answer=True):
        self.state, self.answer, self.sent = state, answer, []

    def flags(self, watched, settable):
        self.sent.append(('flags', list(watched), list(settable)))
        return 'f1'

    def set_flag(self, name, value=1):
        self.sent.append(('set_flag', name, value))
        request_id = f'r{len(self.sent)}'
        if self.state is not None and self.answer:
            self.state.replies[request_id] = {'type': 'task', 'id': request_id, 'ok': True,
                                              'detail': f'flag {name} = {value}'}
        return request_id


def flags(state, values):
    state.apply({'type': 'flags', 'flags': values})


class Parsing(unittest.TestCase):
    def test_valid_entries(self):
        self.assertEqual(triggers.events(SQUAD), [{**SQUAD['events'][0], 'internal': False}])
        self.assertEqual(triggers.actions(SQUAD), [{**SQUAD['actions'][0], 'value': 1, 'internal': False}])   # value 1 by default

    def test_the_atc_is_fixed(self):
        self.assertEqual((triggers.events(ATC), triggers.actions(ATC)), ([], []))
        with self.assertRaises(ValueError):
            triggers.check(ATC)

    def test_bad_entries_left_out_and_named_by_check(self):
        agent = {'role': 'squad', 'events': [{'flag': '10; os.exit()', 'instructions': 'x'}],
                 'actions': [{'name': 'Smoke Ready', 'description': 'x', 'flag': '20'}]}
        self.assertEqual((triggers.events(agent), triggers.actions(agent)), ([], []))
        with self.assertRaisesRegex(ValueError, 'events.0'):
            triggers.check(agent)
        agent['events'] = []
        with self.assertRaisesRegex(ValueError, 'actions.0'):
            triggers.check(agent)
        triggers.check(SQUAD)

    def test_action_cannot_take_a_role_tool_name_or_repeat(self):
        reserved = tuple(d['name'] for d in SquadToolBox.definitions)
        agent = {'role': 'squad', 'actions': [{'name': 'pop_smoke', 'description': 'x', 'flag': '20'}]}
        self.assertEqual(triggers.actions(agent, reserved), [])
        twice = {'role': 'squad', 'actions': [{'name': 'go', 'description': 'x', 'flag': '1'},
                                               {'name': 'go', 'description': 'y', 'flag': '2'}]}
        with self.assertRaisesRegex(ValueError, 'actions.1'):
            triggers.check(twice)


class Watch(unittest.TestCase):
    def setUp(self):
        self.state = CockpitState()
        self.tasks = FakeTasks()
        self.watch = FlagWatch(FakeAgents(SQUAD, MECHANIC, ATC), self.state, self.tasks)

    def test_lists_are_every_active_agents_flags_except_the_atc(self):
        self.watch.tick()
        self.assertEqual(self.tasks.sent, [('flags', ['10', 'convoy_left'], ['20'])])
        self.watch.tick()
        self.assertEqual(len(self.tasks.sent), 1)                  # not again until LISTS_EVERY_S

    def test_fires_on_off_to_on_only(self):
        flags(self.state, {'10': 0, 'convoy_left': 1})                 # baseline: convoy_left already on
        self.assertEqual(self.watch.tick(), [])
        self.assertEqual(self.watch.tick(), [])                       # same message: nothing new
        flags(self.state, {'10': 1, 'convoy_left': 1})
        fired = self.watch.tick()
        self.assertEqual(sorted((a, e['flag']) for a, e in fired), [('buenos', '10'), ('mechanic', '10')])
        flags(self.state, {'10': 2, 'convoy_left': 1})                 # still on: no new event
        self.assertEqual(self.watch.tick(), [])
        flags(self.state, {'10': 0, 'convoy_left': 0})
        self.watch.tick()
        flags(self.state, {'10': 0, 'convoy_left': 1})
        self.assertEqual([(a, e['flag']) for a, e in self.watch.tick()], [('mechanic', 'convoy_left')])

    def test_first_reading_on_does_not_fire(self):
        flags(self.state, {'10': 1})
        self.assertEqual(self.watch.tick(), [])

    def test_stale_flags_ignored(self):
        flags(self.state, {'10': 0})
        self.watch.tick()
        flags(self.state, {'10': 1})
        self.state.flags_at = time.monotonic() - 60
        self.assertEqual(self.watch.tick(), [])

    def test_inactive_agent_does_not_listen(self):
        watch = FlagWatch(FakeAgents({**SQUAD, 'active': False}), self.state, self.tasks)
        flags(self.state, {'10': 0})
        watch.tick()
        flags(self.state, {'10': 1})
        self.assertEqual(watch.tick(), [])
        self.assertEqual(self.tasks.sent, [])                         # no flags for nobody


class Actions(unittest.TestCase):
    def setUp(self):
        self.state = CockpitState()
        self.tasks = FakeTasks(self.state)
        self.box = TriggerToolBox(SquadToolBox(SquadDesk(CockpitState(), SQUAD)), FakeAgents(SQUAD), 'buenos', self.tasks, self.state)

    def test_action_becomes_a_tool(self):
        namespaces = {n['name']: [t['name'] for t in n['tools']] for n in self.box.tools()}
        self.assertEqual(namespaces['mission'], ['smoke_ready'])
        self.assertIn('pop_smoke', namespaces['squad'])
        self.assertIn('- smoke_ready: Use it when the pilot sees your smoke.', self.box.guide())

    def test_calling_it_sets_its_flag(self):
        for name in ('mission.smoke_ready', 'smoke_ready'):
            self.assertEqual(json.loads(self.box.call(name, '{}')), {'ok': True, 'detail': 'done'})
        self.assertEqual(self.tasks.sent[-1], ('set_flag', '20', 1))

    def test_role_tools_still_reach_the_role(self):
        self.assertIn('error', json.loads(self.box.call('squad.smoke_ready_not', '{}')))
        self.assertEqual(self.tasks.sent, [])

    def test_no_answer_from_the_mission(self):
        self.tasks.answer = False
        triggers.REPLY_WAIT_S, saved = 0.1, triggers.REPLY_WAIT_S
        try:
            self.assertFalse(json.loads(self.box.call('smoke_ready', '{}'))['ok'])
        finally:
            triggers.REPLY_WAIT_S = saved

    def test_no_actions_no_namespace(self):
        box = TriggerToolBox(SquadToolBox(SquadDesk(CockpitState(), SQUAD)), FakeAgents(MECHANIC), 'mechanic', self.tasks, self.state)
        self.assertEqual([n['name'] for n in box.tools()], ['squad'])


TANGO = {'id': 'tango', 'name': 'Tango', 'role': 'squad', 'active': True,
         'actions': [{'name': 'confirm_in_z1', 'description': 'When your team is in zone 1.', 'flag': 'tango_z1',
                      'internal': True},
                     {'name': 'smoke_ready', 'description': 'When the pilot sees the smoke.', 'flag': '20'}]}
VICTOR = {'id': 'victor', 'name': 'Victor', 'role': 'squad', 'active': True,
          'events': [{'flag': 'tango_z1', 'instructions': 'Ask the pilot whether you advance.', 'internal': True},
                     {'flag': 'tango_z1', 'instructions': 'A DCS flag of the same name: another flag.'}]}


class Internal(unittest.TestCase):
    """Internal flags (2026-11-03): Tango's action fires Victor's event inside the program; DCS never sees them."""

    def setUp(self):
        self.state = CockpitState()
        self.tasks = FakeTasks(self.state)
        self.internal = triggers.InternalFlags()
        self.agents = FakeAgents(TANGO, VICTOR)
        self.watch = FlagWatch(self.agents, self.state, self.tasks, self.internal)
        self.box = TriggerToolBox(None, self.agents, 'tango', self.tasks, self.state, self.internal)

    def test_action_fires_the_other_agents_event_at_once(self):
        self.assertEqual(self.watch.tick(), [])
        self.assertEqual(json.loads(self.box.call('mission.confirm_in_z1', '{}')), {'ok': True, 'detail': 'done'})
        fired = self.watch.tick()
        self.assertEqual([(a, e['instructions']) for a, e in fired],
                         [('victor', 'Ask the pilot whether you advance.')])   # not the DCS event of that name
        self.assertEqual(fired[0][1]['cause'], 'Tango used its action confirm_in_z1, which it uses when: When your '
                                               'team is in zone 1.')   # Victor knows what happened
        self.assertEqual(self.watch.tick(), [])                                 # once
        self.box.call('confirm_in_z1', '{}')                                    # still on: no new event
        self.assertEqual(self.watch.tick(), [])
        self.internal.set('tango_z1', 0)
        self.box.call('confirm_in_z1', '{}')                                    # off -> on again
        self.assertEqual(len(self.watch.tick()), 1)

    def test_dcs_never_sees_them(self):
        self.watch.tick()
        self.box.call('confirm_in_z1', '{}')
        self.assertNotIn(('set_flag', 'tango_z1', 1), self.tasks.sent)
        self.assertEqual(self.tasks.sent[0], ('flags', ['tango_z1'], ['20']))   # only the DCS rows' flags

    def test_without_the_store_it_says_so(self):
        box = TriggerToolBox(None, self.agents, 'tango', self.tasks, self.state)
        self.assertFalse(json.loads(box.call('confirm_in_z1', '{}'))['ok'])


class FakeDesk:
    def __init__(self):
        self.now = None

    def facts(self):
        return self.now


def facts(alive=4, enemies=False, helicopter=False, zones=(), lost=False):
    return {'lost': True} if lost else {'lost': False, 'alive': alive, 'size': 4, 'enemies': enemies,
                                        'helicopter': helicopter, 'zones': set(zones)}


class Conditions(unittest.TestCase):
    """Condition events (2026-11-03): something happens to a squad -> an agent calls, no flag."""

    def setUp(self):
        self.tango = {'id': 'tango', 'name': 'Tango', 'role': 'squad', 'active': True, 'events': [
            {'when': 'casualties', 'instructions': 'Report losses.'},
            {'when': 'zone_reached', 'zone': 'Z1', 'instructions': 'Say you are in Z1.'}]}
        self.victor = {'id': 'victor', 'name': 'Victor', 'role': 'squad', 'active': True, 'events': [
            {'when': 'squad_lost', 'about': 'tango', 'instructions': 'Tell the pilot Tango is gone.'}]}
        self.desks = {'tango': FakeDesk(), 'victor': FakeDesk()}
        self.watch = triggers.ConditionWatch(FakeAgents(self.tango, self.victor), self.desks.get)

    def test_parsing_and_check(self):
        self.assertEqual(triggers.events(self.tango)[1],
                         {'when': 'zone_reached', 'instructions': 'Say you are in Z1.', 'zone': 'Z1'})
        for bad in ({'when': 'sunset', 'instructions': 'x'}, {'when': 'zone_reached', 'instructions': 'x'},
                    {'when': 'casualties', 'about': 'Not An Id', 'instructions': 'x'}):
            with self.assertRaisesRegex(ValueError, 'events.0'):
                triggers.check({'role': 'squad', 'events': [bad]})
        self.assertEqual(triggers.flag_events(self.tango), [])   # never in the hook's flag lists

    def test_first_reading_is_a_baseline_then_changes_fire(self):
        self.desks['tango'].now = facts(alive=3, zones=['z1'])     # already in Z1 and hurt at start: nothing
        self.assertEqual(self.watch.tick(), [])
        self.desks['tango'].now = facts(alive=1, zones=['z1'])
        fired = self.watch.tick()
        self.assertEqual([(a, e['when'], e['cause']) for a, e in fired],
                         [('tango', 'casualties', 'your squad lost 2 soldiers (1 of 4 left)')])
        self.desks['tango'].now = facts(alive=0, lost=True)
        self.assertEqual([(a, e['cause']) for a, e in self.watch.tick()],
                         [('victor', "Tango's squad was wiped out")])

    def test_zone_and_cooldown(self):
        self.desks['tango'].now = facts()
        self.watch.tick()
        self.desks['tango'].now = facts(zones=['z1'])
        self.assertEqual(self.watch.tick()[0][1]['cause'], 'your squad reached zone Z1')
        self.desks['tango'].now = facts()
        self.watch.tick()
        self.desks['tango'].now = facts(zones=['z1'])
        self.assertEqual(self.watch.tick(), [])                     # in and out within a minute: once

    def test_together_in_one_call(self):
        """Live 2026-11-03: Victor's losses and enemies at once made two calls back to back."""
        self.victor['events'] += [{'when': 'casualties', 'instructions': 'Report losses.'},
                                  {'when': 'enemies_seen', 'instructions': 'Report enemies.'}]
        self.desks['victor'].now = facts()
        self.watch.tick()
        self.desks['victor'].now = facts(alive=3, enemies=True)
        fired = self.watch.tick()
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][1]['when'], 'casualties+enemies_seen')
        self.assertEqual(fired[0][1]['instructions'], '- Report losses.\n- Report enemies.')
        self.assertEqual(fired[0][1]['cause'], 'your squad lost 1 soldiers (3 of 4 left); your squad spotted enemies')

    def test_pilot_request_fires_once(self):
        class RequestDesk(FakeDesk):
            def __init__(self):
                super().__init__()
                self.requests = [{'when': 'helicopter_seen', 'request': 'tell me', 'instructions': 'Tell him.'}]

            def requests_due(self):
                return self.requests

            def request_done(self, row):
                self.requests.remove(row)

        self.desks['victor'] = desk = RequestDesk()
        desk.now = facts()
        self.watch.tick()
        desk.now = facts(helicopter=True)
        self.assertEqual([(a, e['instructions']) for a, e in self.watch.tick()], [('victor', 'Tell him.')])
        self.assertEqual(desk.requests, [])

    def test_unknown_facts_keep_the_baseline(self):
        self.desks['tango'].now = facts(alive=4)
        self.watch.tick()
        self.desks['tango'].now = None                              # aboard, or no news
        self.watch.tick()
        self.desks['tango'].now = facts(alive=2)
        self.assertEqual(len(self.watch.tick()), 1)


class FakeBrain(Brain):
    name = 'fake'

    def __init__(self):
        self.asked = []

    def ask(self, question, context, language):
        self.asked.append((question, context))
        return 'Colt 1-1, Buenos, we see you, you are north of us.'

    def remember(self, heard, said):
        pass


class EventCall(unittest.TestCase):
    def test_model_phrases_the_instructions_with_its_situation(self):
        spoken, brain = [], FakeBrain()
        responder = Responder(brain, {}, 'en', CockpitState(), speak=spoken.append, show=lambda _: None,
                              situation=lambda: {'helicopter': {'in_sight': True}}, name='Buenos', role='squad')
        reply = responder.event_call(SQUAD['events'][0])
        self.assertEqual(spoken, [reply])
        question, context = brain.asked[0]
        self.assertEqual(question, EVENT_PROMPT.format(instructions=SQUAD['events'][0]['instructions'], language='en'))
        self.assertEqual(context, {'helicopter': {'in_sight': True}})

    def test_heard_and_obeyed_but_its_answer_does_not_reach_the_pilot(self):
        spoken = []
        responder = Responder(FakeBrain(), {}, 'en', CockpitState(), speak=spoken.append, show=lambda _: None,
                              name='Buenos', role='squad')
        responder.desk = type('Desk', (), {'can_reach_pilot': lambda self: False})()
        self.assertTrue(responder.answer('Buenos, go to Z2'))      # the model answered (and could act)
        self.assertEqual(spoken, [])                                # nothing on the air

    def test_out_of_radio_range_the_call_waits(self):
        from instructor import voice_loop
        spoken, brain = [], FakeBrain()
        responder = Responder(brain, {}, 'en', CockpitState(), speak=spoken.append, show=lambda _: None,
                              name='Buenos', role='squad')
        responder.desk = type('Desk', (), {'reach': False, 'can_reach_pilot': lambda self: self.reach})()
        saved = voice_loop.EVENT_HOLD_POLL_S
        voice_loop.EVENT_HOLD_POLL_S = 0.05
        try:
            self.assertEqual(responder.event_call(SQUAD['events'][0]), '')
            self.assertEqual(spoken, [])                                   # held, nothing said
            responder.desk.reach = True                                    # back in range
            for _ in range(100):
                if spoken:
                    break
                time.sleep(0.02)
            self.assertEqual(len(spoken), 1)
        finally:
            voice_loop.EVENT_HOLD_POLL_S = saved


if __name__ == '__main__':
    unittest.main()
