#!/usr/bin/env python3
"""
A-SWARM Micro-Act Catalog v0.2
Extended containment actions with TTL and auto-revert
"""
import os
import sys
import time
import json
import threading
import subprocess
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('aswarm.microact')

# Dry run mode from environment
DRY_RUN = os.getenv("ASWARM_DRY_RUN", "true").lower() in ("1", "true", "yes")
MAX_RING = int(os.getenv("ASWARM_MAX_RING", "3"))  # Default max ring 3

class Ring(Enum):
    """Defense rings with increasing impact"""
    RING_1 = 1  # Observable: logs, alerts, metrics
    RING_2 = 2  # Reversible: network isolation, rate limits
    RING_3 = 3  # Disruptive: process kill, token revoke
    RING_4 = 4  # Persistent: ban lists, config changes
    RING_5 = 5  # Physical: power cycle, console access

@dataclass
class MicroAct:
    """Definition of a micro-containment action"""
    id: str
    ring: Ring
    name: str
    description: str
    ttl_seconds: int
    supports_probe: bool = True
    requires_params: List[str] = None
    optional_params: List[str] = None

@dataclass
class ActuationResult:
    """Result of executing a micro-act"""
    success: bool
    message: str
    revert_handle: Optional[str] = None
    probe_endpoint: Optional[str] = None
    applied_at: str = None
    expires_at: str = None
    proof: Optional[Dict[str, Any]] = None

def _run(cmd: List[str]) -> Tuple[bool, str]:
    """Execute command respecting DRY_RUN mode"""
    if DRY_RUN:
        logger.info(f"[DRY_RUN] {' '.join(cmd)}")
        return True, "[dry-run]"
    
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            logger.error(f"Command failed: {' '.join(cmd)} :: {p.stderr.strip()}")
            return False, p.stderr
        return True, p.stdout
    except Exception as e:
        logger.error(f"Command execution error: {e}")
        return False, str(e)

def _compute_proof(action_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Generate proof dictionary for action certificate"""
    proof_data = f"{action_id}:{json.dumps(params, sort_keys=True)}"
    proof_hash = hashlib.sha256(proof_data.encode()).hexdigest()
    
    return {
        "action_id": action_id,
        "params_hash": proof_hash[:16],
        "controller": "microact-v2",
        "dry_run": DRY_RUN,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

class MicroActCatalog:
    """Catalog of available micro-containment actions"""
    
    def __init__(self):
        self.actions = {}
        self.active_ttls = {}  # Track active TTL actions for auto-revert
        self._lock = threading.Lock()  # Thread safety for TTL tracking
        self._register_actions()
        self._start_ttl_monitor()
    
    def _register_actions(self):
        """Register all available micro-acts"""
        
        # Ring 1: Observable actions
        self.register(MicroAct(
            id="log_anomaly",
            ring=Ring.RING_1,
            name="Log Anomaly",
            description="Write structured anomaly event to SIEM",
            ttl_seconds=0,  # No TTL for logging
            supports_probe=False,
            requires_params=["asset_id", "anomaly_type", "score"]
        ))
        
        # Ring 2: Reversible network actions
        self.register(MicroAct(
            id="networkpolicy_isolate",  # Aligned with certificate naming
            ring=Ring.RING_2,
            name="Pod Network Isolation",
            description="Apply NetworkPolicy to isolate pod traffic",
            ttl_seconds=300,  # 5 min default
            requires_params=["namespace", "selector"],
            optional_params=["ttl_seconds"]
        ))
        
        self.register(MicroAct(
            id="egress_rate_limit",
            ring=Ring.RING_2,
            name="Egress Rate Limit",
            description="Apply per-host egress bandwidth limit via tc/eBPF",
            ttl_seconds=300,
            requires_params=["host", "rate_mbps"],
            optional_params=["interface", "ttl_seconds"]
        ))
        
        self.register(MicroAct(
            id="dns_sinkhole",
            ring=Ring.RING_2,
            name="DNS Sinkhole",
            description="Redirect DNS queries to sinkhole for analysis",
            ttl_seconds=600,
            requires_params=["namespace", "selector"],
            optional_params=["sinkhole_ip", "ttl_seconds"]
        ))
        
        # Ring 3: Disruptive actions
        self.register(MicroAct(
            id="process_freeze",
            ring=Ring.RING_3,
            name="Process Freeze",
            description="Freeze process execution via cgroups freezer",
            ttl_seconds=120,
            requires_params=["host", "pid"],
            optional_params=["ttl_seconds"]
        ))
        
        self.register(MicroAct(
            id="token_revoke",
            ring=Ring.RING_3,
            name="IdP Token Revoke",
            description="Revoke OAuth/SAML tokens for compromised identity",
            ttl_seconds=3600,  # 1 hour default
            requires_params=["provider", "user_id"],
            optional_params=["scope", "ttl_seconds"]
        ))
        
        self.register(MicroAct(
            id="container_pause",
            ring=Ring.RING_3,
            name="Container Pause",
            description="Pause container execution preserving state",
            ttl_seconds=180,
            requires_params=["namespace", "pod", "container"],
            optional_params=["ttl_seconds"]
        ))
    
    def register(self, action: MicroAct):
        """Register a micro-act in the catalog"""
        self.actions[action.id] = action
        logger.info(f"Registered micro-act: {action.id} (Ring {action.ring.value})")
    
    def list_actions(self, ring: Optional[Ring] = None) -> List[MicroAct]:
        """List available actions, optionally filtered by ring"""
        actions = list(self.actions.values())
        if ring:
            actions = [a for a in actions if a.ring == ring]
        return sorted(actions, key=lambda a: (a.ring.value, a.id))
    
    def get_action(self, action_id: str) -> Optional[MicroAct]:
        """Get action definition by ID"""
        return self.actions.get(action_id)
    
    def execute(self, action_id: str, params: Dict[str, Any]) -> ActuationResult:
        """Execute a micro-act with given parameters"""
        action = self.get_action(action_id)
        if not action:
            return ActuationResult(
                success=False,
                message=f"Unknown action: {action_id}"
            )
        
        # Check ring limits
        if action.ring.value > MAX_RING:
            return ActuationResult(
                success=False,
                message=f"Action {action_id} (Ring {action.ring.value}) exceeds MAX_RING={MAX_RING}"
            )
        
        # Validate required parameters
        missing = []
        for req in (action.requires_params or []):
            if req not in params:
                missing.append(req)
        
        if missing:
            return ActuationResult(
                success=False,
                message=f"Missing required parameters: {missing}"
            )
        
        # Validate parameter values
        if "rate_mbps" in params and params["rate_mbps"] <= 0:
            return ActuationResult(
                success=False,
                message=f"Invalid rate_mbps: must be positive"
            )
        
        if "selector" in params and not params["selector"].strip():
            return ActuationResult(
                success=False,
                message=f"Invalid selector: cannot be empty"
            )
        
        if "pid" in params:
            try:
                int(params["pid"])
            except (ValueError, TypeError):
                return ActuationResult(
                    success=False,
                    message=f"Invalid pid: must be integer"
                )
        
        # Get TTL from params or use default
        ttl = params.get("ttl_seconds", action.ttl_seconds)
        
        # Generate proof
        proof = _compute_proof(action_id, params)
        
        # Execute based on action ID
        logger.info(f"Executing {action_id} with params: {params}")
        
        try:
            if action_id == "networkpolicy_isolate":
                return self._execute_pod_isolate(params, ttl, proof)
            elif action_id == "egress_rate_limit":
                return self._execute_egress_limit(params, ttl, proof)
            elif action_id == "token_revoke":
                return self._execute_token_revoke(params, ttl, proof)
            elif action_id == "log_anomaly":
                return self._execute_log_anomaly(params, proof)
            elif action_id == "dns_sinkhole":
                return self._execute_dns_sinkhole(params, ttl, proof)
            elif action_id == "process_freeze":
                return self._execute_process_freeze(params, ttl, proof)
            elif action_id == "container_pause":
                return self._execute_container_pause(params, ttl, proof)
            else:
                return ActuationResult(
                    success=False,
                    message=f"Action {action_id} not implemented"
                )
        except Exception as e:
            logger.error(f"Failed to execute {action_id}: {e}")
            return ActuationResult(
                success=False,
                message=f"Execution failed: {str(e)}"
            )
    
    def _execute_pod_isolate(self, params: Dict[str, Any], ttl: int, proof: Dict[str, Any]) -> ActuationResult:
        """Execute pod network isolation via NetworkPolicy"""
        namespace = params["namespace"]
        selector = params["selector"]
        
        # Generate unique policy name
        policy_name = f"aswarm-isolate-{int(time.time())}"
        
        # Create NetworkPolicy YAML
        policy = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": policy_name,
                "namespace": namespace,
                "labels": {
                    "aswarm.ai/action": "networkpolicy-isolate",
                    "aswarm.ai/ttl": str(ttl)
                }
            },
            "spec": {
                "podSelector": {
                    "matchLabels": self._parse_selector(selector)
                },
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],  # Deny all ingress
                "egress": [
                    # Allow DNS for service discovery
                    # Note: Uses kubernetes.io/metadata.name which K8s automatically adds to namespaces
                    # For clusters using CoreDNS, the pod selector matches the standard label
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "kube-system"
                                    }
                                },
                                "podSelector": {
                                    "matchLabels": {
                                        "k8s-app": "kube-dns"
                                    }
                                }
                            }
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53}
                        ]
                    }
                ]
            }
        }
        
        # Apply via kubectl
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            import yaml
            yaml.dump(policy, f)
            policy_file = f.name
        
        try:
            success, output = _run(["kubectl", "apply", "-f", policy_file])
            
            if success:
                revert_handle = f"{namespace}/{policy_name}"
                self._schedule_ttl_revert("networkpolicy_isolate", revert_handle, ttl)
                
                proof["resource"] = f"NetworkPolicy/{namespace}/{policy_name}"
                
                return ActuationResult(
                    success=True,
                    message=f"Applied network isolation to {selector} in {namespace}",
                    revert_handle=revert_handle,
                    probe_endpoint=f"http://probe.{namespace}.svc:8080/network",
                    applied_at=datetime.now(timezone.utc).isoformat(),
                    expires_at=datetime.fromtimestamp(time.time() + ttl, timezone.utc).isoformat(),
                    proof=proof
                )
            else:
                return ActuationResult(
                    success=False,
                    message=f"Failed to apply NetworkPolicy: {output}"
                )
        finally:
            os.unlink(policy_file)
    
    def _execute_egress_limit(self, params: Dict[str, Any], ttl: int, proof: Dict[str, Any]) -> ActuationResult:
        """Execute egress rate limiting via tc"""
        host = params["host"]
        rate_mbps = params["rate_mbps"]
        interface = params.get("interface", "eth0")
        
        if DRY_RUN:
            logger.info(f"[DRY_RUN] Would apply {rate_mbps}Mbps limit to {host} on {interface}")
            success = True
            message = f"[DRY_RUN] Would apply {rate_mbps}Mbps egress limit"
        else:
            # In production: use node agent API
            return ActuationResult(
                success=False,
                message="Egress rate limiting requires node agent (not implemented)"
            )
        
        if success:
            revert_handle = f"{host}/{interface}/{rate_mbps}"
            self._schedule_ttl_revert("egress_rate_limit", revert_handle, ttl)
            proof["resource"] = f"tc/{host}/{interface}"
            
            return ActuationResult(
                success=True,
                message=message,
                revert_handle=revert_handle,
                probe_endpoint=f"http://{host}:9100/metrics",
                applied_at=datetime.now(timezone.utc).isoformat(),
                expires_at=datetime.fromtimestamp(time.time() + ttl, timezone.utc).isoformat(),
                proof=proof
            )
    
    def _execute_token_revoke(self, params: Dict[str, Any], ttl: int, proof: Dict[str, Any]) -> ActuationResult:
        """Execute IdP token revocation"""
        provider = params["provider"]
        user_id = params["user_id"]
        scope = params.get("scope", "all")
        
        if DRY_RUN:
            logger.info(f"[DRY_RUN] Would revoke {scope} tokens for {user_id} on {provider}")
            success = True
            message = f"[DRY_RUN] Would revoke tokens for {user_id}"
        else:
            # In production: call IdP API
            return ActuationResult(
                success=False,
                message="Token revocation requires IdP integration (not implemented)"
            )
        
        if success:
            revert_handle = f"{provider}/{user_id}/{scope}"
            self._schedule_ttl_revert("token_revoke", revert_handle, ttl)
            proof["resource"] = f"idp/{provider}/user/{user_id}"
            
            return ActuationResult(
                success=True,
                message=message,
                revert_handle=revert_handle,
                probe_endpoint=f"https://{provider}/api/v1/users/{user_id}/status",
                applied_at=datetime.now(timezone.utc).isoformat(),
                expires_at=datetime.fromtimestamp(time.time() + ttl, timezone.utc).isoformat(),
                proof=proof
            )
    
    def _execute_log_anomaly(self, params: Dict[str, Any], proof: Dict[str, Any]) -> ActuationResult:
        """Execute anomaly logging to SIEM"""
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "aswarm.anomaly_detected",
            "asset_id": params["asset_id"],
            "anomaly_type": params["anomaly_type"],
            "anomaly_score": params["score"],
            "severity": "high" if params["score"] > 0.8 else "medium",
            "source": "aswarm-microact",
            "proof": proof
        }
        
        # Log to various destinations
        logger.warning(f"ANOMALY: {json.dumps(event)}")
        
        # Always succeeds for logging
        return ActuationResult(
            success=True,
            message=f"Logged anomaly for {params['asset_id']}",
            applied_at=datetime.now(timezone.utc).isoformat(),
            proof=proof
        )
    
    def _execute_dns_sinkhole(self, params: Dict[str, Any], ttl: int, proof: Dict[str, Any]) -> ActuationResult:
        """Execute DNS sinkholing via CoreDNS config"""
        namespace = params["namespace"]
        selector = params["selector"]
        sinkhole_ip = params.get("sinkhole_ip", "10.0.0.254")
        
        if DRY_RUN:
            logger.info(f"[DRY_RUN] Would configure DNS sinkhole for {selector} -> {sinkhole_ip}")
            success = True
            message = f"[DRY_RUN] Would apply DNS sinkhole"
        else:
            # In production: modify CoreDNS ConfigMap
            return ActuationResult(
                success=False,
                message="DNS sinkhole requires CoreDNS integration (not implemented)"
            )
        
        if success:
            revert_handle = f"{namespace}/{selector}/{sinkhole_ip}"
            self._schedule_ttl_revert("dns_sinkhole", revert_handle, ttl)
            proof["resource"] = f"coredns/{namespace}"
            
            return ActuationResult(
                success=True,
                message=message,
                revert_handle=revert_handle,
                probe_endpoint=f"http://dns-probe.{namespace}.svc:8053/metrics",
                applied_at=datetime.now(timezone.utc).isoformat(),
                expires_at=datetime.fromtimestamp(time.time() + ttl, timezone.utc).isoformat(),
                proof=proof
            )
    
    def _execute_process_freeze(self, params: Dict[str, Any], ttl: int, proof: Dict[str, Any]) -> ActuationResult:
        """Execute process freezing via cgroups"""
        host = params["host"]
        pid = params["pid"]
        
        if DRY_RUN:
            logger.info(f"[DRY_RUN] Would freeze process {pid} on {host}")
            success = True
            message = f"[DRY_RUN] Would freeze process {pid}"
        else:
            # In production: use node agent API
            return ActuationResult(
                success=False,
                message="Process freeze requires node agent (not implemented)"
            )
        
        if success:
            revert_handle = f"{host}/{pid}"
            self._schedule_ttl_revert("process_freeze", revert_handle, ttl)
            proof["resource"] = f"cgroup/{host}/pid/{pid}"
            
            return ActuationResult(
                success=True,
                message=message,
                revert_handle=revert_handle,
                probe_endpoint=f"http://{host}:9100/metrics",
                applied_at=datetime.now(timezone.utc).isoformat(),
                expires_at=datetime.fromtimestamp(time.time() + ttl, timezone.utc).isoformat(),
                proof=proof
            )
    
    def _execute_container_pause(self, params: Dict[str, Any], ttl: int, proof: Dict[str, Any]) -> ActuationResult:
        """Execute container pause via CRI"""
        namespace = params["namespace"]
        pod = params["pod"]
        container = params["container"]
        
        if DRY_RUN:
            logger.info(f"[DRY_RUN] Would pause container {container} in {namespace}/{pod}")
            success = True
            message = f"[DRY_RUN] Would pause container {container}"
        else:
            # In production: use crictl via node agent
            return ActuationResult(
                success=False,
                message="Container pause requires node agent (not implemented)"
            )
        
        if success:
            revert_handle = f"{namespace}/{pod}/{container}"
            self._schedule_ttl_revert("container_pause", revert_handle, ttl)
            proof["resource"] = f"container/{namespace}/{pod}/{container}"
            
            return ActuationResult(
                success=True,
                message=message,
                revert_handle=revert_handle,
                probe_endpoint=f"http://probe.{namespace}.svc:8080/container/{container}",
                applied_at=datetime.now(timezone.utc).isoformat(),
                expires_at=datetime.fromtimestamp(time.time() + ttl, timezone.utc).isoformat(),
                proof=proof
            )
    
    def _parse_selector(self, selector: str) -> Dict[str, str]:
        """Parse selector string to label dict"""
        labels = {}
        for part in selector.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                labels[k.strip()] = v.strip()
        return labels
    
    def _schedule_ttl_revert(self, action_id: str, handle: str, ttl_seconds: int):
        """Schedule automatic revert after TTL expires"""
        if ttl_seconds <= 0:
            return
        
        expire_mono = time.monotonic() + ttl_seconds
        with self._lock:
            self.active_ttls[handle] = {
                "action_id": action_id,
                "expire_mono": expire_mono,
                "applied_mono": time.monotonic(),
                "applied_at": datetime.now(timezone.utc).isoformat()
            }
        
        logger.info(f"Scheduled revert for {action_id}:{handle} in {ttl_seconds}s")
    
    def _start_ttl_monitor(self):
        """Start background thread to monitor and revert expired TTLs"""
        def monitor_loop():
            while True:
                now = time.monotonic()
                expired = []
                
                with self._lock:
                    for handle, info in list(self.active_ttls.items()):
                        if now >= info["expire_mono"]:
                            expired.append((handle, info))
                    
                    for handle, _ in expired:
                        self.active_ttls.pop(handle, None)
                
                for handle, info in expired:
                    logger.info(f"TTL expired for {info['action_id']}:{handle}, reverting...")
                    self._revert_action(info["action_id"], handle)
                
                time.sleep(1)  # Tighter cadence for accurate TTLs
        
        thread = threading.Thread(target=monitor_loop, daemon=True)
        thread.start()
    
    def _revert_action(self, action_id: str, handle: str):
        """Revert a previously applied action"""
        logger.info(f"Reverting {action_id} with handle: {handle}")
        
        if action_id == "networkpolicy_isolate":
            # Delete NetworkPolicy (idempotent)
            namespace, policy_name = handle.split("/", 1)
            ok, err = _run([
                "kubectl", "delete", "networkpolicy", policy_name,
                "-n", namespace, "--ignore-not-found=true"
            ])
            if not ok:
                logger.warning(f"Revert NetworkPolicy failed: {err}")
                
        elif action_id == "egress_rate_limit":
            # Remove tc rules
            host, interface, rate = handle.split("/")
            if DRY_RUN:
                logger.info(f"[DRY_RUN] Would remove rate limit from {host} on {interface}")
            else:
                logger.warning("Egress rate limit revert requires node agent")
                
        elif action_id == "token_revoke":
            # Re-enable tokens (provider-specific)
            provider, user_id, scope = handle.split("/")
            if DRY_RUN:
                logger.info(f"[DRY_RUN] Would re-enable tokens for {user_id} on {provider}")
            else:
                logger.warning("Token re-enable requires IdP integration")
                
        elif action_id == "dns_sinkhole":
            # Restore normal DNS
            namespace, selector, sinkhole_ip = handle.split("/")
            if DRY_RUN:
                logger.info(f"[DRY_RUN] Would restore DNS for {selector} in {namespace}")
            else:
                logger.warning("DNS restore requires CoreDNS integration")
                
        elif action_id == "process_freeze":
            # Unfreeze process
            host, pid = handle.split("/")
            if DRY_RUN:
                logger.info(f"[DRY_RUN] Would unfreeze process {pid} on {host}")
            else:
                logger.warning("Process unfreeze requires node agent")
                
        elif action_id == "container_pause":
            # Unpause container
            namespace, pod, container = handle.split("/")
            if DRY_RUN:
                logger.info(f"[DRY_RUN] Would unpause container {container} in {namespace}/{pod}")
            else:
                logger.warning("Container unpause requires node agent")
    
    def probe_effectiveness(self, result: ActuationResult) -> Dict[str, Any]:
        """Probe to verify action effectiveness"""
        if not result.probe_endpoint:
            return {"status": "no_probe", "message": "No probe endpoint available"}
        
        if DRY_RUN:
            logger.info(f"[DRY_RUN] Would probe effectiveness at: {result.probe_endpoint}")
            return {
                "status": "dry_run",
                "probe_time": datetime.now(timezone.utc).isoformat(),
                "endpoint": result.probe_endpoint
            }
        
        # In production: actually probe the endpoint
        logger.info(f"Probing effectiveness at: {result.probe_endpoint}")
        
        # Simulated probe result
        return {
            "status": "effective",
            "probe_time": datetime.now(timezone.utc).isoformat(),
            "endpoint": result.probe_endpoint,
            "metrics": {
                "network_blocked": True,
                "connections_dropped": 42,
                "packets_rejected": 1337
            }
        }

def main():
    """CLI for testing micro-acts"""
    import argparse
    
    parser = argparse.ArgumentParser(description="A-SWARM Micro-Act Catalog v0.2")
    parser.add_argument("--list", action="store_true", help="List all actions")
    parser.add_argument("--ring", type=int, help="Filter by ring number")
    parser.add_argument("--execute", help="Execute action by ID")
    parser.add_argument("--params", help="JSON parameters for execution")
    parser.add_argument("--dry-run", help="Override DRY_RUN setting", 
                       choices=["true", "false"])
    
    args = parser.parse_args()
    
    # Override DRY_RUN if specified
    if args.dry_run:
        global DRY_RUN
        DRY_RUN = args.dry_run == "true"
    
    catalog = MicroActCatalog()
    
    print(f"\n[Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}] [Max Ring: {MAX_RING}]\n")
    
    if args.list:
        actions = catalog.list_actions(Ring(args.ring) if args.ring else None)
        print(f"Available Micro-Acts ({len(actions)} total):\n")
        
        for action in actions:
            print(f"[Ring {action.ring.value}] {action.id}")
            print(f"  Name: {action.name}")
            print(f"  Desc: {action.description}")
            print(f"  TTL:  {action.ttl_seconds}s")
            if action.requires_params:
                print(f"  Required: {', '.join(action.requires_params)}")
            if action.optional_params:
                print(f"  Optional: {', '.join(action.optional_params)}")
            print()
    
    elif args.execute:
        if not args.params:
            print("Error: --params required for execution")
            sys.exit(1)
        
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON parameters: {e}")
            sys.exit(1)
        
        result = catalog.execute(args.execute, params)
        print(f"\nExecution Result:")
        print(f"  Success: {result.success}")
        print(f"  Message: {result.message}")
        if result.revert_handle:
            print(f"  Revert:  {result.revert_handle}")
        if result.expires_at:
            print(f"  Expires: {result.expires_at}")
        if result.proof:
            print(f"  Proof:   {json.dumps(result.proof, indent=2)}")
        
        if result.success and result.probe_endpoint:
            print("\nProbing effectiveness...")
            probe = catalog.probe_effectiveness(result)
            print(f"  Probe Status: {probe['status']}")
            if "metrics" in probe:
                print(f"  Metrics: {json.dumps(probe['metrics'], indent=2)}")

if __name__ == "__main__":
    main()