import json
import re
import shutil
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def copy_game(name: str) -> tuple[Path, Path]:
    """Copy a fixture game into a temp dir; returns (game_dir, projects_home)."""
    tmp = Path(tempfile.mkdtemp(prefix="yakupatch-test-"))
    game = tmp / "games" / name
    shutil.copytree(FIXTURES / name, game)
    return game, tmp / "home"


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def fake_translate(masked: str) -> str:
    """Same placeholders and line breaks; other characters become distinct hanzi (keeps the length ratio,
    never trips the repetition check)."""
    return "".join(part if re.fullmatch(r"<\d+>|⟦\d+⟧|\n", part) else
                   "".join(chr(0x4E00 + i) for i in range(len(part)))
                   for part in re.split(r"(<\d+>|⟦\d+⟧|\n)", masked) if part)


def fill_batches(project: Path) -> int:
    out, inn = project / "batches" / "out", project / "batches" / "in"
    inn.mkdir(parents=True, exist_ok=True)
    n = 0
    for part in sorted(out.glob("part-*.jsonl")):
        lines = []
        for line in part.read_text("utf-8").splitlines():
            obj = json.loads(line)
            obj["dst"] = fake_translate(obj["src"])
            lines.append(json.dumps(obj, ensure_ascii=False))
            n += 1
        (inn / part.name).write_text("\n".join(lines) + "\n", "utf-8")
    return n
