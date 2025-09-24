package hllcodec

import (
	"bytes"
	"crypto/sha256"
	"errors"
	"time"

	"github.com/a-swarm/prototype/hll"
	fpb "github.com/Connerlevi/A-Swarm/federation/pb"
)

var (
	ErrIncompatibleHLL = errors.New("incompatible HLL configuration")
	ErrInvalidMetadata = errors.New("invalid sketch metadata")
	ErrCorruptSketch   = errors.New("sketch hash mismatch")
)

// PackSketch converts an HLL sketch and metadata into a SketchAttestation
func PackSketch(antibodyID, environment string, signatureType fpb.SignatureType, windowStart time.Time, windowSize time.Duration, sketch hll.HLL, phase fpb.AntibodyPhase) (*fpb.SketchAttestation, error) {
	if antibodyID == "" || environment == "" {
		return nil, ErrInvalidMetadata
	}
	if windowSize < 0 {
		return nil, ErrInvalidMetadata
	}

	// Marshal HLL to binary
	sketchData, err := sketch.MarshalBinary()
	if err != nil {
		return nil, err
	}

	// Create canonical hash of sketch data
	hash := sha256.Sum256(sketchData)

	metadata := &fpb.SketchMetadata{
		AntibodyId:          antibodyID,
		Environment:         environment,
		WindowStartUnix:     uint64(windowStart.UTC().Unix()),
		WindowSizeSeconds:   uint64(windowSize / time.Second),
		SignatureType:       signatureType,
		CardinalityEstimate: uint64(sketch.Count()),
		ConfidenceScore:     0.0, // To be filled by fitness evaluator
		SketchHash:          hash[:],
		// HLL config is in the binary header, no need for redundant fields
	}

	attestation := &fpb.SketchAttestation{
		Metadata:      metadata,
		SketchData:    sketchData,
		Phase:         phase,
		Qc:            nil, // To be filled by quorum certificate assembly
		LineageHashes: nil, // To be filled by evolution system
	}

	return attestation, nil
}

// UnpackSketch extracts and validates an HLL sketch from a SketchAttestation
func UnpackSketch(attestation *fpb.SketchAttestation, expectedConfig hll.HLLConfig) (hll.HLL, error) {
	if attestation == nil || attestation.Metadata == nil {
		return nil, ErrInvalidMetadata
	}

	metadata := attestation.Metadata
	if metadata.AntibodyId == "" || metadata.Environment == "" || len(attestation.SketchData) == 0 {
		return nil, ErrInvalidMetadata
	}

	// Validate HLL compatibility by peeking the binary header (no proto changes required)
	if err := validateHeader(attestation.SketchData, expectedConfig); err != nil {
		return nil, err
	}

	// Create compatible HLL instance
	sketch, err := hll.NewDense(expectedConfig)
	if err != nil {
		return nil, err
	}

	// Unmarshal binary data
	if err := sketch.UnmarshalBinary(attestation.SketchData); err != nil {
		return nil, err
	}

	// Verify sketch hash integrity
	hash := sha256.Sum256(attestation.SketchData)
	if !bytes.Equal(hash[:], metadata.SketchHash) {
		return nil, ErrCorruptSketch
	}

	return sketch, nil
}

// ValidateCompatibility checks if an attestation is compatible with local HLL config
func ValidateCompatibility(attestation *fpb.SketchAttestation, localConfig hll.HLLConfig) error {
	if attestation == nil || attestation.Metadata == nil {
		return ErrInvalidMetadata
	}

	if err := validateHeader(attestation.SketchData, localConfig); err != nil {
		return err
	}

	return nil
}

// CreateSketchKey builds an hll.SketchKey from protobuf metadata
func CreateSketchKey(metadata *fpb.SketchMetadata) hll.SketchKey {
	return hll.SketchKey{
		AntibodyID:    metadata.AntibodyId,
		Environment:   metadata.Environment,
		WindowStart:   time.Unix(int64(metadata.WindowStartUnix), 0).UTC(),
		WindowSize:    time.Duration(metadata.WindowSizeSeconds) * time.Second,
		SignatureType: convertSignatureType(metadata.SignatureType),
	}
}

// convertSignatureType maps protobuf enum to string
func convertSignatureType(sigType fpb.SignatureType) string {
	switch sigType {
	case fpb.SignatureType_SIGNATURE_TYPE_IOC_HASH:
		return "ioc_hash"
	case fpb.SignatureType_SIGNATURE_TYPE_BEHAVIORAL:
		return "behavioral"
	case fpb.SignatureType_SIGNATURE_TYPE_NETWORK:
		return "network"
	case fpb.SignatureType_SIGNATURE_TYPE_PROCESS:
		return "process"
	default:
		return "unknown"
	}
}

// ConvertToProtoSignatureType maps string to protobuf enum
func ConvertToProtoSignatureType(sigType string) fpb.SignatureType {
	switch sigType {
	case "ioc_hash":
		return fpb.SignatureType_SIGNATURE_TYPE_IOC_HASH
	case "behavioral":
		return fpb.SignatureType_SIGNATURE_TYPE_BEHAVIORAL
	case "network":
		return fpb.SignatureType_SIGNATURE_TYPE_NETWORK
	case "process":
		return fpb.SignatureType_SIGNATURE_TYPE_PROCESS
	default:
		return fpb.SignatureType_SIGNATURE_TYPE_UNKNOWN
	}
}

// --- internal helpers ---

// validateHeader peeks at the 19-byte dense header to verify version/precision/salt.
// Header layout matches hll/hll_dense.go: version(1)|precision(1)|salt(8)|sparse_threshold(4)|flags(1)|reg_len(4)
func validateHeader(b []byte, cfg hll.HLLConfig) error {
	if len(b) < 19 {
		return ErrCorruptSketch
	}
	ver := b[0]
	prec := int(b[1])
	// salt little-endian u64
	s := uint64(b[2]) |
		uint64(b[3])<<8 |
		uint64(b[4])<<16 |
		uint64(b[5])<<24 |
		uint64(b[6])<<32 |
		uint64(b[7])<<40 |
		uint64(b[8])<<48 |
		uint64(b[9])<<56
	// versionCode is 1 for "v1"
	if ver != 1 || cfg.Version != "v1" {
		return ErrIncompatibleHLL
	}
	if prec != cfg.Precision || s != uint64(cfg.Salt) {
		return ErrIncompatibleHLL
	}
	return nil
}