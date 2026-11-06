"""Fail unless pyproject.toml, server.json, and (optionally) a git tag agree on the version.

Usage: python scripts/check_version.py [v1.2.3]
"""

import json
import sys
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parent.parent
versions = {
    "pyproject.toml": tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"],
    "server.json": json.loads((root / "server.json").read_text())["version"],
}
versions["server.json packages[0]"] = json.loads((root / "server.json").read_text())["packages"][0]["version"]
if len(sys.argv) > 1:
    versions["git tag"] = sys.argv[1].removeprefix("v")

if len(set(versions.values())) != 1:
    for where, v in versions.items():
        print(f"{where}: {v}")
    sys.exit("Versions don't match. Update them all before tagging a release.")
print(f"Version {versions['pyproject.toml']} is consistent.")
