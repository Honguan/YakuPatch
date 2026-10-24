"""E1 Detector: pick the engine plugin for a game folder."""
from pathlib import Path

from .base import EnginePlugin, LangPack, Segment
from .rpgmaker_mz import RpgMakerMZ
from .rpgmaker_mv import RpgMakerMV
from .renpy import RenPy
from .tyrano import Tyrano
from .kirikiri import KiriKiri
from .unity import Unity
from .godot import Godot
from .artemis import Artemis
from .wolf import Wolf

PLUGINS: list[EnginePlugin] = [RpgMakerMZ(), RpgMakerMV(), Tyrano(), RenPy(), KiriKiri(), Unity(), Godot(), Artemis(), Wolf()]
MIN_SCORE = 0.9


def detect(game_dir: Path) -> tuple[EnginePlugin | None, float]:
    best = max(((p, p.detect(game_dir)) for p in PLUGINS), key=lambda t: t[1])
    return best if best[1] >= MIN_SCORE else (None, best[1])


def find_game_roots(path: Path, depth: int = 3) -> list[tuple[Path, EnginePlugin]]:
    """Users often pick a parent folder (e.g. the one holding PC/); look a few levels down."""
    plugin, _ = detect(path)
    if plugin:
        return [(path, plugin)]
    if depth == 0:
        return []
    found = []
    try:
        children = sorted(p for p in path.iterdir() if p.is_dir() and not p.name.startswith("_yakupatch"))
    except OSError:          # unreadable folders (permissions, offline drives) are skipped while scanning a library
        return found
    for child in children:
        found += find_game_roots(child, depth - 1)
    return found
