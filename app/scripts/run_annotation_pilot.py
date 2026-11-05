"""Start the real development annotation pilot on localhost only."""
from __future__ import annotations

import json
import os
from pathlib import Path

import uvicorn


def main() -> None:
    private = Path(os.environ["ANNOTATOR_PILOT_DIR"]).resolve()
    manifest_file = next((p for p in (private / "approved_annotation_manifest_v4.json",
                                      private / "approved_annotation_manifest_v3.json",
                                      private / "approved_annotation_manifest_v2.json",
                                      private / "approved_annotation_manifest_v1.json") if p.exists()))
    manifest = json.loads(manifest_file.read_text())
    if len(manifest) not in {100, 535} or any(r["role"] != "development" for r in manifest):
        raise RuntimeError("ANNOTATION_PILOT_MANIFEST_INVALID")
    roots = {str(Path(r["ct_path"]).parent) for r in manifest} | {str(Path(r["mask_path"]).parent) for r in manifest if r["mask_path"]}
    roots |= {str(Path(r["deidentified_report_path"]).parent) for r in manifest if r["deidentified_report_path"]}
    os.environ["ANNOTATOR_DATA_ROOTS"] = os.pathsep.join(sorted(roots))
    os.environ["ANNOTATOR_DB"] = str(private / "annotations.sqlite3")
    os.environ.pop("ANNOTATOR_LINK_ONLY", None)
    os.environ.pop("ANNOTATOR_DIRECT_USER", None)
    uvicorn.run("annotator.app:app", host="127.0.0.1", port=8767, workers=1, access_log=False)


if __name__ == "__main__":
    main()
