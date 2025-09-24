package signing

// Keyring supplies per-cluster keys to verification helpers.
type Keyring interface {
	HMACKey(clusterID string) []byte    // may return nil
	Ed25519Pub(clusterID string) []byte // 32 bytes or nil
}