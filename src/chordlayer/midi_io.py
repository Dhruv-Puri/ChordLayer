"""Standard MIDI File reading and writing (pure stdlib, no dependencies).

Used by the FL Studio 20 companion: FL exports the piano roll as a MIDI file,
ChordLayer reads it, generates a melody, and writes a new MIDI file to import
back into FL Studio.

The reader tolerates the usual real-world messiness: format 0 and 1, running
status, note-off-as-note-on-with-velocity-0, unmatched notes, overlapping notes
of the same pitch, and SMPTE (frame-based) time divisions.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field, replace
from typing import BinaryIO

DEFAULT_TICKS_PER_BEAT = 480
FALLBACK_TEMPO_BPM = 120.0
# What the arranger assumes when a project has no tempo of its own (typed chords).
DEFAULT_TEMPO_BPM = FALLBACK_TEMPO_BPM

_NOTE_OFF = 0x80
_NOTE_ON = 0x90
_POLY_AFTERTOUCH = 0xA0
_CONTROL_CHANGE = 0xB0
_PROGRAM_CHANGE = 0xC0
_CHANNEL_PRESSURE = 0xD0
_PITCH_BEND = 0xE0
_SYSEX = 0xF0
_META = 0xFF


@dataclass
class MidiNote:
    """A note in ticks. Velocity is 0.0-1.0 to match FL Studio's scale.

    ``channel`` is the MIDI channel (0-15). It matters more than ``track`` for
    FL Studio, whose MIDI importer maps *channels* to Instrument channels (see
    :func:`set_channel`).
    """

    pitch: int
    start: int
    length: int
    velocity: float = 0.8
    track: int = 0
    channel: int = 0

    @property
    def end(self) -> int:
        return self.start + self.length


@dataclass
class MidiFile:
    """Everything ChordLayer needs from a parsed MIDI file."""

    notes: list[MidiNote] = field(default_factory=list)
    ticks_per_beat: float = DEFAULT_TICKS_PER_BEAT
    tempo_bpm: float = FALLBACK_TEMPO_BPM
    time_signature: tuple[int, int] = (4, 4)
    track_names: list[str] = field(default_factory=list)
    format: int = 1
    warnings: list[str] = field(default_factory=list)
    used_smpte_fallback: bool = False

    @property
    def beats_per_bar(self) -> float:
        """Bar length in quarter-note beats (6/8 -> 3.0)."""
        numerator, denominator = self.time_signature
        if denominator <= 0:
            return 4.0
        return max(1.0, numerator * 4.0 / denominator)

    @property
    def length_ticks(self) -> int:
        return max((note.end for note in self.notes), default=0)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def eof(self) -> bool:
        return self.pos >= len(self.data)

    def u8(self) -> int:
        if self.eof():
            raise EOFError("unexpected end of MIDI data")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def bytes(self, count: int) -> bytes:
        if self.pos + count > len(self.data):
            raise EOFError("unexpected end of MIDI data")
        chunk = self.data[self.pos : self.pos + count]
        self.pos += count
        return chunk

    def u16(self) -> int:
        return struct.unpack(">H", self.bytes(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.bytes(4))[0]

    def vlq(self) -> int:
        """Variable-length quantity (used for delta times and meta lengths)."""
        value = 0
        for _ in range(5):
            byte = self.u8()
            value = (value << 7) | (byte & 0x7F)
            if not byte & 0x80:
                return value
        raise ValueError("malformed variable-length quantity (more than 5 bytes)")


def _vlq_bytes(value: int) -> bytes:
    """Encode a non-negative integer as a variable-length quantity."""
    if value < 0:
        raise ValueError("variable-length quantities cannot be negative")
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(out)


def read_midi(path: str) -> MidiFile:
    """Parse a MIDI file into :class:`MidiFile` (notes in ticks)."""
    with open(path, "rb") as handle:
        data = handle.read()
    return parse_midi(data)


def parse_midi(data: bytes) -> MidiFile:
    """Parse MIDI file bytes (separate from :func:`read_midi` for testing)."""
    reader = _Reader(data)
    result = MidiFile()

    if reader.bytes(4) != b"MThd":
        raise ValueError("not a MIDI file (missing MThd header)")
    header_length = reader.u32()
    header = _Reader(reader.bytes(header_length))
    result.format = header.u16()
    track_count = header.u16()
    division = header.u16()

    if division & 0x8000:
        # SMPTE: high byte is negative fps, low byte is ticks per frame.
        frames_per_second = 256 - (division >> 8)
        ticks_per_frame = division & 0xFF
        if frames_per_second <= 0:
            frames_per_second = 25
        result.ticks_per_beat = max(1, int(frames_per_second * ticks_per_frame / 2))
        result.used_smpte_fallback = True
        result.warnings.append(
            "SMPTE time division found; timing approximated as "
            f"{result.ticks_per_beat} ticks per beat"
        )
    else:
        result.ticks_per_beat = division or DEFAULT_TICKS_PER_BEAT

    tempos: list[tuple[int, float]] = []
    time_signatures: list[tuple[int, tuple[int, int]]] = []
    raw_notes: list[MidiNote] = []

    for track_index in range(track_count):
        if reader.eof():
            break
        chunk_type = reader.bytes(4)
        chunk_length = reader.u32()
        chunk = _Reader(reader.bytes(chunk_length))
        if chunk_type != b"MTrk":
            result.warnings.append(f"skipped unknown chunk {chunk_type!r}")
            continue
        _read_track(chunk, track_index, result, raw_notes, tempos, time_signatures)

    if tempos:
        tempos.sort(key=lambda item: item[0])
        result.tempo_bpm = tempos[0][1]
    if time_signatures:
        time_signatures.sort(key=lambda item: item[0])
        result.time_signature = time_signatures[0][1]

    result.notes = sorted(raw_notes, key=lambda note: (note.start, note.pitch))
    if not result.notes:
        result.warnings.append("no notes found in the MIDI file")
    return result


def _read_track(
    chunk: _Reader,
    track_index: int,
    result: MidiFile,
    raw_notes: list[MidiNote],
    tempos: list[tuple[int, float]],
    time_signatures: list[tuple[int, tuple[int, int]]],
) -> None:
    absolute = 0
    status: int | None = None
    # Open notes per (channel, pitch). Note-offs close them in order (FIFO), so
    # deliberately overlapping same-pitch notes - like an 808 slide - keep their
    # own lengths instead of merging into one long note.
    open_notes: dict[tuple[int, int], list[tuple[int, float]]] = {}
    track_name = ""

    while not chunk.eof():
        delta = chunk.vlq()
        absolute += delta

        first = chunk.u8()
        if first == _META:
            status = None
            meta_type = chunk.u8()
            length = chunk.vlq()
            payload = chunk.bytes(length)
            if meta_type == 0x51 and len(payload) == 3:
                micros = int.from_bytes(payload, "big")
                if micros > 0:
                    tempos.append((absolute, round(60_000_000 / micros, 3)))
            elif meta_type == 0x58 and len(payload) >= 2:
                time_signatures.append((absolute, (payload[0] or 4, 2 ** payload[1])))
            elif meta_type == 0x03:
                track_name = payload.decode("utf-8", "replace").strip("\x00")
            elif meta_type == 0x2F:
                break
            continue

        if first in (_SYSEX, 0xF7):
            status = None
            length = chunk.vlq()
            chunk.bytes(length)
            continue

        if first & 0x80:
            status = first
        elif status is None:
            raise ValueError("MIDI data uses running status before any status byte")
        else:
            chunk.pos -= 1  # the byte was data, not status: rewind

        if status is None:
            continue
        command = status & 0xF0
        channel = status & 0x0F

        if command in (_NOTE_ON, _NOTE_OFF):
            pitch = chunk.u8()
            velocity = chunk.u8()
            key = (channel, pitch)
            is_on = command == _NOTE_ON and velocity > 0
            if is_on:
                open_notes.setdefault(key, []).append((absolute, velocity / 127.0))
            else:
                queue = open_notes.get(key)
                if queue:
                    start, velocity_value = queue.pop(0)
                    raw_notes.append(
                        MidiNote(
                            pitch=pitch,
                            start=start,
                            length=max(1, absolute - start),
                            velocity=velocity_value,
                            track=track_index,
                            channel=channel,
                        )
                    )
        elif command in (_POLY_AFTERTOUCH, _CONTROL_CHANGE, _PITCH_BEND):
            chunk.bytes(2)
        elif command in (_PROGRAM_CHANGE, _CHANNEL_PRESSURE):
            chunk.bytes(1)
        else:
            chunk.bytes(1)

    # Close any notes that never received a note-off.
    for (channel, pitch), queue in open_notes.items():
        for start, velocity_value in queue:
            raw_notes.append(
                MidiNote(
                    pitch=pitch,
                    start=start,
                    length=max(1, absolute - start),
                    velocity=velocity_value,
                    track=track_index,
                    channel=channel,
                )
            )
    result.track_names.append(track_name or f"Track {track_index + 1}")


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

_TICKS_PER_QUARTER = 480


def to_midi_notes(notes, ticks_per_beat: float = DEFAULT_TICKS_PER_BEAT) -> list[MidiNote]:
    """Convert engine MelodyNotes (or anything note-like) into MIDI notes."""
    converted = []
    for note in notes:
        converted.append(
            MidiNote(
                pitch=int(getattr(note, "pitch", getattr(note, "number", 60))),
                start=int(getattr(note, "start", getattr(note, "time", 0))),
                length=int(getattr(note, "length", 1)),
                velocity=float(getattr(note, "velocity", 0.8)),
                track=int(getattr(note, "track", 0)),
                channel=int(getattr(note, "channel", 0)),
            )
        )
    return converted


def set_channel(notes: list[MidiNote], channel: int) -> list[MidiNote]:
    """Return the same notes moved to another MIDI channel (0-15).

    FL Studio's MIDI importer routes by channel ("Create one channel per
    track - imports each MIDI channel"), so giving every instrument its own
    channel is what keeps a multi-part file from collapsing into one piano
    roll where only the chord track is visible.
    """
    channel = max(0, min(15, int(channel)))
    return [replace(note, channel=channel) for note in notes]


def write_midi(
    path: str,
    notes: list[MidiNote],
    ticks_per_beat: float = DEFAULT_TICKS_PER_BEAT,
    tempo_bpm: float = FALLBACK_TEMPO_BPM,
    track_name: str = "ChordLayer melody",
    extra_tracks: list[tuple[str, list[MidiNote]]] | None = None,
) -> None:
    """Write a format-1 MIDI file with one track per part.

    Keeping the source PPQ means the melody lines up bar-for-bar when it is
    imported back into the piano roll. Each note carries its own MIDI channel
    (see :func:`set_channel`) so importers that route by channel keep the parts
    apart.
    """
    with open(path, "wb") as handle:
        write_midi_stream(
            handle, notes, ticks_per_beat, tempo_bpm, track_name, extra_tracks
        )


def write_midi_stream(
    handle: BinaryIO,
    notes: list[MidiNote],
    ticks_per_beat: float = DEFAULT_TICKS_PER_BEAT,
    tempo_bpm: float = FALLBACK_TEMPO_BPM,
    track_name: str = "ChordLayer melody",
    extra_tracks: list[tuple[str, list[MidiNote]]] | None = None,
) -> None:
    """Stream a format-1 MIDI file (used by the tests and :func:`write_midi`)."""
    division = int(round(ticks_per_beat)) or DEFAULT_TICKS_PER_BEAT
    division = min(0x7FFF, max(1, division))
    parts = [(track_name, notes)] + list(extra_tracks or [])

    handle.write(b"MThd")
    handle.write(struct.pack(">IHHH", 6, 1, len(parts), division))
    for index, (name, part_notes) in enumerate(parts):
        payload = _track_payload(part_notes, tempo_bpm if index == 0 else None, name)
        handle.write(b"MTrk")
        handle.write(struct.pack(">I", len(payload)))
        handle.write(payload)


def _track_payload(notes: list[MidiNote], tempo_bpm: float | None, name: str) -> bytes:
    events: list[tuple[int, int, bytes]] = []  # (tick, order, bytes)
    if name:
        events.append((0, 0, b"\xFF\x03" + _vlq_bytes(len(name)) + name.encode("utf-8", "replace")))
    if tempo_bpm:
        micros = max(1, int(round(60_000_000 / max(1.0, tempo_bpm))))
        events.append((0, 1, b"\xFF\x51\x03" + micros.to_bytes(3, "big")))

    for note in notes:
        pitch = max(0, min(127, int(note.pitch)))
        velocity = max(1, min(127, int(round(note.velocity * 127)) or 1))
        start = max(0, int(note.start))
        end = max(start + 1, int(note.start) + max(1, int(note.length)))
        channel = max(0, min(15, int(getattr(note, "channel", 0))))
        events.append((start, 2, bytes([_NOTE_ON | channel, pitch, velocity])))
        events.append((end, 3, bytes([_NOTE_OFF | channel, pitch, 0])))

    events.sort(key=lambda item: (item[0], item[1]))
    out = bytearray()
    previous_tick = 0
    for tick, _order, payload in events:
        out += _vlq_bytes(max(0, tick - previous_tick))
        out += payload
        previous_tick = tick
    out += _vlq_bytes(0) + b"\xFF\x2F\x00"  # end of track
    return bytes(out)
