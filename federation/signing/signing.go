package signing

import (
	"crypto/ed25519"
	"crypto/hmac"
	"crypto/sha256"
	"errors"

	"google.golang.org/protobuf/proto"
)

var ErrAuth = errors.New("auth/verify failed")

// A domain tag to separate signatures from other protocols/components.
const domainTag = "ASWARM-FEDERATION-V1"

// CanonicalBytes returns deterministic protobuf bytes for signing/HMAC.
func CanonicalBytes(m proto.Message) ([]byte, error) {
	return proto.MarshalOptions{Deterministic: true}.Marshal(m)
}

// addDomain binds bytes to our protocol to prevent cross-protocol reuse.
func addDomain(b []byte) []byte {
	// domainTag || 0x00 || msg
	out := make([]byte, 0, len(domainTag)+1+len(b))
	out = append(out, domainTag...)
	out = append(out, 0)
	out = append(out, b...)
	return out
}

// Ed25519Sign signs the deterministic bytes of a protobuf message (with domain tag).
func Ed25519Sign(priv ed25519.PrivateKey, m proto.Message) ([]byte, error) {
	b, err := CanonicalBytes(m)
	if err != nil {
		return nil, err
	}
	return ed25519.Sign(priv, addDomain(b)), nil
}

// Ed25519Verify verifies an Ed25519 signature (with domain tag).
func Ed25519Verify(pub ed25519.PublicKey, m proto.Message, sig []byte) error {
	b, err := CanonicalBytes(m)
	if err != nil {
		return err
	}
	if !ed25519.Verify(pub, addDomain(b), sig) {
		return ErrAuth
	}
	return nil
}

// HMACSign creates HMAC-SHA256 over deterministic protobuf bytes (with domain tag).
func HMACSign(key []byte, m proto.Message) ([]byte, error) {
	b, err := CanonicalBytes(m)
	if err != nil {
		return nil, err
	}
	h := hmac.New(sha256.New, key)
	_, _ = h.Write(addDomain(b))
	return h.Sum(nil), nil
}

// HMACVerify verifies HMAC-SHA256 (with domain tag).
func HMACVerify(key []byte, m proto.Message, mac []byte) error {
	b, err := CanonicalBytes(m)
	if err != nil {
		return err
	}
	h := hmac.New(sha256.New, key)
	_, _ = h.Write(addDomain(b))
	if !hmac.Equal(mac, h.Sum(nil)) {
		return ErrAuth
	}
	return nil
}