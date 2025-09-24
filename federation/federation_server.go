package main

import (
	"context"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/Connerlevi/A-Swarm/federation/hllcodec"
	"github.com/Connerlevi/A-Swarm/federation/server"
	"github.com/Connerlevi/A-Swarm/federation/signing"
	pb "github.com/Connerlevi/A-Swarm/federation/pb"
	hllpb "github.com/Connerlevi/A-Swarm/hll/pb"
	"github.com/Connerlevi/A-Swarm/hll"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// FederationServer implements the Federator gRPC service
type FederationServer struct {
	pb.UnimplementedFederatorServer

	clusterID    string
	sketchStore  hll.SketchStore
	rateLimiter  *server.RateLimiter
	replayGuard  *server.ReplayGuard
	keyring      signing.Keyring
	trustScores  map[string]*pb.TrustScore
}

// NewFederationServer creates a new federation server instance
func NewFederationServer(
	clusterID string,
	store hll.SketchStore,
	keyring signing.Keyring,
) *FederationServer {
	return &FederationServer{
		clusterID:   clusterID,
		sketchStore: store,
		rateLimiter: server.NewRateLimiter(100, time.Minute), // 100 RPM default
		replayGuard: server.NewReplayGuard(1000, time.Hour),   // 1000 entries, 1 hour TTL
		keyring:     keyring,
		trustScores: make(map[string]*pb.TrustScore),
	}
}

// ShareSketch receives and merges sketches from peer clusters
func (s *FederationServer) ShareSketch(ctx context.Context, req *pb.ShareSketchRequest) (*pb.ShareSketchResponse, error) {
	// Validate request
	if req.ClusterId == "" {
		return nil, status.Error(codes.InvalidArgument, "cluster_id is required")
	}

	if req.ClusterId == s.clusterID {
		return nil, status.Error(codes.InvalidArgument, "cannot share sketch with self")
	}

	// Check rate limits
	if !s.rateLimiter.Allow(req.ClusterId) {
		return &pb.ShareSketchResponse{
			Success:   false,
			ErrorCode: pb.ErrorCode_ERROR_CODE_RATE_LIMITED,
			Message:   "Rate limit exceeded",
		}, nil
	}

	// Verify signature
	if err := signing.VerifyShareSketch(s.keyring, req); err != nil {
		return &pb.ShareSketchResponse{
			Success:   false,
			ErrorCode: pb.ErrorCode_ERROR_CODE_INVALID_SIGNATURE,
			Message:   fmt.Sprintf("Signature verification failed: %v", err),
		}, nil
	}

	// Check for replay attacks
	nonce := fmt.Sprintf("%s:%d:%s", req.ClusterId, req.SequenceNumber, req.Nonce)
	if s.replayGuard.IsReplayed(nonce, time.Unix(req.Timestamp, 0)) {
		return &pb.ShareSketchResponse{
			Success:   false,
			ErrorCode: pb.ErrorCode_ERROR_CODE_REPLAY_DETECTED,
			Message:   "Replay attack detected",
		}, nil
	}

	// Check trust score
	trustScore := s.getTrustScore(req.ClusterId)
	if trustScore.reliability_score < 0.3 { // Configurable threshold
		return &pb.ShareSketchResponse{
			Success:   false,
			ErrorCode: pb.ErrorCode_ERROR_CODE_TRUST_BELOW_THRESHOLD,
			Message:   "Peer trust score below threshold",
		}, nil
	}

	// Convert and validate HLL sketch
	sketch, err := hllcodec.ConvertFromProto(req.Sketch)
	if err != nil {
		return &pb.ShareSketchResponse{
			Success:   false,
			ErrorCode: pb.ErrorCode_ERROR_CODE_INVALID_SKETCH,
			Message:   fmt.Sprintf("Invalid sketch data: %v", err),
		}, nil
	}

	// Store and merge the sketch
	sketchID := fmt.Sprintf("%s:%s:%d", req.ClusterId, req.AntibodyPhase.String(), req.Timestamp)
	if err := s.sketchStore.Store(ctx, sketchID, sketch); err != nil {
		log.Printf("Failed to store sketch from %s: %v", req.ClusterId, err)
		return &pb.ShareSketchResponse{
			Success:   false,
			ErrorCode: pb.ErrorCode_ERROR_CODE_INTERNAL_ERROR,
			Message:   "Failed to store sketch",
		}, nil
	}

	// Update trust score based on successful interaction
	s.updateTrustScore(req.ClusterId, true)

	log.Printf("Successfully received and stored sketch from cluster %s (phase: %s)",
		req.ClusterId, req.AntibodyPhase.String())

	return &pb.ShareSketchResponse{
		Success:     true,
		ErrorCode:   pb.ErrorCode_ERROR_CODE_UNSPECIFIED,
		Message:     "Sketch received and merged successfully",
		ReceiverId:  s.clusterID,
		ProcessedAt: time.Now().Unix(),
	}, nil
}

// RequestSketch allows peers to request sketches from this cluster
func (s *FederationServer) RequestSketch(ctx context.Context, req *pb.RequestSketchRequest) (*pb.RequestSketchResponse, error) {
	// Validate request
	if req.ClusterId == "" {
		return nil, status.Error(codes.InvalidArgument, "cluster_id is required")
	}

	// Check rate limits
	if !s.rateLimiter.Allow(req.ClusterId) {
		return &pb.RequestSketchResponse{
			Success:   false,
			ErrorCode: pb.ErrorCode_ERROR_CODE_RATE_LIMITED,
		}, nil
	}

	// Verify signature
	if err := signing.VerifyRequestSketch(s.keyring, req); err != nil {
		return &pb.RequestSketchResponse{
			Success:   false,
			ErrorCode: pb.ErrorCode_ERROR_CODE_INVALID_SIGNATURE,
		}, nil
	}

	// Check trust score
	trustScore := s.getTrustScore(req.ClusterId)
	if trustScore.reliability_score < 0.3 {
		return &pb.RequestSketchResponse{
			Success:   false,
			ErrorCode: pb.ErrorCode_ERROR_CODE_TRUST_BELOW_THRESHOLD,
		}, nil
	}

	// Get sketches from store based on criteria
	sketches, err := s.sketchStore.List(ctx, &hll.ListOptions{
		Since: time.Unix(req.SinceTimestamp, 0),
		Limit: int(req.Limit),
	})
	if err != nil {
		return &pb.RequestSketchResponse{
			Success:   false,
			ErrorCode: pb.ErrorCode_ERROR_CODE_INTERNAL_ERROR,
		}, nil
	}

	// Convert sketches to protobuf format
	var pbSketches []*hllpb.HLLSketch
	for _, sketch := range sketches {
		pbSketch, err := hllcodec.ConvertToProto(sketch)
		if err != nil {
			log.Printf("Failed to convert sketch to protobuf: %v", err)
			continue
		}
		pbSketches = append(pbSketches, pbSketch)
	}

	return &pb.RequestSketchResponse{
		Success:     true,
		ErrorCode:   pb.ErrorCode_ERROR_CODE_UNSPECIFIED,
		Sketches:    pbSketches,
		ClusterId:   s.clusterID,
		RespondedAt: time.Now().Unix(),
	}, nil
}

// ReportHealth provides cluster health and status information
func (s *FederationServer) ReportHealth(ctx context.Context, req *pb.HealthReportRequest) (*pb.HealthReportResponse, error) {
	// Get store statistics
	stats := s.sketchStore.Stats()

	return &pb.HealthReportResponse{
		ClusterId:     s.clusterID,
		Status:        pb.HealthStatus_HEALTH_STATUS_HEALTHY,
		SketchCount:   int64(stats.TotalSketches),
		LastUpdate:    time.Now().Unix(),
		Version:       "1.0.0",
		Capabilities:  []string{"hll-merge", "byzantine-consensus", "rate-limiting"},
		Load:          0.5, // TODO: Implement actual load calculation
	}, nil
}

// FederationStream handles bidirectional streaming for real-time federation
func (s *FederationServer) FederationStream(stream pb.Federator_FederationStreamServer) error {
	ctx := stream.Context()

	log.Printf("Federation stream started")
	defer log.Printf("Federation stream ended")

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
			// Receive message from peer
			msg, err := stream.Recv()
			if err != nil {
				return err
			}

			// Process message based on type
			response, err := s.processFederationMessage(ctx, msg)
			if err != nil {
				log.Printf("Failed to process federation message: %v", err)
				continue
			}

			// Send response
			if err := stream.Send(response); err != nil {
				return err
			}
		}
	}
}

// Helper methods

func (s *FederationServer) getTrustScore(clusterID string) *pb.TrustScore {
	if score, exists := s.trustScores[clusterID]; exists {
		return score
	}

	// Default trust score for new clusters
	defaultScore := &pb.TrustScore{
		ReliabilityScore: 0.5,
		ResponseScore:    1.0,
		ConsensusScore:   1.0,
	}
	s.trustScores[clusterID] = defaultScore
	return defaultScore
}

func (s *FederationServer) updateTrustScore(clusterID string, success bool) {
	score := s.getTrustScore(clusterID)

	// Simple trust update algorithm (could be more sophisticated)
	if success {
		score.ReliabilityScore = min(1.0, score.ReliabilityScore+0.01)
		score.ResponseScore = min(1.0, score.ResponseScore+0.01)
	} else {
		score.ReliabilityScore = max(0.0, score.ReliabilityScore-0.05)
		score.ResponseScore = max(0.0, score.ResponseScore-0.02)
	}
}

func (s *FederationServer) processFederationMessage(ctx context.Context, msg *pb.FederationMessage) (*pb.FederationMessage, error) {
	// TODO: Implement bidirectional federation message processing
	// This would handle real-time sketch sharing, consensus voting, etc.

	return &pb.FederationMessage{
		ClusterId: s.clusterID,
		Timestamp: time.Now().Unix(),
		Nonce:     "response-nonce", // Generate proper nonce
	}, nil
}

func min(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

func max(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

// Server startup and main function
func main() {
	// Configuration from environment
	clusterID := os.Getenv("CLUSTER_ID")
	if clusterID == "" {
		clusterID = "default-cluster"
	}

	listenAddr := os.Getenv("LISTEN_ADDR")
	if listenAddr == "" {
		listenAddr = ":9443"
	}

	// Initialize HLL store
	store := hll.NewMemoryStore()

	// Initialize keyring (in production, load from secure storage)
	keyring := signing.NewSimpleKeyring()
	// TODO: Load actual cluster keys from configuration

	// Create federation server
	federationServer := NewFederationServer(clusterID, store, keyring)

	// Setup gRPC server
	grpcServer := grpc.NewServer(
		grpc.UnaryInterceptor(loggingInterceptor),
	)

	pb.RegisterFederatorServer(grpcServer, federationServer)

	// Start listening
	lis, err := net.Listen("tcp", listenAddr)
	if err != nil {
		log.Fatalf("Failed to listen: %v", err)
	}

	// Handle graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		<-sigChan
		log.Println("Shutting down federation server...")
		grpcServer.GracefulStop()
	}()

	log.Printf("Federation server listening on %s (cluster: %s)", listenAddr, clusterID)
	if err := grpcServer.Serve(lis); err != nil {
		log.Fatalf("Failed to serve: %v", err)
	}
}

// gRPC interceptors
func loggingInterceptor(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
	start := time.Now()

	resp, err := handler(ctx, req)

	duration := time.Since(start)
	status := "OK"
	if err != nil {
		status = "ERROR"
	}

	log.Printf("gRPC %s %s %v", info.FullMethod, status, duration)

	return resp, err
}