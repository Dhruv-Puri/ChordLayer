# -*- coding: utf-8 -*-
"""ChordLayer Studio - the FL Studio 20 companion window.

FL Studio 20 has no piano roll scripting, so this app works by MIDI round-trip:

    1. In FL Studio 20, open the chords in the piano roll and use
       Piano roll menu -> Export as MIDI file.
    2. Open that .mid here, type the instruments you want (808s, flute,
       violin, pad, drums), press Generate song.
    3. Press Save all parts..., then import the saved file (tick "Create one
       channel per track"): every instrument arrives as its own track on its
       own MIDI channel, playing what that instrument actually plays.

Save one file per instrument... writes one file per part instead, each holding
that instrument alone - no chord track - so a stem is never the harmony.

The instrument list is the only required input - the register, note lengths,
articulation and density come from the instrument profiles. The old single
melody knobs are still there behind "Advanced", for anyone who wants them.

Run it with:

    python -B app/chordlayer_app.py

Nothing is installed, and -B keeps Python from writing cache folders.
`--selftest` runs the whole pipeline headlessly (no window) as a sanity check.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from chordlayer.generator import CONTOURS, RHYTHM_PRESETS, STRATEGIES  # noqa: E402
from chordlayer.instruments import (  # noqa: E402
    DEFAULT_INSTRUMENTS,
    INSTRUMENTS,
    instrument_choices,
)
from chordlayer.midi_io import MidiNote, write_midi  # noqa: E402
from chordlayer.session import (  # noqa: E402
    KEY_CHOICES,
    SCALE_CHOICES,
    Session,
    SessionError,
)

TITLE = "ChordLayer Studio - write a part for every instrument (FL Studio MIDI)"
MELODY_COLOR = "#4da3ff"
CHORD_COLOR = "#3b4a5a"
CHORD_BAND = "#232c36"
CHORD_TEXT = "#9fb3c8"

# One-click starting points for people who just want to hear something.
SURPRISE_COMBOS = (
    "808s, pad, flute, drums",
    "808s, piano, violin, bell, full kit",
    "bass, strings, guitar, lead, drums",
    "sub, pad, cello, harp",
    "808s, guitar, harmony, pluck, drums",
    "bass, organ, brass, arp, full kit",
)


# ---------------------------------------------------------------------------
# Headless self-test (no window): proves the pipeline works on this machine.
# ---------------------------------------------------------------------------


def build_demo_chords(path: str, ticks_per_beat: int = 480) -> str:
    """Write a small Am / F / C / G chords file to exercise the pipeline."""
    bar = ticks_per_beat * 4
    chords = [("Am", [57, 60, 64]), ("F", [53, 57, 60]), ("C", [48, 52, 55]), ("G", [55, 59, 62])]
    notes = [
        MidiNote(pitch=pitch, start=index * bar, length=bar, velocity=0.75)
        for index, (_label, pitches) in enumerate(chords)
        for pitch in pitches
    ]
    write_midi(path, notes, ticks_per_beat=ticks_per_beat, tempo_bpm=120, track_name="Chords")
    return path


def selftest(instruments: str = "808s, flute, violin, pad, drums") -> int:
    """Run the whole round-trip (chords in, several instrument and drum parts out)."""
    with tempfile.TemporaryDirectory() as tmp:
        chords_path = build_demo_chords(os.path.join(tmp, "demo-chords.mid"))
        session = Session()
        session.load_midi(chords_path)
        try:
            session.set_instruments(instruments)
            analysis = session.analyze_song()
        except SessionError as error:
            print(f"SELFTEST FAILED: {error}")
            return 1
        out_path = session.save_parts(
            os.path.join(tmp, "demo-song.mid"), include_chords=True, split=True
        )[0]
        print("ChordLayer Studio self-test")
        for line in session.log:
            print(f"  {line}")
        print(f"  chords detected: {', '.join(analysis.chord_labels())}")
        print(f"  key: {analysis.key_label}")
        total = sum(len(part.notes) for part in analysis.parts)
        print(
            f"  melody: {total} notes in {len(analysis.parts)} instrument part(s) "
            f"-> {out_path}"
        )
        print("  SELFTEST OK")
    return 0


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


def run_gui(initial_file: str | None = None, smoke: bool = False) -> int:
    """Open the window. ``smoke`` builds it, generates once and closes (no mainloop)."""
    try:
        import tkinter as tk
        from tkinter import filedialog, ttk
    except ImportError:
        print(
            "This Python build has no tkinter, so the window cannot open.\n"
            "Use 'python app/chordlayer_app.py --selftest' to verify the engine."
        )
        return 1

    class ChordLayerStudio:
        def __init__(self, root):
            self.root = root
            self.session = Session()
            self.mode = "song"
            self.surprise_index = 0
            self._build()
            if initial_file:
                self._load_file(initial_file)

        # -- construction --------------------------------------------------

        def _build(self):
            self.root.title(TITLE)
            self.root.minsize(880, 660)

            outer = ttk.Frame(self.root, padding=10)
            outer.pack(fill="both", expand=True)
            outer.columnconfigure(0, weight=1)
            outer.rowconfigure(5, weight=1)

            # 1. chords -----------------------------------------------------
            source = ttk.LabelFrame(outer, text="1. Chords", padding=8)
            source.grid(row=0, column=0, sticky="ew")
            source.columnconfigure(1, weight=1)

            self.path_var = tk.StringVar()
            ttk.Label(source, text="Chords MIDI:").grid(row=0, column=0, sticky="w")
            ttk.Entry(source, textvariable=self.path_var).grid(
                row=0, column=1, sticky="ew", padx=6
            )
            ttk.Button(source, text="Browse...", command=self._browse).grid(row=0, column=2)
            ttk.Button(source, text="Load", command=self._load_from_entry).grid(
                row=0, column=3, padx=(6, 0)
            )

            self.progression_var = tk.StringVar()
            ttk.Label(source, text="or type chords:").grid(row=1, column=0, sticky="w", pady=(6, 0))
            progression_entry = ttk.Entry(source, textvariable=self.progression_var)
            progression_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
            ttk.Label(source, text="e.g. Am F C G").grid(row=1, column=2, columnspan=2, sticky="w")
            ttk.Button(source, text="Use text", command=self._use_progression).grid(
                row=1, column=3, padx=(6, 0), pady=(6, 0)
            )
            self.source_info = ttk.Label(source, text="No chords loaded yet.")
            self.source_info.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

            # 2. instruments ------------------------------------------------
            instruments = ttk.LabelFrame(
                outer,
                text="2. Instruments (this is all you have to choose)",
                padding=8,
            )
            instruments.grid(row=1, column=0, sticky="ew", pady=8)
            instruments.columnconfigure(1, weight=1)

            self.instruments_var = tk.StringVar(
                value=", ".join(INSTRUMENTS[key].label for key in DEFAULT_INSTRUMENTS)
            )
            ttk.Label(instruments, text="Instruments:").grid(row=0, column=0, sticky="w")
            entry = ttk.Entry(instruments, textvariable=self.instruments_var)
            entry.grid(row=0, column=1, sticky="ew", padx=6)
            entry.bind("<Return>", lambda _event: self._generate_song())
            ttk.Button(instruments, text="Surprise me", command=self._surprise).grid(
                row=0, column=2
            )
            ttk.Label(
                instruments,
                text="Examples: 808s, flute, violin, pad, drums   |   bass, piano, strings, full kit   |   "
                "808s, guitar, harmony, bell   |   sub, cello, harp, lead",
                wraplength=780,
                foreground="#5b6b7c",
            ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

            # actions -------------------------------------------------------
            actions = ttk.Frame(outer)
            actions.grid(row=2, column=0, sticky="ew")
            ttk.Button(actions, text="Generate song", command=self._generate_song).pack(
                side="left"
            )
            ttk.Button(actions, text="Re-roll", command=self._reroll).pack(side="left", padx=6)
            ttk.Button(actions, text="Save all parts...", command=self._save_parts).pack(
                side="left"
            )
            ttk.Button(
                actions, text="Save one file per instrument...", command=self._save_split
            ).pack(side="left", padx=6)
            self.status = ttk.Label(actions, text="")
            self.status.pack(side="right")

            self.advanced_var = tk.IntVar(value=0)
            ttk.Checkbutton(
                outer,
                text="Show the single-melody knobs (optional - not needed for a song)",
                variable=self.advanced_var,
                command=self._toggle_advanced,
            ).grid(row=3, column=0, sticky="w", pady=(6, 0))

            # 3. optional single-melody knobs -------------------------------
            self.advanced = ttk.LabelFrame(outer, text="Optional: one hand-tuned melody", padding=8)
            self.advanced.grid(row=4, column=0, sticky="ew", pady=6)
            self.advanced.grid_remove()
            for column in range(6):
                self.advanced.columnconfigure(column, weight=1)

            self.vars = {}
            self.combo_vars = {}
            self.combo_values = {}
            combos = [
                ("Rhythm", "rhythm_index", RHYTHM_PRESETS),
                ("Strategy", "strategy_index", STRATEGIES),
                ("Contour", "contour_index", CONTOURS),
                ("Key", "key_index", KEY_CHOICES),
                ("Scale", "scale_index", SCALE_CHOICES),
            ]
            for index, (label, key, values) in enumerate(combos):
                row, column = divmod(index, 3)
                ttk.Label(self.advanced, text=label).grid(
                    row=row * 2, column=column * 2, sticky="w", pady=(0, 6)
                )
                var = tk.StringVar(value=values[0])
                self.combo_vars[key] = var
                self.combo_values[key] = tuple(values)
                ttk.Combobox(
                    self.advanced,
                    textvariable=var,
                    values=list(values),
                    width=20,
                    state="readonly",
                ).grid(
                    row=row * 2,
                    column=column * 2 + 1,
                    columnspan=2,
                    sticky="w",
                    padx=(4, 16),
                    pady=(0, 6),
                )

            knobs = [
                ("Density", "density", 0.0, 1.0, 0.05),
                ("Rest chance", "rest_chance", 0.0, 0.9, 0.05),
                ("Gate", "gate", 0.1, 1.0, 0.05),
                ("Velocity", "velocity", 0.1, 1.0, 0.05),
                ("Vel jitter", "velocity_jitter", 0.0, 0.4, 0.01),
                ("Timing jitter", "timing_jitter", 0.0, 60.0, 1.0),
            ]
            base_row = 4
            for index, (label, key, low, high, step) in enumerate(knobs):
                row, column = divmod(index, 3)
                ttk.Label(self.advanced, text=label).grid(
                    row=base_row + row * 2, column=column * 2, sticky="w"
                )
                var = tk.DoubleVar(value=0.0)
                self.vars[key] = var
                ttk.Spinbox(
                    self.advanced, textvariable=var, from_=low, to=high, increment=step, width=6
                ).grid(row=base_row + row * 2, column=column * 2 + 1, sticky="w", padx=(4, 12))

            spin_ints = [
                ("Center octave", "center_octave", 0, 9),
                ("Range (semitones)", "range_semitones", 1, 24),
                ("Motif bars", "motif_bars", 0, 8),
                ("Track", "track_index", -1, 15),
                ("Seed", "seed", 0, 9999),
            ]
            int_row = base_row + 4
            for index, (label, key, low, high) in enumerate(spin_ints):
                row, column = divmod(index, 3)
                ttk.Label(self.advanced, text=label).grid(
                    row=int_row + row * 2, column=column * 2, sticky="w"
                )
                var = tk.IntVar(value=0)
                self.vars[key] = var
                ttk.Spinbox(
                    self.advanced, textvariable=var, from_=low, to=high, width=6
                ).grid(row=int_row + row * 2, column=column * 2 + 1, sticky="w", padx=(4, 12))

            melody_actions = ttk.Frame(self.advanced)
            melody_actions.grid(row=int_row + 4, column=0, columnspan=6, sticky="w", pady=(8, 0))
            ttk.Button(
                melody_actions, text="Generate single melody", command=self._generate_melody
            ).pack(side="left")
            ttk.Button(
                melody_actions, text="Save melody MIDI...", command=self._save_melody
            ).pack(side="left", padx=6)
            ttk.Button(
                melody_actions, text="Save melody + chords...", command=self._save_melody_chords
            ).pack(side="left")

            # preview + log -------------------------------------------------
            preview = ttk.LabelFrame(
                outer,
                text="Preview (chords behind, one colour per instrument)",
                padding=4,
            )
            preview.grid(row=5, column=0, sticky="nsew")
            preview.columnconfigure(0, weight=1)
            preview.rowconfigure(0, weight=1)
            self.canvas = tk.Canvas(preview, background="#161b22", height=280, highlightthickness=0)
            self.canvas.grid(row=0, column=0, sticky="nsew")
            self.canvas.bind("<Configure>", lambda _event: self._draw_preview())

            log_frame = ttk.LabelFrame(outer, text="Log", padding=4)
            log_frame.grid(row=6, column=0, sticky="ew", pady=(8, 0))
            log_frame.columnconfigure(0, weight=1)
            self.log_text = tk.Text(log_frame, height=7, wrap="word")
            self.log_text.grid(row=0, column=0, sticky="ew")

            self._sync_ui_from_options()
            self._write_log(
                [
                    "Ready. In FL Studio 20: piano roll menu -> Export as MIDI file,",
                    "then load it here, name your instruments and press Generate song.",
                    "Known instruments: " + ", ".join(instrument_choices()),
                ]
            )

        # -- helpers -------------------------------------------------------

        def _write_log(self, lines):
            for line in lines:
                self.log_text.insert("end", line + "\n")
            self.log_text.see("end")

        def _toggle_advanced(self):
            if self.advanced_var.get():
                self.advanced.grid()
            else:
                self.advanced.grid_remove()

        def _instrument_text(self) -> str:
            keys = self.session.instruments()
            return ", ".join(INSTRUMENTS[key].label for key in keys)

        def _sync_ui_from_options(self):
            options = self.session.options
            for key, var in self.vars.items():
                current = getattr(options, key)
                var.set(current)
            for key, var in self.combo_vars.items():
                values = self.combo_values[key]
                index = int(getattr(options, key)) % len(values)
                var.set(values[index])
            self.instruments_var.set(self._instrument_text())

        def _read_options(self):
            values = {}
            for key, var in self.vars.items():
                try:
                    value = var.get()
                except Exception:  # tk raises while a Spinbox is empty
                    continue
                values[key] = type(getattr(self.session.options, key))(value)
            for key, var in self.combo_vars.items():
                try:
                    values[key] = self.combo_values[key].index(var.get())
                except ValueError:
                    continue
            self.session.options = self.session.options.copy_with(**values)

        # -- actions -------------------------------------------------------

        def _browse(self):
            path = filedialog.askopenfilename(
                title="Select a chords MIDI file",
                filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")],
            )
            if path:
                self.path_var.set(path)
                self._load_file(path)

        def _load_from_entry(self):
            path = self.path_var.get().strip().strip('"')
            if path:
                self._load_file(path)

        def _load_file(self, path):
            try:
                midi = self.session.load_midi(path)
            except SessionError as error:
                self._write_log([f"Problem: {error}"])
                return
            self.source_info.config(
                text=f"Loaded {os.path.basename(path)}: {len(midi.notes)} notes, "
                f"{midi.ticks_per_beat:g} PPQ, {midi.tempo_bpm:g} BPM, "
                f"{midi.time_signature[0]}/{midi.time_signature[1]}"
            )
            self._write_log(session_lines(self.session))
            self._generate_song()

        def _use_progression(self):
            text = self.progression_var.get().strip()
            self.session.set_progression(text)
            if text:
                # Typed chords override the file's harmony while keeping its
                # timing (PPQ, tempo, bar length), exactly like the script does.
                detail = "over the loaded file's timing" if self.session.midi else "standalone"
                self.source_info.config(
                    text=f"Using typed progression '{text}' ({detail})"
                )
            self._generate_song()

        def _generate_song(self):
            self.mode = "song"
            text = self.instruments_var.get().strip()
            if not text:
                text = ", ".join(INSTRUMENTS[key].label for key in DEFAULT_INSTRUMENTS)
                self.instruments_var.set(text)
            try:
                keys = self.session.set_instruments(text)
            except SessionError as error:
                self._write_log([f"Problem: {error}"])
                self.status.config(text="check instruments")
                return
            self._read_options()
            try:
                analysis = self.session.analyze_song()
            except SessionError as error:
                self._write_log([f"Problem: {error}"])
                self.status.config(text="no song")
                return
            total = sum(len(part.notes) for part in analysis.parts)
            self.status.config(text=f"{len(analysis.parts)} parts / {total} notes")
            self._write_log(analysis.summary_lines())
            self._draw_preview()

        def _generate_melody(self):
            self.mode = "melody"
            self._read_options()
            try:
                analysis = self.session.analyze()
            except SessionError as error:
                self._write_log([f"Problem: {error}"])
                self.status.config(text="no melody")
                return
            self.status.config(text=f"{len(analysis.melody)} notes")
            self._write_log(analysis.summary_lines())
            self._draw_preview()

        def _surprise(self):
            seed = self.session.options.seed
            combo = SURPRISE_COMBOS[(self.surprise_index + seed) % len(SURPRISE_COMBOS)]
            self.surprise_index += 1
            self.instruments_var.set(combo)
            self._generate_song()

        def _reroll(self):
            seed = self.session.reroll()
            self.vars["seed"].set(seed)
            self._write_log([f"Seed -> {seed}"])
            if self.mode == "melody":
                self._generate_melody()
            else:
                self._generate_song()

        # -- saving --------------------------------------------------------

        def _ask_path(self, default_name, title="Save generated MIDI"):
            return filedialog.asksaveasfilename(
                title=title,
                defaultextension=".mid",
                initialfile=default_name,
                filetypes=[("MIDI files", "*.mid")],
            )

        def _save_parts(self):
            self._save_song(split=False)

        def _save_split(self):
            self._save_song(split=True)

        def _save_song(self, split):
            if not any(part.notes for part in self.session.analysis.parts):
                self._write_log(["Nothing to save yet - press Generate song first."])
                return
            path = self._ask_path("chordlayer-song.mid")
            if not path:
                return
            try:
                written = self.session.save_parts(path, include_chords=True, split=split)
            except SessionError as error:
                self._write_log([f"Problem: {error}"])
                return
            lines = [f"Saved: {written[0]}"]
            if split:
                lines.extend(
                    f"      {extra}   (this instrument only)" for extra in written[1:]
                )
            lines.extend(
                [
                    "In FL Studio: File > Import > MIDI file and tick 'Create one channel",
                    "per track' - each instrument sits on its own MIDI channel and in its",
                    "own register, so the parts never collapse into the chords.",
                    "A single-instrument file holds that part and nothing else: drag it",
                    "straight onto the channel/piano roll you want it in.",
                ]
            )
            self._write_log(lines)

        def _save_melody(self):
            self._save_melody_variant(include_chords=False)

        def _save_melody_chords(self):
            self._save_melody_variant(include_chords=True)

        def _save_melody_variant(self, include_chords):
            if not self.session.analysis.melody:
                self._write_log(["Nothing to save yet - press Generate first."])
                return
            default_name = "chordlayer-parts.mid" if include_chords else "chordlayer-melody.mid"
            path = self._ask_path(default_name)
            if not path:
                return
            try:
                self.session.save_melody(path, include_chords=include_chords)
            except SessionError as error:
                self._write_log([f"Problem: {error}"])
                return
            self._write_log(
                [
                    f"Saved: {path}",
                    "Now drag it into the FL Studio piano roll (or use Import MIDI",
                    "file) - it lines up bar-for-bar with the source chords.",
                ]
            )

        # -- preview -------------------------------------------------------

        def _draw_preview(self):
            canvas = self.canvas
            canvas.delete("all")
            analysis = self.session.analysis
            width = max(10, canvas.winfo_width())
            height = max(10, canvas.winfo_height())

            if not analysis.chords:
                canvas.create_text(
                    width // 2, height // 2, fill="#6b7c93",
                    text="Load chords, name your instruments and press Generate song",
                )
                return

            series = [(part.label, part.color, part.notes) for part in analysis.parts]
            if not series:
                series = [("Melody", MELODY_COLOR, analysis.melody)]

            total_ticks = max(chord.end for chord in analysis.chords) * analysis.ticks_per_beat
            total_ticks = max(1.0, total_ticks)
            margin = 8
            legend_height = 18 if len(series) > 1 else 0

            def x_of(tick):
                return margin + (width - margin * 2) * (tick / total_ticks)

            pitches = [note.pitch for _label, _color, notes in series for note in notes] or [60]
            chord_roots = [chord.root + 48 for chord in analysis.chords]
            low = min(pitches + chord_roots) - 2
            high = max(pitches + chord_roots) + 2
            span = max(1, high - low)

            def y_of(pitch):
                return (
                    height - margin - legend_height
                    - (height - margin * 2 - legend_height) * ((pitch - low) / span)
                )

            # chord bands + labels
            for chord in analysis.chords:
                start = chord.start * analysis.ticks_per_beat
                end = chord.end * analysis.ticks_per_beat
                canvas.create_rectangle(
                    x_of(start), margin, x_of(end), height - margin - legend_height,
                    fill=CHORD_BAND, outline="",
                )
                canvas.create_rectangle(
                    x_of(start), y_of(chord.root + 48) - 4, x_of(end), y_of(chord.root + 48) + 4,
                    fill=CHORD_COLOR, outline="",
                )
                canvas.create_text(
                    x_of(start) + 4, margin + 8, anchor="w", fill=CHORD_TEXT,
                    text=chord.label, font=("Segoe UI", 8),
                )

            # bar lines
            beat = analysis.ticks_per_beat
            tick = 0
            while tick < total_ticks:
                canvas.create_line(
                    x_of(tick), margin, x_of(tick), height - margin - legend_height,
                    fill="#2a3441",
                )
                tick += beat * analysis.beats_per_bar

            # one colour per instrument
            for _label, color, notes in series:
                for note in notes:
                    x0, x1 = x_of(note.start), x_of(note.end)
                    y = y_of(note.pitch)
                    canvas.create_rectangle(
                        x0, y - 2, max(x1, x0 + 2), y + 2, fill=color, outline=""
                    )

            # legend
            if legend_height:
                canvas.create_rectangle(
                    0, height - legend_height, width, height, fill="#0d1117", outline=""
                )
                x = 6
                for label, color, notes in series:
                    canvas.create_rectangle(x, height - 13, x + 8, height - 5, fill=color, outline="")
                    text = f"{label} ({len(notes)})"
                    canvas.create_text(
                        x + 12, height - 9, anchor="w", fill="#c8d4e0",
                        text=text, font=("Segoe UI", 8),
                    )
                    x += 24 + 7 * len(text)
                    if x > width - 60:
                        break

    root = tk.Tk()
    try:
        studio = ChordLayerStudio(root)
        if smoke:
            # Exercise layout, instrument parsing, arrangement and preview.
            root.update_idletasks()
            studio.progression_var.set("Am F C G")
            studio._use_progression()
            # A full line-up exercises register sharing, colours and the legend.
            studio.instruments_var.set(
                "808s, piano, strings, flute, harmony, arp, bell, pluck, drums"
            )
            studio._generate_song()
            root.update()
            song_parts = studio.session.analysis.parts
            parts = len(song_parts)
            notes = sum(len(part.notes) for part in song_parts)
            # ...and the optional single-melody path still works too.
            studio.advanced_var.set(1)
            studio._toggle_advanced()
            studio._generate_melody()
            studio._reroll()
            root.update()
            print(f"GUI SMOKE OK: {parts} instrument parts, {notes} notes drawn")
            root.destroy()
            return 0
        root.mainloop()
    except tk.TclError as error:  # e.g. no display available
        print(f"Could not open a window: {error}")
        return 1
    return 0


def session_lines(session: Session) -> list[str]:
    return list(session.log)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    if "--smoke-gui" in argv:
        return run_gui(smoke=True)
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    initial = next((arg for arg in argv if not arg.startswith("-")), None)
    return run_gui(initial)


if __name__ == "__main__":
    raise SystemExit(main())
