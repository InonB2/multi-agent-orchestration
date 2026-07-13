#!/usr/bin/env python3
"""
Pre-deploy governance gate for local repos.

Runs four checks in order and stops on the first failure:
1. Secret scan via the repo's existing gitleaks config and hook-style command.
2. Required environment variable validation from a text/YAML/JSON manifest.
3. Test suite command execution.
4. Deployment registry append to dashboard/deployments.jsonl on full success.

TODO:
- Add remote-platform env validation for deploy targets that store env vars
  outside the local machine (for example Railway/Lovable-hosted environments).
  This script currently validates only os.environ plus a local .env file.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


EXIT_OK = 0
EXIT_SECRET_SCAN_FAILED = 1
EXIT_ENV_VALIDATION_FAILED = 2
EXIT_TESTS_FAILED = 3


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_command(command, cwd: Path, shell: bool = False) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        shell=shell,
        capture_output=True,
        text=True,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def find_gitleaks_binary(repo_root: Path) -> str:
    vendored = repo_root / "scripts" / "security" / "bin" / "gitleaks.exe"
    if vendored.exists():
        return str(vendored)
    resolved = shutil.which("gitleaks")
    if resolved:
        return resolved
    raise RuntimeError(
        "gitleaks binary not found. Vendor it at scripts/security/bin/gitleaks.exe "
        "or install it on PATH."
    )


def load_required_env_names(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    stripped = raw.strip()

    if suffix == ".json" or stripped.startswith("{") or stripped.startswith("["):
        data = json.loads(raw)
        return _names_from_structured_manifest(data)

    if suffix in {".yaml", ".yml"} or ":" in raw or raw.lstrip().startswith("-"):
        yaml_names = _parse_simple_yaml_manifest(raw)
        if yaml_names:
            return yaml_names

    return _normalize_names(_parse_text_manifest(raw))


def _names_from_structured_manifest(data) -> list[str]:
    if isinstance(data, list):
        return _normalize_names(data)
    if isinstance(data, dict):
        for key in ("required_env", "env_vars", "required"):
            value = data.get(key)
            if isinstance(value, list):
                return _normalize_names(value)
        return _normalize_names(data.keys())
    raise ValueError("env manifest must be a list or object of env var names")


def _parse_simple_yaml_manifest(raw: str) -> list[str]:
    names: list[str] = []
    in_named_list = False

    for original_line in raw.splitlines():
        line = original_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.endswith(":"):
            in_named_list = stripped[:-1] in {"required_env", "env_vars", "required"}
            continue
        if stripped.startswith("- "):
            if (
                in_named_list
                or original_line.startswith("-")
                or original_line.startswith("  -")
                or original_line.startswith("\t-")
            ):
                names.append(stripped[2:].strip())
                continue
        if ":" in stripped:
            key, _value = stripped.split(":", 1)
            names.append(key.strip())

    return _normalize_names(names)


def _parse_text_manifest(raw: str) -> list[str]:
    names = []
    for line in raw.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        names.append(stripped)
    return names


def _normalize_names(values: Iterable[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = str(value).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    if not names:
        raise ValueError("env manifest did not contain any env var names")
    return names


def collect_effective_env(repo_root: Path) -> dict[str, str]:
    effective = dict(os.environ)
    dotenv_path = repo_root / ".env"
    if not dotenv_path.exists():
        return effective

    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        effective.setdefault(key, value)
    return effective


def validate_required_env_vars(repo_root: Path, manifest_path: Path) -> tuple[list[str], list[str]]:
    required = load_required_env_names(manifest_path)
    effective_env = collect_effective_env(repo_root)
    missing = [name for name in required if not effective_env.get(name)]
    return required, missing


def run_secret_scan(repo_root: Path) -> CommandResult:
    binary = find_gitleaks_binary(repo_root)
    command = [
        binary,
        "protect",
        "--staged",
        "--redact",
        "--config",
        str(repo_root / ".gitleaks.toml"),
        "--source",
        str(repo_root),
        "-v",
    ]
    return run_command(command, cwd=repo_root)


def append_deployment_log(repo_root: Path, target: str, test_command: str, results: dict[str, str]) -> Path:
    dashboard_dir = repo_root / "dashboard"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    log_path = dashboard_dir / "deployments.jsonl"

    entry = {
        "project": repo_root.name,
        "commit_sha": git_output(repo_root, ["git", "rev-parse", "HEAD"]),
        "branch": git_output(repo_root, ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "target": target,
        "results": {
            **results,
            "test_command": test_command,
        },
    }
    with log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
    return log_path


def git_output(repo_root: Path, command: list[str]) -> str:
    result = run_command(command, cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git command failed: {' '.join(command)}")
    return result.stdout.strip()


def print_gate(status: str, gate: str, detail: str = "") -> None:
    line = f"{status} {gate}"
    if detail:
        line = f"{line}: {detail}"
    print(line)


def print_command_output(result: CommandResult) -> None:
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local pre-deploy governance gate.")
    parser.add_argument("--env-manifest", required=True, help="Path to required env var manifest.")
    parser.add_argument("--test-command", required=True, help="Shell command to run the test suite.")
    parser.add_argument("--target", required=True, help="Deployment target label for the registry log.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path.cwd()
    manifest_path = Path(args.env_manifest).expanduser().resolve()
    results = {
        "secret_scan": "pending",
        "env_validation": "pending",
        "tests": "pending",
    }

    try:
        secret_scan = run_secret_scan(repo_root)
    except RuntimeError as exc:
        results["secret_scan"] = "fail"
        print_gate("FAIL", "secret scan", str(exc))
        print(f"Summary: {json.dumps(results)}")
        return EXIT_SECRET_SCAN_FAILED
    print_command_output(secret_scan)
    if secret_scan.returncode != 0:
        results["secret_scan"] = "fail"
        print_gate("FAIL", "secret scan", "gitleaks detected a potential secret in staged changes")
        print(f"Summary: {json.dumps(results)}")
        return EXIT_SECRET_SCAN_FAILED
    results["secret_scan"] = "pass"
    print_gate("PASS", "secret scan", "gitleaks staged protect check passed")

    _required, missing = validate_required_env_vars(repo_root, manifest_path)
    if missing:
        results["env_validation"] = "fail"
        print_gate("FAIL", "env validation", "missing required vars: " + ", ".join(missing))
        print(f"Summary: {json.dumps(results)}")
        return EXIT_ENV_VALIDATION_FAILED
    results["env_validation"] = "pass"
    print_gate("PASS", "env validation", f"manifest satisfied: {manifest_path.name}")

    tests = run_command(args.test_command, cwd=repo_root, shell=True)
    print_command_output(tests)
    if tests.returncode != 0:
        results["tests"] = "fail"
        print_gate("FAIL", "tests", f"command exited with status {tests.returncode}")
        print(f"Summary: {json.dumps(results)}")
        return EXIT_TESTS_FAILED
    results["tests"] = "pass"
    print_gate("PASS", "tests", args.test_command)

    log_path = append_deployment_log(repo_root, args.target, args.test_command, results)
    print_gate("PASS", "deployment registry", str(log_path))
    print(f"Summary: {json.dumps(results)}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
