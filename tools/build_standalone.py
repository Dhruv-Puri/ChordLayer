"""Build a single-file ChordLayer script with the engine inlined.

FL Studio loads the bundled ``chordlayer`` package from the same folder as the
script. If a user would rather have one self-contained file (or an FL build
misbehaves with sibling packages), this tool produces
``dist/ChordLayer (standalone).pyscript`` with every engine module embedded.

Usage:
    python tools/build_standalone.py [--out dist]
"""

from __future__ import annotations

import argparse
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
SRC_DIR = os.path.join(ROOT, "src")

# Dependency order: theory/rng first, the package __init__ last.
ENGINE_FILES = (
    "chordlayer/theory.py",
    "chordlayer/rng.py",
    "chordlayer/chord_detect.py",
    "chordlayer/generator.py",
    "chordlayer/instruments.py",
    "chordlayer/__init__.py",
)
MARKER = "_INLINE_ENGINE = {}"


def build_standalone(script_name: str = "ChordLayer.pyscript", out_dir: str | None = None):
    """Return the path of the generated single-file script."""
    with open(os.path.join(SCRIPTS_DIR, script_name), "r", encoding="utf-8") as handle:
        script_source = handle.read()
    if MARKER not in script_source:
        raise RuntimeError(
            f"{script_name} no longer contains the inline marker {MARKER!r}"
        )

    payload = {}
    for relative in ENGINE_FILES:
        with open(os.path.join(SRC_DIR, relative), "r", encoding="utf-8") as handle:
            payload[relative] = handle.read()

    standalone = script_source.replace(MARKER, "_INLINE_ENGINE = " + repr(payload), 1)
    standalone = standalone.replace(
        "import chordlayer.generator as generator",
        "# engine inlined above\nimport chordlayer.generator as generator",
        1,
    )

    target_dir = out_dir or os.path.join(ROOT, "dist")
    os.makedirs(target_dir, exist_ok=True)
    stem = os.path.splitext(script_name)[0]
    target = os.path.join(target_dir, f"{stem} (standalone).pyscript")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(standalone)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="output directory (default: dist/)")
    parser.add_argument("--script", default="ChordLayer.pyscript")
    args = parser.parse_args()
    path = build_standalone(args.script, args.out)
    size = os.path.getsize(path)
    print(f"wrote {path} ({size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
