import json
from pathlib import Path
from .util import BuildError


def resolve_shim_path(board_info: dict) -> Path | None:
    """Return the configured local shim path for a board, or None."""
    raw = board_info.get("shim_path")
    if not raw:
        return None
    return Path(raw).expanduser()

DATA_DIR = Path(__file__).parent.parent / "data"


def _load() -> dict:
    path = DATA_DIR / "boards.json"
    with open(path) as f:
        return json.load(f)


def get_board(name: str) -> dict:
    boards = _load()
    if name not in boards:
        known = ", ".join(sorted(boards))
        raise BuildError(
            f"Unknown board: '{name}'\n"
            f"Supported boards: {known}\n"
            "Run 'shimpy list-boards' for details."
        )
    return {"name": name, **boards[name]}


def all_boards() -> dict[str, dict]:
    return _load()
