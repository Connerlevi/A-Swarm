#!/usr/bin/env python3
"""
A-SWARM Kill-Switch Governance v1 - Dual-Control Emergency Controls
ConfigMap-based approval workflow for destructive operations
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Set, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
import secrets
import base64

try:
    from kubernetes import client, config, watch
    from kubernetes.client import ApiException
    HAS_K8S = True
except ImportError:
    HAS_K8S = False
    logging.warning("Kubernetes client not available - kill-switch will operate in local mode")

logger = logging.getLogger('aswarm.killswitch')

class RequestType(Enum):
    """Types of kill-switch requests"""
    DISABLE_FASTPATH = "disable_fastpath"  # Stop processing elevations
    ENABLE_AUDIT_ONLY = "enable_audit_only"  # Degraded mode
    EMERGENCY_SHUTDOWN = "emergency_shutdown"  # Full stop
    RESTORE_NORMAL = "restore_normal"  # Return to normal ops
    RELOAD_KEYS = "reload_keys"  # Rotate HMAC keys
    FLUSH_WAL = "flush_wal"  # Clear WAL (data loss!)

class ApprovalRole(Enum):
    """Required approval roles"""
    SECURITY = "security"
    OPERATIONS = "operations"
    SRE = "sre"

@dataclass
class KillSwitchRequest:
    """Kill-switch request requiring dual approval"""
    request_id: str
    request_type: RequestType
    requester: str
    reason: str
    created_at: str  # ISO timestamp
    expires_at: str  # ISO timestamp
    approvals: Dict[str, Dict[str, Any]]  # role -> {approver, timestamp, signature}
    status: str  # pending, approved, executed, expired
    metadata: Dict[str, Any]  # Additional context

@dataclass
class ApprovalSignature:
    """Cryptographic approval signature"""
    approver: str
    role: str
    timestamp: str
    nonce: str
    signature: str  # HMAC of request_id + role + timestamp + nonce

class KillSwitchGovernance:
    """Manages kill-switch requests with dual-control approval"""
    
    # Role requirements for each request type
    APPROVAL_REQUIREMENTS = {
        RequestType.DISABLE_FASTPATH: [ApprovalRole.SECURITY, ApprovalRole.OPERATIONS],
        RequestType.ENABLE_AUDIT_ONLY: [ApprovalRole.OPERATIONS],
        RequestType.EMERGENCY_SHUTDOWN: [ApprovalRole.SECURITY, ApprovalRole.SRE],
        RequestType.RESTORE_NORMAL: [ApprovalRole.OPERATIONS],
        RequestType.RELOAD_KEYS: [ApprovalRole.SECURITY],
        RequestType.FLUSH_WAL: [ApprovalRole.SECURITY, ApprovalRole.OPERATIONS, ApprovalRole.SRE]
    }
    
    def __init__(self, namespace: str = "aswarm",
                 action_callback: Optional[callable] = None,
                 approval_timeout_minutes: int = 10):
        """
        Initialize kill-switch governance
        
        Args:
            namespace: Kubernetes namespace for ConfigMaps
            action_callback: Callback when request is approved
            approval_timeout_minutes: Time window for approvals
        """
        self.namespace = namespace
        self.action_callback = action_callback or self._default_action
        self.approval_timeout = timedelta(minutes=approval_timeout_minutes)
        
        # Pending requests
        self.pending_requests: Dict[str, KillSwitchRequest] = {}
        self.lock = threading.Lock()
        
        # Approval keys (in production, use proper key management)
        self.approval_keys = self._load_approval_keys()
        
        # Initialize Kubernetes client
        self.k8s_api = None
        if HAS_K8S:
            try:
                try:
                    config.load_incluster_config()
                except:
                    config.load_kube_config()
                self.k8s_api = client.CoreV1Api()
                logger.info("Kubernetes client initialized for kill-switch governance")
            except Exception as e:
                logger.error(f"Failed to initialize Kubernetes client: {e}")
        
        # Start watchers
        self.running = False
        self.watcher_thread = None
        self.cleanup_thread = None
        
    def _load_approval_keys(self) -> Dict[str, bytes]:
        """Load approval keys for each role"""
        keys = {}
        
        # Try environment variables first
        for role in ApprovalRole:
            env_key = f"ASWARM_APPROVAL_KEY_{role.value.upper()}"
            key_val = os.environ.get(env_key)
            if key_val:
                if key_val.startswith('base64:'):
                    keys[role.value] = base64.b64decode(key_val[7:])
                else:
                    keys[role.value] = key_val.encode('utf-8')
        
        # Generate default keys if none provided (INSECURE - only for testing)
        if not keys:
            logger.warning("No approval keys configured - generating insecure defaults")
            for role in ApprovalRole:
                keys[role.value] = hashlib.sha256(f"insecure-{role.value}".encode()).digest()
        
        return keys
    
    def start(self):
        """Start governance system"""
        self.running = True
        
        if self.k8s_api:
            # Start ConfigMap watcher
            self.watcher_thread = threading.Thread(target=self._watch_configmaps, daemon=True)
            self.watcher_thread.start()
        
        # Start cleanup thread
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()
        
        logger.info("Kill-switch governance started")
    
    def stop(self):
        """Stop governance system"""
        self.running = False
        if self.watcher_thread:
            self.watcher_thread.join(timeout=5)
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=5)
    
    def create_request(self, request_type: RequestType, requester: str, 
                      reason: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Create a new kill-switch request
        
        Args:
            request_type: Type of request
            requester: Identity of requester
            reason: Human-readable reason
            metadata: Additional context
            
        Returns:
            Request ID
        """
        request_id = f"ks-{int(time.time())}-{secrets.token_hex(4)}"
        now = datetime.now(timezone.utc)
        
        request = KillSwitchRequest(
            request_id=request_id,
            request_type=request_type,
            requester=requester,
            reason=reason,
            created_at=now.isoformat(),
            expires_at=(now + self.approval_timeout).isoformat(),
            approvals={},
            status="pending",
            metadata=metadata or {}
        )
        
        # Store locally
        with self.lock:
            self.pending_requests[request_id] = request
        
        # Create ConfigMap if K8s available
        if self.k8s_api:
            self._create_configmap(request)
        
        # Log structured event
        event = {
            "event": "kill_switch_request_created",
            "request_id": request_id,
            "type": request_type.value,
            "requester": requester,
            "required_approvals": [r.value for r in self.APPROVAL_REQUIREMENTS[request_type]]
        }
        logger.info(json.dumps(event))
        
        return request_id
    
    def approve_request(self, request_id: str, approver: str, role: ApprovalRole) -> bool:
        """
        Approve a kill-switch request
        
        Args:
            request_id: Request to approve
            approver: Identity of approver
            role: Role of approver
            
        Returns:
            True if approval successful
        """
        with self.lock:
            request = self.pending_requests.get(request_id)
            if not request:
                logger.error(f"Request {request_id} not found")
                return False
            
            # Check if already approved by this role
            if role.value in request.approvals:
                logger.warning(f"Request {request_id} already approved by {role.value}")
                return False
            
            # Check if role is required
            required_roles = self.APPROVAL_REQUIREMENTS[request.request_type]
            if role not in required_roles:
                logger.error(f"Role {role.value} not required for {request.request_type.value}")
                return False
            
            # Check expiry
            if datetime.fromisoformat(request.expires_at) < datetime.now(timezone.utc):
                logger.error(f"Request {request_id} has expired")
                return False
            
            # Generate approval signature
            nonce = secrets.token_hex(8)
            timestamp = datetime.now(timezone.utc).isoformat()
            
            # Create signature
            sig_data = f"{request_id}|{role.value}|{timestamp}|{nonce}".encode()
            signature = hashlib.sha256(
                self.approval_keys[role.value] + sig_data
            ).hexdigest()
            
            # Record approval
            request.approvals[role.value] = {
                "approver": approver,
                "timestamp": timestamp,
                "nonce": nonce,
                "signature": signature
            }
            
            # Check if all approvals received
            all_approved = all(
                r.value in request.approvals 
                for r in required_roles
            )
            
            if all_approved:
                request.status = "approved"
                self._execute_request(request)
        
        # Update ConfigMap
        if self.k8s_api:
            self._update_configmap(request)
        
        # Log structured event
        event = {
            "event": "kill_switch_approval",
            "request_id": request_id,
            "approver": approver,
            "role": role.value,
            "all_approved": all_approved
        }
        logger.info(json.dumps(event))
        
        return True
    
    def _execute_request(self, request: KillSwitchRequest):
        """Execute an approved request"""
        try:
            # Invoke action callback
            self.action_callback(request.request_type, request)
            
            # Mark as executed
            request.status = "executed"
            
            # Log structured event
            event = {
                "event": "kill_switch_executed",
                "request_id": request.request_id,
                "type": request.request_type.value,
                "approvals": list(request.approvals.keys())
            }
            logger.info(json.dumps(event))
            
        except Exception as e:
            logger.error(f"Failed to execute kill-switch {request.request_id}: {e}")
            request.status = "failed"
    
    def _default_action(self, request_type: RequestType, request: KillSwitchRequest):
        """Default action handler"""
        logger.warning(f"No action handler configured for {request_type.value}")
    
    def _create_configmap(self, request: KillSwitchRequest):
        """Create ConfigMap for request"""
        if not self.k8s_api:
            return
            
        cm_name = f"killswitch-{request.request_id}"
        
        # Convert request to dict
        data = asdict(request)
        data['request_type'] = request.request_type.value
        
        body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                name=cm_name,
                namespace=self.namespace,
                labels={
                    "aswarm.ai/type": "kill-switch",
                    "aswarm.ai/status": request.status,
                    "aswarm.ai/request-type": request.request_type.value
                }
            ),
            data={
                "request.json": json.dumps(data, indent=2)
            }
        )
        
        try:
            self.k8s_api.create_namespaced_config_map(
                namespace=self.namespace,
                body=body
            )
            logger.debug(f"Created ConfigMap {cm_name}")
        except ApiException as e:
            logger.error(f"Failed to create ConfigMap: {e}")
    
    def _update_configmap(self, request: KillSwitchRequest):
        """Update ConfigMap for request"""
        if not self.k8s_api:
            return
            
        cm_name = f"killswitch-{request.request_id}"
        
        # Convert request to dict
        data = asdict(request)
        data['request_type'] = request.request_type.value
        
        body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                labels={
                    "aswarm.ai/status": request.status
                }
            ),
            data={
                "request.json": json.dumps(data, indent=2)
            }
        )
        
        try:
            self.k8s_api.patch_namespaced_config_map(
                name=cm_name,
                namespace=self.namespace,
                body=body
            )
            logger.debug(f"Updated ConfigMap {cm_name}")
        except ApiException as e:
            logger.error(f"Failed to update ConfigMap: {e}")
    
    def _watch_configmaps(self):
        """Watch for ConfigMap changes"""
        if not self.k8s_api:
            return
            
        w = watch.Watch()
        
        while self.running:
            try:
                # Watch for kill-switch ConfigMaps
                for event in w.stream(
                    self.k8s_api.list_namespaced_config_map,
                    namespace=self.namespace,
                    label_selector="aswarm.ai/type=kill-switch",
                    timeout_seconds=60
                ):
                    if not self.running:
                        break
                        
                    event_type = event['type']
                    cm = event['object']
                    
                    if event_type in ['ADDED', 'MODIFIED']:
                        self._process_configmap(cm)
                        
            except Exception as e:
                if self.running:
                    logger.error(f"ConfigMap watch error: {e}")
                    time.sleep(5)  # Backoff before retry
    
    def _process_configmap(self, cm):
        """Process a kill-switch ConfigMap"""
        try:
            # Extract request data
            request_json = cm.data.get('request.json')
            if not request_json:
                return
                
            data = json.loads(request_json)
            
            # Convert back to KillSwitchRequest
            request = KillSwitchRequest(
                request_id=data['request_id'],
                request_type=RequestType(data['request_type']),
                requester=data['requester'],
                reason=data['reason'],
                created_at=data['created_at'],
                expires_at=data['expires_at'],
                approvals=data['approvals'],
                status=data['status'],
                metadata=data['metadata']
            )
            
            # Update local state
            with self.lock:
                self.pending_requests[request.request_id] = request
                
                # Check if newly approved
                if request.status == "approved" and self.action_callback:
                    required_roles = self.APPROVAL_REQUIREMENTS[request.request_type]
                    all_approved = all(
                        r.value in request.approvals 
                        for r in required_roles
                    )
                    if all_approved:
                        self._execute_request(request)
                        
        except Exception as e:
            logger.error(f"Error processing ConfigMap: {e}")
    
    def _cleanup_loop(self):
        """Clean up expired requests"""
        while self.running:
            try:
                time.sleep(60)  # Check every minute
                
                now = datetime.now(timezone.utc)
                expired_ids = []
                
                with self.lock:
                    for req_id, request in self.pending_requests.items():
                        if request.status == "pending":
                            expires = datetime.fromisoformat(request.expires_at)
                            if expires < now:
                                request.status = "expired"
                                expired_ids.append(req_id)
                
                # Clean up expired ConfigMaps
                if self.k8s_api and expired_ids:
                    for req_id in expired_ids:
                        cm_name = f"killswitch-{req_id}"
                        try:
                            self.k8s_api.delete_namespaced_config_map(
                                name=cm_name,
                                namespace=self.namespace
                            )
                            logger.info(f"Cleaned up expired request {req_id}")
                        except ApiException:
                            pass
                            
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
    
    def get_pending_requests(self) -> List[Dict[str, Any]]:
        """Get all pending requests"""
        with self.lock:
            pending = []
            for request in self.pending_requests.values():
                if request.status == "pending":
                    data = asdict(request)
                    data['request_type'] = request.request_type.value
                    data['required_approvals'] = [
                        r.value for r in self.APPROVAL_REQUIREMENTS[request.request_type]
                    ]
                    data['received_approvals'] = list(request.approvals.keys())
                    pending.append(data)
            return pending


def integrate_kill_switch(listener, namespace: str = "aswarm"):
    """
    Integrate kill-switch governance with UDP listener
    
    Args:
        listener: FastPathListener instance
        namespace: Kubernetes namespace
        
    Returns:
        KillSwitchGovernance instance
    """
    def action_handler(request_type: RequestType, request: KillSwitchRequest):
        """Handle approved kill-switch actions"""
        if request_type == RequestType.DISABLE_FASTPATH:
            # Disable elevation callbacks
            listener.elevation_callback = lambda *args: None
            logger.warning("Fast-path DISABLED via kill-switch")
            
        elif request_type == RequestType.ENABLE_AUDIT_ONLY:
            # Switch to degraded mode
            with listener.mode_lock:
                listener.system_mode = listener.SystemMode.DEGRADED
            logger.warning("System switched to AUDIT-ONLY mode via kill-switch")
            
        elif request_type == RequestType.EMERGENCY_SHUTDOWN:
            # Stop the listener
            listener.stop()
            logger.critical("EMERGENCY SHUTDOWN via kill-switch")
            
        elif request_type == RequestType.RESTORE_NORMAL:
            # Restore normal operations
            with listener.mode_lock:
                listener.system_mode = listener.SystemMode.NORMAL
            logger.info("System restored to NORMAL mode via kill-switch")
            
        elif request_type == RequestType.RELOAD_KEYS:
            # Reload HMAC keys
            listener.reload_keys()
            logger.info("HMAC keys reloaded via kill-switch")
    
    # Create governance system
    governance = KillSwitchGovernance(
        namespace=namespace,
        action_callback=action_handler
    )
    
    governance.start()
    return governance


def main():
    """CLI for testing kill-switch governance"""
    import argparse
    
    parser = argparse.ArgumentParser(description='A-SWARM Kill-Switch Governance')
    parser.add_argument('command', choices=['create', 'approve', 'list'],
                       help='Command to execute')
    parser.add_argument('--type', choices=[
        'disable_fastpath', 'enable_audit_only', 'emergency_shutdown',
        'restore_normal', 'reload_keys', 'flush_wal'
    ], help='Request type for create')
    parser.add_argument('--requester', help='Requester identity')
    parser.add_argument('--reason', help='Reason for request')
    parser.add_argument('--request-id', help='Request ID for approve')
    parser.add_argument('--approver', help='Approver identity')
    parser.add_argument('--role', choices=['security', 'operations', 'sre'],
                       help='Approver role')
    parser.add_argument('--namespace', default='aswarm', help='Kubernetes namespace')
    
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    governance = KillSwitchGovernance(namespace=args.namespace)
    governance.start()
    
    try:
        if args.command == 'create':
            if not all([args.type, args.requester, args.reason]):
                parser.error("create requires --type, --requester, and --reason")
                
            request_type = RequestType(args.type)
            request_id = governance.create_request(
                request_type=request_type,
                requester=args.requester,
                reason=args.reason
            )
            
            print(f"Created kill-switch request: {request_id}")
            print(f"Required approvals: {[r.value for r in governance.APPROVAL_REQUIREMENTS[request_type]]}")
            
        elif args.command == 'approve':
            if not all([args.request_id, args.approver, args.role]):
                parser.error("approve requires --request-id, --approver, and --role")
                
            role = ApprovalRole(args.role)
            success = governance.approve_request(
                request_id=args.request_id,
                approver=args.approver,
                role=role
            )
            
            if success:
                print(f"Approval recorded for {args.request_id}")
            else:
                print(f"Approval failed for {args.request_id}")
                
        elif args.command == 'list':
            requests = governance.get_pending_requests()
            if not requests:
                print("No pending kill-switch requests")
            else:
                print(f"Pending kill-switch requests ({len(requests)}):")
                for req in requests:
                    print(f"\n  ID: {req['request_id']}")
                    print(f"  Type: {req['request_type']}")
                    print(f"  Requester: {req['requester']}")
                    print(f"  Reason: {req['reason']}")
                    print(f"  Created: {req['created_at']}")
                    print(f"  Expires: {req['expires_at']}")
                    print(f"  Required: {req['required_approvals']}")
                    print(f"  Received: {req['received_approvals']}")
                    
    finally:
        governance.stop()


if __name__ == '__main__':
    main()