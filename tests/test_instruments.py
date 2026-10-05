"""Tests for chordlayer.instruments (the instrument-aware arranger)."""

import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from chordlayer.generator import CONTOURS, RHYTHM_PRESETS, STRATEGIES  # noqa: E402
from chordlayer.instruments import (  # noqa: E402
    DEFAULT_INSTRUMENTS,
    DRUM_CLOSED_HAT,
    DRUM_CRASH,
    DRUM_KICK,
    DRUM_OPEN_HAT,
    DRUM_PIECES,
    DRUM_SNARE,
    INSTRUMENTS,
    PART_COLORS,
    ROLES,
    ROLE_DRUMS,
    InstrumentError,
    build_song,
    expand_kits,
    instrument_choices,
    parse_instruments,
    resolve_instrument,
)
from chordlayer.theory import chord_pitch_classes, parse_progression  # noqa: E402

PPQ = 480
TONIC = 9  # A
SCALE = "Natural Minor (Aeolian)"


def song_for(instruments, text="Am F C G", seed=7, beats_per_bar=4.0, tempo_bpm=120.0):
    return build_song(
        instruments,
        parse_progression(text),
        TONIC,
        SCALE,
        PPQ,
        beats_per_bar,
        seed,
        tempo_bpm=tempo_bpm,
    )


def steps_of(part):
    """A part's onsets as sixteenth-note steps from the start of the song."""
    return sorted(round(note.start / PPQ * 4) for note in part.notes)


def bar_steps(part, bar):
    """The sixteenth steps one bar of a part plays, relative to that bar."""
    offset = bar * 16
    return sorted(step - offset for step in steps_of(part) if offset <= step < offset + 16)


class TestProfiles(unittest.TestCase):
    def test_every_profile_is_usable(self):
        for key, profile in INSTRUMENTS.items():
            self.assertEqual(profile.key, key)
            self.assertIn(profile.role, ROLES)
            self.assertIn(profile.rhythm, RHYTHM_PRESETS)
            self.assertIn(profile.strategy, STRATEGIES)
            self.assertIn(profile.contour, CONTOURS)
            self.assertGreaterEqual(profile.lo, 12)
            self.assertLessEqual(profile.hi, 108)
            self.assertGreaterEqual(profile.max_voices, 1)
            self.assertTrue(0.1 <= profile.gate <= 1.0)
            self.assertTrue(0.0 <= profile.density <= 1.0)

    def test_one_alias_cannot_mean_two_instruments(self):
        seen = {}
        for key, profile in INSTRUMENTS.items():
            for name in {key} | set(profile.aliases):
                self.assertNotIn(name, seen, f"{name!r} is claimed twice")
                seen[name] = key

    def test_aliases_resolve_to_the_right_instrument(self):
        cases = {
            "808": "808s",
            "808s": "808s",
            "sub bass": "sub",
            "finger bass": "bass",
            "e-piano": "piano",
            "hammond": "organ",
            "strummed guitar": "guitar",
            "warm pad": "pad",
            "choir": "strings",
            "pan flute": "flute",
            "fiddle": "violin",
            "sax": "brass",
            "violoncello": "cello",
            "supersaw": "lead",
            "counter melody": "harmony",
            "glockenspiel": "bell",
            "kalimba": "pluck",
            "pizzicato": "pluck",
            "harp": "harp",
            "arpeggiator": "arp",
        }
        for name, expected in cases.items():
            self.assertEqual(resolve_instrument(name).key, expected, name)

    def test_typos_get_a_suggestion(self):
        with self.assertRaises(InstrumentError) as caught:
            resolve_instrument("flut")
        self.assertIn("Flute", str(caught.exception))

    def test_choices_list_every_instrument(self):
        self.assertEqual(len(instrument_choices()), len(INSTRUMENTS))
        self.assertIn("808s", instrument_choices())


class TestParsing(unittest.TestCase):
    def test_people_style_lists(self):
        self.assertEqual(
            parse_instruments("808s, flute and violin plus warm pad"),
            ("808s", "flute", "violin", "pad"),
        )

    def test_duplicates_are_dropped_and_order_is_kept(self):
        self.assertEqual(parse_instruments("pad, Pad, PAD, 808s"), ("pad", "808s"))

    def test_empty_list_is_an_error(self):
        with self.assertRaises(InstrumentError):
            parse_instruments("   ")

    def test_unknown_names_are_named_not_dropped(self):
        with self.assertRaises(InstrumentError) as caught:
            parse_instruments("808s, kazoo, flute")
        message = str(caught.exception)
        self.assertIn("kazoo", message)
        self.assertIn("808s", message)  # the help text lists what does work

    def test_default_instruments_exist(self):
        for key in DEFAULT_INSTRUMENTS:
            self.assertIn(key, INSTRUMENTS)

    def test_profile_objects_can_be_passed_directly(self):
        parts = song_for([INSTRUMENTS["flute"], INSTRUMENTS["pad"]])
        self.assertEqual([part.key for part in parts], ["flute", "pad"])

    def test_no_chords_is_an_error(self):
        with self.assertRaises(InstrumentError):
            build_song(["flute"], [], 0, SCALE, PPQ)


class TestSongStructure(unittest.TestCase):
    def setUp(self):
        self.parts = song_for(["808s", "pad", "flute", "violin"])

    def test_one_part_per_instrument_in_the_order_asked(self):
        self.assertEqual(
            [part.key for part in self.parts], ["808s", "pad", "flute", "violin"]
        )
        self.assertEqual([part.label for part in self.parts], ["808s", "Pad", "Flute", "Violin"])

    def test_every_instrument_gets_notes(self):
        for part in self.parts:
            self.assertTrue(part.notes, f"{part.label} has no notes")
            self.assertIn(part.label, part.summary())

    def test_parts_stay_inside_their_own_register(self):
        for part in self.parts:
            for note in part.notes:
                self.assertGreaterEqual(note.pitch, part.lo)
                self.assertLessEqual(note.pitch, part.hi)

    def test_notes_stay_inside_the_chord_timeline(self):
        end = max(chord.end for chord in parse_progression("Am F C G")) * PPQ
        for part in self.parts:
            for note in part.notes:
                self.assertGreaterEqual(note.start, 0)
                self.assertLessEqual(note.start, end)
                self.assertGreaterEqual(note.length, 1)
                self.assertTrue(0.1 <= note.velocity <= 1.0)

    def test_parts_are_visually_distinguishable(self):
        colors = [part.color for part in self.parts]
        self.assertEqual(len(set(colors)), len(colors))
        self.assertEqual(len(PART_COLORS), 8)

    def test_no_duplicate_notes_within_a_part(self):
        for part in self.parts:
            pairs = [(note.start, note.pitch) for note in part.notes]
            self.assertEqual(len(pairs), len(set(pairs)), part.label)

    def test_same_seed_rebuilds_the_same_song(self):
        first = song_for(["808s", "flute"], seed=3)
        second = song_for(["808s", "flute"], seed=3)
        self.assertEqual(
            [(p.key, [(n.pitch, n.start, n.length) for n in p.notes]) for p in first],
            [(p.key, [(n.pitch, n.start, n.length) for n in p.notes]) for p in second],
        )

    def test_a_new_seed_rewrites_the_song(self):
        first = song_for(["808s", "flute"], seed=3)
        other = song_for(["808s", "flute"], seed=4)
        self.assertNotEqual(
            [(n.pitch, n.start) for n in first[1].notes],
            [(n.pitch, n.start) for n in other[1].notes],
        )

    def test_clashing_registers_are_spread_out(self):
        parts = song_for(["flute", "violin", "lead", "bell"])
        centers = [part.center_octave for part in parts]
        self.assertEqual(len(set(centers)), len(centers))
        self.assertTrue(any(part.register_note for part in parts), "expected a shift note")
        for part in parts:
            self.assertGreaterEqual(part.lo, 12)
            self.assertLessEqual(part.hi, 108)

    def test_a_full_palette_of_instruments_all_produce_music(self):
        parts = song_for(
            ["808s", "piano", "strings", "flute", "harmony", "arp", "bell", "pluck"]
        )
        self.assertEqual(len(parts), 8)
        for part in parts:
            self.assertTrue(part.notes, part.label)

    def test_every_instrument_always_gets_a_part(self):
        # A deliberately lifeless profile still has to yield notes, so the user
        # never names an instrument and gets an empty track back.
        dud = replace(INSTRUMENTS["flute"], density=0.0, rest_chance=0.9)
        for seed in range(6):
            parts = song_for([dud], seed=seed)
            self.assertTrue(parts[0].notes, f"seed {seed} produced an empty part")


class TestKits(unittest.TestCase):
    def test_a_kit_name_expands_into_its_pieces(self):
        self.assertEqual(parse_instruments("drums"), ("kick", "snare", "hats"))
        self.assertEqual(
            parse_instruments("808s, drum kit"), ("808s", "kick", "snare", "hats")
        )
        self.assertEqual(
            expand_kits(["fullkit"]), ("kick", "snare", "hats", "clap", "toms")
        )

    def test_kit_pieces_are_real_instruments(self):
        for key, profile in INSTRUMENTS.items():
            for piece in profile.kit:
                self.assertIn(piece, INSTRUMENTS, key)
                self.assertEqual(INSTRUMENTS[piece].role, ROLE_DRUMS)

    def test_a_kit_produces_one_part_per_piece(self):
        parts = song_for(["drums"])
        self.assertEqual([part.key for part in parts], ["kick", "snare", "hats"])
        for part in parts:
            self.assertEqual(part.role, ROLE_DRUMS)
            self.assertEqual(part.register_label, "GM drum kit")

    def test_drum_pitches_stay_on_the_gm_map(self):
        for part in song_for(["fullkit"]):
            allowed = set(DRUM_PIECES[part.key])
            self.assertTrue(all(note.pitch in allowed for note in part.notes), part.label)

    def test_drums_are_never_transposed_by_register_placement(self):
        # A kit shares the drum map with melodic parts that move octaves; the
        # drums must not follow them out of the map.
        parts = song_for(["drums", "pad", "flute", "violin"])
        drum_parts = [part for part in parts if part.role == ROLE_DRUMS]
        self.assertEqual(len(drum_parts), 3)
        for part in drum_parts:
            self.assertTrue(all(note.pitch in DRUM_PIECES[part.key] for note in part.notes))

    def test_choices_include_the_kit_names(self):
        choices = instrument_choices()
        self.assertIn("Drums", choices)
        self.assertIn("Full kit", choices)
        self.assertIn("Hi-hats", choices)


class TestDrumGrooves(unittest.TestCase):
    """The kit has to sound like one drummer playing one song."""

    def parts_by_key(self, instruments, **kwargs):
        return {part.key: part for part in song_for(instruments, **kwargs)}

    def test_kick_snare_and_hats_share_one_groove(self):
        parts = self.parts_by_key(["kick", "snare", "hats"])
        feels = {part.register_note for part in parts.values()}
        self.assertEqual(len(feels), 1)
        self.assertIn("BPM", feels.pop())

    def test_the_backbeat_lands_on_2_and_4(self):
        snare = self.parts_by_key(["snare"], tempo_bpm=128.0)["snare"]
        for bar in range(4):
            steps = bar_steps(snare, bar)
            self.assertIn(4, steps)
            self.assertIn(12, steps)

    def test_half_time_feels_put_the_snare_on_three(self):
        feels = set()
        for seed in range(8):
            parts = song_for(["808s", "snare"], seed=seed, tempo_bpm=140.0)
            snare = next(part for part in parts if part.key == "snare")
            feel = snare.register_note.split(" groove")[0]
            feels.add(feel)
            if feel in ("trap", "half time"):
                self.assertIn(8, bar_steps(snare, 0), feel)
            else:
                self.assertEqual([4, 12], bar_steps(snare, 0), feel)
        # An 808 line-up is never only dance music.
        self.assertIn("trap", feels)

    def test_kick_never_flams_against_the_bass(self):
        parts = self.parts_by_key(["808s", "kick"])
        bass_steps = set(steps_of(parts["808s"]))
        for step in steps_of(parts["kick"]):
            near = {bass for bass in bass_steps if abs(bass - step) <= 0.5}
            if near:
                # A kick within a sixteenth of an 808 arrival has to be on it.
                self.assertIn(step, near, f"kick at step {step} flams the 808")

    def test_trap_kick_plays_the_808_line(self):
        checked = False
        for seed in range(8):
            parts = song_for(["808s", "kick"], seed=seed, tempo_bpm=140.0)
            bass = next(part for part in parts if part.key == "808s")
            kick = next(part for part in parts if part.key == "kick")
            if not kick.register_note.startswith("trap"):
                continue
            checked = True
            # The kick and the 808 hit as one instrument - nothing is left over.
            self.assertTrue(set(steps_of(bass)) <= set(steps_of(kick)), kick.register_note)
        self.assertTrue(checked, "no seed picked the trap groove")

    def test_a_new_chord_pulls_in_a_kick(self):
        parts = self.parts_by_key(["808s", "kick"])
        bass_steps = set(steps_of(parts["808s"]))
        kick_steps = set(steps_of(parts["kick"]))
        for chord in parse_progression("Am F C G"):
            step = round(chord.start * 4)
            if step in bass_steps:  # only when the low end actually arrives there
                self.assertIn(step, kick_steps, f"no kick with the 808 on {chord.label}")

    def test_kick_plays_on_every_downbeat_of_a_dance_groove(self):
        checked = False
        for seed in range(8):
            parts = song_for(["bass", "kick"], seed=seed, tempo_bpm=128.0)
            kick = next(part for part in parts if part.key == "kick")
            if "four on the floor" not in kick.register_note:
                continue
            checked = True
            for bar in range(4):
                self.assertIn(0, bar_steps(kick, bar))
        self.assertTrue(checked, "no seed picked the dance groove")

    def test_hats_keep_the_grid_moving(self):
        hats = self.parts_by_key(["hats"])["hats"]
        per_bar = [len(bar_steps(hats, bar)) for bar in range(4)]
        self.assertTrue(all(count >= 4 for count in per_bar), per_bar)
        self.assertGreater(sum(per_bar), len(per_bar) * 4, "hats should subdivide")

    def test_open_hats_never_double_a_closed_hat(self):
        hats = self.parts_by_key(["hats"], tempo_bpm=90.0)["hats"]
        closed = {note.start for note in hats.notes if note.pitch == DRUM_CLOSED_HAT}
        for note in hats.notes:
            if note.pitch == DRUM_OPEN_HAT:
                self.assertNotIn(note.start, closed)

    def test_phrase_ends_get_a_fill(self):
        # Four bars, so the last bar turns the phrase around.
        for instruments in (["snare"], ["toms"], ["fullkit"]):
            parts = song_for(instruments)
            fill_bar = [
                step for part in parts for step in bar_steps(part, 3)
                if step >= 12 and part.key in ("snare", "toms")
            ]
            self.assertTrue(fill_bar, f"no fill in the last bar of {instruments}")

    def test_no_fill_when_there_is_only_one_bar(self):
        for seed in range(6):
            snare = song_for(["snare"], text="Am", seed=seed, tempo_bpm=120.0)[0]
            self.assertTrue(snare.notes)
            last_beat = [step for step in bar_steps(snare, 0) if step >= 12]
            self.assertLessEqual(len(last_beat), 1, f"seed {seed} filled a one-bar song")

    def test_crash_marks_the_phrase_start(self):
        hats = self.parts_by_key(["hats"])["hats"]
        crash = [note.start for note in hats.notes if note.pitch == DRUM_CRASH]
        self.assertEqual(crash, [0])

    def test_drums_are_deterministic_and_re_roll(self):
        def signature(seed):
            return str(
                [
                    (part.key, [(n.pitch, n.start) for n in part.notes])
                    for part in song_for(["fullkit"], seed=seed)
                ]
            )

        self.assertEqual(signature(5), signature(5))
        kits = {signature(seed) for seed in range(5, 10)}
        self.assertGreater(len(kits), 1, "re-rolling changed nothing")

    def test_every_drum_piece_plays_something(self):
        for key in DRUM_PIECES:
            parts = song_for([key])
            self.assertTrue(parts[0].notes, f"{key} produced no notes")

    def test_drum_notes_stay_inside_the_song(self):
        end = max(chord.end for chord in parse_progression("Am F C G")) * PPQ
        for part in song_for(["fullkit"]):
            for note in part.notes:
                self.assertGreaterEqual(note.start, 0)
                self.assertLess(note.start, end)
                self.assertGreaterEqual(note.length, 1)
                self.assertTrue(0.1 <= note.velocity <= 1.0)

    def test_no_duplicate_hits_within_a_piece(self):
        for part in song_for(["fullkit"]):
            hits = [(note.start, note.pitch) for note in part.notes]
            self.assertEqual(len(hits), len(set(hits)), part.label)

    def test_tempo_drives_the_groove(self):
        slow = song_for(["kick", "snare"], tempo_bpm=85.0)
        fast = song_for(["kick", "snare"], tempo_bpm=140.0)
        self.assertIn("85 BPM", slow[0].register_note)
        self.assertIn("140 BPM", fast[0].register_note)


class TestRoleIdioms(unittest.TestCase):
    def chords(self, text="Am F C G"):
        return parse_progression(text)

    def test_bass_plays_chord_tones_in_the_low_end(self):
        part = song_for(["808s"])[0]
        chords = self.chords()
        self.assertLessEqual(max(part.pitches()), 36)
        for note in part.notes:
            chord = next(c for c in chords if c.contains(note.start / PPQ))
            self.assertIn(note.pitch % 12, chord_pitch_classes(chord.root, chord.template))

    def test_808s_hold_across_the_next_note_to_slide(self):
        part = song_for(["808s"])[0]
        overlaps = [
            part.notes[i + 1].start < part.notes[i].start + part.notes[i].length
            for i in range(len(part.notes) - 1)
        ]
        self.assertTrue(any(overlaps), "expected 808 notes to overlap so FL can slide")

    def test_plain_bass_leaves_a_gap_between_notes(self):
        part = song_for(["bass"])[0]
        for index in range(len(part.notes) - 1):
            current, following = part.notes[index], part.notes[index + 1]
            if following.start // PPQ == current.start // PPQ:
                self.assertLessEqual(current.start + current.length, following.start)

    def test_pad_sustains_voice_led_chords(self):
        part = song_for(["pad"])[0]
        chords = self.chords()
        groups = {}
        for note in part.notes:
            groups.setdefault(note.start, []).append(note)
        self.assertEqual(len(groups), len(chords))

        onsets = sorted(groups)
        for position, onset in enumerate(onsets):
            chord = next(c for c in chords if c.contains(onset / PPQ))
            tones = chord_pitch_classes(chord.root, chord.template)
            for note in groups[onset]:
                self.assertIn(note.pitch % 12, tones)
            # Sustained, not staccato.
            self.assertGreater(groups[onset][0].length, (chord.length * PPQ) // 2)
            if position:
                previous = sorted(note.pitch for note in groups[onsets[position - 1]])
                current = sorted(note.pitch for note in groups[onset])
                moves = [abs(a - b) for a, b in zip(previous, current)]
                self.assertTrue(max(moves) <= 7, f"voice jumped {moves} semitones")

    def test_arp_is_a_monophonic_chord_tone_pattern(self):
        part = song_for(["arp"])[0]
        chords = self.chords()
        by_onset = {}
        for note in part.notes:
            by_onset.setdefault(note.start, []).append(note.pitch)
        self.assertTrue(all(len(pitches) == 1 for pitches in by_onset.values()))
        self.assertGreater(len(by_onset), 8, "an arp should keep moving")
        for onset, (pitch,) in by_onset.items():
            chord = next(c for c in chords if c.contains(onset / PPQ))
            self.assertIn(pitch % 12, chord_pitch_classes(chord.root, chord.template))

    def test_keys_comping_respects_the_voice_count(self):
        part = song_for(["piano"])[0]
        profile = INSTRUMENTS["piano"]
        by_onset = {}
        for note in part.notes:
            by_onset.setdefault(note.start, []).append(note.pitch)
        for pitches in by_onset.values():
            self.assertLessEqual(len(pitches), profile.max_voices)
            self.assertEqual(len(set(pitches)), len(pitches))

    def test_lead_lines_stay_monophonic(self):
        for name in ("flute", "violin", "lead"):
            part = song_for([name])[0]
            for index in range(len(part.notes) - 1):
                current, following = part.notes[index], part.notes[index + 1]
                self.assertLessEqual(
                    current.start + current.length,
                    following.start,
                    f"{name} overlaps its own notes",
                )

    def test_lead_lines_move_around(self):
        part = song_for(["flute"])[0]
        pitches = part.pitches()
        self.assertGreater(len(set(pitches)), 3)
        self.assertGreaterEqual(min(pitches), INSTRUMENTS["flute"].lo)
        self.assertLessEqual(max(pitches), INSTRUMENTS["flute"].hi)

    def test_harmony_is_sparser_than_the_lead(self):
        lead, harmony = song_for(["lead", "harmony"])
        self.assertLess(len(harmony.notes), len(lead.notes))

    def test_notes_land_on_the_bar_grid(self):
        part = song_for(["pad"])[0]
        for note in part.notes:
            self.assertEqual(note.start % (PPQ * 4), 0, "pads should change on the bar")


if __name__ == "__main__":
    unittest.main()
