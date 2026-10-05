"""Shared helpers for the ChordLayer test suite."""

from __future__ import annotations

import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
SCRIPTS = os.path.join(ROOT, "scripts")

for path in (SRC, HERE, SCRIPTS):
    if path not in sys.path:
        sys.path.insert(0, path)


def load_script(filename: str, module_name: str):
    """Import a .pyscript file as a module (requires fake_flp.install() first).

    .pyscript is not a known extension, so we supply a SourceFileLoader
    explicitly - exactly the kind of load FL Studio performs on script files.
    """
    path = os.path.join(SCRIPTS, filename)
    loader = SourceFileLoader(module_name, path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module
