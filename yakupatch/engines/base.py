"""EnginePlugin interface (PROJECT_PLAN §3.5)."""
from dataclasses import dataclass
from pathlib import Path

from ..encoding import decode


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

    # Relative paths; a member of a container is "<container>!<inner>" (see vfs.py).
    def locate(self, game_dir: Path) -> list[str]:
        raise NotImplementedError

    def title(self, game_dir: Path, read) -> str:
        """Original game title, used for the project fingerprint. `read(rel)` returns original bytes."""
        return ""

    def decode(self, rel: str, raw: bytes, game_dir: Path | None = None) -> tuple[str, dict]:
        """E4: file bytes to the text parse() reads, plus the encoding info W4 checks translations against."""
        return decode(raw, self.default_encoding)

    def outputs(self, texts: dict[str, str], repl: dict[str, dict[str, str]],
                game_dir: Path | None = None) -> dict[str, tuple[bytes, int]] | None:
        """W5 for engines that add new files instead of editing the originals: {rel: (bytes, translations)} of
        every file the translation needs (files we created earlier and no longer need are deleted).
        None: render each file."""
        return None

    def parse(self, rel: str, text: str) -> list[Segment]:
        raise NotImplementedError

    def render(self, rel: str, text: str, repl: dict[str, str]) -> str:
        raise NotImplementedError

    def fit(self, kind: str, text: str) -> str:
        """Make one translated line safe for the file format (e.g. no line break inside a script line)."""
        return text

    def wrap_rule(self, kind: str) -> bool:
        """W3: True when the engine does not wrap this kind itself, so long lines must be broken by us."""
        return False
