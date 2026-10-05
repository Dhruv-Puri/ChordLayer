"""Tests for tools/build_standalone.py (single-file build)."""

import importlib.util
import os
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)

import fake_flp  # noqa: E402


def load_builder():
    path = os.path.join(ROOT, "tools", "build_standalone.py")
    spec = importlib.util.spec_from_file_location("build_standalone", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def drop_chordlayer_modules():
    for name in [n for n in sys.modules if n == "chordlayer" or n.startswith("chordlayer.")]:
        del sys.modules[name]


class TestStandaloneBuild(unittest.TestCase):
    def tearDown(self):
        drop_chordlayer_modules()  # let other tests import the real package again

    def test_build_embeds_engine(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as tmp:
            path = builder.build_standalone(out_dir=tmp)
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotIn("_INLINE_ENGINE = {}", source)
            for engine_file in builder.ENGINE_FILES:
                self.assertIn(engine_file, source)

    def test_standalone_script_runs_without_package_folder(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as tmp:
            path = builder.build_standalone(out_dir=tmp)
            drop_chordlayer_modules()

            fake_flp.install()
            fake_flp.reset(PPQ=240)
            loader = SourceFileLoader("chordlayer_standalone", path)
            spec = importlib.util.spec_from_loader("chordlayer_standalone", loader)
            module = importlib.util.module_from_spec(spec)
            sys.modules["chordlayer_standalone"] = module
            loader.exec_module(module)

            # The inline engine must be importable as a normal package.
            self.assertIn("chordlayer", sys.modules)
            self.assertIn("chordlayer.theory", sys.modules)

            # Empty roll -> a user-facing message, no crash.
            form = module.createDialog()
            module.apply(form)
            self.assertTrue(fake_flp.Utils.messages)

            # With a chord present it must generate tagged melody notes.
            for pitch in (57, 60, 64):
                fake_flp.score.addNote(fake_flp.Note(number=pitch, time=0, length=960))
            module.apply(form)
            melody = [note for note in fake_flp.score._notes if note.color == 15]
            self.assertTrue(melody)


if __name__ == "__main__":
    unittest.main()
