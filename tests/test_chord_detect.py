"""Unit tests for chordlayer.chord_detect."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chordlayer.chord_detect import SimpleNote, detect_chords, infer_key_from_chords
from chordlayer.theory import parse_progression

PPQ = 240  # ticks per beat for these tests


def chord_notes(start_beat, pitches, length_beats=4.0):
    return [
        SimpleNote(pitch=pitch, start=int(start_beat * PPQ), length=int(length_beats * PPQ))
        for pitch in pitches
    ]


class TestDetectChords(unittest.TestCase):
    def test_simple_progression(self):
        notes = (
            chord_notes(0, [57, 60, 64])  # A3 C4 E4 -> Am
            + chord_notes(4, [53, 57, 60])  # F3 A3 C4 -> F
            + chord_notes(8, [48, 52, 55])  # C3 E3 G3 -> C
            + chord_notes(12, [55, 59, 62])  # G3 B3 D4 -> G
        )
        result = detect_chords(notes, PPQ)
        labels = [chord.label for chord in result.chords]
        self.assertEqual(labels, ["Am", "F", "C", "G"])
        self.assertEqual([chord.start for chord in result.chords], [0.0, 4.0, 8.0, 12.0])
        self.assertEqual(result.scale_name, "Natural Minor (Aeolian)")  # Am F C G is A minor

    def test_seventh_chords(self):
        notes = chord_notes(0, [50, 53, 57, 60]) + chord_notes(4, [55, 59, 62, 65])  # G7
        result = detect_chords(notes, PPQ)
        labels = [chord.label for chord in result.chords]
        self.assertEqual(labels, ["Dm7", "G7"])

    def test_sus_chord(self):
        result = detect_chords(chord_notes(0, [48, 50, 55]), PPQ)  # C D G -> Csus2
        self.assertEqual(result.chords[0].label, "Csus2")

    def test_inversion_respects_bass(self):
        notes = chord_notes(0, [52, 55, 60])  # E3 G3 C4: C major, E in bass -> C/E
        result = detect_chords(notes, PPQ)
        # Root detection should still be C major; bass note bonus keeps root C.
        self.assertEqual(result.chords[0].label, "C")

    def test_single_note_yields_chord(self):
        result = detect_chords(chord_notes(0, [48]), PPQ)
        self.assertEqual(len(result.chords), 1)

    def test_two_note_dyad(self):
        result = detect_chords(chord_notes(0, [48, 55]), PPQ)  # C-G
        self.assertEqual(len(result.chords), 1)
        self.assertIn(result.chords[0].template, ("5", "maj", "min"))

    def test_rest_gap_splits_chords(self):
        notes = chord_notes(0, [57, 60, 64], 2.0) + chord_notes(6, [53, 57, 60], 2.0)
        result = detect_chords(notes, PPQ)
        self.assertEqual(len(result.chords), 2)
        self.assertEqual(result.chords[1].start, 6.0)

    def test_tiny_slices_dropped(self):
        notes = (
            chord_notes(0, [57, 60, 64], 1.9)
            + [SimpleNote(pitch=71, start=int(1.9 * PPQ), length=int(0.1 * PPQ))]
            + chord_notes(2.0, [53, 57, 60], 2.0)
        )
        result = detect_chords(notes, PPQ, min_length_beats=0.25)
        # The 0.1-beat flash of B must be dropped, leaving Am then F.
        labels = [chord.label for chord in result.chords]
        self.assertEqual(labels, ["Am", "F"])

    def test_empty_notes(self):
        result = detect_chords([], PPQ)
        self.assertEqual(result.chords, [])

    def test_progression_text_overrides_detection(self):
        notes = chord_notes(0, [57, 60, 64], 8.0)
        result = detect_chords(notes, PPQ, progression_text="C G Am F")
        labels = [chord.label for chord in result.chords]
        self.assertEqual(labels, ["C", "G", "Am", "F"])

    def test_progression_text_tiles_to_note_span(self):
        notes = chord_notes(0, [57, 60, 64], 24.0)  # 6 bars
        result = detect_chords(notes, PPQ, progression_text="C G")  # 8 beats/cycle
        self.assertEqual(len(result.chords), 6)

    def test_progression_text_without_notes(self):
        result = detect_chords([], PPQ, progression_text="C Am")
        self.assertEqual(len(result.chords), 4)  # 16 beats / 4-beat chords

    def test_melody_on_top_does_not_break_chords(self):
        notes = chord_notes(0, [48, 52, 55])  # C major
        melody = [
            SimpleNote(pitch=p, start=int(t * PPQ), length=int(0.5 * PPQ))
            for p, t in [(76, 0.0), (79, 0.5), (77, 1.0), (76, 1.5), (72, 2.0), (76, 2.5), (79, 3.0), (76, 3.5)]
        ]
        result = detect_chords(notes + melody, PPQ)
        # Melody in C major over C major chord: should stay one C chord.
        labels = [chord.label for chord in result.chords]
        self.assertTrue(labels, "expected at least one chord")
        self.assertTrue(all(label.startswith("C") for label in labels))

    def test_key_inference_minor_vs_major(self):
        chords = parse_progression("C G Am F")
        tonic, name = infer_key_from_chords(chords)
        self.assertEqual(name, "Major (Ionian)")
        chords = parse_progression("Am F C G")
        tonic, name = infer_key_from_chords(chords)
        self.assertEqual(name, "Natural Minor (Aeolian)")


if __name__ == "__main__":
    unittest.main()
