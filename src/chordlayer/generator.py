"""Melody generation over a detected chord timeline.

Rule-based engine: chord tones are preferred on strong beats, scale tones fill
weak beats, contour presets shape the melodic direction, motifs repeat at
interval distance, and everything is constrained to a user-selectable pitch
range with humanized velocity/timing. All randomness derives from the seed, so
the same parameters always produce the same melody.
"""

from __future__ import annotations

from dataclasses import dataclass

from .rng import Rng
from .theory import (
    ChordSpec,
    chord_tones_in_range,
    scale_mask,
    scale_notes_in_range,
)

# Note color used to tag generated melody notes (0-15, FL colors 1-16).
MELODY_COLOR_TAG = 15

DEFAULT_CENTER_OCTAVE = 4
# Range is measured in semitones either side of the center pitch.
DEFAULT_RANGE = 12

RHYTHM_PRESETS = ("Eighths", "Sixteenths", "Syncopated", "Offbeats", "Ballad", "Free")
STRATEGIES = ("Chord + passing", "Arpeggio", "Guide tones", "Random walk")
CONTOURS = ("Arch", "Follow chords", "Motif repeat", "Free")

_MAJOR_THIRD_TEMPLATES = {"maj", "maj7", "7", "maj6", "aug", "add9", "9", "maj9"}


@dataclass(frozen=True)
class MelodyNote:
    """A generated note (times in ticks, like flpianoroll.Note)."""

    pitch: int
    start: int
    length: int
    velocity: float
    color: int = MELODY_COLOR_TAG

    @property
    def end(self) -> int:
        return self.start + self.length


@dataclass
class MelodyParams:
    """All knobs exposed in the script dialog (documented in the README)."""

    ticks_per_beat: float
    chords: list[ChordSpec]
    tonic: int = 0
    scale_name: str = "Major (Ionian)"
    beats_per_bar: float = 4.0
    rhythm_preset: str = "Eighths"
    density: float = 0.8
    rest_chance: float = 0.1
    gate: float = 0.9
    strategy: str = "Chord + passing"
    contour: str = "Arch"
    center_octave: int = DEFAULT_CENTER_OCTAVE
    range_semitones: int = DEFAULT_RANGE
    motif_bars: int = 0
    velocity: float = 0.8
    velocity_jitter: float = 0.05
    timing_jitter: float = 0.0  # ticks
    seed: int = 1
    melody_color: int = MELODY_COLOR_TAG

    def __post_init__(self) -> None:
        if self.ticks_per_beat <= 0:
            raise ValueError("ticks_per_beat must be positive")
        if not self.chords:
            raise ValueError("MelodyParams needs at least one chord")
        self.beats_per_bar = min(16.0, max(1.0, float(self.beats_per_bar)))
        self.density = min(1.0, max(0.0, float(self.density)))
        self.rest_chance = min(0.9, max(0.0, float(self.rest_chance)))
        self.gate = min(1.0, max(0.1, float(self.gate)))
        self.range_semitones = min(24, max(1, int(self.range_semitones)))
        self.center_octave = min(9, max(0, int(self.center_octave)))
        self.motif_bars = min(8, max(0, int(self.motif_bars)))
        self.melody_color = min(15, max(0, int(self.melody_color)))
        self.velocity = min(1.0, max(0.1, float(self.velocity)))
        self.velocity_jitter = min(0.5, max(0.0, float(self.velocity_jitter)))
        self.timing_jitter = max(0.0, float(self.timing_jitter))
        if self.rhythm_preset not in RHYTHM_PRESETS:
            raise ValueError(f"Unknown rhythm preset {self.rhythm_preset!r}")
        if self.strategy not in STRATEGIES:
            raise ValueError(f"Unknown strategy {self.strategy!r}")
        if self.contour not in CONTOURS:
            raise ValueError(f"Unknown contour {self.contour!r}")
        self.scale_name = scale_mask(self.scale_name) and self.scale_name

    @property
    def lo(self) -> int:
        # FL Studio displays note 48 as C4, so center octave 4 -> note 48.
        return self.center_octave * 12 - self.range_semitones

    @property
    def hi(self) -> int:
        return self.center_octave * 12 + self.range_semitones

    def validate(self) -> list[str]:
        """Human-readable problems; empty list means ready to generate."""
        problems: list[str] = []
        lo, hi = self.lo, self.hi
        if lo < 0 or hi > 131:
            problems.append(
                f"Melody range {lo}..{hi} exceeds MIDI 0..131 — lower Center octave or Range."
            )
        return problems


@dataclass(frozen=True)
class _Slot:
    start: float  # beats, absolute on the timeline
    length: float  # beats
    strong: bool  # chord-tone landing expected


# ---------------------------------------------------------------------------
# Rhythm
# ---------------------------------------------------------------------------

# Slot length in beats for each rhythm preset (shared with the arranger).
STEP_BEATS = {
    "Eighths": 0.5,
    "Sixteenths": 0.25,
    "Syncopated": 0.5,
    "Offbeats": 0.5,
    "Ballad": 1.0,
    "Free": 0.25,
}


def build_rhythm(params: MelodyParams) -> list[_Slot]:
    """Slots across every chord span, per the rhythm preset and density."""
    step = STEP_BEATS[params.rhythm_preset]
    timeline_start = min(chord.start for chord in params.chords)
    timeline_end = max(chord.end for chord in params.chords)
    step_count = max(1, int(round((timeline_end - timeline_start) / step)))

    density_rng = Rng(params.seed + 101)
    free_rng = Rng(params.seed + 211)

    slots: list[_Slot] = []
    for index in range(step_count):
        start = timeline_start + index * step
        if start >= timeline_end - 1e-9:
            break
        if chord_at(params.chords, start) is None:
            continue  # chord gap -> rest
        if params.rhythm_preset == "Offbeats" and abs(start % 1.0) < 1e-9:
            continue  # the preset lives between the beats
        strong = _is_strong(params, start, free_rng)
        # Density prunes slots; strong slots get a survival bonus so the
        # pulse survives at low density. Step 0 always survives.
        keep_chance = params.density + (0.25 if strong else 0.0)
        if index > 0 and not density_rng.chance(min(1.0, keep_chance)):
            continue
        length = step * params.gate
        if params.rhythm_preset == "Ballad":
            length = min(step * 1.8 * params.gate, 2.0)
        slots.append(_Slot(start=start, length=length, strong=strong))
    return slots


def _is_strong(params: MelodyParams, start: float, free_rng: Rng) -> bool:
    """Where the accents (and therefore chord tones) land for each preset.

    Strong positions are expressed against the project's beats-per-bar so
    3/4 and 6/8 projects get sensible accents too.
    """
    preset = params.rhythm_preset
    bar = params.beats_per_bar
    pos = start % bar
    on_downbeat = pos < 1e-9
    on_midbar = abs(pos - bar / 2.0) < 1e-9
    offbeat = abs(start % 1.0 - 0.5) < 1e-9
    if preset in ("Eighths", "Sixteenths", "Ballad"):
        return on_downbeat or on_midbar
    if preset in ("Syncopated", "Offbeats"):
        return offbeat
    return free_rng.chance(0.5)  # Free


def chord_at(chords: list[ChordSpec], time: float) -> ChordSpec | None:
    """The chord sounding at ``time`` (beats), or None in a gap."""
    for chord in chords:
        if chord.contains(time):
            return chord
    return None


# ---------------------------------------------------------------------------
# Pitch selection
# ---------------------------------------------------------------------------


def _pitch_pool(params: MelodyParams, chord: ChordSpec) -> tuple[list[int], list[int]]:
    """Chord tones and scale tones inside the allowed range."""
    lo, hi = params.lo, params.hi
    chord_notes = chord_tones_in_range(chord.root, chord.template, lo, hi)
    scale_notes = scale_notes_in_range(params.tonic, scale_mask(params.scale_name), lo, hi)
    return chord_notes, scale_notes


def _nearest_in_pool(target: int, pool: list[int], rng: Rng) -> int | None:
    """Closest pool note to ``target``, with a touch of melodic variety.

    Ties (within a semitone) are broken randomly, and occasionally the second
    nearest tier wins instead so lines leap rather than crawl.
    """
    if not pool:
        return None
    distances = sorted({abs(note - target) for note in pool})
    tiers = [
        [note for note in pool if abs(note - target) == distance]
        for distance in distances[:2]
    ]
    tier = tiers[-1] if len(tiers) > 1 and rng.chance(0.25) else tiers[0]
    return rng.choice(tier)


def _guide_tones(chord: ChordSpec, chord_notes: list[int]) -> list[int]:
    """In-range chord tones that are the 3rd or 7th of the chord.

    Guide tones are the voice-leading skeleton of a progression, so the caller
    picks the one nearest the previous melody note. If the chord has neither
    (e.g. a power chord) every chord tone is fair game.
    """
    third = (chord.root + (4 if chord.template in _MAJOR_THIRD_TEMPLATES else 3)) % 12
    seventh = (chord.root + 10) % 12
    guides = [note for note in chord_notes if note % 12 in (third, seventh)]
    return guides or list(chord_notes)


def _closest_in_pool(target: int, pool: list[int]) -> int | None:
    """Deterministic nearest pool note (ties resolve to the lower pitch)."""
    if not pool:
        return None
    return min(pool, key=lambda note: (abs(note - target), note))


def _arch_target(params: MelodyParams, start: float, rng: Rng) -> int:
    """Mid-range anchor following a gentle arc over the progression."""
    timeline_start = min(chord.start for chord in params.chords)
    timeline_end = max(chord.end for chord in params.chords)
    span = max(timeline_end - timeline_start, 1e-9)
    phase = max(0.0, min(1.0, (start - timeline_start) / span))
    anchor = params.lo + (params.hi - params.lo) * (0.5 + 0.45 * math.sin(phase * math.pi))
    return int(anchor) + rng.choice([-2, -1, 0, 0, 1, 2])


def _snap_into_range(note: int, lo: int, hi: int) -> int:
    """Transpose by octaves until the note sits inside [lo, hi]."""
    if note < lo:
        steps = (lo - note + 11) // 12
        return note + 12 * steps
    if note > hi:
        steps = (note - hi + 11) // 12
        return note - 12 * steps
    return note


def _step_along(pool: list[int], note: int, steps: int) -> int:
    """Move ``steps`` positions along a sorted pool from the note nearest it.

    Stepping along the scale (rather than adding semitones) keeps lines
    melodic: a two-note move is a third in key, not a chromatic slide.
    """
    if not pool:
        return note
    index = min(range(len(pool)), key=lambda i: (abs(pool[i] - note), pool[i]))
    index = max(0, min(len(pool) - 1, index + steps))
    return pool[index]


def _avoid_repeat(pitch: int | None, previous_pitch: int | None, pool: list[int], rng: Rng) -> int | None:
    """Mostly stop a melody from sitting on the same note twice in a row."""
    if pitch is None or previous_pitch is None or pitch != previous_pitch:
        return pitch
    alternatives = [note for note in pool if note != pitch]
    if not alternatives or not rng.chance(0.65):
        return pitch
    return min(alternatives, key=lambda note: (abs(note - pitch), note))


def _choose_pitch(
    params: MelodyParams,
    rng: Rng,
    slot: _Slot,
    chord: ChordSpec,
    previous_pitch: int | None,
    motif_target: int | None,
) -> int | None:
    """Pick the pitch for one slot following the strategy and contour rules."""
    chord_notes, scale_notes = _pitch_pool(params, chord)
    allowed = sorted(set(chord_notes) | set(scale_notes))
    if not allowed:
        return None
    lo, hi = params.lo, params.hi

    # --- motif replay has first say: follow the target the interval implies ---
    # Replays stay faithful, so this snaps deterministically (no leap variety).
    if motif_target is not None:
        candidate = _snap_into_range(motif_target, lo, hi)
        return _closest_in_pool(candidate, allowed)

    melodic_pool = scale_notes or allowed

    # --- pick a target pitch from the contour --------------------------------
    middle = (lo + hi) // 2
    if params.contour == "Arch":
        target = _arch_target(params, slot.start, rng)
    elif params.contour == "Follow chords":
        guides = _guide_tones(chord, chord_notes)
        if previous_pitch is None or not guides:
            target = guides[len(guides) // 2] if guides else middle
        else:
            # Voice-leading: step to the nearest 3rd/7th of the new chord.
            target = _closest_in_pool(previous_pitch, guides) or middle
    elif params.contour == "Free":
        anchor = previous_pitch if previous_pitch is not None else middle
        target = _step_along(melodic_pool, anchor, rng.choice((-2, -1, -1, 1, 1, 2)))
    else:
        # 'Motif repeat' on its first pass: walk the scale so the motif has a
        # contour worth repeating.
        anchor = previous_pitch if previous_pitch is not None else middle
        target = _step_along(melodic_pool, anchor, rng.choice((-3, -2, -1, -1, 1, 1, 2, 3)))

    # --- strategy decides which pool to draw from -----------------------------
    if params.strategy == "Arpeggio":
        pool = chord_notes or allowed
        if previous_pitch is not None and previous_pitch in pool:
            above = [note for note in pool if note > previous_pitch]
            below = [note for note in pool if note < previous_pitch]
            direction = rng.choice([1, 1, 2])  # favor upward runs
            if direction >= 2 and below:
                return below[-1]
            if above:
                return above[0]
            if below:
                return below[0]
        return _avoid_repeat(_nearest_in_pool(target, pool, rng), previous_pitch, pool, rng)

    if params.strategy == "Guide tones":
        if slot.strong:
            pool = _guide_tones(chord, chord_notes) or allowed
            if previous_pitch is not None:
                return _closest_in_pool(previous_pitch, pool)
            return _nearest_in_pool(target, pool, rng)
        pool = scale_notes or allowed
        return _avoid_repeat(_nearest_in_pool(target, pool, rng), previous_pitch, pool, rng)

    if params.strategy == "Random walk":
        pool = allowed
        anchor = previous_pitch if previous_pitch is not None else target
        walk = _step_along(pool, anchor, rng.choice((-2, -1, -1, 1, 1, 2)))
        return _avoid_repeat(_closest_in_pool(walk, pool), previous_pitch, pool, rng)

    # "Chord + passing": chord tones on strong slots, scale tones elsewhere.
    if slot.strong:
        pool = chord_notes or allowed
    else:
        pool = scale_notes or allowed
    return _avoid_repeat(_nearest_in_pool(target, pool, rng), previous_pitch, pool, rng)


# ---------------------------------------------------------------------------
# Top-level generation
# ---------------------------------------------------------------------------

import math  # noqa: E402  (kept close to usage for readability)


def _motif_key(beats: float) -> int:
    """Grid-safe key for a position inside the motif (milli-beats).

    Slots live on 1/8 and 1/16 positions, so a plain int() would collapse
    them all onto the same key and destroy the motif.
    """
    return int(round(beats * 1000))


def generate_melody(params: MelodyParams) -> list[MelodyNote]:
    """Generate the full melody note list for the given parameters."""
    problems = params.validate()
    if problems:
        raise ValueError("; ".join(problems))

    rng = Rng(params.seed)
    slots = build_rhythm(params)
    timeline_start = min(chord.start for chord in params.chords)

    use_motif = params.contour == "Motif repeat" and params.motif_bars > 0
    motif_length = params.motif_bars * params.beats_per_bar
    source_pitches: dict[int, int] = {}  # motif position (milli-beats) -> pitch

    notes: list[MelodyNote] = []
    previous_pitch: int | None = None
    prev_source_pitch: int | None = None

    for slot in slots:
        chord = chord_at(params.chords, slot.start)
        if chord is None:
            previous_pitch = None
            continue

        motif_target: int | None = None
        offset = slot.start - timeline_start
        in_source_window = not use_motif or offset < motif_length - 1e-9
        if use_motif and not in_source_window:
            source_pitch = source_pitches.get(_motif_key(offset % motif_length))
            if source_pitch is None:
                continue  # the motif rested (or had no slot) here
            # Transpose the stored interval onto the current melodic position,
            # so the contour survives chord changes and octave snapping.
            if prev_source_pitch is not None and previous_pitch is not None:
                motif_target = previous_pitch + (source_pitch - prev_source_pitch)
            else:
                motif_target = source_pitch
            prev_source_pitch = source_pitch

        if not use_motif or in_source_window:
            if params.rest_chance and rng.chance(params.rest_chance):
                previous_pitch = None
                continue
            pitch = _choose_pitch(params, rng, slot, chord, previous_pitch, None)
            if use_motif and in_source_window and pitch is not None:
                source_pitches[_motif_key(offset)] = pitch
                prev_source_pitch = pitch
        else:
            pitch = _choose_pitch(params, rng, slot, chord, previous_pitch, motif_target)

        if pitch is None:
            previous_pitch = None
            continue

        velocity = params.velocity
        if params.velocity_jitter:
            velocity = min(
                1.0,
                max(0.1, velocity + rng.uniform(-params.velocity_jitter, params.velocity_jitter)),
            )
        jitter_ticks = 0
        if params.timing_jitter:
            jitter_ticks = int(round(rng.uniform(-params.timing_jitter, params.timing_jitter)))

        start_ticks = max(0, int(round(slot.start * params.ticks_per_beat)) + jitter_ticks)
        length_ticks = max(1, int(round(slot.length * params.ticks_per_beat)))
        notes.append(
            MelodyNote(
                pitch=pitch,
                start=start_ticks,
                length=length_ticks,
                velocity=velocity,
                color=params.melody_color,
            )
        )
        previous_pitch = pitch

    return _finalize(notes)


def _finalize(notes: list[MelodyNote]) -> list[MelodyNote]:
    """Sort by start time; drop exact duplicates (same start and pitch)."""
    notes = sorted(notes, key=lambda note: (note.start, note.pitch))
    result: list[MelodyNote] = []
    for note in notes:
        if result and result[-1].start == note.start and result[-1].pitch == note.pitch:
            continue
        result.append(note)
    return result
