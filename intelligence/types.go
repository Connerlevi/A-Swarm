// Package intelligence - Core Types and Interfaces for A-SWARM Evolution
// This file only adds NEW types/interfaces not already defined in existing files
package intelligence

import (
    "context"
)

// ---------- NEW Interfaces for Evolution Loop ----------

// Evaluator scores antibody variants in combat scenarios
type Evaluator interface {
    // Evaluate runs variants through arena and returns fitness scores
    Evaluate(ctx context.Context, cohort []AntibodyVariant, environment string) (map[string]FitnessSummary, error)
}

// Store handles persistence of evolution state
type Store interface {
    // PutState persists population state snapshot
    PutState(ctx context.Context, state PopulationState) error

    // GetLatestState retrieves most recent population snapshot
    GetLatestState(ctx context.Context) (*PopulationState, error)

    // PutVariant stores an antibody variant
    PutVariant(ctx context.Context, variant AntibodyVariant) error

    // GetVariant retrieves a variant by ID
    GetVariant(ctx context.Context, id string) (*AntibodyVariant, error)

    // PutFitness stores fitness evaluation results
    PutFitness(ctx context.Context, variantID string, fitness FitnessSummary) error

    // ListLatestParents returns most recent parent pool
    ListLatestParents(ctx context.Context) ([]AntibodyVariant, error)
}

// ---------- Extended Types for Production Use ----------

// ExtendedFitnessSummary adds extra fields for production evaluation
// Note: Base FitnessSummary from evolution-contracts.go has:
// OverallFitness, ROC, P95LatencyMs, StabilityScore, SampleSize,
// ConfidenceLo, ConfidenceHi, BlastRadius
type ExtendedFitnessSummary struct {
    FitnessSummary // Embed base type

    // Detection performance (NOT in base FitnessSummary)
    TruePositives  int     `json:"true_positives"`
    FalsePositives int     `json:"false_positives"`
    TrueNegatives  int     `json:"true_negatives"`
    FalseNegatives int     `json:"false_negatives"`
    Precision      float64 `json:"precision"`
    Recall         float64 `json:"recall"`
    F1Score        float64 `json:"f1_score"`

    // Additional operational metrics (NOT in base)
    AvgLatencyMs float64 `json:"avg_latency_ms"`
    CPUCores     float64 `json:"cpu_cores"`
    MemoryMB     float64 `json:"memory_mb"`

    // Additional safety metrics (NOT in base)
    SafetyViolations int `json:"safety_violations"`

    // Metadata (NOT in base)
    VariantID      string  `json:"variant_id"`
    Environment    string  `json:"environment"`
    EvaluatedAt    int64   `json:"evaluated_at"` // Unix timestamp
    EvaluationTime float64 `json:"evaluation_time_seconds"`
}

// ConvertToBasicFitness extracts the base FitnessSummary
func (e ExtendedFitnessSummary) ConvertToBasicFitness() FitnessSummary {
    return e.FitnessSummary
}

// ---------- Evolution Loop Configuration ----------

// EvolutionConfig controls the main evolution loop
type EvolutionConfig struct {
    // Loop control
    MaxGenerations   int     `json:"max_generations"`
    StallGenerations int     `json:"stall_generations"` // Generations without improvement before intervention
    CohortSize       int     `json:"cohort_size"`
    ParentCount      int     `json:"parent_count"`

    // Rate limiting
    MaxCohortPerMinute int `json:"max_cohort_per_minute"`
    MaxEvaluationsPerEnv int `json:"max_evaluations_per_env"`

    // Safety
    KillSwitch bool `json:"kill_switch"` // Global flag to freeze evolution
    AllowedEnvironments []string `json:"allowed_environments"`
    AllowedNamespaces []string `json:"allowed_namespaces"`
    MaxRing int `json:"max_ring"` // Maximum enforcement ring allowed
}

// DefaultEvolutionConfig returns safe starting configuration
func DefaultEvolutionConfig() EvolutionConfig {
    return EvolutionConfig{
        MaxGenerations:   1000,
        StallGenerations: 20,
        CohortSize:       16,
        ParentCount:      8,
        MaxCohortPerMinute: 2,
        MaxEvaluationsPerEnv: 100,
        KillSwitch: false,
        AllowedEnvironments: []string{"shadow"},
        AllowedNamespaces: []string{"aswarm-arena"},
        MaxRing: 1, // Start with lowest impact actions only
    }
}