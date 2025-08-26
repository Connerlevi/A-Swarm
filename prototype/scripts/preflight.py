#!/usr/bin/env python3
import subprocess, sys, json

def sh(args):
    return subprocess.check_output(args).decode().strip()

def main():
    status = {}
    print("A-SWARM Prototype Preflight Checks")
    print("=" * 40)
    
    try:
        ctx = sh(["kubectl","config","current-context"])
        status["context"] = ctx
        print(f"✅ kubectl context: {ctx}")
    except Exception as e:
        print(f"❌ kubectl not configured: {e}", file=sys.stderr)
        sys.exit(1)

    # Check cluster connectivity
    try:
        nodes = sh(["kubectl","get","nodes","-o","name"])
        node_count = len(nodes.strip().split('\n')) if nodes.strip() else 0
        status["nodes"] = node_count
        print(f"✅ Cluster nodes: {node_count}")
    except Exception as e:
        print(f"❌ Cannot reach cluster: {e}", file=sys.stderr)
        sys.exit(1)

    # Check NetworkPolicy support
    try:
        api = sh(["kubectl","api-resources","--api-group=networking.k8s.io","-o","name"])
        status["networking.k8s.io"] = api.splitlines()
        if "networkpolicies" not in api:
            print("❌ Cluster lacks NetworkPolicy support", file=sys.stderr)
            sys.exit(2)
        print("✅ NetworkPolicy support available")
    except Exception as e:
        print(f"❌ Cannot query API resources: {e}", file=sys.stderr)
        sys.exit(2)

    # Test apply in dry-run
    try:
        sh(["kubectl","apply","-f","k8s/baseline-allow.yaml","--dry-run=client"])
        status["dry_run"] = "ok"
        print("✅ Dry-run apply test passed")
    except Exception as e:
        print(f"❌ Dry-run apply failed: {e}", file=sys.stderr)
        sys.exit(3)

    # Check for required tools
    tools = ["helm", "python3"]
    for tool in tools:
        try:
            subprocess.check_call([tool, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"✅ {tool} available")
            status[f"{tool}_available"] = True
        except (subprocess.CalledProcessError, FileNotFoundError):
            print(f"⚠️  {tool} not found (optional)")
            status[f"{tool}_available"] = False

    print("\nPreflight checks completed successfully!")
    print("Ready to deploy A-SWARM prototype")
    
    # Output JSON for programmatic use
    status["ok"] = True
    if "--json" in sys.argv:
        print(json.dumps(status, indent=2))

if __name__ == "__main__":
    main()