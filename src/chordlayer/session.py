"""Session core for the FL Studio 20 companion: chords MIDI in, melody MIDI out.

FL Studio 20 has no piano roll scripting, so the companion works by file
round-trip: the piano roll exports the chords as MIDI, this session detects the
chords and generates a melody with the same engine the piano roll script uses,
then writes a MIDI file to import back into FL Studio.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields

from .chord_detect import DetectionResult, SimpleNote, detect_chords, infer_key_from_chords
from .generator import (
    CONTOURS,
    RHYTHM_PRESETS,
    STRATEGIES,
    MelodyNote,
    MelodyParams,
    generate_melody,
)
from .instruments import (
    DEFAULT_INSTRUMENTS,
    expand_kits,
    InstrumentError,
    InstrumentPart,
    build_song,
    instrument_choices,
    parse_instruments,
)
from .midi_io import (
    DEFAULT_TEMPO_BPM,
    DEFAULT_TICKS_PER_BEAT,
    MidiFile,
    MidiNote,
    read_midi,
    set_channel,
    to_midi_notes,
    write_midi,
)
from .theory import (
    ChordSpec,
    NOTE_NAMES,
    SCALES,
    chord_pitch_classes,
    format_chord,
    parse_progression,
)

# dialog-shaped choice lists, shared with the UI
KEY_CHOICES = ("Auto (from chords)",) + NOTE_NAMES
SCALE_CHOICES = ("Auto (from chords)",) + tuple(SCALES.keys())


class SessionError(Exception):
    """A user-facing problem (missing file, no chords, bad progression...)."""


@dataclass
class Options:
    """Every knob the companion exposes - mirrors the piano roll script.

    ``instruments`` is the one that matters for the normal workflow: name the
    instruments and each part is written for it. The rest are optional overrides
    for people who want to hand-tune a single melody.
    """

    instruments: tuple[str, ...] = DEFAULT_INSTRUMENTS
    rhythm_index: int = 0
    density: float = 0.8
    rest_chance: float = 0.1
    gate: float = 0.9
    strategy_index: int = 0
    contour_index: int = 0
    center_octave: int = 4
    range_semitones: int = 12
    motif_bars: int = 0
    velocity: float = 0.8
    velocity_jitter: float = 0.05
    timing_jitter: float = 0.0
    seed: int = 1
    key_index: int = 0  # 0 = auto, else 1..12 -> C..B
    scale_index: int = 0  # 0 = auto, else index into SCALE_CHOICES
    track_index: int = -1  # -1 = all tracks, else 0-based track
    min_chord_beats: float = 0.25

    def __post_init__(self) -> None:
        # A kit name ("drums") is expanded once, here, so everything downstream
        # - parts, export, the UI - only ever sees real instrument keys.
        self.instruments = expand_kits(tuple(self.instruments or ()))

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def copy_with(self, **changes) -> "Options":
        data = self.as_dict()
        data.update(changes)
        return Options(**data)


@dataclass
class Analysis:
    """Detected chords plus the generated melody."""

    chords: list[ChordSpec] = field(default_factory=list)
    tonic: int = 0
    scale_name: str = "Major (Ionian)"
    melody: list[MelodyNote] = field(default_factory=list)
    parts: list[InstrumentPart] = field(default_factory=list)
    melody_part: str = ""
    ticks_per_beat: float = DEFAULT_TICKS_PER_BEAT
    beats_per_bar: float = 4.0
    detected_source: str = "MIDI file"

    # -- reporting ---------------------------------------------------------

    @property
    def key_label(self) -> str:
        """Readable key, e.g. 'A minor' or 'C major'."""
        name = NOTE_NAMES[self.tonic % 12]
        if "Minor" in self.scale_name:
            return f"{name} minor"
        if "Major" in self.scale_name:
            return f"{name} major"
        return f"{name} {self.scale_name.split(' ')[0].lower()}"

    def chord_labels(self) -> list[str]:
        return [chord.label for chord in self.chords]

    def part_labels(self) -> list[str]:
        return [part.label for part in self.parts]

    def summary_lines(self) -> list[str]:
        if not self.chords:
            return ["No chords detected."]
        lines = [f"Key: {self.key_label}   Chords: {' | '.join(self.chord_labels())}"]
        if self.parts:
            lines.append(f"Parts: {' + '.join(self.part_labels())}")
            lines.extend(f"  {part.summary()}" for part in self.parts)
            if self.melody and self.melody_part:
                lines.append(
                    f"Melody line: {self.melody_part} "
                    f"({len(self.melody)} notes) - this is what the preview outlines"
                )
            return lines
        if self.melody:
            pitches = [note.pitch for note in self.melody]
            lines.append(
                f"Melody: {len(self.melody)} notes, range {min(pitches)}..{max(pitches)}"
            )
        else:
            lines.append("Melody: (none - press Generate)")
        return lines


class Session:
    """Holds a loaded chords MIDI file and turns it into a melody."""

    def __init__(self) -> None:
        self.midi: MidiFile | None = None
        self.midi_path: str | None = None
        self.progression_text: str = ""
        self.instrument_text: str = ""
        self.options = Options()
        self.analysis = Analysis()
        self.log: list[str] = []

    # -- input -------------------------------------------------------------

    def load_midi(self, path: str) -> MidiFile:
        """Read a chords MIDI file exported from FL Studio's piano roll."""
        try:
            midi = read_midi(path)
        except (OSError, ValueError) as error:
            raise SessionError(f"Could not read MIDI file: {error}") from error
        self.midi = midi
        self.midi_path = path
        notes = len(midi.notes)
        self.log.append(
            f"Loaded {path}: {notes} notes, "
            f"{midi.ticks_per_beat:g} PPQ, {midi.tempo_bpm:g} BPM, "
            f"{midi.time_signature[0]}/{midi.time_signature[1]}"
        )
        for warning in midi.warnings:
            self.log.append(f"  note: {warning}")
        if not notes and not self.progression_text:
            self.log.append("  note: this MIDI file contains no notes")
        return midi

    def set_progression(self, text: str) -> None:
        self.progression_text = (text or "").strip()

    def set_instruments(self, text: str) -> tuple[str, ...]:
        """Read the instrument list; every named instrument must be known.

        Accepts the friendly forms people actually type - ``"808s, flute and
        violin"`` - and stores canonical keys on the options.
        """
        self.instrument_text = (text or "").strip()
        try:
            keys = parse_instruments(self.instrument_text)
        except InstrumentError as error:
            raise SessionError(str(error)) from error
        self.options = self.options.copy_with(instruments=keys)
        return keys

    def instruments(self) -> tuple[str, ...]:
        return tuple(self.options.instruments)

    @staticmethod
    def instrument_choices() -> list[str]:
        return instrument_choices()

    # -- helpers -----------------------------------------------------------

    @property
    def ticks_per_beat(self) -> float:
        if self.midi:
            return float(self.midi.ticks_per_beat)
        return DEFAULT_TICKS_PER_BEAT

    @property
    def beats_per_bar(self) -> float:
        if self.midi:
            return self.midi.beats_per_bar
        return 4.0

    def source_notes(self, include_track: int | None = None) -> list[SimpleNote]:
        """Notes used for chord detection (optionally one track only)."""
        if not self.midi:
            return []
        track = self.options.track_index if include_track is None else include_track
        notes = [
            SimpleNote(
                pitch=note.pitch,
                start=note.start,
                length=max(1, note.length),
            )
            for note in self.midi.notes
            if track < 0 or note.track == track
        ]
        return notes

    def track_choices(self) -> list[str]:
        if not self.midi:
            return ["All tracks"]
        names = [
            f"{index + 1}: {name}" for index, name in enumerate(self.midi.track_names)
        ]
        return ["All tracks"] + names

    # -- analysis ----------------------------------------------------------

    def _analyze_context(self) -> tuple[DetectionResult, int, str, float]:
        """Detect the harmony and resolve the key - shared by both generators."""
        if not self.midi and not self.progression_text:
            raise SessionError(
                "Load a chords MIDI file (in FL Studio: piano roll menu -> "
                "Export as MIDI file), or type a progression like 'Am F C G'."
            )
        if self.progression_text:
            try:
                parse_progression(self.progression_text)
            except ValueError as error:
                raise SessionError(f"Progression text problem: {error}") from error

        ticks_per_beat = self.ticks_per_beat
        result: DetectionResult = detect_chords(
            self.source_notes(),
            ticks_per_beat,
            min_length_beats=self.options.min_chord_beats,
            progression_text=self.progression_text or None,
        )
        if not result.chords:
            raise SessionError(
                "No chords detected. Make sure the MIDI file contains chord notes "
                "(two or more notes playing together), or type a progression."
            )

        key_index = self.options.key_index
        scale_index = self.options.scale_index
        tonic = key_index - 1 if key_index > 0 else result.tonic
        scale_name = SCALE_CHOICES[scale_index] if scale_index > 0 else result.scale_name
        return result, tonic, scale_name, ticks_per_beat

    def analyze(self) -> Analysis:
        """Detect chords and generate one melody from the manual knobs."""
        result, tonic, scale_name, ticks_per_beat = self._analyze_context()

        params = MelodyParams(
            ticks_per_beat=ticks_per_beat,
            chords=result.chords,
            tonic=tonic,
            scale_name=scale_name,
            beats_per_bar=self.beats_per_bar,
            rhythm_preset=RHYTHM_PRESETS[self.options.rhythm_index % len(RHYTHM_PRESETS)],
            density=self.options.density,
            rest_chance=self.options.rest_chance,
            gate=self.options.gate,
            strategy=STRATEGIES[self.options.strategy_index % len(STRATEGIES)],
            contour=CONTOURS[self.options.contour_index % len(CONTOURS)],
            center_octave=self.options.center_octave,
            range_semitones=self.options.range_semitones,
            motif_bars=self.options.motif_bars,
            velocity=self.options.velocity,
            velocity_jitter=self.options.velocity_jitter,
            timing_jitter=self.options.timing_jitter,
            seed=self.options.seed,
        )
        problems = params.validate()
        if problems:
            raise SessionError(problems[0])

        melody = generate_melody(params)
        self.analysis = Analysis(
            chords=result.chords,
            tonic=tonic,
            scale_name=scale_name,
            melody=melody,
            melody_part="Melody",
            ticks_per_beat=ticks_per_beat,
            beats_per_bar=self.beats_per_bar,
            detected_source="typed progression" if self.progression_text else "MIDI file",
        )
        self.log.append(
            f"Generated {len(melody)} melody notes over {len(result.chords)} chords "
            f"({self.analysis.key_label}, seed {self.options.seed})"
        )
        return self.analysis

    def analyze_song(self) -> Analysis:
        """Write one idiomatic part per instrument over the detected chords.

        This is the zero-tuning path: the instruments decide the register,
        note lengths, articulation and density, so nothing else has to be set.
        """
        result, tonic, scale_name, ticks_per_beat = self._analyze_context()
        instruments = self.instruments()
        try:
            parts = build_song(
                instruments,
                result.chords,
                tonic,
                scale_name,
                ticks_per_beat,
                beats_per_bar=self.beats_per_bar,
                seed=self.options.seed,
                tempo_bpm=self.midi.tempo_bpm if self.midi else DEFAULT_TEMPO_BPM,
            )
        except InstrumentError as error:
            raise SessionError(str(error)) from error

        lead = next((part for part in parts if part.role == "lead"), None)
        primary = lead or parts[0]
        # The single-melody preview follows a pitched part, never the kit.
        if primary.role == "drums":
            primary = next((part for part in parts if part.role != "drums"), primary)
        self.analysis = Analysis(
            chords=result.chords,
            tonic=tonic,
            scale_name=scale_name,
            melody=list(primary.notes),
            parts=parts,
            melody_part=primary.label,
            ticks_per_beat=ticks_per_beat,
            beats_per_bar=self.beats_per_bar,
            detected_source="typed progression" if self.progression_text else "MIDI file",
        )
        total = sum(len(part.notes) for part in parts)
        self.log.append(
            f"Wrote {len(parts)} instrument part(s), {total} notes over "
            f"{len(result.chords)} chords ({self.analysis.key_label}, seed {self.options.seed})"
        )
        self.log.extend(f"  {part.summary()}" for part in parts)
        return self.analysis

    # -- output ------------------------------------------------------------

    def save_melody(self, path: str, include_chords: bool = True) -> str:
        """Write the melody (and optionally the source chords) to a MIDI file."""
        if not self.analysis.melody:
            raise SessionError("Nothing to save yet - press Generate first.")
        melody_notes = to_midi_notes(self.analysis.melody, self.analysis.ticks_per_beat)
        extra_tracks = []
        if include_chords:
            chords = self.chord_track_notes()
            if chords:
                extra_tracks.append(("Chords", chords))
        write_midi(
            path,
            notes=melody_notes,
            ticks_per_beat=self.analysis.ticks_per_beat,
            tempo_bpm=self.midi.tempo_bpm if self.midi else 120.0,
            track_name="ChordLayer melody",
            extra_tracks=extra_tracks,
        )
        self.log.append(f"Saved {path} ({len(melody_notes)} melody notes)")
        return path

    def save_parts(
        self, path: str, include_chords: bool = True, split: bool = False
    ) -> list[str]:
        """Write every instrument part as its own track in one MIDI file.

        Each part also gets its own MIDI channel, because FL Studio's importer
        routes by MIDI channel: parts sharing a channel collapse into one
        Instrument channel where only the chords are audible.

        With ``split=True`` each instrument additionally gets its own file next
        to ``path`` containing *only that instrument* - no chord track - so a
        stem can never be mistaken for the harmony. Falls back to the single
        melody when no parts were written.
        """
        parts = self.analysis.parts
        if not parts:
            return [self.save_melody(path, include_chords=include_chords)]
        if not any(part.notes for part in parts):
            raise SessionError("Nothing to save yet - press Generate first.")

        ticks_per_beat = self.analysis.ticks_per_beat
        tempo = self.midi.tempo_bpm if self.midi else 120.0
        part_tracks = [
            (part.label, set_channel(to_midi_notes(part.notes, ticks_per_beat), index))
            for index, part in enumerate(parts)
        ]
        extra_tracks = list(part_tracks[1:])
        if include_chords:
            chord_notes = set_channel(self.chord_track_notes(), len(parts))
            if chord_notes:
                extra_tracks.append(("Chords", chord_notes))
        first_label, first_notes = part_tracks[0]
        write_midi(
            path,
            notes=first_notes,
            ticks_per_beat=ticks_per_beat,
            tempo_bpm=tempo,
            track_name=first_label,
            extra_tracks=extra_tracks,
        )
        written = [path]
        self.log.append(
            f"Saved {path} ({len(parts)} tracks: "
            f"{', '.join(part.label for part in parts)})"
        )

        if split:
            stem, extension = os.path.splitext(path)
            for index, part in enumerate(parts):
                part_path = f"{stem} - {part.label}{extension or '.mid'}"
                write_midi(
                    part_path,
                    notes=set_channel(to_midi_notes(part.notes, ticks_per_beat), index),
                    ticks_per_beat=ticks_per_beat,
                    tempo_bpm=tempo,
                    track_name=part.label,
                    extra_tracks=None,
                )
                written.append(part_path)
            self.log.append(
                f"  plus {len(parts)} single-instrument file(s) "
                "(one instrument each, no chord track)"
            )
        return written

    def chord_track_notes(self) -> list[MidiNote]:
        """The analysed chords as MIDI notes, so the part rides along on export.

        These come from the analysis rather than the raw file, so a typed
        progression exports exactly the chords the melody was built on.
        """
        notes: list[MidiNote] = []
        ticks_per_beat = self.analysis.ticks_per_beat
        for chord in self.analysis.chords:
            for pitch_class in chord_pitch_classes(chord.root, chord.template):
                notes.append(
                    MidiNote(
                        pitch=48 + pitch_class,  # compact voicing, 48..59
                        start=int(round(chord.start * ticks_per_beat)),
                        length=max(1, int(round(chord.length * ticks_per_beat))),
                        velocity=0.7,
                    )
                )
        return notes

    def reroll(self, step: int = 1) -> int:
        """Advance the seed (what the piano roll script's Seed knob does)."""
        self.options = self.options.copy_with(seed=(self.options.seed + step) % 10000)
        return self.options.seed


def describe_chords(chords: list[ChordSpec]) -> str:
    return " | ".join(format_chord(chord.root, chord.template) for chord in chords)


def key_from_chords(chords: list[ChordSpec]) -> tuple[int, str]:
    return infer_key_from_chords(chords)
