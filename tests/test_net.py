"""Offline tests for the internal net (shared/net.py): agents send each other text messages; the recipient answers
back on the net, and a reply may become a call to the pilot (instructor/voice_loop.py Responder.net_call).

Run: python -m unittest discover tests
"""
import json
import unittest

import quiet_log  # noqa: F401  (the program's log goes to a temporary file)
from instructor.brain import Brain
from instructor.voice_loop import Responder
from shared import net as net_module
from shared.cockpit import CockpitState
from shared.net import MAX_HOPS, Net, NetToolBox

AXE = {'id': 'axe', 'name': 'Axe', 'role': 'vehicles', 'active': True}
ARCHER = {'id': 'archer', 'name': 'Archer', 'role': 'artillery', 'active': True, 'aliases': ['Steel Rain']}
TANGO = {'id': 'buenos', 'name': 'Tango', 'role': 'squad', 'active': True}
OFF = {'id': 'victor', 'name': 'Victor', 'role': 'squad', 'active': False}
ATC = {'id': 'atc', 'name': 'ATC', 'role': 'atc', 'active': True}


class FakeAgents:
    def __init__(self, *agents):
        self.agents = {a['id']: a for a in agents}

    def active(self):
        return [a for a in self.agents.values() if a.get('active')]

    def get(self, agent_id):
        return self.agents.get(agent_id)


def make_net():
    delivered, shown = [], []
    net = Net(FakeAgents(AXE, ARCHER, TANGO, OFF, ATC), deliver=lambda to, event: delivered.append((to, event)),
              on_message=lambda *line: shown.append(line))
    return net, delivered, shown


class Sending(unittest.TestCase):
    def test_a_message_reaches_the_agent_named(self):
        net, delivered, shown = make_net()
        result = net.send('axe', 'Archer', 'Fire mission: enemy armour 3 km north of me.')
        self.assertTrue(result['ok'])
        self.assertEqual(delivered, [('archer', {'net': 'message', 'from': 'axe', 'from_name': 'Axe',
                                                 'text': 'Fire mission: enemy armour 3 km north of me.', 'hops': 0})])
        self.assertEqual(shown, [('Axe', 'Archer', 'Fire mission: enemy armour 3 km north of me.', 'message')])

    def test_names_are_matched_like_on_the_radio(self):
        net, _, _ = make_net()
        self.assertEqual(net.find('archer')['id'], 'archer')
        self.assertEqual(net.find('Archr')['id'], 'archer')       # a little off
        self.assertEqual(net.find('steel rain')['id'], 'archer')  # an alias
        self.assertIsNone(net.find('Axe', exclude='axe'))         # not to itself

    def test_off_agents_and_the_atc_are_not_on_the_net(self):
        net, delivered, _ = make_net()
        for name in ('Victor', 'ATC'):
            result = net.send('axe', name, 'hello')
            self.assertFalse(result['ok'])
            self.assertIn('Archer', result['detail'])   # it says who is on the net
        self.assertFalse(net.send('atc', 'Axe', 'hello')['ok'])
        self.assertEqual(delivered, [])

    def test_empty_messages_are_refused(self):
        net, delivered, _ = make_net()
        self.assertFalse(net.send('axe', 'Archer', '   ')['ok'])
        self.assertEqual(delivered, [])

    def test_endless_back_and_forth_is_cut(self):
        net, delivered, _ = make_net()
        self.assertTrue(net.send('axe', 'Archer', 'again', hops=MAX_HOPS)['ok'])
        self.assertFalse(net.send('archer', 'Axe', 'again', hops=MAX_HOPS + 1)['ok'])
        self.assertEqual(len(delivered), 1)

    def test_a_sender_is_limited_per_minute(self):
        net, delivered, _ = make_net()
        results = [net.send('axe', 'Tango', f'message {n}')['ok'] for n in range(net_module.PER_MINUTE + 1)]
        self.assertEqual(results, [True] * net_module.PER_MINUTE + [False])
        self.assertTrue(net.send('archer', 'Tango', 'others still can')['ok'])


class Base:
    """A role toolbox with one tool of its own."""
    remembered = ('move_to',)

    def tools(self):
        return [{'type': 'function', 'name': 'move_to'}]

    def guide(self):
        return '- move_to: drive somewhere'

    def call(self, name, arguments):
        return json.dumps({'ok': True, 'detail': f'base {name}'})


class Tools(unittest.TestCase):
    def test_the_tool_lists_who_is_on_the_net(self):
        net, _, _ = make_net()
        box = NetToolBox(Base(), net, 'axe')
        namespace = box.tools()[-1]
        self.assertEqual(namespace['name'], 'net')
        description = namespace['tools'][0]['description']
        self.assertIn('Archer (artillery battery)', description)
        self.assertIn('Tango (infantry squad)', description)
        self.assertNotIn('Axe (', description)
        self.assertNotIn('Victor', description)
        self.assertIn('send_message', box.guide())
        self.assertEqual(box.remembered, ('move_to', 'send_message', 'call_for_fire',
                                          'request_air_support'))   # kept like an order

    def test_calls_go_to_the_net_or_the_base(self):
        net, delivered, _ = make_net()
        box = NetToolBox(Base(), net, 'axe')
        box.hops = 3
        result = json.loads(box.call('net.send_message', json.dumps({'to': 'Archer', 'text': 'Ready?'})))
        self.assertTrue(result['ok'])
        self.assertEqual(delivered[0][1]['hops'], 3)
        self.assertEqual(json.loads(box.call('vehicles.move_to', '{}'))['detail'], 'base vehicles.move_to')


class ScriptedBrain(Brain):
    """Answers in turn; may call a tool through a toolbox while answering (as the real brain does)."""
    name = 'fake'

    def __init__(self, *answers, toolbox=None, tool_call=None):
        self.answers, self.toolbox, self.tool_call, self.asked = list(answers), toolbox, tool_call, []

    def ask(self, question, context, language):
        self.asked.append(question)
        if self.toolbox and self.tool_call:
            self.toolbox.call(*self.tool_call)
        return self.answers.pop(0)


def responder(brain, agent_id, name, net, net_box=None):
    spoken = []
    r = Responder(brain, {}, 'es', CockpitState(), speak=spoken.append, show=lambda _: None, name=name)
    r.agent_id, r.net, r.net_box = agent_id, net, net_box
    return r, spoken


class Answers(unittest.TestCase):
    def test_a_message_is_answered_on_the_net_not_on_the_radio(self):
        net, delivered, _ = make_net()
        net.send('axe', 'Archer', 'Fire on the tanks.')
        archer, spoken = responder(ScriptedBrain('Shot, splash in 40 seconds.'), 'archer', 'Archer', net)
        archer.net_call(delivered.pop()[1])
        self.assertEqual(spoken, [])
        self.assertIn('Axe: Fire on the tanks.', archer.brain.asked[0])
        self.assertEqual(delivered, [('axe', {'net': 'reply', 'from': 'archer', 'from_name': 'Archer',
                                              'text': 'Shot, splash in 40 seconds.', 'hops': 1})])

    def test_an_answer_sent_with_the_tool_is_still_a_reply(self):
        """Live 2026-11-07: Tango answered Bison with send_message; Bison took it as a new message and its call to
        the pilot went back to Tango on the net."""
        net, delivered, _ = make_net()
        net.send('axe', 'Tango', 'Where are you?')
        net.send('buenos', 'Axe', 'In the ditch by the junction.')
        net.send('buenos', 'Axe', 'And three enemies north.')   # the question was answered: this one is new
        self.assertEqual([event['net'] for _, event in delivered], ['message', 'reply', 'message'])

    def test_a_reply_may_become_a_call_to_the_pilot(self):
        net, delivered, _ = make_net()
        axe, spoken = responder(ScriptedBrain('Colt, artillery on the way, 40 seconds.'), 'axe', 'Axe', net)
        axe.net_call({'net': 'reply', 'from': 'archer', 'from_name': 'Archer', 'text': 'Shot, splash 40 s.',
                      'hops': 1})
        self.assertEqual(spoken, ['Colt, artillery on the way, 40 seconds.'])
        self.assertIn('"es"', axe.brain.asked[0])   # the pilot's language for the radio
        self.assertEqual(delivered, [])

    def test_silent_sends_and_says_nothing(self):
        net, delivered, _ = make_net()
        for kind in ('message', 'reply'):
            agent, spoken = responder(ScriptedBrain('SILENT'), 'tango', 'Tango', net)
            agent.net_call({'net': kind, 'from': 'axe', 'from_name': 'Axe', 'text': 'FYI, moving north.', 'hops': 0})
            self.assertEqual(spoken, [])
        self.assertEqual(delivered, [])

    def test_a_killed_agent_answers_nothing(self):
        net, delivered, _ = make_net()
        tango, spoken = responder(ScriptedBrain('We are here.'), 'buenos', 'Tango', net)
        tango.desk = type('Desk', (), {'silent': lambda self, event=None: True})()
        tango.net_call({'net': 'message', 'from': 'axe', 'from_name': 'Axe', 'text': 'Where are you?', 'hops': 0})
        self.assertEqual((spoken, delivered, tango.brain.asked), ([], [], []))

    def test_what_it_sends_while_answering_counts_one_hop_more(self):
        net, delivered, _ = make_net()
        box = NetToolBox(Base(), net, 'archer')
        brain = ScriptedBrain('Asked Tango.', toolbox=box,
                              tool_call=('net.send_message', json.dumps({'to': 'Tango', 'text': 'Keep clear.'})))
        archer, _ = responder(brain, 'archer', 'Archer', net, box)
        net.send('axe', 'Archer', 'Fire near Tango.', hops=2)
        archer.net_call(delivered.pop()[1])
        self.assertEqual([(to, event['net'], event['hops']) for to, event in delivered],
                         [('buenos', 'message', 3), ('axe', 'reply', 3)])
        self.assertEqual(box.hops, 0)   # back to a fresh exchange afterwards


if __name__ == '__main__':
    unittest.main()
