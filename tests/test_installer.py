"""Tests for install/install.py detection logic (no real FL folders touched)."""

import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)


def load_installer():
    path = os.path.join(ROOT, "install", "install.py")
    spec = importlib.util.spec_from_file_location("chordlayer_installer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_install(root, name, exe=True, portable=True, factory_scripts=False, version=None):
    """Build a synthetic FL Studio install tree under ``root``."""
    path = os.path.join(root, name)
    os.makedirs(path, exist_ok=True)
    if exe:
        with open(os.path.join(path, "FL64.exe"), "wb") as handle:
            handle.write(b"MZ fake")  # enough for the file-existence checks
    if portable:
        os.makedirs(os.path.join(path, "User_data"), exist_ok=True)
    if factory_scripts:
        os.makedirs(
            os.path.join(path, "System", "Config", "Piano roll scripts"), exist_ok=True
        )
    return path


class TestVersionGate(unittest.TestCase):
    def setUp(self):
        self.installer = load_installer()

    def test_piano_roll_requires_21_1(self):
        supports = self.installer.version_supports_piano_roll
        self.assertFalse(supports((20, 8, 4)))  # the user's FL Studio 20.8.4
        self.assertFalse(supports((21, 0, 5)))
        self.assertTrue(supports((21, 1, 0)))
        self.assertTrue(supports((21, 2, 3)))
        self.assertTrue(supports((24, 0, 0)))
        self.assertTrue(supports((26, 0, 1)))

    def test_unknown_version_does_not_block(self):
        self.assertTrue(self.installer.version_supports_piano_roll(None))

    def test_minimum_version_constant(self):
        self.assertEqual(self.installer.MIN_PIANO_ROLL_VERSION, (21, 1, 0))


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.installer = load_installer()

    def test_find_installations_ignores_non_fl_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_install(tmp, "Fl Studio", factory_scripts=False)
            make_install(tmp, "FL Studio 26", factory_scripts=True)
            os.makedirs(os.path.join(tmp, "Downloads"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "Fl Studio 26 partial"), exist_ok=True)  # no exe
            found = sorted(os.path.basename(p) for p in self.installer.find_fl_installations([tmp]))
            self.assertEqual(found, ["FL Studio 26", "Fl Studio"])

    def test_factory_scripts_feature_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = make_install(tmp, "FL Studio 26", factory_scripts=True)
            old = make_install(tmp, "Fl Studio", factory_scripts=False)
            self.assertTrue(self.installer.supports_piano_roll_scripts(new))
            self.assertFalse(self.installer.supports_piano_roll_scripts(old))

    def test_describe_install_reports_portable_and_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = make_install(tmp, "Fl Studio", portable=True, factory_scripts=False)
            described = self.installer.describe_install(old)
            self.assertTrue(described["portable"])
            self.assertFalse(described["factory_scripts"])
            # No readable version info in a fake exe -> unsupported because the
            # 21.1+ factory folder is absent.
            self.assertFalse(described["piano_roll"])

            new = make_install(tmp, "FL Studio 26", portable=True, factory_scripts=True)
            described_new = self.installer.describe_install(new)
            self.assertTrue(described_new["piano_roll"])


class TestDestinations(unittest.TestCase):
    def setUp(self):
        self.installer = load_installer()

    def test_portable_install_data_folder_comes_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_path = make_install(tmp, "Fl Studio", factory_scripts=False)
            docs = os.path.join(tmp, "Documents")
            installs = [self.installer.describe_install(install_path)]
            candidates = self.installer.destination_candidates(installs, docs=docs)
            self.assertEqual(
                candidates[0]["path"],
                os.path.join(install_path, "User_data", "Settings", "Piano roll scripts"),
            )
            self.assertEqual(candidates[0]["kind"], "portable user data")
            self.assertIn("does not support piano roll scripting", candidates[0]["warn"])
            self.assertTrue(
                candidates[1]["path"].startswith(os.path.join(docs, "Image-Line"))
            )

    def test_factory_folder_offered_only_when_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = make_install(tmp, "Fl Studio", factory_scripts=False)
            new = make_install(tmp, "FL Studio 26", factory_scripts=True)
            docs = os.path.join(tmp, "Documents")
            installs = [
                self.installer.describe_install(old),
                self.installer.describe_install(new),
            ]
            kinds = [
                c["kind"] for c in self.installer.destination_candidates(installs, docs=docs)
            ]
            self.assertEqual(kinds.count("factory scripts folder"), 1)
            self.assertEqual(kinds[0], "portable user data")

    def test_no_installs_still_offers_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = os.path.join(tmp, "Documents")
            candidates = self.installer.destination_candidates([], docs=docs)
            self.assertEqual(len(candidates), 1)
            self.assertIn("Piano roll scripts", candidates[0]["path"])


class TestCopy(unittest.TestCase):
    def setUp(self):
        self.installer = load_installer()

    def test_install_copies_scripts_and_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = self.installer.install(tmp)
            target = os.path.join(tmp, "ChordLayer")
            self.assertTrue(os.path.isfile(os.path.join(target, "ChordLayer.pyscript")))
            self.assertTrue(
                os.path.isfile(os.path.join(target, "ChordLayer Analyzer.pyscript"))
            )
            self.assertTrue(os.path.isfile(os.path.join(target, "chordlayer", "generator.py")))
            self.assertFalse(
                any("__pycache__" in path for path in copied),
                "engine copy must not drag bytecode caches along",
            )

    def test_install_refuses_existing_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.installer.install(tmp)
            with self.assertRaises(SystemExit):
                self.installer.install(tmp)
            self.installer.install(tmp, force=True)  # --force overwrites


if __name__ == "__main__":
    unittest.main()
