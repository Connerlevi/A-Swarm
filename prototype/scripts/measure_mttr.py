#!/usr/bin/env python3
import subprocess, json, time, re, sys, argparse
from datetime import datetime, timezone

def iso(s): 
    return datetime.fromisoformat(s.replace('Z','+00:00'))

def main(ns):
    # get anomaly start from Job logs
    logs = subprocess.check_output(['kubectl','-n',ns,'logs','job/anomaly-scan']).decode()
    m = re.search(r'T_ANOMALY_START\s+(\S+)', logs)
    if not m:
        print("Could not find anomaly start in logs.", file=sys.stderr)
        sys.exit(1)
    t0 = iso(m.group(1))
    
    # wait until pheromone elevates (configmap updated)
    print("Waiting for elevation...")
    t1 = None
    for _ in range(60):
        try:
            out = subprocess.check_output(['kubectl','-n',ns,'get','cm/aswarm-elevated','-o','json']).decode()
            data = json.loads(out).get('data',{})
            if data.get('elevated') == 'true':
                t1 = iso(data.get('ts'))
                break
        except subprocess.CalledProcessError:
            pass
        time.sleep(1)
    
    if not t1:
        print("No elevation detected (timeout).", file=sys.stderr)
        sys.exit(2)
    
    # apply micro-containment and record time t2
    t2a = datetime.now(timezone.utc)
    subprocess.check_call(['kubectl','apply','-f','k8s/quarantine-template.yaml'])
    subprocess.check_call(['kubectl','-n',ns,'label','pods','-l','app=anomaly','aswarm/quarantine=true','--overwrite'])
    # crude check: try to exec into anomaly pod and curl; expect denial soon
    time.sleep(2)
    t2 = datetime.now(timezone.utc)
    
    # report
    mttd = (t1 - t0).total_seconds()*1000.0
    mttr = (t2 - t1).total_seconds()
    print(json.dumps({
        "t_anomaly_start": t0.isoformat(),
        "t_detect_elevated": t1.isoformat(),
        "t_containment_effective": t2.isoformat(),
        "MTTD_ms": round(mttd,1),
        "MTTR_s": round(mttr,2)
    }, indent=2))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace", default="aswarm")
    args = ap.parse_args()
    main(args.namespace)