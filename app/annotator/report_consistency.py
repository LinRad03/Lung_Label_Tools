"""Conservative, versioned report mention comparison for annotation review.

This is a review cue, not a validated clinical report parser. It never turns an
unmentioned disease into an explicit negative label.
"""
from __future__ import annotations

import re

VERSION = "report_consistency_v1"
TERMS = {
    "pulmonary_nodule": ("磨玻璃结节", "实性结节", "小结节", "结节", "nodules", "nodule"),
    "atelectasis": ("肺不张", "atelectasis"),
    "consolidation": ("实变", "consolidation"),
    "bronchiectasis": ("支气管扩张", "bronchiectasis"),
    "pleural_effusion": ("胸腔积液", "胸水", "pleural effusion"),
    "lymphadenopathy": ("淋巴结肿大", "肿大淋巴结", "lymphadenopathy"),
    "pericardial_effusion": ("心包积液", "pericardial effusion"),
    "pulmonary_bulla": ("肺大泡", "肺大疱", "bullae", "bulla"),
    "copd_review": ("慢性阻塞性肺疾病", "慢性阻塞性肺病", "慢阻肺", "copd"),
}
NEGATION = re.compile(r"(?:未见|无明显|未发现|未提示|排除|没有|无|no\s+evidence\s+of|without|no)\s*(?:(?:明显|双肺|左肺|右肺|肺部|肺|胸腔|心包|纵隔|肺门)\s*)*$", re.I)
UNCERTAIN = re.compile(r"(?:考虑|疑似|可能|待排|不能排除|不除外|倾向|可疑|suspect|possible|may\s+be)", re.I)
HISTORICAL = re.compile(r"(?:既往|病史|术后|已消退|吸收|history\s+of|resolved)", re.I)
CONCLUSION_HEADING = re.compile(r"(?:诊断意见|影像诊断|检查结论|报告结论|印象|结论)\s*[:：]", re.I)


def compare(report: str, disease: str, human_state: str) -> dict[str, str]:
    if human_state not in {"positive", "negative", "uncertain"}:
        raise ValueError("INVALID_STATE")
    if disease not in TERMS:
        raise ValueError("UNSUPPORTED_DISEASE")
    headings = list(CONCLUSION_HEADING.finditer(report))
    scope = "conclusion" if headings else "full_report"
    analyzed = report[headings[-1].end():] if headings else report
    positives = negatives = uncertain = 0
    for clause in re.split(r"[。；;\n.!?！？]", analyzed):
        lower = clause.casefold()
        for term in TERMS[disease]:
            pattern = re.compile(r"(?<![a-z])" + re.escape(term.casefold()) + r"(?![a-z])") if term[0].isascii() else re.compile(re.escape(term))
            for match in pattern.finditer(lower):
                prefix = lower[max(0, match.start() - 30):match.start()]
                context = lower[max(0, match.start() - 30):min(len(lower), match.end() + 15)]
                if HISTORICAL.search(context) or UNCERTAIN.search(context):
                    uncertain += 1
                elif NEGATION.search(prefix):
                    negatives += 1
                else:
                    positives += 1
    if uncertain or (positives and negatives):
        report_state = "uncertain"
    elif positives:
        report_state = "positive"
    elif negatives:
        report_state = "negative"
    else:
        report_state = "unmentioned"
    if human_state == "uncertain" or report_state == "uncertain":
        agreement, reason = "uncertain", "uncertain_or_conflicting"
    elif report_state == "unmentioned":
        agreement, reason = ("disagree", "positive_not_mentioned") if human_state == "positive" else ("uncertain", "not_mentioned_is_not_negative")
    elif human_state == report_state:
        agreement, reason = "agree", "states_match"
    else:
        agreement, reason = "disagree", "states_conflict"
    return {"report_state": report_state, "agreement": agreement, "reason": reason,
            "scope": scope, "version": VERSION}
