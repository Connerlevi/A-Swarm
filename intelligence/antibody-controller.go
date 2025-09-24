// Package intelligence - A-SWARM Antibody Controller
// Wires fitness evaluation into Kubernetes CRD status updates with promotion gating

package intelligence

import (
	"context"
	"fmt"
	"math"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

//
// ─── CRD SHAPES (minimal subset used by the controller) ──────────────────────
//

type AntibodySpec struct {
	Detector DetectorSpec `json:"detector"`
	Scope    ScopeSpec    `json:"scope"`
	Lineage  LineageSpec  `json:"lineage,omitempty"`
	Controls ControlsSpec `json:"controls,omitempty"`
}

type DetectorSpec struct {
	Type   string      `json:"type"` // rule, model, hybrid
	Rule   *RuleSpec   `json:"rule,omitempty"`
	Model  *ModelSpec  `json:"model,omitempty"`
	Hybrid *HybridSpec `json:"hybrid,omitempty"`
}

type RuleSpec struct {
	Pattern    string            `json:"pattern"`
	Features   map[string]string `json:"features"`
	EngineHint string            `json:"engine_hint"`
}

type ModelSpec struct {
	Features     map[string]interface{} `json:"features"`
	TrainingData string                 `json:"training_data"`
}

type HybridSpec struct {
	RuleWeight  float64 `json:"rule_weight"`
	ModelWeight float64 `json:"model_weight"`
}

type ScopeSpec struct {
	Environments        []string          `json:"environments"`
	Namespaces          []string          `json:"namespaces,omitempty"`
	Labels              map[string]string `json:"labels,omitempty"`
	ConfidenceThreshold float64           `json:"confidence_threshold,omitempty"`
}

type LineageSpec struct {
	ParentID     string    `json:"parent_id,omitempty"`
	Generation   int       `json:"generation,omitempty"`
	MutationType string    `json:"mutation_type,omitempty"`
	CreationTime time.Time `json:"creation_time,omitempty"`
	Creator      string    `json:"creator,omitempty"`
}

type ControlsSpec struct {
	TTLHours    int  `json:"ttl_hours,omitempty"`
	ShadowHours int  `json:"shadow_hours,omitempty"`
	MaxRing     int  `json:"max_ring,omitempty"`
	AutoPromote bool `json:"auto_promote,omitempty"`
}

type AntibodyStatus struct {
	Fitness    FitnessStatus      `json:"fitness,omitempty"`
	Deployment DeploymentStatus   `json:"deployment,omitempty"`
	Evidence   EvidenceStatus     `json:"evidence,omitempty"`
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

type FitnessStatus struct {
	TPRAtFPR001    *float64 `json:"tpr_at_fpr_001,omitempty"`
	MTTDP95Ms      float64  `json:"mttd_p95_ms"`
	BlastRadius    string   `json:"blast_radius,omitempty"`
	StabilityScore float64  `json:"stability_score"`
	AdaptationRate float64  `json:"adaptation_rate,omitempty"`
}

type DeploymentStatus struct {
	Phase             string    `json:"phase,omitempty"`
	ClustersDeployed  []string  `json:"clusters_deployed,omitempty"`
	ShadowStart       time.Time `json:"shadow_start,omitempty"`
	PromotionEligible time.Time `json:"promotion_eligible,omitempty"`
	LastUpdate        time.Time `json:"last_update,omitempty"`
}

type EvidenceStatus struct {
	ReplayTraces   []string     `json:"replay_traces,omitempty"`
	TestResults    []TestResult `json:"test_results,omitempty"`
	FalsePositives []FPIncident `json:"false_positives,omitempty"`
}

type TestResult struct {
	TestName  string    `json:"test_name"`
	Passed    bool      `json:"passed"`
	Score     float64   `json:"score"`
	Timestamp time.Time `json:"timestamp"`
}

type FPIncident struct {
	IncidentID string    `json:"incident_id"`
	Timestamp  time.Time `json:"timestamp"`
	Impact     string    `json:"impact"`
}

type Antibody struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`
	Spec              AntibodySpec   `json:"spec"`
	Status            AntibodyStatus `json:"status,omitempty"`
}

//
// ─── CONTROLLER ───────────────────────────────────────────────────────────────
//

type AntibodyController struct {
	Client           client.Client
	Scheme           *runtime.Scheme
	FitnessEvaluator *FitnessEvaluator

	// Promotion thresholds (configurable)
	MinTPRLowerBound float64 // Wilson lower bound on DetectionRate
	MaxFPRUpperBound float64 // FPR at operating point (when ROC is present)
	MinShadowHours   int
}

func NewAntibodyController(c client.Client, scheme *runtime.Scheme) *AntibodyController {
	return &AntibodyController{
		Client:           c,
		Scheme:           scheme,
		FitnessEvaluator: NewFitnessEvaluator(),
		MinTPRLowerBound: 0.90,  // 90% lower-bound on detection
		MaxFPRUpperBound: 0.001, // 0.1% FPR
		MinShadowHours:   168,   // 1 week
	}
}

// EvaluateAndUpdate runs fitness and updates the CRD Status.
// Note: benignSamples currently unused until ROC benign stream is wired.
func (ac *AntibodyController) EvaluateAndUpdate(
	ctx context.Context,
	antibodyName, namespace string,
	attackSamples, benignSamples int,
	environment string,
) error {
	// Fetch current CR
	ab := &Antibody{}
	key := types.NamespacedName{Name: antibodyName, Namespace: namespace}
	if err := ac.Client.Get(ctx, key, ab); err != nil {
		return fmt.Errorf("fetch antibody %s/%s: %w", namespace, antibodyName, err)
	}

	// Run evaluator (current API: sampleSize = attackSamples)
	fit, err := ac.FitnessEvaluator.EvaluateFitness(ctx, antibodyName, attackSamples, environment)
	if err != nil {
		return fmt.Errorf("fitness evaluation: %w", err)
	}

	// Map fitness to status and persist
	if err := ac.updateAntibodyStatus(ctx, ab, fit); err != nil {
		return fmt.Errorf("status update: %w", err)
	}

	// Promotion phase state machine
	if err := ac.evaluatePromotion(ctx, ab, fit); err != nil {
		return fmt.Errorf("promotion evaluation: %w", err)
	}

	return nil
}

func (ac *AntibodyController) updateAntibodyStatus(ctx context.Context, ab *Antibody, fit AntibodyFitness) error {
	now := metav1.NewTime(time.Now())

	// fitness.tpr_at_fpr_001 → pointer only if evaluator provided a number
	tprPtr := optionalFloat(fit.TPRAtFPR001)

	ab.Status.Fitness = FitnessStatus{
		TPRAtFPR001:    tprPtr,
		MTTDP95Ms:      fit.P95LatencyMs,
		StabilityScore: fit.StabilityScore,
		AdaptationRate: 0.0, // TODO: compute from battle trends
		BlastRadius:    mapBlastRadiusToRing(fit.AvgBlastRadius),
	}
	ab.Status.Deployment.LastUpdate = now.Time

	// conditions[]
	ac.updateConditions(&ab.Status, fit, now)

	// Persist status subresource
	if err := ac.Client.Status().Update(ctx, ab); err != nil {
		return fmt.Errorf("k8s status update: %w", err)
	}
	return nil
}

func (ac *AntibodyController) updateConditions(status *AntibodyStatus, fit AntibodyFitness, now metav1.Time) {
	conds := make([]metav1.Condition, 0, 3)

	ready := metav1.Condition{
		Type:               "Ready",
		Status:             metav1.ConditionTrue,
		LastTransitionTime: now,
		Reason:             "FitnessEvaluated",
		Message:            fmt.Sprintf("Evaluated %d samples, detection rate=%.3f", fit.SampleSize, fit.DetectionRate),
	}
	conds = append(conds, ready)

	validated := metav1.Condition{
		Type:               "Validated",
		Status:             metav1.ConditionFalse,
		LastTransitionTime: now,
		Reason:             "InsufficientSamples",
		Message:            fmt.Sprintf("Only %d samples (need 200+)", fit.SampleSize),
	}
	if fit.SampleSize >= 200 {
		validated.Status = metav1.ConditionTrue
		validated.Reason = "StatisticallyValid"
		validated.Message = fmt.Sprintf("95%% Wilson CI: [%.3f, %.3f]", fit.ConfidenceLower, fit.ConfidenceUpper)
	}
	conds = append(conds, validated)

	promoted := metav1.Condition{
		Type:               "Promoted",
		Status:             metav1.ConditionFalse,
		LastTransitionTime: now,
		Reason:             "BelowThreshold",
		Message:            fmt.Sprintf("TPR_LB %.3f < %.3f required", fit.ConfidenceLower, ac.MinTPRLowerBound),
	}
	if fit.MeetsPromotionSLO(ac.MinTPRLowerBound, ac.MaxFPRUpperBound) {
		promoted.Status = metav1.ConditionTrue
		promoted.Reason = "MeetsSLO"
		if !math.IsNaN(fit.TPRAtFPR001) {
			promoted.Message = fmt.Sprintf("TPR %.3f at/under FPR %.4f", fit.DetectionRate, fit.FPR)
		} else {
			promoted.Message = "Meets promotion criteria"
		}
	}
	conds = append(conds, promoted)

	status.Conditions = conds
}

func (ac *AntibodyController) evaluatePromotion(ctx context.Context, ab *Antibody, fit AntibodyFitness) error {
	phase := ab.Status.Deployment.Phase
	if phase == "" {
		phase = "pending"
	}
	now := time.Now()
	newPhase := phase

	switch phase {
	case "pending":
		newPhase = "shadow"
		ab.Status.Deployment.ShadowStart = now
		ab.Status.Deployment.PromotionEligible = now.Add(time.Duration(ac.MinShadowHours) * time.Hour)

	case "shadow":
		if now.After(ab.Status.Deployment.PromotionEligible) &&
			fit.MeetsPromotionSLO(ac.MinTPRLowerBound, ac.MaxFPRUpperBound) {
			newPhase = "staged"
		}

	case "staged":
		if ab.Spec.Controls.AutoPromote && fit.StabilityScore >= 0.8 {
			newPhase = "canary"
		}

	case "canary":
		// Intentionally manual to "active" via external process.

	case "active":
		if fit.ConfidenceLower < 0.7 {
			newPhase = "retired"
		}
	}

	if newPhase != phase {
		ab.Status.Deployment.Phase = newPhase
		ab.Status.Deployment.LastUpdate = now
		if err := ac.Client.Status().Update(ctx, ab); err != nil {
			return fmt.Errorf("k8s status update (phase): %w", err)
		}
	}

	return nil
}

//
// ─── HELPERS ──────────────────────────────────────────────────────────────────
//

func optionalFloat(v float64) *float64 {
	if math.IsNaN(v) {
		return nil
	}
	return &v
}

// MeetsPromotionSLO provides simple gating until full ROC is wired.
// Uses Wilson lower bound on DetectionRate and FPR (if provided by evaluator).
func (af AntibodyFitness) MeetsPromotionSLO(minTPRLowerBound, maxFPR float64) bool {
	if af.ConfidenceLower < minTPRLowerBound {
		return false
	}
	// If evaluator supplied a concrete FPR, check it. Otherwise pass.
	if !math.IsNaN(af.FPR) && af.FPR > 0 && af.FPR > maxFPR {
		return false
	}
	return true
}

// mapBlastRadiusToRing converts numeric scope to ring enum expected by the CRD.
func mapBlastRadiusToRing(avgBlastRadius float64) string {
	switch {
	case avgBlastRadius <= 1:
		return "ring-1"
	case avgBlastRadius <= 5:
		return "ring-2"
	case avgBlastRadius <= 15:
		return "ring-3"
	case avgBlastRadius <= 50:
		return "ring-4"
	default:
		return "ring-5"
	}
}

// Optional: surface recent battle history (same package → OK to call unexported)
func (ac *AntibodyController) GetAntibodyFitnessHistory(antibodyID string, maxBattles int) []BattleRecord {
	return ac.FitnessEvaluator.getRecentBattles(antibodyID, maxBattles)
}

func (ac *AntibodyController) SetPromotionThresholds(minTPR, maxFPR float64, shadowHours int) {
	ac.MinTPRLowerBound = minTPR
	ac.MaxFPRUpperBound = maxFPR
	ac.MinShadowHours = shadowHours
}