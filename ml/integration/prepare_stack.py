"""Make a new disposable copy of the UI, backend and contracts inside ml/.tmp."""
from pathlib import Path
import shutil
from tempfile import mkdtemp

ML = Path(__file__).resolve().parents[1]
SOURCES = {
    "backend": ("app", "migrations", "tests", "alembic.ini", "pytest.ini", "requirements.lock"),
    "contracts": ("schemas", "examples"),
    "frontend": ("src", "public", "tests", "index.html", "vite.config.ts", "tsconfig.json",
                 "package-lock.json", "package.json"),
    "tools": ("check_contracts.py",),
}


def main():
    parent = ML / ".tmp"
    parent.mkdir(exist_ok=True)
    stage = Path(mkdtemp(prefix="live-rehearsal-", dir=parent))
    ignore = shutil.ignore_patterns("__pycache__", ".tmp*", ".pytest_cache", "*.pyc")
    for area, names in SOURCES.items():
        (stage / area).mkdir()
        for name in names:
            src, dst = ML.parent / area / name, stage / area / name
            if src.is_dir():
                shutil.copytree(src, dst, ignore=ignore)
            else:
                shutil.copyfile(src, dst)
    (stage / "backend" / "data").mkdir()
    # Only ASCII crosses the PowerShell/Python pipe on Windows.
    print(stage.name)


if __name__ == "__main__":
    main()
