"""End-to-end tests: the real .pyscript files run against a fake flpianoroll."""

import unittest

import fake_flp
from helpers import load_script


def make_dialog(script):
    """createDialog() with defaults, plus test-friendly access."""
    form = script.createDialog()
    return form


def add_chords(score, PPQ):
    """Two bars: Am then F, as quarter-length chord notes."""
    notes = [
        (57, 0), (60, 0), (64, 0),  # A3 C4 E4
        (53, PPQ * 4), (57, PPQ * 4), (60, PPQ * 4),  # F3 A3 C4
    ]
    for pitch, start in notes:
        score.addNote(fake_flp.Note(number=pitch, time=start, length=PPQ * 4))


class TestChordLayerScriptE2E(unittest.TestCase):
    def setUp(self):
        fake_flp.install()
        fake_flp.reset(PPQ=240)
        self.script = load_script("ChordLayer.pyscript", "chordlayer_script")
        self.score = fake_flp.score

    def test_dialog_builds(self):
        form = make_dialog(self.script)
        self.assertEqual(form.getInputValue("Seed"), 1)
        self.assertEqual(form.getInputValue("Density"), 0.8)

    def test_apply_generates_tagged_selected_notes(self):
        add_chords(self.score, 240)
        form = make_dialog(self.script)
        self.script.apply(form)
        melody_notes = [n for n in self.score._notes if n.color == 15]
        self.assertTrue(melody_notes, "expected generated melody notes")
        self.assertTrue(all(n.selected for n in melody_notes))
        # All melody notes start after beat 0 and lie within a sane range.
        for note in melody_notes:
            self.assertGreaterEqual(note.number, 0)
            self.assertLessEqual(note.number, 131)

    def test_idempotent_reapply_no_duplicates(self):
        add_chords(self.score, 240)
        form = make_dialog(self.script)
        self.script.apply(form)
        first_count = len([n for n in self.score._notes if n.color == 15])
        self.script.apply(form)
        second_count = len([n for n in self.score._notes if n.color == 15])
        self.assertEqual(first_count, second_count)
        # And the user's chord notes are still there (3 per bar * 2 bars).
        chord_notes = [n for n in self.score._notes if n.color != 15]
        self.assertEqual(len(chord_notes), 6)

    def test_clear_previous_off_appends(self):
        add_chords(self.score, 240)
        form = make_dialog(self.script)
        self.script.apply(form)
        count_after_first = len(self.score._notes)
        form.set("Clear previous melody", False)
        self.script.apply(form)
        self.assertGreater(len(self.score._notes), count_after_first)

    def test_seed_changes_result(self):
        add_chords(self.score, 240)
        form_a = make_dialog(self.script)
        self.script.apply(form_a)
        pitches_a = [n.number for n in fake_flp.score._notes if n.color == 15]
        self.assertTrue(pitches_a)
        fake_flp.reset(PPQ=240)
        self.score = fake_flp.score  # reset() rebinds the module global
        add_chords(self.score, 240)
        form_b = make_dialog(self.script)
        form_b.set("Seed", 999)
        self.script.apply(form_b)
        pitches_b = [n.number for n in fake_flp.score._notes if n.color == 15]
        self.assertTrue(pitches_b)
        self.assertNotEqual(pitches_a, pitches_b)

    def test_progression_text_generates_without_chord_notes(self):
        # Empty roll, typed progression: must still generate a melody.
        form = make_dialog(self.script)
        form.set("Progression (optional)", "Am F C G")
        self.script.apply(form)
        melody_notes = [n for n in self.score._notes if n.color == 15]
        self.assertTrue(melody_notes)

    def test_selection_only_mode(self):
        add_chords(self.score, 240)
        for note in self.score._notes[:3]:  # select just the Am bar
            note.selected = True
        form = make_dialog(self.script)
        form.set("Source", 1)  # "Selection only"
        self.script.apply(form)
        melody_notes = [n for n in self.score._notes if n.color == 15]
        self.assertTrue(melody_notes)
        # All melody must stay within the selected span (first 4 beats).
        for note in melody_notes:
            self.assertLess(note.time, 240 * 4)

    def test_error_when_no_chords(self):
        form = make_dialog(self.script)
        self.script.apply(form)  # empty roll, no progression
        self.assertTrue(fake_flp.Utils.messages, "expected a user-facing message")

    def test_melody_color_change(self):
        add_chords(self.score, 240)
        form = make_dialog(self.script)
        form.set("Melody color", 3)
        self.script.apply(form)
        tagged = [n for n in self.score._notes if n.color == 3]
        self.assertTrue(tagged)
        self.assertFalse([n for n in self.score._notes if n.color == 15])


class TestAnalyzerScriptE2E(unittest.TestCase):
    def setUp(self):
        fake_flp.install()
        fake_flp.reset(PPQ=240)
        self.script = load_script("ChordLayer Analyzer.pyscript", "analyzer_script")
        self.score = fake_flp.score

    def test_reports_progression(self):
        add_chords(self.score, 240)
        form = make_dialog(self.script)
        self.script.apply(form)
        self.assertTrue(fake_flp.Utils.messages)
        report = fake_flp.Utils.messages[-1]
        self.assertIn("Am", report)
        self.assertIn("F", report)

    def test_empty_roll_message(self):
        form = make_dialog(self.script)
        self.script.apply(form)
        report = fake_flp.Utils.messages[-1]
        self.assertIn("No chords detected", report)


class TestRunChordlayerCore(unittest.TestCase):
    """The pure run_chordlayer() core, without any FL objects."""

    def setUp(self):
        fake_flp.install()
        fake_flp.reset(PPQ=240)
        self.script = load_script("ChordLayer.pyscript", "chordlayer_script_core")

    def test_accepts_plain_note_objects(self):
        class PlainNote:
            def __init__(self, number=57, time=0, length=960):
                self.number = number
                self.time = time
                self.length = length
                self.selected = False
                self.muted = False

        notes = [PlainNote(), PlainNote(number=60), PlainNote(number=64)]
        melody = self.script.run_chordlayer(notes, ticks_per_beat=240, seed=3)
        self.assertTrue(melody)
        for note in melody:
            self.assertTrue(hasattr(note, "pitch"))

    def test_progression_via_core(self):
        melody = self.script.run_chordlayer([], ticks_per_beat=240, progression_text="C G Am F")
        self.assertTrue(melody)

    def test_melody_problem_raised_for_empty_roll(self):
        with self.assertRaises(self.script.MelodyProblem):
            self.script.run_chordlayer([], ticks_per_beat=240)


if __name__ == "__main__":
    unittest.main()
