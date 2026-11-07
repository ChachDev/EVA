"""Report edits outside a role. Read-only; never stages or changes Git state."""
import argparse
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ROLES = {"frontend": "frontend", "backend": "backend", "ml": "ml"}


def git_paths(*args: str) -> set[str]:
    result = subprocess.run(
        ["git", "--no-optional-locks", *args], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return {item for item in result.stdout.decode("utf-8", errors="replace").split("\0") if item}


def allowed(name: str, role: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        return False
    if path.parts[0].lower() != ROLES[role]:
        return False
    # Detect symlinks that lead outside the owner directory as well.
    try:
        (ROOT / path).resolve().relative_to((ROOT / role).resolve())
    except ValueError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=ROLES, required=True)
    parser.add_argument("--paths", nargs="+", help="Check proposed paths instead of Git changes")
    args = parser.parse_args()
    if args.paths is not None:
        paths = set(args.paths)
    else:
        try:
            paths = git_paths("diff", "--name-only", "--no-renames", "-z")
            paths |= git_paths("diff", "--cached", "--name-only", "--no-renames", "-z")
            paths |= git_paths("ls-files", "--others", "--exclude-standard", "-z")
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            print(f"Cannot inspect local Git changes: {type(exc).__name__}")
            return 2
    violations = sorted(path for path in paths if not allowed(path, args.role))
    if violations:
        print(f"OUTSIDE {args.role}/:")
        for path in violations:
            print(f"  {path}")
        print("Do not edit these paths. Ask the owner via your CHANGE_REQUESTS.md.")
        print("Before the initial human commit, the whole foundation is untracked.")
        return 1
    print(f"OK: {len(paths)} path(s) inside {args.role}/. This is a diagnostic, not a sandbox.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
