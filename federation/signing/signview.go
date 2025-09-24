package signing

import (
	fpb "github.com/Connerlevi/A-Swarm/federation/pb"
)

// Views exclude auth fields; they're what we actually sign/HMAC.

// ShareSketchSignView builds the signing view for ShareSketchRequest
// Uses the new explicit SignView message from protobuf
func ShareSketchSignView(req *fpb.ShareSketchRequest) *fpb.ShareSketchSignView {
	return &fpb.ShareSketchSignView{
		Attestation:     req.GetAttestation(),
		TimestampUnix:   req.GetTimestampUnix(),
		SequenceNumber:  req.GetSequenceNumber(),
		Nonce:           req.GetNonce(),
		ClusterId:       req.GetClusterId(),
	}
}

// HealthReportSignView builds the signing view for HealthReportRequest
func HealthReportSignView(req *fpb.HealthReportRequest) *fpb.HealthReportSignView {
	return &fpb.HealthReportSignView{
		ClusterId:      req.GetClusterId(),
		Capabilities:   req.GetCapabilities(),
		Metrics:        req.GetMetrics(),
		TimestampUnix:  req.GetTimestampUnix(),
		SequenceNumber: req.GetSequenceNumber(),
		Nonce:          req.GetNonce(),
	}
}

// RequestSketchSignView builds the signing view for RequestSketchRequest
func RequestSketchSignView(req *fpb.RequestSketchRequest) *fpb.RequestSketchSignView {
	return &fpb.RequestSketchSignView{
		RequestingClusterId: req.GetRequestingClusterId(),
		TargetAntibodyId:    req.GetTargetAntibodyId(),
		Environment:         req.GetEnvironment(),
		WindowStartUnix:     req.GetWindowStartUnix(),
		WindowSizeSeconds:   req.GetWindowSizeSeconds(),
		SignatureType:       req.GetSignatureType(),
		TimestampUnix:       req.GetTimestampUnix(),
		SequenceNumber:      req.GetSequenceNumber(),
		Nonce:               req.GetNonce(),
	}
}