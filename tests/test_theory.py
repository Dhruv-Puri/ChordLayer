"""Unit tests for chordlayer.theory."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chordlayer.theory import (
    CHORD_TEMPLATES,
    ChordSpec,
    chord_pitch_classes,
    chord_tones_in_range,
    format_chord,
    infer_key,
    is_in_scale,
    match_chord,
    nearest_note,
    note_name,
    parse_chord_name,
    parse_progression,
    scale_mask,
    scale_notes_in_range,
)


class TestNoteNames(unittest.TestCase):
    def test_note_name_fl_convention(self):
        # FL Studio's piano roll shows 48 as C4 and middle C (60) as C5.
        self.assertEqual(note_name(48), "C4")
        self.assertEqual(note_name(60), "C5")
        self.assertEqual(note_name(61), "C#5")
        self.assertEqual(note_name(70), "A#5")

    def test_pitch_class_from_name(self):
        cases = {"C": 0, "C#": 1, "Db": 1, "F##": 7, "Bb": 10, "G": 7}
        for name, expected in cases.items():
            self.assertEqual(
                __import__("chordlayer.theory", fromlist=["x"]).pitch_class_from_name(name),
                expected,
                name,
            )


class TestScales(unittest.TestCase):
    def test_major_mask_shape(self):
        mask = scale_mask("Major (Ionian)")
        self.assertEqual(len(mask), 12)
        self.assertEqual(sum(mask), 7)

    def test_unknown_scale_raises(self):
        with self.assertRaises(ValueError):
            scale_mask("Blues - Unknown")

    def test_scale_pitch_classes(self):
        from chordlayer.theory import scale_pitch_classes

        self.assertEqual(scale_pitch_classes(0, scale_mask("Major (Ionian)")), (0, 2, 4, 5, 7, 9, 11))
        self.assertEqual(scale_pitch_classes(9, scale_mask("Natural Minor (Aeolian)")), (9, 11, 0, 2, 4, 5, 7))

    def test_is_in_scale(self):
        major = scale_mask("Major (Ionian)")
        self.assertTrue(is_in_scale(4, 0, major))  # E over C major
        self.assertFalse(is_in_scale(1, 0, major))  # Db over C major

    def test_scale_notes_in_range(self):
        notes = scale_notes_in_range(0, scale_mask("Major (Ionian)"), 60, 72)
        self.assertEqual(notes[0], 60)
        self.assertEqual(notes[-1], 72)
        self.assertEqual(len(notes), 8)  # two octaves of C major share the boundary

    def test_infer_key_prefers_c_major(self):
        weights = {0: 4.0, 4: 2.0, 7: 2.0, 2: 1.0}
        tonic, name = infer_key(weights)
        self.assertEqual(tonic, 0)
        self.assertEqual(name, "Major (Ionian)")

    def test_infer_key_prefers_a_minor(self):
        weights = {9: 4.0, 0: 2.0, 4: 2.0, 7: 1.0, 2: 1.0}
        tonic, name = infer_key(weights)
        self.assertEqual((tonic, name), (9, "Natural Minor (Aeolian)"))


class TestChordParsing(unittest.TestCase):
    def test_basic_names(self):
        self.assertEqual(parse_chord_name("C"), (0, "maj"))
        self.assertEqual(parse_chord_name("Am"), (9, "min"))
        self.assertEqual(parse_chord_name("Bb"), (10, "maj"))
        self.assertEqual(parse_chord_name("F#"), (6, "maj"))
        self.assertEqual(parse_chord_name("G7"), (7, "7"))
        self.assertEqual(parse_chord_name("Cmaj7"), (0, "maj7"))
        self.assertEqual(parse_chord_name("Dm7"), (2, "min7"))
        self.assertEqual(parse_chord_name("Bdim"), (11, "dim"))
        self.assertEqual(parse_chord_name("Fsus4"), (5, "sus4"))
        self.assertEqual(parse_chord_name("Csus2"), (0, "sus2"))
        self.assertEqual(parse_chord_name("Dm7b5"), (2, "min7b5"))
        self.assertEqual(parse_chord_name("Bdim7"), (11, "dim7"))
        self.assertEqual(parse_chord_name("C6"), (0, "maj6"))
        self.assertEqual(parse_chord_name("Cm6"), (0, "min6"))
        self.assertEqual(parse_chord_name("Cadd9"), (0, "add9"))
        self.assertEqual(parse_chord_name("C9"), (0, "9"))
        self.assertEqual(parse_chord_name("C5"), (0, "5"))
        self.assertEqual(parse_chord_name("Eaug"), (4, "aug"))
        self.assertEqual(parse_chord_name("C7sus4"), (0, "7sus4"))
        self.assertEqual(parse_chord_name("Cmaj9"), (0, "maj9"))
        self.assertEqual(parse_chord_name("Cm9"), (0, "min9"))

    def test_case_insensitive_min(self):
        self.assertEqual(parse_chord_name("amin"), (9, "min"))
        self.assertEqual(parse_chord_name("AMIN"), (9, "min"))

    def test_unknown_quality_raises(self):
        with self.assertRaises(ValueError):
            parse_chord_name("Hsus")

    def test_parse_progression_defaults(self):
        chords = parse_progression("Am F C G")
        self.assertEqual([c.label for c in chords], ["Am", "F", "C", "G"])
        self.assertEqual([c.start for c in chords], [0.0, 4.0, 8.0, 12.0])
        self.assertEqual([c.length for c in chords], [4.0, 4.0, 4.0, 4.0])

    def test_parse_progression_lengths_and_commas(self):
        chords = parse_progression("Dm7:4, Gm:2, A7:2")
        self.assertEqual([c.label for c in chords], ["Dm7", "Gm", "A7"])
        self.assertEqual([c.length for c in chords], [4.0, 2.0, 2.0])
        self.assertEqual([c.start for c in chords], [0.0, 4.0, 6.0])

    def test_parse_progression_empty_is_error(self):
        with self.assertRaises(ValueError):
            parse_progression("   ")


class TestChordMatching(unittest.TestCase):
    def test_exact_triad(self):
        result = match_chord({0, 4, 7}, 0)
        self.assertIsNotNone(result)
        root, template, score = result
        self.assertEqual((root, template), (0, "maj"))

    def test_inversion_with_bass_bonus(self):
        # E-G-C (C major, first inversion): root must come from the bass.
        result = match_chord({4, 7, 0}, 4)
        root, template, _ = result
        self.assertEqual((root, template), (0, "maj"))

    def test_seventh(self):
        root, template, _ = match_chord({2, 5, 9, 0}, 2)  # D F A C -> Dm7
        self.assertEqual((root, template), (2, "min7"))

    def test_power_chord_ambiguous_is_power(self):
        root, template, _ = match_chord({7, 0}, 0)
        self.assertEqual(template, "5")

    def test_single_note_matches_something(self):
        result = match_chord({4})
        self.assertIsNotNone(result)

    def test_empty_is_none(self):
        self.assertIsNone(match_chord(set()))

    def test_dissonant_cluster_is_none(self):
        # 0,1,2,3,4 chromatic cluster: nothing plausible should match.
        self.assertIsNone(match_chord({0, 1, 2, 3, 4}))


class TestHelpers(unittest.TestCase):
    def test_chord_pitch_classes(self):
        self.assertEqual(chord_pitch_classes(9, "min"), (0, 4, 9))
        self.assertEqual(chord_pitch_classes(0, "maj7"), (0, 4, 7, 11))

    def test_chord_tones_in_range(self):
        tones = chord_tones_in_range(0, "maj", 60, 72)
        self.assertIn(60, tones)
        self.assertIn(64, tones)
        self.assertIn(67, tones)
        self.assertIn(72, tones)
        self.assertNotIn(61, tones)

    def test_nearest_note(self):
        self.assertEqual(nearest_note(62, [60, 64, 67]), 60)
        self.assertEqual(nearest_note(63, [60, 64, 67]), 64)
        self.assertEqual(nearest_note(66, [64, 67]), 67)  # tie -> lower wins? 64 is 2 away, 67 is 1 away

    def test_format_chord(self):
        self.assertEqual(format_chord(9, "min"), "Am")
        self.assertEqual(format_chord(6, "min7"), "F#m7")

    def test_chord_spec_contains(self):
        chord = ChordSpec(root=9, template="min", start=0.0, length=2.0)
        self.assertTrue(chord.contains(0.0))
        self.assertTrue(chord.contains(1.999))
        self.assertFalse(chord.contains(2.0))

    def test_template_table_consistent(self):
        for template, intervals in CHORD_TEMPLATES.items():
            self.assertIn(0, intervals, template)  # root always present
            self.assertTrue(all(0 <= i < 12 for i in intervals), template)


if __name__ == "__main__":
    unittest.main()
