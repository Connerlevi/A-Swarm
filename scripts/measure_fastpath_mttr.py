#!/usr/bin/env python3
"""
Measure MTTR with Fast-Path enabled
Combines existing anomaly injection with UDP fast-path
"""
import os
import sys
import time
import json
import subprocess
import threading
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import fast-path components
from sentinel.fast_path import FastPathSender
from pheromone.udp_listener import FastPathListener

class FastPathMTTR:
    def __init__(self, namespace="aswarm"):
        self.namespace = namespace
        self.detection_time = None
        self.containment_time = None
        self.anomaly_start = None
        
    def handle_elevation(self, data, addr):
        """Record fast-path detection time"""
        if not self.detection_time:
            self.detection_time = time.time()
            latency = (self.detection_time - self.anomaly_start) * 1000
            print(f"🚀 FAST-PATH DETECTION: {latency:.1f}ms from {addr[0]}")
            
    def inject_anomaly_with_fastpath(self):
        """Inject anomaly using both Lease and UDP paths"""
        
        # Start UDP listener
        print("Starting fast-path listener...")
        listener = FastPathListener(
            bind_addr='0.0.0.0',
            bind_port=8888,
            shared_keys={1: os.environ.get('ASWARM_FASTPATH_KEY', 'demo-key')},
            elevation_callback=self.handle_elevation
        )
        listener.start()
        time.sleep(0.5)
        
        # Create fast-path sender
        sender = FastPathSender(
            host='localhost',  # Send to local listener
            port=8888,
            shared_key=os.environ.get('ASWARM_FASTPATH_KEY', 'demo-key')
        )
        
        print("\n🎯 Injecting anomaly via fast-path...")
        self.anomaly_start = time.time()
        
        # Send high-confidence anomaly
        anomaly = {
            'score': 0.95,
            'witness_count': 4,
            'selector': 'app=anomaly',
            'event_type': 'port_scan',
            'pod_ips': ['10.42.0.10', '10.42.0.11', '10.42.0.12', '10.42.0.13']
        }
        
        # Send via fast-path
        stats = sender.send_elevation(anomaly, run_id='mttr-test')
        print(f"Sent via UDP: {stats.bytes} bytes in {stats.send_ms_first:.1f}ms")
        
        # Also trigger via kubectl (existing method)
        print("\nAlso injecting via kubectl...")
        subprocess.run([
            'kubectl', 'apply', '-f', 'k8s/anomaly-job.yaml',
            '-n', self.namespace
        ])
        
        # Monitor for containment
        print("\n⏱️  Monitoring for containment...")
        start_monitor = time.time()
        
        while time.time() - start_monitor < 30:
            # Check for NetworkPolicy
            result = subprocess.run([
                'kubectl', 'get', 'networkpolicy',
                '-n', self.namespace,
                '-l', 'aswarm.ai/action=networkpolicy-isolate',
                '-o', 'json'
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                policies = json.loads(result.stdout)
                if policies.get('items'):
                    self.containment_time = time.time()
                    print(f"✅ Containment applied: {policies['items'][0]['metadata']['name']}")
                    break
            
            time.sleep(0.5)
        
        # Cleanup
        sender.close()
        listener.stop()
        
        # Calculate metrics
        if self.detection_time and self.anomaly_start:
            mttd = (self.detection_time - self.anomaly_start) * 1000
            print(f"\n📊 MTTD (Fast-Path): {mttd:.1f}ms")
        else:
            print("\n❌ No fast-path detection recorded")
            
        if self.containment_time and self.anomaly_start:
            mttr = self.containment_time - self.anomaly_start
            print(f"📊 MTTR (Total): {mttr:.2f}s")
        else:
            print("❌ No containment detected")
            
        return {
            'mttd_ms': mttd if self.detection_time else None,
            'mttr_s': mttr if self.containment_time else None
        }

def main():
    print("=== A-SWARM Fast-Path MTTR Test ===\n")
    
    # Check if microact is available
    result = subprocess.run([
        'kubectl', 'get', 'pods', '-n', 'aswarm',
        '-l', 'app.kubernetes.io/component=executor'
    ], capture_output=True)
    
    if result.returncode != 0:
        print("⚠️  No MicroAct executor found - containment won't work")
        print("   But we can still test fast-path detection!")
    
    # Run test
    tester = FastPathMTTR()
    results = tester.inject_anomaly_with_fastpath()
    
    print("\n=== Summary ===")
    if results['mttd_ms'] and results['mttd_ms'] < 200:
        print(f"✅ Fast-Path MTTD: {results['mttd_ms']:.1f}ms < 200ms TARGET MET!")
    else:
        print(f"❌ Fast-Path MTTD: {results.get('mttd_ms', 'N/A')}ms")
        
    if results['mttr_s']:
        print(f"📊 Total MTTR: {results['mttr_s']:.2f}s")

if __name__ == '__main__':
    main()