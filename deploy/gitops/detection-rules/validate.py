#!/usr/bin/env python3
"""
A-SWARM Detection Rules Validator

Validates detection rules JSON against schema and enforces cross-field constraints
that JSON Schema cannot express.

Usage:
    python validate.py detection-rules.json
    python validate.py detection-rules.json --schema schema.json
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

try:
    import jsonschema
    from jsonschema import Draft7Validator, FormatChecker
except ImportError:
    print("ERROR: jsonschema package required. Install with: pip install jsonschema")
    sys.exit(1)

try:
    from dateutil.parser import isoparse  # robust ISO-8601
except Exception:
    isoparse = None  # fallback to datetime.fromisoformat with light normalization


SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_semver(s: str) -> Optional[Tuple[int, int, int]]:
    m = SEMVER_RE.match(s or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def schema_version_from_schema(schema: Dict[str, Any]) -> Optional[str]:
    # Prefer $id suffix …/X.Y.Z
    sid = schema.get("$id")
    if isinstance(sid, str):
        tail = sid.rstrip("/").split("/")[-1]
        if SEMVER_RE.match(tail):
            return tail
    # Fallback: parse from title like "A-SWARM Detection Rules v1.1.0"
    title = schema.get("title") or ""
    m = re.search(r"v(\d+\.\d+\.\d+)$", title)
    if m:
        return m.group(1)
    return None


class DetectionRulesValidator:
    """Validates A-SWARM detection rules with cross-field constraint checking."""

    def __init__(self, schema_path: Optional[Path] = None):
        if schema_path is None:
            schema_path = Path(__file__).parent / "schema.json"

        with open(schema_path) as f:
            self.schema = json.load(f)

        self.errors: List[str] = []
        self.warnings: List[str] = []

        self._schema_version = schema_version_from_schema(self.schema)
        self._validator = Draft7Validator(self.schema, format_checker=FormatChecker())

    def validate(self, rules_data: Dict[str, Any], strict: bool = False) -> bool:
        """Validate rules data. Returns True if valid, False otherwise."""
        self.errors.clear()
        self.warnings.clear()

        # JSON Schema validation (collect all errors)
        schema_errors = sorted(self._validator.iter_errors(rules_data), key=lambda e: e.path)
        for e in schema_errors:
            path = "root" if not list(e.absolute_path) else " -> ".join(map(str, e.absolute_path))
            self.errors.append(f"[schema] {path}: {e.message}")

        if schema_errors:
            # Still continue to cross-field to show as much as possible in one run
            pass

        # Cross-field constraint validation
        metadata = rules_data.get("metadata", {})
        rules = rules_data.get("content", {}).get("detection_rules", [])

        self._validate_metadata(metadata, strict=strict)
        self._validate_rules(rules)

        return len(self.errors) == 0

    def _validate_metadata(self, metadata: Dict[str, Any], strict: bool) -> None:
        # Schema/version compatibility
        declared = metadata.get("schema_version")
        if self._schema_version and declared:
            if parse_semver(declared) != parse_semver(self._schema_version):
                msg = f"[metadata] schema_version '{declared}' does not match schema '{self._schema_version}'"
                if strict:
                    self.errors.append(msg)
                else:
                    self.warnings.append(msg)
        elif self._schema_version and not declared:
            self.warnings.append("[metadata] schema_version missing (add to enable compatibility gating)")

        # Engine min version informational guard (hook for CI to gate)
        engine_min = metadata.get("engine_min_version")
        if engine_min and not parse_semver(engine_min):
            self.errors.append(f"[metadata] engine_min_version '{engine_min}' is not valid semver (X.Y.Z)")

    def _validate_rules(self, rules: List[Dict[str, Any]]) -> None:
        rule_ids_ci: set = set()
        rule_ids_exact: set = set()
        rule_uuids: set = set()

        for i, rule in enumerate(rules):
            rid = rule.get("id")
            ctx = f"rule[{i}]{'('+rid+')' if rid else ''}"

            # Duplicate IDs (exact + case-insensitive)
            if rid:
                rid_ci = rid.lower()
                if rid in rule_ids_exact:
                    self.errors.append(f"[{ctx}] Duplicate rule id '{rid}'")
                if rid_ci in rule_ids_ci and rid not in rule_ids_exact:
                    self.warnings.append(f"[{ctx}] Rule id '{rid}' differs only by case from an existing id")
                rule_ids_exact.add(rid)
                rule_ids_ci.add(rid_ci)

            # Duplicate UUIDs (optional field)
            ruuid = rule.get("rule_uuid")
            if ruuid:
                if ruuid in rule_uuids:
                    self.errors.append(f"[{ctx}] Duplicate rule_uuid '{ruuid}'")
                rule_uuids.add(ruuid)

            # MITRE technique/sub-technique consistency
            self._validate_mitre_consistency(rule, ctx)

            # Time windows & scheduling
            self._validate_time_windows(rule, ctx)

            # Query non-empty (after strip)
            q = rule.get("query", "")
            if isinstance(q, str) and not q.strip():
                self.errors.append(f"[{ctx}] query must not be empty or whitespace")

            # Threshold sanity nudges (schema bounds already enforced 0..1)
            thr = rule.get("threshold")
            if isinstance(thr, (int, float)):
                if thr < 0.05:
                    self.warnings.append(f"[{ctx}] threshold={thr} is very low; may cause noise")
                if thr > 0.95:
                    self.warnings.append(f"[{ctx}] threshold={thr} is very high; may miss events")

            # Tests presence/quality
            self._validate_tests(rule, ctx)

    def _validate_mitre_consistency(self, rule: Dict[str, Any], ctx: str) -> None:
        md = rule.get("metadata", {}) or {}
        tech = md.get("mitre_technique")
        sub = md.get("mitre_sub_technique")
        if tech and sub and "." in sub:
            base = sub.split(".")[0]
            if base != tech:
                self.errors.append(
                    f"[{ctx}] MITRE sub-technique '{sub}' does not share base with technique '{tech}'"
                )

    def _parse_iso(self, s: str) -> datetime:
        if isoparse:
            return isoparse(s)
        # best-effort fallback
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    def _validate_time_windows(self, rule: Dict[str, Any], ctx: str) -> None:
        vf = rule.get("valid_from")
        vt = rule.get("valid_to")
        if vf and vt:
            try:
                if self._parse_iso(vf) >= self._parse_iso(vt):
                    self.errors.append(f"[{ctx}] valid_from ({vf}) must be before valid_to ({vt})")
            except Exception as e:
                self.errors.append(f"[{ctx}] invalid datetime in valid_from/valid_to: {e}")

        window_seconds = rule.get("window_seconds", 300)
        cooldown_seconds = rule.get("cooldown_seconds", 0)
        dedup_seconds = rule.get("dedup_seconds", 0)

        if isinstance(cooldown_seconds, int) and isinstance(window_seconds, int):
            if cooldown_seconds > window_seconds:
                self.warnings.append(
                    f"[{ctx}] cooldown_seconds ({cooldown_seconds}) > window_seconds ({window_seconds})"
                )
        if isinstance(dedup_seconds, int) and isinstance(window_seconds, int):
            if dedup_seconds > window_seconds:
                self.warnings.append(
                    f"[{ctx}] dedup_seconds ({dedup_seconds}) > window_seconds ({window_seconds})"
                )

    def _validate_tests(self, rule: Dict[str, Any], ctx: str) -> None:
        tests = rule.get("tests", [])
        if not tests:
            self.warnings.append(f"[{ctx}] no test fixtures defined")
            return

        names = set()
        pos = neg = False
        for t in tests:
            name = t.get("name")
            if name in names:
                self.errors.append(f"[{ctx}] duplicate test name '{name}'")
            names.add(name)
            if t.get("should_match"):
                pos = True
            else:
                neg = True

        if not pos:
            self.warnings.append(f"[{ctx}] no positive test cases (should_match: true)")
        if not neg:
            self.warnings.append(f"[{ctx}] no negative test cases (should_match: false)")

    def get_errors(self) -> List[str]:
        return self.errors

    def get_warnings(self) -> List[str]:
        return self.warnings


def main() -> int:
    p = argparse.ArgumentParser(description="Validate A-SWARM detection rules")
    p.add_argument("rules_file", help="Path to detection rules JSON file")
    p.add_argument("--schema", help="Path to JSON schema file")
    p.add_argument("--warnings-as-errors", action="store_true", help="Treat warnings as errors")
    p.add_argument("--strict", action="store_true",
                   help="Enable strict mode (schema_version mismatch becomes an error)")
    p.add_argument("--summary", action="store_true", help="Print rules summary")
    p.add_argument("--gh-annotations", action="store_true",
                   help="Emit GitHub Actions style ::error/::warning lines")
    args = p.parse_args()

    # Load rules file
    try:
        with open(args.rules_file) as f:
            rules_data = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: Rules file not found: {args.rules_file}")
        return 1
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {args.rules_file}: {e}")
        return 1

    schema_path = Path(args.schema) if args.schema else None
    validator = DetectionRulesValidator(schema_path)

    ok = validator.validate(rules_data, strict=args.strict)
    errors = validator.get_errors()
    warnings = validator.get_warnings()

    # Optional summary
    if args.summary:
        rules = rules_data.get("content", {}).get("detection_rules", [])
        print(f"Summary: rules={len(rules)} severity_dist=" +
              json.dumps({s: sum(1 for r in rules if r.get('severity') == s)
                          for s in ("critical","high","medium","low")}))

    # Output formatting
    if errors:
        print("VALIDATION ERRORS:")
        for e in errors:
            print(f" ❌ {e}")
    if warnings:
        print("VALIDATION WARNINGS:")
        for w in warnings:
            print(f" ⚠️ {w}")

    if args.gh_annotations:
        for e in errors:
            print(f"::error ::{e}")
        for w in warnings:
            print(f"::warning ::{w}")

    if ok and not warnings:
        print("✅ Validation passed - no errors or warnings")
        return 0
    elif ok:
        print(f"✅ Validation passed - {len(warnings)} warning(s)")
        return 1 if args.warnings_as_errors else 0
    else:
        print(f"❌ Validation failed - {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1


if __name__ == "__main__":
    sys.exit(main())