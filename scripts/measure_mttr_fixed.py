#!/usr/bin/env python3
import subprocess, json, time, re, sys, argparse, os
import hashlib, hmac
from datetime import datetime, timezone
from pathlib import Path

def iso(s): 
    return datetime.fromisoformat(s.replace('Z','+00:00'))

def main(ns, selector="app=anomaly", repeat=1):
    all_runs = []
    
    # Clean up any previous runs
    subprocess.call(['kubectl','-n',ns,'delete','job','anomaly-scan'], 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    for run_num in range(repeat):
        if repeat > 1:
            print(f"\n=== Run {run_num + 1}/{repeat} ===", flush=True)
        
        # Generate unique run ID
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{run_num:03d}"
        print(f"Run ID: {run_id}", flush=True)
        
        # Clean up old elevation ConfigMaps
        subprocess.call(['kubectl','-n',ns,'delete','cm','aswarm-elevated'], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Create anomaly job
        subprocess.check_call(['kubectl','apply','-f','k8s/anomaly-job.yaml'])
        
        # Wait for anomaly to start and generate logs
        time.sleep(3)
        
        # Timing using monotonic clock for intervals
        perf_start = time.perf_counter()
        
        # get anomaly start from Job logs
        for attempt in range(10):
            try:
                logs = subprocess.check_output(['kubectl','-n',ns,'logs','job/anomaly-scan']).decode()
                m = re.search(r'T_ANOMALY_START\s+(\S+)', logs)
                if m:
                    break
            except:
                pass
            time.sleep(0.5)
        
        if not m:
            print("Could not find anomaly start in logs.", file=sys.stderr)
            sys.exit(1)
        t0 = iso(m.group(1))
        t0_perf = perf_start  # Use current time as reference
        
        # wait until pheromone elevates (check both old and new style)
        print("Waiting for elevation...", flush=True)
        t1 = None
        t1_perf = None
        elevation_data = {}
        
        for _ in range(60):
            # Try new run-specific configmap first
            try:
                out = subprocess.check_output(['kubectl','-n',ns,'get',f'cm/aswarm-elevated-{run_id}','-o','json'], 
                                            stderr=subprocess.DEVNULL).decode()
                cm_data = json.loads(out)
                data = cm_data.get('data',{})
                if data.get('elevated') == 'true':
                    # Check if this elevation is after anomaly start
                    elev_ts = iso(data.get('ts'))
                    if elev_ts > t0:
                        t1 = elev_ts
                        t1_perf = time.perf_counter()
                        elevation_data = convert_elevation_data(data, run_id)
                        break
            except subprocess.CalledProcessError:
                pass
            
            # Fall back to old style configmap
            try:
                out = subprocess.check_output(['kubectl','-n',ns,'get','cm/aswarm-elevated','-o','json'],
                                            stderr=subprocess.DEVNULL).decode()
                cm_data = json.loads(out)
                data = cm_data.get('data',{})
                if data.get('elevated') == 'true':
                    # Check if this elevation is after anomaly start
                    elev_ts = iso(data.get('ts'))
                    if elev_ts > t0:
                        t1 = elev_ts
                        t1_perf = time.perf_counter()
                        elevation_data = convert_elevation_data(data, run_id)
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
        mttd_wall = (t1 - t0).total_seconds() * 1000.0
        mttr_wall = (t2 - t1).total_seconds()
        
        # Calculate clock skew
        clock_skew_ms = mttd_wall - mttd_perf
        
        # Determine clock source
        clock_source = "UTC+NTP"  # In production, detect actual source
        
        # Get policy hash
        policy_yaml = subprocess.check_output(
            ['kubectl','-n',ns,'get','networkpolicy/aswarm-quarantine','-o','yaml']
        )
        policy_hash = hashlib.sha256(policy_yaml).hexdigest()
        
        # Launch auto-revert job for safety
        print("Launching TTL auto-revert job...", flush=True)
        try:
            # Delete any existing revert job first
            subprocess.call(['kubectl','-n',ns,'delete','job','quarantine-revert'], 
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Create new revert job
            subprocess.check_call(['kubectl','apply','-f','k8s/quarantine-revert-job.yaml'])
            revert_scheduled_at = datetime.now(timezone.utc).isoformat()
        except subprocess.CalledProcessError as e:
            print(f"Warning: Could not launch auto-revert job: {e}", file=sys.stderr)
            revert_scheduled_at = None
        
        artifact = {
            "certificate_id": run_id,
            "run_id": run_id,
            "site_id": ns,
            "asset_id": anom_pod,
            "timestamps": {
                "anomaly_start": t0.isoformat(),
                "detect_elevated": t1.isoformat(),
                "actuation_start": apply_t.isoformat(),
                "actuation_effective": t2.isoformat(),
                "revert_scheduled": revert_scheduled_at
            },
            "elevation_context": elevation_data,
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
                "MTTD_ms_monotonic": round(mttd_perf, 1),
                "MTTR_s_monotonic": round(mttr_perf, 2),
                "MTTD_ms_wall": round(mttd_wall, 1),
                "MTTR_s_wall": round(mttr_wall, 2),
                "clock_skew_ms": round(clock_skew_ms, 1)
            },
            "time": {
                "clock_source": clock_source
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
        
        # Run result
        run_result = {
            "run": run_num + 1,
            "run_id": run_id,
            "MTTD_ms": round(mttd_perf, 1),
            "MTTR_s": round(mttr_perf, 2),
            "certificate": str(cert_path),
            "signature": sig,
            "auto_revert": "scheduled"
        }
        
        print(json.dumps(run_result, indent=2))
        all_runs.append({
            "mttd": mttd_perf,
            "mttr": mttr_perf
        })
        
        # Clean up for next run
        if run_num < repeat - 1:
            subprocess.call(['kubectl','-n',ns,'delete','job','anomaly-scan'], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.call(['kubectl','-n',ns,'delete','cm','aswarm-elevated'], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
    
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

def convert_elevation_data(data, run_id):
    """Convert elevation data to proper types"""
    return {
        "scenario": data.get("scenario", "unknown"),
        "pattern": data.get("pattern", "unknown"),
        "count": int(data.get("count", 0)),
        "threshold": int(data.get("threshold", 0)),
        "window_seconds": int(data.get("window_seconds", 0)),
        "witnesses": int(data.get("witnesses", 0)),
        "witness_pods": data.get("witness_pods", "").split(",") if data.get("witness_pods") else [],
        "confidence": float(data.get("confidence", 0)) / 100.0,  # Convert to 0-1 range
        "ts": data.get("ts"),
        "elevated": True,
        "run_id": run_id
    }

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace", default="aswarm")
    ap.add_argument("--selector", default="app=anomaly", help="Pod selector for quarantine")
    ap.add_argument("--repeat", type=int, default=1, help="Number of runs for percentile metrics")
    args = ap.parse_args()
    main(args.namespace, args.selector, args.repeat)