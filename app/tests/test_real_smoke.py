import hashlib
import hmac

from scripts.prepare_real_smoke import pseudonym
from scripts.prepare_annotation_pilot import choose_groups
from scripts.attach_report_cues import cue_for_lobe_rows


def test_stable_non_plaintext_technical_group():
    secret = b"synthetic-secret"
    first = pseudonym(secret, "synthetic group", "PG_")
    assert first == pseudonym(secret, "synthetic group", "PG_")
    assert first != pseudonym(secret, "different synthetic group", "PG_")
    assert "synthetic" not in first


def test_patient_budget_selection_deterministic():
    rows = [{"patient_group_id": f"synthetic_{i}"} for i in range(12)]
    chosen = choose_groups(rows, 42, 5)
    assert chosen == choose_groups(rows, 42, 5)
    assert len({r["patient_group_id"] for r in chosen}) == 5


def test_structured_report_cue_never_copies_report_text():
    rows = [{"lobe_name": lobe, "has_nodule": "1" if lobe == "RUL" else "0",
             "full_report_text": "SYNTHETIC PRIVATE REPORT SENTENCE"}
            for lobe in ("RUL", "RML", "RLL", "LUL", "LLL")]
    cue = cue_for_lobe_rows(rows)
    assert "右上叶" in cue
    assert "SYNTHETIC PRIVATE REPORT SENTENCE" not in cue
    assert "非报告原文" in cue
