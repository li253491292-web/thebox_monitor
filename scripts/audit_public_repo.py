"""Block sensitive runtime data and credentials before public Git uploads."""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GIT_EXE = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
FORBIDDEN_PATHS = (
    ".env",
    "browser_profile/",
    "data/",
    "logs/",
    "site/",
    "config.yaml",
    "debug.log",
)
FORBIDDEN_REPORT_PATTERNS = (
    re.compile(r"^reports/report_.*\.html$"),
    re.compile(r"^reports/daily_report_.*\.md$"),
    re.compile(r"^reports/.*\.xlsx$"),
    re.compile(r"^reports/大纲_"),
)
QUOTED_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(app[_-]?secret|api[_-]?key|access[_-]?token|refresh[_-]?token|password|cookie|session[_-]?id|private[_-]?key)\b\s*[:=]\s*(['\"])([^'\"]+)\2"
)
CONFIG_SECRET_ASSIGNMENT = re.compile(
    r"(?i)^\s*(app[_-]?secret|api[_-]?key|access[_-]?token|refresh[_-]?token|password|cookie|session[_-]?id|private[_-]?key)\s*:\s*([^#\s]+)"
)
TOKEN_VALUE = re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|eyJ[A-Za-z0-9_-]{20,})\b")
SAFE_VALUES = {"", "null", "none", "your-value", "your-model-name", "recipient@example.com"}


def is_forbidden_path(path):
    normalized = path.replace("\\", "/")
    if normalized in FORBIDDEN_PATHS or any(normalized.startswith(prefix) for prefix in FORBIDDEN_PATHS if prefix.endswith("/")):
        return True
    return any(pattern.search(normalized) for pattern in FORBIDDEN_REPORT_PATTERNS)


def is_placeholder(value):
    value = value.strip().strip("'\"").lower()
    return (
        value in SAFE_VALUES
        or value.startswith("${")
        or value.startswith("<")
        or value.startswith("your_")
        or value.endswith("@example.com")
        or value.isupper()
    )


def scan_file(path):
    findings = []
    if is_forbidden_path(path.as_posix()):
        findings.append("forbidden runtime/content path")
        return findings
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return findings
    for line_number, line in enumerate(text.splitlines(), 1):
        for match in QUOTED_SECRET_ASSIGNMENT.finditer(line):
            if not is_placeholder(match.group(3)):
                findings.append(f"possible credential at line {line_number}")
        if path.suffix.lower() in {".yaml", ".yml", ".json", ".env"}:
            match = CONFIG_SECRET_ASSIGNMENT.search(line)
            if match and not is_placeholder(match.group(2)):
                findings.append(f"possible configuration credential at line {line_number}")
        if TOKEN_VALUE.search(line):
            findings.append(f"token-like value at line {line_number}")
    return findings


def git_paths(mode):
    command = ["git", "diff", "--cached", "--name-only", "-z"] if mode == "staged" else ["git", "ls-files", "-z"]
    result = subprocess.run(
        [GIT_EXE, "-c", f"safe.directory={PROJECT_ROOT}", *command[1:]],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    )
    return [Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args()
    paths = git_paths("staged" if args.staged else "tracked")
    violations = []
    for relative_path in paths:
        full_path = PROJECT_ROOT / relative_path
        if not full_path.is_file():
            continue
        for finding in scan_file(full_path):
            violations.append(f"{relative_path}: {finding}")
    if violations:
        print("Public repository audit failed:")
        print("\n".join(f"- {finding}" for finding in violations))
        return 1
    print(f"Public repository audit passed ({len(paths)} files checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
