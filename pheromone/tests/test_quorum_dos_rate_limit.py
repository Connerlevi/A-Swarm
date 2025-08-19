from pheromone.gossip import GossipConfig, rate_limited
from pheromone.quorum import elevate

def test_rate_limit():
    assert rate_limited(10, GossipConfig(rate_limit_eps=50))
    assert not rate_limited(100, GossipConfig(rate_limit_eps=50))

def test_quorum():
    assert elevate(0.85, 0.8)
    assert not elevate(0.5, 0.8)
