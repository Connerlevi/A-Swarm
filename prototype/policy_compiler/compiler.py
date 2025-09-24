import yaml, typer, json
from pathlib import Path

app = typer.Typer(help='Policy Compiler')

def compile_yaml(yaml_path: Path) -> dict:
    data = yaml.safe_load(yaml_path.read_text())
    cmds = []
    for p in data.get('policies', []):
        if p.get('ring') != 1:
            continue
        if p['action'] == 'iptables_rate_limit':
            cmds.append({'kind': 'iptables', 'cmd': f"tc qdisc add dev {p['params']['iface']} root tbf rate {p['params']['rate']} burst 32kbit latency 400ms"})
        elif p['action'] == 'switch_vlan_isolate':
            cmds.append({'kind': 'switch', 'cmd': f"isolate-to-vlan {p['params']['vlan_id']}"})
        elif p['action'] == 'revoke_service_token':
            cmds.append({'kind': 'idp', 'cmd': f"revoke-token {p['params']['principal']}"})
    return {'commands': cmds, 'revert_ttl': max([p.get('params',{}).get('ttl_seconds',60) for p in data.get('policies',[])], default=60)}

@app.command()
def compile(policy_yaml: str = 'configs/policy_catalog.example.yaml'):
    res = compile_yaml(Path(policy_yaml))
    print(json.dumps(res, indent=2))

if __name__ == '__main__':
    app()
