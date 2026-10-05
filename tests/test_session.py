"""Tests for chordlayer.session (the FL Studio 20 companion core)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from chordlayer.midi_io import MidiNote, read_midi, write_midi  # noqa: E402
from chordlayer.session import (  # noqa: E402
    Options,
    Session,
    SessionError,
    describe_chords,
    key_from_chords,
)

PPQ = 480
BAR = PPQ * 4


def write_chords(path, progression=None):
    """Write a chords MIDI file the way FL Studio would export one."""
    progression = progression or [
        ("Am", [57, 60, 64]),
        ("F", [53, 57, 60]),
        ("C", [48, 52, 55]),
        ("G", [55, 59, 62]),
    ]
    notes = []
    for index, (_label, pitches) in enumerate(progression):
        for pitch in pitches:
            notes.append(
                MidiNote(pitch=pitch, start=index * BAR, length=BAR, velocity=0.75)
            )
    write_midi(path, notes, ticks_per_beat=PPQ, tempo_bpm=128, track_name="Chords")
    return path


class TestSessionInput(unittest.TestCase):
    def test_analyze_without_input_is_a_friendly_error(self):
        session = Session()
        with self.assertRaises(SessionError) as caught:
            session.analyze()
        self.assertIn("MIDI", str(caught.exception))

    def test_load_reports_details_in_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_chords(os.path.join(tmp, "chords.mid"))
            session = Session()
            midi = session.load_midi(path)
            self.assertEqual(midi.ticks_per_beat, PPQ)
            self.assertEqual(len(midi.notes), 12)
            self.assertTrue(any("Loaded" in line for line in session.log))

    def test_missing_file_is_a_friendly_error(self):
        session = Session()
        with self.assertRaises(SessionError):
            session.load_midi(os.path.join(tempfile.gettempdir(), "does-not-exist.mid"))

    def test_empty_midi_explains_the_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "empty.mid")
            write_midi(path, [], ticks_per_beat=PPQ)
            session = Session()
            session.load_midi(path)
            with self.assertRaises(SessionError) as caught:
                session.analyze()
            self.assertIn("No chords detected", str(caught.exception))


class TestAnalysis(unittest.TestCase):
    def test_detects_chords_and_generates_melody(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session()
            session.load_midi(write_chords(os.path.join(tmp, "chords.mid")))
            analysis = session.analyze()
            self.assertEqual(analysis.chord_labels(), ["Am", "F", "C", "G"])
            self.assertTrue(analysis.melody, "expected a generated melody")
            self.assertGreater(len(analysis.summary_lines()), 0)

    def test_melody_stays_inside_the_configured_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session()
            session.load_midi(write_chords(os.path.join(tmp, "chords.mid")))
            session.options = session.options.copy_with(
                center_octave=5, range_semitones=7, seed=5
            )
            analysis = session.analyze()
            for note in analysis.melody:
                self.assertGreaterEqual(note.pitch, 60 - 7)
                self.assertLessEqual(note.pitch, 60 + 7)

    def test_melody_never_leaves_the_chord_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session()
            session.load_midi(write_chords(os.path.join(tmp, "chords.mid")))
            analysis = session.analyze()
            last_tick = max(chord.end for chord in analysis.chords) * analysis.ticks_per_beat
            for note in analysis.melody:
                self.assertGreaterEqual(note.start, 0)
                self.assertLessEqual(note.start, last_tick)

    def test_seed_is_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_chords(os.path.join(tmp, "chords.mid"))
            first = Session()
            first.load_midi(path)
            first.options = first.options.copy_with(seed=11)
            melody_a = [(n.pitch, n.start) for n in first.analyze().melody]

            second = Session()
            second.load_midi(path)
            second.options = second.options.copy_with(seed=11)
            melody_b = [(n.pitch, n.start) for n in second.analyze().melody]
            self.assertEqual(melody_a, melody_b)

    def test_reroll_changes_the_melody(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session()
            session.load_midi(write_chords(os.path.join(tmp, "chords.mid")))
            before = [(n.pitch, n.start) for n in session.analyze().melody]
            new_seed = session.reroll()
            after = [(n.pitch, n.start) for n in session.analyze().melody]
            self.assertNotEqual(before, after)
            self.assertEqual(session.options.seed, new_seed)

    def test_typed_progression_without_midi(self):
        session = Session()
        session.set_progression("Am F C G")
        analysis = session.analyze()
        self.assertEqual(analysis.chord_labels(), ["Am", "F", "C", "G"])
        self.assertEqual(analysis.detected_source, "typed progression")
        self.assertTrue(analysis.melody)

    def test_bad_progression_reports_the_token(self):
        session = Session()
        session.set_progression("Am Hm7 C")
        with self.assertRaises(SessionError) as caught:
            session.analyze()
        self.assertIn("Hm7", str(caught.exception))

    def test_key_and_scale_override(self):
        session = Session()
        session.set_progression("Am F C G")
        session.options = session.options.copy_with(key_index=1, scale_index=2)  # C, minor
        analysis = session.analyze()
        self.assertIn("C", analysis.key_label)

    def test_track_filter_uses_one_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "two-tracks.mid")
            chords = [
                MidiNote(pitch=p, start=i * BAR, length=BAR)
                for i, pitches in enumerate([[57, 60, 64], [53, 57, 60]])
                for p in pitches
            ]
            melody = [
                MidiNote(pitch=p, start=i * (BAR // 2), length=BAR // 4)
                for i, p in enumerate([76, 79, 77, 76])
            ]
            write_midi(path, chords, ticks_per_beat=PPQ, extra_tracks=[("Melody", melody)])

            session = Session()
            session.load_midi(path)
            self.assertEqual(len(session.track_choices()), 3)  # All + 2 tracks
            analysis = session.analyze()
            self.assertTrue(analysis.chords)

            session.options = session.options.copy_with(track_index=0)
            filtered = session.analyze()
            self.assertEqual(filtered.chord_labels(), ["Am", "F"])
            self.assertTrue(filtered.melody)

            # With every track merged, the extra melody line joins the slices
            # but the harmony is still readable.
            session.options = session.options.copy_with(track_index=-1)
            merged = session.analyze()
            self.assertTrue(merged.melody)
            self.assertTrue(merged.chord_labels()[0].startswith("A"))


class TestOutput(unittest.TestCase):
    def test_save_melody_writes_importable_midi(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session()
            session.load_midi(write_chords(os.path.join(tmp, "chords.mid")))
            analysis = session.analyze()
            out = os.path.join(tmp, "melody.mid")
            session.save_melody(out, include_chords=True)

            reloaded = read_midi(out)
            self.assertEqual(reloaded.ticks_per_beat, PPQ)
            self.assertEqual(reloaded.tempo_bpm, 128)
            melody_notes = [note for note in reloaded.notes if note.track == 0]
            self.assertEqual(len(melody_notes), len(analysis.melody))
            self.assertEqual(
                sorted(note.start for note in melody_notes),
                sorted(note.start for note in analysis.melody),
            )
            # The chord part rides along on its own track.
            self.assertIn("Chords", reloaded.track_names)
            chord_notes = [note for note in reloaded.notes if note.track != 0]
            self.assertTrue(chord_notes)
            # Chord blocks span whole bars and stay behind the melody register.
            self.assertTrue(all(note.pitch < 60 for note in chord_notes))
            self.assertTrue(any(note.length >= PPQ * 4 for note in chord_notes))

    def test_save_without_chords_is_single_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session()
            session.load_midi(write_chords(os.path.join(tmp, "chords.mid")))
            session.analyze()
            out = os.path.join(tmp, "melody-only.mid")
            session.save_melody(out, include_chords=False)
            reloaded = read_midi(out)
            self.assertEqual(len(reloaded.track_names), 1)
            self.assertNotIn("Chords", reloaded.track_names)

    def test_save_before_generating_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session()
            session.load_midi(write_chords(os.path.join(tmp, "chords.mid")))
            with self.assertRaises(SessionError):
                session.save_melody(os.path.join(tmp, "nope.mid"))

    def test_progression_only_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session()
            session.set_progression("Cmaj7 Am7 Dm7 G7")
            session.analyze()
            out = os.path.join(tmp, "from-text.mid")
            session.save_melody(out, include_chords=True)
            reloaded = read_midi(out)
            self.assertTrue(reloaded.notes)
            # Typed progressions export their chords too, so the part is complete.
            self.assertIn("Chords", reloaded.track_names)


class TestSongParts(unittest.TestCase):
    """The instrument-driven path: name instruments in, parts out."""

    def make_session(self, instruments="808s, flute, violin, pad"):
        session = Session()
        session.set_progression("Am F C G")
        session.set_instruments(instruments)
        return session

    def test_analyze_song_writes_one_part_per_instrument(self):
        session = self.make_session()
        analysis = session.analyze_song()
        self.assertEqual(analysis.part_labels(), ["808s", "Flute", "Violin", "Pad"])
        self.assertTrue(all(part.notes for part in analysis.parts))
        # The lead instrument is what the preview outlines.
        self.assertEqual(analysis.melody_part, "Flute")
        self.assertEqual(
            [(n.pitch, n.start) for n in analysis.melody],
            [(n.pitch, n.start) for n in analysis.parts[1].notes],
        )
        summary = " ".join(analysis.summary_lines())
        self.assertIn("Parts: 808s + Flute + Violin + Pad", summary)
        self.assertIn("808s [bass]", summary)

    def test_parts_ignore_the_manual_melody_knobs(self):
        session = self.make_session()
        session.options = session.options.copy_with(
            density=0.0, rest_chance=0.9, rhythm_index=5, center_octave=8, range_semitones=1
        )
        analysis = session.analyze_song()
        for part in analysis.parts:
            self.assertTrue(part.notes, part.label)
            for note in part.notes:
                self.assertGreaterEqual(note.pitch, part.lo)
                self.assertLessEqual(note.pitch, part.hi)

    def test_instrument_list_can_change_between_songs(self):
        session = self.make_session()
        session.analyze_song()
        session.set_instruments("piano")
        self.assertEqual(session.analyze_song().part_labels(), ["Piano"])

    def test_unknown_instrument_is_a_friendly_error(self):
        session = Session()
        session.set_progression("Am F C G")
        with self.assertRaises(SessionError) as caught:
            session.set_instruments("808s, kazoo")
        message = str(caught.exception)
        self.assertIn("kazoo", message)
        # The previous list survives so the UI is not left holding junk.
        self.assertTrue(session.instruments())

    def test_empty_instrument_list_is_an_error(self):
        session = self.make_session()
        with self.assertRaises(SessionError):
            session.set_instruments("   ")

    def test_default_instruments_write_a_song(self):
        session = Session()
        session.set_progression("Am F C G")
        analysis = session.analyze_song()
        # The default line-up is a low end, a pad, a melody and the kit.
        self.assertEqual(analysis.part_labels(), ["808s", "Pad", "Flute", "Kick", "Snare", "Hi-hats"])
        self.assertEqual(session.instruments(), ("808s", "pad", "flute", "kick", "snare", "hats"))

    def test_song_is_repeatable_and_reroll_rewrites_it(self):
        first = self.make_session().analyze_song()
        second = self.make_session().analyze_song()
        self.assertEqual(
            [(p.label, [(n.pitch, n.start) for n in p.notes]) for p in first.parts],
            [(p.label, [(n.pitch, n.start) for n in p.notes]) for p in second.parts],
        )
        session = self.make_session()
        before = [(n.pitch, n.start) for n in session.analyze_song().parts[1].notes]
        session.reroll()
        after = [(n.pitch, n.start) for n in session.analyze_song().parts[1].notes]
        self.assertNotEqual(before, after)

    def test_song_from_a_chords_midi_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session()
            session.load_midi(write_chords(os.path.join(tmp, "chords.mid")))
            session.set_instruments("bass, strings, bell")
            analysis = session.analyze_song()
            self.assertEqual(analysis.part_labels(), ["Bass", "Strings", "Bell"])
            self.assertEqual(analysis.detected_source, "MIDI file")

    def test_analyze_still_writes_the_single_melody(self):
        session = self.make_session()
        analysis = session.analyze()
        self.assertEqual(analysis.parts, [])
        self.assertTrue(analysis.melody)


class TestSongOutput(unittest.TestCase):
    def make_session(self, instruments="808s, flute, violin, pad"):
        session = Session()
        session.set_progression("Am F C G")
        session.set_instruments(instruments)
        session.analyze_song()
        return session

    def test_save_parts_writes_one_track_per_instrument(self):
        session = self.make_session("808s, flute")
        out = os.path.join(tempfile.mkdtemp(), "song.mid")
        written = session.save_parts(out, include_chords=True)
        self.assertEqual(written, [out])

        reloaded = read_midi(out)
        self.assertEqual(reloaded.track_names, ["808s", "Flute", "Chords"])
        self.assertEqual(reloaded.ticks_per_beat, PPQ)
        parts = session.analysis.parts
        for index, part in enumerate(parts):
            track_notes = [note for note in reloaded.notes if note.track == index]
            self.assertEqual(len(track_notes), len(part.notes))
            self.assertEqual(
                sorted(note.start for note in track_notes),
                sorted(note.start for note in part.notes),
            )
        # Each instrument keeps its own register inside the one file.
        bass_notes = [note for note in reloaded.notes if note.track == 0]
        flute_notes = [note for note in reloaded.notes if note.track == 1]
        self.assertLess(max(note.pitch for note in bass_notes), min(note.pitch for note in flute_notes))

    def test_every_instrument_gets_its_own_midi_channel(self):
        """FL Studio's importer routes by MIDI channel, not by track name."""
        session = self.make_session("808s, flute, pad")
        out = os.path.join(tempfile.mkdtemp(), "song.mid")
        session.save_parts(out, include_chords=True)

        reloaded = read_midi(out)
        self.assertEqual(reloaded.track_names, ["808s", "Flute", "Pad", "Chords"])
        for index, name in enumerate(reloaded.track_names):
            channels = {note.channel for note in reloaded.notes if note.track == index}
            self.assertEqual(channels, {index}, f"{name} should own channel {index}")

    def test_save_parts_can_split_one_file_per_instrument(self):
        session = self.make_session("808s, pad")
        out = os.path.join(tempfile.mkdtemp(), "song.mid")
        written = session.save_parts(out, include_chords=False, split=True)
        self.assertEqual(len(written), 3)
        for path, label in zip(written[1:], session.analysis.part_labels()):
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(path.endswith(f"- {label}.mid"), path)
            reloaded = read_midi(path)
            self.assertEqual(reloaded.track_names, [label])
            self.assertTrue(reloaded.notes)

    def test_split_stems_hold_only_their_own_instrument(self):
        """Each stem is one instrument - never the chords with a track name."""

        def onsets(notes):
            return sorted((note.pitch, note.start) for note in notes)

        def offsets(notes):
            # Note-ons and note-offs are what FL plays. Lengths of deliberately
            # overlapping same-pitch notes (an 808 slide) cannot be re-paired
            # unambiguously from an event stream, so compare both streams.
            return sorted((note.pitch, note.start + note.length) for note in notes)

        session = self.make_session("808s, flute, violin, pad")
        out = os.path.join(tempfile.mkdtemp(), "song.mid")
        chords = sorted((n.start, n.pitch) for n in session.chord_track_notes())
        written = session.save_parts(out, include_chords=True, split=True)
        self.assertEqual(len(written), 1 + len(session.analysis.parts))

        channels = []
        for index, (path, part) in enumerate(zip(written[1:], session.analysis.parts)):
            reloaded = read_midi(path)
            self.assertEqual(reloaded.track_names, [part.label])
            self.assertNotIn("Chords", reloaded.track_names)
            self.assertEqual(
                onsets(reloaded.notes), onsets(part.notes), part.label
            )
            self.assertEqual(
                offsets(reloaded.notes), offsets(part.notes), part.label
            )
            # The regression that started this: every stem was the chords.
            self.assertNotEqual(
                onsets(reloaded.notes),
                chords,
                f"{part.label} stem must not be the chord table",
            )
            channels.append({note.channel for note in reloaded.notes})
        # One channel per stem (0-based index) keeps them apart in FL too.
        self.assertEqual(channels, [{index} for index in range(len(channels))])
        # The part registers must differ, or the stems would all sound alike.
        registers = [
            (min(n.pitch for n in part.notes), max(n.pitch for n in part.notes))
            for part in session.analysis.parts
        ]
        self.assertEqual(len(set(registers)), len(registers))

    def test_save_parts_falls_back_to_the_single_melody(self):
        session = Session()
        session.set_progression("Am F C G")
        session.analyze()
        out = os.path.join(tempfile.mkdtemp(), "melody.mid")
        written = session.save_parts(out, include_chords=True)
        self.assertEqual(written, [out])
        self.assertIn("Chords", read_midi(out).track_names)

    def test_save_parts_before_generating_is_an_error(self):
        session = Session()
        session.set_progression("Am F C G")
        with self.assertRaises(SessionError):
            session.save_parts(os.path.join(tempfile.mkdtemp(), "nope.mid"))


class TestOptions(unittest.TestCase):
    def test_copy_with_does_not_mutate(self):
        options = Options()
        changed = options.copy_with(seed=99, density=0.5)
        self.assertEqual(options.seed, 1)
        self.assertEqual((changed.seed, changed.density), (99, 0.5))

    def test_as_dict_covers_every_field(self):
        data = Options().as_dict()
        for key in ("rhythm_index", "seed", "track_index", "scale_index", "instruments"):
            self.assertIn(key, data)

    def test_drums_are_part_of_the_parts_pipeline(self):
        """A named kit arrives as a drum part per piece, cohesive by construction."""
        session = Session()
        session.set_progression("Am F C G")
        session.set_instruments("808s, drums")
        analysis = session.analyze_song()
        drums = [part for part in analysis.parts if part.role == "drums"]
        self.assertEqual([part.label for part in drums], ["Kick", "Snare", "Hi-hats"])
        # A typed progression has no file tempo, so everything sits at 120.
        self.assertTrue(all("120 BPM" in part.register_note for part in drums))
        # Every piece shares one groove, so the parts line up with each other.
        self.assertEqual(len({part.register_note for part in drums}), 1)
        kick = [note.start for note in drums[0].notes]
        self.assertTrue(set(kick) & {note.start for note in drums[2].notes})

    def test_default_instruments_are_named(self):
        self.assertTrue(Options().instruments)
        session = Session()
        self.assertEqual(session.instruments(), Options().instruments)

    def test_unknown_option_is_rejected(self):
        with self.assertRaises(TypeError):
            Options(not_a_knob=1)


class TestHelpers(unittest.TestCase):
    def test_describe_and_key_of_chords(self):
        session = Session()
        session.set_progression("Am F C G")
        analysis = session.analyze()
        self.assertEqual(describe_chords(analysis.chords), "Am | F | C | G")
        tonic, scale = key_from_chords(analysis.chords)
        self.assertIn(scale, ("Major (Ionian)", "Natural Minor (Aeolian)"))
        self.assertIn(tonic, range(12))


if __name__ == "__main__":
    unittest.main()
