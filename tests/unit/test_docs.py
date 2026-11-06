"""Every relative link in the documentation leads somewhere: the file exists, and the heading it
points to (``#anchor``) exists in that file. Anchors follow GitHub's rule for headings."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = sorted(
    [ROOT / "README.md", *(ROOT / "docs").glob("*.md")],
    key=lambda path: path.as_posix(),
)
LOCAL_ONLY = {"PROJECT_BRIEF.md"}  # git-ignored; never part of the public documentation
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")


def _anchor(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces as hyphens."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    anchors, seen = set(), dict[str, int]()
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
        if in_code or not (match := HEADING.match(line)):
            continue
        base = _anchor(match.group(1))
        count = seen.get(base, 0)
        seen[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


@pytest.mark.parametrize("document", [d for d in DOCUMENTS if d.name not in LOCAL_ONLY], ids=str)
def test_every_relative_link_resolves(document: Path) -> None:
    text = document.read_text(encoding="utf-8")
    broken = []
    for target in LINK.findall(text):
        if re.match(r"^[a-z]+:", target):  # http:, https:, mailto:
            continue
        path_part, _, anchor = target.partition("#")
        linked = (document.parent / path_part).resolve() if path_part else document
        if not linked.exists():
            broken.append(f"{target}: no such file")
        elif anchor and linked.suffix == ".md" and anchor not in _anchors(linked):
            broken.append(f"{target}: no such heading")
    assert broken == []


def test_the_anchor_rule_matches_github() -> None:
    heading = "Batteries and charging policies (Phase 2, P2, 2026-11-05)"

    assert _anchor(heading) == "batteries-and-charging-policies-phase-2-p2-2026-11-05"
    assert _anchor("Breakdowns: re-timing and replanning") == "breakdowns-re-timing-and-replanning"
