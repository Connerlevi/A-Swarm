package hllcodec

import (
	"bytes"
	"testing"
	"time"

	"github.com/a-swarm/prototype/hll"
	fpb "github.com/Connerlevi/A-Swarm/federation/pb"
)

func TestPackUnpackRoundTrip(t *testing.T) {
	cfg := hll.DefaultHLLConfig()
	cfg.Salt = 0xDEADBEEF
	sketch, err := hll.NewDense(cfg)
	if err != nil {
		t.Fatalf("NewDense: %v", err)
	}

	// Add some data
	for i := 0; i < 100; i++ {
		sketch.AddHash64(uint64(i))
	}

	windowStart := time.Date(2024, 1, 1, 12, 0, 0, 0, time.UTC)
	windowSize := 5 * time.Minute

	// Pack
	att, err := PackSketch(
		"test-antibody",
		"production",
		fpb.SignatureType_SIGNATURE_TYPE_NETWORK,
		windowStart,
		windowSize,
		sketch,
		fpb.AntibodyPhase_PHASE_SHADOW,
	)
	if err != nil {
		t.Fatalf("PackSketch: %v", err)
	}

	// Verify metadata
	if att.Metadata.AntibodyId != "test-antibody" {
		t.Fatalf("AntibodyId mismatch")
	}
	if att.Metadata.Environment != "production" {
		t.Fatalf("Environment mismatch")
	}
	if att.Metadata.WindowStartUnix != uint64(windowStart.Unix()) {
		t.Fatalf("WindowStartUnix mismatch")
	}
	if att.Metadata.WindowSizeSeconds != 300 {
		t.Fatalf("WindowSizeSeconds mismatch: got %d", att.Metadata.WindowSizeSeconds)
	}

	// Unpack
	unpacked, err := UnpackSketch(att, cfg)
	if err != nil {
		t.Fatalf("UnpackSketch: %v", err)
	}

	// Verify counts match
	if unpacked.Count() != sketch.Count() {
		t.Fatalf("Count mismatch: got %d, want %d", unpacked.Count(), sketch.Count())
	}
}

func TestInvalidMetadata(t *testing.T) {
	cfg := hll.DefaultHLLConfig()
	sketch, _ := hll.NewDense(cfg)

	// Empty antibody ID
	_, err := PackSketch("", "prod", fpb.SignatureType_SIGNATURE_TYPE_IOC_HASH, time.Now(), time.Minute, sketch, fpb.AntibodyPhase_PHASE_ACTIVE)
	if err != ErrInvalidMetadata {
		t.Fatalf("Expected ErrInvalidMetadata for empty antibody ID, got %v", err)
	}

	// Empty environment
	_, err = PackSketch("test", "", fpb.SignatureType_SIGNATURE_TYPE_IOC_HASH, time.Now(), time.Minute, sketch, fpb.AntibodyPhase_PHASE_ACTIVE)
	if err != ErrInvalidMetadata {
		t.Fatalf("Expected ErrInvalidMetadata for empty environment, got %v", err)
	}

	// Negative window size
	_, err = PackSketch("test", "prod", fpb.SignatureType_SIGNATURE_TYPE_IOC_HASH, time.Now(), -time.Minute, sketch, fpb.AntibodyPhase_PHASE_ACTIVE)
	if err != ErrInvalidMetadata {
		t.Fatalf("Expected ErrInvalidMetadata for negative window size, got %v", err)
	}
}

func TestHashIntegrity(t *testing.T) {
	cfg := hll.DefaultHLLConfig()
	sketch, _ := hll.NewDense(cfg)
	sketch.AddString("test-data")

	att, err := PackSketch("test", "prod", fpb.SignatureType_SIGNATURE_TYPE_BEHAVIORAL, time.Now(), time.Hour, sketch, fpb.AntibodyPhase_PHASE_STAGED)
	if err != nil {
		t.Fatalf("PackSketch: %v", err)
	}

	// Corrupt the sketch data
	att.SketchData[10] ^= 0xFF

	// Should fail hash verification
	_, err = UnpackSketch(att, cfg)
	if err != ErrCorruptSketch {
		t.Fatalf("Expected ErrCorruptSketch, got %v", err)
	}
}

func TestIncompatibleConfig(t *testing.T) {
	cfg1 := hll.DefaultHLLConfig()
	cfg1.Salt = 0xAAAA
	sketch, _ := hll.NewDense(cfg1)
	sketch.AddString("test-data")

	att, err := PackSketch("test", "prod", fpb.SignatureType_SIGNATURE_TYPE_PROCESS, time.Now(), time.Hour, sketch, fpb.AntibodyPhase_PHASE_CANARY)
	if err != nil {
		t.Fatalf("PackSketch: %v", err)
	}

	// Try to unpack with different config
	cfg2 := hll.DefaultHLLConfig()
	cfg2.Salt = 0xBBBB // Different salt

	_, err = UnpackSketch(att, cfg2)
	if err != ErrIncompatibleHLL {
		t.Fatalf("Expected ErrIncompatibleHLL, got %v", err)
	}

	// Validate compatibility should also fail
	if err := ValidateCompatibility(att, cfg2); err != ErrIncompatibleHLL {
		t.Fatalf("Expected ErrIncompatibleHLL from ValidateCompatibility, got %v", err)
	}
}

func TestCreateSketchKey(t *testing.T) {
	metadata := &fpb.SketchMetadata{
		AntibodyId:        "test-ab",
		Environment:       "staging",
		WindowStartUnix:   1704110400, // 2024-01-01 12:00:00 UTC
		WindowSizeSeconds: 600,        // 10 minutes
		SignatureType:     fpb.SignatureType_SIGNATURE_TYPE_NETWORK,
	}

	key := CreateSketchKey(metadata)

	if key.AntibodyID != "test-ab" {
		t.Fatalf("AntibodyID mismatch")
	}
	if key.Environment != "staging" {
		t.Fatalf("Environment mismatch")
	}
	if key.WindowStart.Unix() != 1704110400 {
		t.Fatalf("WindowStart mismatch: got %d", key.WindowStart.Unix())
	}
	if key.WindowSize != 10*time.Minute {
		t.Fatalf("WindowSize mismatch: got %v", key.WindowSize)
	}
	if key.SignatureType != "network" {
		t.Fatalf("SignatureType mismatch: got %s", key.SignatureType)
	}
}

func TestSignatureTypeConversion(t *testing.T) {
	tests := []struct {
		proto  fpb.SignatureType
		str    string
	}{
		{fpb.SignatureType_SIGNATURE_TYPE_IOC_HASH, "ioc_hash"},
		{fpb.SignatureType_SIGNATURE_TYPE_BEHAVIORAL, "behavioral"},
		{fpb.SignatureType_SIGNATURE_TYPE_NETWORK, "network"},
		{fpb.SignatureType_SIGNATURE_TYPE_PROCESS, "process"},
		{fpb.SignatureType_SIGNATURE_TYPE_UNKNOWN, "unknown"},
	}

	for _, test := range tests {
		// Proto to string
		if got := convertSignatureType(test.proto); got != test.str {
			t.Errorf("convertSignatureType(%v) = %s, want %s", test.proto, got, test.str)
		}

		// String to proto
		if got := ConvertToProtoSignatureType(test.str); got != test.proto {
			t.Errorf("ConvertToProtoSignatureType(%s) = %v, want %v", test.str, got, test.proto)
		}
	}
}

func TestValidateHeader(t *testing.T) {
	cfg := hll.DefaultHLLConfig()
	cfg.Salt = 0xCAFEBABE
	cfg.Precision = 14

	sketch, _ := hll.NewDense(cfg)
	data, _ := sketch.MarshalBinary()

	// Should validate successfully
	if err := validateHeader(data, cfg); err != nil {
		t.Fatalf("validateHeader failed: %v", err)
	}

	// Test with wrong salt
	cfg2 := cfg
	cfg2.Salt = 0xDEADBEEF
	if err := validateHeader(data, cfg2); err != ErrIncompatibleHLL {
		t.Fatalf("Expected ErrIncompatibleHLL for wrong salt, got %v", err)
	}

	// Test with wrong precision
	cfg3 := cfg
	cfg3.Precision = 12
	if err := validateHeader(data, cfg3); err != ErrIncompatibleHLL {
		t.Fatalf("Expected ErrIncompatibleHLL for wrong precision, got %v", err)
	}

	// Test with truncated data
	if err := validateHeader(data[:10], cfg); err != ErrCorruptSketch {
		t.Fatalf("Expected ErrCorruptSketch for truncated data, got %v", err)
	}
}

func TestUTCNormalization(t *testing.T) {
	cfg := hll.DefaultHLLConfig()
	sketch, _ := hll.NewDense(cfg)

	// Create time in non-UTC timezone
	loc, _ := time.LoadLocation("America/New_York")
	windowStart := time.Date(2024, 1, 1, 12, 0, 0, 0, loc)

	att, err := PackSketch("test", "prod", fpb.SignatureType_SIGNATURE_TYPE_IOC_HASH, windowStart, time.Hour, sketch, fpb.AntibodyPhase_PHASE_ACTIVE)
	if err != nil {
		t.Fatalf("PackSketch: %v", err)
	}

	// Should be stored as UTC
	expectedUTC := windowStart.UTC().Unix()
	if att.Metadata.WindowStartUnix != uint64(expectedUTC) {
		t.Fatalf("WindowStartUnix not normalized to UTC: got %d, want %d", att.Metadata.WindowStartUnix, expectedUTC)
	}

	// CreateSketchKey should also return UTC
	key := CreateSketchKey(att.Metadata)
	if key.WindowStart.Location() != time.UTC {
		t.Fatalf("CreateSketchKey didn't return UTC time")
	}
}

func TestNilAttestation(t *testing.T) {
	cfg := hll.DefaultHLLConfig()

	// Nil attestation
	_, err := UnpackSketch(nil, cfg)
	if err != ErrInvalidMetadata {
		t.Fatalf("Expected ErrInvalidMetadata for nil attestation, got %v", err)
	}

	// Nil metadata
	att := &fpb.SketchAttestation{Metadata: nil}
	_, err = UnpackSketch(att, cfg)
	if err != ErrInvalidMetadata {
		t.Fatalf("Expected ErrInvalidMetadata for nil metadata, got %v", err)
	}

	// Empty sketch data
	att = &fpb.SketchAttestation{
		Metadata: &fpb.SketchMetadata{
			AntibodyId:  "test",
			Environment: "prod",
		},
		SketchData: nil,
	}
	_, err = UnpackSketch(att, cfg)
	if err != ErrInvalidMetadata {
		t.Fatalf("Expected ErrInvalidMetadata for empty sketch data, got %v", err)
	}
}