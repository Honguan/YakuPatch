"""Private subprocess entry point for GUI writeback previews."""
import json
import pickle
import sys
import traceback
from pathlib import Path

from . import engines, writeback
from .exchange import Store


if __name__ == "__main__":
    game, name, folder = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    try:
        data = json.loads((folder / "input.json").read_text("utf-8"))
        store = Store(folder)
        store.manifest, store.records = data["manifest"], data["records"]
        plugin = next(p for p in engines.PLUGINS if p.name == name)
        result = writeback.plan(game, store, plugin)
        with (folder / "plan.pickle").open("wb") as output:
            pickle.dump(result, output, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as ex:
        (folder / "error.txt").write_text(str(ex), encoding="utf-8")
        traceback.print_exc()
        sys.exit(1)
