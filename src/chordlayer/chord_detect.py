"""Chord detection: turn piano roll notes into a timeline of chords.

The detector slices the timeline at every note start, classifies the pitch
classes sounding in each slice (with a bass-note bonus from the lowest pitch),
merges adjacent slices with identical labels, and drops slices that are too
short to be musical.
"""

from __future__ import annotations

from dataclasses import dataclass

from .theory import ChordSpec, infer_key, match_chord, parse_chord_name


@dataclass(frozen=True)
class SimpleNote:
    """A note as seen by the detector (times in ticks, pitch = MIDI number)."""

    pitch: int
    start: int
    length: int
    selected: bool = False
    muted: bool = False

    @property
    def end(self) -> int:
        return self.start + self.length


@dataclass
class DetectionResult:
    chords: list[ChordSpec]
    tonic: int
    scale_name: str
    skipped_slices: int
    used_all_notes: bool

    @property
    def timeline_end(self) -> float:
        return max((chord.end for chord in self.chords), default=0.0)


def _slice_boundaries(notes: list[SimpleNote], window_end: float) -> list[float]:
    """Cut points for the timeline, always including 0 and ``window_end``.

    The final slice must survive, otherwise the last chord of a phrase is
    dropped (a bug this function exists to prevent).
    """
    cuts = {0.0, float(window_end)}
    for note in notes:
        start = float(note.start)
        end = float(note.end)
        if 0.0 < start < window_end:
            cuts.add(start)
        if 0.0 < end < window_end:
            cuts.add(end)
    return sorted(cuts)


def _covering_notes(notes: list[SimpleNote], start: float, end: float) -> list[SimpleNote]:
    return [
        note
        for note in notes
        if note.start <= start + 1e-9 and note.end >= end - 1e-9
    ]


def detect_chords(
    notes: list[SimpleNote],
    ticks_per_beat: float,
    min_length_beats: float = 0.25,
    progression_text: str | None = None,
) -> DetectionResult:
    """Detect the chord timeline from raw notes.

    ``progression_text`` (e.g. ``"Am F C G"``) bypasses detection entirely and
    lays the typed chords over the detected time span.
    """
    if ticks_per_beat <= 0:
        raise ValueError("ticks_per_beat must be positive")
    if progression_text and progression_text.strip():
        return _from_progression_text(notes, ticks_per_beat, progression_text.strip())
    if not notes:
        return DetectionResult([], 0, "Major (Ionian)", 0, True)

    window_end = max(note.end for note in notes)
    boundaries = _slice_boundaries(notes, float(window_end))
    if len(boundaries) < 2:
        boundaries = [0.0, float(window_end)]

    chords: list[ChordSpec] = []
    skipped = 0
    for start, end in zip(boundaries, boundaries[1:]):
        covering = _covering_notes(notes, start, end)
        if not covering:
            continue
        pitch_classes = {note.pitch % 12 for note in covering}
        bass = min(covering, key=lambda note: note.pitch).pitch % 12
        match = match_chord(pitch_classes, bass)
        if match is None:
            skipped += 1
            continue
        root, template, _score = match
        chords.append(
            ChordSpec(
                root=root,
                template=template,
                start=start / ticks_per_beat,
                length=(end - start) / ticks_per_beat,
            )
        )

    chords = _merge_adjacent(chords)
    chords = [chord for chord in chords if chord.length >= min_length_beats - 1e-9]
    chords = _merge_adjacent(chords)
    if not chords:
        return DetectionResult([], 0, "Major (Ionian)", skipped, True)

    tonic, scale_name = infer_key_from_chords(chords)
    return DetectionResult(chords, tonic, scale_name, skipped, True)


def infer_key_from_chords(chords: list[ChordSpec]) -> tuple[int, str]:  # noqa: C901
    """Infer (tonic, scale) from chord roots weighted by duration."""
    weights: dict[int, float] = {}
    for chord in chords:
        weights[chord.root] = weights.get(chord.root, 0.0) + chord.length
        # Thirds help tell major from minor contexts.
        third = (
            chord.root
            + (4 if chord.template in ("maj", "maj7", "7", "maj6", "aug", "add9", "9", "maj9") else 3)
        ) % 12
        weights[third] = weights.get(third, 0.0) + chord.length * 0.5
    tonic, scale_name = infer_key(weights)
    # Small heuristic: the first chord is usually the home chord.
    if chords:
        first = chords[0]
        first_key = (first.root, _relative_scale(first))
        if first_key != (tonic, scale_name):
            weights_first = dict(weights)
            weights_first[first.root] = weights_first.get(first.root, 0.0) + 1.0
            alt_tonic, alt_scale = infer_key(weights_first)
            if (alt_tonic, alt_scale) == first_key:
                return alt_tonic, alt_scale
    return tonic, scale_name


def _relative_scale(chord: ChordSpec) -> str:
    is_majorish = chord.template in ("maj", "maj7", "7", "maj6", "add9", "9", "maj9", "sus4", "sus2", "aug")
    return "Major (Ionian)" if is_majorish else "Natural Minor (Aeolian)"


def _merge_adjacent(chords: list[ChordSpec]) -> list[ChordSpec]:
    """Join consecutive chords that share root and template."""
    merged: list[ChordSpec] = []
    for chord in chords:
        if (
            merged
            and merged[-1].root == chord.root
            and merged[-1].template == chord.template
            and abs(merged[-1].end - chord.start) < 1e-9
        ):
            previous = merged[-1]
            merged[-1] = ChordSpec(
                root=previous.root,
                template=previous.template,
                start=previous.start,
                length=previous.length + chord.length,
            )
        else:
            merged.append(chord)
    return merged


def _from_progression_text(
    notes: list[SimpleNote], ticks_per_beat: float, text: str
) -> DetectionResult:
    """Lay typed chords over the span of the notes (or 4 bars when empty)."""
    from .theory import parse_progression

    chords = parse_progression(text)
    if notes:
        span_beats = max(note.end for note in notes) / ticks_per_beat
    else:
        span_beats = 16.0
    total = sum(chord.length for chord in chords)
    repeats = max(1, int(round(span_beats / total))) if total > 0 else 1
    tiled: list[ChordSpec] = []
    cursor = 0.0
    for _ in range(repeats):
        for chord in chords:
            tiled.append(
                ChordSpec(
                    root=chord.root,
                    template=chord.template,
                    start=cursor,
                    length=chord.length,
                )
            )
            cursor += chord.length
    tonic, scale_name = infer_key_from_chords(tiled)
    return DetectionResult(tiled, tonic, scale_name, 0, False)


def parse_chords_only(text: str) -> list[tuple[int, str]]:
    """Parse ``Am F C G`` into (root, template) pairs — used by the Analyzer."""
    pairs = []
    for token in text.replace(",", " ").split():
        pairs.append(parse_chord_name(token))
    return pairs
