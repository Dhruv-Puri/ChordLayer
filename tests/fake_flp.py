"""A fake ``flpianoroll`` module for running the real scripts headless.

Mimics the subset of FL Studio's piano roll scripting API that ChordLayer
uses: ``Note``, ``Marker``, ``score`` (global), ``ScriptDialog`` and
``Utils``. Install with ``install()`` before importing a .pyscript file.
"""

from __future__ import annotations

import sys


class Note:
    """Mimics flpianoroll.Note: attribute bag with FL defaults."""

    def __init__(self, number=60, time=0, length=480, velocity=0.8, color=0):
        self.number = int(number)
        self.time = int(time)
        self.length = int(length)
        self.velocity = float(velocity)
        self.release = 0.5
        self.pan = 0.5
        self.group = 0
        self.color = int(color)
        self.fcut = 0.5
        self.fres = 0.5
        self.pitchofs = 0
        self.slide = False
        self.porta = False
        self.muted = False
        self.selected = False

    def clone(self):
        clone = Note(self.number, self.time, self.length, self.velocity, self.color)
        clone.selected = self.selected
        clone.muted = self.muted
        return clone


class Marker:
    def __init__(self, time=0, name="", mode=0, tsnum=4, tsden=4):
        self.time = int(time)
        self.name = name
        self.mode = mode
        self.tsnum = tsnum
        self.tsden = tsden


class _FakeScore:
    """Holds notes/markers; mimics flpianoroll.score globals."""

    def __init__(self, PPQ=480, tsnum=4, tsden=4):
        self.PPQ = PPQ
        self.tsnum = tsnum
        self.tsden = tsden
        self._notes = []
        self._markers = []

    # -- notes ---------------------------------------------------------------
    @property
    def noteCount(self):
        return len(self._notes)

    def getNote(self, index):
        return self._notes[index]

    def addNote(self, note):
        self._notes.append(note)

    def deleteNote(self, index):
        del self._notes[index]

    def clearNotes(self, all=False):
        if all:
            self._notes = []
        else:
            self._notes = [note for note in self._notes if not note.selected]

    # -- markers ---------------------------------------------------------------
    @property
    def markerCount(self):
        return len(self._markers)

    def getMarker(self, index):
        return self._markers[index]

    def addMarker(self, marker):
        self._markers.append(marker)

    def deleteMarker(self, index):
        del self._markers[index]


class _FakeInput:
    def __init__(self, name, value):
        self.name = name
        self.value = value


class ScriptDialog:
    """Mimics flpianoroll.ScriptDialog: named controls you can set for tests."""

    def __init__(self, title, description):
        self.title = title
        self.description = description
        self._inputs = {}

    def addInput(self, name, value):
        self._inputs[name] = _FakeInput(name, value)

    def addInputKnob(self, name, value, min_value, max_value):
        self._inputs[name] = _FakeInput(name, value)

    def addInputKnobInt(self, name, value, min_value, max_value):
        self._inputs[name] = _FakeInput(name, int(value))

    def addInputCombo(self, name, value_list, value_index):
        self._inputs[name] = _FakeInput(name, int(value_index))

    def addInputText(self, name, value):
        self._inputs[name] = _FakeInput(name, str(value))

    def addInputCheckbox(self, name, value):
        self._inputs[name] = _FakeInput(name, bool(value))

    def getInputValue(self, name):
        if name not in self._inputs:
            raise KeyError(f"Unknown dialog control: {name!r}")
        return self._inputs[name].value

    def set(self, name, value):
        """Test helper: set a control value before calling apply()."""
        self._inputs[name].value = value


class _FakeUtils:
    def __init__(self):
        self.messages = []
        self.logs = []

    def ShowMessage(self, message):
        self.messages.append(str(message))

    def log(self, message):
        self.logs.append(str(message))


score = _FakeScore()
Utils = _FakeUtils()


def reset(PPQ=480, tsnum=4, tsden=4, notes=None):
    """Fresh score for each test."""
    global score, Utils
    score = _FakeScore(PPQ, tsnum, tsden)
    Utils = _FakeUtils()
    for note in notes or []:
        score.addNote(note)


def install():
    """Register this module as ``flpianoroll`` in sys.modules."""
    module = sys.modules[__name__]
    sys.modules["flpianoroll"] = module
    return module
