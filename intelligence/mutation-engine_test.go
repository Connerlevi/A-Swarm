package intelligence

import (
	"context"
	"sync"
	"testing"
	"time"
)

func TestValidationFailures(t *testing.T) {
	me := NewMutationEngine(42)
	cfg := DefaultMutationConfig()
	ctx := context.Background()

	tests := []struct {
		name    string
		spec    AntibodySpec
		wantErr string
	}{
		{
			name: "confidence_threshold_too_low",
			spec: AntibodySpec{
				Scope: ScopeSpec{
					ConfidenceThreshold: -0.1,
					Environments:        []string{"test"},
				},
			},
			wantErr: "confidence_threshold -0.100 must be in [0,1]",
		},
		{
			name: "confidence_threshold_too_high",
			spec: AntibodySpec{
				Scope: ScopeSpec{
					ConfidenceThreshold: 1.5,
					Environments:        []string{"test"},
				},
			},
			wantErr: "confidence_threshold 1.500 must be in [0,1]",
		},
		{
			name: "hybrid_without_weights",
			spec: AntibodySpec{
				Detector: DetectorSpec{Type: "hybrid"},
				Scope: ScopeSpec{
					ConfidenceThreshold: 0.8,
					Environments:        []string{"test"},
				},
			},
			wantErr: "hybrid detector requires hybrid weights",
		},
		{
			name: "negative_hybrid_weights",
			spec: AntibodySpec{
				Detector: DetectorSpec{
					Type: "hybrid",
					Hybrid: &HybridSpec{
						RuleWeight:  -0.1,
						ModelWeight: 1.1,
					},
				},
				Scope: ScopeSpec{
					ConfidenceThreshold: 0.8,
					Environments:        []string{"test"},
				},
			},
			wantErr: "hybrid weights must be non-negative",
		},
		{
			name: "hybrid_weights_dont_sum_to_1",
			spec: AntibodySpec{
				Detector: DetectorSpec{
					Type: "hybrid",
					Hybrid: &HybridSpec{
						RuleWeight:  0.3,
						ModelWeight: 0.4, // sum = 0.7
					},
				},
				Scope: ScopeSpec{
					ConfidenceThreshold: 0.8,
					Environments:        []string{"test"},
				},
			},
			wantErr: "hybrid weights must sum to 1.0",
		},
		{
			name: "no_environments",
			spec: AntibodySpec{
				Scope: ScopeSpec{
					ConfidenceThreshold: 0.8,
					Environments:        []string{},
				},
			},
			wantErr: "at least one environment must be specified",
		},
		{
			name: "empty_rule_pattern",
			spec: AntibodySpec{
				Detector: DetectorSpec{
					Type: "rule",
					Rule: &RuleSpec{
						Pattern: "",
						Features: map[string]string{"test": "1"},
					},
				},
				Scope: ScopeSpec{
					ConfidenceThreshold: 0.8,
					Environments:        []string{"test"},
				},
			},
			wantErr: "rule pattern cannot be empty",
		},
		{
			name: "rule_pattern_too_long",
			spec: AntibodySpec{
				Detector: DetectorSpec{
					Type: "rule",
					Rule: &RuleSpec{
						Pattern:  string(make([]byte, 2049)), // Too long
						Features: map[string]string{"test": "1"},
					},
				},
				Scope: ScopeSpec{
					ConfidenceThreshold: 0.8,
					Environments:        []string{"test"},
				},
			},
			wantErr: "rule pattern too long: 2049 > 2048 chars",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := me.ValidateSpec(ctx, tt.spec, cfg)
			if err == nil {
				t.Errorf("ValidateSpec() expected error containing %q, got nil", tt.wantErr)
				return
			}
			if !contains(err.Error(), tt.wantErr) {
				t.Errorf("ValidateSpec() error = %v, want error containing %q", err, tt.wantErr)
			}
		})
	}
}

func TestJitterBounds(t *testing.T) {
	me := NewMutationEngine(42)
	cfg := MutationConfig{
		ParamJitterProb:  1.0, // Always jitter
		ThresholdDelta:   0.1,
		ParamJitterSigma: 0.05,
	}
	ctx := context.Background()

	parent := AntibodySpec{
		Detector: DetectorSpec{Type: "rule"},
		Scope: ScopeSpec{
			ConfidenceThreshold: 0.5,
			Environments:        []string{"test"},
		},
	}

	// Run many mutations to test bounds
	for i := 0; i < 1000; i++ {
		mutant, err := me.Mutate(ctx, parent, cfg)
		if err != nil {
			t.Fatalf("Mutate() error = %v", err)
		}

		// Confidence threshold should always be in [0,1]
		if mutant.Scope.ConfidenceThreshold < 0.0 || mutant.Scope.ConfidenceThreshold > 1.0 {
			t.Errorf("Confidence threshold out of bounds: %f", mutant.Scope.ConfidenceThreshold)
		}
	}
}

func TestHybridNormalization(t *testing.T) {
	me := NewMutationEngine(42)
	cfg := MutationConfig{
		WeightShuffleProb: 1.0, // Always shuffle
		ParamJitterSigma:  0.1,
	}
	ctx := context.Background()

	parent := AntibodySpec{
		Detector: DetectorSpec{
			Type: "hybrid",
			Hybrid: &HybridSpec{
				RuleWeight:  0.6,
				ModelWeight: 0.4,
			},
		},
		Scope: ScopeSpec{
			ConfidenceThreshold: 0.8,
			Environments:        []string{"test"},
		},
	}

	// Run many mutations to test normalization
	for i := 0; i < 1000; i++ {
		mutant, err := me.Mutate(ctx, parent, cfg)
		if err != nil {
			t.Fatalf("Mutate() error = %v", err)
		}

		h := mutant.Detector.Hybrid
		if h == nil {
			t.Fatal("Hybrid spec disappeared")
		}

		// Weights should be non-negative
		if h.RuleWeight < 0 || h.ModelWeight < 0 {
			t.Errorf("Negative weights: rule=%f, model=%f", h.RuleWeight, h.ModelWeight)
		}

		// Weights should sum to approximately 1
		sum := h.RuleWeight + h.ModelWeight
		if sum < 0.999 || sum > 1.001 {
			t.Errorf("Weights don't sum to 1: %f", sum)
		}
	}
}

func TestCrossoverDeterminism(t *testing.T) {
	parent1 := AntibodySpec{
		Detector: DetectorSpec{
			Type: "rule",
			Rule: &RuleSpec{
				Pattern:  "test1",
				Features: map[string]string{"feature1": "1", "feature2": "0"},
			},
		},
		Scope: ScopeSpec{
			ConfidenceThreshold: 0.8,
			Environments:        []string{"test"},
		},
	}

	parent2 := AntibodySpec{
		Detector: DetectorSpec{
			Type: "rule",
			Rule: &RuleSpec{
				Pattern:  "test2",
				Features: map[string]string{"feature1": "0", "feature3": "1"},
			},
		},
		Scope: ScopeSpec{
			ConfidenceThreshold: 0.7,
			Environments:        []string{"test"},
		},
	}

	cfg := DefaultMutationConfig()
	ctx := context.Background()

	// Two engines with same seed should produce identical results
	me1 := NewMutationEngine(123)
	me2 := NewMutationEngine(123)

	offspring1, err1 := me1.CrossOver(ctx, []AntibodySpec{parent1, parent2}, cfg)
	offspring2, err2 := me2.CrossOver(ctx, []AntibodySpec{parent1, parent2}, cfg)

	if err1 != nil || err2 != nil {
		t.Fatalf("CrossOver() errors: %v, %v", err1, err2)
	}

	// Should be identical
	if !specsEqual(offspring1, offspring2) {
		t.Errorf("CrossOver() not deterministic with same seed")
	}
}

func TestBitsetDiversityBehavior(t *testing.T) {
	me := NewMutationEngine(42)
	ctx := context.Background()

	spec1 := AntibodySpec{
		Detector: DetectorSpec{
			Type: "rule",
			Rule: &RuleSpec{
				Pattern:  "test",
				Features: map[string]string{"feature1": "1"},
			},
		},
		Scope: ScopeSpec{
			ConfidenceThreshold: 0.8,
			Environments:        []string{"test"},
		},
	}

	spec2 := spec1
	spec2.Detector.Rule.Features = map[string]string{"feature1": "0"} // One feature flipped

	sig1, err := me.ComputeDiversitySignature(ctx, spec1)
	if err != nil {
		t.Fatalf("ComputeDiversitySignature() error = %v", err)
	}

	sig2, err := me.ComputeDiversitySignature(ctx, spec2)
	if err != nil {
		t.Fatalf("ComputeDiversitySignature() error = %v", err)
	}

	// Same spec should produce same signature
	sig1Dup, err := me.ComputeDiversitySignature(ctx, spec1)
	if err != nil {
		t.Fatalf("ComputeDiversitySignature() error = %v", err)
	}

	if sig1 != sig1Dup {
		t.Errorf("Same spec produced different signatures")
	}

	// Different specs should have similarity < 1
	similarity, err := ComputeBitsetJaccardSimilarity(sig1, sig2)
	if err != nil {
		t.Fatalf("ComputeBitsetJaccardSimilarity() error = %v", err)
	}

	if similarity >= 1.0 {
		t.Errorf("Different specs have similarity = %f, expected < 1.0", similarity)
	}

	// Test version mismatch
	badSig := "v999:SGVsbG9Xb3JsZA=="
	_, err = ComputeBitsetJaccardSimilarity(sig1, badSig)
	if err == nil {
		t.Error("Expected version mismatch error")
	}
}

func TestSanitization(t *testing.T) {
	me := NewMutationEngine(42)
	cfg := DefaultMutationConfig()
	ctx := context.Background()

	spec := AntibodySpec{
		Detector: DetectorSpec{
			Type: "rule",
			Rule: &RuleSpec{
				Pattern:  "  test pattern  ", // Leading/trailing spaces
				Features: map[string]string{"test": "1"},
			},
		},
		Scope: ScopeSpec{
			ConfidenceThreshold: 0.8,
			Environments:        []string{"test"},
			Labels: map[string]string{
				"  KEY1  ": "  value1  ", // Spaces and uppercase
				"key2":     "value2",
			},
		},
	}

	mutant, err := me.Mutate(ctx, spec, cfg)
	if err != nil {
		t.Fatalf("Mutate() error = %v", err)
	}

	// Pattern should be trimmed
	if mutant.Detector.Rule.Pattern != "test pattern" {
		t.Errorf("Pattern not trimmed: %q", mutant.Detector.Rule.Pattern)
	}

	// Labels should be lowercased and trimmed
	if mutant.Scope.Labels["key1"] != "value1" {
		t.Errorf("Label not sanitized: %v", mutant.Scope.Labels)
	}
}

func TestRaceConditions(t *testing.T) {
	me := NewMutationEngine(42)
	cfg := DefaultMutationConfig()
	ctx := context.Background()

	parent := AntibodySpec{
		Detector: DetectorSpec{
			Type: "rule",
			Rule: &RuleSpec{
				Pattern:  "test",
				Features: map[string]string{"feature1": "1"},
			},
		},
		Scope: ScopeSpec{
			ConfidenceThreshold: 0.8,
			Environments:        []string{"test"},
		},
	}

	// Run mutations concurrently
	var wg sync.WaitGroup
	numWorkers := 10
	numMutationsPerWorker := 100

	for i := 0; i < numWorkers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < numMutationsPerWorker; j++ {
				_, err := me.Mutate(ctx, parent, cfg)
				if err != nil {
					t.Errorf("Concurrent mutation failed: %v", err)
				}

				_, err = me.ComputeDiversitySignature(ctx, parent)
				if err != nil {
					t.Errorf("Concurrent signature computation failed: %v", err)
				}
			}
		}()
	}

	wg.Wait()
}

func TestMutateN(t *testing.T) {
	me := NewMutationEngine(42)
	cfg := DefaultMutationConfig()
	ctx := context.Background()

	parent := AntibodySpec{
		Detector: DetectorSpec{Type: "rule"},
		Scope: ScopeSpec{
			ConfidenceThreshold: 0.8,
			Environments:        []string{"test"},
		},
	}

	variants, err := me.MutateN(ctx, parent, cfg, 5)
	if err != nil {
		t.Fatalf("MutateN() error = %v", err)
	}

	if len(variants) != 5 {
		t.Errorf("MutateN() returned %d variants, want 5", len(variants))
	}

	// Each variant should be valid
	for i, variant := range variants {
		if err := me.ValidateSpec(ctx, variant, cfg); err != nil {
			t.Errorf("Variant %d invalid: %v", i, err)
		}
	}
}

func TestDiversityDistance(t *testing.T) {
	sig1 := "v1:SGVsbG9Xb3JsZA=="
	sig2 := "v1:SGVsbG9Xb3JsZA==" // Same
	sig3 := "v1:RGlmZmVyZW50U2ln" // Different

	// Same signatures should have distance 0
	dist, err := DiversityDistance(sig1, sig2)
	if err != nil {
		t.Fatalf("DiversityDistance() error = %v", err)
	}
	if dist != 0.0 {
		t.Errorf("Distance between identical signatures = %f, want 0.0", dist)
	}

	// Different signatures should have distance > 0
	dist, err = DiversityDistance(sig1, sig3)
	if err != nil {
		t.Fatalf("DiversityDistance() error = %v", err)
	}
	if dist <= 0.0 {
		t.Errorf("Distance between different signatures = %f, want > 0.0", dist)
	}
}

// Helper functions

func contains(s, substr string) bool {
	return len(s) >= len(substr) &&
		   (s == substr ||
		    (len(s) > len(substr) &&
		     (s[:len(substr)] == substr ||
		      s[len(s)-len(substr):] == substr ||
		      containsSubstring(s, substr))))
}

func containsSubstring(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

func specsEqual(a, b AntibodySpec) bool {
	// Simplified equality check for testing
	if a.Detector.Type != b.Detector.Type {
		return false
	}
	if a.Scope.ConfidenceThreshold != b.Scope.ConfidenceThreshold {
		return false
	}

	// Check rule features if present
	if a.Detector.Rule != nil && b.Detector.Rule != nil {
		if len(a.Detector.Rule.Features) != len(b.Detector.Rule.Features) {
			return false
		}
		for k, v := range a.Detector.Rule.Features {
			if b.Detector.Rule.Features[k] != v {
				return false
			}
		}
	} else if a.Detector.Rule != b.Detector.Rule {
		return false
	}

	return true
}

// Benchmark tests
func BenchmarkMutate(b *testing.B) {
	me := NewMutationEngine(42)
	cfg := DefaultMutationConfig()
	ctx := context.Background()

	parent := AntibodySpec{
		Detector: DetectorSpec{
			Type: "rule",
			Rule: &RuleSpec{
				Pattern:  "test",
				Features: make(map[string]string),
			},
		},
		Scope: ScopeSpec{
			ConfidenceThreshold: 0.8,
			Environments:        []string{"test"},
		},
	}

	// Add many features to stress-test
	for i := 0; i < 100; i++ {
		parent.Detector.Rule.Features[fmt.Sprintf("feature_%d", i)] = "1"
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := me.Mutate(ctx, parent, cfg)
		if err != nil {
			b.Fatalf("Mutate() error = %v", err)
		}
	}
}

func BenchmarkComputeDiversitySignature(b *testing.B) {
	me := NewMutationEngine(42)
	ctx := context.Background()

	spec := AntibodySpec{
		Detector: DetectorSpec{
			Type: "rule",
			Rule: &RuleSpec{
				Pattern:  "test",
				Features: make(map[string]string),
			},
		},
		Scope: ScopeSpec{
			ConfidenceThreshold: 0.8,
			Environments:        []string{"test"},
		},
	}

	// Add features
	for i := 0; i < 50; i++ {
		spec.Detector.Rule.Features[fmt.Sprintf("feature_%d", i)] = "1"
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := me.ComputeDiversitySignature(ctx, spec)
		if err != nil {
			b.Fatalf("ComputeDiversitySignature() error = %v", err)
		}
	}
}