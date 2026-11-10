"""Changing an agent's frequency by radio (user, 2026-11-05: "Tango, change to 353" to join the helicopters'
frequency): a tool for every agent but the ATC, added on top of its role's toolbox.

The agent answers on its old frequency first ("Tango, changing to 353"), then moves: the change waits until its answer
has been handed to the voice (apply(), called by the voice loop after each transmission), and is saved in agent.json
like a change made in the GUI, so the next press of the pilot's radio reaches it on the new frequency.
"""
import json

from instructor.tools import parameters
from shared.agents import FREQUENCY_RANGE_MHZ, MODULATIONS

NAMESPACE = 'radio'
NAMESPACE_DESCRIPTION = 'Your own radio.'
DEFINITION = {
    'type': 'function', 'name': 'change_frequency',
    'description': 'Move to another radio frequency when the pilot tells you to ("Tango, change to 353", "go to the '
                   'helicopters\' frequency, 251.5"). mhz: the frequency in MHz; modulation: AM, or FM if the pilot '
                   'says so. Your answer still goes out on your old frequency (confirm it, e.g. "Tango, changing to '
                   '353"); from then on you listen and talk on the new one. Returns ok and what was done.',
    'parameters': parameters({'mhz': {'type': 'number'}, 'modulation': {'type': 'string', 'enum': ['AM', 'FM']}},
                             ('mhz', 'modulation')),
}


class RadioToolBox:
    """The role's toolbox (base) plus change_frequency. Same interface as the role toolboxes."""

    def __init__(self, base, agents, agent_id: str):
        self.base, self.agents, self.agent_id = base, agents, agent_id
        self.pending: tuple[float, str] | None = None   # (MHz, modulation) applied after the next transmission

    def __getattr__(self, name):   # the base's own attributes (playbook, desk...)
        return getattr(self.base, name)

    @property
    def remembered(self) -> tuple:
        return tuple(getattr(self.base, 'remembered', ())) + (DEFINITION['name'],)

    def tools(self) -> list[dict]:
        tools = list(self.base.tools()) if self.base else []
        return tools + [{'type': 'namespace', 'name': NAMESPACE, 'description': NAMESPACE_DESCRIPTION,
                         'tools': [DEFINITION]}]

    def guide(self) -> str:
        lines = [self.base.guide()] if self.base else []
        return '\n'.join(line for line in lines + [f'- change_frequency: {DEFINITION["description"]}'] if line)

    def call(self, name: str, arguments: str) -> str:
        prefix, _, leaf = name.rpartition('.')
        if leaf == DEFINITION['name'] and prefix in ('', NAMESPACE):
            try:
                return json.dumps(self.change_frequency(**json.loads(arguments or '{}')), ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                return json.dumps({'error': f'{type(exc).__name__}: {exc}'})
        if self.base:
            return self.base.call(name, arguments)
        return json.dumps({'error': f'unknown tool {name}'})

    def change_frequency(self, mhz: float, modulation: str = 'AM') -> dict:
        low, high = FREQUENCY_RANGE_MHZ
        mhz, modulation = round(float(mhz), 3), str(modulation or 'AM').upper()
        if not low <= mhz <= high:
            return {'ok': False, 'detail': f'{mhz} MHz is outside the radio band ({low} to {high} MHz)'}
        if modulation not in MODULATIONS:
            return {'ok': False, 'detail': f'modulation must be one of {", ".join(MODULATIONS)}'}
        agent = self.agents.get(self.agent_id) or {}
        if abs(float(agent.get('frequency_mhz') or 0) - mhz) < 0.0005 and agent.get('modulation') == modulation:
            return {'ok': True, 'detail': f'you are already on {mhz:.3f} {modulation}'}
        self.pending = (mhz, modulation)
        return {'ok': True, 'detail': f'you move to {mhz:.3f} {modulation} right after this answer, which still goes '
                                      'out on your old frequency'}

    def apply(self) -> dict | None:
        """The pending change, once the answer is on its way: saved like a change in the GUI; the agent, or None."""
        if not self.pending:
            return None
        mhz, modulation = self.pending
        self.pending = None
        return self.agents.update(self.agent_id, {'frequency_mhz': mhz, 'modulation': modulation})
