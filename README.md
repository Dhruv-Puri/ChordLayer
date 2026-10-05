# ChordLayer

**Name your instruments. Get the parts they would actually play.**

ChordLayer reads the chords already sitting in your project, then writes a part for every
instrument you name — an 808 line that holds and slides, a pad that moves by voice leading, a
flute melody, a piano comping part, a drum kit that locks to what they all play — each in its
own register with its own note lengths and articulation, all exported together as MIDI tracks.
There is nothing to tune: the instrument name *is* the preset, and the register, density, gate
and glide are set the way that instrument is played.

It ships in two forms, because FL Studio changed a lot between versions:

| Your FL Studio | Use | How it works |
| --- | --- | --- |
| **20.x and earlier** (no piano roll scripting) | **ChordLayer Studio** (`app/chordlayer_app.py`) | MIDI round-trip: export the chords, name your instruments, import all the parts back |
| **21.1+ / 2024 / 2025 / 2026** | `ChordLayer.pyscript` | Runs *inside* the piano roll: reads the roll, writes melody notes straight into it |

Both share the same generation engine, so seeds and results match.

```
   Am            F             C             G            ← your chords
   808s  ▄▄▄▄▄      ▄▄▄▄  ▄▄       ▄▄▄▄▄  ▄▄           ← root line that holds and slides
   pad   ▄▄▄▄▄      ▄▄▄▄▄▄▄      ▄▄▄▄▄▄▄       ▄▄▄▄      ← voice-led sustained chords
   flute  ▄▄ ▄▄▄      ▄▄▄ ▄▄      ▄▄▄▄  ▄▄     ▄▄▄       ← melody in its own octave
   drums ● ● ● ●    ●  ●● ● ●    ● ● ● ●    ● ●●●●      ← kick, snare and hats on one groove
```

Where the in-roll script runs, it can read **and write** the notes in your piano roll —
something no VST plugin can do. There is nothing to compile and no Python installation:
FL Studio runs the scripts with its own bundled interpreter.

---

## FL Studio version compatibility

ChordLayer is built on FL Studio's **piano roll scripting** API (`flpianoroll` / `.pyscript`),
which Image-Line released with **FL Studio 21.1**. Check what you are running first:

```bash
python install/install.py --detect
```

| FL Studio | Piano roll scripting | ChordLayer |
| --- | --- | --- |
| **20.x and earlier** | not available | **cannot run** — the API to read/write piano roll notes does not exist |
| 21.1, 21.2 | available | works |
| 2024, 2025, 2026 | available | works |

FL Studio 20 only supports Python for **MIDI controller** scripting, which can trigger notes
and control the mixer/playlist but has **no access to piano roll note data**. No script can
read your chords or write a melody into the roll on FL 20 — that is a hard platform limit,
not a ChordLayer limitation, and no install location changes it.

---

## ChordLayer Studio (for FL Studio 20 and earlier)

A small window that does the same job through MIDI files, because that is the only door FL 20
leaves open:

```
piano roll --Export as MIDI file--> ChordLayer Studio --Save all parts--> import back into FL
```

**Start it:** double-click `ChordLayer Studio.bat`, or run

```bash
python -B app/chordlayer_app.py
```

**Use it:**

1. In FL Studio 20, open the chord pattern's piano roll → **Piano roll menu → Export as MIDI file**.
2. Load that `.mid` in ChordLayer Studio (Browse → Load). It reports the chords and key it found.
3. Type the instruments you want — `808s, flute, violin, pad, drums` — and press **Generate song**.
   Every instrument gets its own part, in its own register, and the log lists what was written
   (a drum kit arrives as one part per piece, playing one groove).
4. **Save all parts…** writes one MIDI file with one track per instrument (plus a Chords track):
   import it in FL (**File → Import → MIDI file**, then tick **Create one channel per track**).
   **Save one file per instrument…** also writes a file per instrument, for dropping into one
   piano roll each.

Timing lines up bar-for-bar because the exported file keeps your project's PPQ, tempo and bar
length.

**Why the parts stay apart:** every instrument is written on its own MIDI channel and, in the
split files, into a file of its own with nothing else in it. FL Studio's importer routes by
*MIDI channel* ("Create one channel per track — imports each MIDI channel"), so parts sharing a
channel collapse into a single Instrument channel where the harmony drowns everything out. A
split file is always exactly one instrument — **no chord track** — so it can never import as the
chords. Not sure what to ask for? Press **Surprise me** for a balanced line-up, or **Re-roll**
for a fresh take — the same seed always rebuilds the same song.

No chords written yet? Type them instead: `Am F C G`, or `Dm7:4 Gm:2 A7:2` to set lengths.
Typed progressions work with or without a loaded file (with one, they override its harmony but
keep its timing).

The hand-tuning knobs for a single melody are still there (**Show the single-melody knobs**) with
**Generate single melody** — they never interfere with the instrument path.

Verify the whole pipeline works on your machine without opening a window:

```bash
python -B app/chordlayer_app.py --selftest
```

The window needs `tkinter`, which ships with standard Python builds. The `.bat` launcher uses
`pythonw -B`, so it installs nothing and leaves no cache folders behind.

---

## Instruments

Type any of these (aliases in brackets) into the app's instrument box; the engine writes that
instrument's part. Registers are shown in FL Studio's display octaves (48 = C4):

| Instrument | Also accepts | Register | What it plays |
| --- | --- | --- | --- |
| **808s** | `808`, `808 bass`, `boom 808`, `trap 808` | C1..C3 | root notes that hold and slide into the next one |
| **Sub bass** | `sub`, `sub bass`, `subbass`, `deep sub`, `sub bass line` | E1..G#2 | long, plain root notes under the arrangement |
| **Bass** | `bassline`, `bass line`, `synth bass`, `bass guitar`, `finger bass`, `electric bass`, `reese`, `reese bass` | C2..C4 | root/fifth line with a little movement |
| **Piano** | `keys`, `keyboard`, `rhodes`, `electric piano`, `e-piano`, `wurlitzer`, `clav`, `clavinet`, `acoustic piano`, `grand piano` | C3..C5 | voice-led chord stabs |
| **Organ** | `hammond`, `drawbar organ`, `church organ`, `b3` | C3..C5 | sustained chord stabs |
| **Guitar** | `acoustic guitar`, `electric guitar`, `rhythm guitar`, `strum`, `strummed guitar` | C3..C5 | strummed chords |
| **Pad** | `warm pad`, `synth pad`, `sustained`, `sustained pad`, `ambient pad` | C4..C6 | sustained voice-led chords |
| **Strings** | `string section`, `orchestral strings`, `ensemble`, `violins`, `cello section`, `choir`, `oohs`, `aahs`, `vocal pad` | C4..C6 | sustained section chords |
| **Flute** | `piccolo`, `recorder`, `whistle`, `pan flute` | C5..C7 | breathy arch-shaped melody |
| **Violin** | `fiddle`, `solo violin`, `viola` | C5..C7 | legato line that follows the voice leading |
| **Brass** | `trumpet`, `horn`, `sax`, `saxophone`, `trombone`, `flugel` | C4..C6 | syncopated, punchy melody |
| **Cello** | `violoncello`, `low strings` | C3..C5 | warm low line |
| **Lead synth** | `synth lead`, `saw lead`, `square lead`, `supersaw`, `lead synth` | C4..C6 | busy hook line |
| **Harmony line** | `harmony`, `counter`, `counter melody`, `countermelody`, `backing line` | C4..C6 | sparse second line under the melody |
| **Bell** | `bells`, `glockenspiel`, `music box`, `chimes`, `celesta` | C6..C8 | sparse high chord tones |
| **Pluck** | `plucky`, `pluck synth`, `marimba`, `kalimba`, `staccato synth`, `pizzicato` | C5..C7 | short sixteenth-note pattern |
| **Harp** | `arpeggio guitar`, `fingerpicked guitar`, `guitar arpeggio` | C4..C6 | ringing eighth-note rolls |
| **Arp** | `arpeggio`, `arpeggiator`, `sequence`, `synth arp` | C4..C6 | up/down chord-tone pattern |
| **Drums** | `drum`, `drum kit`, `drumkit`, `kit`, `beat`, `drum pattern`, `drum loop` | GM kit | the classic kit: kick + snare + hats, one groove between them |
| **Full kit** | `full drums`, `drum set`, `drumset`, `drum kit with fills` | GM kit | the above plus claps and toms |
| **Kick** | `kicks`, `kick drum`, `kick pattern`, `bass drum`, `bd`, `808 kick` | 36 | kick pattern locked to the bass onsets |
| **Snare** | `snares`, `snare drum`, `snare pattern`, `sd`, `backbeat` | 38 | backbeat, ghost notes, phrase-end fill |
| **Hi-hats** | `hat`, `hihat`, `hihats`, `hi hat`, `hi-hats`, `closed hats`, `hh` | 42/46/49 | hat grid with open hats and a crash per phrase |
| **Clap** | `claps`, `handclap`, `snap`, `fingersnap` | 39 | claps doubling the backbeat |
| **Toms** | `tom`, `tom fill`, `tom fills`, `floor tom`, `tom run` | 43..50 | descending tom fill at the end of each phrase |

Names are matched case-insensitively, extra words are fine (`warm pad` → Pad, `lush strings` →
**Strings**), and separators can be commas, `+`, `/`, `&` or the word *and*. A name it does not
know is an error rather than a silently dropped track, with the closest match suggested.

**The arranger shares out the octaves.** If two instruments want the same register (two leads,
two pads), the later one moves an octave — downward first, so accompaniment stays under the
melody — and the log says so, e.g. `Violin … (moved an octave down to leave room for Flute)`.

### What each kind of part does

| Role | Instruments | How the part is written |
| --- | --- | --- |
| **bass** | 808s, Sub bass, Bass | the chord root in the low register, a fifth or octave now and then, one note at a time. 808s extend each note past the next onset so FL's porta/slide has something to glide from; the other basses leave a small gap |
| **keys** | Piano, Organ, Guitar | voice-led chord voicings played as stabs; the full voicing lands on strong slots and weak slots get thinned to one or two notes. Guitar spreads the voices a few ticks apart (a strum); piano humanises timing slightly |
| **pad** | Pad, Strings | the same voice-led voicings, held for the whole chord instead of stabbed, and re-attacked once per bar when a chord lasts longer than a bar |
| **lead** | Flute, Violin, Brass, Cello, Lead synth | the melodic engine with that instrument's rhythm, density, contour and humanisation — guide-tone voice leading for Violin, an arch-shaped line for Flute, syncopation for Brass |
| **counter** | Harmony line | a sparser second line under the melody (fewer notes, more rests, its own register) |
| **arp** | Bell, Pluck, Harp, Arp | a chord-tone pattern that walks up and down inside each chord, re-anchoring on the root when the harmony changes |
| **drums** | Drums, Full kit, Kick, Snare, Hi-hats, Clap, Toms | one groove shared by every drum piece: the tempo picks the feel, the kick locks onto the bass onsets and chord changes, and the phrase turns around with a fill and a crash (see **Drums** below) |

Every part is generated from the same chords, tonic and scale with the song seed, so **Re-roll**
rewrites the whole arrangement coherently instead of shuffling one track. The only knob the
arranger reads is that seed — plus the optional Key/Scale override if you set one, which is how
you force a tonality when the relative keys are ambiguous (`Am F C G`).

---

## Drums

Name `drums` (or `full kit` for claps and tom fills) and the kit arrives as **one part per
piece** — its own track, its own colour in the preview, its own file in the split export — so in
FL each piece lands on its own drum channel:

```
808s, flute, violin, pad, drums     ->  808s | Flute | Violin | Pad | Kick | Snare | Hi-hats
```

The pieces play **one groove**, not four unrelated loops. Everything comes from the song:

- **The tempo picks the feel.** ~85 BPM and under leans boom bap / half time, 100–140 picks
trap or boom bap, 124 and up can go four-on-the-floor, and an 808 in the line-up tips the odds
towards trap. The seed still gets a say, so **Re-roll** gives you another take.
- **The kick locks onto the low end.** Feel patterns are the starting point; a kick that would
land a sixteenth away from an 808/bass arrival snaps onto it instead (no flam), and a chord
change where the bass plays pulls in a kick of its own. In the trap and half-time feels the
kick also *plays the 808 line*, so the two hit as one instrument.
- **The backbeat matches the feel.** Snare (and clap) on 2 and 4 for boom bap and dance feels,
on beat 3 for half-time and trap, with light ghost notes in between.
- **The hats subdivide the bar** — eighths for boom bap, offbeats for four-on-the-floor,
sixteenths (plus a 32nd roll) for trap — and an open hat never doubles a closed one.
- **Every phrase turns around.** The last bar of each four-bar phrase gets a fill: a snare
sixteenth run by default, or a descending tom run when the line-up has `toms`/`full kit`. A crash
marks the downbeat of each phrase.
- **Nothing flams.** All pieces are written through the same grid and clamp to the chord
timeline.

The pitches are the **General MIDI drum map** (kick 36, snare 38, clap 39, closed hat 42, open
hat 46, crash 49, toms 50/47/45/43), so a drum channel plays them without any remapping. Drums
are never transposed by the register-sharing logic — a kit piece plays its pitch, whatever the
melodic parts are doing.

---

## Install (the in-piano-roll script, FL 21.1+)

```bash
python install/install.py --detect    # show every FL Studio install + supported/not
python install/install.py             # install into the best detected folder
```

The installer understands both layouts FL Studio uses:

| Layout | Scripts folder |
| --- | --- |
| Standard | `Documents/Image-Line/FL Studio/Settings/Piano roll scripts/ChordLayer/` |
| **Portable** (e.g. `F:\Fl Studio`) | `F:\Fl Studio\User_data\Settings\Piano roll scripts\ChordLayer\` |

It only ever writes into that one `ChordLayer` folder, and it refuses to install into an FL
Studio version that cannot run piano roll scripts (pointing out why). Use `--dest <folder>` to
override the destination, or `--force` to refresh an existing install.

```
<your FL scripts folder>/ChordLayer/
├── ChordLayer.pyscript
├── ChordLayer Analyzer.pyscript
└── chordlayer/            ← the generation engine (plain .py modules)
```

Restart FL Studio (or reload the script list), then open any piano roll:
**Tools → Scripts → ChordLayer → ChordLayer**.

**Prefer one self-contained file?** Build the single-file version instead — it inlines the
whole engine, so there is no package folder to keep together:

```bash
python tools/build_standalone.py          # writes dist/ChordLayer (standalone).pyscript
```

**Manual install:** copy `scripts/ChordLayer.pyscript`, `scripts/ChordLayer Analyzer.pyscript`
and the `src/chordlayer` folder (renamed to `chordlayer`) into the same `ChordLayer` folder
described above. The sub-folder name becomes a category in FL's scripts menu.

### ChordLayer installs nothing on your machine

No `pip install`, no global packages, no virtualenv, no system Python changes:

- The project has **zero third-party dependencies** (there is no `requirements.txt`,
  `setup.py` or `pyproject.toml`). Everything imports only the Python standard library,
  plus FL Studio's own built-in `flpianoroll` module when running inside FL.
- `python install/install.py` **copies plain files** into FL Studio's *user data* folder
  (`Documents/Image-Line/FL Studio/Settings/Piano roll scripts/ChordLayer/`). Use `--dest`
  to point it somewhere else — it only ever writes into that one folder.
- `python tools/build_standalone.py` writes a single file into `dist/` inside this repo.
- FL Studio runs the scripts with its own bundled interpreter; your system Python is only
  used for the optional tests and the install/build helpers.

---

## Quick start (in-roll script, FL 21.1+)

1. Write a chord progression in the piano roll (one chord per bar, whole notes are fine).
2. **Tools → Scripts → ChordLayer → ChordLayer** and press **OK**. A melody appears.
3. Tweak knobs — the melody regenerates live. Happy with it? The generated notes are
   **selected** and share one **note color**, so `Ctrl+X` and paste them into another
   instrument's piano roll (or use *Select by color* in the piano roll's selection tools).

Nothing outside that one note color is ever touched, so your chords stay exactly as written.

### Useful workflows

| Goal | How |
| --- | --- |
| Melody for chords in the roll | Default settings, press OK. |
| No chords written yet | Type them into **Progression**: `Am F C G`, or `Dm7:4 Gm:2 A7:2` to set lengths in beats. |
| Melody for *selected* chords only | Select those notes, set **Source** to `Selection only`. |
| Layer several takes | Turn **Clear previous** off and change the **Seed** — takes stack up (each keeps the melody color). |
| Check what the script hears | Run **ChordLayer Analyzer** — it reports the detected chords and key per bar. |
| Move the melody to a synth | `Ctrl+X` (notes are already selected) or deselect all first if you want to keep them in place. |

---

## Controls (in-roll script)

| Control | What it does |
| --- | --- |
| **Source** | `Whole roll` analyses every note; `Selection only` uses just the selected notes. |
| **Progression (optional)** | Overrides detection with typed chords, e.g. `Am F C G`. Works even in an empty roll. |
| **Key / Scale** | `Auto` uses the key inferred from your chords; pick both explicitly to force a tonality. |
| **Rhythm** | `Eighths`, `Sixteenths`, `Syncopated`, `Offbeats`, `Ballad`, `Free`. |
| **Density** | Probability that each rhythmic slot plays (strong beats survive better). |
| **Rest chance** | Chance a slot rests; strong beats rest half as often. |
| **Gate** | Note length as a fraction of the slot. `Ballad` lets notes ring longer. |
| **Pitch strategy** | `Chord + passing` (chord tones on strong beats, scale tones between), `Arpeggio`, `Guide tones` (3rds/7ths voice-leading), `Random walk`. |
| **Contour** | `Arch` (rise and fall over the progression), `Follow chords` (step to the nearest 3rd/7th), `Motif repeat` (generate bar 1, replay it transposed), `Free`. |
| **Center octave / Range** | The melodic band. Center octave 4 = note 48 (C4 in FL's display); Range is in semitones either side. |
| **Motif bars** | Length of the motif used by the `Motif repeat` contour (0 = off). |
| **Velocity / jitter / Timing jitter** | Humanisation amounts. |
| **Melody color** | Which of the 16 piano roll note colors tags generated notes. Keep it distinct from your chords. |
| **Clear previous** | Deletes notes of the melody color before regenerating (leave on for a stable, idempotent re-run). |
| **Seed** | Rerolls the random choices. Same seed + same settings = the same melody, every time. |

---

## How it works

```
piano roll notes (or a MIDI file)      "808s, flute, violin, pad"
            │                                      │
            ▼                                      ▼
     chord detection ──▶ key inference ──▶ instrument profiles + shared registers
                                                     │
                          ┌──────────────────────────┴──────────────────────────┐
                          ▼                                                     ▼
      tagged notes in the roll (in-roll script)         a .mid with one track per part
```

1. **Chord detection** slices the timeline at every note start, classifies the sounding
   pitch classes against chord templates (maj, min, dim, aug, sus, 6, 7, maj7, m7, m7b5, dim7,
   add9, 9, maj9, m9 …), scores roots with a bass-note bonus (so inversions resolve to the
   right root), merges adjacent identical slices and drops flashes shorter than a 16th note.
2. **Key inference** weights the detected chords (durations + the first chord, which is
   usually home) against major/minor scales to guess the key for scale-tone selection.
3. **Rhythm** builds slots from the chosen preset, thinned by Density with a survival bonus
   on strong beats so the pulse survives.
4. **Pitch selection** picks chord tones on strong slots and scale tones elsewhere, honours
   the contour, keeps everything inside the range, avoids immediate repeats, and applies the
   Motif contour by re-transposing the recorded intervals onto each new chord.
5. **Humanisation** then applies velocity/timing jitter and gate lengths before the notes are
   written back with the melody color.
6. **Instrument arranging** (the companion app) then maps each named instrument to a profile:
   the profile chooses the role, register, rhythm, density, gate, glide, voice count and
   humanisation. Bass/arp/key/pad builders write their pattern directly; lead and counter parts
   go through steps 3-5 above with the instrument's settings. Chord-playing parts share one
   voice-leading routine, so every harmony note is a chord tone chosen by the shortest move
   from the previous voicing, and registers are handed out so named instruments do not collide.
   Nothing in that pipeline reads a user knob except the song seed (and the optional Key/Scale
   override, which applies to both paths).

### Repository layout

```
src/chordlayer/          pure-Python engine (no FL imports, fully unit-testable)
├── theory.py            note/scale/chord tables, chord-name parsing, key inference
├── chord_detect.py      timeline slicing, chord recognition, progression text
├── generator.py         rhythm grid, pitch strategies, contours, motifs, humanisation
├── instruments.py       instrument profiles + the arranger (one idiomatic part per instrument)
├── midi_io.py           Standard MIDI File reader/writer, one MIDI channel per part (stdlib only)
├── session.py           MIDI in -> chords -> instrument parts -> multi-track MIDI (app core)
└── rng.py               seeded RNG helpers
scripts/
├── ChordLayer.pyscript          the in-roll script (dialog + FL glue)
└── ChordLayer Analyzer.pyscript  diagnostic: what chords/key did it detect?
app/
└── chordlayer_app.py            ChordLayer Studio window (+ headless --selftest)
ChordLayer Studio.bat            double-click launcher (pythonw -B, installs nothing)
tests/                   unittest suite + a fake flpianoroll module
flpianoroll              (fake module under tests/ lets the real scripts run headless)
tools/build_standalone.py        single-file build of the in-roll script
install/install.py               copies scripts into FL's user scripts folder
```

The FL-facing code is deliberately thin. The script's `apply()` reads the piano roll into
plain objects and writes notes back; the app's window is a thin shell over `session.Session`.
Both call the same engine, which is why the whole pipeline is testable without FL Studio.

---

## Development

```bash
python -B -m unittest discover -s tests -v  # 217 tests, stdlib only, no dependencies
python -B app/chordlayer_app.py --selftest  # end-to-end MIDI round-trip, no window
python -B app/chordlayer_app.py --smoke-gui # build the real window, generate, draw, close
tools/build_standalone.py                   # single-file build of the in-roll script
python install/install.py --detect          # show FL installs and script folders
python install/install.py --dest <folder>   # install somewhere explicit
```

`-B` keeps Python from writing `__pycache__` bytecode folders, so running the tests leaves
no artifacts behind. (Plain `python -m unittest` works too; the caches it creates are
git-ignored and safe to delete.)

`tests/fake_flp.py` is a fake `flpianoroll` module (Note, Marker, score, ScriptDialog,
Utils) injected into `sys.modules`, which lets the *real* `.pyscript` files execute headless.
The suite covers chord parsing/detection, key inference, generator properties (range bounds,
chord tones on strong beats, determinism per seed, motif contour, preset coverage), script
behaviour (idempotent re-apply, selection mode, progression override, colour cleanup), the
standalone build, the installer's FL detection (including portable installs and the 21.1
version gate), MIDI file reading/writing (running status, tempo, time signature, unmatched and
overlapping notes, SMPTE fallback), the instrument arranger (alias parsing, register sharing,
determinism, and the idiom of each role: chord tones only, sustained voice-led pads,
monophonic leads, 808 slide tails) and the companion app's headless round-trip.
All tests write only to temporary folders.

---

## Limitations

- **FL Studio 20 and earlier cannot run the in-roll script at all** (no piano roll scripting API).
  Use ChordLayer Studio, which trades the in-roll convenience for a MIDI file round-trip.
- A piano roll script can only see **the piano roll it was launched from**: one channel, one
  pattern. Chords on another channel are not readable — type the progression instead, or keep
  the chords in the same roll. This is an FL API limit, not a ChordLayer choice.
- ChordLayer Studio needs the chords in a MIDI file, so the round-trip is a few extra clicks:
  export from FL, generate, import back. It cannot capture what you play live.
- Key inference is a heuristic; `Am F C G` and `C G Am F` are genuinely ambiguous (relative
  keys). Override **Key/Scale** when you want a specific tonality.
- Strong/medium beats follow the project time signature, but the phrase shapes (`Arch`,
  `Motif repeat`) assume 4/4 phrasing.
- Script edits are not part of FL's undo stack: re-running with **Clear previous** on is the
  supported way to back out. Nothing outside the melody color is modified.
- Melody notes are written to the *current* channel. Use the note color (or cut/paste) to
  move them — a dedicated "send to channel" step is on the roadmap.
- Instrument registers are **fixed per instrument** rather than key-relative: an 808 line is
  always a C1–C3 line, a flute always sings up in C5–C7. That is how producers pick octaves in
  practice, but it does mean you move the part (or the sample) if you want a different octave.
- The instrument arranger lives in the companion engine, so **FL Studio 20 via ChordLayer
  Studio is where multi-instrument songs come from**. The in-roll script (FL 21.1+) still writes
  one melodic line at a time; wiring the arranger into that dialog is on the roadmap.
- Drum parts are written as **GM drum-map notes on a normal MIDI track**, not as an FL drum
  pattern: drop each piece onto its own drum channel (or use `FPC`/`Drumaxx` and let the pitches
  map themselves). Fills are one bar per four-bar phrase; there is no separate fill library.
- Two parts can share an octave when they are different roles (a pad under a violin, say). The
  arranger only forces a shift when instruments from the same group clash.
- The preview shows note positions, not sound: ChordLayer never renders audio. The MIDI file it
  writes is what you hear through your own instrument/sample.

---

## Roadmap

- **Harmoniser mode** — keep a user melody and write chord voicings, bass lines or arpeggios
  underneath it, clash-free and voice-led (both the script and the app).
- **Bring the arranger into the in-roll script** (FL 21.1+): an instrument list in the dialog and
  several color-tagged parts written in one pass, one per named instrument.
- More instruments and style presets (plucked strings, ethnic winds, latin percussion,
  half-time breaks), a swing/shuffle amount for the drums, plus per-part register overrides for
  the "I want it an octave up" case.
- Chord-suggestion tools ("what fits this melody?"), scale/degree helpers.
- Batch mode: a folder of chords MIDI files in, full multi-instrument songs out.
- A real-time companion (VST / VFX Script) for live layering while you play.

---

## Manual QA checklist (ChordLayer Studio, FL 20)

- [ ] `python -B app/chordlayer_app.py --selftest` prints `SELFTEST OK` with 7 instrument parts,
      including the three pieces of the drum kit.
- [ ] Double-clicking `ChordLayer Studio.bat` opens the window (no console, no cache folders).
- [ ] Export a chord pattern from FL 20's piano roll → **Browse** → the chip reports notes/PPQ/BPM,
      the app generates a song straight away and the log lists one line per instrument.
- [ ] Detected chords and key match what you wrote; the preview shows one colour per instrument.
- [ ] Typing `808s, flute, violin, pad` gives a low root line, a melody, a counter line and
      sustained chords — and the violin's log line mentions the octave it moved to.
- [ ] Adding `drums` adds Kick, Snare and Hi-hats, all three logging the *same* feel and BPM, with
      the kick landing on the 808 onsets and a fill in the last bar of every four-bar phrase.
- [ ] A busy chords file at a different tempo (import one, or type a slower progression) gives a
      matching feel — boom bap under ~100 BPM, trap from ~100 up with an 808 in the line-up.
- [ ] A nonsense name (`kazoo`) logs a friendly error and keeps the previous arrangement.
- [ ] **Surprise me** fills in a line-up; **Re-roll** rewrites every part at once.
- [ ] **Save all parts…** → importing the file into FL (with **Create one channel per track**)
      gives one Instrument channel per part, in register — the parts are not merged into the chords.
- [ ] **Save one file per instrument…** → each file drags into its own piano roll, bar-aligned, and
      holds that instrument alone (no chord track).
- [ ] **Show the single-melody knobs** still hand-tunes one melody (Generate single melody).
- [ ] Closing the window and re-opening keeps working (no leftover locks or temp files).

## Manual QA checklist (in-roll script, FL 21.1+)

Run through this after installing, and after any engine change:

- [ ] Script appears under **Tools → Scripts → ChordLayer** and opens its dialog.
- [ ] Chords on whole notes: press OK → melody appears, chords untouched.
- [ ] Melody notes are all one color and selected; `Ctrl+X` moves them out cleanly.
- [ ] Tweak **Seed** → the melody changes; tweak it back → the original melody returns.
- [ ] Drag **Density** to 0 → only a few notes remain; **Rhythm** to `Ballad` → long notes.
- [ ] **Contour** `Follow chords` sounds voice-led; `Motif repeat` repeats bar 1 per motif bars.
- [ ] Empty roll + typed progression (`Am F C G`) → melody generated, no chord notes added.
- [ ] Select some notes and set **Source** to `Selection only` → only those chords are used.
- [ ] 3/4 project: accents land per bar, no notes outside the piano roll's range.
- [ ] **Clear previous** off → a second pass layers more notes; on → the result is stable.
- [ ] Debug log (View → Script output) shows the note count and no tracebacks.
- [ ] **ChordLayer Analyzer** reports plausible chords and the expected key.
