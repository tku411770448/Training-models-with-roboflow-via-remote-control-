from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()


def get_path(relative_path: str, check_exists: bool = False) -> str:
    path = (PROJECT_ROOT / relative_path).resolve()
    if check_exists and not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")
    return str(path)
