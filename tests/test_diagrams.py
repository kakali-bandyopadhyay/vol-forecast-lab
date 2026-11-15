from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "docs" / "diagrams"
RENDERED = ROOT / "figures" / "diagrams"
GUIDE = ROOT / "BUILD_GUIDE.md"


def _mermaid_blocks(section: str) -> list[str]:
    return [block.strip() for block in re.findall(r"```mermaid\s*\n(.*?)\n```", section, re.S)]


def _guide_section(start: str, end: str) -> str:
    text = GUIDE.read_text(encoding="utf-8")
    return text.split(start, 1)[1].split(end, 1)[0]


def test_rendered_diagrams_exist_and_are_nonempty():
    if not RENDERED.is_dir():
        pytest.skip("figures/diagrams/ is absent; CI may omit locally rendered Mermaid exports")

    sources = sorted(DIAGRAMS.glob("*.mmd"))
    assert sources, "No Mermaid sources found"
    for source in sources:
        for extension in ("svg", "png"):
            output = RENDERED / f"{source.stem}.{extension}"
            assert output.is_file(), f"Missing rendered diagram: {output.relative_to(ROOT)}"
            assert output.stat().st_size > 0, f"Empty rendered diagram: {output.relative_to(ROOT)}"


@pytest.mark.parametrize(
    ("filename", "start", "end"),
    [
        ("step10b.mmd", "## Step 10b", "## Step 10c"),
        ("step10c.mmd", "## Step 10c", "## Step 11"),
    ],
)
def test_step_diagrams_match_build_guide(filename: str, start: str, end: str):
    expected_blocks = _mermaid_blocks(_guide_section(start, end))
    assert expected_blocks, f"No Mermaid block found between {start!r} and {end!r}"
    actual = (DIAGRAMS / filename).read_text(encoding="utf-8").strip()
    assert actual == expected_blocks[0]
