import json
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / 'config' / 'agents.json'


def load_agents(path=DEFAULT_CONFIG):
    data = json.loads(Path(path).read_text())
    agents = data['agents']
    expected = {f'AI{i:02}' for i in range(1, 29)}
    if data['schema_version'] != 1 or len(agents) != 28 or {a['id'] for a in agents} != expected:
        raise ValueError('Exactly AI01 through AI28 are required')
    for agent in agents:
        for field in ('role', 'inputs', 'outputs', 'tools', 'permissions', 'reviewer', 'reports_to'):
            if not agent.get(field):
                raise ValueError(f'Missing {field}')
        if agent['reviewer'] not in expected - {agent['id']}:
            raise ValueError('Independent reviewer required')
        if agent['reports_to'] not in expected | {'representative'}:
            raise ValueError('Unknown supervisor')
        if agent['enabled'] is not False or agent['permissions'] != dict(external_send=False, approve=False, production_access=False, spend=False):
            raise ValueError('Phase one agents must remain disabled and without external authority')
    registry={a['id']:a for a in agents}
    for agent in agents:
        if not isinstance(agent['tools'],list) or not set(agent['tools']) <= {'artifact.read','artifact.write','task.propose','review.record'}:
            raise ValueError('Unknown tool')
        visited=set()
        supervisor=agent['id']
        while supervisor!='representative':
            if supervisor in visited:
                raise ValueError('Supervisor cycle detected')
            visited.add(supervisor)
            supervisor=registry[supervisor]['reports_to']
    return registry
