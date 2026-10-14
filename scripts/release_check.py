"""Check publishable files without printing secrets; optionally export a clean tarball."""
import argparse
import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PREFIXES = ("history/", "backend/data/", "artifacts/", "experiments/")
KEY_PATTERN = re.compile(rb"(?:sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|LTAI[A-Za-z0-9]{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
SECRET_ASSIGNMENT = re.compile(
    r'''(?im)^[ \t]*["']?(?:[A-Z_]*(?:API_KEY|ACCESS_KEY_SECRET|ACCESS_KEY_ID|SECURITY_TOKEN)|GRADING_AUTH_PASSWORD)["']?\s*[:=][ \t]*["']?([^\s"'#,}]+)'''
)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def private_path(name):
    path = Path(name)
    return (name.startswith(PRIVATE_PREFIXES)
            or path.name.startswith(".env") and path.name != ".env.example"
            or path.name.startswith("config.local.")
            or path.suffix in {".pem", ".key", ".p12", ".pfx"})


def local_secrets():
    values = set()
    for name in ("backend/.env", "backend/ocr/config.local.toml"):
        path = ROOT / name
        if path.exists():
            values.update(value.encode() for value in SECRET_ASSIGNMENT.findall(path.read_text()) if len(value) >= 8)
    return values


def contains_secret(data, known):
    return bool(KEY_PATTERN.search(data) or any(secret in data for secret in known))


def candidate_files():
    names = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
    return sorted({name for name in names if name and (ROOT / name).exists()})


def scan_history(known):
    findings = []
    objects = git("rev-list", "--objects", "--all").decode().splitlines()
    proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for entry in objects:
            oid, _, name = entry.partition(" ")
            proc.stdin.write((oid + "\n").encode())
            proc.stdin.flush()
            header = proc.stdout.readline().split()
            data = proc.stdout.read(int(header[2]))
            proc.stdout.read(1)
            if header[1] == b"blob" and contains_secret(data, known):
                findings.append(f"historical secret candidate: {oid[:12]} {name}")
    finally:
        proc.stdin.close()
        proc.wait()
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="scan all reachable Git objects too")
    parser.add_argument("--staged", action="store_true", help="check the exact Git index before commit")
    parser.add_argument("--export", type=Path, help="write a clean source tar.gz; never includes .git")
    args = parser.parse_args()
    known = local_secrets()
    findings = []
    names = candidate_files()
    if args.staged:
        names = [name for name in git("ls-files", "-z").decode().split("\0") if name]
    for name in names:
        path = ROOT / name
        if private_path(name) or path.is_symlink():
            findings.append(f"private path or symlink in publish set: {name}")
            continue
        data = git("show", f":{name}") if args.staged else path.read_bytes()
        if contains_secret(data, known):
            findings.append(f"secret candidate: {name}")
        # Templates must contain no real credential values, regardless of format.
        if ".example" in name:
            for value in SECRET_ASSIGNMENT.findall(data.decode("utf-8", errors="replace")):
                if value:
                    findings.append(f"nonempty credential in template: {name}")
    if args.history:
        findings.extend(scan_history(known))
    if findings:
        print("Release check FAILED (values redacted):\n" + "\n".join(findings))
        return 1
    if args.export:
        if args.staged:
            parser.error("--export uses the worktree; do not combine with --staged")
        destination = args.export.resolve()
        if not destination.is_relative_to(ROOT / "history"):
            parser.error("write release archives under ignored history/ only")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(destination, "w:gz") as archive:
            for name in names:
                archive.add(ROOT / name, arcname=f"grading/{name}", recursive=False)
        print(f"Clean source archive: {destination}")
    print(f"Release check passed: {len(names)} files; known local credentials and key patterns checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
