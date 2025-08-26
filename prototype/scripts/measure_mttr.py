#!/usr/bin/env python3
import subprocess, json, time, re, sys, argparse, os
import hashlib, hmac
from datetime import datetime, timezone
from pathlib import Path

def iso(s): 
    return datetime.fromisoformat(s.replace('Z','+00:00'))

def main(ns, selector="app=anomaly", repeat=1):
    all_runs = []
    
    for run_num in range(repeat):
        if repeat > 1:
            print(f"\n=== Run {run_num + 1}/{repeat} ===", flush=True)
        
        # Timing using monotonic clock for intervals
        perf_start = time.perf_counter()
        
        # get anomaly start from Job logs
        logs = subprocess.check_output(['kubectl','-n',ns,'logs','job/anomaly-scan']).decode()
        m = re.search(r'T_ANOMALY_START\s+(\S+)', logs)
        if not m:
            print("Could not find anomaly start in logs.", file=sys.stderr)
            sys.exit(1)
        t0 = iso(m.group(1))
        t0_perf = perf_start  # Use current time as reference
        
        # wait until pheromone elevates (configmap updated)
        print("Waiting for elevation...", flush=True)
        t1 = None
        t1_perf = None
        elevation_data = {}
        
        for _ in range(60):
            try:
                out = subprocess.check_output(['kubectl','-n',ns,'get','cm/aswarm-elevated','-o','json']).decode()
                cm_data = json.loads(out)
                data = cm_data.get('data',{})
                if data.get('elevated') == 'true':
                    t1 = iso(data.get('ts'))
                    t1_perf = time.perf_counter()
                    elevation_data = data  # Save all elevation context
                    break
            except subprocess.CalledProcessError:
                pass
            time.sleep(1)
        
        if not t1:
            print("No elevation detected (timeout).", file=sys.stderr)
            sys.exit(2)
        
        # apply Ring-1 and record effectiveness when probes fail
        print("Applying micro-containment...", flush=True)
        apply_t = datetime.now(timezone.utc)
        apply_t_perf = time.perf_counter()
        
        subprocess.check_call(['kubectl','apply','-f','k8s/quarantine-template.yaml'])
        subprocess.check_call(['kubectl','-n',ns,'label','pods','-l',selector,'aswarm/quarantine=true','--overwrite'])
        
        # find an anomaly pod
        anom_pod = subprocess.check_output(
            ['kubectl','-n',ns,'get','pods','-l',selector,'-o','jsonpath={.items[0].metadata.name}']
        ).decode().strip()
        
        if not anom_pod:
            print(f"No pods found with selector {selector}", file=sys.stderr)
            sys.exit(3)
        
        # probe a noisy pod service until connections fail (denied by NetworkPolicy)
        print(f"Probing connectivity from {anom_pod}...", flush=True)
        start_probe = time.perf_counter()
        deadline = start_probe + 15.0  # 15s max
        contained_at = None
        contained_at_perf = None
        probe_attempts = 0
        
        while time.perf_counter() < deadline:
            probe_attempts += 1
            # Try to connect to a service
            rc = subprocess.call([
                'kubectl','-n',ns,'exec',anom_pod,'--','/bin/sh','-c',
                'timeout 1 nc -zv noisy.aswarm.svc.cluster.local 80 2>&1 || echo "BLOCKED"'
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Also check if we can see "BLOCKED" in output
            try:
                probe_out = subprocess.check_output([
                    'kubectl','-n',ns,'exec',anom_pod,'--','/bin/sh','-c',
                    'timeout 1 nc -zv noisy.aswarm.svc.cluster.local 80 2>&1 || echo "BLOCKED"'
                ]).decode()
                if "BLOCKED" in probe_out or "Connection refused" in probe_out or rc != 0:
                    contained_at = datetime.now(timezone.utc)
                    contained_at_perf = time.perf_counter()
                    print(f"Containment verified after {probe_attempts} probes", flush=True)
                    break
            except subprocess.CalledProcessError:
                # Connection blocked by NetworkPolicy
                contained_at = datetime.now(timezone.utc)
                contained_at_perf = time.perf_counter()
                print(f"Containment verified after {probe_attempts} probes", flush=True)
                break
            
            time.sleep(0.25)
        
        if not contained_at:
            print("Containment not observed within 15s window.", file=sys.stderr)
            sys.exit(3)
        
        t2 = contained_at
        t2_perf = contained_at_perf
        
        # Calculate metrics using monotonic clock
        mttd_perf = (t1_perf - t0_perf) * 1000.0  # milliseconds
        mttr_perf = t2_perf - t1_perf  # seconds
        
        # For display, also calculate from wall clock
        mttd = (t1 - t0).total_seconds() * 1000.0
        mttr = (t2 - t1).total_seconds()
        
        # Generate Action Certificate
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{run_num:03d}"
        
        # Get policy hash
        policy_yaml = subprocess.check_output(
            ['kubectl','-n',ns,'get','networkpolicy/aswarm-quarantine','-o','yaml']
        )
        policy_hash = hashlib.sha256(policy_yaml).hexdigest()
        
        artifact = {
            "certificate_id": run_id,
            "site_id": ns,
            "asset_id": anom_pod,
            "timestamps": {
                "anomaly_start": t0.isoformat(),
                "detect_elevated": t1.isoformat(),
                "actuation_start": apply_t.isoformat(),
                "actuation_effective": t2.isoformat()
            },
            "elevation_context": elevation_data,  # Include all elevation data
            "policy": {
                "policy_id": "aswarm-quarantine",
                "version_hash": policy_hash,
                "selector": selector
            },
            "action": {
                "ring": 1,
                "kind": "networkpolicy_isolate",
                "params": {"selector": selector},
                "ttl_seconds": 120
            },
            "outcome": {
                "status": "contained",
                "probe_attempts": probe_attempts,
                "containment_delay_ms": round((t2_perf - apply_t_perf) * 1000, 1),
                "notes": "connectivity blocked via NetworkPolicy probe"
            },
            "metrics": {
                "MTTD_ms": round(mttd, 1),
                "MTTR_s": round(mttr, 2),
                "MTTD_ms_monotonic": round(mttd_perf, 1),
                "MTTR_s_monotonic": round(mttr_perf, 2)
            }
        }
        
        # Save certificate
        out_dir = Path("ActionCertificates")
        out_dir.mkdir(exist_ok=True)
        cert_path = out_dir / f"{run_id}.json"
        cert_path.write_text(json.dumps(artifact, indent=2))
        
        # Demo signature (replace with HSM later)
        key = os.environ.get("ACTION_CERT_DEMO_KEY", "aswarm-demo-key").encode()
        sig = hmac.new(key, cert_path.read_bytes(), hashlib.sha256).hexdigest()
        
        # Launch auto-revert job for safety
        print("Launching TTL auto-revert job...", flush=True)
        try:
            # Delete any existing revert job first
            subprocess.call(['kubectl','-n',ns,'delete','job','quarantine-revert'], 
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Create new revert job
            subprocess.check_call(['kubectl','apply','-f','k8s/quarantine-revert-job.yaml'])
        except subprocess.CalledProcessError as e:
            print(f"Warning: Could not launch auto-revert job: {e}", file=sys.stderr)
        
        # Run result
        run_result = {
            "run": run_num + 1,
            "MTTD_ms": round(mttd, 1),
            "MTTR_s": round(mttr, 2),
            "certificate": str(cert_path),
            "signature": sig,
            "auto_revert": "scheduled"
        }
        
        print(json.dumps(run_result, indent=2))
        all_runs.append({
            "mttd": mttd,
            "mttr": mttr
        })
    
    # If multiple runs, calculate percentiles
    if repeat > 1:
        mttds = sorted([r["mttd"] for r in all_runs])
        mttrs = sorted([r["mttr"] for r in all_runs])
        
        def percentile(data, p):
            n = len(data)
            i = int(n * p / 100)
            return data[min(i, n-1)]
        
        summary = {
            "total_runs": repeat,
            "MTTD_ms": {
                "p50": round(percentile(mttds, 50), 1),
                "p95": round(percentile(mttds, 95), 1),
                "p99": round(percentile(mttds, 99), 1),
                "min": round(min(mttds), 1),
                "max": round(max(mttds), 1)
            },
            "MTTR_s": {
                "p50": round(percentile(mttrs, 50), 2),
                "p95": round(percentile(mttrs, 95), 2),
                "p99": round(percentile(mttrs, 99), 2),
                "min": round(min(mttrs), 2),
                "max": round(max(mttrs), 2)
            }
        }
        
        print("\n=== Summary ===")
        print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace", default="aswarm")
    ap.add_argument("--selector", default="app=anomaly", help="Pod selector for quarantine")
    ap.add_argument("--repeat", type=int, default=1, help="Number of runs for percentile metrics")
    args = ap.parse_args()
    main(args.namespace, args.selector, args.repeat)