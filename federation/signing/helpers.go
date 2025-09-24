package signing

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/binary"
	"errors"

	fpb "github.com/Connerlevi/A-Swarm/federation/pb"
)

// FillNonce sets a 128-bit random nonce if empty (defends against cross-channel replay within same ts/seq).
func FillNonce(nonce []byte) []byte {
	if len(nonce) != 0 {
		return nonce
	}
	var b [16]byte
	_, _ = rand.Read(b[:])
	return b[:]
}

// ---------- ShareSketch ----------

func SignShareSketchEd25519(priv ed25519.PrivateKey, req *fpb.ShareSketchRequest) error {
	req.Nonce = FillNonce(req.GetNonce())
	view := ShareSketchSignView(req)
	sig, err := Ed25519Sign(priv, view)
	if err != nil {
		return err
	}
	req.Auth = &fpb.ShareSketchRequest_SignatureEd25519{SignatureEd25519: sig}
	return nil
}

func SignShareSketchHMAC(key []byte, req *fpb.ShareSketchRequest) error {
	req.Nonce = FillNonce(req.GetNonce())
	view := ShareSketchSignView(req)
	mac, err := HMACSign(key, view)
	if err != nil {
		return err
	}
	req.Auth = &fpb.ShareSketchRequest_HmacSha256{HmacSha256: mac}
	return nil
}

// Verify side: choose keying based on which oneof is set.
func VerifyShareSketch(keys Keyring, req *fpb.ShareSketchRequest) error {
	view := ShareSketchSignView(req)
	switch a := req.GetAuth().(type) {
	case *fpb.ShareSketchRequest_HmacSha256:
		key := keys.HMACKey(req.GetClusterId())
		if len(key) == 0 {
			return ErrAuth
		}
		return HMACVerify(key, view, a.HmacSha256)
	case *fpb.ShareSketchRequest_SignatureEd25519:
		pub := ed25519.PublicKey(keys.Ed25519Pub(req.GetClusterId()))
		if len(pub) != ed25519.PublicKeySize {
			return ErrAuth
		}
		return Ed25519Verify(pub, view, a.SignatureEd25519)
	default:
		return errors.New("no auth provided")
	}
}

// ---------- HealthReport ----------

func SignHealthEd25519(priv ed25519.PrivateKey, req *fpb.HealthReportRequest) error {
	req.Nonce = FillNonce(req.GetNonce())
	view := HealthReportSignView(req)
	sig, err := Ed25519Sign(priv, view)
	if err != nil {
		return err
	}
	req.Auth = &fpb.HealthReportRequest_SignatureEd25519{SignatureEd25519: sig}
	return nil
}

func SignHealthHMAC(key []byte, req *fpb.HealthReportRequest) error {
	req.Nonce = FillNonce(req.GetNonce())
	view := HealthReportSignView(req)
	mac, err := HMACSign(key, view)
	if err != nil {
		return err
	}
	req.Auth = &fpb.HealthReportRequest_HmacSha256{HmacSha256: mac}
	return nil
}

func VerifyHealth(keys Keyring, req *fpb.HealthReportRequest) error {
	view := HealthReportSignView(req)
	switch a := req.GetAuth().(type) {
	case *fpb.HealthReportRequest_HmacSha256:
		key := keys.HMACKey(req.GetClusterId())
		if len(key) == 0 {
			return ErrAuth
		}
		return HMACVerify(key, view, a.HmacSha256)
	case *fpb.HealthReportRequest_SignatureEd25519:
		pub := ed25519.PublicKey(keys.Ed25519Pub(req.GetClusterId()))
		if len(pub) != ed25519.PublicKeySize {
			return ErrAuth
		}
		return Ed25519Verify(pub, view, a.SignatureEd25519)
	default:
		return errors.New("no auth provided")
	}
}

// ---------- RequestSketch ----------

func SignRequestSketchEd25519(priv ed25519.PrivateKey, req *fpb.RequestSketchRequest) error {
	req.Nonce = FillNonce(req.GetNonce())
	view := RequestSketchSignView(req)
	sig, err := Ed25519Sign(priv, view)
	if err != nil {
		return err
	}
	req.Auth = &fpb.RequestSketchRequest_SignatureEd25519{SignatureEd25519: sig}
	return nil
}

func SignRequestSketchHMAC(key []byte, req *fpb.RequestSketchRequest) error {
	req.Nonce = FillNonce(req.GetNonce())
	view := RequestSketchSignView(req)
	mac, err := HMACSign(key, view)
	if err != nil {
		return err
	}
	req.Auth = &fpb.RequestSketchRequest_HmacSha256{HmacSha256: mac}
	return nil
}

func VerifyRequestSketch(keys Keyring, req *fpb.RequestSketchRequest) error {
	view := RequestSketchSignView(req)
	switch a := req.GetAuth().(type) {
	case *fpb.RequestSketchRequest_HmacSha256:
		key := keys.HMACKey(req.GetRequestingClusterId())
		if len(key) == 0 {
			return ErrAuth
		}
		return HMACVerify(key, view, a.HmacSha256)
	case *fpb.RequestSketchRequest_SignatureEd25519:
		pub := ed25519.PublicKey(keys.Ed25519Pub(req.GetRequestingClusterId()))
		if len(pub) != ed25519.PublicKeySize {
			return ErrAuth
		}
		return Ed25519Verify(pub, view, a.SignatureEd25519)
	default:
		return errors.New("no auth provided")
	}
}

// ----- Replay guard helpers -----

// UniqueKeyForShare returns a stable 24-byte key for replay guard: seq(8)||ts(8)||first8(sketch_hash)
func UniqueKeyForShare(req *fpb.ShareSketchRequest) []byte {
	out := make([]byte, 0, 24)
	var buf [8]byte
	binary.LittleEndian.PutUint64(buf[:], uint64(req.GetSequenceNumber()))
	out = append(out, buf[:]...)
	binary.LittleEndian.PutUint64(buf[:], uint64(req.GetTimestampUnix()))
	out = append(out, buf[:]...)
	var h []byte
	if att := req.GetAttestation(); att != nil {
		if md := att.GetMetadata(); md != nil {
			h = md.GetSketchHash()
		}
	}
	if len(h) >= 8 {
		out = append(out, h[:8]...)
	} else {
		out = append(out, h...)
	}
	return out
}

// UniqueKeyForRequest returns a stable replay guard key for RequestSketch
func UniqueKeyForRequest(req *fpb.RequestSketchRequest) []byte {
	out := make([]byte, 0, 24)
	var buf [8]byte
	binary.LittleEndian.PutUint64(buf[:], uint64(req.GetSequenceNumber()))
	out = append(out, buf[:]...)
	binary.LittleEndian.PutUint64(buf[:], uint64(req.GetTimestampUnix()))
	out = append(out, buf[:]...)
	// Use first 8 bytes of nonce for uniqueness
	if len(req.GetNonce()) >= 8 {
		out = append(out, req.GetNonce()[:8]...)
	} else {
		out = append(out, req.GetNonce()...)
	}
	return out
}

// UniqueKeyForHealth returns a stable replay guard key for HealthReport
func UniqueKeyForHealth(req *fpb.HealthReportRequest) []byte {
	out := make([]byte, 0, 24)
	var buf [8]byte
	binary.LittleEndian.PutUint64(buf[:], uint64(req.GetSequenceNumber()))
	out = append(out, buf[:]...)
	binary.LittleEndian.PutUint64(buf[:], uint64(req.GetTimestampUnix()))
	out = append(out, buf[:]...)
	// Use first 8 bytes of nonce for uniqueness
	if len(req.GetNonce()) >= 8 {
		out = append(out, req.GetNonce()[:8]...)
	} else {
		out = append(out, req.GetNonce()...)
	}
	return out
}