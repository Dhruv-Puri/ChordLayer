"""Instrument-aware arranging: one idiomatic MIDI part per instrument.

A musician should not have to tune a pile of knobs to get usable MIDI. Name the
instruments (``808s, flute, violin, pad``) and each one gets the register, note
length, articulation, density and pattern that the instrument really plays:

    bass      root-driven low end (808s, sub, bass) - mono, long, gliding tails
    keys      voice-led chord stabs on a rhythm grid (piano, organ, guitar)
    pad       sustained voice-led chord voicings (pads, strings, choir)
    lead      the melody line (flute, violin, brass, synth lead)
    counter   a second, sparser line that answers the lead
    arp       chord-tone patterns (arp, pluck, bell, harp)
    drums     kick / snare / hats / clap / toms written against the song's tempo,
              bass onsets and chord changes, so the groove fits the arrangement

The arranger also allocates registers so named instruments do not fight for the
same octave. Everything is deterministic from the song seed, so Re-roll rewrites
the whole song and the same seed always rebuilds the same one.

Naming ``drums`` (or ``drum kit``) expands into the kit pieces - kick, snare and
hats - each as its own part, so a drum line-up arrives in FL as separate drum
channels or as one MIDI file per piece.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field, replace

from .generator import (
    CONTOURS,
    RHYTHM_PRESETS,
    STRATEGIES,
    MelodyNote,
    MelodyParams,
    build_rhythm,
    chord_at,
    generate_melody,
)
from .rng import Rng
from .theory import (
    ChordSpec,
    chord_pitch_classes,
    chord_tones_in_range,
    nearest_note,
    note_name,
)

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

ROLE_BASS = "bass"
ROLE_KEYS = "keys"
ROLE_PAD = "pad"
ROLE_LEAD = "lead"
ROLE_COUNTER = "counter"
ROLE_ARP = "arp"
ROLE_DRUMS = "drums"

ROLES = (ROLE_BASS, ROLE_KEYS, ROLE_PAD, ROLE_LEAD, ROLE_COUNTER, ROLE_ARP, ROLE_DRUMS)

# Which octaves compete with which: the arranger spreads parts inside a group.
_REGISTER_GROUPS = {
    ROLE_BASS: "low",
    ROLE_KEYS: "mid",
    ROLE_PAD: "mid",
    ROLE_LEAD: "high",
    ROLE_COUNTER: "high",
    ROLE_ARP: "high",
    # Drums keep their own group: their pitches are fixed by the GM drum map.
    ROLE_DRUMS: "drums",
}

# ---------------------------------------------------------------------------
# General MIDI drum map (the pitches a drum channel expects from a MIDI file)
# ---------------------------------------------------------------------------

DRUM_KICK = 36
DRUM_SNARE = 38
DRUM_CLAP = 39
DRUM_CLOSED_HAT = 42
DRUM_OPEN_HAT = 46
DRUM_CRASH = 49
DRUM_TOMS = (50, 47, 45, 43)

# Every pitch a piece can play, so a drum part never leaves the drum map.
DRUM_PIECES: dict[str, tuple[int, ...]] = {
    "kick": (DRUM_KICK,),
    "snare": (DRUM_SNARE,),
    "clap": (DRUM_CLAP,),
    "hats": (DRUM_CLOSED_HAT, DRUM_OPEN_HAT, DRUM_CRASH),
    "toms": DRUM_TOMS,
}

# Naming one of these expands into the whole kit.
DRUM_KITS: dict[str, tuple[str, ...]] = {
    "drums": ("kick", "snare", "hats"),
    "fullkit": ("kick", "snare", "hats", "clap", "toms"),
}

# Preview colours, one per part (hex for the window, 0-15 for the piano roll).
PART_COLORS = (
    ("#4da3ff", 15),
    ("#ff7ab6", 11),
    ("#7fe08a", 10),
    ("#ffc266", 4),
    ("#c08cff", 6),
    ("#5ce1e6", 7),
    ("#ff8f6b", 3),
    ("#a6e3a1", 13),
)

# Keep every part inside FL's playable range: C1 (12) up to C9 (108).
LOWEST_PITCH = 12
HIGHEST_PITCH = 108


class InstrumentError(ValueError):
    """An instrument name ChordLayer does not know."""


# ---------------------------------------------------------------------------
# Instrument profiles: the whole "preset" for one instrument
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstrumentProfile:
    """Everything the arranger needs to write one instrument's part.

    These are *not* user knobs - they encode how the instrument is played.
    ``center_octave`` is an FL Studio octave (48 = C4), so a profile with
    ``center_octave=2, range_semitones=12`` lives between FL's C1 and C3.
    """

    key: str
    label: str
    role: str
    aliases: tuple[str, ...] = ()
    blurb: str = ""
    center_octave: int = 5
    range_semitones: int = 12
    rhythm: str = "Eighths"
    density: float = 0.8
    rest_chance: float = 0.1
    gate: float = 0.9
    strategy: str = "Chord + passing"
    contour: str = "Arch"
    velocity: float = 0.8
    velocity_jitter: float = 0.05
    timing_jitter: float = 0.0
    glide: float = 0.0
    max_voices: int = 1
    strum_ticks: int = 0
    # Non-empty on a kit name such as "drums": the pieces it stands for.
    kit: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"Unknown role {self.role!r} for instrument {self.key!r}")
        if self.rhythm not in RHYTHM_PRESETS:
            raise ValueError(f"Unknown rhythm {self.rhythm!r} for {self.key!r}")
        if self.strategy not in STRATEGIES:
            raise ValueError(f"Unknown strategy {self.strategy!r} for {self.key!r}")
        if self.contour not in CONTOURS:
            raise ValueError(f"Unknown contour {self.contour!r} for {self.key!r}")

    @property
    def lo(self) -> int:
        return self.center_octave * 12 - self.range_semitones

    @property
    def hi(self) -> int:
        return self.center_octave * 12 + self.range_semitones

    @property
    def register_label(self) -> str:
        return f"{note_name(self.lo)}..{note_name(self.hi)}"


def _derived(base: InstrumentProfile, key: str, label: str, **changes) -> InstrumentProfile:
    """A profile that keeps ``base``'s playing style but changes a few facts."""
    return replace(base, key=key, label=label, **changes)


# -- low end -----------------------------------------------------------------

_808S = InstrumentProfile(
    key="808s",
    label="808s",
    role=ROLE_BASS,
    aliases=("808", "808s", "808 bass", "boom 808", "trap 808"),
    blurb="root notes that hold and slide into the next one",
    center_octave=2,
    rhythm="Syncopated",
    density=0.3,
    rest_chance=0.05,
    gate=1.0,
    velocity=0.95,
    velocity_jitter=0.02,
    glide=0.3,
)

SUB = InstrumentProfile(
    key="sub",
    label="Sub bass",
    role=ROLE_BASS,
    aliases=("sub", "sub bass", "subbass", "deep sub", "sub bass line"),
    blurb="long, plain root notes under the arrangement",
    center_octave=2,
    range_semitones=8,
    rhythm="Eighths",
    density=0.45,
    rest_chance=0.0,
    gate=1.0,
    velocity=0.9,
    velocity_jitter=0.02,
    glide=0.12,
)

BASS = InstrumentProfile(
    key="bass",
    label="Bass",
    role=ROLE_BASS,
    aliases=(
        "bass",
        "bassline",
        "bass line",
        "synth bass",
        "bass guitar",
        "finger bass",
        "electric bass",
        "reese",
        "reese bass",
    ),
    blurb="root/fifth line with a little movement",
    center_octave=3,
    rhythm="Eighths",
    density=0.8,
    rest_chance=0.05,
    gate=0.85,
    velocity=0.85,
    velocity_jitter=0.04,
    glide=0.06,
)

# -- chords / comping --------------------------------------------------------

PIANO = InstrumentProfile(
    key="piano",
    label="Piano",
    role=ROLE_KEYS,
    aliases=(
        "piano",
        "keys",
        "keyboard",
        "rhodes",
        "electric piano",
        "e-piano",
        "wurlitzer",
        "clav",
        "clavinet",
        "acoustic piano",
        "grand piano",
    ),
    blurb="voice-led chord stabs",
    center_octave=4,
    rhythm="Eighths",
    density=0.75,
    rest_chance=0.0,
    gate=0.55,
    velocity=0.75,
    velocity_jitter=0.06,
    timing_jitter=4,
    max_voices=4,
)

ORGAN = _derived(
    PIANO,
    "organ",
    "Organ",
    aliases=("organ", "hammond", "drawbar organ", "church organ", "b3"),
    blurb="sustained chord stabs",
    density=0.65,
    gate=0.95,
    max_voices=3,
    velocity=0.7,
)

GUITAR = _derived(
    PIANO,
    "guitar",
    "Guitar",
    aliases=(
        "guitar",
        "acoustic guitar",
        "electric guitar",
        "rhythm guitar",
        "strum",
        "strummed guitar",
    ),
    blurb="strummed chords",
    density=0.7,
    gate=0.45,
    max_voices=4,
    velocity=0.72,
    strum_ticks=14,
)

PAD = InstrumentProfile(
    key="pad",
    label="Pad",
    role=ROLE_PAD,
    aliases=(
        "pad",
        "warm pad",
        "synth pad",
        "sustained",
        "sustained pad",
        "ambient pad",
    ),
    blurb="sustained voice-led chords",
    center_octave=5,
    rhythm="Ballad",
    density=1.0,
    rest_chance=0.0,
    gate=0.97,
    velocity=0.6,
    velocity_jitter=0.03,
    max_voices=4,
)

STRINGS = _derived(
    PAD,
    "strings",
    "Strings",
    aliases=(
        "strings",
        "string section",
        "orchestral strings",
        "ensemble",
        "violins",
        "cello section",
        "choir",
        "oohs",
        "aahs",
        "vocal pad",
    ),
    blurb="sustained section chords",
    gate=0.98,
    velocity=0.72,
    max_voices=4,
)

# -- melody lines ------------------------------------------------------------

FLUTE = InstrumentProfile(
    key="flute",
    label="Flute",
    role=ROLE_LEAD,
    aliases=("flute", "piccolo", "recorder", "whistle", "pan flute"),
    blurb="breathy arch-shaped melody",
    center_octave=6,
    rhythm="Eighths",
    density=0.7,
    rest_chance=0.15,
    gate=0.85,
    strategy="Chord + passing",
    contour="Arch",
    velocity=0.72,
    velocity_jitter=0.05,
    timing_jitter=6,
)

VIOLIN = InstrumentProfile(
    key="violin",
    label="Violin",
    role=ROLE_LEAD,
    aliases=("violin", "fiddle", "solo violin", "viola"),
    blurb="legato line that follows the voice leading",
    center_octave=6,
    rhythm="Eighths",
    density=0.75,
    rest_chance=0.12,
    gate=1.0,
    strategy="Guide tones",
    contour="Follow chords",
    velocity=0.78,
    velocity_jitter=0.04,
    timing_jitter=5,
)

BRASS = InstrumentProfile(
    key="brass",
    label="Brass",
    role=ROLE_LEAD,
    aliases=("brass", "trumpet", "horn", "sax", "saxophone", "trombone", "flugel"),
    blurb="syncopated, punchy melody",
    center_octave=5,
    rhythm="Syncopated",
    density=0.7,
    rest_chance=0.18,
    gate=0.7,
    strategy="Chord + passing",
    contour="Free",
    velocity=0.85,
    velocity_jitter=0.05,
)

CELLO = _derived(
    VIOLIN,
    "cello",
    "Cello",
    aliases=("cello", "violoncello", "low strings"),
    blurb="warm low line",
    center_octave=4,
    rhythm="Ballad",
    density=0.6,
)

LEAD = InstrumentProfile(
    key="lead",
    label="Lead synth",
    role=ROLE_LEAD,
    aliases=("lead", "synth lead", "saw lead", "square lead", "supersaw", "lead synth"),
    blurb="busy hook line",
    center_octave=5,
    rhythm="Sixteenths",
    density=0.85,
    rest_chance=0.08,
    gate=0.8,
    strategy="Chord + passing",
    contour="Free",
    velocity=0.8,
    velocity_jitter=0.05,
)

HARMONY = InstrumentProfile(
    key="harmony",
    label="Harmony line",
    role=ROLE_COUNTER,
    aliases=("harmony", "counter", "counter melody", "countermelody", "backing line"),
    blurb="sparse second line under the melody",
    center_octave=5,
    rhythm="Eighths",
    density=0.55,
    rest_chance=0.35,
    gate=0.9,
    strategy="Guide tones",
    contour="Follow chords",
    velocity=0.65,
    velocity_jitter=0.04,
    timing_jitter=6,
)

# -- patterns ----------------------------------------------------------------

BELL = InstrumentProfile(
    key="bell",
    label="Bell",
    role=ROLE_ARP,
    aliases=("bell", "bells", "glockenspiel", "music box", "chimes", "celesta"),
    blurb="sparse high chord tones",
    center_octave=7,
    rhythm="Eighths",
    density=0.55,
    rest_chance=0.0,
    gate=0.8,
    velocity=0.6,
    velocity_jitter=0.05,
)

PLUCK = InstrumentProfile(
    key="pluck",
    label="Pluck",
    role=ROLE_ARP,
    aliases=(
        "pluck",
        "plucky",
        "pluck synth",
        "marimba",
        "kalimba",
        "staccato synth",
        "pizzicato",
    ),
    blurb="short sixteenth-note pattern",
    center_octave=6,
    rhythm="Sixteenths",
    density=0.8,
    rest_chance=0.0,
    gate=0.5,
    velocity=0.75,
    velocity_jitter=0.05,
)

HARP = InstrumentProfile(
    key="harp",
    label="Harp",
    role=ROLE_ARP,
    aliases=("harp", "arpeggio guitar", "fingerpicked guitar", "guitar arpeggio"),
    blurb="ringing eighth-note rolls",
    center_octave=5,
    rhythm="Eighths",
    density=0.85,
    rest_chance=0.0,
    gate=0.9,
    velocity=0.7,
    velocity_jitter=0.05,
    strum_ticks=10,
)

ARP = InstrumentProfile(
    key="arp",
    label="Arp",
    role=ROLE_ARP,
    aliases=("arp", "arpeggio", "arpeggiator", "sequence", "synth arp"),
    blurb="up/down chord-tone pattern",
    center_octave=5,
    rhythm="Sixteenths",
    density=0.85,
    rest_chance=0.0,
    gate=0.7,
    velocity=0.72,
    velocity_jitter=0.05,
)

# -- drums --------------------------------------------------------------------
# Drum pitches are fixed by the GM map, so these profiles set no register: the
# groove itself (feel, accents, fills) comes from the song, not from knobs.

KICK = InstrumentProfile(
    key="kick",
    label="Kick",
    role=ROLE_DRUMS,
    aliases=("kick", "kicks", "kick drum", "kick pattern", "bass drum", "bd", "808 kick"),
    blurb="kick pattern locked to the bass line, with a break at the end of each phrase",
    center_octave=3,
    range_semitones=0,
    rhythm="Sixteenths",
    density=1.0,
    rest_chance=0.0,
    gate=0.4,
    strategy="Chord + passing",
    contour="Free",
    velocity=1.0,
    velocity_jitter=0.03,
)

SNARE = InstrumentProfile(
    key="snare",
    label="Snare",
    role=ROLE_DRUMS,
    aliases=("snare", "snares", "snare drum", "snare pattern", "sd", "backbeat"),
    blurb="backbeat with ghost notes and the phrase-end fill",
    center_octave=3,
    range_semitones=2,
    rhythm="Sixteenths",
    density=1.0,
    rest_chance=0.0,
    gate=0.4,
    strategy="Chord + passing",
    contour="Free",
    velocity=0.9,
    velocity_jitter=0.05,
)

HATS = InstrumentProfile(
    key="hats",
    label="Hi-hats",
    role=ROLE_DRUMS,
    aliases=(
        "hats",
        "hat",
        "hihat",
        "hihats",
        "hi hat",
        "hi hats",
        "hi-hat",
        "hi-hats",
        "closed hats",
        "hh",
    ),
    blurb="hat grid with open hats and a crash on every phrase",
    center_octave=3,
    range_semitones=8,
    rhythm="Sixteenths",
    density=1.0,
    rest_chance=0.0,
    gate=0.3,
    strategy="Chord + passing",
    contour="Free",
    velocity=0.6,
    velocity_jitter=0.05,
)

CLAP = InstrumentProfile(
    key="clap",
    label="Clap",
    role=ROLE_DRUMS,
    aliases=("clap", "claps", "handclap", "snap", "fingersnap", "clap pattern"),
    blurb="claps doubling the backbeat",
    center_octave=3,
    range_semitones=3,
    rhythm="Sixteenths",
    density=1.0,
    rest_chance=0.0,
    gate=0.3,
    strategy="Chord + passing",
    contour="Free",
    velocity=0.8,
    velocity_jitter=0.06,
)

TOMS = InstrumentProfile(
    key="toms",
    label="Toms",
    role=ROLE_DRUMS,
    aliases=("toms", "tom", "tom fill", "tom fills", "floor tom", "tom run"),
    blurb="descending tom fill at the end of each phrase",
    center_octave=4,
    range_semitones=7,
    rhythm="Sixteenths",
    density=1.0,
    rest_chance=0.0,
    gate=0.4,
    strategy="Chord + passing",
    contour="Free",
    velocity=0.8,
    velocity_jitter=0.05,
)

DRUMS = InstrumentProfile(
    key="drums",
    label="Drums",
    role=ROLE_DRUMS,
    aliases=(
        "drums",
        "drum",
        "drum kit",
        "drumkit",
        "kit",
        "beat",
        "drum pattern",
        "drum loop",
        "percussion",
    ),
    blurb="kick, snare and hats written against the song's groove",
    center_octave=3,
    range_semitones=8,
    rhythm="Sixteenths",
    density=1.0,
    rest_chance=0.0,
    gate=0.4,
    strategy="Chord + passing",
    contour="Free",
    velocity=0.85,
    velocity_jitter=0.05,
    kit=DRUM_KITS["drums"],
)

FULL_KIT = _derived(
    DRUMS,
    "fullkit",
    "Full kit",
    aliases=("full kit", "full drums", "drum set", "drumset", "drum kit with fills"),
    blurb="kick, snare, hats, claps and tom fills",
    kit=DRUM_KITS["fullkit"],
)

# The registry order is also the order shown in the UI.
INSTRUMENTS: dict[str, InstrumentProfile] = {
    profile.key: profile
    for profile in (
        _808S,
        SUB,
        BASS,
        PIANO,
        ORGAN,
        GUITAR,
        PAD,
        STRINGS,
        FLUTE,
        VIOLIN,
        BRASS,
        CELLO,
        LEAD,
        HARMONY,
        BELL,
        PLUCK,
        HARP,
        ARP,
        DRUMS,
        FULL_KIT,
        KICK,
        SNARE,
        HATS,
        CLAP,
        TOMS,
    )
}

DEFAULT_INSTRUMENTS = ("808s", "pad", "flute", "drums")


def _build_alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for key, profile in INSTRUMENTS.items():
        index.setdefault(key, key)
        for alias in profile.aliases:
            index.setdefault(_normalize_name(alias), key)
    return index


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


_ALIAS_INDEX = _build_alias_index()
_SPLIT_RE = re.compile(r"[,/+&;]|\band\b|\bwith\b|\bplus\b", re.IGNORECASE)


def instrument_choices() -> list[str]:
    """Canonical instrument names, for dialogs and help text."""
    return [profile.label for profile in INSTRUMENTS.values()]


def resolve_instrument(name: str) -> InstrumentProfile:
    """Look up one instrument by canonical name or any alias.

    Multi-word aliases are matched longest-first, and a fuzzy suggestion is
    added when nothing matches (``kazoo`` -> "Did you mean ...").
    """
    normalized = _normalize_name(name)
    if not normalized:
        raise InstrumentError("Instrument name is empty.")
    key = _ALIAS_INDEX.get(normalized)
    if key is not None:
        return INSTRUMENTS[key]
    # Handles descriptions like "warm pad" or "808s (main)".
    for alias in sorted(_ALIAS_INDEX, key=len, reverse=True):
        if alias and alias in normalized:
            return INSTRUMENTS[_ALIAS_INDEX[alias]]
    close = difflib.get_close_matches(normalized, list(_ALIAS_INDEX), n=1, cutoff=0.6)
    hint = f" Did you mean {INSTRUMENTS[_ALIAS_INDEX[close[0]]].label!r}?" if close else ""
    raise InstrumentError(
        f"I don't know the instrument {name!r}.{hint} Try one of: "
        + ", ".join(instrument_choices())
    )


def expand_kits(keys) -> tuple[str, ...]:
    """Replace a kit name (``"drums"``) with the pieces it stands for."""
    expanded: list[str] = []
    for key in keys:
        profile = INSTRUMENTS.get(key)
        pieces = profile.kit if profile is not None else ()
        for piece in pieces or (key,):
            if piece not in expanded:
                expanded.append(piece)
    return tuple(expanded)


def parse_instruments(text: str) -> tuple[str, ...]:
    """Turn ``"808s, flute and violin"`` into canonical instrument keys.

    Unknown names raise :class:`InstrumentError` naming every one that failed,
    so a typo is never silently dropped from the arrangement. Kit names expand
    here, so ``"drums"`` becomes kick + snare + hats.
    """
    chunks = [chunk.strip() for chunk in _SPLIT_RE.split(text or "") if chunk.strip()]
    if not chunks:
        raise InstrumentError(
            "Name at least one instrument, e.g. '808s, pad, flute'."
        )
    keys: list[str] = []
    unknown: list[str] = []
    for chunk in chunks:
        try:
            key = resolve_instrument(chunk).key
        except InstrumentError:
            unknown.append(chunk)
            continue
        if key not in keys:
            keys.append(key)
    if unknown:
        quoted = ", ".join(repr(name) for name in unknown)
        raise InstrumentError(
            f"I don't know the instrument {quoted}. Try: " + ", ".join(instrument_choices())
        )
    return expand_kits(keys)


# ---------------------------------------------------------------------------
# Arranging context and result
# ---------------------------------------------------------------------------


@dataclass
class _Song:
    """Shared musical facts every part is written against."""

    chords: list[ChordSpec]
    tonic: int
    scale_name: str
    ticks_per_beat: float
    beats_per_bar: float
    seed: int = 1

    @property
    def start(self) -> float:
        return min(chord.start for chord in self.chords)

    @property
    def end(self) -> float:
        return max(chord.end for chord in self.chords)


@dataclass
class InstrumentPart:
    """One instrument's notes, ready for a MIDI track or the preview."""

    key: str
    label: str
    role: str
    notes: list[MelodyNote] = field(default_factory=list)
    color: str = PART_COLORS[0][0]
    color_id: int = PART_COLORS[0][1]
    center_octave: int = 5
    range_semitones: int = 12
    register_label: str = ""
    blurb: str = ""
    register_note: str = ""
    # Drum pieces are fixed to GM drum-map pitches, so they state the pitches
    # they use instead of a movable register.
    pitch_lo: int | None = None
    pitch_hi: int | None = None

    @property
    def lo(self) -> int:
        if self.pitch_lo is not None:
            return self.pitch_lo
        return self.center_octave * 12 - self.range_semitones

    @property
    def hi(self) -> int:
        if self.pitch_hi is not None:
            return self.pitch_hi
        return self.center_octave * 12 + self.range_semitones

    def pitches(self) -> list[int]:
        return [note.pitch for note in self.notes]

    def summary(self) -> str:
        """One line for the log pane: what was written and where it sits."""
        if not self.notes:
            return f"{self.label}: no notes (empty chord timeline)"
        low, high = min(self.pitches()), max(self.pitches())
        line = (
            f"{self.label} [{self.role}] - {len(self.notes)} notes, "
            f"{note_name(low)}..{note_name(high)}"
        )
        if self.register_note:
            line += f" ({self.register_note})"
        if self.blurb:
            line += f" - {self.blurb}"
        return line


# ---------------------------------------------------------------------------
# Small helpers shared by the builders
# ---------------------------------------------------------------------------


def _params(
    profile: InstrumentProfile,
    song: _Song,
    center_octave: int,
    color_id: int,
    seed: int,
) -> MelodyParams:
    return MelodyParams(
        ticks_per_beat=song.ticks_per_beat,
        chords=list(song.chords),
        tonic=song.tonic,
        scale_name=song.scale_name,
        beats_per_bar=song.beats_per_bar,
        rhythm_preset=profile.rhythm,
        density=profile.density,
        rest_chance=profile.rest_chance,
        gate=profile.gate,
        strategy=profile.strategy,
        contour=profile.contour,
        center_octave=center_octave,
        range_semitones=profile.range_semitones,
        velocity=profile.velocity,
        velocity_jitter=profile.velocity_jitter,
        timing_jitter=profile.timing_jitter,
        seed=seed,
        melody_color=color_id,
    )


def _emit(
    notes: list[MelodyNote],
    song: _Song,
    pitch: int,
    start_beats: float,
    length_beats: float,
    velocity: float,
    color_id: int,
    offset_ticks: int = 0,
) -> None:
    start = max(0, int(round(start_beats * song.ticks_per_beat)) + offset_ticks)
    length = max(1, int(round(length_beats * song.ticks_per_beat)))
    notes.append(
        MelodyNote(
            pitch=int(pitch),
            start=start,
            length=length,
            velocity=min(1.0, max(0.1, float(velocity))),
            color=color_id,
        )
    )


def _velocity(profile: InstrumentProfile, strong: bool, rng: Rng) -> float:
    value = profile.velocity + (0.08 if strong else 0.0)
    if profile.velocity_jitter:
        value += rng.uniform(-profile.velocity_jitter, profile.velocity_jitter)
    return min(1.0, max(0.1, value))


def _span_beats(slots, index: int, chord: ChordSpec) -> float:
    """How long a note may sound from ``slots[index]`` before the next onset.

    The next onset is the next slot, clipped to the chord so notes do not run
    into a harmony they were not written for.
    """
    start = slots[index].start
    if index + 1 < len(slots):
        next_start = min(slots[index + 1].start, chord.end)
    else:
        next_start = chord.end
    return max(0.05, next_start - start)


# ---------------------------------------------------------------------------
# Voice-led chord voicings (pads, keys, guitar)
# ---------------------------------------------------------------------------


def _voice_chord(
    chord: ChordSpec,
    previous: list[int],
    lo: int,
    hi: int,
    voices: int,
) -> list[int]:
    """Chord tones in ``lo``..``hi``, each voice moved the shortest distance.

    Voice leading is what makes pads and comping sound written rather than
    stacked: every voice picks the chord tone closest to where it just was.
    """
    tones = chord_tones_in_range(chord.root, chord.template, lo, hi)
    if not tones:
        return []
    voices = max(1, min(int(voices), len(tones)))
    root_pitches = [note for note in tones if note % 12 == chord.root % 12]

    if not previous:
        # First chord of the progression: a clear root-position voicing, built
        # upwards from the low end of the register so the harmony reads.
        anchor = (
            nearest_note(lo + (hi - lo) // 4, root_pitches) if root_pitches else tones[0]
        )
        chosen = [anchor]
        while len(chosen) < voices:
            above = [note for note in tones if note > chosen[-1] and note not in chosen]
            below = [note for note in tones if note < chosen[0] and note not in chosen]
            if above:
                chosen.append(above[0])
            elif below:
                chosen.insert(0, below[-1])
            else:
                break
        return sorted(set(chosen))

    chosen = []
    for index in range(voices):
        target = previous[index] if index < len(previous) else chosen[-1]
        options = [note for note in tones if note not in chosen]
        if not options:
            break
        chosen.append(min(options, key=lambda note: (abs(note - target), note)))

    # Keep the root in the voicing when there is room for it (clarity). The
    # cheapest voice to move is a doubled pitch class, then the closest one.
    if len(chosen) >= 3 and root_pitches and not any(
        note % 12 == chord.root % 12 for note in chosen
    ):
        anchor = nearest_note(chosen[0], root_pitches)
        counts = {note: chosen.count(note) for note in chosen}
        doubled = [index for index, note in enumerate(chosen) if counts[note] > 1]
        candidates = doubled or list(range(len(chosen)))
        replace_index = min(candidates, key=lambda i: abs(chosen[i] - anchor))
        chosen[replace_index] = anchor
    return sorted(set(chosen))


# ---------------------------------------------------------------------------
# Builders, one per role
# ---------------------------------------------------------------------------


def _build_bass(
    profile: InstrumentProfile, song: _Song, center: int, color_id: int, seed: int
) -> list[MelodyNote]:
    """Root-driven low end: the chord root, occasionally a fifth or octave."""
    params = _params(profile, song, center, color_id, seed)
    rng = Rng(seed + 17)
    slots = build_rhythm(params)
    lo, hi = params.lo, params.hi
    notes: list[MelodyNote] = []
    previous_chord: ChordSpec | None = None
    previous_pitch: int | None = None
    middle = lo + (hi - lo) // 2

    for index, slot in enumerate(slots):
        chord = chord_at(song.chords, slot.start)
        if chord is None:
            continue
        tones = chord_tones_in_range(chord.root, chord.template, lo, hi)
        if not tones:
            continue
        roots = [note for note in tones if note % 12 == chord.root % 12] or tones
        root_note = nearest_note(middle, roots)
        options = [root_note]
        fifth = (chord.root + 7) % 12
        options += [note for note in tones if note % 12 == fifth]
        octave = [note for note in tones if note % 12 == chord.root % 12 and note != root_note]

        changed = chord != previous_chord
        if changed or slot.strong or previous_pitch is None or rng.chance(0.45):
            pitch = root_note
        else:
            candidates = [root_note] + options[1:] * 2 + octave
            pitch = rng.choice(candidates)
        previous_chord = chord
        previous_pitch = pitch

        span = _span_beats(slots, index, chord)
        length = span * profile.gate
        if profile.glide:
            length = span * min(1.6, profile.gate + profile.glide)
        _emit(
            notes,
            song,
            pitch,
            slot.start,
            length,
            _velocity(profile, slot.strong, rng),
            color_id,
        )

    return notes


def _build_arp(
    profile: InstrumentProfile, song: _Song, center: int, color_id: int, seed: int
) -> list[MelodyNote]:
    """A chord-tone pattern that walks up and down inside the chord."""
    params = _params(profile, song, center, color_id, seed)
    rng = Rng(seed + 31)
    slots = build_rhythm(params)
    lo, hi = params.lo, params.hi
    notes: list[MelodyNote] = []
    index_in_chord = 0
    direction = 1
    previous_chord: ChordSpec | None = None
    middle = (lo + hi) // 2

    for index, slot in enumerate(slots):
        chord = chord_at(song.chords, slot.start)
        if chord is None:
            continue
        tones = chord_tones_in_range(chord.root, chord.template, lo, hi)
        if not tones:
            continue

        if chord != previous_chord:
            roots = [note for note in tones if note % 12 == chord.root % 12] or tones
            anchor = nearest_note(middle, roots)
            index_in_chord = min(
                range(len(tones)), key=lambda i: (abs(tones[i] - anchor), tones[i])
            )
            direction = -1 if rng.chance(0.25) else 1
            previous_chord = chord
        else:
            index_in_chord += direction
            if index_in_chord >= len(tones):
                index_in_chord = max(0, len(tones) - 2)
                direction = -1
            elif index_in_chord < 0:
                index_in_chord = min(1, len(tones) - 1)
                direction = 1

        pitch = tones[max(0, min(len(tones) - 1, index_in_chord))]
        length = slot.length
        if profile.gate >= 0.8:
            length = _span_beats(slots, index, chord) * profile.gate
        _emit(
            notes,
            song,
            pitch,
            slot.start,
            length,
            _velocity(profile, slot.strong, rng),
            color_id,
        )

    return notes


def _build_pad(
    profile: InstrumentProfile, song: _Song, center: int, color_id: int, seed: int
) -> list[MelodyNote]:
    """Sustained, voice-led chords - re-attacked once per bar on long chords."""
    rng = Rng(seed + 43)
    lo = center * 12 - profile.range_semitones
    hi = center * 12 + profile.range_semitones
    notes: list[MelodyNote] = []
    previous: list[int] = []

    for chord in song.chords:
        tone_count = len(chord_pitch_classes(chord.root, chord.template))
        voices = min(profile.max_voices, max(3, tone_count))
        voicing = _voice_chord(chord, previous, lo, hi, voices)
        if not voicing:
            continue
        previous = voicing

        block = chord.length
        if chord.length > song.beats_per_bar * 1.5:
            block = song.beats_per_bar  # a held 8-bar pad is not what players do
        blocks = max(1, int(math.ceil(chord.length / block - 1e-9)))
        for step in range(blocks):
            start = chord.start + step * block
            length = max(0.1, min(block, chord.end - start)) * profile.gate
            for voice_index, pitch in enumerate(voicing):
                _emit(
                    notes,
                    song,
                    pitch,
                    start,
                    length,
                    _velocity(profile, step == 0 and voice_index == 0, rng),
                    color_id,
                    offset_ticks=voice_index * profile.strum_ticks,
                )

    return notes


def _build_keys(
    profile: InstrumentProfile, song: _Song, center: int, color_id: int, seed: int
) -> list[MelodyNote]:
    """Comping: voice-led chords played as stabs, thinned out on weak slots."""
    params = _params(profile, song, center, color_id, seed)
    rng = Rng(seed + 53)
    slots = build_rhythm(params)
    lo, hi = params.lo, params.hi
    notes: list[MelodyNote] = []
    previous: list[int] = []
    voicing: list[int] = []
    previous_chord: ChordSpec | None = None
    jitter = profile.timing_jitter

    for index, slot in enumerate(slots):
        chord = chord_at(song.chords, slot.start)
        if chord is None:
            continue
        if chord != previous_chord:
            tone_count = len(chord_pitch_classes(chord.root, chord.template))
            voicing = _voice_chord(
                chord, previous, lo, hi, min(profile.max_voices, max(3, tone_count))
            )
            previous = voicing or previous
            previous_chord = chord
        if not voicing:
            continue

        if slot.strong or len(voicing) <= 2:
            voices = voicing
        elif rng.chance(0.35):
            voices = [voicing[-1]]  # a single colour note
        elif rng.chance(0.5):
            voices = [voicing[0], voicing[-1]]
        else:
            voices = voicing

        length = slot.length
        offset = 0
        if jitter:
            offset = int(round(rng.uniform(-jitter, jitter)))
        for voice_index, pitch in enumerate(voices):
            _emit(
                notes,
                song,
                pitch,
                slot.start,
                length,
                _velocity(profile, slot.strong, rng),
                color_id,
                offset_ticks=offset + voice_index * profile.strum_ticks,
            )

    return notes


def _trim_overlaps(notes: list[MelodyNote]) -> list[MelodyNote]:
    """Stop a monophonic line's humanised timing from overlapping itself."""
    ordered = sorted(notes, key=lambda note: (note.start, note.pitch))
    trimmed: list[MelodyNote] = []
    for index, note in enumerate(ordered):
        length = note.length
        if index + 1 < len(ordered):
            gap = ordered[index + 1].start - note.start
            if 0 < gap < length:
                length = max(1, gap)
        trimmed.append(replace(note, length=length))
    return trimmed


def _build_line(
    profile: InstrumentProfile, song: _Song, center: int, color_id: int, seed: int
) -> list[MelodyNote]:
    """A monophonic melodic line: the existing melody engine, instrument-tuned."""
    params = _params(profile, song, center, color_id, seed)
    return _trim_overlaps(generate_melody(params))


_BUILDERS = {
    ROLE_BASS: _build_bass,
    ROLE_ARP: _build_arp,
    ROLE_PAD: _build_pad,
    ROLE_KEYS: _build_keys,
    ROLE_LEAD: _build_line,
    ROLE_COUNTER: _build_line,
}


def _fallback_part(
    profile: InstrumentProfile, song: _Song, center: int, color_id: int
) -> list[MelodyNote]:
    """Guarantee a part exists: one note per chord, in the right register."""
    lo = center * 12 - profile.range_semitones
    hi = center * 12 + profile.range_semitones
    notes: list[MelodyNote] = []
    for chord in song.chords:
        tones = chord_tones_in_range(chord.root, chord.template, lo, hi)
        if not tones:
            continue
        pitch = nearest_note(lo + (hi - lo) // 2, tones)
        _emit(notes, song, pitch, chord.start, max(0.25, chord.length * 0.95), profile.velocity, color_id)
    return notes


# ---------------------------------------------------------------------------
# Drums: one groove for the whole song, written across the kit pieces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DrumFeel:
    """One groove, counted in sixteenths of a bar (0-15).

    The kick cycles through ``kick_bars`` a bar at a time so a four-bar loop
    breathes instead of repeating one bar verbatim.
    """

    name: str
    kick_bars: tuple[tuple[int, ...], ...]
    snare: tuple[int, ...]
    hats: tuple[int, ...]
    open_hats: tuple[int, ...] = ()
    ghost_snares: bool = False
    hat_rolls: bool = False
    fill_drops_kick: bool = True
    # Trap and half time live on the 808: the kick plays the low end's arrivals
    # too, instead of only holding its own pattern.
    bass_locked: bool = False


_DRUM_FEELS = (
    _DrumFeel(
        name="four on the floor",
        kick_bars=((0, 4, 8, 12), (0, 4, 8, 12), (0, 4, 8, 12), (0, 4, 10, 12)),
        snare=(4, 12),
        hats=(2, 6, 10, 14),
        fill_drops_kick=False,
    ),
    _DrumFeel(
        name="boom bap",
        kick_bars=((0, 7, 10), (0, 7, 10), (0, 5, 10), (0, 7, 11)),
        snare=(4, 12),
        hats=tuple(range(0, 16, 2)),
        open_hats=(14,),
    ),
    _DrumFeel(
        name="trap",
        kick_bars=((0, 10), (0, 6, 10), (0, 10), (0, 6, 13)),
        snare=(8,),
        hats=tuple(range(16)),
        open_hats=(6, 14),
        ghost_snares=True,
        hat_rolls=True,
        bass_locked=True,
    ),
    _DrumFeel(
        name="half time",
        kick_bars=((0, 10), (0, 6, 10)),
        snare=(8,),
        hats=tuple(range(0, 16, 2)),
        open_hats=(14,),
        ghost_snares=True,
        bass_locked=True,
    ),
)


@dataclass
class _DrumPlan:
    """The groove decisions every drum piece shares - this is what keeps them
    cohesive: same feel, same phrase length, same lock to the bass line."""

    feel: _DrumFeel
    steps_per_bar: int
    beats_per_step: float
    bars: int
    phrase_bars: int
    bass_onsets: tuple[float, ...]
    chord_starts: frozenset[float]
    has_toms: bool

    def step_beats(self, step: float) -> float:
        return step * self.beats_per_step

    def kick_steps(self, bar_index: int) -> set[float]:
        bars = self.feel.kick_bars
        steps = [step for step in bars[bar_index % len(bars)] if step < self.steps_per_bar]
        return {float(step) for step in steps}

    def is_fill_bar(self, bar_index: int) -> bool:
        """The last bar of every phrase, where the kit turns around."""
        return self.phrase_bars > 1 and (bar_index + 1) % self.phrase_bars == 0

    def fill_steps(self) -> range:
        return range(self.steps_per_bar - 4, self.steps_per_bar)


_FEEL_BY_NAME = {feel.name: feel for feel in _DRUM_FEELS}


def _feel_for(tempo_bpm: float, has_808: bool, seed: int) -> _DrumFeel:
    """Pick a groove from the song's tempo - and from what is playing under it."""
    if tempo_bpm >= 124:
        names = ("four on the floor", "trap")
    elif tempo_bpm >= 100:
        names = ("trap", "boom bap")
    else:
        names = ("boom bap", "half time")
    rng = Rng(seed + 7)
    if has_808 and "trap" in names:
        # An 808 line leans towards trap, but the seed still gets a say.
        weights = tuple(0.65 if name == "trap" else 0.35 for name in names)
        return _FEEL_BY_NAME[rng.weighted(names, weights)]
    return _FEEL_BY_NAME[rng.choice(names)]


def _drum_plan(
    song: _Song,
    tempo_bpm: float,
    keys: tuple[str, ...],
    bass_notes: list[MelodyNote],
    seed: int,
) -> _DrumPlan:
    """Work out the groove once, from the song - tempo, bars, bass, chords."""
    beats_per_bar = song.beats_per_bar
    steps_per_bar = max(4, int(round(beats_per_bar * 4)))
    length = max(beats_per_bar, song.end - song.start)
    bars = max(1, int(round(length / beats_per_bar)))
    phrase = 4 if bars >= 4 else bars
    onsets = tuple(
        sorted({round(note.start / song.ticks_per_beat, 4) for note in bass_notes})
    )
    return _DrumPlan(
        feel=_feel_for(float(tempo_bpm), "808s" in keys, seed),
        steps_per_bar=steps_per_bar,
        beats_per_step=beats_per_bar / steps_per_bar,
        bars=bars,
        phrase_bars=phrase,
        bass_onsets=onsets,
        chord_starts=frozenset(round(chord.start, 4) for chord in song.chords),
        has_toms="toms" in keys,
    )


def _emit_step(
    notes: list[MelodyNote],
    song: _Song,
    plan: _DrumPlan,
    pitch: int,
    beat: float,
    length_beats: float,
    velocity: float,
    color_id: int,
) -> None:
    """Place one drum hit on the shared groove grid.

    Every piece goes through here, so the whole kit sits on the same (at most
    thirty-second) grid and nothing lands outside the chord timeline.
    """
    grid = plan.beats_per_step / 2
    beat = round(beat / grid) * grid
    if beat < song.start or beat >= song.end:
        return
    _emit(notes, song, pitch, beat, length_beats, velocity, color_id)


def _accent(base: float, step: float, rng: Rng, jitter: float = 0.05) -> float:
    """Downbeats play louder - that is what makes a pattern read as a groove."""
    value = base + (0.1 if step % 4 == 0 else 0.0)
    return min(1.0, max(0.1, value + rng.uniform(-jitter, jitter)))


def _build_kick(
    profile: InstrumentProfile, song: _Song, plan: _DrumPlan, color_id: int, seed: int
) -> list[MelodyNote]:
    """Kick pattern, snapped onto the bass onsets so nothing flams."""
    rng = Rng(seed + 61)
    notes: list[MelodyNote] = []
    for bar_index in range(plan.bars):
        bar_start = song.start + bar_index * song.beats_per_bar
        steps = plan.kick_steps(bar_index)
        if plan.is_fill_bar(bar_index) and plan.feel.fill_drops_kick:
            steps = {s for s in steps if plan.step_beats(s) < song.beats_per_bar - 1.0}

        for onset in plan.bass_onsets:
            offset = round(onset - bar_start, 4)
            if not 0.0 <= offset < song.beats_per_bar:
                continue
            step = offset / plan.beats_per_step
            near = min(steps, key=lambda s: abs(s - step)) if steps else None
            if near is not None and abs(near - step) <= 0.5:
                steps.discard(near)  # the kick moves with the bass, not against it
            elif not plan.feel.bass_locked and offset not in plan.chord_starts:
                continue  # only a new chord pulls in an extra kick
            steps.add(round(step, 4))

        for step in sorted(steps):
            _emit_step(
                notes,
                song,
                plan,
                DRUM_KICK,
                bar_start + plan.step_beats(step),
                0.2,
                _accent(profile.velocity, step, rng, jitter=0.02),
                color_id,
            )
    return notes


def _build_snare(
    profile: InstrumentProfile, song: _Song, plan: _DrumPlan, color_id: int, seed: int
) -> list[MelodyNote]:
    """Backbeat, ghost notes, and the phrase-end fill when there are no toms."""
    rng = Rng(seed + 67)
    notes: list[MelodyNote] = []
    beat_len = min(0.25, plan.beats_per_step)
    for bar_index in range(plan.bars):
        bar_start = song.start + bar_index * song.beats_per_bar
        backbeat = plan.feel.snare
        fill = plan.is_fill_bar(bar_index) and not plan.has_toms
        fill_steps = set(plan.fill_steps()) if fill else set()
        for step in backbeat:
            if step in fill_steps:
                continue  # the fill takes that beat
            _emit_step(
                notes,
                song,
                plan,
                DRUM_SNARE,
                bar_start + plan.step_beats(step),
                beat_len,
                _accent(profile.velocity, step, rng),
                color_id,
            )
        if plan.feel.ghost_snares:
            for step in range(plan.steps_per_bar):
                if step in backbeat or step in fill_steps or step % 4 != 2:
                    continue
                if rng.chance(0.3):
                    _emit_step(
                        notes,
                        song,
                        plan,
                        DRUM_SNARE,
                        bar_start + plan.step_beats(step),
                        beat_len,
                        0.32 + rng.uniform(0.0, 0.08),
                        color_id,
                    )
        if fill:
            for index, step in enumerate(plan.fill_steps()):
                _emit_step(
                    notes,
                    song,
                    plan,
                    DRUM_SNARE,
                    bar_start + plan.step_beats(step),
                    beat_len,
                    0.6 + 0.12 * index,
                    color_id,
                )
    return notes


def _build_hats(
    profile: InstrumentProfile, song: _Song, plan: _DrumPlan, color_id: int, seed: int
) -> list[MelodyNote]:
    """Closed-hat grid, open hats to breathe, crash on every phrase start."""
    rng = Rng(seed + 71)
    notes: list[MelodyNote] = []
    hat_len = min(0.2, plan.beats_per_step)
    for bar_index in range(plan.bars):
        bar_start = song.start + bar_index * song.beats_per_bar
        open_steps = {step for step in plan.feel.open_hats if step < plan.steps_per_bar}
        rolling = plan.feel.hat_rolls and plan.is_fill_bar(bar_index)
        roll_steps = set(plan.fill_steps()) if rolling else set()
        for index, step in enumerate(plan.feel.hats):
            # A closed hat never doubles an open hat, and the roll replaces the
            # last beat instead of flamming against it.
            if step >= plan.steps_per_bar or step in open_steps or step in roll_steps:
                continue
            base = profile.velocity + (0.08 if index % 2 == 0 else -0.04)
            _emit_step(
                notes,
                song,
                plan,
                DRUM_CLOSED_HAT,
                bar_start + plan.step_beats(step),
                hat_len,
                _accent(base, step, rng),
                color_id,
            )
        for step in sorted(open_steps):
            _emit_step(
                notes,
                song,
                plan,
                DRUM_OPEN_HAT,
                bar_start + plan.step_beats(step),
                min(0.75, plan.beats_per_step * 3),
                _accent(profile.velocity + 0.05, step, rng),
                color_id,
            )
        if plan.bars > 1 and bar_index % plan.phrase_bars == 0:
            _emit_step(
                notes,
                song,
                plan,
                DRUM_CRASH,
                bar_start,
                2.0,
                0.9,
                color_id,
            )
        if rolling:
            roll_start = bar_start + plan.step_beats(plan.steps_per_bar - 4)
            for index in range(8):  # a 32nd roll into the next phrase
                _emit_step(
                    notes,
                    song,
                    plan,
                    DRUM_CLOSED_HAT,
                    roll_start + index * plan.beats_per_step / 2,
                    hat_len / 2,
                    min(1.0, 0.6 + 0.05 * index),
                    color_id,
                )
    return notes


def _build_clap(
    profile: InstrumentProfile, song: _Song, plan: _DrumPlan, color_id: int, seed: int
) -> list[MelodyNote]:
    """Claps on the backbeat, slightly loose so they are not robotic."""
    rng = Rng(seed + 73)
    notes: list[MelodyNote] = []
    for bar_index in range(plan.bars):
        bar_start = song.start + bar_index * song.beats_per_bar
        for step in plan.feel.snare:
            _emit_step(
                notes,
                song,
                plan,
                DRUM_CLAP,
                bar_start + plan.step_beats(step),
                0.25,
                _accent(profile.velocity, step, rng, jitter=0.06),
                color_id,
            )
    return notes


def _build_toms(
    profile: InstrumentProfile, song: _Song, plan: _DrumPlan, color_id: int, seed: int
) -> list[MelodyNote]:
    """A descending tom run fills the last beat of every phrase."""
    rng = Rng(seed + 79)
    notes: list[MelodyNote] = []
    run = DRUM_TOMS
    for bar_index in range(plan.bars):
        if not plan.is_fill_bar(bar_index):
            continue
        bar_start = song.start + bar_index * song.beats_per_bar
        for index, step in enumerate(plan.fill_steps()):
            _emit_step(
                notes,
                song,
                plan,
                run[min(index, len(run) - 1)],
                bar_start + plan.step_beats(step),
                0.25,
                _accent(profile.velocity + 0.06 * index, step, rng),
                color_id,
            )
    return notes


_DRUM_BUILDERS = {
    "kick": _build_kick,
    "snare": _build_snare,
    "hats": _build_hats,
    "clap": _build_clap,
    "toms": _build_toms,
}


def _fallback_drum_part(
    profile: InstrumentProfile, song: _Song, color_id: int
) -> list[MelodyNote]:
    """Never hand back an empty drum track: one hit per chord, on its pitch."""
    pitch = DRUM_PIECES[profile.key][0]
    notes: list[MelodyNote] = []
    for chord in song.chords:
        _emit(notes, song, pitch, chord.start, 0.2, profile.velocity, color_id)
    return notes


# ---------------------------------------------------------------------------
# The arranger
# ---------------------------------------------------------------------------


def _place_register(
    profile: InstrumentProfile, used: list[int], label_by_center: dict[int, str]
) -> tuple[int, str]:
    """Pick a register for this part, moving out of the way when it clashes."""
    # Parts give way downwards first: accompaniment sits under the melody.
    for delta in (0, -1, -2, -3, 1, 2, 3):
        center = profile.center_octave + delta
        lo = center * 12 - profile.range_semitones
        hi = center * 12 + profile.range_semitones
        if not (LOWEST_PITCH <= lo and hi <= HIGHEST_PITCH):
            continue
        if center in used:
            continue
        note = ""
        if delta:
            direction = "down" if delta < 0 else "up"
            other = label_by_center.get(profile.center_octave, "another part")
            note = f"moved an octave {direction} to leave room for {other}"
        return center, note
    return profile.center_octave, ""


def build_song(
    instruments,
    chords: list[ChordSpec],
    tonic: int,
    scale_name: str,
    ticks_per_beat: float,
    beats_per_bar: float = 4.0,
    seed: int = 1,
    tempo_bpm: float = 120.0,
) -> list[InstrumentPart]:
    """Write one part per instrument over the same chord timeline.

    ``instruments`` may be canonical keys or anything the alias table knows
    (``"808s"``, ``"warm pad"``, ``"violin"``, ``"drums"``...). Parts are
    returned in the order they were asked for, each with its own colour,
    register and notes. ``tempo_bpm`` only steers the drum groove, which is why
    the drums are written after the bass line they lock to.
    """
    if not chords:
        raise InstrumentError("ChordLayer needs at least one chord to write against.")
    profiles: list[InstrumentProfile] = []
    for name in instruments:
        if isinstance(name, InstrumentProfile):
            profiles.append(name)
        else:
            profiles.append(resolve_instrument(str(name)))
    if not profiles:
        raise InstrumentError("Name at least one instrument, e.g. '808s, pad, flute'.")
    expanded: list[InstrumentProfile] = []
    for profile in profiles:
        if profile.kit:
            expanded.extend(INSTRUMENTS[key] for key in profile.kit)
        else:
            expanded.append(profile)
    profiles = expanded

    song = _Song(
        chords=list(chords),
        tonic=int(tonic) % 12,
        scale_name=scale_name,
        ticks_per_beat=float(ticks_per_beat),
        beats_per_bar=max(1.0, float(beats_per_bar)),
        seed=int(seed),
    )

    parts: list[InstrumentPart | None] = [None] * len(profiles)
    used_centers: dict[str, list[int]] = {}
    center_labels: dict[str, dict[int, str]] = {}
    drum_slots: list[int] = []
    bass_notes: list[MelodyNote] = []

    for index, profile in enumerate(profiles):
        if profile.role == ROLE_DRUMS:
            drum_slots.append(index)  # written below, once the low end exists
            continue
        group = _REGISTER_GROUPS[profile.role]
        used = used_centers.setdefault(group, [])
        labels = center_labels.setdefault(group, {})
        center, register_note = _place_register(profile, used, labels)
        used.append(center)
        labels.setdefault(center, profile.label)

        color, color_id = PART_COLORS[index % len(PART_COLORS)]
        part_seed = song.seed * 131 + index * 977
        builder = _BUILDERS[profile.role]
        notes = builder(profile, song, center, color_id, part_seed)
        if not notes:
            notes = _fallback_part(profile, song, center, color_id)
        if profile.role == ROLE_BASS:
            bass_notes.extend(notes)

        parts[index] = InstrumentPart(
            key=profile.key,
            label=profile.label,
            role=profile.role,
            notes=sorted(notes, key=lambda note: (note.start, note.pitch)),
            color=color,
            color_id=color_id,
            center_octave=center,
            range_semitones=profile.range_semitones,
            register_label=f"{note_name(center * 12 - profile.range_semitones)}.."
            f"{note_name(center * 12 + profile.range_semitones)}",
            blurb=profile.blurb,
            register_note=register_note,
        )

    if drum_slots:
        plan = _drum_plan(
            song,
            tempo_bpm,
            tuple(profile.key for profile in profiles),
            bass_notes,
            song.seed,
        )
        for index in drum_slots:
            profile = profiles[index]
            color, color_id = PART_COLORS[index % len(PART_COLORS)]
            part_seed = song.seed * 131 + index * 977
            builder = _DRUM_BUILDERS.get(profile.key)
            pitches = DRUM_PIECES.get(profile.key)
            if builder is None or pitches is None:
                raise InstrumentError(
                    f"I don't know how to write a drum part for {profile.label!r}."
                )
            notes = builder(profile, song, plan, color_id, part_seed)
            if not notes:
                notes = _fallback_drum_part(profile, song, color_id)
            feel = plan.feel.name
            parts[index] = InstrumentPart(
                key=profile.key,
                label=profile.label,
                role=profile.role,
                notes=sorted(notes, key=lambda note: (note.start, note.pitch)),
                color=color,
                color_id=color_id,
                center_octave=pitches[0] // 12,
                range_semitones=max(0, pitches[-1] - pitches[0]),
                register_label="GM drum kit",
                blurb=profile.blurb,
                register_note=f"{feel} groove at {round(float(tempo_bpm))} BPM",
                pitch_lo=min(note.pitch for note in notes),
                pitch_hi=max(note.pitch for note in notes),
            )
    return [part for part in parts if part is not None]
