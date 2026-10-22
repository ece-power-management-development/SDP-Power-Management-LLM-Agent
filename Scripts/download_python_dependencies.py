#!/usr/bin/env python3
"""Download Python wheels for an offline GridDyn interface installation.

This downloader uses the same Python interpreter that runs it, so the
wheelhouse can be installed later with ``pip --no-index --find-links``. It
does not require any third-party package itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PROFILES = {
    "core": ROOT / "requirements-core.txt",
    "helics": ROOT / "requirements-helics.txt",
    "local-agent": ROOT / "requirements-local-agent.txt",
    "all": ROOT / "requirements-all.txt",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def requirement_entries(path: Path, _seen: set[Path] | None = None) -> list[str]:
    """Return package entries, recursively expanding local ``-r`` files."""
    seen = set() if _seen is None else _seen
    path = path.resolve()
    if path in seen:
        raise ValueError(f"Circular requirements include detected at {path}")
    seen.add(path)
    entries: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r ") or line.startswith("--requirement "):
            included_name = line.split(None, 1)[1].strip()
            included_path = (path.parent / included_name).resolve()
            entries.extend(requirement_entries(included_path, seen))
        else:
            entries.append(line)
    seen.remove(path)
    return entries


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifacts(destination: Path, manifest_name: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for path in sorted(destination.iterdir()):
        if not path.is_file() or path.name == manifest_name:
            continue
        output.append(
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    return output


def build_download_command(args: argparse.Namespace, requirements: Path, destination: Path) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--dest",
        str(destination),
        "--requirement",
        str(requirements),
    ]
    if not args.allow_source:
        command.extend(["--only-binary", ":all:"])
    if args.python_version:
        command.extend(["--python-version", args.python_version])
    if args.platform:
        command.extend(["--platform", args.platform])
    if args.implementation:
        command.extend(["--implementation", args.implementation])
    if args.abi:
        command.extend(["--abi", args.abi])
    return command


def write_manifest(
    destination: Path,
    *,
    profile: str,
    requirements: Path,
    args: argparse.Namespace,
) -> Path:
    manifest_name = "dependency-manifest.json"
    manifest_path = destination / manifest_name
    payload = {
        "manifest_version": "1.0.0",
        "created_at_utc": utc_now(),
        "profile": profile,
        "requirements_file": requirements.name,
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "target_python_version": args.python_version,
        "target_platform": args.platform,
        "target_implementation": args.implementation,
        "target_abi": args.abi,
        "binary_wheels_only": not args.allow_source,
        "artifacts": artifacts(destination, manifest_name),
    }
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download GridDyn interface Python dependencies into an offline wheelhouse."
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="all",
        help="Dependency profile to download (default: all).",
    )
    parser.add_argument(
        "--destination",
        default=str(ROOT / "wheelhouse"),
        help="Directory for downloaded artifacts (default: ./wheelhouse).",
    )
    parser.add_argument(
        "--allow-source",
        action="store_true",
        help="Allow source distributions when a compatible wheel is unavailable.",
    )
    parser.add_argument("--python-version", help="Target Python version for pip, such as 3.12.")
    parser.add_argument("--platform", help="Target pip platform tag, such as win_amd64 or manylinux_2_17_x86_64.")
    parser.add_argument("--implementation", help="Target Python implementation tag, such as cp.")
    parser.add_argument("--abi", help="Target Python ABI tag, such as cp312.")
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="List the selected direct requirement entries without downloading.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    requirements = PROFILES[args.profile]
    if not requirements.exists():
        print(f"Requirements file not found: {requirements}", file=sys.stderr)
        return 2

    print(f"Profile: {args.profile}")
    print(f"Requirements: {requirements.name}")
    entries = requirement_entries(requirements)
    if entries:
        print("Direct entries:")
        for entry in entries:
            print(f"  {entry}")
    else:
        print("Direct entries: none (Python standard library only)")
    if args.list_only:
        return 0

    destination = Path(args.destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if entries:
        command = build_download_command(args, requirements, destination)
        print(f"Downloading wheels into {destination}")
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            print("pip download failed; no complete offline wheelhouse was produced.", file=sys.stderr)
            return completed.returncode
    else:
        print("No third-party downloads are needed for this profile.")

    manifest_path = write_manifest(destination, profile=args.profile, requirements=requirements, args=args)
    print(f"Wrote dependency manifest: {manifest_path}")
    print("Install later with: pip install --no-index --find-links <wheelhouse> -r <requirements-file>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
