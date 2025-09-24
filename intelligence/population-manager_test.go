// Package intelligence - Population Manager Test Suite
package intelligence

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"
)

// TestCohortGeneration verifies that ProposeCohort generates valid variants
func TestCohortGeneration(t *testing.T) {
	config := DefaultPopulationConfig()
	engine := NewSimpleMutationEngine()
	pm := NewSimplePopulationManager(config, engine)

	// Create parent variants
	parents := []AntibodyVariant{
		{
			ID:       "parent-1",
			SpecHash: "hash1",
			Spec: AntibodySpec{
				Detector: DetectorSpec{
					Type: "rule",
					Rule: &RuleSpec{
						Pattern:    "test-pattern",
						EngineHint: "regex",
						Features:   map[string]string{"method": "POST"},
					},
				},
				Scope: ScopeSpec{
					ConfidenceThreshold: 0.8,
					Environments:        []string{"test"},
				},
			},
			DiversitySig: "v1:AAAA",
		},
		{
			ID:       "parent-2",
			SpecHash: "hash2",
			Spec: AntibodySpec{
				Detector: DetectorSpec{
					Type: "rule",
					Rule: &RuleSpec{
						Pattern:    "other-pattern",
						EngineHint: "exact",
						Features:   map[string]string{"path": "/api"},
					},
				},
				Scope: ScopeSpec{
					ConfidenceThreshold: 0.9,
					Environments:        []string{"prod"},
				},
			},
			DiversitySig: "v1:BBBB",
		},
	}

	ctx := context.Background()
	cohortSize := 10
	cohort, err := pm.ProposeCohort(ctx, parents, cohortSize, "staging")

	if err != nil {
		t.Fatalf("ProposeCohort failed: %v", err)
	}

	if len(cohort) == 0 {
		t.Fatal("Cohort generation returned no variants")
	}

	// Verify uniqueness of IDs
	idMap := make(map[string]bool)
	for _, variant := range cohort {
		if idMap[variant.ID] {
			t.Errorf("Duplicate variant ID found: %s", variant.ID)
		}
		idMap[variant.ID] = true

		// Check that variant was stored in manager
		if stored, exists := pm.variants[variant.ID]; !exists {
			t.Errorf("Variant %s not stored in manager", variant.ID)
		} else if stored == nil {
			t.Errorf("Stored variant %s is nil", variant.ID)
		}

		// Verify environment was added
		hasStaging := false
		for _, env := range variant.Spec.Scope.Environments {
			if env == "staging" {
				hasStaging = true
				break
			}
		}
		if !hasStaging {
			t.Errorf("Variant %s missing staging environment", variant.ID)
		}

		// Verify generation increment
		if variant.Generation != 1 {
			t.Errorf("Expected generation 1, got %d", variant.Generation)
		}

		// Verify parent linkage
		if len(variant.ParentIDs) == 0 {
			t.Errorf("Variant %s has no parents", variant.ID)
		}
	}
}

// TestDiversityCalculation verifies diversity metrics work correctly
func TestDiversityCalculation(t *testing.T) {
	config := DefaultPopulationConfig()
	engine := NewSimpleMutationEngine()
	pm := NewSimplePopulationManager(config, engine)

	// Add identical variants (should have low diversity)
	identicalSpec := AntibodySpec{
		Detector: DetectorSpec{
			Type: "rule",
			Rule: &RuleSpec{
				Pattern:  "same",
				Features: map[string]string{"a": "1"},
			},
		},
		Scope: ScopeSpec{ConfidenceThreshold: 0.5},
	}

	sig1, _ := engine.ComputeDiversitySignature(context.Background(), identicalSpec)

	for i := 0; i < 3; i++ {
		id := fmt.Sprintf("identical-%d", i)
		pm.variants[id] = &AntibodyVariant{
			ID:           id,
			Spec:         identicalSpec,
			DiversitySig: sig1,
		}
		pm.parentPool = append(pm.parentPool, id)
		pm.fitness[id] = &FitnessSummary{OverallFitness: 0.7}
	}

	// Calculate diversity for identical variants
	err := pm.updateDiversityMetrics()
	if err != nil {
		t.Fatalf("updateDiversityMetrics failed: %v", err)
	}

	lowDiversity := pm.diversity
	t.Logf("Diversity for identical variants: %.4f", lowDiversity)

	// Reset and add diverse variants
	pm.variants = make(map[string]*AntibodyVariant)
	pm.parentPool = []string{}

	specs := []AntibodySpec{
		{
			Detector: DetectorSpec{
				Type: "rule",
				Rule: &RuleSpec{
					Pattern:  "pattern1",
					Features: map[string]string{"method": "GET", "path": "/api"},
				},
			},
			Scope: ScopeSpec{ConfidenceThreshold: 0.6},
		},
		{
			Detector: DetectorSpec{
				Type: "model",
				Model: &ModelSpec{
					TrainingData: "dataset2",
					Features:     map[string]interface{}{"window": 100},
				},
			},
			Scope: ScopeSpec{ConfidenceThreshold: 0.7},
		},
		{
			Detector: DetectorSpec{
				Type: "hybrid",
				Hybrid: &HybridSpec{
					RuleWeight:  0.3,
					ModelWeight: 0.7,
				},
				Rule: &RuleSpec{
					Pattern:  "pattern3",
					Features: map[string]string{"status": "500"},
				},
			},
			Scope: ScopeSpec{ConfidenceThreshold: 0.85},
		},
	}

	for i, spec := range specs {
		id := fmt.Sprintf("diverse-%d", i)
		sig, _ := engine.ComputeDiversitySignature(context.Background(), spec)
		pm.variants[id] = &AntibodyVariant{
			ID:           id,
			Spec:         spec,
			DiversitySig: sig,
		}
		pm.parentPool = append(pm.parentPool, id)
		pm.fitness[id] = &FitnessSummary{OverallFitness: 0.7}
	}

	// Calculate diversity for diverse variants
	err = pm.updateDiversityMetrics()
	if err != nil {
		t.Fatalf("updateDiversityMetrics failed: %v", err)
	}

	highDiversity := pm.diversity
	t.Logf("Diversity for diverse variants: %.4f", highDiversity)

	// Diverse population should have higher diversity
	if highDiversity <= lowDiversity {
		t.Errorf("Diverse population (%.4f) should have higher diversity than identical population (%.4f)",
			highDiversity, lowDiversity)
	}
}

// TestGenerationIncrement verifies generation counter advances
func TestGenerationIncrement(t *testing.T) {
	config := DefaultPopulationConfig()
	engine := NewSimpleMutationEngine()
	pm := NewSimplePopulationManager(config, engine)

	// Initial generation should be 0
	if pm.generation != 0 {
		t.Errorf("Initial generation should be 0, got %d", pm.generation)
	}

	// Add a variant and ingest results
	pm.variants["test-1"] = &AntibodyVariant{
		ID:   "test-1",
		Spec: AntibodySpec{},
	}

	results := map[string]FitnessSummary{
		"test-1": {
			OverallFitness: 0.8,
			P95LatencyMs:   50,
			StabilityScore: 0.9,
			ConfidenceLo:   0.85,
			ConfidenceHi:   0.95,
		},
	}

	ctx := context.Background()
	err := pm.IngestResults(ctx, results)
	if err != nil {
		t.Fatalf("IngestResults failed: %v", err)
	}

	// Generation should increment after ingesting results
	if pm.generation != 1 {
		t.Errorf("Generation should be 1 after IngestResults, got %d", pm.generation)
	}

	// Ingest again
	err = pm.IngestResults(ctx, results)
	if err != nil {
		t.Fatalf("Second IngestResults failed: %v", err)
	}

	if pm.generation != 2 {
		t.Errorf("Generation should be 2 after second IngestResults, got %d", pm.generation)
	}
}

// TestTournamentSelection verifies tournament selection with diversity
func TestTournamentSelection(t *testing.T) {
	config := DefaultPopulationConfig()
	config.DiversityLambda = 0 // No diversity penalty initially
	engine := NewSimpleMutationEngine()
	pm := NewSimplePopulationManager(config, engine)

	// Create variants with different fitness scores
	variants := []struct {
		id      string
		fitness float64
		sig     string
	}{
		{"high-fitness", 0.95, "v1:AAAA"},
		{"medium-fitness", 0.70, "v1:BBBB"},
		{"low-fitness", 0.40, "v1:CCCC"},
	}

	for _, v := range variants {
		pm.variants[v.id] = &AntibodyVariant{
			ID:           v.id,
			DiversitySig: v.sig,
			Spec:         AntibodySpec{},
		}
		pm.fitness[v.id] = &FitnessSummary{
			OverallFitness: v.fitness,
		}
		pm.parentPool = append(pm.parentPool, v.id)
	}

	// With no diversity penalty, high fitness should win most tournaments
	ctx := context.Background()
	parents, err := pm.SelectNextParents(ctx, 10)
	if err != nil {
		t.Fatalf("SelectNextParents failed: %v", err)
	}

	highFitnessCount := 0
	for _, parent := range parents {
		if parent.ID == "high-fitness" {
			highFitnessCount++
		}
	}

	if highFitnessCount < 5 {
		t.Errorf("High fitness variant should win most tournaments, but only won %d/10", highFitnessCount)
	}

	// Now test with diversity penalty
	config.DiversityLambda = 0.5
	pm.config = config

	// Add a clone of high-fitness
	pm.variants["high-clone"] = &AntibodyVariant{
		ID:           "high-clone",
		DiversitySig: "v1:AAAA", // Same signature as high-fitness
		Spec:         AntibodySpec{},
	}
	pm.fitness["high-clone"] = &FitnessSummary{
		OverallFitness: 0.94, // Slightly lower than original
	}
	pm.parentPool = append(pm.parentPool, "high-clone")

	// With diversity penalty, the clone should be penalized
	parents2, err := pm.SelectNextParents(ctx, 10)
	if err != nil {
		t.Fatalf("SelectNextParents with diversity failed: %v", err)
	}

	// Check for unique parents
	uniqueIDs := make(map[string]int)
	for _, parent := range parents2 {
		uniqueIDs[parent.ID]++
	}

	// Should have multiple unique parents due to diversity pressure
	if len(uniqueIDs) < 2 {
		t.Errorf("Expected diverse parent selection, got only %d unique parents", len(uniqueIDs))
	}

	t.Logf("Parent distribution with diversity penalty: %v", uniqueIDs)
}

// TestConcurrentMutations verifies thread-safety of population manager
func TestConcurrentMutations(t *testing.T) {
	config := DefaultPopulationConfig()
	engine := NewSimpleMutationEngine()
	pm := NewSimplePopulationManager(config, engine)

	// Create initial parents
	baseSpec := AntibodySpec{
		Detector: DetectorSpec{
			Type: "rule",
			Rule: &RuleSpec{
				Pattern:  "base",
				Features: map[string]string{"a": "1"},
			},
		},
		Scope: ScopeSpec{ConfidenceThreshold: 0.5},
	}

	parents := []AntibodyVariant{
		{ID: "parent-1", Spec: baseSpec},
	}

	ctx := context.Background()
	var wg sync.WaitGroup
	numGoroutines := 10
	cohortsPerGoroutine := 5

	// Track all generated IDs to check for uniqueness
	idChan := make(chan string, numGoroutines*cohortsPerGoroutine*10)

	for i := 0; i < numGoroutines; i++ {
		wg.Add(1)
		go func(routine int) {
			defer wg.Done()

			for j := 0; j < cohortsPerGoroutine; j++ {
				cohort, err := pm.ProposeCohort(ctx, parents, 2, "test")
				if err != nil {
					t.Errorf("Goroutine %d: ProposeCohort failed: %v", routine, err)
					continue
				}

				for _, v := range cohort {
					idChan <- v.ID
				}
			}
		}(i)
	}

	wg.Wait()
	close(idChan)

	// Check for duplicate IDs
	idMap := make(map[string]int)
	for id := range idChan {
		idMap[id]++
	}

	duplicates := 0
	for id, count := range idMap {
		if count > 1 {
			t.Errorf("Duplicate ID generated: %s (count: %d)", id, count)
			duplicates++
		}
	}

	if duplicates > 0 {
		t.Fatalf("Found %d duplicate IDs in concurrent execution", duplicates)
	}

	t.Logf("Successfully generated %d unique variants across %d goroutines",
		len(idMap), numGoroutines)
}

// TestConfigValidation verifies config validation works
func TestConfigValidation(t *testing.T) {
	config := DefaultPopulationConfig()
	engine := NewSimpleMutationEngine()
	pm := NewSimplePopulationManager(config, engine)

	ctx := context.Background()

	// Test invalid elite size
	invalidConfig := config
	invalidConfig.EliteSize = 100 // Larger than ShadowPoolSize
	err := pm.UpdateConfig(ctx, invalidConfig)
	if err == nil || !strings.Contains(err.Error(), "elite size") {
		t.Errorf("Expected elite size error, got: %v", err)
	}

	// Test invalid mutation rate
	invalidConfig = config
	invalidConfig.MutationRate = 1.5
	err = pm.UpdateConfig(ctx, invalidConfig)
	if err == nil || !strings.Contains(err.Error(), "mutation rate") {
		t.Errorf("Expected mutation rate error, got: %v", err)
	}

	// Test invalid diversity lambda
	invalidConfig = config
	invalidConfig.DiversityLambda = -0.5
	err = pm.UpdateConfig(ctx, invalidConfig)
	if err == nil || !strings.Contains(err.Error(), "diversity lambda") {
		t.Errorf("Expected diversity lambda error, got: %v", err)
	}

	// Test invalid crossover rate
	invalidConfig = config
	invalidConfig.CrossoverRate = -0.1
	err = pm.UpdateConfig(ctx, invalidConfig)
	if err == nil || !strings.Contains(err.Error(), "crossover rate") {
		t.Errorf("Expected crossover rate error, got: %v", err)
	}

	// Valid config should succeed
	validConfig := config
	validConfig.MutationRate = 0.25
	err = pm.UpdateConfig(ctx, validConfig)
	if err != nil {
		t.Errorf("Valid config update failed: %v", err)
	}

	if pm.config.MutationRate != 0.25 {
		t.Errorf("Config not updated, expected MutationRate 0.25, got %.2f", pm.config.MutationRate)
	}
}

// TestSnapshotAndRestore verifies state persistence
func TestSnapshotAndRestore(t *testing.T) {
	config := DefaultPopulationConfig()
	engine := NewSimpleMutationEngine()
	pm := NewSimplePopulationManager(config, engine)

	// Create some state
	pm.generation = 5
	pm.diversity = 0.75
	pm.bestFitness = 0.92

	variants := []string{"v1", "v2", "v3"}
	for _, id := range variants {
		pm.variants[id] = &AntibodyVariant{
			ID:       id,
			SpecHash: "hash-" + id,
		}
		pm.parentPool = append(pm.parentPool, id)
	}

	pm.archivePool = []string{"v1", "v2"}
	pm.bestByGen = []float64{0.5, 0.6, 0.7, 0.8, 0.92}

	ctx := context.Background()
	snapshot, err := pm.Snapshot(ctx)
	if err != nil {
		t.Fatalf("Snapshot failed: %v", err)
	}

	// Verify snapshot contents
	if snapshot.Generation != 5 {
		t.Errorf("Snapshot generation mismatch: expected 5, got %d", snapshot.Generation)
	}

	if snapshot.Diversity != 0.75 {
		t.Errorf("Snapshot diversity mismatch: expected 0.75, got %.2f", snapshot.Diversity)
	}

	if snapshot.BestFitness != 0.92 {
		t.Errorf("Snapshot best fitness mismatch: expected 0.92, got %.2f", snapshot.BestFitness)
	}

	if len(snapshot.ParentPool) != 3 {
		t.Errorf("Snapshot parent pool size mismatch: expected 3, got %d", len(snapshot.ParentPool))
	}

	if len(snapshot.ArchivePool) != 2 {
		t.Errorf("Snapshot archive pool size mismatch: expected 2, got %d", len(snapshot.ArchivePool))
	}

	if len(snapshot.BestFitnessByGen) != 5 {
		t.Errorf("Snapshot fitness history size mismatch: expected 5, got %d", len(snapshot.BestFitnessByGen))
	}

	// Check spec hashes
	for _, id := range variants {
		expectedHash := "hash-" + id
		if snapshot.SpecHashes[id] != expectedHash {
			t.Errorf("Spec hash mismatch for %s: expected %s, got %s",
				id, expectedHash, snapshot.SpecHashes[id])
		}
	}
}

// BenchmarkProposeCohort measures cohort generation performance
func BenchmarkProposeCohort(b *testing.B) {
	config := DefaultPopulationConfig()
	engine := NewSimpleMutationEngine()
	pm := NewSimplePopulationManager(config, engine)

	parents := []AntibodyVariant{
		{
			ID: "parent-1",
			Spec: AntibodySpec{
				Detector: DetectorSpec{
					Type: "rule",
					Rule: &RuleSpec{
						Pattern:  "bench",
						Features: map[string]string{"a": "1", "b": "2"},
					},
				},
				Scope: ScopeSpec{ConfidenceThreshold: 0.5},
			},
		},
	}

	ctx := context.Background()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := pm.ProposeCohort(ctx, parents, 10, "bench")
		if err != nil {
			b.Fatalf("ProposeCohort failed: %v", err)
		}
	}
}

// TestRaceConditions runs the race detector on critical paths
func TestRaceConditions(t *testing.T) {
	if testing.Short() {
		t.Skip("Skipping race test in short mode")
	}

	config := DefaultPopulationConfig()
	engine := NewSimpleMutationEngine()
	pm := NewSimplePopulationManager(config, engine)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	// Create initial population
	baseSpec := AntibodySpec{
		Detector: DetectorSpec{
			Type: "rule",
			Rule: &RuleSpec{
				Pattern:  "race",
				Features: map[string]string{"test": "1"},
			},
		},
		Scope: ScopeSpec{ConfidenceThreshold: 0.5},
	}

	parent := AntibodyVariant{ID: "race-parent", Spec: baseSpec}

	var wg sync.WaitGroup

	// Writer goroutines - propose cohorts
	for i := 0; i < 3; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 10; j++ {
				select {
				case <-ctx.Done():
					return
				default:
				}

				_, _ = pm.ProposeCohort(ctx, []AntibodyVariant{parent}, 3, "race-test")
				time.Sleep(10 * time.Millisecond)
			}
		}()
	}

	// Reader goroutines - select parents and get specs
	for i := 0; i < 3; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 10; j++ {
				select {
				case <-ctx.Done():
					return
				default:
				}

				// Try to select parents (may fail if pool empty)
				_, _ = pm.SelectNextParents(ctx, 2)

				// Get diversity
				_, _ = pm.GetDiversityIndex(ctx)

				// Take snapshot
				_, _ = pm.Snapshot(ctx)

				time.Sleep(10 * time.Millisecond)
			}
		}()
	}

	// Updater goroutine - ingest results
	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < 10; i++ {
			select {
			case <-ctx.Done():
				return
			default:
			}

			// Build results for any known variants
			results := make(map[string]FitnessSummary)
			pm.mu.RLock()
			for id := range pm.variants {
				results[id] = FitnessSummary{
					OverallFitness: 0.5 + float64(i)*0.05,
					P95LatencyMs:   100,
					StabilityScore: 0.8,
				}
				if len(results) >= 3 {
					break
				}
			}
			pm.mu.RUnlock()

			if len(results) > 0 {
				_ = pm.IngestResults(ctx, results)
			}
			time.Sleep(20 * time.Millisecond)
		}
	}()

	wg.Wait()
}