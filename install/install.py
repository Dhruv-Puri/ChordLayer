"""Install ChordLayer into FL Studio's user 'Piano roll scripts' folder.

Handles both layouts FL Studio uses:

* standard installs - user data in ``Documents/Image-Line/FL Studio/Settings``
* portable installs - user data beside the executable, e.g.
  ``F:/Fl Studio/User_data/Settings/Piano roll scripts``

and checks the detected FL Studio version, because piano roll scripting only
exists from FL Studio 21.1 onwards.

Usage:
    python install/install.py                  # auto-detect, then install
    python install/install.py --detect         # report what was found, change nothing
    python install/install.py --dest "<folder>"  # install into an explicit folder
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
SRC_DIR = os.path.join(ROOT, "src", "chordlayer")
CATEGORY = "ChordLayer"

# Piano roll scripting (.pyscript + flpianoroll) arrived in FL Studio 21.1.
MIN_PIANO_ROLL_VERSION = (21, 1, 0)
SCRIPTS_FOLDER_NAME = "Piano roll scripts"
_INSTALL_DIR_RE = re.compile(r"^(fl ?studio|image-?line)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def candidate_drive_roots(drive_letters: str = "CDEFGHIJKLMNOPQRSTUVWXYZ") -> list[str]:
    """Root paths worth scanning for an FL Studio install."""
    if os.name != "nt":
        return [os.path.expanduser("~"), "/Applications"]
    roots = []
    for letter in drive_letters:
        root = f"{letter}:{os.sep}"
        if os.path.isdir(root):
            roots.append(root)
    return roots


def find_fl_installations(roots: list[str] | None = None) -> list[str]:
    """Directories that look like an FL Studio install (contain FL64.exe/FL.exe)."""
    found = []
    for root in roots if roots is not None else candidate_drive_roots():
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for entry in entries:
            if not _INSTALL_DIR_RE.match(entry):
                continue
            path = os.path.join(root, entry)
            if not os.path.isdir(path):
                continue
            if any(os.path.isfile(os.path.join(path, exe)) for exe in ("FL64.exe", "FL.exe")):
                found.append(path)
    return found


def documents_dir() -> str:
    """The user's real Documents folder (Windows registry aware)."""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                raw, _ = winreg.QueryValueEx(key, "Personal")
            expanded = os.path.expandvars(raw)
            if os.path.isdir(expanded):
                return expanded
        except OSError:
            pass
    home = os.path.expanduser("~")
    for candidate in (
        os.path.join(home, "Documents"),
        os.path.join(home, "OneDrive", "Documents"),
    ):
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(home, "Documents")


def read_exe_version(path: str) -> tuple[int, int, int] | None:
    """File version of a Windows executable, or None when unavailable."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buffer):
            return None
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(
            buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)
        ):
            return None
        info = ctypes.cast(pointer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        return (
            info.dwFileVersionMS >> 16,
            info.dwFileVersionMS & 0xFFFF,
            info.dwFileVersionLS >> 16,
        )
    except Exception:  # ctypes/version.dll unavailable: caller falls back to a feature check
        return None


def version_supports_piano_roll(version: tuple[int, int, int] | None) -> bool:
    """True when the version is known to support piano roll scripting."""
    if version is None:
        return True  # unknown: don't block the user, warn instead
    return version >= MIN_PIANO_ROLL_VERSION


def supports_piano_roll_scripts(install_dir: str) -> bool:
    """Feature check that needs no version parsing.

    FL Studio 21.1+ ships a factory 'Piano roll scripts' folder under
    System/Config; older versions simply do not have it.
    """
    return os.path.isdir(
        os.path.join(install_dir, "System", "Config", SCRIPTS_FOLDER_NAME)
    )


def describe_install(install_dir: str) -> dict:
    """Human-readable facts about one detected FL Studio install."""
    version = read_exe_version(os.path.join(install_dir, "FL64.exe"))
    if version is None:
        version = read_exe_version(os.path.join(install_dir, "FL.exe"))
    portable = os.path.isdir(os.path.join(install_dir, "User_data"))
    return {
        "path": install_dir,
        "version": version,
        "portable": portable,
        "piano_roll": version_supports_piano_roll(version)
        and (supports_piano_roll_scripts(install_dir) or version is not None),
        "factory_scripts": supports_piano_roll_scripts(install_dir),
    }


def destination_candidates(
    installs: list[dict] | None = None, docs: str | None = None
) -> list[dict]:
    """Scripts folders to install into, best candidate first.

    Each entry: {"path", "kind", "reason", "warn"}. Nothing is created here.
    """
    installs = installs if installs is not None else [
        describe_install(path) for path in find_fl_installations()
    ]
    docs = docs if docs is not None else documents_dir()
    candidates: list[dict] = []

    # Portable installs keep user data beside the executable.
    for install in installs:
        if install.get("portable"):
            candidates.append(
                {
                    "path": os.path.join(
                        install["path"], "User_data", "Settings", SCRIPTS_FOLDER_NAME
                    ),
                    "kind": "portable user data",
                    "reason": f"portable install at {install['path']}",
                    "warn": None if install.get("piano_roll") else _old_version_warning(install),
                }
            )

    # Standard installs (and portable ones whose user data was relocated):
    # the shared user data folder in Documents.
    candidates.append(
        {
            "path": os.path.join(docs, "Image-Line", "FL Studio", "Settings", SCRIPTS_FOLDER_NAME),
            "kind": "standard user data",
            "reason": "FL Studio's default user data folder",
            "warn": None,
        }
    )

    # Last resort: the install's own factory scripts folder. Works, but FL
    # documents it as the place for factory scripts, so it is offered last.
    for install in installs:
        if install.get("factory_scripts"):
            candidates.append(
                {
                    "path": os.path.join(
                        install["path"], "System", "Config", SCRIPTS_FOLDER_NAME
                    ),
                    "kind": "factory scripts folder",
                    "reason": f"{install['path']} (already supports piano roll scripts)",
                    "warn": "FL Studio treats this as its own folder; prefer the user data one.",
                }
            )
    return candidates


def _old_version_warning(install: dict) -> str:
    version = install.get("version")
    label = ".".join(str(part) for part in version) if version else "unknown"
    return (
        f"FL Studio {label} does not support piano roll scripting "
        f"(needs {MIN_PIANO_ROLL_VERSION[0]}.{MIN_PIANO_ROLL_VERSION[1]}+) - "
        "ChordLayer's script cannot run there."
    )


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def install(dest: str, force: bool = False) -> list[str]:
    """Copy scripts + engine into ``dest``; returns the paths written."""
    target_dir = os.path.join(dest, CATEGORY)
    if os.path.exists(target_dir) and not force:
        raise SystemExit(
            f"{os.path.join(dest, CATEGORY)} already exists - re-run with --force to overwrite it."
        )
    os.makedirs(target_dir, exist_ok=True)

    copied: list[str] = []
    for name in sorted(os.listdir(SCRIPTS_DIR)):
        if name.endswith(".pyscript"):
            target = os.path.join(target_dir, name)
            shutil.copy2(os.path.join(SCRIPTS_DIR, name), target)
            copied.append(target)

    engine_dir = os.path.join(target_dir, "chordlayer")
    if os.path.isdir(engine_dir):
        shutil.rmtree(engine_dir)
    shutil.copytree(SRC_DIR, engine_dir, ignore=shutil.ignore_patterns("__pycache__"))
    copied.append(engine_dir)
    return copied


def print_report(installs: list[dict], candidates: list[dict]) -> None:
    print("FL Studio installations found:")
    if not installs:
        print("  (none - scanning C:..Z: for folders like 'FL Studio 21'/'FL Studio 26')")
    for install in installs:
        version = install.get("version")
        label = ".".join(str(part) for part in version) if version else "version unknown"
        flags = []
        if install.get("portable"):
            flags.append("portable")
        flags.append(
            "piano roll scripts: yes" if install.get("piano_roll") else "piano roll scripts: NO"
        )
        print(f"  {install['path']}  [{label}, {', '.join(flags)}]")
        if not install.get("piano_roll"):
            print(f"      ! {_old_version_warning(install)}")
    print()
    print("Candidate install folders (best first):")
    for candidate in candidates:
        print(f"  {candidate['path']}")
        print(f"      {candidate['kind']} - {candidate['reason']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", default=None, help="explicit 'Piano roll scripts' folder")
    parser.add_argument("--force", action="store_true", help="overwrite an existing install")
    parser.add_argument("--detect", action="store_true", help="report only, install nothing")
    args = parser.parse_args()

    installs = [describe_install(path) for path in find_fl_installations()]
    candidates = destination_candidates(installs)

    if args.detect:
        print_report(installs, candidates)
        return 0

    if args.dest:
        dest = args.dest
        print(f"Installing into: {dest} (explicit --dest)")
    else:
        supported = [c for c in candidates if (c["warn"] is None or "does not support" not in (c["warn"] or ""))]
        chosen = supported[0] if supported else candidates[0]
        dest = chosen["path"]
        print_report(installs, candidates)
        print()
        print(f"Installing into: {dest}")
        print(f"  ({chosen['kind']} - {chosen['reason']})")
        if chosen["warn"]:
            print(f"  NOTE: {chosen['warn']}")
        if not any(i.get("piano_roll") for i in installs):
            print()
            print("  WARNING: no FL Studio 21.1+ install was detected. Piano roll scripts")
            print("           need 21.1 or newer, so this copy will only work after you")
            print("           install a supported version (see README, 'FL Studio 20').")

    copied = install(dest, args.force)
    print()
    print(f"Installed {len(copied)} item(s) into {os.path.join(dest, CATEGORY)}:")
    for path in copied:
        print(f"  {path}")
    print()
    print("Note: this only copies script files - no Python packages are installed and")
    print("      nothing outside that folder is modified.")
    print()
    print("Next steps:")
    print("  1. Restart FL Studio (or reload the piano roll scripts).")
    print("  2. In a piano roll: Tools > Scripts > ChordLayer > ChordLayer.")
    print("  3. Press OK to generate, tweak knobs, then Ctrl+X the selected melody")
    print("     into another instrument's piano roll.")
    if sys.platform.startswith("win") and not os.path.isdir(dest):
        print("\nNOTE: FL Studio creates this folder on first run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
