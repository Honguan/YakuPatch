"""EnginePlugin interface (PROJECT_PLAN §3.5)."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Segment:
    loc: str                    # position inside the file, e.g. a JSON Pointer
    kind: str                   # dialogue / choice / name / system / ui / other
    text: str
    speaker: str | None = None
    group: str | None = None    # lines of one message share a group; E6 merges them


@dataclass(frozen=True)
class LangPack:
    lang: str
    path: str
    hint: str


class EnginePlugin:
    name: str
    version: str
    default_encoding: str = "utf-8"
    control_codes: list[str] = []

    def detect(self, game_dir: Path) -> float:
        raise NotImplementedError

    def language_packs(self, game_dir: Path) -> list[LangPack]:
        return []

    # shortcut: loose files only; add containers() (xp3/rpa/asar...) with the first packed engine (I3/I4).
    def locate(self, game_dir: Path) -> list[str]:
        raise NotImplementedError

    def title(self, game_dir: Path, read) -> str:
        """Original game title, used for the project fingerprint. `read(rel)` returns original bytes."""
        return ""

    def parse(self, rel: str, text: str) -> list[Segment]:
        raise NotImplementedError

    def render(self, rel: str, text: str, repl: dict[str, str]) -> str:
        raise NotImplementedError
