from policy_compiler.compiler import compile_yaml
from pathlib import Path
import yaml, tempfile

def test_compile_commands():
    y = {
        'policies': [
            {'id':'r','ring':1,'action':'iptables_rate_limit','params':{'iface':'eth0','rate':'1mbit','ttl_seconds':60}},
            {'id':'q','ring':1,'action':'switch_vlan_isolate','params':{'vlan_id':3999,'ttl_seconds':120}},
        ]
    }
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)/'p.yaml'
        p.write_text(yaml.safe_dump(y))
        res = compile_yaml(p)
        assert len(res['commands']) == 2
        assert res['revert_ttl'] == 120
