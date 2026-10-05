"""Tests for the ChordLayer Studio window app (headless: no window is opened)."""

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)


def load_app():
    """Import the app module without opening a window (it only builds one in run_gui)."""
    path = os.path.join(ROOT, "app", "chordlayer_app.py")
    spec = importlib.util.spec_from_file_location("chordlayer_app", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["chordlayer_app"] = module
    spec.loader.exec_module(module)
    return module


class TestAppHeadless(unittest.TestCase):
    def setUp(self):
        self.app = load_app()

    def test_module_imports_without_tkinter(self):
        # run_gui imports tkinter lazily, so importing the module must be safe
        # even where tkinter is missing.
        self.assertTrue(hasattr(self.app, "selftest"))
        self.assertTrue(hasattr(self.app, "run_gui"))

    def test_selftest_round_trip(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self.app.selftest()
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("SELFTEST OK", output)
        self.assertIn("Am", output)
        self.assertIn("melody", output)

    def test_selftest_lists_every_instrument_part(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self.app.selftest()
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        for label in ("808s", "Flute", "Violin", "Pad"):
            self.assertIn(label, output)
        self.assertIn("instrument part", output)

    def test_selftest_rejects_an_unknown_instrument(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self.app.selftest("808s, kazoo")
        self.assertEqual(code, 1)
        self.assertIn("SELFTEST FAILED", buffer.getvalue())

    def test_main_selftest_flag(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self.app.main(["--selftest"])
        self.assertEqual(code, 0)
        self.assertIn("SELFTEST OK", buffer.getvalue())

    def test_main_help_flag(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self.app.main(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("Export as MIDI file", buffer.getvalue())


class TestGuiSmoke(unittest.TestCase):
    """Builds the real Tk window, generates once, and closes it."""

    def test_window_builds_generates_and_draws(self):
        import subprocess

        try:
            import tkinter  # noqa: F401
        except ImportError:
            self.skipTest("tkinter is not available in this Python build")

        app_path = os.path.join(ROOT, "app", "chordlayer_app.py")
        result = subprocess.run(
            [sys.executable, "-B", app_path, "--smoke-gui"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if "Could not open a window" in result.stdout:
            self.skipTest("no display available for Tk")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("GUI SMOKE OK", result.stdout)
        # The smoke run has to have generated (and drawn) real instrument parts.
        import re

        match = re.search(
            r"GUI SMOKE OK: (\d+) instrument parts, (\d+) notes", result.stdout
        )
        self.assertIsNotNone(match, result.stdout)
        # Eight named instruments plus the three pieces of the drum kit.
        self.assertEqual(int(match.group(1)), 11)
        self.assertGreater(int(match.group(2)), 0)


class TestDemoChords(unittest.TestCase):
    def setUp(self):
        self.app = load_app()

    def test_demo_chords_file_is_valid(self):
        from chordlayer.midi_io import read_midi
        from chordlayer.session import Session

        with tempfile.TemporaryDirectory() as tmp:
            path = self.app.build_demo_chords(os.path.join(tmp, "demo.mid"))
            midi = read_midi(path)
            self.assertEqual(len(midi.notes), 12)
            self.assertEqual(midi.ticks_per_beat, 480)

            session = Session()
            session.load_midi(path)
            analysis = session.analyze()
            self.assertEqual(analysis.chord_labels(), ["Am", "F", "C", "G"])
            self.assertTrue(analysis.melody)


if __name__ == "__main__":
    unittest.main()
