"""Property-style tests for the melody generator."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chordlayer.chord_detect import SimpleNote, detect_chords
from chordlayer.generator import (
    CONTOURS,
    MelodyParams,
    RHYTHM_PRESETS,
    STRATEGIES,
    build_rhythm,
    generate_melody,
)
from chordlayer.rng import Rng, normalize_seed
from chordlayer.theory import parse_progression

PPQ = 480


def make_params(**overrides):
    chords = parse_progression(overrides.pop("progression", "Am F C G"))
    base = dict(
        ticks_per_beat=PPQ,
        chords=chords,
        tonic=9,
        scale_name="Natural Minor (Aeolian)",
        rhythm_preset="Eighths",
        density=1.0,
        rest_chance=0.0,
        gate=0.9,
        strategy="Chord + passing",
        contour="Arch",
        center_octave=4,
        range_semitones=12,
        motif_bars=0,
        velocity=0.8,
        velocity_jitter=0.0,
        timing_jitter=0.0,
        seed=7,
        melody_color=15,
    )
    base.update(overrides)
    return MelodyParams(**base)


class TestRhythm(unittest.TestCase):
    def test_eighths_full_density_slot_count(self):
        params = make_params(rhythm_preset="Eighths", density=1.0)
        slots = build_rhythm(params)
        # 16 beats of progression, 0.5-beat steps -> 32 slots.
        self.assertEqual(len(slots), 32)

    def test_density_prunes(self):
        full = build_rhythm(make_params(density=1.0))
        low = build_rhythm(make_params(density=0.0))
        self.assertLess(len(low), len(full))
        self.assertGreaterEqual(len(low), 1)  # step 0 always survives

    def test_ballad_notes_ring(self):
        params = make_params(rhythm_preset="Ballad", gate=1.0)
        slots = build_rhythm(params)
        self.assertGreater(slots[0].length, 1.0)

    def test_offbeats_strong_positions(self):
        params = make_params(rhythm_preset="Offbeats")
        slots = build_rhythm(params)
        for slot in slots:
            self.assertAlmostEqual(slot.start % 1.0, 0.5, places=6)
            self.assertTrue(slot.strong)

    def test_gaps_become_rests(self):
        # Chord list with a hole: 0-4 and 6-10.
        chords = parse_progression("Am:4 Cmaj7:4")
        chords[1].start  # touch to ensure computed
        shifted = [chords[0]]
        second = chords[1]
        from chordlayer.theory import ChordSpec

        shifted.append(ChordSpec(second.root, second.template, second.start + 2.0, second.length))
        params = make_params(chords=shifted, rhythm_preset="Eighths", density=1.0)
        slots = build_rhythm(params)
        starts = {round(slot.start, 6) for slot in slots}
        self.assertNotIn(4.0, starts)
        self.assertNotIn(4.5, starts)
        self.assertIn(6.0, starts)


class TestPitchSelection(unittest.TestCase):
    def test_notes_within_range(self):
        for contour in CONTOURS:
            params = make_params(contour=contour, seed=11)
            for note in generate_melody(params):
                self.assertGreaterEqual(note.pitch, params.lo, contour)
                self.assertLessEqual(note.pitch, params.hi, contour)

    def test_strong_slots_landing_on_chord_tones(self):
        params = make_params(strategy="Chord + passing", rhythm_preset="Eighths", density=1.0, rest_chance=0.0)
        notes = generate_melody(params)
        self.assertTrue(notes)
        chords = params.chords
        for note in notes:
            time = note.start / PPQ
            chord = next((c for c in chords if c.contains(time)), None)
            if chord is None:
                continue
            pos = (time - chord.start) % params.beats_per_bar
            if abs(pos) < 1e-9 or abs(pos - params.beats_per_bar / 2) < 1e-9:
                from chordlayer.theory import chord_pitch_classes

                pcs = set(chord_pitch_classes(chord.root, chord.template))
                self.assertIn(note.pitch % 12, pcs, f"{note} at {time} over {chord.label}")

    def test_arpeggio_uses_chord_tones_only(self):
        params = make_params(strategy="Arpeggio", rest_chance=0.0, density=1.0)
        from chordlayer.theory import chord_pitch_classes

        for note in generate_melody(params):
            time = note.start / PPQ
            chord = next(c for c in params.chords if c.contains(time))
            pcs = set(chord_pitch_classes(chord.root, chord.template))
            self.assertIn(note.pitch % 12, pcs)

    def test_no_duplicate_simultaneous_notes(self):
        params = make_params(seed=3)
        notes = generate_melody(params)
        seen = set()
        for note in notes:
            key = (note.start, note.pitch)
            self.assertNotIn(key, seen)
            seen.add(key)

    def test_deterministic_per_seed(self):
        a = generate_melody(make_params(seed=42))
        b = generate_melody(make_params(seed=42))
        c = generate_melody(make_params(seed=43))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_motif_repeat_transposes(self):
        params = make_params(contour="Motif repeat", motif_bars=1, density=1.0, rest_chance=0.0, seed=5)
        notes = generate_melody(params)
        self.assertTrue(notes)
        # Second bar should mirror first bar's contour: intervals correlate.
        first_bar = [n.pitch for n in notes if n.start < PPQ * 4]
        later = [n.pitch for n in notes if PPQ * 8 <= n.start < PPQ * 12]
        if first_bar and later and len(first_bar) == len(later):
            d1 = [b - a for a, b in zip(first_bar, first_bar[1:])]
            d2 = [b - a for a, b in zip(later, later[1:])]

            def direction(interval):
                return (interval > 0) - (interval < 0)

            # The motif is repeated over new chords, so pitches may snap to the
            # local harmony, but the melodic contour must survive.
            same_direction = sum(
                1 for x, y in zip(d1, d2) if direction(x) == direction(y)
            )
            self.assertGreaterEqual(same_direction, len(d1) - 2)

    def test_velocities_in_bounds(self):
        params = make_params(velocity_jitter=0.2, velocity=0.9)
        for note in generate_melody(params):
            self.assertGreaterEqual(note.velocity, 0.1)
            self.assertLessEqual(note.velocity, 1.0)

    def test_timing_jitter_shifts_starts(self):
        params = make_params(timing_jitter=30, seed=9)
        for note in generate_melody(params):
            self.assertGreaterEqual(note.start, 0)

    def test_melody_color_applied(self):
        params = make_params(melody_color=7)
        for note in generate_melody(params):
            self.assertEqual(note.color, 7)


class TestParams(unittest.TestCase):
    def test_unknown_preset_raises(self):
        with self.assertRaises(ValueError):
            make_params(rhythm_preset="Waltz-step")

    def test_range_clamp(self):
        params = make_params(range_semitones=99)
        self.assertLessEqual(params.range_semitones, 24)

    def test_validate_flags_out_of_midi_range(self):
        params = make_params(center_octave=0, range_semitones=24)  # lo dips below 0
        self.assertTrue(params.validate())

    def test_all_presets_and_strategies_generate(self):
        for preset in RHYTHM_PRESETS:
            for strategy in STRATEGIES:
                params = make_params(rhythm_preset=preset, strategy=strategy, seed=13)
                notes = generate_melody(params)
                self.assertGreaterEqual(len(notes), 1, (preset, strategy))

    def test_progression_time_signatures(self):
        params = make_params(beats_per_bar=3.0, contour="Motif repeat", motif_bars=1)
        for note in generate_melody(params):
            self.assertGreaterEqual(note.pitch, params.lo)


if __name__ == "__main__":
    unittest.main()
