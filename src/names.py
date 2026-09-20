"""Turning a business name into something comparable.

Nothing here calls a model. Name matching is the weakest evidence we use, so
the rules have to be visible and arguable rather than learned.
"""

from __future__ import annotations

import re

# Legal form suffixes. Stripped for comparison, kept as a separate signal:
# a PTY LTD and an INC with the same stem are probably different bodies.
LEGAL_FORMS = {
    "PTY LTD": "pty_ltd", "PTY LIMITED": "pty_ltd", "PTY. LTD.": "pty_ltd",
    "PROPRIETARY LIMITED": "pty_ltd", "LIMITED": "ltd", "LTD": "ltd",
    "INCORPORATED": "inc", "INC": "inc",
    "ASSOCIATION": "association", "ASSOC": "association",
    "CO-OPERATIVE": "cooperative", "COOPERATIVE": "cooperative",
    "TRUST": "trust", "FOUNDATION": "foundation",
    "NL": "nl", "PLC": "plc",
}

# Words that only appear in an organisation's name, never a person's.
ORGANISATION_WORDS = re.compile(
    r"\b(PTY|LTD|LIMITED|INC|INCORPORATED|TRUST|FOUNDATION|ASSOCIATION|"
    r"COUNCIL|SOCIETY|CHURCH|SCHOOL|COLLEGE|UNIVERSITY|HOSPITAL|CLUB|GROUP|"
    r"HOLDINGS|SERVICES|SOLUTIONS|ENTERPRISES|INDUSTRIES|PARTNERS|COMPANY|"
    r"CORPORATION|CENTRE|CENTER|INSTITUTE|BOARD|AUTHORITY|COMMISSION|"
    r"COOPERATIVE|CO-OPERATIVE|NL|PLC)\b")


def normalise(name: str | None) -> str:
    """Uppercase, punctuation gone, legal form removed, spaces collapsed."""
    if not name:
        return ""
    text = re.sub(r"[^A-Z0-9& ]", " ", name.upper())
    text = re.sub(r"\s+", " ", text).strip()
    # Strip a trailing legal form, longest first so "PTY LTD" beats "LTD".
    for form in sorted(LEGAL_FORMS, key=len, reverse=True):
        if text.endswith(" " + form) or text == form:
            text = text[: -len(form)].strip()
            break
    return re.sub(r"\s+", " ", text).strip()


def legal_form(name: str | None) -> str:
    """Which legal form the name declares, if any."""
    if not name:
        return ""
    text = re.sub(r"[^A-Z0-9& ]", " ", name.upper())
    text = re.sub(r"\s+", " ", text).strip()
    for form in sorted(LEGAL_FORMS, key=len, reverse=True):
        if text.endswith(" " + form) or text == form:
            return LEGAL_FORMS[form]
    return ""


# Words that never appear in a person's name. Without these, "Arthritis NSW"
# reads as two name-shaped words and gets refused as an individual.
NOT_A_PERSON_WORD = {
    "NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT", "NZ",
    "AUSTRALIA", "AUSTRALIAN", "NATIONAL", "REGIONAL", "STATE", "FEDERAL",
    "NORTH", "SOUTH", "EAST", "WEST", "CENTRAL", "UPPER", "LOWER",
    "CITY", "SHIRE", "TOWN", "VALLEY", "RIVER", "BAY", "PARK", "HILL",
    "FIRST", "SECOND", "NEW", "OLD", "GREAT", "ROYAL", "UNITED",
}


def looks_like_a_person(name: str | None) -> bool:
    """A name that reads as an individual rather than an organisation.

    The Victorian liquor register lists a licensee, and often that is a person:
    "ARNOLD BRUCE" trading as University Ski Club. Matching people as if they
    were businesses is the merge that quietly corrupts everything above it.
    """
    if not name:
        return False
    text = re.sub(r"[^A-Z ]", " ", name.upper())
    text = re.sub(r"\s+", " ", text).strip()
    if ORGANISATION_WORDS.search(text):
        return False
    words = text.split()
    if not (2 <= len(words) <= 3 and all(len(w) >= 2 for w in words)):
        return False
    return not any(w in NOT_A_PERSON_WORD for w in words)


def too_generic(normalised: str) -> bool:
    """A name with nothing distinctive enough to carry a match on its own."""
    if len(normalised) < 6:
        return True
    return len(normalised.split()) < 2 and len(normalised) < 10


# Words that mark a body attached to another organisation rather than the
# organisation itself. A foundation is not its college. A committee of
# management is not the association it manages. They share an address, a
# postcode and most of a name, which is exactly what a name-based matcher
# falls for.
#
# The rule is asymmetric: we only refuse when ONE side carries such a word.
# Two foundations with the same name are still plausibly the same foundation.
RELATED_BODY = re.compile(
    r"\b(FOUNDATION|COMMITTEE|AUXILIARY|AUXILIARIES|FRIENDS|ALUMNI|"
    r"ENDOWMENT|APPEAL|SUB BRANCH|SUBBRANCH|BRANCH|CHAPTER|GUILD|"
    r"TRUSTEE|BEQUEST|SCHOLARSHIP)\b")


def related_body_marker(name: str | None) -> str:
    """The word marking this as a body attached to something else, if any."""
    if not name:
        return ""
    text = re.sub(r"[^A-Z0-9 ]", " ", name.upper())
    found = RELATED_BODY.search(re.sub(r"\s+", " ", text))
    return found.group(1) if found else ""
