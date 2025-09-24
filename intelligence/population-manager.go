// Package intelligence - A-SWARM Population Manager Implementation
// Orchestrates antibody evolution with diversity-aware selection

package intelligence

import (
	"context"
	"crypto/sha256"
	"fmt"
	"math/rand"
	"sort"
	"strings"
	"sync"
	"time"
)

// SimplePopulationManager implements PopulationManager with tournament selection and diversity awareness
type SimplePopulationManager struct {
	mu sync.RWMutex

	// Population pools
	variants     map[string]*AntibodyVariant // variantID -> variant
	fitness      map[string]*FitnessSummary  // variantID -> fitness
	parentPool   []string                    // Current breeding population (variant IDs)
	archivePool  []string                    // Historical best performers
	activePoolsByPhase map[string][]string   // phase -> variant IDs

	// Configuration
	config PopulationConfig
	engine MutationEngine

	// State tracking
	generation    int
	diversity     float64
	bestFitness   float64
	bestByGen     []float64 // Ring buffer for trend tracking
	lastUpdated   int64

	// Random number generator with seed for determinism
	rng   *rand.Rand
	rngMu sync.Mutex // Separate mutex for thread-safe RNG
}

// NewSimplePopulationManager creates a new population manager with the given configuration
func NewSimplePopulationManager(config PopulationConfig, engine MutationEngine) *SimplePopulationManager {
	// Use deterministic seed for reproducible evolution
	seed := time.Now().UnixNano()

	pm := &SimplePopulationManager{
		variants:           make(map[string]*AntibodyVariant),
		fitness:           make(map[string]*FitnessSummary),
		parentPool:        make([]string, 0, config.ShadowPoolSize),
		archivePool:       make([]string, 0, config.EliteSize*3), // Archive larger than elite
		activePoolsByPhase: make(map[string][]string),
		config:            config,
		engine:            engine,
		generation:        0,
		diversity:         0.0,
		bestFitness:       0.0,
		bestByGen:         make([]float64, 0, 50), // Ring buffer for 50 generations
		lastUpdated:       time.Now().Unix(),
		rng:               rand.New(rand.NewSource(seed)),
	}

	// Initialize phase pools
	pm.activePoolsByPhase["shadow"] = make([]string, 0, config.ShadowPoolSize)
	pm.activePoolsByPhase["staged"] = make([]string, 0, config.StagedPoolSize)

	return pm
}

// Thread-safe RNG helpers
func (pm *SimplePopulationManager) rndFloat64() float64 {
	pm.rngMu.Lock()
	defer pm.rngMu.Unlock()
	return pm.rng.Float64()
}

func (pm *SimplePopulationManager) rndIntn(n int) int {
	pm.rngMu.Lock()
	defer pm.rngMu.Unlock()
	return pm.rng.Intn(n)
}

// ProposeCohort generates new candidate variants from parent specs
func (pm *SimplePopulationManager) ProposeCohort(ctx context.Context, parents []AntibodyVariant, size int, environment string) ([]AntibodyVariant, error) {
	pm.mu.Lock()
	defer pm.mu.Unlock()

	if len(parents) == 0 {
		return nil, fmt.Errorf("no parents provided for cohort generation")
	}

	var cohort []AntibodyVariant
	mutationConfig := DefaultMutationConfig()

	// Use CrossoverRate from config if available, default to 0.2
	crossoverRate := 0.2
	if pm.config.CrossoverRate > 0 {
		crossoverRate = pm.config.CrossoverRate
	}

	for i := 0; i < size; i++ {
		// Check context cancellation
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}

		var child AntibodyVariant
		var err error

		// Decide on crossover vs mutation
		doCrossover := len(parents) > 1 && pm.rndFloat64() < crossoverRate
		if doCrossover && len(parents) < 2 {
			doCrossover = false // Fallback to mutation if insufficient parents
		}

		if doCrossover {
			// Crossover - select 2 random parents
			p1 := parents[pm.rndIntn(len(parents))]
			p2 := parents[pm.rndIntn(len(parents))]

			// Ensure different parents
			attempts := 0
			for p1.ID == p2.ID && attempts < 10 && len(parents) > 1 {
				p2 = parents[pm.rndIntn(len(parents))]
				attempts++
			}

			// Bail to mutation if we can't find different parents
			if p1.ID == p2.ID {
				doCrossover = false
			} else {
				childSpec, err := pm.engine.CrossOver(ctx, []AntibodySpec{p1.Spec, p2.Spec}, mutationConfig)
				if err != nil {
					continue // Skip failed crossover
				}

				child = AntibodyVariant{
					ID:           generateVariantID("crossover", pm.generation, i, p1.ID, p2.ID),
					SpecHash:     ComputeSpecHash(childSpec),
					ParentIDs:    []string{p1.ID, p2.ID},
					Generation:   pm.generation + 1,
					Spec:         childSpec,
					ProposedBy:   fmt.Sprintf("population-manager@gen-%d", pm.generation),
					CreatedAt:    time.Now().Unix(),
				}
			}
		}

		if !doCrossover {
			// Mutation - select single random parent
			parent := parents[pm.rndIntn(len(parents))]

			childSpec, err := pm.engine.Mutate(ctx, parent.Spec, mutationConfig)
			if err != nil {
				continue // Skip failed mutation
			}

			child = AntibodyVariant{
				ID:           generateVariantID("mutation", pm.generation, i, parent.ID),
				SpecHash:     ComputeSpecHash(childSpec),
				ParentIDs:    []string{parent.ID},
				Generation:   pm.generation + 1,
				Spec:         childSpec,
				ProposedBy:   fmt.Sprintf("population-manager@gen-%d", pm.generation),
				CreatedAt:    time.Now().Unix(),
			}
		}

		// Add environment to spec if provided and not already present
		if environment != "" {
			hasEnv := false
			for _, e := range child.Spec.Scope.Environments {
				if e == environment {
					hasEnv = true
					break
				}
			}
			if !hasEnv {
				child.Spec.Scope.Environments = append(child.Spec.Scope.Environments, environment)
			}
		}

		// Validate the generated spec with config
		if err = pm.engine.ValidateSpec(ctx, child.Spec, mutationConfig); err != nil {
			continue // Skip invalid specs
		}

		// Generate diversity signature
		if diversitySig, err := pm.engine.ComputeDiversitySignature(ctx, child.Spec); err == nil {
			child.DiversitySig = diversitySig
		}

		cohort = append(cohort, child)

		// Store in variants map - FIX: create new pointer to avoid loop variable issue
		c := child
		pm.variants[c.ID] = &c
	}

	if len(cohort) == 0 {
		return nil, fmt.Errorf("failed to generate any valid cohort members")
	}

	return cohort, nil
}

// IngestResults processes fitness evaluation results and updates population state
func (pm *SimplePopulationManager) IngestResults(ctx context.Context, results map[string]FitnessSummary) error {
	pm.mu.Lock()
	defer pm.mu.Unlock()

	// Update fitness summaries
	for variantID, summary := range results {
		if _, exists := pm.variants[variantID]; !exists {
			continue // Skip unknown variants
		}

		// Store fitness summary
		fitnessClone := summary
		pm.fitness[variantID] = &fitnessClone

		// Update best fitness tracking
		overallFitness := ComputeOverallFitness(summary)
		if overallFitness > pm.bestFitness {
			pm.bestFitness = overallFitness
		}
	}

	// Update diversity metrics
	if err := pm.updateDiversityMetrics(); err != nil {
		return fmt.Errorf("failed to update diversity metrics: %w", err)
	}

	// Promote high-performing variants to parent pool
	if err := pm.updateParentPool(); err != nil {
		return fmt.Errorf("failed to update parent pool: %w", err)
	}

	// Increment generation after pool updates
	pm.generation++
	pm.lastUpdated = time.Now().Unix()

	return nil
}

// SelectNextParents chooses breeding population for next generation using tournament selection
func (pm *SimplePopulationManager) SelectNextParents(ctx context.Context, k int) ([]AntibodyVariant, error) {
	pm.mu.RLock()
	defer pm.mu.RUnlock()

	if len(pm.parentPool) == 0 {
		return nil, fmt.Errorf("no variants available for parent selection")
	}

	// Tournament selection with diversity penalty
	var parents []AntibodyVariant
	tournamentSize := 5 // Default tournament size

	// Cap tournament size by available parents
	if len(pm.parentPool) < tournamentSize {
		tournamentSize = len(pm.parentPool)
	}

	// Track selected variants to avoid duplicates
	seen := make(map[string]struct{})
	maxAttempts := k * 3 // Prevent infinite loop

	for len(parents) < k && maxAttempts > 0 {
		// Check context cancellation
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}

		// Run tournament
		tournament := pm.selectTournamentCandidates(tournamentSize)
		winner := pm.runTournamentWithDiversity(tournament)

		if winner == "" {
			maxAttempts--
			continue
		}

		// Skip if already selected
		if _, alreadySelected := seen[winner]; alreadySelected {
			maxAttempts--
			continue
		}

		if variant, exists := pm.variants[winner]; exists {
			parents = append(parents, *variant)
			seen[winner] = struct{}{}
		}

		maxAttempts--
	}

	if len(parents) == 0 {
		return nil, fmt.Errorf("tournament selection failed to produce parents")
	}

	return parents, nil
}

// GetSpecs retrieves current antibody specs by IDs
func (pm *SimplePopulationManager) GetSpecs(ctx context.Context, variantIDs []string) ([]AntibodyVariant, error) {
	pm.mu.RLock()
	defer pm.mu.RUnlock()

	var variants []AntibodyVariant
	for _, id := range variantIDs {
		if variant, exists := pm.variants[id]; exists {
			variants = append(variants, *variant)
		}
	}

	return variants, nil
}

// Snapshot returns current population state for persistence/observability
func (pm *SimplePopulationManager) Snapshot(ctx context.Context) (PopulationState, error) {
	pm.mu.RLock()
	defer pm.mu.RUnlock()

	// Build spec hashes map
	specHashes := make(map[string]string)
	for id, variant := range pm.variants {
		specHashes[id] = variant.SpecHash
	}

	// Copy active pools
	activePools := make(map[string][]string)
	for phase, pool := range pm.activePoolsByPhase {
		activePools[phase] = make([]string, len(pool))
		copy(activePools[phase], pool)
	}

	// Copy parent and archive pools
	parentPool := make([]string, len(pm.parentPool))
	copy(parentPool, pm.parentPool)
	archivePool := make([]string, len(pm.archivePool))
	copy(archivePool, pm.archivePool)

	// Copy best fitness history
	bestByGen := make([]float64, len(pm.bestByGen))
	copy(bestByGen, pm.bestByGen)

	return PopulationState{
		Generation:       pm.generation,
		ActivePools:      activePools,
		ParentPool:       parentPool,
		ArchivePool:      archivePool,
		SpecHashes:       specHashes,
		Diversity:        pm.diversity,
		BestFitness:      pm.bestFitness,
		BestFitnessByGen: bestByGen,
		Params:           pm.config,
		LastUpdated:      pm.lastUpdated,
	}, nil
}

// UpdateConfig applies new population parameters for runtime tuning
func (pm *SimplePopulationManager) UpdateConfig(ctx context.Context, config PopulationConfig) error {
	pm.mu.Lock()
	defer pm.mu.Unlock()

	// Validate config
	if config.EliteSize > config.ShadowPoolSize {
		return fmt.Errorf("elite size (%d) cannot exceed shadow pool size (%d)", config.EliteSize, config.ShadowPoolSize)
	}

	if config.MutationRate < 0 || config.MutationRate > 1 {
		return fmt.Errorf("mutation rate must be in [0,1], got %f", config.MutationRate)
	}

	if config.DiversityLambda < 0 {
		return fmt.Errorf("diversity lambda must be non-negative, got %f", config.DiversityLambda)
	}

	if config.CrossoverRate < 0 || config.CrossoverRate > 1 {
		return fmt.Errorf("crossover rate must be in [0,1], got %f", config.CrossoverRate)
	}

	pm.config = config
	pm.lastUpdated = time.Now().Unix()

	return nil
}

// GetDiversityIndex computes current population diversity metric
func (pm *SimplePopulationManager) GetDiversityIndex(ctx context.Context) (float64, error) {
	pm.mu.RLock()
	defer pm.mu.RUnlock()

	return pm.diversity, nil
}

// Private helper methods

// updateDiversityMetrics calculates population diversity using pairwise Jaccard similarity
func (pm *SimplePopulationManager) updateDiversityMetrics() error {
	if len(pm.parentPool) < 2 {
		pm.diversity = 1.0 // Maximum diversity for small populations
		return nil
	}

	var similarities []float64
	for i := 0; i < len(pm.parentPool); i++ {
		for j := i + 1; j < len(pm.parentPool); j++ {
			variant1 := pm.variants[pm.parentPool[i]]
			variant2 := pm.variants[pm.parentPool[j]]

			if variant1 != nil && variant2 != nil {
				// Use mutation engine's ComputeBitsetJaccardSimilarity
				sim := DiversitySimilarity(variant1.DiversitySig, variant2.DiversitySig)
				similarities = append(similarities, sim)
			}
		}
	}

	if len(similarities) == 0 {
		pm.diversity = 1.0
		return nil
	}

	// Compute average similarity, then diversity = 1 - avgSimilarity
	var totalSimilarity float64
	for _, sim := range similarities {
		totalSimilarity += sim
	}
	avgSimilarity := totalSimilarity / float64(len(similarities))
	pm.diversity = 1.0 - avgSimilarity

	return nil
}

// updateParentPool promotes high-performing variants to breeding population
func (pm *SimplePopulationManager) updateParentPool() error {
	// Collect all variants with fitness data
	type candidateVariant struct {
		id      string
		fitness float64
		variant *AntibodyVariant
	}

	var candidates []candidateVariant
	for id, fitnessSum := range pm.fitness {
		if variant, exists := pm.variants[id]; exists {
			candidates = append(candidates, candidateVariant{
				id:      id,
				fitness: ComputeOverallFitness(*fitnessSum),
				variant: variant,
			})
		}
	}

	if len(candidates) == 0 {
		return nil // No candidates available
	}

	// Sort by fitness (descending)
	sort.Slice(candidates, func(i, j int) bool {
		return candidates[i].fitness > candidates[j].fitness
	})

	// Update parent pool with top performers
	pm.parentPool = pm.parentPool[:0] // Clear existing
	maxParents := pm.config.ShadowPoolSize
	for i := 0; i < len(candidates) && i < maxParents; i++ {
		pm.parentPool = append(pm.parentPool, candidates[i].id)
	}

	// Update archive pool with elite performers (use set to avoid duplicates)
	archiveSet := make(map[string]struct{})
	for _, id := range pm.archivePool {
		archiveSet[id] = struct{}{}
	}

	maxElite := pm.config.EliteSize
	for i := 0; i < len(candidates) && i < maxElite; i++ {
		archiveSet[candidates[i].id] = struct{}{}
	}

	// Convert back to slice, maintaining size limit
	pm.archivePool = make([]string, 0, len(archiveSet))
	for id := range archiveSet {
		pm.archivePool = append(pm.archivePool, id)
		if len(pm.archivePool) >= pm.config.EliteSize*3 {
			break // Maintain archive size limit
		}
	}

	// Track best fitness by generation
	if len(candidates) > 0 {
		pm.bestByGen = append(pm.bestByGen, candidates[0].fitness)

		// Maintain ring buffer of 50 generations
		if len(pm.bestByGen) > 50 {
			pm.bestByGen = pm.bestByGen[1:]
		}
	}

	// Update active phase pools
	shadowSize := min(len(pm.parentPool), pm.config.ShadowPoolSize)
	pm.activePoolsByPhase["shadow"] = pm.parentPool[:shadowSize]

	stagedSize := min(len(pm.archivePool), pm.config.StagedPoolSize)
	pm.activePoolsByPhase["staged"] = pm.archivePool[:stagedSize]

	return nil
}

// selectTournamentCandidates randomly selects candidates for tournament
func (pm *SimplePopulationManager) selectTournamentCandidates(size int) []string {
	if len(pm.parentPool) <= size {
		// Return all available candidates
		candidates := make([]string, len(pm.parentPool))
		copy(candidates, pm.parentPool)
		return candidates
	}

	// Random selection without replacement
	candidates := make([]string, 0, size)
	used := make(map[int]bool)

	for len(candidates) < size {
		idx := pm.rndIntn(len(pm.parentPool))
		if !used[idx] {
			candidates = append(candidates, pm.parentPool[idx])
			used[idx] = true
		}
	}

	return candidates
}

// runTournamentWithDiversity selects tournament winner with diversity penalty
func (pm *SimplePopulationManager) runTournamentWithDiversity(candidates []string) string {
	if len(candidates) == 0 {
		return ""
	}

	if len(candidates) == 1 {
		return candidates[0]
	}

	bestScore := -1.0
	winner := candidates[0]

	for _, candidateID := range candidates {
		variant := pm.variants[candidateID]
		fitnessSum := pm.fitness[candidateID]

		if variant == nil || fitnessSum == nil {
			continue
		}

		// Base fitness score
		baseScore := ComputeOverallFitness(*fitnessSum)

		// Apply diversity penalty
		diversityPenalty := pm.computeDiversityPenalty(variant)
		finalScore := baseScore - pm.config.DiversityLambda*diversityPenalty

		if finalScore > bestScore {
			bestScore = finalScore
			winner = candidateID
		}
	}

	return winner
}

// computeDiversityPenalty calculates penalty based on similarity to existing parents
func (pm *SimplePopulationManager) computeDiversityPenalty(candidate *AntibodyVariant) float64 {
	if candidate.DiversitySig == "" || len(pm.parentPool) <= 1 {
		return 0.0 // No penalty for first parent or missing signatures
	}

	var maxSimilarity float64
	for _, parentID := range pm.parentPool {
		if parentID == candidate.ID {
			continue // Skip self-comparison
		}

		parent := pm.variants[parentID]
		if parent != nil && parent.DiversitySig != "" {
			// Use mutation engine's DiversitySimilarity
			sim := DiversitySimilarity(candidate.DiversitySig, parent.DiversitySig)
			if sim > maxSimilarity {
				maxSimilarity = sim
			}
		}
	}

	return maxSimilarity
}

// generateVariantID creates a deterministic variant ID for replayability
func generateVariantID(kind string, generation, index int, parents ...string) string {
	base := fmt.Sprintf("%s|g=%d|i=%d|p=%s", kind, generation, index, strings.Join(parents, ","))
	h := sha256.Sum256([]byte(base))
	return fmt.Sprintf("variant-%x", h[:8])
}

// min returns the minimum of two integers
func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}