package server

import (
	"errors"
	"sync"
	"time"
)

// Token-bucket RL per cluster.
type RateLimiter interface {
	Allow(clusterID string) (ok bool, reset time.Time, remaining int)
}

type tokenBucket struct {
	mu   sync.Mutex
	rpm  int // tokens per minute; must be >0
	now  func() time.Time
	bkts map[string]bucket
}

type bucket struct {
	tokens int
	reset  time.Time
}

func NewTokenBucket(rpm int) *tokenBucket {
	if rpm <= 0 {
		rpm = 1
	}
	return &tokenBucket{
		rpm:  rpm,
		now:  time.Now,
		bkts: map[string]bucket{},
	}
}

func (t *tokenBucket) Allow(id string) (bool, time.Time, int) {
	t.mu.Lock()
	defer t.mu.Unlock()

	b := t.bkts[id]
	now := t.now()

	if now.After(b.reset) {
		b.tokens = t.rpm
		b.reset = now.Add(time.Minute)
	}

	if b.tokens <= 0 {
		t.bkts[id] = b
		return false, b.reset, 0
	}

	b.tokens--
	t.bkts[id] = b
	if b.tokens < 0 { // shouldn't happen, but don't return negative
		b.tokens = 0
	}
	return true, b.reset, b.tokens
}

// Test helpers (optional)
func (t *tokenBucket) SetNow(fn func() time.Time) { // for unit tests / simulation
	t.mu.Lock()
	defer t.mu.Unlock()
	if fn != nil {
		t.now = fn
	}
}

// ReplayGuard with (cluster, lastTimestamp, LRU of recent hashes)
type ReplayGuard interface {
	Check(cluster string, ts uint64, unique []byte) error
}

type simpleReplay struct {
	mu        sync.Mutex
	watermark map[string]uint64            // last seen timestamp
	seen      map[string]map[string]int64  // unique hash -> expiresAt
	ttl       time.Duration
	now       func() time.Time
}

func NewReplayGuard(ttl time.Duration) *simpleReplay {
	return &simpleReplay{
		watermark: map[string]uint64{},
		seen:      map[string]map[string]int64{},
		ttl:       ttl,
		now:       time.Now,
	}
}

var ErrReplay = errors.New("replay detected")

func (r *simpleReplay) Check(cluster string, ts uint64, uniq []byte) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	now := r.now()

	// monotonic timestamp (equal allowed; strictly older rejected)
	if ts < r.watermark[cluster] {
		return ErrReplay
	}
	if ts > r.watermark[cluster] {
		r.watermark[cluster] = ts
	}

	if r.seen[cluster] == nil {
		r.seen[cluster] = map[string]int64{}
	}

	if len(uniq) == 0 {
		return ErrReplay // callers must supply a non-empty uniqueness token
	}
	key := string(uniq)
	if exp, ok := r.seen[cluster][key]; ok && now.Unix() <= exp {
		return ErrReplay
	}

	r.seen[cluster][key] = now.Add(r.ttl).Unix()
	// opportunistic GC (cheap): drop a few expired entries on writes
	gcChecked := 0
	for k, exp := range r.seen[cluster] {
		if exp < now.Unix() {
			delete(r.seen[cluster], k)
		}
		gcChecked++
		if gcChecked > 64 { // cap work per call
			break
		}
	}
	return nil
}

// Test helper
func (r *simpleReplay) SetNow(fn func() time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if fn != nil {
		r.now = fn
	}
}