"""docs/art/make-panel.py: the same bytes on every run, and the committed SVG files are its output."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("fontTools") is None or importlib.util.find_spec("brotli") is None,
    reason="needs docs/art/requirements.txt (fonttools and brotli)",
)

PANELS = ("panel-dark.svg", "panel-light.svg")


def draw(root: Path) -> dict[str, bytes]:
    subprocess.run(
        [sys.executable, str(root / "docs" / "art" / "make-panel.py")],
        cwd=root, check=True, capture_output=True, timeout=300,
    )
    return {name: (root / "docs" / "assets" / name).read_bytes() for name in PANELS}


@pytest.fixture
def copy(tmp_path):
    """The script, its fonts and the recipe it reads, in a folder of their own."""
    for part in ("docs/art", "docs/fonts", "recipes/kerbrute"):
        shutil.copytree(ROOT / part, tmp_path / part)
    (tmp_path / "docs" / "assets").mkdir()
    return tmp_path


def test_the_script_writes_the_same_bytes_twice(copy):
    assert draw(copy) == draw(copy)


def test_the_committed_panels_are_what_the_script_writes(copy):
    drawn = draw(copy)
    for name in PANELS:
        assert drawn[name] == (ROOT / "docs" / "assets" / name).read_bytes(), name
