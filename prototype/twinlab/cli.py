import typer, json
from .replay import deterministic_replay
from .action_certificate import make_certificate

app = typer.Typer(help='TwinLab CLI')

@app.command()
def replay(*fixtures: str):
    res = deterministic_replay(list(fixtures))
    print(json.dumps(res))

@app.command()
def cert(site_id: str = 'example-dc-01', asset_id: str = 'host-01', policy_id: str = 'rate-limit-egress'):
    c = make_certificate(site_id, asset_id, policy_id, 'iptables_rate_limit')
    print(c.model_dump_json())

if __name__ == '__main__':
    app()
