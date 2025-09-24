#!/usr/bin/env python3
"""
Validate that evidence pack metrics meet SLO targets
Used in CI to gate releases
"""
import json
import sys
import zipfile
from pathlib import Path

def validate_evidence_pack(zip_path: str, mttd_threshold: float = 2000, mttr_threshold: float = 1500):
    """Validate evidence pack meets SLO thresholds"""
    print(f"Validating evidence pack: {zip_path}")
    
    if not Path(zip_path).exists():
        print(f"ERROR: Evidence pack not found: {zip_path}")
        return False
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Read evidence.json
            evidence_data = json.loads(zf.read('evidence.json').decode())
            
            # Extract metrics
            mttd_p95 = evidence_data['metrics']['mttd']['p95_ms']
            mttr_p95 = evidence_data['metrics']['mttr']['p95_ms']
            mttd_success = evidence_data['metrics']['mttd']['success_rate']
            mttr_success = evidence_data['metrics']['mttr']['success_rate']
            
            print(f"\nMetrics Summary:")
            print(f"  P95 MTTD: {mttd_p95:.0f}ms (threshold: {mttd_threshold}ms)")
            print(f"  P95 MTTR: {mttr_p95:.0f}ms (threshold: {mttr_threshold}ms)")
            print(f"  MTTD Success Rate: {mttd_success:.0f}%")
            print(f"  MTTR Success Rate: {mttr_success:.0f}%")
            
            # Validate against thresholds
            passed = True
            failures = []
            
            if mttd_p95 > mttd_threshold:
                failures.append(f"P95 MTTD ({mttd_p95:.0f}ms) exceeds threshold ({mttd_threshold}ms)")
                passed = False
                
            if mttr_p95 > mttr_threshold:
                failures.append(f"P95 MTTR ({mttr_p95:.0f}ms) exceeds threshold ({mttr_threshold}ms)")
                passed = False
                
            if mttd_success < 90:
                failures.append(f"MTTD success rate ({mttd_success:.0f}%) below 90%")
                passed = False
                
            if mttr_success < 90:
                failures.append(f"MTTR success rate ({mttr_success:.0f}%) below 90%")
                passed = False
            
            # Report results
            if passed:
                print("\n✅ PASS: All SLOs met")
                return True
            else:
                print("\n❌ FAIL: SLO violations detected:")
                for failure in failures:
                    print(f"  - {failure}")
                return False
                
    except Exception as e:
        print(f"ERROR: Failed to validate evidence pack: {e}")
        return False

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Validate evidence pack SLOs")
    parser.add_argument("evidence_pack", help="Path to evidence pack ZIP")
    parser.add_argument("--mttd-threshold", type=float, default=2000,
                      help="P95 MTTD threshold in ms (default: 2000)")
    parser.add_argument("--mttr-threshold", type=float, default=1500,
                      help="P95 MTTR threshold in ms (default: 1500)")
    
    args = parser.parse_args()
    
    success = validate_evidence_pack(
        args.evidence_pack,
        mttd_threshold=args.mttd_threshold,
        mttr_threshold=args.mttr_threshold
    )
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()