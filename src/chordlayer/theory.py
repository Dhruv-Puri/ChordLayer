"""Music-theory helpers: pitch classes, scales, chord templates and parsing.

Everything here is pure Python with no dependencies. Pitch is represented as a
MIDI note number (FL Studio convention: 48 = C4, 60 = C5) and harmony as pitch
classes 0-11 with C = 0, C# = 1, ... B = 11.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Note naming helpers
# ---------------------------------------------------------------------------

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

_LETTER_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

_CHORD_TOKEN_RE = re.compile(
    r"^(?P<root>[A-Ga-g])(?P<accidental>[#b]*)(?P<quality>[A-Za-z0-9+\-°ø]*)"
    r"(?::(?P<length>[0-9]*\.?[0-9]+))?$"
)


def pitch_class_from_name(name: str) -> int:
    """Return the pitch class (0-11) for a note name like ``C``, ``Db`` or ``F##``."""
    letter = name[0].upper()
    if letter not in _LETTER_SEMITONE:
        raise ValueError(f"Unknown note name: {name!r}")
    value = _LETTER_SEMITONE[letter]
    for char in name[1:]:
        if char == "#":
            value += 1
        elif char == "b":
            value -= 1
        else:
            raise ValueError(f"Unknown accidental {char!r} in note name {name!r}")
    return value % 12


def note_name(note: int) -> str:
    """Human-readable note name for a MIDI note number.

    FL Studio's piano roll shows 48 as C4 and middle C (60) as C5, so the
    displayed octave is simply ``note // 12``.
    """
    return f"{NOTE_NAMES[note % 12]}{note // 12}"


# ---------------------------------------------------------------------------
# Scales
# ---------------------------------------------------------------------------

# Masks are 12 booleans indexed by pitch class offset from the tonic;
# 1 means the pitch class belongs to the scale.
SCALES: dict[str, tuple[int, ...]] = {
    "Major (Ionian)": (1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1),
    "Natural Minor (Aeolian)": (1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1, 0),
    "Harmonic Minor": (1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 0, 1),
    "Dorian": (1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 0),
    "Phrygian": (1, 1, 0, 1, 0, 1, 0, 1, 1, 0, 1, 0),
    "Lydian": (1, 0, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1),
    "Mixolydian": (1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0),
    "Locrian": (1, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 0),
    "Major Pentatonic": (1, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 0),
    "Minor Pentatonic": (1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 0),
    "Chromatic": (1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1),
}

# Scales considered when inferring a key from the chords.
KEY_INFERENCE_SCALES = ("Major (Ionian)", "Natural Minor (Aeolian)")


def scale_mask(name: str) -> tuple[int, ...]:
    """Return the 12-slot mask for a scale name (raises for unknown names)."""
    try:
        return SCALES[name]
    except KeyError:
        raise ValueError(
            f"Unknown scale {name!r}. Known scales: {', '.join(SCALES)}"
        ) from None


def scale_pitch_classes(tonic: int, mask: tuple[int, ...]) -> tuple[int, ...]:
    """Ascending pitch classes of a scale with the given tonic."""
    return tuple((tonic + step) % 12 for step in range(12) if mask[step % 12])


def is_in_scale(pitch_class: int, tonic: int, mask: tuple[int, ...]) -> bool:
    return bool(mask[(pitch_class - tonic) % 12])


def scale_notes_in_range(
    tonic: int, mask: tuple[int, ...], lo: int, hi: int
) -> list[int]:
    """All MIDI notes of the scale within the inclusive range ``lo``..``hi``."""
    notes = []
    for note in range(lo, hi + 1):
        if mask[(note - tonic) % 12]:
            notes.append(note)
    return notes


def infer_key(weights: dict[int, float]) -> tuple[int, str]:
    """Best-fit (tonic, scale name) for weighted pitch classes.

    ``weights`` maps pitch class (0-11) to a non-negative duration/weight.
    Scales that explain more of the weighted pitch material win; a small bonus
    prefers tonics that are actually present.
    """
    if not weights:
        return 0, KEY_INFERENCE_SCALES[0]
    total = sum(weights.values()) or 1.0
    best = (-1e9, 0, KEY_INFERENCE_SCALES[0])
    for tonic in range(12):
        tonic_weight = weights.get(tonic, 0.0)
        # A tonic that is both present and prominent wins ties between
        # relative keys (Am F C G is A minor, not C major).
        present_bonus = 0.15 + 0.9 * (tonic_weight / total)
        for scale_name in KEY_INFERENCE_SCALES:
            mask = SCALES[scale_name]
            inside = sum(
                weight
                for pitch_class, weight in weights.items()
                if mask[(pitch_class - tonic) % 12]
            )
            score = inside / total + present_bonus
            if score > best[0] + 1e-9:
                best = (score, tonic, scale_name)
    return best[1], best[2]


# ---------------------------------------------------------------------------
# Chord templates
# ---------------------------------------------------------------------------

# Interval sets (in semitones from the root). Matching works on pitch classes,
# so compound intervals (9ths, 11ths) collapse to their simple equivalents.
CHORD_TEMPLATES: dict[str, frozenset[int]] = {
    "5": frozenset({0, 7}),
    "maj": frozenset({0, 4, 7}),
    "min": frozenset({0, 3, 7}),
    "dim": frozenset({0, 3, 6}),
    "aug": frozenset({0, 4, 8}),
    "sus2": frozenset({0, 2, 7}),
    "sus4": frozenset({0, 5, 7}),
    "7sus4": frozenset({0, 5, 7, 10}),
    "maj6": frozenset({0, 4, 7, 9}),
    "min6": frozenset({0, 3, 7, 9}),
    "7": frozenset({0, 4, 7, 10}),
    "maj7": frozenset({0, 4, 7, 11}),
    "min7": frozenset({0, 3, 7, 10}),
    "min7b5": frozenset({0, 3, 6, 10}),
    "dim7": frozenset({0, 3, 6, 9}),
    "add9": frozenset({0, 2, 4, 7}),
    "9": frozenset({0, 2, 4, 7, 10}),
    "maj9": frozenset({0, 2, 4, 7, 11}),
    "min9": frozenset({0, 2, 3, 7, 10}),
}

# Preferred display suffix for each template.
CHORD_SUFFIXES: dict[str, str] = {
    "5": "5",
    "maj": "",
    "min": "m",
    "dim": "dim",
    "aug": "aug",
    "sus2": "sus2",
    "sus4": "sus4",
    "7sus4": "7sus4",
    "maj6": "6",
    "min6": "m6",
    "7": "7",
    "maj7": "maj7",
    "min7": "m7",
    "min7b5": "m7b5",
    "dim7": "dim7",
    "add9": "add9",
    "9": "9",
    "maj9": "maj9",
    "min9": "m9",
}

# Every spelling users might type, mapped to a canonical template.
QUALITY_ALIASES: dict[str, str] = {
    "": "maj",
    "M": "maj",
    "maj": "maj",
    "major": "maj",
    "m": "min",
    "min": "min",
    "minor": "min",
    "-": "min",
    "-7": "min7",
    "dim": "dim",
    "o": "dim",
    "o7": "dim7",
    "°": "dim",
    "°7": "dim7",
    "aug": "aug",
    "+": "aug",
    "sus2": "sus2",
    "sus4": "sus4",
    "sus": "sus4",
    "4": "sus4",
    "5": "5",
    "6": "maj6",
    "m6": "min6",
    "min6": "min6",
    "-6": "min6",
    "7": "7",
    "dom7": "7",
    "maj7": "maj7",
    "M7": "maj7",
    "Δ": "maj7",
    "m7": "min7",
    "min7": "min7",
    "m7b5": "min7b5",
    "ø": "min7b5",
    "ø7": "min7b5",
    "dim7": "dim7",
    "7sus4": "7sus4",
    "add9": "add9",
    "add2": "add9",
    "9": "9",
    "maj9": "maj9",
    "M9": "maj9",
    "m9": "min9",
    "min9": "min9",
    "-9": "min9",
}


def chord_pitch_classes(root: int, template: str) -> tuple[int, ...]:
    """Sorted pitch classes of a chord (root is a pitch class)."""
    intervals = CHORD_TEMPLATES[template]
    return tuple(sorted({(root + interval) % 12 for interval in intervals}))


def format_chord(root: int, template: str) -> str:
    """Canonical label such as ``C#m7`` for a root pitch class and template."""
    if template not in CHORD_TEMPLATES:
        raise ValueError(f"Unknown chord template {template!r}")
    return f"{NOTE_NAMES[root % 12]}{CHORD_SUFFIXES[template]}"


@dataclass(frozen=True)
class ChordSpec:
    """A chord with a position in musical time (beats)."""

    root: int  # pitch class of the root
    template: str  # key into CHORD_TEMPLATES
    start: float  # start time in beats
    length: float  # length in beats

    @property
    def label(self) -> str:
        return format_chord(self.root, self.template)

    @property
    def end(self) -> float:
        return self.start + self.length

    def contains(self, time: float) -> bool:
        return self.start <= time < self.end


def parse_chord_name(token: str) -> tuple[int, str]:
    """Parse ``C#m7`` / ``Bb`` / ``F5`` into (root pitch class, template)."""
    token = token.strip()
    if not token:
        raise ValueError("Empty chord name")
    match = _CHORD_TOKEN_RE.match(token)
    if not match:
        raise ValueError(f"Cannot parse chord name: {token!r}")
    root = pitch_class_from_name(match.group("root") + (match.group("accidental") or ""))
    quality = match.group("quality") or ""
    template = QUALITY_ALIASES.get(quality) or QUALITY_ALIASES.get(quality.lower())
    if template is None:
        raise ValueError(
            f"Unknown chord quality {quality!r} in {token!r}. "
            f"Examples: Am, Cmaj7, F#5, Bb, Gsus4, Dm7b5, Cadd9"
        )
    return root, template


def parse_progression(text: str, default_beats: float = 4.0) -> list[ChordSpec]:
    """Parse a typed progression like ``Am F C G`` or ``Dm7:4 Gm:2 A7:2``.

    Each chord occupies one bar (``default_beats``) unless ``:n`` specifies a
    duration in beats. Tokens are separated by whitespace and/or commas.
    """
    chords: list[ChordSpec] = []
    cursor = 0.0
    for raw in re.split(r"[\s,]+", text.strip()):
        if not raw:
            continue
        match = _CHORD_TOKEN_RE.match(raw)
        if not match or ":" in raw and match.group("length") is None:
            # Let parse_chord_name produce a precise error message.
            parse_chord_name(raw)
            raise ValueError(f"Cannot parse chord: {raw!r}")
        root, template = parse_chord_name(raw)
        length_token = match.group("length")
        length = float(length_token) if length_token else default_beats
        if length <= 0:
            raise ValueError(f"Chord {raw!r} must have a positive length")
        chords.append(ChordSpec(root=root, template=template, start=cursor, length=length))
        cursor += length
    if not chords:
        raise ValueError(
            "No chords in the progression text - try something like 'Am F C G'."
        )
    return chords


# ---------------------------------------------------------------------------
# Small numeric helpers shared by detection and generation
# ---------------------------------------------------------------------------


def nearest_note(target: int, candidates) -> int:
    """The candidate closest to ``target`` (ties resolve to the lower note)."""
    best = None
    best_distance = None
    for candidate in candidates:
        distance = abs(candidate - target)
        if best is None or distance < best_distance:
            best = candidate
            best_distance = distance
    if best is None:
        raise ValueError("nearest_note() needs at least one candidate")
    return best


def chord_tones_in_range(root: int, template: str, lo: int, hi: int) -> list[int]:
    """All MIDI notes of the chord within the inclusive range ``lo``..``hi``."""
    pitch_classes = chord_pitch_classes(root, template)
    return [note for note in range(lo, hi + 1) if note % 12 in pitch_classes]


def match_chord(
    pitch_classes: set[int], bass_pitch_class: int | None = None
) -> tuple[int, str, float] | None:
    """Score ``pitch_classes`` against every template/root pair.

    Returns ``(root, template, score)`` for the best match, or ``None`` when
    nothing plausible fits. The score rewards template coverage and penalises
    pitches the template cannot explain; an exact match always wins, and the
    bass note gets a small bonus for predicting the root.
    """
    if not pitch_classes:
        return None
    best: tuple[int, str, float] | None = None
    normalized = {pitch_class % 12 for pitch_class in pitch_classes}
    for root in range(12):
        for template, intervals in CHORD_TEMPLATES.items():
            template_pcs = {(root + interval) % 12 for interval in intervals}
            uncovered = len(template_pcs - normalized)
            extras = len(normalized - template_pcs)
            if uncovered:
                # Only exact sets (or subsets that drop colour tones) match.
                if uncovered > 1 or extras:
                    continue
                score = 0.5 - 0.25 * uncovered
            else:
                score = 1.0 - 0.35 * extras
            if bass_pitch_class is not None and bass_pitch_class == root:
                score += 0.2
            if best is None or score > best[2] + 1e-9:
                best = (root, template, score)
    if best is None or best[2] <= 0.0:
        return None
    return best
