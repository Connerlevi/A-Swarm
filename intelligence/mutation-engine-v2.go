// Package intelligence - A-SWARM Mutation Engine v2
// Implements genetic operators with context handling, mutation diffs, and production hardening

package intelligence

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"math"
	"math/rand"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	// Bitset size for diversity signatures (512 bits = 64 bytes)
	DiversityBitsetSize = 512

	// Feature hashing salt to prevent adversarial feature collisions
	FeatureHashSalt = "aswarm-diversity-v1"

	// Diversity signature version (prefix for encoded bitsets)
	DiversitySigVersion = "v1"
)

// MutationConfig controls mutation probabilities and magnitudes
type MutationConfig struct {
	// General
	ParamJitterProb   float64 // 0..1 probability to attempt parameter jitter
	ParamJitterSigma  float64 // stddev for Gaussian jitter on weights (hybrid)
	ThresholdDelta    float64 // absolute jitter magnitude for confidence threshold (e.g., 0.05)
	MaxComplexityHint int     // optional guardrail; 0=ignore

	// Rule features
	FeatureToggleProb float64 // 0..1 probability to flip a binary rule feature
	FeatureAddProb    float64 // 0..1 probability to add a random feature
	FeatureRemoveProb float64 // 0..1 probability to remove a random feature

	// Hybrid
	WeightShuffleProb float64 // 0..1 probability to jitter hybrid weights
}

// MutationDiff tracks changes made during mutation for auditability
type MutationDiff struct {
	ThresholdBefore float64     `json:"threshold_before"`
	ThresholdAfter  float64     `json:"threshold_after"`
	Toggled         []string    `json:"toggled,omitempty"`         // Features that were flipped
	Added           []string    `json:"added,omitempty"`           // Features that were added
	Removed         []string    `json:"removed,omitempty"`         // Features that were removed
	HybridBefore    *HybridSpec `json:"hybrid_before,omitempty"`
	HybridAfter     *HybridSpec `json:"hybrid_after,omitempty"`
}

// SimpleMutationEngine implements basic genetic operators for antibody specs
type SimpleMutationEngine struct {
	rng *rand.Rand // Deterministic RNG for reproducible mutations
	mu  sync.Mutex // Protects RNG for concurrent access
}

// NewMutationEngine creates a mutation engine with specified seed for reproducibility
func NewMutationEngine(seed int64) *SimpleMutationEngine {
	if seed == 0 {
		seed = time.Now().UnixNano()
	}
	return &SimpleMutationEngine{
		rng: rand.New(rand.NewSource(seed)),
	}
}

// WithSeed creates a new engine with deterministic seed (useful for replay/tests)
func (me *SimpleMutationEngine) WithSeed(seed int64) *SimpleMutationEngine {
	return &SimpleMutationEngine{
		rng: rand.New(rand.NewSource(seed)),
	}
}

// SeedForOffspring generates deterministic seed for offspring lineage tracking
func SeedForOffspring(parentID string, index int) int64 {
	hasher := sha256.New()
	hasher.Write([]byte(fmt.Sprintf("%s|%d", parentID, index)))
	hash := hasher.Sum(nil)

	// Convert first 8 bytes to int64
	seed := int64(hash[0])<<56 | int64(hash[1])<<48 | int64(hash[2])<<40 | int64(hash[3])<<32 |
		   int64(hash[4])<<24 | int64(hash[5])<<16 | int64(hash[6])<<8 | int64(hash[7])

	return seed
}

// Thread-safe RNG helpers
func (me *SimpleMutationEngine) float64() float64 {
	me.mu.Lock()
	defer me.mu.Unlock()
	return me.rng.Float64()
}

func (me *SimpleMutationEngine) normFloat64() float64 {
	me.mu.Lock()
	defer me.mu.Unlock()
	return me.rng.NormFloat64()
}

func (me *SimpleMutationEngine) int31() int32 {
	me.mu.Lock()
	defer me.mu.Unlock()
	return me.rng.Int31()
}

func (me *SimpleMutationEngine) intn(n int) int {
	me.mu.Lock()
	defer me.mu.Unlock()
	return me.rng.Intn(n)
}

// ctxDone checks if context is done and returns appropriate error
func ctxDone(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
		return nil
	}
}

// MutateWithDiff applies genetic operators and returns both the variant and diff for auditability
func (me *SimpleMutationEngine) MutateWithDiff(ctx context.Context, parent AntibodySpec, config MutationConfig) (AntibodySpec, *MutationDiff, error) {
	if err := ctxDone(ctx); err != nil {
		return AntibodySpec{}, nil, err
	}

	// Initialize diff tracking
	diff := &MutationDiff{
		ThresholdBefore: parent.Scope.ConfidenceThreshold,
	}

	// Deep copy parent to avoid mutation side effects
	mutant := me.deepCopySpec(parent)

	// Track hybrid weights before mutation
	if mutant.Detector.Hybrid != nil {
		diff.HybridBefore = &HybridSpec{
			RuleWeight:  mutant.Detector.Hybrid.RuleWeight,
			ModelWeight: mutant.Detector.Hybrid.ModelWeight,
		}
	}

	// Apply parameter jitter mutations
	if me.float64() < config.ParamJitterProb {
		if err := ctxDone(ctx); err != nil {
			return AntibodySpec{}, nil, err
		}
		if err := me.mutateParameters(&mutant, config); err != nil {
			return AntibodySpec{}, nil, fmt.Errorf("parameter mutation failed: %w", err)
		}
	}

	// Apply feature mutations based on detector type
	switch mutant.Detector.Type {
	case "rule":
		if mutant.Detector.Rule != nil {
			if err := ctxDone(ctx); err != nil {
				return AntibodySpec{}, nil, err
			}
			if err := me.mutateRuleFeatures(mutant.Detector.Rule, config, diff); err != nil {
				return AntibodySpec{}, nil, fmt.Errorf("rule feature mutation failed: %w", err)
			}
		}
	case "hybrid":
		if mutant.Detector.Hybrid != nil {
			if err := ctxDone(ctx); err != nil {
				return AntibodySpec{}, nil, err
			}
			if err := me.mutateHybridWeights(mutant.Detector.Hybrid, config); err != nil {
				return AntibodySpec{}, nil, fmt.Errorf("hybrid weight mutation failed: %w", err)
			}
		}
		// Also mutate rule features if present
		if mutant.Detector.Rule != nil {
			if err := me.mutateRuleFeatures(mutant.Detector.Rule, config, diff); err != nil {
				return AntibodySpec{}, nil, fmt.Errorf("hybrid rule feature mutation failed: %w", err)
			}
		}
	case "model":
		// Model mutations would be implemented here for ML-based detectors
		// For now, skip model-specific mutations
	}

	// Apply scope mutations (conservative)
	if err := me.mutateScope(&mutant.Scope, config); err != nil {
		return AntibodySpec{}, nil, fmt.Errorf("scope mutation failed: %w", err)
	}

	// Track final state in diff
	diff.ThresholdAfter = mutant.Scope.ConfidenceThreshold
	if mutant.Detector.Hybrid != nil {
		diff.HybridAfter = &HybridSpec{
			RuleWeight:  mutant.Detector.Hybrid.RuleWeight,
			ModelWeight: mutant.Detector.Hybrid.ModelWeight,
		}
	}

	// Sanitize before validation
	me.sanitizeSpec(&mutant)

	// Validate the mutated spec meets safety constraints
	if err := me.ValidateSpec(ctx, mutant, config); err != nil {
		return AntibodySpec{}, nil, fmt.Errorf("mutated spec failed validation: %w", err)
	}

	return mutant, diff, nil
}

// Mutate applies genetic operators to generate a variant from parent spec
func (me *SimpleMutationEngine) Mutate(ctx context.Context, parent AntibodySpec, config MutationConfig) (AntibodySpec, error) {
	variant, _, err := me.MutateWithDiff(ctx, parent, config)
	return variant, err
}

// MutateN produces a burst of variants from a single parent with deterministic lineage seeds
func (me *SimpleMutationEngine) MutateN(ctx context.Context, parent AntibodySpec, parentID string, config MutationConfig, n int) ([]AntibodySpec, error) {
	variants := make([]AntibodySpec, 0, n)
	for i := 0; i < n; i++ {
		if err := ctxDone(ctx); err != nil {
			return nil, err
		}

		// Use deterministic seed for reproducible lineage
		seed := SeedForOffspring(parentID, i)
		engine := me.WithSeed(seed)

		variant, err := engine.Mutate(ctx, parent, config)
		if err != nil {
			return nil, fmt.Errorf("mutation %d failed: %w", i, err)
		}
		variants = append(variants, variant)
	}
	return variants, nil
}

// CrossOver combines features from multiple parents (simplified implementation)
func (me *SimpleMutationEngine) CrossOver(ctx context.Context, parents []AntibodySpec, config MutationConfig) (AntibodySpec, error) {
	if err := ctxDone(ctx); err != nil {
		return AntibodySpec{}, err
	}

	if len(parents) < 2 {
		return AntibodySpec{}, fmt.Errorf("crossover requires at least 2 parents, got %d", len(parents))
	}

	// Use first parent as base template
	offspring := me.deepCopySpec(parents[0])

	// For rule-based crossover, blend features from multiple parents
	if offspring.Detector.Type == "rule" && offspring.Detector.Rule != nil {
		if err := ctxDone(ctx); err != nil {
			return AntibodySpec{}, err
		}
		if err := me.crossoverRuleFeatures(offspring.Detector.Rule, parents, config); err != nil {
			return AntibodySpec{}, fmt.Errorf("rule crossover failed: %w", err)
		}
	}

	// For hybrid detectors, average weights across parents
	if offspring.Detector.Type == "hybrid" && offspring.Detector.Hybrid != nil {
		if err := me.crossoverHybridWeights(offspring.Detector.Hybrid, parents, config); err != nil {
			return AntibodySpec{}, fmt.Errorf("hybrid crossover failed: %w", err)
		}
	}

	// Sanitize before validation
	me.sanitizeSpec(&offspring)

	// Validate crossover result
	if err := me.ValidateSpec(ctx, offspring, config); err != nil {
		return AntibodySpec{}, fmt.Errorf("crossover offspring failed validation: %w", err)
	}

	return offspring, nil
}

// ValidateSpec ensures generated specs meet safety constraints
func (me *SimpleMutationEngine) ValidateSpec(ctx context.Context, spec AntibodySpec, cfg MutationConfig) error {
	if err := ctxDone(ctx); err != nil {
		return err
	}

	// Validate confidence threshold is in valid range
	if spec.Scope.ConfidenceThreshold < 0.0 || spec.Scope.ConfidenceThreshold > 1.0 {
		return fmt.Errorf("confidence_threshold %.3f must be in [0,1]", spec.Scope.ConfidenceThreshold)
	}

	// Require hybrid config if hybrid detector selected
	if spec.Detector.Type == "hybrid" && spec.Detector.Hybrid == nil {
		return fmt.Errorf("hybrid detector requires hybrid weights")
	}

	// Validate hybrid weights sum to 1 and are non-negative
	if spec.Detector.Type == "hybrid" && spec.Detector.Hybrid != nil {
		h := spec.Detector.Hybrid
		if h.RuleWeight < 0 || h.ModelWeight < 0 {
			return fmt.Errorf("hybrid weights must be non-negative: rule=%.3f, model=%.3f", h.RuleWeight, h.ModelWeight)
		}
		sum := h.RuleWeight + h.ModelWeight
		if math.Abs(sum-1.0) > 1e-6 {
			return fmt.Errorf("hybrid weights must sum to 1.0, got %.6f", sum)
		}
		// Paranoia guard against NaN
		if math.IsNaN(h.RuleWeight) || math.IsNaN(h.ModelWeight) {
			return fmt.Errorf("hybrid weights contain NaN")
		}
	}

	// Validate environments are specified
	if len(spec.Scope.Environments) == 0 {
		return fmt.Errorf("at least one environment must be specified")
	}

	// Validate rule patterns are not empty
	if spec.Detector.Type == "rule" && spec.Detector.Rule != nil {
		if spec.Detector.Rule.Pattern == "" {
			return fmt.Errorf("rule pattern cannot be empty")
		}
		if len(spec.Detector.Rule.Pattern) > 2048 {
			return fmt.Errorf("rule pattern too long: %d > 2048 chars", len(spec.Detector.Rule.Pattern))
		}
	}

	// Complexity guardrail with soft enforcement
	if cfg.MaxComplexityHint > 0 {
		complexity := me.computeComplexity(spec)
		if complexity > cfg.MaxComplexityHint {
			// TODO: Add probabilistic rejection or feature trimming for evolutionary pressure
			// For now, just validate
			return fmt.Errorf("spec complexity %d exceeds limit %d", complexity, cfg.MaxComplexityHint)
		}
	}
	return nil
}

// ComputeDiversitySignature generates base64-encoded bitset for diversity calculation
func (me *SimpleMutationEngine) ComputeDiversitySignature(ctx context.Context, spec AntibodySpec) (string, error) {
	if err := ctxDone(ctx); err != nil {
		return "", err
	}

	// Initialize bitset (512 bits = 64 bytes)
	bitset := make([]byte, DiversityBitsetSize/8)

	// Hash features into bitset positions
	if err := me.hashSpecToBitset(spec, bitset); err != nil {
		return "", fmt.Errorf("failed to hash spec to bitset: %w", err)
	}

	// Encode as base64 with version prefix
	encoded := base64.StdEncoding.EncodeToString(bitset)
	return fmt.Sprintf("%s:%s", DiversitySigVersion, encoded), nil
}

// Helper methods for mutation operations

func (me *SimpleMutationEngine) sanitizeSpec(spec *AntibodySpec) {
	// Trim rule patterns
	if spec.Detector.Rule != nil {
		spec.Detector.Rule.Pattern = strings.TrimSpace(spec.Detector.Rule.Pattern)
	}

	// Lowercase label keys for consistency
	if len(spec.Scope.Labels) > 0 {
		normalized := make(map[string]string, len(spec.Scope.Labels))
		for k, v := range spec.Scope.Labels {
			normalized[strings.ToLower(strings.TrimSpace(k))] = strings.TrimSpace(v)
		}
		spec.Scope.Labels = normalized
	}
}

func (me *SimpleMutationEngine) deepCopySpec(spec AntibodySpec) AntibodySpec {
	// Create deep copy to avoid mutations affecting original
	cp := AntibodySpec{
		Detector: DetectorSpec{
			Type: spec.Detector.Type,
		},
		Scope: ScopeSpec{
			Environments:        make([]string, len(spec.Scope.Environments)),
			ConfidenceThreshold: spec.Scope.ConfidenceThreshold,
		},
		Lineage:  spec.Lineage,  // value copy (your type is not a pointer)
		Controls: spec.Controls, // value copy (your type is not a pointer)
	}

	copy(cp.Scope.Environments, spec.Scope.Environments)

	// Copy namespaces if present
	if len(spec.Scope.Namespaces) > 0 {
		cp.Scope.Namespaces = make([]string, len(spec.Scope.Namespaces))
		copy(cp.Scope.Namespaces, spec.Scope.Namespaces)
	}

	// Copy labels if present
	if len(spec.Scope.Labels) > 0 {
		cp.Scope.Labels = make(map[string]string, len(spec.Scope.Labels))
		for k, v := range spec.Scope.Labels {
			cp.Scope.Labels[k] = v
		}
	}

	// Copy detector-specific fields
	if spec.Detector.Rule != nil {
		cp.Detector.Rule = &RuleSpec{
			Pattern:    spec.Detector.Rule.Pattern,
			EngineHint: spec.Detector.Rule.EngineHint,
			Features:   make(map[string]string, len(spec.Detector.Rule.Features)),
		}
		for k, v := range spec.Detector.Rule.Features {
			cp.Detector.Rule.Features[k] = v
		}
	}

	if spec.Detector.Model != nil {
		cp.Detector.Model = &ModelSpec{
			TrainingData: spec.Detector.Model.TrainingData,
			Features:     make(map[string]interface{}, len(spec.Detector.Model.Features)),
		}
		for k, v := range spec.Detector.Model.Features {
			cp.Detector.Model.Features[k] = v
		}
	}

	if spec.Detector.Hybrid != nil {
		cp.Detector.Hybrid = &HybridSpec{
			RuleWeight:  spec.Detector.Hybrid.RuleWeight,
			ModelWeight: spec.Detector.Hybrid.ModelWeight,
		}
	}

	return cp
}

func (me *SimpleMutationEngine) mutateParameters(spec *AntibodySpec, config MutationConfig) error {
	// Mutate confidence threshold with jitter
	if me.float64() < 0.5 { // 50% chance to mutate confidence threshold
		delta := me.normFloat64() * config.ThresholdDelta
		newThreshold := spec.Scope.ConfidenceThreshold + delta
		spec.Scope.ConfidenceThreshold = clampFloat64(newThreshold, 0.0, 1.0)
	}
	return nil
}

func (me *SimpleMutationEngine) mutateRuleFeatures(rule *RuleSpec, config MutationConfig, diff *MutationDiff) error {
	// Feature toggle mutations
	for feature := range rule.Features {
		if me.float64() < config.FeatureToggleProb {
			oldValue := rule.Features[feature]
			// TODO: handle non-binary rule features; metric: mutation_non_binary_feature_total++
			if rule.Features[feature] == "1" {
				rule.Features[feature] = "0"
			} else {
				rule.Features[feature] = "1"
			}

			// Track toggle in diff if value actually changed
			if rule.Features[feature] != oldValue {
				diff.Toggled = append(diff.Toggled, feature)
			}
		}
	}

	// Feature addition mutations with uniqueness check
	if me.float64() < config.FeatureAddProb {
		if rule.Features == nil {
			rule.Features = map[string]string{}
		}
		// Generate unique feature name
		for {
			newFeature := fmt.Sprintf("mutated_feature_%d", me.int31())
			if _, exists := rule.Features[newFeature]; !exists {
				rule.Features[newFeature] = "1" // Add as active feature
				diff.Added = append(diff.Added, newFeature)
				break
			}
		}
	}

	// Feature removal mutations
	if me.float64() < config.FeatureRemoveProb && len(rule.Features) > 1 {
		keys := make([]string, 0, len(rule.Features))
		for k := range rule.Features {
			keys = append(keys, k)
		}
		if len(keys) > 0 {
			removeKey := keys[me.intn(len(keys))]
			delete(rule.Features, removeKey)
			diff.Removed = append(diff.Removed, removeKey)
		}
	}
	return nil
}

func (me *SimpleMutationEngine) mutateHybridWeights(hybrid *HybridSpec, config MutationConfig) error {
	if me.float64() < config.WeightShuffleProb {
		// Add jitter to weights
		ruleJitter := me.normFloat64() * config.ParamJitterSigma
		modelJitter := me.normFloat64() * config.ParamJitterSigma

		newRule := math.Max(0.0, hybrid.RuleWeight+ruleJitter)
		newModel := math.Max(0.0, hybrid.ModelWeight+modelJitter)

		// Normalize to sum to 1
		sum := newRule + newModel
		if sum > 0 && !math.IsNaN(sum) && !math.IsInf(sum, 0) {
			hybrid.RuleWeight = newRule / sum
			hybrid.ModelWeight = newModel / sum
		}
	}
	return nil
}

func (me *SimpleMutationEngine) mutateScope(_ *ScopeSpec, _ MutationConfig) error {
	// Intentionally conservative for v0 (confidence threshold handled in mutateParameters)
	return nil
}

func (me *SimpleMutationEngine) crossoverRuleFeatures(offspring *RuleSpec, parents []AntibodySpec, _ MutationConfig) error {
	// Collect all features from all rule-based parents
	all := make(map[string][]string) // feature -> values from parents
	for _, p := range parents {
		if p.Detector.Type == "rule" && p.Detector.Rule != nil {
			for feature, value := range p.Detector.Rule.Features {
				all[feature] = append(all[feature], value)
			}
		}
	}
	if offspring.Features == nil {
		offspring.Features = map[string]string{}
	}
	// For each feature, randomly select a parent value
	for feature, values := range all {
		if len(values) > 0 {
			offspring.Features[feature] = values[me.intn(len(values))]
		}
	}
	return nil
}

func (me *SimpleMutationEngine) crossoverHybridWeights(offspring *HybridSpec, parents []AntibodySpec, _ MutationConfig) error {
	var rw, mw []float64
	for _, p := range parents {
		if p.Detector.Type == "hybrid" && p.Detector.Hybrid != nil {
			rw = append(rw, p.Detector.Hybrid.RuleWeight)
			mw = append(mw, p.Detector.Hybrid.ModelWeight)
		}
	}
	if len(rw) > 0 && len(mw) > 0 {
		avgR := me.average(rw)
		avgM := me.average(mw)
		sum := avgR + avgM
		if sum > 0 {
			offspring.RuleWeight = avgR / sum
			offspring.ModelWeight = avgM / sum
		}
	}
	return nil
}

func (me *SimpleMutationEngine) computeComplexity(spec AntibodySpec) int {
	complexity := 0
	// Count rule features
	if spec.Detector.Rule != nil {
		complexity += len(spec.Detector.Rule.Features)
		complexity += len(spec.Detector.Rule.Pattern) / 10 // pattern length proxy
	}
	// Count model features
	if spec.Detector.Model != nil {
		complexity += len(spec.Detector.Model.Features)
	}
	// Hybrid base complexity
	if spec.Detector.Hybrid != nil {
		complexity += 2
	}
	return complexity
}

func (me *SimpleMutationEngine) hashSpecToBitset(spec AntibodySpec, bitset []byte) error {
	hasher := sha256.New()
	hasher.Write([]byte(FeatureHashSalt))

	// Detector type
	hasher.Write([]byte(fmt.Sprintf("type:%s", spec.Detector.Type)))

	// Rule features
	if spec.Detector.Rule != nil {
		var feats []string
		for k, v := range spec.Detector.Rule.Features {
			feats = append(feats, fmt.Sprintf("%s=%s", k, v))
		}
		sort.Strings(feats)
		for _, f := range feats {
			hasher.Write([]byte(f))
			h := hasher.Sum(nil)
			setBitFromHash(bitset, h)
			hasher.Reset()
			hasher.Write([]byte(FeatureHashSalt))
		}
	}

	// Model features
	if spec.Detector.Model != nil {
		var mfeats []string
		for k, v := range spec.Detector.Model.Features {
			mfeats = append(mfeats, fmt.Sprintf("%s=%v", k, v))
		}
		sort.Strings(mfeats)
		for _, f := range mfeats {
			hasher.Write([]byte(f))
			h := hasher.Sum(nil)
			setBitFromHash(bitset, h)
			hasher.Reset()
			hasher.Write([]byte(FeatureHashSalt))
		}
	}

	// Hybrid weights (quantized)
	if spec.Detector.Hybrid != nil {
		rw := int(spec.Detector.Hybrid.RuleWeight * 1000)  // 3 decimals
		mw := int(spec.Detector.Hybrid.ModelWeight * 1000) // 3 decimals
		hasher.Write([]byte(fmt.Sprintf("rw:%d|mw:%d", rw, mw)))
		h := hasher.Sum(nil)
		setBitFromHash(bitset, h)
		hasher.Reset()
		hasher.Write([]byte(FeatureHashSalt))
	}

	// Confidence threshold (quantized)
	confQ := int(spec.Scope.ConfidenceThreshold * 1000)
	hasher.Write([]byte(fmt.Sprintf("conf:%d", confQ)))
	h := hasher.Sum(nil)
	setBitFromHash(bitset, h)

	return nil
}

// Improved bit distribution using 16 bits instead of 8
func setBitFromHash(bitset []byte, h []byte) {
	if len(h) < 2 || len(bitset) == 0 {
		return
	}
	idx := (int(h[0])<<8 | int(h[1])) % DiversityBitsetSize
	bitset[idx/8] |= 1 << (idx % 8)
}

func (me *SimpleMutationEngine) average(values []float64) float64 {
	if len(values) == 0 {
		return 0.0
	}
	sum := 0.0
	for _, v := range values {
		sum += v
	}
	return sum / float64(len(values))
}

// Enhanced Jaccard similarity for base64 bitsets
func ComputeBitsetJaccardSimilarity(sig1, sig2 string) (float64, error) {
	// Parse version and extract base64 content
	parts1 := strings.Split(sig1, ":")
	parts2 := strings.Split(sig2, ":")

	if len(parts1) != 2 || len(parts2) != 2 {
		return ComputeJaccardSimilarity(sig1, sig2), nil // Fallback
	}
	if parts1[0] != parts2[0] {
		return 0.0, fmt.Errorf("diversity signature version mismatch: %s vs %s", parts1[0], parts2[0])
	}

	// Decode base64 bitsets
	b1, err := base64.StdEncoding.DecodeString(parts1[1])
	if err != nil {
		return 0.0, fmt.Errorf("failed to decode bitset1: %w", err)
	}
	b2, err := base64.StdEncoding.DecodeString(parts2[1])
	if err != nil {
		return 0.0, fmt.Errorf("failed to decode bitset2: %w", err)
	}
	if len(b1) != len(b2) {
		return 0.0, fmt.Errorf("bitset length mismatch: %d vs %d", len(b1), len(b2))
	}

	// Calculate intersection and union
	intersection := 0
	union := 0
	for i := 0; i < len(b1); i++ {
		ib := b1[i] & b2[i]
		ub := b1[i] | b2[i]
		intersection += popcount(ib)
		union += popcount(ub)
	}
	if union == 0 {
		return 1.0, nil // Both empty bitsets are identical
	}
	return float64(intersection) / float64(union), nil
}

// DiversityDistance computes diversity distance as 1 - similarity
func DiversityDistance(sig1, sig2 string) (float64, error) {
	similarity, err := ComputeBitsetJaccardSimilarity(sig1, sig2)
	if err != nil {
		return 0.0, err
	}
	return 1.0 - similarity, nil
}

// DiversitySimilarity is an alias for ComputeBitsetJaccardSimilarity for ergonomics
func DiversitySimilarity(sig1, sig2 string) (float64, error) {
	return ComputeBitsetJaccardSimilarity(sig1, sig2)
}

// Minimal fallback (exact-match-or-zero) when bitset form isn't present
func ComputeJaccardSimilarity(a, b string) float64 {
	if a == b && a != "" {
		return 1.0
	}
	return 0.0
}

// popcount counts the number of set bits in a byte
func popcount(b byte) int {
	count := 0
	for b != 0 {
		count += int(b & 1)
		b >>= 1
	}
	return count
}

func clampFloat64(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// DefaultMutationConfig returns sane defaults for production use
func DefaultMutationConfig() MutationConfig {
	return MutationConfig{
		ParamJitterProb:   0.6,  // 60% chance to jitter parameters
		ParamJitterSigma:  0.08, // 8% Gaussian jitter on hybrid weights
		ThresholdDelta:    0.05, // ±5% absolute jitter on confidence threshold
		FeatureToggleProb: 0.05, // 5% chance to flip rule features
		FeatureAddProb:    0.02, // 2% chance to add features
		FeatureRemoveProb: 0.02, // 2% chance to remove features
		WeightShuffleProb: 0.10, // 10% chance to shuffle hybrid weights
		MaxComplexityHint: 0,    // Disable complexity limits initially
	}
}