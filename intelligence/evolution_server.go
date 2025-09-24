package intelligence

import (
	"context"
	"fmt"
	"log"
	"net"
	"time"

	pb "github.com/Connerlevi/A-Swarm/intelligence/pb"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// EvolutionServer implements the gRPC Evolution service
// Adapts existing intelligence/* types for Python consumption
type EvolutionServer struct {
	pb.UnimplementedEvolutionServer

	evaluator Evaluator
	store     Store
	mutator   MutationEngine
	popMgr    PopulationManager
}

// NewEvolutionServer creates a new gRPC server instance
func NewEvolutionServer(eval Evaluator, store Store, mut MutationEngine, pop PopulationManager) *EvolutionServer {
	return &EvolutionServer{
		evaluator: eval,
		store:     store,
		mutator:   mut,
		popMgr:    pop,
	}
}

// EvaluateFitness adapts Go fitness evaluation for Python clients
func (s *EvolutionServer) EvaluateFitness(ctx context.Context, req *pb.EvaluateFitnessRequest) (*pb.EvaluateFitnessResponse, error) {
	if req.Antibody == nil {
		return nil, status.Error(codes.InvalidArgument, "antibody is required")
	}

	// Convert protobuf antibody to Go types
	antibody := convertPbToAntibody(req.Antibody)

	// Convert combat results
	var results []CombatResult
	for _, pbResult := range req.CombatResults {
		results = append(results, convertPbToCombatResult(pbResult))
	}

	// Run fitness evaluation
	fitness, err := s.evaluator.EvaluateFitness(ctx, antibody, results)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "fitness evaluation failed: %v", err)
	}

	// Determine promotion eligibility
	config := getEvaluationConfig(req.Config)
	shouldPromote := fitness.OverallFitness >= config.FitnessThreshold &&
					fitness.TotalTests >= int64(config.MinSampleSize)

	// Generate reasoning
	reasoning := fmt.Sprintf("Fitness: %.3f (threshold: %.3f), Tests: %d (min: %d)",
		fitness.OverallFitness, config.FitnessThreshold, fitness.TotalTests, config.MinSampleSize)

	return &pb.EvaluateFitnessResponse{
		Fitness:       convertExtendedFitnessToPb(fitness),
		ShouldPromote: shouldPromote,
		Reasoning:     reasoning,
		Warnings:      generateFitnessWarnings(fitness),
	}, nil
}

// EvolveOnce runs one complete evolution cycle
func (s *EvolutionServer) EvolveOnce(ctx context.Context, req *pb.EvolveOnceRequest) (*pb.EvolveOnceResponse, error) {
	config := getEvolutionConfig(req.Config)

	// Get current population
	population, err := s.popMgr.GetPopulation(ctx, config)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "failed to get population: %v", err)
	}

	// Run evolution cycle
	newGeneration, metrics, err := s.mutator.Evolve(ctx, population, config)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "evolution failed: %v", err)
	}

	// Convert results to protobuf
	var pbAntibodies []*pb.Antibody
	for _, antibody := range newGeneration {
		pbAntibodies = append(pbAntibodies, convertAntibodyToPb(antibody))
	}

	return &pb.EvolveOnceResponse{
		NewAntibodies: pbAntibodies,
		Metrics:       convertEvolutionMetricsToPb(metrics),
		Status:        "success",
		Errors:        []string{}, // No errors if we got here
	}, nil
}

// StoreAntibody persists an antibody to the store
func (s *EvolutionServer) StoreAntibody(ctx context.Context, req *pb.StoreAntibodyRequest) (*pb.StoreAntibodyResponse, error) {
	if req.Antibody == nil {
		return nil, status.Error(codes.InvalidArgument, "antibody is required")
	}

	antibody := convertPbToAntibody(req.Antibody)

	err := s.store.Store(ctx, antibody)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "failed to store antibody: %v", err)
	}

	return &pb.StoreAntibodyResponse{
		Stored:     true,
		AntibodyId: antibody.ID,
		Warnings:   []string{},
	}, nil
}

// GetPopulation retrieves current antibody population
func (s *EvolutionServer) GetPopulation(ctx context.Context, req *pb.GetPopulationRequest) (*pb.GetPopulationResponse, error) {
	// Convert phase filter
	var phaseFilter *AntibodyPhase
	if req.PhaseFilter != pb.AntibodyPhase_ANTIBODY_PHASE_UNSPECIFIED {
		phase := convertPbToAntibodyPhase(req.PhaseFilter)
		phaseFilter = &phase
	}

	// Get antibodies from store
	antibodies, err := s.store.List(ctx, &ListOptions{
		PhaseFilter:    phaseFilter,
		Limit:         int(req.Limit),
		Cursor:        req.Cursor,
		IncludeFitness: req.IncludeFitness,
	})
	if err != nil {
		return nil, status.Errorf(codes.Internal, "failed to list antibodies: %v", err)
	}

	// Convert to protobuf
	var pbAntibodies []*pb.Antibody
	for _, antibody := range antibodies {
		pbAntibodies = append(pbAntibodies, convertAntibodyToPb(antibody))
	}

	// Get population stats
	stats, err := s.store.GetStats(ctx)
	if err != nil {
		log.Printf("Failed to get population stats: %v", err)
		stats = &PopulationStats{} // Return empty stats on error
	}

	return &pb.GetPopulationResponse{
		Antibodies: pbAntibodies,
		NextCursor: generateNextCursor(antibodies, req.Limit),
		TotalCount: int64(len(antibodies)),
		Stats:      convertPopulationStatsToPb(stats),
	}, nil
}

// GetMetrics retrieves evolution metrics
func (s *EvolutionServer) GetMetrics(ctx context.Context, req *pb.GetMetricsRequest) (*pb.GetMetricsResponse, error) {
	// Get current metrics from population manager
	currentMetrics, err := s.popMgr.GetMetrics(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "failed to get metrics: %v", err)
	}

	// Get historical metrics if requested
	var history []*pb.HistoricalMetric
	if req.SinceTimestamp > 0 {
		histMetrics, err := s.store.GetHistoricalMetrics(ctx, time.Unix(req.SinceTimestamp, 0))
		if err != nil {
			log.Printf("Failed to get historical metrics: %v", err)
		} else {
			for _, hm := range histMetrics {
				history = append(history, convertHistoricalMetricToPb(hm))
			}
		}
	}

	return &pb.GetMetricsResponse{
		Current:       convertEvolutionMetricsToPb(currentMetrics),
		History:       history,
		CustomMetrics: make(map[string]double), // TODO: Implement custom metrics
	}, nil
}

// Helper functions for type conversion

func convertPbToAntibody(pb *pb.Antibody) *Antibody {
	return &Antibody{
		ID:       pb.Id,
		SpecHash: pb.SpecHash,
		Spec:     pb.Spec,
		Phase:    convertPbToAntibodyPhase(pb.Phase),
		Fitness:  convertPbToFitnessSummary(pb.Fitness),
		Generation: pb.Generation,
		ParentID: pb.ParentId,
		Lineage:  pb.Lineage,
		CreatedAt: time.Unix(pb.CreatedAt, 0),
		UpdatedAt: time.Unix(pb.UpdatedAt, 0),
	}
}

func convertAntibodyToPb(a *Antibody) *pb.Antibody {
	return &pb.Antibody{
		Id:         a.ID,
		SpecHash:   a.SpecHash,
		Spec:       a.Spec,
		Phase:      convertAntibodyPhaseToPb(a.Phase),
		Fitness:    convertFitnessSummaryToPb(a.Fitness),
		Generation: a.Generation,
		ParentId:   a.ParentID,
		Lineage:    a.Lineage,
		CreatedAt:  a.CreatedAt.Unix(),
		UpdatedAt:  a.UpdatedAt.Unix(),
	}
}

func convertPbToAntibodyPhase(pb pb.AntibodyPhase) AntibodyPhase {
	switch pb {
	case pb.AntibodyPhase_ANTIBODY_PHASE_SHADOW:
		return PhaseShadow
	case pb.AntibodyPhase_ANTIBODY_PHASE_STAGED:
		return PhaseStaged
	case pb.AntibodyPhase_ANTIBODY_PHASE_CANARY:
		return PhaseCanary
	case pb.AntibodyPhase_ANTIBODY_PHASE_ACTIVE:
		return PhaseActive
	case pb.AntibodyPhase_ANTIBODY_PHASE_RETIRED:
		return PhaseRetired
	default:
		return PhaseShadow // Default fallback
	}
}

func convertAntibodyPhaseToPb(phase AntibodyPhase) pb.AntibodyPhase {
	switch phase {
	case PhaseShadow:
		return pb.AntibodyPhase_ANTIBODY_PHASE_SHADOW
	case PhaseStaged:
		return pb.AntibodyPhase_ANTIBODY_PHASE_STAGED
	case PhaseCanary:
		return pb.AntibodyPhase_ANTIBODY_PHASE_CANARY
	case PhaseActive:
		return pb.AntibodyPhase_ANTIBODY_PHASE_ACTIVE
	case PhaseRetired:
		return pb.AntibodyPhase_ANTIBODY_PHASE_RETIRED
	default:
		return pb.AntibodyPhase_ANTIBODY_PHASE_SHADOW
	}
}

func convertPbToFitnessSummary(pb *pb.FitnessSummary) *FitnessSummary {
	if pb == nil {
		return &FitnessSummary{}
	}
	return &FitnessSummary{
		DetectionRate:        pb.DetectionRate,
		FalsePositiveRate:   pb.FalsePositiveRate,
		LatencyP95Ms:        pb.LatencyP95Ms,
		OverallFitness:      pb.OverallFitness,
		TotalTests:          pb.TotalTests,
		SuccessfulDetections: pb.SuccessfulDetections,
	}
}

func convertFitnessSummaryToPb(fs *FitnessSummary) *pb.FitnessSummary {
	if fs == nil {
		return &pb.FitnessSummary{}
	}
	return &pb.FitnessSummary{
		DetectionRate:         fs.DetectionRate,
		FalsePositiveRate:    fs.FalsePositiveRate,
		LatencyP95Ms:         fs.LatencyP95Ms,
		OverallFitness:       fs.OverallFitness,
		TotalTests:           fs.TotalTests,
		SuccessfulDetections: fs.SuccessfulDetections,
	}
}

func convertExtendedFitnessToPb(efs *ExtendedFitnessSummary) *pb.ExtendedFitnessSummary {
	if efs == nil {
		return &pb.ExtendedFitnessSummary{}
	}
	return &pb.ExtendedFitnessSummary{
		Base:             convertFitnessSummaryToPb(&efs.FitnessSummary),
		CoverageScore:    efs.CoverageScore,
		SafetyScore:      efs.SafetyScore,
		EfficiencyScore:  efs.EfficiencyScore,
		ExtendedFitness:  efs.ExtendedFitness,
	}
}

func convertPbToCombatResult(pb *pb.CombatResult) CombatResult {
	return CombatResult{
		AntibodyID:       pb.AntibodyId,
		AttackSignature:  pb.AttackSignature,
		Detected:         pb.Detected,
		LatencyMs:        pb.LatencyMs,
		FalsePositive:    pb.FalsePositive,
		BlastRadius:      pb.BlastRadius,
		Timestamp:        time.Unix(pb.Timestamp, 0),
		Metadata:         pb.Metadata,
	}
}

func convertEvolutionMetricsToPb(em *EvolutionMetrics) *pb.EvolutionMetrics {
	if em == nil {
		return &pb.EvolutionMetrics{}
	}
	return &pb.EvolutionMetrics{
		BestFitness:          em.BestFitness,
		AvgFitness:           em.AvgFitness,
		DiversityScore:       em.DiversityScore,
		Generation:           em.Generation,
		PopulationSize:       em.PopulationSize,
		MutationsAttempted:   em.MutationsAttempted,
		SuccessfulMutations:  em.SuccessfulMutations,
	}
}

func convertPopulationStatsToPb(ps *PopulationStats) *pb.PopulationStats {
	if ps == nil {
		return &pb.PopulationStats{}
	}

	byPhase := make(map[string]int64)
	for phase, count := range ps.ByPhase {
		byPhase[string(phase)] = count
	}

	return &pb.PopulationStats{
		TotalAntibodies:   ps.TotalAntibodies,
		ByPhase:          byPhase,
		AvgFitness:       ps.AvgFitness,
		BestFitness:      ps.BestFitness,
		CurrentGeneration: ps.CurrentGeneration,
	}
}

func convertHistoricalMetricToPb(hm *HistoricalMetric) *pb.HistoricalMetric {
	return &pb.HistoricalMetric{
		Timestamp: hm.Timestamp.Unix(),
		Metrics:   convertEvolutionMetricsToPb(&hm.Metrics),
		Milestone: hm.Milestone,
	}
}

func getEvaluationConfig(pbConfig *pb.EvaluationConfig) *EvaluationConfig {
	if pbConfig == nil {
		return &EvaluationConfig{
			WilsonConfidence: 0.95,
			LatencyPenalty:   0.1,
			SafetyWeight:     0.2,
			MinSampleSize:    10,
			FitnessThreshold: 0.7,
		}
	}
	return &EvaluationConfig{
		WilsonConfidence: pbConfig.WilsonConfidence,
		LatencyPenalty:   pbConfig.LatencyPenalty,
		SafetyWeight:     pbConfig.SafetyWeight,
		MinSampleSize:    int(pbConfig.MinSampleSize),
		FitnessThreshold: 0.7, // Default, not in protobuf
	}
}

func getEvolutionConfig(pbConfig *pb.EvolutionConfig) *EvolutionConfig {
	if pbConfig == nil {
		return &EvolutionConfig{
			FitnessThreshold:   0.7,
			DiversityThreshold: 0.3,
			MaxGenerations:     100,
			MutationStrategies: []string{"point", "crossover", "inversion"},
		}
	}
	return &EvolutionConfig{
		FitnessThreshold:   pbConfig.FitnessThreshold,
		DiversityThreshold: pbConfig.DiversityThreshold,
		MaxGenerations:     int(pbConfig.MaxGenerations),
		MutationStrategies: pbConfig.MutationStrategies,
	}
}

func generateFitnessWarnings(fitness *ExtendedFitnessSummary) []string {
	var warnings []string

	if fitness.TotalTests < 10 {
		warnings = append(warnings, "Low sample size - results may not be statistically significant")
	}
	if fitness.FalsePositiveRate > 0.05 {
		warnings = append(warnings, "High false positive rate detected")
	}
	if fitness.LatencyP95Ms > 200 {
		warnings = append(warnings, "High detection latency may impact performance")
	}
	if fitness.SafetyScore < 0.7 {
		warnings = append(warnings, "Low safety score - consider restricting blast radius")
	}

	return warnings
}

func generateNextCursor(antibodies []*Antibody, limit int32) string {
	if len(antibodies) < int(limit) {
		return "" // No more results
	}
	if len(antibodies) > 0 {
		return antibodies[len(antibodies)-1].ID // Use last ID as cursor
	}
	return ""
}

// Server startup helper
func StartEvolutionServer(addr string, eval Evaluator, store Store, mut MutationEngine, pop PopulationManager) error {
	server := NewEvolutionServer(eval, store, mut, pop)

	grpcServer := grpc.NewServer()
	pb.RegisterEvolutionServer(grpcServer, server)

	lis, err := net.Listen("tcp", addr)
	if err != nil {
		return fmt.Errorf("failed to listen: %v", err)
	}

	log.Printf("Evolution gRPC server listening on %s", addr)
	return grpcServer.Serve(lis)
}