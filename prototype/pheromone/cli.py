import typer
from .gossip import GossipConfig, rate_limited
from .quorum import elevate

app = typer.Typer(help='Pheromone mesh CLI')

@app.command()
def metrics(events: int = 10):
    cfg = GossipConfig()
    print({'rate_ok': rate_limited(events, cfg)})

@app.command()
def quorum(score: float = 0.5, threshold: float = 0.8):
    print({'elevated': elevate(score, threshold)})

if __name__ == '__main__':
    app()
