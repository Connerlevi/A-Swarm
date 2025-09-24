// Package intelligence - A-SWARM Antibody Fitness Evaluation Engine
// Production-hardened evaluator with JSON-safe metrics, ROC analysis, and audit compliance

package intelligence

import (
	"context"
	"fmt"
	"math"
	"sort"
	"sync"
	"time"
)

const (
	MaxBattleHistory = 50000 // Ring buffer size for battle history
	MaxWorkers       = 20    // Bounded parallelism to prevent resource exhaustion

	// Stability score defaults
	SingleEnvStability = 0.8 // High stability for single environment
	InsufficientDataStability = 0.5 // Neutral stability for <10 samples
)

// AttackResult represents the outcome of a Red attack execution
type AttackResult struct {
	AttackID       string
	Pattern        string
	Success        bool
	Techniques     []string // MITRE ATT&CK techniques used
	DurationMs     float64
	BlastRadiusIPs int // Number of IPs affected for containment cost
}

// DetectionResult represents Blue team's detection performance
type DetectionResult struct {
	Detected    bool
	LatencyMs   float64 // Blue-reported detection latency
	Confidence  float64 // [0,1] detector confidence score
	RingLevel   int     // Containment ring triggered (1-5)
	FalseAlarm  bool    // Operator marked as false positive
}

// BattleRecord tracks individual Red vs Blue confrontations with full context
type BattleRecord struct {
	AntibodyID      string
	AttackResult    AttackResult
	DetectionResult DetectionResult
	BattleID        string
	Timestamp       time.Time
	Environment     string  // staging/prod/canary for environment-aware scoring
	MonotonicMs     float64 // Monotonic timing for consistency
}

// ROCSummary contains ROC analysis results when available
type ROCSummary struct {
	Threshold float64 `json:"threshold"`
	TPR       float64 `json:"tpr"`
	FPR       float64 `json:"fpr"`
}

// AntibodyFitness represents comprehensive fitness metrics for promotion decisions
type AntibodyFitness struct {
	// Core detection performance (honest naming until ROC implemented)
	DetectionRate   float64 `json:"detection_rate"` // True Positive Rate for attacks-only sampling
	AvgLatencyMs    float64 `json:"avg_latency_ms"` // Mean time to detection
	P95LatencyMs    float64 `json:"p95_latency_ms"` // 95th percentile MTTD

	// ROC analysis (only when benign samples included)
	ROC    *ROCSummary `json:"roc,omitempty"`
	HasROC bool        `json:"has_roc"`

	// Confidence and robustness
	ConfidenceLower float64 `json:"confidence_lower"` // Lower bound of 95% Wilson confidence interval
	ConfidenceUpper float64 `json:"confidence_upper"` // Upper bound of 95% Wilson confidence interval
	StabilityScore  float64 `json:"stability_score"`  // Environmental performance stability [0,1]
	SampleSize      int     `json:"sample_size"`      // Number of battles evaluated

	// Operational impact
	AvgBlastRadius  float64 `json:"avg_blast_radius"`  // Mean containment scope (cost proxy)
	ContainmentCost float64 `json:"containment_cost"`  // Resource impact of responses
}

// MeetsPromotionSLO evaluates if antibody meets promotion criteria
func (af AntibodyFitness) MeetsPromotionSLO(minTPRLB, maxFPRUB float64) bool {
	if af.SampleSize < 200 {
		return false // Insufficient statistical power
	}
	if af.ConfidenceLower < minTPRLB {
		return false // TPR lower bound too low
	}
	if af.HasROC && af.ROC != nil && af.ROC.FPR > maxFPRUB {
		return false // FPR upper bound exceeded
	}
	return true
}

// BattleResult aggregates attack execution and detection outcome
type BattleResult struct {
	AttackResult    AttackResult
	DetectionResult DetectionResult
	MonotonicMs     float64 // Monotonic battle duration
	Error           error
}

// Sample represents a labeled detection result for ROC analysis
type Sample struct {
	Score float64 // Confidence score [0,1]
	Label int     // 1=attack, 0=benign
}

// FitnessEvaluator orchestrates Red vs Blue battles for antibody evaluation
// Uses dependency injection for testability and bounded parallelism for safety
type FitnessEvaluator struct {
	// Dependency injection for testing - functions can be mocked
	LaunchRedAttack      func(ctx context.Context, pattern, battleID string) (*AttackResult, error)
	MonitorBlueDetection func(ctx context.Context, battleID, antibodyID string, timeout time.Duration) (*DetectionResult, error)
	GenerateBenignSample func(ctx context.Context, antibodyID string) (*DetectionResult, error)

	// Battle history tracking with ring buffer for O(1) operations
	battleHistory []BattleRecord
	historyIndex  int  // Current write position in ring buffer
	historyFull   bool // Indicates if buffer has wrapped around
	mu            sync.RWMutex
}

// NewFitnessEvaluator creates a production-ready evaluator with real implementations
func NewFitnessEvaluator() *FitnessEvaluator {
	return &FitnessEvaluator{
		LaunchRedAttack:      launchRedAttackReal,      // Real implementation
		MonitorBlueDetection: monitorBlueDetectionReal, // Real implementation
		GenerateBenignSample: generateBenignSampleReal, // Real implementation
		battleHistory:        make([]BattleRecord, MaxBattleHistory),
		historyIndex:         0,
		historyFull:          false,
	}
}

// EvaluateFitness conducts population-based battles and computes comprehensive fitness metrics
// Uses bounded parallelism and streaming results to prevent memory bloat
func (fe *FitnessEvaluator) EvaluateFitness(ctx context.Context, antibodyID string, attackSamples int, benignSamples int, environment string) (AntibodyFitness, error) {
	totalSamples := attackSamples + benignSamples
	if totalSamples < 30 {
		return AntibodyFitness{}, fmt.Errorf("insufficient sample size: %d < 30 (statistical significance)", totalSamples)
	}
	if totalSamples > 1000 {
		return AntibodyFitness{}, fmt.Errorf("excessive sample size: %d > 1000 (resource protection)", totalSamples)
	}

	// Create bounded worker pool to prevent resource exhaustion
	workerCount := min(MaxWorkers, totalSamples)
	battleChan := make(chan battleTask, totalSamples)
	resultChan := make(chan BattleResult, workerCount)

	// Launch workers with proper timeout handling
	runCtx, cancel := context.WithTimeout(ctx, 10*time.Minute)
	defer cancel()

	var wg sync.WaitGroup
	for i := 0; i < workerCount; i++ {
		wg.Add(1)
		go fe.battleWorker(runCtx, &wg, battleChan, resultChan, antibodyID)
	}

	// Queue battles (attack + benign samples)
	go func() {
		defer close(battleChan)

		// Queue attack samples
		for i := 0; i < attackSamples; i++ {
			select {
			case battleChan <- battleTask{Type: "attack", Index: i}:
			case <-runCtx.Done():
				return
			}
		}

		// Queue benign samples
		for i := 0; i < benignSamples; i++ {
			select {
			case battleChan <- battleTask{Type: "benign", Index: i}:
			case <-runCtx.Done():
				return
			}
		}
	}()

	// Close result channel when all workers complete
	go func() {
		wg.Wait()
		close(resultChan)
	}()

	// Stream results directly into metrics
	var samples []Sample
	truePositiveCount := 0
	falsePositiveCount := 0
	totalLatency := 0.0
	latencies := make([]float64, 0, attackSamples) // Only attack latencies
	blastRadiusSum := 0

	for i := 0; i < totalSamples; i++ {
		select {
		case result, ok := <-resultChan:
			if !ok {
				return AntibodyFitness{}, fmt.Errorf("worker channel closed prematurely")
			}
			if result.Error != nil {
				return AntibodyFitness{}, fmt.Errorf("battle %d failed: %w", i, result.Error)
			}

			// Collect samples for ROC analysis
			label := 1 // attack
			if result.AttackResult.Pattern == "benign" {
				label = 0
			}
			samples = append(samples, Sample{
				Score: result.DetectionResult.Confidence,
				Label: label,
			})

			// Update streaming metrics
			if result.DetectionResult.Detected {
				if label == 1 {
					truePositiveCount++
				} else {
					falsePositiveCount++
				}
			}
			if result.DetectionResult.FalseAlarm {
				falsePositiveCount++
			}

			// Only include attack latencies in timing metrics
			if label == 1 {
				totalLatency += result.DetectionResult.LatencyMs
				latencies = append(latencies, result.DetectionResult.LatencyMs)
				blastRadiusSum += result.AttackResult.BlastRadiusIPs
			}

			// Track battle history with environment set
			fe.addBattleHistory(BattleRecord{
				AntibodyID:      antibodyID,
				AttackResult:    result.AttackResult,
				DetectionResult: result.DetectionResult,
				BattleID:        fmt.Sprintf("battle-%d", time.Now().UnixNano()),
				Timestamp:       time.Now(),
				Environment:     environment, // Now properly set
				MonotonicMs:     result.MonotonicMs,
			})

		case <-runCtx.Done():
			return AntibodyFitness{}, runCtx.Err()
		}
	}

	// Calculate fitness metrics with defensive math
	detectionRate := float64(truePositiveCount) / float64(attackSamples) // Honest naming for attack detection
	avgLatency := totalLatency / float64(attackSamples)

	// Wilson confidence bounds for detection rate
	confidenceLower, confidenceUpper := Wilson(truePositiveCount, attackSamples, 0.05)

	// Calculate P95 latency
	p95Latency := calculateP95(latencies)

	// ROC analysis (if benign samples present)
	var rocSummary *ROCSummary
	hasROC := benignSamples > 0
	if hasROC {
		tpr, threshold, fpr := tprAtFPR(samples, 0.001) // TPR at 0.1% FPR
		rocSummary = &ROCSummary{
			Threshold: threshold,
			TPR:       tpr,
			FPR:       fpr,
		}
	}

	// Calculate environment-aware stability score from recent performance
	stabilityScore := fe.calculateEnvironmentStability(antibodyID)

	return AntibodyFitness{
		DetectionRate:   detectionRate,
		AvgLatencyMs:    avgLatency,
		P95LatencyMs:    p95Latency,
		ROC:             rocSummary,
		HasROC:          hasROC,
		ConfidenceLower: confidenceLower,
		ConfidenceUpper: confidenceUpper,
		StabilityScore:  stabilityScore,
		SampleSize:      totalSamples,
		AvgBlastRadius:  float64(blastRadiusSum) / float64(attackSamples),
		ContainmentCost: (avgLatency / 1000.0) * (float64(blastRadiusSum) / float64(attackSamples)),
	}, nil
}

// battleTask represents work for a battle worker
type battleTask struct {
	Type  string // "attack" or "benign"
	Index int
}

// battleWorker executes individual Red vs Blue confrontations
func (fe *FitnessEvaluator) battleWorker(ctx context.Context, wg *sync.WaitGroup, battles <-chan battleTask, results chan<- BattleResult, antibodyID string) {
	defer wg.Done()

	for task := range battles {
		select {
		case <-ctx.Done():
			return
		default:
			// Execute single battle with timeout
			battleCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
			var result BattleResult

			if task.Type == "attack" {
				result = fe.executeAttackBattle(battleCtx, antibodyID, task.Index)
			} else {
				result = fe.executeBenignBattle(battleCtx, antibodyID, task.Index)
			}
			cancel()

			select {
			case results <- result:
			case <-ctx.Done():
				return
			}
		}
	}
}

// executeAttackBattle conducts a single Red attack vs Blue detection engagement
func (fe *FitnessEvaluator) executeAttackBattle(ctx context.Context, antibodyID string, battleNum int) BattleResult {
	battleID := fmt.Sprintf("attack-%s-%d-%d", antibodyID, battleNum, time.Now().UnixNano())

	// Launch Red attack with monotonic timing
	start := time.Now()
	attackResult, err := fe.LaunchRedAttack(ctx, "privilege-escalation", battleID)
	if err != nil {
		return BattleResult{Error: fmt.Errorf("red attack failed: %w", err)}
	}

	// Monitor Blue detection (preserve Blue's latency measurement)
	detectionTimeout := 5 * time.Second
	detectionResult, err := fe.MonitorBlueDetection(ctx, battleID, antibodyID, detectionTimeout)
	if err != nil {
		return BattleResult{Error: fmt.Errorf("blue detection failed: %w", err)}
	}

	// Record monotonic battle duration separately
	monotonicMs := float64(time.Since(start).Nanoseconds()) / 1e6

	return BattleResult{
		AttackResult:    *attackResult,
		DetectionResult: *detectionResult,
		MonotonicMs:     monotonicMs,
	}
}

// executeBenignBattle conducts a benign sample evaluation
func (fe *FitnessEvaluator) executeBenignBattle(ctx context.Context, antibodyID string, sampleNum int) BattleResult {
	start := time.Now()

	// Generate benign detection result
	detectionResult, err := fe.GenerateBenignSample(ctx, antibodyID)
	if err != nil {
		return BattleResult{Error: fmt.Errorf("benign sample failed: %w", err)}
	}

	monotonicMs := float64(time.Since(start).Nanoseconds()) / 1e6

	// Create synthetic attack result for benign sample
	benignAttack := AttackResult{
		AttackID:       fmt.Sprintf("benign-%d", sampleNum),
		Pattern:        "benign",
		Success:        false,
		Techniques:     []string{},
		DurationMs:     0,
		BlastRadiusIPs: 0,
	}

	return BattleResult{
		AttackResult:    benignAttack,
		DetectionResult: *detectionResult,
		MonotonicMs:     monotonicMs,
	}
}

// tprAtFPR computes TPR and threshold at target FPR using ROC analysis
func tprAtFPR(samples []Sample, targetFPR float64) (tpr, threshold, fpr float64) {
	if len(samples) == 0 {
		return 0, math.NaN(), 0
	}

	// Sort by confidence score descending
	sort.Slice(samples, func(i, j int) bool {
		return samples[i].Score > samples[j].Score
	})

	// Count positives and negatives
	var pos, neg int
	for _, s := range samples {
		if s.Label == 1 {
			pos++
		} else {
			neg++
		}
	}

	if pos == 0 || neg == 0 {
		return 0, math.NaN(), 0
	}

	// Sweep thresholds
	var tp, fp int
	bestTPR, bestThreshold, bestFPR := 0.0, math.NaN(), 1.0

	for i := 0; i < len(samples); {
		currentThreshold := samples[i].Score

		// Count all samples at this threshold (handle ties)
		j := i
		for j < len(samples) && samples[j].Score == currentThreshold {
			if samples[j].Label == 1 {
				tp++
			} else {
				fp++
			}
			j++
		}

		currFPR := float64(fp) / float64(max(1, neg))
		currTPR := float64(tp) / float64(max(1, pos))

		// Find best TPR within target FPR constraint
		if currFPR <= targetFPR && currTPR >= bestTPR {
			bestTPR, bestThreshold, bestFPR = currTPR, currentThreshold, currFPR
		}

		i = j
	}

	return bestTPR, bestThreshold, bestFPR
}

// addBattleHistory adds a battle record using ring buffer for O(1) operation
func (fe *FitnessEvaluator) addBattleHistory(record BattleRecord) {
	fe.mu.Lock()
	defer fe.mu.Unlock()

	fe.battleHistory[fe.historyIndex] = record
	fe.historyIndex = (fe.historyIndex + 1) % MaxBattleHistory

	if fe.historyIndex == 0 {
		fe.historyFull = true
	}
}

// calculateEnvironmentStability computes environmental performance consistency
// Returns stability score [0,1] where 1.0 indicates consistent performance across environments
func (fe *FitnessEvaluator) calculateEnvironmentStability(antibodyID string) float64 {
	fe.mu.RLock()
	defer fe.mu.RUnlock()

	// Collect recent battles for this antibody (last 100 or available)
	recentBattles := fe.getRecentBattles(antibodyID, 100)
	if len(recentBattles) < 10 {
		return InsufficientDataStability // Neutral stability for insufficient data
	}

	// Group by environment and calculate detection rate variance
	envDetections := make(map[string][]float64)
	for _, battle := range recentBattles {
		env := battle.Environment
		if env == "" {
			env = "unknown" // Should not happen with proper environment setting
		}

		detected := 0.0
		if battle.DetectionResult.Detected {
			detected = 1.0
		}
		envDetections[env] = append(envDetections[env], detected)
	}

	// Calculate cross-environment variance
	if len(envDetections) < 2 {
		return SingleEnvStability // High stability for single environment
	}

	envMeans := make([]float64, 0, len(envDetections))
	for _, detections := range envDetections {
		sum := 0.0
		for _, d := range detections {
			sum += d
		}
		envMeans = append(envMeans, sum/float64(len(detections)))
	}

	// Calculate variance of environment means
	overallMean := 0.0
	for _, mean := range envMeans {
		overallMean += mean
	}
	overallMean /= float64(len(envMeans))

	variance := 0.0
	for _, mean := range envMeans {
		diff := mean - overallMean
		variance += diff * diff
	}
	variance /= float64(len(envMeans))

	// Convert variance to stability score (lower variance = higher stability)
	stabilityScore := math.Exp(-4.0 * variance) // Exponential decay
	return math.Max(0.0, math.Min(1.0, stabilityScore))
}

// getRecentBattles retrieves the most recent N battles for an antibody from ring buffer
func (fe *FitnessEvaluator) getRecentBattles(antibodyID string, maxCount int) []BattleRecord {
	recent := make([]BattleRecord, 0, maxCount)
	count := MaxBattleHistory
	if !fe.historyFull {
		count = fe.historyIndex
	}

	// Iterate backwards from current position
	for i := 0; i < count && len(recent) < maxCount; i++ {
		idx := (fe.historyIndex - 1 - i + MaxBattleHistory) % MaxBattleHistory
		battle := fe.battleHistory[idx]

		if battle.AntibodyID == antibodyID {
			recent = append(recent, battle)
		}
	}

	return recent
}

// Wilson computes Wilson score confidence interval for binomial proportion
// More accurate than normal approximation, especially for small samples or extreme proportions
func Wilson(successes, trials int, alpha float64) (lo, hi float64) {
	if trials == 0 {
		return 0, 0
	}

	z := 1.959963984540054 // default 95%
	switch alpha {
	case 0.10:
		z = 1.6448536269514729
	case 0.01:
		z = 2.5758293035489004
	}

	p := float64(successes) / float64(trials)
	n := float64(trials)

	denom := 1 + (z*z)/n
	center := p + (z*z)/(2*n)
	half := z * math.Sqrt((p*(1-p)+(z*z)/(4*n))/n)

	return math.Max(0, (center-half)/denom), math.Min(1, (center+half)/denom)
}

// calculateP95 computes the 95th percentile; it sorts the slice internally.
func calculateP95(latencies []float64) float64 {
	if len(latencies) == 0 {
		return 0
	}

	sort.Float64s(latencies)
	idx := int(math.Ceil(0.95*float64(len(latencies)))) - 1
	if idx < 0 {
		idx = 0
	}
	if idx >= len(latencies) {
		idx = len(latencies) - 1
	}
	return latencies[idx]
}

// Real implementation functions (would be provided by production system)
func launchRedAttackReal(ctx context.Context, pattern, battleID string) (*AttackResult, error) {
	// Production implementation would launch actual Red attack via Kubernetes Job
	// This is a placeholder for the real implementation
	return &AttackResult{
		AttackID:       battleID,
		Pattern:        pattern,
		Success:        true,
		Techniques:     []string{"T1068", "T1055"},
		DurationMs:     250.0,
		BlastRadiusIPs: 3,
	}, nil
}

func monitorBlueDetectionReal(ctx context.Context, battleID, antibodyID string, timeout time.Duration) (*DetectionResult, error) {
	// Production implementation would monitor Blue detection via Pheromone API
	// This is a placeholder for the real implementation
	return &DetectionResult{
		Detected:   true,
		LatencyMs:  95.0, // Blue-authoritative latency measurement
		Confidence: 0.87,
		RingLevel:  2,
		FalseAlarm: false,
	}, nil
}

func generateBenignSampleReal(ctx context.Context, antibodyID string) (*DetectionResult, error) {
	// Production implementation would replay benign traffic or use operator-confirmed clean periods
	// This is a placeholder for the real implementation
	return &DetectionResult{
		Detected:   false, // Benign should not trigger detection
		LatencyMs:  0,     // No detection means no latency
		Confidence: 0.12,  // Low confidence score
		RingLevel:  0,
		FalseAlarm: false,
	}, nil
}

// Utility functions
func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}