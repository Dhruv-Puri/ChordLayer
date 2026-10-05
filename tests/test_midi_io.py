"""Tests for chordlayer.midi_io (Standard MIDI File read/write)."""

import io
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from chordlayer.midi_io import (  # noqa: E402
    MidiNote,
    _vlq_bytes,
    parse_midi,
    to_midi_notes,
    write_midi_stream,
)

EOT = b"\xFF\x2F\x00"


def vlq(value: int) -> bytes:
    return _vlq_bytes(value)


def track(*events: tuple[int, bytes]) -> bytes:
    """Assemble a track payload from (delta, event bytes) pairs.

    Every MIDI event - including end-of-track - needs its own delta time, so
    spelling them out keeps these tests honest.
    """
    return b"".join(vlq(delta) + payload for delta, payload in events)


def track_file(tracks, division=480, fmt=1) -> bytes:
    """Assemble an SMF from raw track payloads."""
    data = b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), division)
    for payload in tracks:
        data += b"MTrk" + struct.pack(">I", len(payload)) + payload
    return data


def on(pitch, velocity=100, channel=0):
    return bytes([0x90 | channel, pitch, velocity])


def off(pitch, channel=0):
    return bytes([0x80 | channel, pitch, 0])


class TestVariableLengthQuantity(unittest.TestCase):
    def test_known_encodings(self):
        self.assertEqual(vlq(0), b"\x00")
        self.assertEqual(vlq(127), b"\x7f")
        self.assertEqual(vlq(128), b"\x81\x00")
        self.assertEqual(vlq(8192), b"\xc0\x00")
        self.assertEqual(vlq(0x0FFFFFFF), b"\xff\xff\xff\x7f")

    def test_negative_is_rejected(self):
        with self.assertRaises(ValueError):
            vlq(-1)


class TestParsing(unittest.TestCase):
    def test_simple_note(self):
        payload = track((0, on(60)), (480, off(60)), (0, EOT))
        midi = parse_midi(track_file([payload]))
        self.assertEqual(len(midi.notes), 1)
        note = midi.notes[0]
        self.assertEqual((note.pitch, note.start, note.length), (60, 0, 480))
        self.assertAlmostEqual(note.velocity, 100 / 127, places=3)
        self.assertEqual(midi.ticks_per_beat, 480)

    def test_absolute_times_accumulate(self):
        payload = track((240, on(60)), (480, off(60)), (120, on(64)), (120, off(64)), (0, EOT))
        midi = parse_midi(track_file([payload]))
        self.assertEqual([(n.pitch, n.start, n.length) for n in midi.notes], [(60, 240, 480), (64, 840, 120)])

    def test_running_status(self):
        # Second event omits the status byte: 0x90 is remembered.
        payload = track((0, on(60)), (240, bytes([60, 0])), (0, EOT))
        midi = parse_midi(track_file([payload]))
        self.assertEqual(midi.notes[0].length, 240)

    def test_note_on_velocity_zero_is_note_off(self):
        payload = track((0, on(62, 90)), (120, on(62, 0)), (0, EOT))
        midi = parse_midi(track_file([payload]))
        self.assertEqual(len(midi.notes), 1)
        self.assertEqual(midi.notes[0].length, 120)

    def test_tempo_and_time_signature(self):
        tempo = b"\xFF\x51\x03" + (500000).to_bytes(3, "big")
        signature = b"\xFF\x58\x04" + bytes([3, 2, 24, 8])  # 3/4
        payload = track((0, tempo), (0, signature), (0, EOT))
        midi = parse_midi(track_file([payload]))
        self.assertAlmostEqual(midi.tempo_bpm, 120.0, places=3)
        self.assertEqual(midi.time_signature, (3, 4))
        self.assertAlmostEqual(midi.beats_per_bar, 3.0)

    def test_six_eight_beats_per_bar(self):
        signature = b"\xFF\x58\x04" + bytes([6, 3, 24, 8])  # 6/8 -> 3 quarter beats
        payload = track((0, signature), (0, EOT))
        midi = parse_midi(track_file([payload]))
        self.assertAlmostEqual(midi.beats_per_bar, 3.0)

    def test_unmatched_note_is_closed_at_track_end(self):
        payload = track((0, on(64, 80)), (100, EOT))
        midi = parse_midi(track_file([payload]))
        self.assertEqual(midi.notes[0].length, 100)

    def test_overlapping_same_pitch(self):
        payload = track((0, on(60)), (0, on(60, 90)), (480, off(60)), (0, off(60)), (0, EOT))
        midi = parse_midi(track_file([payload]))
        self.assertEqual(len(midi.notes), 2)
        self.assertTrue(all(note.length == 480 for note in midi.notes))

    def test_staggered_overlaps_keep_their_own_lengths(self):
        # An 808 slide: the first note is still ringing when the next starts.
        payload = track(
            (0, on(36)), (100, on(36, 90)), (200, off(36)), (200, off(36)), (0, EOT)
        )
        midi = parse_midi(track_file([payload]))
        self.assertEqual(
            sorted(note.length for note in midi.notes), [300, 400]
        )
        self.assertEqual(sorted(note.start for note in midi.notes), [0, 100])

    def test_multiple_tracks_are_merged_with_track_index(self):
        first = track((0, on(57)), (480, off(57)), (0, EOT))
        second = track((0, on(60)), (480, off(60)), (0, EOT))
        midi = parse_midi(track_file([first, second]))
        self.assertEqual(sorted(note.pitch for note in midi.notes), [57, 60])
        self.assertEqual(sorted({note.track for note in midi.notes}), [0, 1])
        self.assertEqual(len(midi.track_names), 2)

    def test_track_names(self):
        name = "Chords"
        payload = track((0, b"\xFF\x03" + vlq(len(name)) + name.encode()), (0, EOT))
        midi = parse_midi(track_file([payload]))
        self.assertEqual(midi.track_names, ["Chords"])

    def test_ignores_sysex_and_controller_events(self):
        sysex = b"\xF0" + vlq(3) + b"\x7E\x7F\x09"
        payload = track(
            (0, sysex),
            (0, bytes([0xB0, 7, 100])),  # control change
            (0, bytes([0xC0, 5])),  # program change
            (0, on(60)),
            (240, off(60)),
            (0, EOT),
        )
        midi = parse_midi(track_file([payload]))
        self.assertEqual([(n.pitch, n.start, n.length) for n in midi.notes], [(60, 0, 240)])

    def test_smpte_division_falls_back(self):
        payload = track((0, on(60)), (500, off(60)), (0, EOT))
        midi = parse_midi(track_file([payload], division=0xE728))  # 25fps * 40 ticks
        self.assertTrue(midi.used_smpte_fallback)
        self.assertEqual(midi.ticks_per_beat, 500)
        self.assertTrue(midi.warnings)

    def test_format_zero(self):
        payload = track((0, on(60)), (240, off(60)), (0, EOT))
        midi = parse_midi(track_file([payload], fmt=0))
        self.assertEqual(midi.format, 0)
        self.assertEqual(len(midi.notes), 1)

    def test_bad_header_raises(self):
        with self.assertRaises(ValueError):
            parse_midi(b"not a midi file at all")

    def test_truncated_data_raises(self):
        good = track_file([track((0, on(60)), (480, off(60)), (0, EOT))])
        with self.assertRaises((EOFError, ValueError)):
            parse_midi(good[:-3])

    def test_empty_file_reports_no_notes(self):
        midi = parse_midi(track_file([track((0, EOT))]))
        self.assertEqual(midi.notes, [])
        self.assertTrue(any("no notes" in warning for warning in midi.warnings))


class TestWriting(unittest.TestCase):
    def test_round_trip_preserves_notes(self):
        notes = [
            MidiNote(pitch=60, start=0, length=480, velocity=0.8),
            MidiNote(pitch=64, start=480, length=240, velocity=0.65),
            MidiNote(pitch=67, start=960, length=960, velocity=0.5),
        ]
        buffer = io.BytesIO()
        write_midi_stream(buffer, notes, ticks_per_beat=480, tempo_bpm=100)
        midi = parse_midi(buffer.getvalue())
        self.assertEqual(midi.ticks_per_beat, 480)
        self.assertAlmostEqual(midi.tempo_bpm, 100.0, places=1)
        self.assertEqual(len(midi.notes), 3)
        for original, loaded in zip(notes, midi.notes):
            self.assertEqual(original.pitch, loaded.pitch)
            self.assertEqual(original.start, loaded.start)
            self.assertEqual(original.length, loaded.length)
            self.assertAlmostEqual(original.velocity, loaded.velocity, places=2)

    def test_channels_survive_a_round_trip(self):
        from chordlayer.midi_io import set_channel

        notes = [MidiNote(pitch=60, start=0, length=480, channel=4)]
        buffer = io.BytesIO()
        write_midi_stream(buffer, set_channel(notes, 7), ticks_per_beat=480)
        midi = parse_midi(buffer.getvalue())
        self.assertEqual([note.channel for note in midi.notes], [7])

    def test_set_channel_clamps_and_copies(self):
        from chordlayer.midi_io import set_channel

        notes = [MidiNote(pitch=60, start=0, length=480, channel=2)]
        self.assertEqual(set_channel(notes, 99)[0].channel, 15)
        self.assertEqual(set_channel(notes, -3)[0].channel, 0)
        self.assertEqual(notes[0].channel, 2, "the original notes must not change")

    def test_extra_tracks_are_written(self):
        melody = [MidiNote(pitch=72, start=0, length=480, velocity=0.8)]
        chords = [MidiNote(pitch=48, start=0, length=1920, velocity=0.7)]
        buffer = io.BytesIO()
        write_midi_stream(
            buffer,
            melody,
            ticks_per_beat=960,
            track_name="ChordLayer melody",
            extra_tracks=[("Chords (source)", chords)],
        )
        midi = parse_midi(buffer.getvalue())
        self.assertEqual(midi.ticks_per_beat, 960)
        self.assertEqual(sorted(note.pitch for note in midi.notes), [48, 72])
        self.assertIn("ChordLayer melody", midi.track_names)
        self.assertIn("Chords (source)", midi.track_names)

    def test_chords_stay_whole_behind_chord_changes(self):
        # A chord that lasts longer than a melody note must come back intact.
        chords = [MidiNote(pitch=48, start=0, length=1920, velocity=0.7)]
        melody = [
            MidiNote(pitch=72, start=0, length=240, velocity=0.8),
            MidiNote(pitch=74, start=240, length=240, velocity=0.8),
        ]
        buffer = io.BytesIO()
        write_midi_stream(buffer, melody, extra_tracks=[("Chords", chords)])
        midi = parse_midi(buffer.getvalue())
        longest = max(midi.notes, key=lambda note: note.length)
        self.assertEqual((longest.pitch, longest.length), (48, 1920))

    def test_zero_velocity_becomes_audible(self):
        buffer = io.BytesIO()
        write_midi_stream(buffer, [MidiNote(pitch=60, start=0, length=10, velocity=0.0)])
        midi = parse_midi(buffer.getvalue())
        self.assertGreater(midi.notes[0].velocity, 0.0)

    def test_ticks_per_beat_is_preserved(self):
        for ppq in (96, 240, 480, 960):
            buffer = io.BytesIO()
            write_midi_stream(buffer, [MidiNote(60, 0, ppq)], ticks_per_beat=ppq)
            self.assertEqual(parse_midi(buffer.getvalue()).ticks_per_beat, ppq)

    def test_to_midi_notes_accepts_engine_objects(self):
        class EngineNote:
            pitch = 64
            start = 100
            length = 200
            velocity = 0.9

        converted = to_midi_notes([EngineNote()], ticks_per_beat=480)
        self.assertEqual(
            (converted[0].pitch, converted[0].start, converted[0].length), (64, 100, 200)
        )


if __name__ == "__main__":
    unittest.main()
