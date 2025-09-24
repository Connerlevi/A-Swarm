import typer, json
from .agent import emit_health_delta

app = typer.Typer(help='Sentinel agent CLI')

@app.command()
def run(sample: int = 1):
    """Run sentinel and emit N health deltas (stdout JSON)."""
    for _ in range(sample):
        print(json.dumps(emit_health_delta().model_dump()))

if __name__ == '__main__':
    app()
