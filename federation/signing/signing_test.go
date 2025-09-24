package signing

import (
	"crypto/ed25519"
	"crypto/rand"
	"testing"
	"time"

	fpb "github.com/Connerlevi/A-Swarm/federation/pb"
)

// Mock keyring for testing
type testKeyring struct {
	hmacKeys map[string][]byte
	ed25519Keys map[string]ed25519.PublicKey
}

func (k *testKeyring) HMACKey(clusterID string) []byte {
	return k.hmacKeys[clusterID]
}

func (k *testKeyring) Ed25519Pub(clusterID string) []byte {
	if pub, ok := k.ed25519Keys[clusterID]; ok {
		return pub
	}
	return nil
}

func newTestKeyring() (*testKeyring, ed25519.PrivateKey) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	hmacKey := make([]byte, 32)
	rand.Read(hmacKey)

	return &testKeyring{
		hmacKeys: map[string][]byte{"test-cluster": hmacKey},
		ed25519Keys: map[string]ed25519.PublicKey{"test-cluster": pub},
	}, priv
}

func TestEd25519SignVerifyRoundTrip(t *testing.T) {
	keys, priv := newTestKeyring()

	// Test ShareSketch
	req := &fpb.ShareSketchRequest{
		ClusterId: "test-cluster",
		Attestation: &fpb.SketchAttestation{
			Metadata: &fpb.SketchMetadata{
				AntibodyId: "test-ab",
				SketchHash: []byte("test-hash-32-bytes-long-value"),
			},
			SketchData: []byte("test-sketch-data"),
		},
		TimestampUnix: uint64(time.Now().Unix()),
		SequenceNumber: 1,
	}

	if err := SignShareSketchEd25519(priv, req); err != nil {
		t.Fatalf("SignShareSketchEd25519: %v", err)
	}

	if err := VerifyShareSketch(keys, req); err != nil {
		t.Fatalf("VerifyShareSketch: %v", err)
	}
}

func TestHMACSignVerifyRoundTrip(t *testing.T) {
	keys, _ := newTestKeyring()
	hmacKey := keys.HMACKey("test-cluster")

	req := &fpb.HealthReportRequest{
		ClusterId: "test-cluster",
		Capabilities: &fpb.ClusterCapabilities{
			ProtocolVersion: "v4",
		},
		TimestampUnix: uint64(time.Now().Unix()),
		SequenceNumber: 1,
	}

	if err := SignHealthHMAC(hmacKey, req); err != nil {
		t.Fatalf("SignHealthHMAC: %v", err)
	}

	if err := VerifyHealth(keys, req); err != nil {
		t.Fatalf("VerifyHealth: %v", err)
	}
}

func TestUniqueKeyStability(t *testing.T) {
	req := &fpb.ShareSketchRequest{
		ClusterId: "test-cluster",
		Attestation: &fpb.SketchAttestation{
			Metadata: &fpb.SketchMetadata{
				SketchHash: []byte("test-hash-32-bytes-long-value"),
			},
		},
		TimestampUnix: 1640995200,
		SequenceNumber: 123,
	}

	key1 := UniqueKeyForShare(req)
	key2 := UniqueKeyForShare(req)

	if len(key1) != 24 {
		t.Fatalf("Expected 24-byte key, got %d", len(key1))
	}

	if string(key1) != string(key2) {
		t.Fatalf("Unique keys should be stable across calls")
	}
}

func TestSignViewMutation(t *testing.T) {
	keys, priv := newTestKeyring()

	req := &fpb.ShareSketchRequest{
		ClusterId: "test-cluster",
		Attestation: &fpb.SketchAttestation{
			Metadata: &fpb.SketchMetadata{
				AntibodyId: "test-ab",
				SketchHash: []byte("test-hash"),
			},
		},
		TimestampUnix: uint64(time.Now().Unix()),
		SequenceNumber: 1,
	}

	// Sign original
	if err := SignShareSketchEd25519(priv, req); err != nil {
		t.Fatalf("SignShareSketchEd25519: %v", err)
	}

	// Verify works
	if err := VerifyShareSketch(keys, req); err != nil {
		t.Fatalf("VerifyShareSketch should succeed: %v", err)
	}

	// Mutate sign-view field
	req.SequenceNumber = 999

	// Verify should fail
	if err := VerifyShareSketch(keys, req); err == nil {
		t.Fatalf("VerifyShareSketch should fail after mutation")
	}
}

func TestNilSafetyInUniqueKey(t *testing.T) {
	// Test with nil attestation
	req := &fpb.ShareSketchRequest{
		SequenceNumber: 123,
		TimestampUnix: 1640995200,
		Attestation: nil,
	}

	key := UniqueKeyForShare(req)
	if len(key) != 16 { // seq(8) + ts(8) + empty hash
		t.Fatalf("Expected 16-byte key for nil attestation, got %d", len(key))
	}

	// Test with nil metadata
	req.Attestation = &fpb.SketchAttestation{Metadata: nil}
	key = UniqueKeyForShare(req)
	if len(key) != 16 {
		t.Fatalf("Expected 16-byte key for nil metadata, got %d", len(key))
	}
}