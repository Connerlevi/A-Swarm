from twinlab.replay import deterministic_replay

def test_replay_order():
    fixtures = ['a','b','c']
    out = deterministic_replay(fixtures)
    assert all(out[k] == 'replayed' for k in fixtures)
