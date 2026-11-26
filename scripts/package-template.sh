#!/usr/bin/env bash
# Package the committed template as a zip in dist/ (git archive of HEAD). It
# refuses a dirty tree and rejects secret or runtime paths even if force-added.
# See docs/development.md#packaging.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing to package: commit or stash your changes first." >&2
  git status --short >&2
  exit 1
fi

if ! py="$(command -v python3 || command -v python)"; then
  echo "Refusing to package: python3 is required to audit the zip." >&2
  exit 1
fi

repo_name="$(basename "$repo_root")"
mkdir -p dist
zip_path="dist/${repo_name}-$(git rev-parse --short HEAD).zip"

git archive --format=zip --prefix="${repo_name}/" -o "$zip_path" HEAD

if ! "$py" - "$zip_path" "$repo_name" <<'PY'
import fnmatch
import subprocess
import sys
import zipfile
from pathlib import Path

zip_path = Path(sys.argv[1])
prefix = sys.argv[2].rstrip("/") + "/"


def fail(msg: str, *lines: str) -> None:
    print(msg, file=sys.stderr)
    for line in lines:
        print(line, file=sys.stderr)
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass
    raise SystemExit(1)


try:
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
except Exception as exc:  # noqa: BLE001 — any listing failure must not keep the zip
    fail(f"Refusing to package: cannot read archive: {exc}")

files = [
    n[len(prefix) :]
    for n in names
    if n.startswith(prefix) and n != prefix and not n.endswith("/")
]

listed = subprocess.check_output(["git", "ls-files", "-z"], text=True)
expected = {p for p in listed.split("\0") if p}
got = set(files)
if got != expected:
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    detail = []
    if missing:
        detail.append("missing from zip:")
        detail.extend(f"  {p}" for p in missing[:50])
    if extra:
        detail.append("unexpected in zip:")
        detail.extend(f"  {p}" for p in extra[:50])
    fail("Refusing to package: zip contents do not match git ls-files.", *detail)

# Every secret name .gitignore ignores, and more: a force-added file is refused too.
SECRET_PATTERNS = (
    ".envrc",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "id_dsa*",
    "secrets.y*ml",
    "credentials.json",
    "*.pem",
    "*.key",
)
DENIED_DIRS = {
    "__pycache__",
    ".venv",
    "venv",
    ".git",
    "staticfiles",
    "graphify-out",
    ".graphify",
    "htmlcov",
    "dist",
    "private_media",
}
# "media" is denied only as a path segment at the repo root.


def is_forbidden(rel: str) -> bool:
    parts = Path(rel).parts
    name = parts[-1] if parts else rel
    if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
        return True
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in SECRET_PATTERNS):
        return True
    if name.endswith(".pyc") or name.startswith(".coverage"):
        return True
    if parts and parts[0] == "media":
        return True
    for part in parts:
        if part in DENIED_DIRS or part.endswith("_cache"):
            return True
    return False


leaked = [f"{prefix}{rel}" for rel in files if is_forbidden(rel)]
if leaked:
    fail("Refusing to package: the archive would contain:", *leaked)

raise SystemExit(0)
PY
then
  rm -f "$zip_path"
  exit 1
fi

echo "$zip_path"
