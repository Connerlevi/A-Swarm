package server

import (
	"testing"
	"time"
)

func TestTokenBucketBasics(t *testing.T) {
	tb := NewTokenBucket(10) // 10 requests per minute

	// Should start with full bucket
	ok, reset, remaining := tb.Allow("cluster1")
	if !ok {
		t.Fatal("First request should be allowed")
	}
	if remaining != 9 {
		t.Fatalf("Expected 9 remaining, got %d", remaining)
	}
	if reset.IsZero() {
		t.Fatal("Reset time should be set")
	}

	// Exhaust the bucket
	for i := 0; i < 9; i++ {
		ok, _, _ = tb.Allow("cluster1")
		if !ok {
			t.Fatalf("Request %d should be allowed", i+2)
		}
	}

	// Should be rate limited now
	ok, _, remaining = tb.Allow("cluster1")
	if ok {
		t.Fatal("Should be rate limited after exhausting bucket")
	}
	if remaining != 0 {
		t.Fatalf("Expected 0 remaining, got %d", remaining)
	}
}

func TestTokenBucketRefill(t *testing.T) {
	tb := NewTokenBucket(5)

	// Mock time
	mockTime := time.Date(2024, 1, 1, 12, 0, 0, 0, time.UTC)
	tb.SetNow(func() time.Time { return mockTime })

	// Exhaust bucket
	for i := 0; i < 5; i++ {
		tb.Allow("cluster1")
	}

	// Should be rate limited
	if ok, _, _ := tb.Allow("cluster1"); ok {
		t.Fatal("Should be rate limited")
	}

	// Advance time by 1 minute
	mockTime = mockTime.Add(time.Minute)
	tb.SetNow(func() time.Time { return mockTime })

	// Should be refilled
	ok, _, remaining := tb.Allow("cluster1")
	if !ok {
		t.Fatal("Should be allowed after refill")
	}
	if remaining != 4 {
		t.Fatalf("Expected 4 remaining after refill, got %d", remaining)
	}
}

func TestTokenBucketPerClusterIsolation(t *testing.T) {
	tb := NewTokenBucket(2)

	// Exhaust cluster1
	tb.Allow("cluster1")
	tb.Allow("cluster1")
	if ok, _, _ := tb.Allow("cluster1"); ok {
		t.Fatal("cluster1 should be rate limited")
	}

	// cluster2 should still work
	if ok, _, _ := tb.Allow("cluster2"); !ok {
		t.Fatal("cluster2 should not be affected by cluster1 rate limit")
	}
}

func TestTokenBucketZeroRPMProtection(t *testing.T) {
	tb := NewTokenBucket(0) // Invalid RPM

	// Should be defaulted to 1
	ok, _, remaining := tb.Allow("cluster1")
	if !ok {
		t.Fatal("Should allow first request even with 0 RPM")
	}
	if remaining != 0 {
		t.Fatalf("Expected 0 remaining with RPM=1, got %d", remaining)
	}

	// Second request should be blocked
	if ok, _, _ := tb.Allow("cluster1"); ok {
		t.Fatal("Second request should be blocked with RPM=1")
	}
}

func TestReplayGuardBasics(t *testing.T) {
	rg := NewReplayGuard(5 * time.Minute)

	unique1 := []byte("unique-token-1")
	unique2 := []byte("unique-token-2")

	// First use should work
	if err := rg.Check("cluster1", 1000, unique1); err != nil {
		t.Fatalf("First use should work: %v", err)
	}

	// Replay should be blocked
	if err := rg.Check("cluster1", 1000, unique1); err == nil {
		t.Fatal("Replay should be blocked")
	}

	// Different unique token should work
	if err := rg.Check("cluster1", 1000, unique2); err != nil {
		t.Fatalf("Different unique token should work: %v", err)
	}
}

func TestReplayGuardMonotonicTimestamp(t *testing.T) {
	rg := NewReplayGuard(5 * time.Minute)

	unique := []byte("test-token")

	// Set initial timestamp
	if err := rg.Check("cluster1", 1000, unique); err != nil {
		t.Fatalf("Initial timestamp should work: %v", err)
	}

	// Older timestamp should be rejected (even with different unique)
	if err := rg.Check("cluster1", 999, []byte("different-token")); err == nil {
		t.Fatal("Older timestamp should be rejected")
	}

	// Equal timestamp with different unique should work
	if err := rg.Check("cluster1", 1000, []byte("another-token")); err != nil {
		t.Fatalf("Equal timestamp with different unique should work: %v", err)
	}

	// Newer timestamp should work
	if err := rg.Check("cluster1", 1001, []byte("newer-token")); err != nil {
		t.Fatalf("Newer timestamp should work: %v", err)
	}
}

func TestReplayGuardEmptyUnique(t *testing.T) {
	rg := NewReplayGuard(5 * time.Minute)

	// Empty unique should be rejected
	if err := rg.Check("cluster1", 1000, []byte{}); err == nil {
		t.Fatal("Empty unique token should be rejected")
	}

	// Nil unique should be rejected
	if err := rg.Check("cluster1", 1000, nil); err == nil {
		t.Fatal("Nil unique token should be rejected")
	}
}

func TestReplayGuardTTLExpiry(t *testing.T) {
	rg := NewReplayGuard(1 * time.Second)

	// Mock time
	mockTime := time.Date(2024, 1, 1, 12, 0, 0, 0, time.UTC)
	rg.SetNow(func() time.Time { return mockTime })

	unique := []byte("test-token")

	// First use
	if err := rg.Check("cluster1", 1000, unique); err != nil {
		t.Fatalf("First use should work: %v", err)
	}

	// Immediate replay should be blocked
	if err := rg.Check("cluster1", 1000, unique); err == nil {
		t.Fatal("Immediate replay should be blocked")
	}

	// Advance time past TTL
	mockTime = mockTime.Add(2 * time.Second)
	rg.SetNow(func() time.Time { return mockTime })

	// Should be allowed again after TTL expiry
	if err := rg.Check("cluster1", 1000, unique); err != nil {
		t.Fatalf("Should be allowed after TTL expiry: %v", err)
	}
}

func TestReplayGuardPerClusterIsolation(t *testing.T) {
	rg := NewReplayGuard(5 * time.Minute)

	unique := []byte("shared-token")

	// Use token in cluster1
	if err := rg.Check("cluster1", 1000, unique); err != nil {
		t.Fatalf("cluster1 first use should work: %v", err)
	}

	// Same token should work in cluster2
	if err := rg.Check("cluster2", 1000, unique); err != nil {
		t.Fatalf("Same token should work in different cluster: %v", err)
	}

	// Replay in cluster1 should still be blocked
	if err := rg.Check("cluster1", 1000, unique); err == nil {
		t.Fatal("Replay in cluster1 should still be blocked")
	}
}