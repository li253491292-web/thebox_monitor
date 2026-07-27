"""Install local Git hooks that run the public repository audit."""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = Path(r"C:\Users\lihongru02\AppData\Local\Programs\Python\Python314\python.exe")
HOOKS = {
    "pre-commit": "--staged",
    "pre-push": "",
}


def install():
    hooks_dir = PROJECT_ROOT / ".git" / "hooks"
    if not hooks_dir.exists():
        raise RuntimeError("Git repository is not initialized")
    for hook_name, option in HOOKS.items():
        hook_path = hooks_dir / hook_name
        command = f'"{PYTHON.as_posix()}" scripts/audit_public_repo.py {option}'.strip()
        hook_path.write_text(f"#!/bin/sh\n{command}\n", encoding="utf-8", newline="\n")
    print("[git] installed public repository audit hooks")


if __name__ == "__main__":
    install()
