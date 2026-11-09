#!/usr/bin/env python3
"""Heuristic static audit for direct DB/collector access in Chart Engine runtime.

This is a review aid, not a security proof. Run it against the actual runtime
source directory after it is supplied. The approved A13 reader is expected to
live outside the analysis runtime directory and is not scanned as runtime code.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys

FORBIDDEN_IMPORT_ROOTS = {
    "sqlite3", "rooibos_a6.storage", "rooibos_a6.parser", "rooibos_db.database",
}
FORBIDDEN_SQL_TOKENS = (
    "a6_candles", "raw_responses", "collector_runs", "a13_snapshot_decisions",
    "a13_record_assessments",
)


def audit_file(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return [f"{path}: unable to parse source: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name == root or alias.name.startswith(root + ".") for root in FORBIDDEN_IMPORT_ROOTS):
                    findings.append(f"{path}:{node.lineno}: forbidden direct-access import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if any(node.module == root or node.module.startswith(root + ".") for root in FORBIDDEN_IMPORT_ROOTS):
                findings.append(f"{path}:{node.lineno}: forbidden direct-access import from {node.module}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in {"connect", "execute", "executemany"}:
                if func.attr == "connect":
                    findings.append(f"{path}:{node.lineno}: possible direct database connection")
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        lowered = arg.value.lower()
                        if any(token in lowered for token in FORBIDDEN_SQL_TOKENS):
                            findings.append(f"{path}:{node.lineno}: SQL references protected collector/governance table")
    return findings


def audit_tree(root: Path) -> list[str]:
    if not root.exists() or not root.is_dir():
        return [f"Runtime source directory does not exist: {root}"]
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts and "tests" not in p.parts)
    if not files:
        return [f"No Python runtime files found under: {root}"]
    return [finding for path in files for finding in audit_file(path)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime_dir", type=Path, help="Chart Engine runtime source directory to audit")
    args = parser.parse_args()
    findings = audit_tree(args.runtime_dir)
    if findings:
        print("CHART ENGINE ACCESS AUDIT: FINDINGS")
        print("\n".join(f"- {item}" for item in findings))
        return 1
    print(f"CHART ENGINE ACCESS AUDIT: no heuristic findings under {args.runtime_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
