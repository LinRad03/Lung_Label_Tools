"""Start localhost technical validation only; never serves raw report text."""
from __future__ import annotations

import json
import os
from pathlib import Path

import uvicorn


def main() -> None:
    private = Path(os.environ["ANNOTATOR_REAL_SMOKE_DIR"]).resolve()
    rows = json.loads((private / "technical_manifest.json").read_text())
    if len(rows) != 1 or rows[0]["project"] != "TECH_ONLY" or rows[0]["deidentified_report_path"] is not None:
        raise RuntimeError("TECHNICAL_MANIFEST_INVALID")
    roots = {str(Path(rows[0]["ct_path"]).parent), str(Path(rows[0]["mask_path"]).parent)}
    os.environ["ANNOTATOR_DATA_ROOTS"] = os.pathsep.join(sorted(roots))
    os.environ["ANNOTATOR_DB"] = str(private / "technical_annotations.sqlite3")
    uvicorn.run("annotator.app:app", host="127.0.0.1", port=8766, workers=1, access_log=False)


if __name__ == "__main__":
    main()
