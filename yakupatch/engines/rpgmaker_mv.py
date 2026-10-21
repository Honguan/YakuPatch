"""MV shares MZ's JSON format; find_game_roots also discovers a nested www/."""
from pathlib import Path

from ..encoding import decode
from ..safety import read_bytes
from .rpgmaker_mz import RpgMakerMZ


class RpgMakerMV(RpgMakerMZ):
    name = "rpgmaker-mv"

    def detect(self, game_dir: Path) -> float:
        return 1.0 if (game_dir / "js" / "rpg_core.js").is_file() and (game_dir / "data").is_dir() else 0.0

    def locate(self, game_dir: Path) -> list[str]:
        files = super().locate(game_dir)
        if "data/System.json" not in files:
            raise ValueError("MV 缺少 System.json（可能是加密 JSON），不支援且不嘗試解密")
        for rel in files:
            raw = read_bytes(game_dir, rel)
            try:
                text, _ = decode(raw, self.default_encoding)
            except UnicodeError:
                text = ""
            if raw.startswith(b"RPGMV") or not text.lstrip().startswith(("{", "[")):
                raise ValueError(f"加密或不支援的 MV JSON：{rel}，不嘗試解密")
        return files

    def title(self, game_dir: Path, read) -> str:
        self.locate(game_dir)
        return super().title(game_dir, read)
