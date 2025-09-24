package intelligence

import (
    "crypto/sha256"
    "encoding/hex"
    "hash"
    "math"
    "sort"
    "strconv"
)

// Latency thresholds for fitness scoring
const (
    P95OKMs  = 500.0  // Latency under this is good
    P95BadMs = 2000.0 // Latency over this is bad
)

// ComputeSpecHash generates a stable, deterministic SHA256 hash of the antibody spec
// Maps are sorted by key to ensure deterministic ordering
func ComputeSpecHash(spec AntibodySpec) string {
    h := sha256.New()

    // Write fields in deterministic order
    h.Write([]byte("detector.type:"))
    h.Write([]byte(spec.Detector.Type))
    h.Write([]byte(";"))

    // Hash detector content based on type
    switch spec.Detector.Type {
    case "rule":
        if spec.Detector.Rule != nil {
            h.Write([]byte("rule.pattern:"))
            h.Write([]byte(spec.Detector.Rule.Pattern))
            h.Write([]byte(";"))
            h.Write([]byte("rule.engine_hint:"))
            h.Write([]byte(spec.Detector.Rule.EngineHint))
            h.Write([]byte(";"))
            hashMapStringString(h, spec.Detector.Rule.Features, "rule.features.")
        }
    case "model":
        if spec.Detector.Model != nil {
            h.Write([]byte("model.training_data:"))
            h.Write([]byte(spec.Detector.Model.TrainingData))
            h.Write([]byte(";"))
            hashMapStringAny(h, spec.Detector.Model.Features, "model.features.")
        }
    case "hybrid":
        if spec.Detector.Hybrid != nil {
            h.Write([]byte("hybrid.rule_weight:"))
            h.Write([]byte(strconv.FormatFloat(spec.Detector.Hybrid.RuleWeight, 'g', -1, 64)))
            h.Write([]byte(";"))
            h.Write([]byte("hybrid.model_weight:"))
            h.Write([]byte(strconv.FormatFloat(spec.Detector.Hybrid.ModelWeight, 'g', -1, 64)))
            h.Write([]byte(";"))
        }
    }

    // Hash scope in deterministic order
    h.Write([]byte("scope.environments:"))
    sortedEnvs := make([]string, len(spec.Scope.Environments))
    copy(sortedEnvs, spec.Scope.Environments)
    sort.Strings(sortedEnvs)
    for _, env := range sortedEnvs {
        h.Write([]byte(env))
        h.Write([]byte(","))
    }
    h.Write([]byte(";"))

    h.Write([]byte("scope.namespaces:"))
    sortedNs := make([]string, len(spec.Scope.Namespaces))
    copy(sortedNs, spec.Scope.Namespaces)
    sort.Strings(sortedNs)
    for _, ns := range sortedNs {
        h.Write([]byte(ns))
        h.Write([]byte(","))
    }
    h.Write([]byte(";"))

    hashMapStringString(h, spec.Scope.Labels, "scope.labels.")

    h.Write([]byte("scope.confidence_threshold:"))
    h.Write([]byte(strconv.FormatFloat(spec.Scope.ConfidenceThreshold, 'g', -1, 64)))
    h.Write([]byte(";"))

    // Hash lineage if present
    h.Write([]byte("lineage:"))
    h.Write([]byte(spec.Lineage))
    h.Write([]byte(";"))

    sum := h.Sum(nil)
    return hex.EncodeToString(sum)
}

// hashMapStringString writes a map[string]string to hasher in deterministic order
func hashMapStringString(h hash.Hash, m map[string]string, prefix string) {
    if len(m) == 0 {
        return
    }
    keys := make([]string, 0, len(m))
    for k := range m {
        keys = append(keys, k)
    }
    sort.Strings(keys)
    for _, k := range keys {
        h.Write([]byte(prefix))
        h.Write([]byte(k))
        h.Write([]byte("="))
        h.Write([]byte(m[k]))
        h.Write([]byte(";"))
    }
}

// hashMapStringAny writes a map[string]interface{} to hasher in deterministic order
func hashMapStringAny(h hash.Hash, m map[string]interface{}, prefix string) {
    if len(m) == 0 {
        return
    }
    keys := make([]string, 0, len(m))
    for k := range m {
        keys = append(keys, k)
    }
    sort.Strings(keys)
    for _, k := range keys {
        h.Write([]byte(prefix))
        h.Write([]byte(k))
        h.Write([]byte("="))
        // Convert value to string representation
        switch v := m[k].(type) {
        case string:
            h.Write([]byte(v))
        case float64:
            h.Write([]byte(strconv.FormatFloat(v, 'g', -1, 64)))
        case int:
            h.Write([]byte(strconv.Itoa(v)))
        case bool:
            h.Write([]byte(strconv.FormatBool(v)))
        default:
            h.Write([]byte("unknown"))
        }
        h.Write([]byte(";"))
    }
}

// WilsonScore returns the lower bound of the Wilson score interval for binomial proportion.
// confidence: e.g., 0.95
func WilsonScore(successes, failures int, confidence float64) float64 {
    n := successes + failures
    if n == 0 {
        return 0.0
    }
    p := float64(successes) / float64(n)

    // z for confidence (two-sided). For 95% ~ 1.96; 90% ~ 1.645.
    // We keep a tiny lookup to avoid importing stats libs.
    z := 1.96
    switch {
    case confidence >= 0.989:
        z = 2.575
    case confidence >= 0.949:
        z = 1.96
    case confidence >= 0.899:
        z = 1.645
    case confidence >= 0.799:
        z = 1.282
    }

    den := 1 + (z*z)/float64(n)
    center := p + (z*z)/(2*float64(n))
    margin := z * math.Sqrt((p*(1-p)+((z*z)/(4*float64(n))))/float64(n))
    lb := (center - margin) / den
    return Clamp01(lb)
}

// Clamp01 ensures value is in [0,1] range
func Clamp01(x float64) float64 {
    if x < 0 {
        return 0
    }
    if x > 1 {
        return 1
    }
    return x
}

// ComputeOverallFitness blends detection quality, latency/cost, and safety.
// This is a conservative, monotone baseline you can tune later.
func ComputeOverallFitness(s FitnessSummary) float64 {
    // If OverallFitness is already set, use it
    if s.OverallFitness > 0 {
        return s.OverallFitness
    }

    // Start with Wilson confidence lower bound if provided, else derive from TP/FN, else 0.5
    base := s.ConfidenceLo
    if base == 0 {
        // Note: TruePositives/FalseNegatives are not in base FitnessSummary
        // This will be handled by ExtendedFitnessSummary processing
        if s.SampleSize > 0 {
            base = 0.5
        }
    }

    // Latency penalty (normalize P95 to 0..1 where 0 is bad, 1 is good).
    p95 := s.P95LatencyMs
    latOK := 1.0
    if p95 > 0 {
        if p95 <= P95OKMs {
            latOK = 1.0
        } else if p95 >= P95BadMs {
            latOK = 0.0
        } else {
            latOK = 1.0 - (p95-P95OKMs)/(P95BadMs-P95OKMs)
        }
    }

    // Stability bonus: cross-environment consistency
    stability := s.StabilityScore
    if stability == 0 {
        stability = 0.5 // neutral if not measured
    }

    // Blast radius penalty: higher rings are more dangerous
    blastPenalty := 1.0
    switch s.BlastRadius {
    case "ring-1":
        blastPenalty = 1.0
    case "ring-2":
        blastPenalty = 0.9
    case "ring-3":
        blastPenalty = 0.7
    case "ring-4":
        blastPenalty = 0.5
    case "ring-5":
        blastPenalty = 0.3
    }

    // Blend (weights can be tuned). Keep result in [0,1].
    // Intuition: detection confidence is most important, then stability, then latency
    raw := 0.5*base + 0.2*stability + 0.2*latOK + 0.1*blastPenalty
    return Clamp01(raw)
}

// ComputeExtendedFitness calculates fitness from ExtendedFitnessSummary
// This uses the additional detection metrics when available
func ComputeExtendedFitness(e ExtendedFitnessSummary) float64 {
    // Detection: use F1 if provided; otherwise from precision/recall.
    f1 := e.F1Score
    if f1 == 0 && (e.Precision > 0 || e.Recall > 0) {
        den := e.Precision + e.Recall
        if den > 0 {
            f1 = 2 * (e.Precision * e.Recall) / den
        }
    }

    // Latency penalty - use P95 from embedded FitnessSummary
    p95 := e.FitnessSummary.P95LatencyMs
    if p95 <= 0 {
        p95 = e.AvgLatencyMs // fallback to extended field
    }
    latOK := 1.0
    if p95 > 0 {
        if p95 <= P95OKMs {
            latOK = 1.0
        } else if p95 >= P95BadMs {
            latOK = 0.0
        } else {
            latOK = 1.0 - (p95-P95OKMs)/(P95BadMs-P95OKMs)
        }
    }

    // Safety penalty: any violations push score down fast.
    safety := 1.0
    if e.SafetyViolations > 0 {
        // Exponential decay with violations
        safety = math.Exp(-0.7 * float64(e.SafetyViolations))
    }

    // Confidence of the estimate: Wilson lower bound on TPR if available.
    // Use TP/(TP+FN) as successes/total.
    wilson := 1.0
    if e.TruePositives+e.FalseNegatives > 0 {
        wilson = WilsonScore(e.TruePositives, e.FalseNegatives, 0.95)
    }

    // Stability from embedded FitnessSummary
    stability := e.FitnessSummary.StabilityScore
    if stability == 0 {
        stability = 0.5 // neutral if not measured
    }

    // Blend (weights can be tuned). Keep result in [0,1].
    // Intuition: require both good detection & safety; latency matters but less.
    detect := 0.7*f1 + 0.3*wilson
    raw := detect * safety * latOK * stability

    // Clamp to [0,1] and return
    return Clamp01(raw)
}