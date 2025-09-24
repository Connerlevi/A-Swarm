#!/usr/bin/env python3
"""
A-SWARM SBOM Analysis Tool
Analyzes Software Bill of Materials for security and compliance
"""
import json
import sys
import argparse
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, List, Any

class SBOMAnalyzer:
    """Analyzer for SBOM files"""
    
    def __init__(self):
        self.vulnerabilities = {}
        self.licenses = Counter()
        self.packages = {}
        
    def analyze_spdx_sbom(self, sbom_path: str) -> Dict[str, Any]:
        """Analyze SPDX format SBOM"""
        with open(sbom_path) as f:
            sbom = json.load(f)
        
        analysis = {
            "format": "SPDX",
            "version": sbom.get("spdxVersion"),
            "document_name": sbom.get("name"),
            "creation_info": sbom.get("creationInfo", {}),
            "packages": [],
            "relationships": len(sbom.get("relationships", [])),
            "license_summary": {},
            "security_summary": {},
            "total_packages": 0
        }
        
        # Analyze packages
        packages = sbom.get("packages", [])
        for pkg in packages:
            pkg_info = {
                "name": pkg.get("name"),
                "version": pkg.get("versionInfo"),
                "download_location": pkg.get("downloadLocation"),
                "license": pkg.get("licenseConcluded"),
                "supplier": pkg.get("supplier"),
                "homepage": pkg.get("homepage")
            }
            
            analysis["packages"].append(pkg_info)
            
            # Count licenses
            license_val = pkg.get("licenseConcluded", "NOASSERTION")
            if license_val != "NOASSERTION":
                self.licenses[license_val] += 1
        
        analysis["total_packages"] = len(packages)
        analysis["license_summary"] = dict(self.licenses.most_common(10))
        
        return analysis
    
    def analyze_cyclonedx_sbom(self, sbom_path: str) -> Dict[str, Any]:
        """Analyze CycloneDX format SBOM"""
        with open(sbom_path) as f:
            sbom = json.load(f)
        
        analysis = {
            "format": "CycloneDX",
            "version": sbom.get("specVersion"),
            "bom_version": sbom.get("version"),
            "serial_number": sbom.get("serialNumber"),
            "metadata": sbom.get("metadata", {}),
            "components": [],
            "vulnerabilities": [],
            "total_components": 0
        }
        
        # Analyze components
        components = sbom.get("components", [])
        for comp in components:
            comp_info = {
                "type": comp.get("type"),
                "name": comp.get("name"),
                "version": comp.get("version"),
                "purl": comp.get("purl"),
                "licenses": comp.get("licenses", []),
                "scope": comp.get("scope")
            }
            analysis["components"].append(comp_info)
        
        analysis["total_components"] = len(components)
        
        # Analyze vulnerabilities if present
        vulns = sbom.get("vulnerabilities", [])
        for vuln in vulns:
            vuln_info = {
                "id": vuln.get("id"),
                "source": vuln.get("source", {}).get("name"),
                "ratings": vuln.get("ratings", []),
                "affected": len(vuln.get("affects", []))
            }
            analysis["vulnerabilities"].append(vuln_info)
        
        return analysis
    
    def check_license_compliance(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Check license compliance against policy"""
        # Define license policies
        approved_licenses = {
            "MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause",
            "ISC", "Python-2.0", "MPL-2.0"
        }
        
        restricted_licenses = {
            "GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.1", "LGPL-3.0"
        }
        
        compliance = {
            "status": "compliant",
            "approved": [],
            "restricted": [],
            "unknown": [],
            "total_packages": analysis.get("total_packages", 0)
        }
        
        license_summary = analysis.get("license_summary", {})
        
        for license_name, count in license_summary.items():
            if license_name in approved_licenses:
                compliance["approved"].append({"license": license_name, "count": count})
            elif license_name in restricted_licenses:
                compliance["restricted"].append({"license": license_name, "count": count})
                compliance["status"] = "restricted_found"
            else:
                compliance["unknown"].append({"license": license_name, "count": count})
                if compliance["status"] == "compliant":
                    compliance["status"] = "review_needed"
        
        return compliance
    
    def generate_security_summary(self, analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate security summary across all SBOMs"""
        total_packages = sum(a.get("total_packages", 0) for a in analyses)
        total_vulns = sum(len(a.get("vulnerabilities", [])) for a in analyses)
        
        # Aggregate license data
        all_licenses = Counter()
        for analysis in analyses:
            for license_name, count in analysis.get("license_summary", {}).items():
                all_licenses[license_name] += count
        
        return {
            "total_components_analyzed": len(analyses),
            "total_packages": total_packages,
            "total_vulnerabilities": total_vulns,
            "license_distribution": dict(all_licenses.most_common(10)),
            "risk_level": "low" if total_vulns < 5 else "medium" if total_vulns < 20 else "high"
        }

def main():
    parser = argparse.ArgumentParser(description="A-SWARM SBOM Analysis Tool")
    parser.add_argument("sbom_path", help="Path to SBOM file or directory")
    parser.add_argument("--format", choices=["spdx", "cyclonedx", "auto"], 
                       default="auto", help="SBOM format")
    parser.add_argument("--output", help="Output file for analysis report")
    parser.add_argument("--compliance-check", action="store_true",
                       help="Check license compliance")
    
    args = parser.parse_args()
    
    analyzer = SBOMAnalyzer()
    sbom_path = Path(args.sbom_path)
    
    analyses = []
    
    if sbom_path.is_file():
        # Single SBOM file
        if args.format == "auto":
            # Auto-detect format
            with open(sbom_path) as f:
                data = json.load(f)
                if "spdxVersion" in data:
                    format_type = "spdx"
                elif "specVersion" in data:
                    format_type = "cyclonedx"
                else:
                    print(f"Error: Cannot detect SBOM format in {sbom_path}")
                    return 1
        else:
            format_type = args.format
        
        if format_type == "spdx":
            analysis = analyzer.analyze_spdx_sbom(sbom_path)
        elif format_type == "cyclonedx":
            analysis = analyzer.analyze_cyclonedx_sbom(sbom_path)
        
        analyses.append(analysis)
        
    elif sbom_path.is_dir():
        # Directory of SBOM files
        for sbom_file in sbom_path.glob("*sbom*.json"):
            print(f"Analyzing {sbom_file.name}...")
            
            if "spdx" in sbom_file.name:
                analysis = analyzer.analyze_spdx_sbom(sbom_file)
            elif "cyclonedx" in sbom_file.name:
                analysis = analyzer.analyze_cyclonedx_sbom(sbom_file)
            else:
                continue
            
            analyses.append(analysis)
    
    else:
        print(f"Error: {sbom_path} not found")
        return 1
    
    # Print summary
    print("\n=== A-SWARM SBOM Analysis Summary ===")
    
    for i, analysis in enumerate(analyses):
        print(f"\nDocument {i+1}: {analysis.get('document_name', 'Unknown')}")
        print(f"Format: {analysis['format']} {analysis.get('version', '')}")
        print(f"Packages: {analysis.get('total_packages', analysis.get('total_components', 0))}")
        
        if analysis.get('vulnerabilities'):
            print(f"Vulnerabilities: {len(analysis['vulnerabilities'])}")
        
        # Top licenses
        licenses = analysis.get("license_summary", {})
        if licenses:
            print("Top licenses:")
            for license_name, count in list(licenses.items())[:3]:
                print(f"  - {license_name}: {count}")
    
    # License compliance check
    if args.compliance_check and analyses:
        print("\n=== License Compliance Check ===")
        # Use first analysis for license check (or combine logic)
        compliance = analyzer.check_license_compliance(analyses[0])
        
        print(f"Status: {compliance['status'].upper()}")
        
        if compliance["approved"]:
            print("✅ Approved licenses:")
            for item in compliance["approved"][:5]:
                print(f"   {item['license']}: {item['count']} packages")
        
        if compliance["restricted"]:
            print("❌ Restricted licenses found:")
            for item in compliance["restricted"]:
                print(f"   {item['license']}: {item['count']} packages")
        
        if compliance["unknown"]:
            print("⚠️  Unknown licenses (need review):")
            for item in compliance["unknown"][:3]:
                print(f"   {item['license']}: {item['count']} packages")
    
    # Security summary
    if len(analyses) > 1:
        print("\n=== Security Summary ===")
        security = analyzer.generate_security_summary(analyses)
        print(f"Components analyzed: {security['total_components_analyzed']}")
        print(f"Total packages: {security['total_packages']}")
        print(f"Vulnerabilities: {security['total_vulnerabilities']}")
        print(f"Risk level: {security['risk_level'].upper()}")
    
    # Save detailed report if requested
    if args.output:
        report = {
            "analysis_timestamp": "2025-08-29T03:07:52Z",  # Current time
            "tool": "aswarm-sbom-analyzer",
            "version": "1.0.0",
            "analyses": analyses
        }
        
        if args.compliance_check:
            report["compliance"] = analyzer.check_license_compliance(analyses[0])
        
        if len(analyses) > 1:
            report["security_summary"] = analyzer.generate_security_summary(analyses)
        
        with open(args.output, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"\nDetailed report saved: {args.output}")
    
    # Exit with error code if compliance issues found
    if args.compliance_check and analyses:
        compliance = analyzer.check_license_compliance(analyses[0])
        if compliance["status"] != "compliant":
            return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())