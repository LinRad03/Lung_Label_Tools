"""Attach safe structured report cues to an existing development pilot.

The raw report is used only to verify a unique report per exact CT. It is never
copied into a cue file or printed. This is a versioned amendment to the pilot.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

from annotator.db import connect

LOBE_CN = {"RUL": "右上叶", "RML": "右中叶", "RLL": "右下叶",
           "LUL": "左上叶", "LLL": "左下叶"}


def cue_for_lobe_rows(rows: list[dict[str, str]]) -> str:
    if {r["lobe_name"] for r in rows} != set(LOBE_CN) or len(rows) != 5:
        raise ValueError("LOBE_ROWS_INCOMPLETE")
    if any(r["has_nodule"] not in {"0", "1"} for r in rows):
        raise ValueError("NODULE_FLAG_INVALID")
    positive = [LOBE_CN[name] for name in LOBE_CN
                if any(r["lobe_name"] == name and r["has_nodule"] == "1" for r in rows)]
    if positive:
        detail = "肺结节阳性解析标记；涉及叶区：" + "、".join(positive) + "。"
    else:
        detail = "未提取到肺结节阳性标记；这不表示报告明确阴性。"
    return "报告衍生弱提示（非报告原文，未经人工语义核验）\n" + detail + "\n请以CT阅片独立确定疾病状态与空间位置。\n"


def main() -> None:
    private = Path(os.environ["ANNOTATOR_PILOT_DIR"]).resolve()
    table = Path(os.environ["ANNOTATOR_REPORT_TABLE"]).resolve(strict=True)
    original = private / "approved_annotation_manifest_v1.json"
    amended = private / "approved_annotation_manifest_v2.json"
    if amended.exists():
        print("REPORT_CUES_ALREADY_ATTACHED")
        return
    cases = json.loads(original.read_text())
    if len(cases) != 100 or any(c["role"] != "development" or c["deidentified_report_path"] for c in cases):
        raise RuntimeError("PILOT_SOURCE_INVALID")
    by_name = defaultdict(list)
    for case in cases:
        by_name[Path(case["ct_path"]).name].append(case)
    if any(len(v) != 1 for v in by_name.values()):
        raise RuntimeError("AMBIGUOUS_FILENAME_LEAD")
    matched: dict[str, list[dict[str, str]]] = defaultdict(list)
    with table.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            candidate = by_name.get(Path(row["ct_path"]).name)
            if candidate and os.path.samefile(row["ct_path"], candidate[0]["ct_path"]):
                matched[candidate[0]["case_id"]].append(row)
    if len(matched) != 100:
        raise RuntimeError("REPORT_CT_IDENTITY_INCOMPLETE")
    cues: dict[str, str] = {}
    for case in cases:
        rows = matched[case["case_id"]]
        if len({r["full_report_text"] for r in rows}) != 1 or not rows[0]["full_report_text"].strip():
            raise RuntimeError("REPORT_NOT_UNIQUE_OR_EMPTY")
        cues[case["case_id"]] = cue_for_lobe_rows(rows)
    cue_dir = private / "report_cues_v1"
    cue_dir.mkdir(mode=0o700, exist_ok=False)
    os.chmod(cue_dir, 0o700)
    updated = []
    for case in cases:
        cue_file = cue_dir / (case["case_id"] + ".txt")
        fd = os.open(cue_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(cues[case["case_id"]])
        updated.append({**case, "deidentified_report_path": str(cue_file),
                        "source_version": "dev_pilot_train_v2_report_cues_v1"})
    temporary = amended.with_suffix(".json.partial")
    temporary.write_text(json.dumps(updated, indent=2))
    os.chmod(temporary, 0o600)
    temporary.replace(amended)
    db = private / "annotations.sqlite3"
    with connect(db) as cx:
        for case in updated:
            changed = cx.execute("UPDATE cases SET report_path=?,source_version=? WHERE case_id=? AND role='development' AND report_path IS NULL",
                                 (case["deidentified_report_path"], case["source_version"], case["case_id"]))
            if changed.rowcount != 1:
                raise RuntimeError("CASE_REPORT_UPDATE_CONFLICT")
    status = {"cases_with_structured_report_cue": len(updated),
              "original_manifest_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
              "amended_manifest_sha256": hashlib.sha256(amended.read_bytes()).hexdigest(),
              "report_table_sha256": hashlib.sha256(table.read_bytes()).hexdigest(),
              "raw_report_exported": False}
    (private / "report_cue_amendment_v1.json").write_text(json.dumps(status, indent=2))
    print({"cases_with_structured_report_cue": len(updated), "raw_report_exported": False})


if __name__ == "__main__":
    main()
