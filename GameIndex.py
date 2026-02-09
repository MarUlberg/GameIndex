import os
import re
import sys
import time
import json
import zlib
import shutil
import string
import struct
import datetime
import subprocess
import unicodedata
import configparser
import xml.etree.ElementTree as ET
from io import BytesIO
from PIL import Image
from colorama import Fore, Style, init

init()

# ============================================================
# ========================== SETUP ===========================
# ============================================================

def resolve_scanner():
    """
    Returns (executable, scanner_path) or (None, None) if unavailable
    """
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__)

    exe = os.path.join(base, "game_scanner.exe")
    py  = os.path.join(base, "game_scanner.py")

    if os.path.isfile(exe):
        return exe, None

    if os.path.isfile(py):
        return sys.executable, py

    return None, None

SCANNER_EXEC, SCANNER_SCRIPT = resolve_scanner()
  
def run_scanner_process(env=None, args=None):
    if not SCANNER_EXEC:
        print("(game_scanner not present — skipping scan)")
        return

    cmd = []

    if SCANNER_SCRIPT:
        cmd = [SCANNER_EXEC, SCANNER_SCRIPT]
    else:
        cmd = [SCANNER_EXEC]

    if args:
        cmd.extend(args)

    subprocess.run(cmd, env=env)


CONFIG_FILE = "specialconfig.txt" if os.path.exists("specialconfig.txt") else "config.txt"

def load_setup_minimal(path):
    env = {}
    if not os.path.exists(path):
        return env

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "=" not in line:
                continue

            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()

            if val.startswith(("'", '"')) and val.endswith(("'", '"')):
                env[key] = val[1:-1]

    return env

def load_setup(path):
    if not os.path.exists(path):
        raise RuntimeError(f"Missing {path}")

    env = {}
    safe = {"os": os}

    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    exec(code, safe, env)
    return env

# ============================================================
# ================== CORE PATH VERIFICATION ==================
# ============================================================

def write_config_updates(path, updates):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    def escape(p):
        return p.replace("\\", "\\\\")

    for key, value in updates:
        written = False
        for i, line in enumerate(lines):
            if line.strip().startswith(key + " "):
                lines[i] = f'{key} = "{escape(value)}"\n'
                written = True
                break

        if not written:
            lines.append(f'{key} = "{escape(value)}"\n')

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

# ============================================================
# ================== PROGRAM ROOT DETECTION ==================
# ============================================================

def locate_program_roots():
    try:
        import win32com.client
    except ImportError:
        return {}

    PROGRAMS = {
        "RETROARCH_DIR": ("RetroArch", "self"),
        "DOLPHIN_DIR":   ("Dolphin", "self"),
        "PCSX2_DIR":     ("PCSX2", "self"),
        "LAUNCHBOX_DIR": ("LaunchBox", "launchbox"),
    }

    start_menu_dirs = [
        os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
    ]

    def is_valid_exe(path, name):
        base = os.path.basename(path).lower()
        return (
            base.endswith(".exe")
            and not base.startswith("unins")
            and name.lower() in base
        )

    def resolve_root(exe_path, rule):
        exe_dir = os.path.dirname(exe_path)
        exe_name = os.path.basename(exe_path).lower()

        if rule == "self":
            return exe_dir

        if rule == "launchbox":
            if os.path.basename(exe_dir).lower() == "core":
                return os.path.dirname(exe_dir)
            if exe_name == "launchbox.exe":
                return exe_dir

        return exe_dir

    shell = win32com.client.Dispatch("WScript.Shell")
    found = {}

    for cfg_key, (prog_name, rule) in PROGRAMS.items():
        found[cfg_key] = None

        for base in start_menu_dirs:
            if not os.path.isdir(base):
                continue

            for root, _, files in os.walk(base):
                for fname in files:
                    if not fname.lower().endswith(".lnk"):
                        continue
                    if prog_name.lower() not in fname.lower():
                        continue

                    try:
                        shortcut = shell.CreateShortCut(os.path.join(root, fname))
                        target = shortcut.Targetpath
                        if not target or not is_valid_exe(target, prog_name):
                            continue

                        found[cfg_key] = resolve_root(target, rule)
                        break
                    except Exception:
                        continue

                if found[cfg_key]:
                    break
            if found[cfg_key]:
                break

    return found
        
# ============================================================
# ======================= PATH PRECHECK ======================
# ============================================================

MIN_SETUP = load_setup_minimal(CONFIG_FILE)

# Emulator presence rules
REQUIRED_EMULATORS = {
    "RETROARCH_DIR":  ["retroarch.exe"],
    "DOLPHIN_DIR":    ["dolphin.exe"],
    "PCSX2_DIR":      ["pcsx2.exe"],
    "LAUNCHBOX_DIR":  ["launchbox.exe", os.path.join("core", "launchbox.exe")],
}

def has_required_exe(root, candidates):
    if not root or not os.path.isdir(root):
        return False

    # PCSX2: name + location are not stable
    if candidates == ["pcsx2.exe"]:
        # Check root
        for name in os.listdir(root):
            low = name.lower()
            if low.startswith("pcsx2") and low.endswith(".exe"):
                return True

        # Check one level deep (portable builds)
        for sub in os.listdir(root):
            subdir = os.path.join(root, sub)
            if not os.path.isdir(subdir):
                continue
            for name in os.listdir(subdir):
                low = name.lower()
                if low.startswith("pcsx2") and low.endswith(".exe"):
                    return True

        return False

    # Exact-match rules for other emulators
    for rel in candidates:
        if os.path.isfile(os.path.join(root, rel)):
            return True

    return False


def status_ok():
    return f"[ {Fore.LIGHTGREEN_EX}OK{Style.RESET_ALL} ]"

def status_xx():
    return f"[ {Fore.LIGHTRED_EX}XX{Style.RESET_ALL} ]"

missing = []

for key, exes in REQUIRED_EMULATORS.items():
    path = MIN_SETUP.get(key)
    if not has_required_exe(path, exes):
        missing.append(key)

if missing:
    print("\nVerifying emulator paths:\n")

    found = locate_program_roots()
    updates = []

    for key in missing:
        path = found.get(key)
        exes = REQUIRED_EMULATORS[key]

        if has_required_exe(path, exes):
            print(f"{status_ok()} {key}: {path}")
            updates.append((key, path))
        else:
            print(f"{status_xx()} {key}: NOT FOUND")

    if updates:
        write_config_updates(CONFIG_FILE, updates)

def use_standalone_emulator(system):
    """
    Decide whether a system should use its standalone emulator
    or fall back to RetroArch behavior.
    """
    if system == "PS2":
        return has_required_exe(PCSX2_DIR, ["pcsx2.exe"])

    if system in ("GC", "WII"):
        return has_required_exe(DOLPHIN_DIR, ["dolphin.exe"])

    return False


# ------------------------------------------------------------
# Now load the full config safely
# ------------------------------------------------------------
SETUP = load_setup(CONFIG_FILE)

CODEWORDS = [
    "(patched)", "[patched]", "(hack)", "[hack]",
]

# ============================================================
# ========================== PATHS ===========================
# ============================================================

LOCAL_DB        = "local_games.txt"
HISTORY         = "history.txt"
PLAYTIME_EXPORT = "playtime_export.txt"
SCANNER_EXEC, SCANNER_SCRIPT = resolve_scanner()

PRINT_ALL = bool(SETUP.get("PRINT_ALL", False))

# --- ROM directory ---
GAMES_DIR = SETUP["GAMES_DIR"]

# --- RetroArch ---
RETROARCH_DIR          = SETUP["RETROARCH_DIR"]
RETROARCH_CFG_DIR      = SETUP["RETROARCH_CFG_DIR"]
RETROARCH_PLAYLIST_DIR = SETUP["RETROARCH_PLAYLIST_DIR"]
RETROARCH_LOG_DIR      = SETUP["RETROARCH_LOG_DIR"]

# --- RetroArch images (optional) ---
RETROARCH_IMG_DIR       = SETUP.get("RETROARCH_IMG_DIR")
RETROARCH_SCREEN_DIR    = SETUP.get("RETROARCH_SCREEN_DIR")
RETROARCH_COVER_SUBDIR  = SETUP.get("RETROARCH_COVER_SUBDIR", "Named_Boxarts")
RETROARCH_SCREEN_SUBDIR = SETUP.get("RETROARCH_SCREEN_SUBDIR", "Named_Snaps")

# --- Dolphin (optional) ---
DOLPHIN_DIR      = SETUP.get("DOLPHIN_DIR")
DOLPHIN_PLAYTIME = SETUP.get("DOLPHIN_PLAYTIME")
DOLPHIN_IMG_DIR  = SETUP.get("DOLPHIN_IMG_DIR")
DOLPHIN_SCREEN_DIR  = SETUP.get("DOLPHIN_SCREEN_DIR")

# --- PCSX2 (optional) ---
PCSX2_DIR        = SETUP.get("PCSX2_DIR")
PCSX2_PLAYTIME   = SETUP.get("PCSX2_PLAYTIME")
PCSX2_IMG_DIR    = SETUP.get("PCSX2_IMG_DIR")
PCSX2_SCREEN_DIR    = SETUP.get("PCSX2_SCREEN_DIR")

# --- LaunchBox (optional) ---
LAUNCHBOX_DATA_DIR      = SETUP.get("LAUNCHBOX_DATA_DIR")
LAUNCHBOX_PLATFORMS     = SETUP.get("LAUNCHBOX_PLATFORMS")
LAUNCHBOX_IMG_DIR       = SETUP.get("LAUNCHBOX_IMG_DIR")
LAUNCHBOX_COVER_SUBDIR  = SETUP.get("LAUNCHBOX_COVER_SUBDIR", "Box - Front")
LAUNCHBOX_SCREEN_SUBDIR = SETUP.get("LAUNCHBOX_SCREEN_SUBDIR", "Screenshot - Gameplay")

# --- Misc ---
ADITIONAL_IMG_DIR = SETUP.get("ADITIONAL_IMG_DIR")


# ============================================================
# ========================= SYSTEMS ==========================
# ============================================================

SYSTEMS = {
    "ARCADE": {
        "platforms": [
            "FBNeo - Arcade Games",
        ],
        "cores": [
            "Daphne",
            "DICE",
            "FB Alpha",
            "FB Alpha 2012",
            "FB Neo",
            "HBMAME",
            "MAME (Current)",
            "MAME 2000",
            "MAME 2003",
            "MAME 2003 Midway",
            "MAME 2003-Plus",
            "MAME 2009",
            "MAME 2010",
            "MAME 2015",
            "MAME 2016",
            "UME 2015",
        ],
    },

    "GW": {
        "platforms": [
            "Handheld Electronic Game",
        ],
        "cores": [
            "GW",
            "MAME (Current)",
        ],
    },

    "GB": {
        "platforms": [
            "Nintendo - Game Boy",
        ],
        "cores": [
            "Emux GB",
            "fixGB",
            "Gambatte",
            "Gearboy",
            "SameBoy",
            "TGB Dual",
            "mGBA",
            "higan Accuracy",
            "Mesen-S",
            "nSide Balanced",
        ],
    },

    "GBC": {
        "platforms": [
            "Nintendo - Game Boy Color",
        ],
        "cores": [
            "Emux GB",
            "fixGB",
            "Gambatte",
            "Gearboy",
            "SameBoy",
            "TGB Dual",
            "mGBA",
            "higan Accuracy",
            "Mesen-S",
            "nSide Balanced",
        ],
    },

    "GBA": {
        "platforms": [
            "Nintendo - Game Boy Advance",
        ],
        "cores": [
            "Beetle GBA",
            "gpSP",
            "Meteor",
            "mGBA",
            "TempGBA",
            "VBA-M",
            "VBA Next",
        ],
    },

    "NDS": {
        "platforms": [
            "Nintendo - Nintendo DS",
        ],
        "cores": [
            "DeSmuME",
            "DeSmuME 2015",
            "melonDS 2021",
            "melonDS DS",
        ],
    },

    "3DS": {
        "platforms": [
            "Nintendo - Nintendo 3DS",
        ],
        "cores": [
            "Citra",
            "Citra 2018",
            "Citra Canary",
        ],
    },

    "NES": {
        "platforms": [
            "Nintendo - Nintendo Entertainment System",
        ],
        "cores": [
            "bnes",
            "Emux NES",
            "FCEUmm",
            "fixNES",
            "Mesen",
            "Nestopia",
            "QuickNES",
        ],
    },

    "SNES": {
        "platforms": [
            "Nintendo - Super Nintendo Entertainment System",
        ],
        "cores": [
            "Beetle bsnes",
            "Beetle Supafaust",
            "bsnes",
            "bsnes 2014 Accuracy",
            "bsnes 2014 Balanced",
            "bsnes 2014 Performance",
            "bsnes C++98 (v085)",
            "bsnes-hd beta",
            "bsnes-jg",
            "bsnes-mercury Accuracy",
            "bsnes-mercury Balanced",
            "bsnes-mercury Performance",
            "Snes9x",
            "Snes9x 2002",
            "Snes9x 2005",
            "Snes9x 2005 Plus",
            "Snes9x 2010",
            "higan Accuracy",
            "Mesen-S",
            "nSide Balanced",
        ],
    },

    "N64": {
        "platforms": [
            "Nintendo - Nintendo 64",
        ],
        "cores": [
            "Mupen64Plus-Next",
            "Mupen64Plus-Next GLES2",
            "Mupen64Plus-Next GLES3",
            "ParaLLEl N64",
        ],
    },

    "VB": {
        "platforms": [
            "Nintendo - Nintendo Virtual Boy",
        ],
        "cores": [
            "Beetle VB",
        ],
    },

    "GC": {
        "platforms": [
            "Nintendo - GameCube",
        ],
        "cores": [
            "Dolphin",
            "Ishiiruka",
        ],
    },

    "WII": {
        "platforms": [
            "Nintendo - Wii",
        ],
        "cores": [
            "Dolphin",
            "Ishiiruka",
        ],
    },

    "MasterSys": {
        "platforms": [
            "Sega - Master System - Mark III",
        ],
        "cores": [
            "Emux SMS",
            "SMS Plus GX",
            "Genesis Plus GX",
            "Gearsystem",
            "PicoDrive",
        ],
    },

    "GameGear": {
        "platforms": [
            "Sega - Game Gear",
        ],
        "cores": [
            "SMS Plus GX",
            "Genesis Plus GX",
            "Gearsystem",
            "PicoDrive",
        ],
    },

    "Genesis": {
        "platforms": [
            "Sega - Mega Drive - Genesis",
        ],
        "cores": [
            "BlastEm",
            "Genesis Plus GX",
            "ClownMDEmu",
            "PicoDrive",
        ],
    },

    "SegaCD": {
        "platforms": [
            "Sega - Mega-CD - Sega CD",
        ],
        "cores": [
            "Genesis Plus GX",
            "ClownMDEmu",
            "PicoDrive",
        ],
    },

    "32X": {
        "platforms": [
            "Sega - 32X",
        ],
        "cores": [
            "PicoDrive",
        ],
    },

    "Saturn": {
        "platforms": [
            "Sega - Saturn",
        ],
        "cores": [
            "Beetle Saturn",
            "YabaSanshiro",
            "Yabause",
            "Kronos",
        ],
    },

    "Dreamcast": {
        "platforms": [
            "Sega - Dreamcast",
        ],
        "cores": [
            "Flycast",
            "Flycast GLES2",
        ],
    },

    "PSX": {
        "platforms": [
            "Sony - PlayStation",
        ],
        "cores": [
            "Beetle PSX",
            "Beetle PSX HW",
            "DuckStation",
            "PCSX ReARMed",
            "Rustation",
            "SwanStation",
        ],
    },

    "PS2": {
        "platforms": [
            "Sony - PlayStation 2",
        ],
        "cores": [
            "LRPS2",
            "Play!",
        ],
    },

    "PSP": {
        "platforms": [
            "Sony - PlayStation Portable",
        ],
        "cores": [
            "PPSSPP",
        ],
    },
}

PLATFORM_TO_SYSTEM = {plat: sys for sys, d in SYSTEMS.items() for plat in d["platforms"]}
SYSTEM_TO_CORES = {sys: d["cores"] for sys, d in SYSTEMS.items()}
PLATFORMS_ORDERED = [plat for d in SYSTEMS.values() for plat in d["platforms"]]

ARCADE_PLATFORMS = {
    "FBNeo - Arcade Games",
    "Handheld Electronic Game",
    # future:
    # "MAME",
    # "FinalBurn Alpha",
}

# ============================================================
# ====================== SHARED HELPERS ======================
# ============================================================

TAG_RE = re.compile(r"[\[\(].*?[\]\)]")

def count_tags(name):
    """Count bracketed / parenthesized tags."""
    return len(TAG_RE.findall(name))

def pick_best_rom_for_gameid(stems):
    """
    Given multiple ROM stems for the same GameID,
    return the best one based on priority rules.
    """
    return sorted(
        stems,
        key=lambda s: (count_tags(s), len(s))
    )[0]

VALID_GAMEID_PATTERNS = [
    # Dolphin: no lowercase letters, max 7 chars
    r"[A-Z0-9]{3,7}",

    # PS2: strict disc IDs
    r"(?:SLES|SLPM|SLUS|SLPS|SCED|SCES|SCUS|SLKA|SCPS|SLED|SCKA|SCAJ|PCPX|PAPX|PBPX|SCCS|TCES|SCPN|TLES|PSXC|SCPM)[_\-\.]?\d{3}[_\-\.]?\d{2}",
]

def is_valid_gameid(gameid):
    for pat in VALID_GAMEID_PATTERNS:
        if re.fullmatch(pat, gameid, re.I):
            return True
    return False

def parse_seconds(value):
    # Parse playtime into seconds.
    if not value:
        return 0

    v = value.strip().lower()

    if v.isdigit():
        return int(v)

    if v.endswith("s") and v[:-1].isdigit():
        return int(v[:-1])

    h = m = s = 0

    mh = re.search(r'([\d\.]+)\s*h', v)
    if mh:
        h = int(mh.group(1).replace(".", "") or 0)

    mm = re.search(r'(\d+)\s*m', v)
    if mm:
        m = int(mm.group(1))

    ms = re.search(r'(\d+)\s*s', v)
    if ms:
        s = int(ms.group(1))

    return h * 3600 + m * 60 + s
    
def make_launchbox_image_name(platform, rom_stem, ext):
    """
    Return LaunchBox-style image filename:
    <Title>-01<ext>

    If no LaunchBox Title exists for this ROM,
    return None (never fall back to ROM filename).
    """
    def lb_normalize(name):
        name = name.replace(":", "_").replace("'", "_")
        name = re.sub(r'[<>"/\\|?*]', '', name)
        return name.strip()

    xmlfile = LAUNCHBOX_PLATFORMS.get(platform)
    if not xmlfile:
        return None

    path = os.path.join(LAUNCHBOX_DATA_DIR, xmlfile)
    if not os.path.exists(path):
        return None

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except:
        return None

    for g in root.findall("Game"):
        app = g.findtext("ApplicationPath", "")
        if not app:
            continue

        app_stem = os.path.splitext(os.path.basename(app))[0]
        if app_stem != rom_stem:
            continue

        title = g.findtext("Title", "").strip()
        if not title:
            return None

        return f"{lb_normalize(title)}-01{ext}"

    return None


# ============================================================
# ================== RETROARCH THUMBNAILS ====================
# ============================================================

# - Applies ONLY to thumbnail filenames
# - ROMs, saves, logs, playlists, XML keep original characters
# - Matching must treat sanitized and unsanitized names as equal

RETROARCH_REJECTED_CHARS = '&/\\:*?"<>|'

def sanitize_rom_filename(name):
    """
    RetroArch thumbnails ONLY.
    Always replace '&' with '_'.
    """
    base, ext = os.path.splitext(name)
    base = base.replace("&", "_")

    for ch in RETROARCH_REJECTED_CHARS:
        base = base.replace(ch, "_")

    return base + ext

def normalize_filename_for_match(name, *, strip_ext=True):
    if strip_ext:
        name, _ = os.path.splitext(name)

    # Normalize Unicode (decompose + remove diacritics) then apply existing rules.
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))

    # Strip bracketed tags
    name = re.sub(r"[\[\(].*?[\]\)]", "", name)

    # RetroArch safety
    name = name.replace("&", "_")

    # Keep only ASCII alphanumerics
    name = re.sub(r"[^a-zA-Z0-9]", "", name)

    return name.lower()


def filenames_equivalent(a, b, *, strip_ext=True):
    if strip_ext:
        a0, _ = os.path.splitext(a)
        b0, _ = os.path.splitext(b)
    else:
        a0, b0 = a, b

    # 1) exact match first
    if a0 == b0:
        return True

    # 2) normalized fallback
    return normalize_filename_for_match(a0, strip_ext=False) == \
           normalize_filename_for_match(b0, strip_ext=False)


def expand_multidisc_renames(rom_dir, old_file, new_file):
    """
    Expand Disc 1 rename across sibling discs.

    - Disc numbers preserved
    - Only base title changes
    - Cue/bin safe
    """
    jobs = []

    def sig(name):
        n = name.lower()
        m = re.search(r"\b(disc|disk|cd)\s*(\d+)\b", n)
        disc = int(m.group(2)) if m else None
        base = re.sub(r"\b(disc|disk|cd)\s*\d+\b", "", n)
        base = re.sub(r"\s+", " ", base).strip()
        return base, disc

    old_base, old_disc = sig(old_file)
    _, new_disc = sig(new_file)

    if not old_disc or not new_disc:
        return [(rom_dir, old_file, new_file)]

    def replace_disc_number(template, disc_num):
        def repl(m):
            return f"{m.group(1)}{disc_num}"

        return re.sub(
            r"\b((?:disc|disk|cd)\s*)\d+\b",
            repl,
            template,
            flags=re.I
        )

    for fname in os.listdir(rom_dir):
        fbase, fdisc = sig(fname)

        if fdisc is None or fbase != old_base:
            continue

        new_name = replace_disc_number(new_file, fdisc)
        jobs.append((rom_dir, fname, new_name))

    return jobs

# ============================================================
# ========================= DATABASE =========================
# ============================================================

# ---------- History ----------

def show_history():
    if not os.path.exists(HISTORY):
        print("(no history)")
        return
    with open(HISTORY, "r", encoding="utf-8") as f:
        print(f.read())

def next_history_index():
    if not os.path.exists(HISTORY):
        return 1
    nums = []
    with open(HISTORY, "r", encoding="utf-8") as f:
        for line in f:
            if "." in line:
                try:
                    nums.append(int(line.split(".", 1)[0]))
                except:
                    pass
    return max(nums) + 1 if nums else 1

def write_history(path, old_line, new_line, index):
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{index}. {old_line} → {new_line}\n")


# ---------- Local games ----------

def load_local():
    rows = []
    if not os.path.exists(LOCAL_DB):
        return rows
    with open(LOCAL_DB, "r", encoding="utf-8") as f:
        for line in f:
            if "|" in line and not line.startswith("Platform"):
                rows.append(line.rstrip("\n"))
    return rows

def save_local(rows):
    with open(LOCAL_DB, "w", encoding="utf-8") as f:
        f.write("Platform | Title | GameID | File\n")
        for r in rows:
            f.write(r + "\n")


# ---------- Playtime export ----------

def load_playtime_export():
    if not os.path.exists(PLAYTIME_EXPORT):
        return []
    rows = []
    with open(PLAYTIME_EXPORT, "r", encoding="utf-8") as f:
        for line in f:
            if "|" in line and not line.startswith("Platform"):
                rows.append(line.rstrip("\n"))
    return rows

def save_playtime_export(rows):
    with open(PLAYTIME_EXPORT, "w", encoding="utf-8") as f:
        f.write("Platform | Title | GameID | Playtime | Last Played | File\n")
        for r in rows:
            f.write(r + "\n")
            
def replace_lines_in_file(path, replacements):
    if not replacements:
        return

    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    # Detect newline style
    newline = "\r\n" if "\r\n" in text else "\n"
    has_trailing_newline = text.endswith(("\n", "\r\n"))

    lines = text.splitlines()
    out = []

    for line in lines:
        if line in replacements:
            out.append(replacements[line])
        else:
            out.append(line)

    new_text = newline.join(out)
    if has_trailing_newline:
        new_text += newline

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)


# ============================================================
# ===================== PLAYTIME LOADERS =====================
# ============================================================

def format_playtime(seconds):
    """
    PLAYTIME_SEC = True  -> "123456s"
    PLAYTIME_SEC = False -> "1234h 56m 07s"
    """
    try:
        seconds = int(seconds)
    except:
        seconds = 0

    show_seconds = SETUP.get("PLAYTIME_SEC", True)

    if show_seconds:
        return f"{seconds}s"

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    # Thousands-separated hours using dot
    h_str = f"{h:,}".replace(",", "")

    return f"{h_str}h {m:02}m {s:02}s"

# ---------- RetroArch ----------

def load_retroarch_playtime():
    out = {}

    logs_root = RETROARCH_LOG_DIR
    if not os.path.isdir(logs_root):
        return out

    # ----------------------------------
    # Build allowed roots explicitly
    # ----------------------------------
    allowed_roots = []

    # root logs dir
    allowed_roots.append(logs_root)

    for platform, system in PLATFORM_TO_SYSTEM.items():
        # platform logs
        plat_dir = os.path.join(logs_root, platform)
        if os.path.isdir(plat_dir):
            allowed_roots.append(plat_dir)

        # core logs
        for core in SYSTEM_TO_CORES.get(system, []):
            core_dir = os.path.join(logs_root, core)
            if os.path.isdir(core_dir):
                allowed_roots.append(core_dir)

    # ----------------------------------
    # Scan allowed roots only
    # ----------------------------------
    for root in allowed_roots:
        for dirpath, _, files in os.walk(root):
            for fname in files:
                if not fname.lower().endswith(".lrtl"):
                    continue

                path = os.path.join(dirpath, fname)
                rom = os.path.splitext(fname)[0]

                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except:
                    continue

                runtime = data.get("runtime", "")
                last = data.get("last_played", "")

                # ---------- runtime ----------
                seconds = 0
                if runtime:
                    parts = runtime.split(":")
                    if len(parts) == 3:
                        try:
                            h, m, s = map(int, parts)
                            seconds = h * 3600 + m * 60 + s
                        except:
                            seconds = 0

                # ---------- last_played ----------
                if isinstance(last, str):
                    last = last.strip()
                else:
                    last = ""

                out[rom] = {
                    "seconds": seconds,
                    "last_played": last
                }

    return out


# ---------- Dolphin ----------

def load_dolphin_playtime():
    data = {}

    if not os.path.exists(DOLPHIN_PLAYTIME):
        return data

    in_block = False
    with open(DOLPHIN_PLAYTIME, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if line == "[TimePlayed]":
                in_block = True
                continue
            if line.startswith("["):
                in_block = False

            if not in_block or "=" not in line:
                continue

            gameid, val = line.split("=", 1)
            try:
                ms = int(val.strip(), 16)
                data[gameid.strip()] = ms // 1000
            except:
                pass

    return data


# ---------- PCSX2 ----------

def load_pcsx2_playtime():
    data = {}

    if not os.path.exists(PCSX2_PLAYTIME):
        return data

    with open(PCSX2_PLAYTIME, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue

            gameid = line[:33].strip()
            secs   = line[33:54].strip()
            last   = line[54:74].strip()

            try:
                secs = int(secs)
            except:
                secs = 0

            try:
                last = int(last)
                if last:
                    last = datetime.datetime.fromtimestamp(last).isoformat(" ")
                else:
                    last = ""
            except:
                last = ""

            data[gameid] = (secs, last)

    return data

# ---------- Minecraft ----------
  
def load_minecraft_playtime():
    root = SETUP.get("MINECRF_DIR")
    if not root:
        return None

    saves = os.path.join(root, "saves")
    if not os.path.isdir(saves):
        return None

    total_ticks = 0
    last_played_ts = 0

    for world in os.listdir(saves):
        stats_dir = os.path.join(saves, world, "stats")
        if not os.path.isdir(stats_dir):
            continue

        for fname in os.listdir(stats_dir):
            if not fname.lower().endswith(".json"):
                continue

            path = os.path.join(stats_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except:
                continue

            stats = data.get("stats", {}).get("minecraft:custom", {})
            ticks = stats.get("minecraft:play_time", 0)

            try:
                total_ticks += int(ticks)
            except:
                pass

            # Use file modification time for "last played"
            try:
                mtime = os.path.getmtime(path)
                if mtime > last_played_ts:
                    last_played_ts = mtime
            except:
                pass

    if total_ticks == 0:
        return None

    seconds = total_ticks // 20
    last_played = datetime.datetime.fromtimestamp(
        last_played_ts
    ).strftime("%Y-%m-%d %H:%M:%S")

    return seconds, last_played

# ---------- World of Warcraft ----------

def load_wow_playtime(root):
    if not root or not os.path.isdir(root):
        return None

    totals = []

    # ---------- SavedInstances.lua ----------
    si_path = os.path.join(root, "SavedInstances.lua")
    if os.path.exists(si_path):
        total = 0
        pat = re.compile(r'\["PlayedTotal"\]\s*=\s*(\d+)')
        try:
            with open(si_path, "r", encoding="utf-8") as f:
                for line in f:
                    m = pat.search(line)
                    if m:
                        total += int(m.group(1))
        except:
            total = 0

        if total > 0:
            totals.append((total, os.path.getmtime(si_path)))

    # ---------- Playtime.lua ----------
    pt_path = os.path.join(root, "Playtime.lua")
    if os.path.exists(pt_path):
        total = 0
        pat = re.compile(r'\]\s*=\s*(\d+)')
        try:
            with open(pt_path, "r", encoding="utf-8") as f:
                for line in f:
                    m = pat.search(line)
                    if m:
                        total += int(m.group(1))
        except:
            total = 0

        if total > 0:
            totals.append((total, os.path.getmtime(pt_path)))

    # ---------- Broker_PlayedTime.lua ----------
    bpt_path = os.path.join(root, "Broker_PlayedTime.lua")
    if os.path.exists(bpt_path):
        total = 0
        pat = re.compile(r'\["timePlayed"\]\s*=\s*(\d+)')
        try:
            with open(bpt_path, "r", encoding="utf-8") as f:
                for line in f:
                    m = pat.search(line)
                    if m:
                        total += int(m.group(1))
        except:
            total = 0

        if total > 0:
            totals.append((total, os.path.getmtime(bpt_path)))

    if not totals:
        return None

    # Highest reported playtime wins
    seconds, mtime = max(totals, key=lambda x: x[0])

    last_played = datetime.datetime.fromtimestamp(
        mtime
    ).strftime("%Y-%m-%d %H:%M:%S")

    return seconds, last_played

# ---------- LaunchBox ----------

def normalize_launchbox_time(s):
    if not s:
        return ""
    if "+" in s:
        s = s.split("+", 1)[0]
    if "." in s:
        s = s.split(".", 1)[0]
    if "T" in s:
        s = s.replace("T", " ", 1)
    return s.strip()

def load_launchbox_lastplayed():
    data = {}

    for xml in LAUNCHBOX_PLATFORMS.values():
        path = os.path.join(LAUNCHBOX_DATA_DIR, xml)
        if not os.path.exists(path):
            continue

        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except:
            continue

        for g in root.findall("Game"):
            app = g.findtext("ApplicationPath", "").strip()
            last = g.findtext("LastPlayedDate", "").strip()

            if not app or not last:
                continue

            # Use filename stem as key (no Version)
            fname = os.path.basename(app)
            stem, _ = os.path.splitext(fname)
            if not stem:
                continue

            data[stem] = normalize_launchbox_time(last)

    return data


# ============================================================
# ===================== PLAYTIME WRITERS =====================
# ============================================================

def indent_xml(elem, level=0):
    """
    In-place pretty printer for ElementTree.
    Ensures each element (including </Game>) is on its own line.
    """
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for e in elem:
            indent_xml(e, level + 1)
        if not e.tail or not e.tail.strip():
            e.tail = i
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i

# ---------- RetroArch ----------

def write_retroarch_time(filename, seconds, lastplayed):
    base = os.path.splitext(filename)[0]
    path = os.path.join(RETROARCH_LOG_DIR, base + ".lrtl")

    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        return

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    data["runtime"] = f"{h}:{m:02}:{s:02}"

    if lastplayed:
        data["last_played"] = lastplayed

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------- LaunchBox ----------

def write_launchbox_windows_time(title_candidates, seconds, lastplayed):
    """
    Write playtime / last-played to LaunchBox Windows.xml
    using Title-based matching.
    """
    xmlfile = LAUNCHBOX_PLATFORMS.get("Windows")
    if not xmlfile:
        return

    path = os.path.join(LAUNCHBOX_DATA_DIR, xmlfile)
    if not os.path.exists(path):
        return

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except:
        return

    # --- normalize lastplayed ---
    norm_lastplayed = ""
    if lastplayed:
        s = str(lastplayed).strip()
        if s.isdigit():
            try:
                norm_lastplayed = datetime.datetime.fromtimestamp(
                    int(s)
                ).strftime("%Y-%m-%dT%H:%M:%S")
            except:
                norm_lastplayed = ""
        else:
            if " " in s:
                s = s.replace(" ", "T", 1)
            norm_lastplayed = s

    titles_lower = [t.lower() for t in title_candidates]
    changed = False

    for g in root.findall("Game"):
        title = g.findtext("Title", "")
        if not title:
            continue

        if title.lower() not in titles_lower:
            continue

        if seconds:
            pt = g.find("PlayTime")
            if pt is None:
                pt = ET.SubElement(g, "PlayTime")
            pt.text = str(seconds)
            changed = True

        if norm_lastplayed:
            lp = g.find("LastPlayedDate")
            if lp is None:
                lp = ET.SubElement(g, "LastPlayedDate")
            lp.text = norm_lastplayed
            changed = True

        break  # only ever update one Windows entry

    if changed:
        indent_xml(root)
        tree.write(path, encoding="utf-8", xml_declaration=True)

def write_launchbox_time(platform, _gameid, filename, seconds, lastplayed):
    """
    Write playtime / last-played data to LaunchBox XML.
    Matching is performed by ROM filename (ApplicationPath),
    """
    xmlfile = LAUNCHBOX_PLATFORMS.get(platform)
    if not xmlfile:
        return

    path = os.path.join(LAUNCHBOX_DATA_DIR, xmlfile)
    if not os.path.exists(path):
        return

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except:
        return

    romname = os.path.basename(filename).lower()
    changed = False

    # --- normalize lastplayed ---
    norm_lastplayed = ""
    if lastplayed:
        s = str(lastplayed).strip()
        if s.isdigit():
            try:
                norm_lastplayed = datetime.datetime.fromtimestamp(
                    int(s)
                ).strftime("%Y-%m-%dT%H:%M:%S")
            except:
                norm_lastplayed = ""
        else:
            if " " in s:
                s = s.replace(" ", "T", 1)
            norm_lastplayed = s

    for g in root.findall("Game"):
        app = g.findtext("ApplicationPath", "")
        if not app:
            continue

        app_base = os.path.basename(app).lower()
        if app_base != romname:
            continue

        if seconds:
            pt = g.find("PlayTime")
            if pt is None:
                pt = ET.SubElement(g, "PlayTime")
            pt.text = str(seconds)
            changed = True

        if norm_lastplayed:
            lp = g.find("LastPlayedDate")
            if lp is None:
                lp = ET.SubElement(g, "LastPlayedDate")
            lp.text = norm_lastplayed
            changed = True

    if changed:
        indent_xml(root)
        tree.write(path, encoding="utf-8", xml_declaration=True)

# ---------- Dolphin ----------

def write_dolphin_time(gameid, seconds):
    if not os.path.exists(DOLPHIN_PLAYTIME):
        return

    try:
        ms = int(seconds) * 1000
    except:
        return

    hexval = "0x" + format(ms, "016x")

    lines = []
    found = False
    in_block = False

    with open(DOLPHIN_PLAYTIME, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.rstrip("\n")

            if raw.strip() == "[TimePlayed]":
                in_block = True
                lines.append(raw)
                continue

            if in_block and raw.startswith("["):
                if not found:
                    lines.append(f"{gameid} = {hexval}")
                    found = True
                in_block = False

            if in_block and raw.strip().startswith(gameid + " "):
                lines.append(f"{gameid} = {hexval}")
                found = True
            else:
                lines.append(raw)

    if not found:
        out = []
        for l in lines:
            out.append(l)
            if l.strip() == "[TimePlayed]":
                out.append(f"{gameid} = {hexval}")
        lines = out

    with open(DOLPHIN_PLAYTIME, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

# ---------- PCSX2 ----------

def format_pcsx2_line(gameid, seconds, lastplayed):
    gid  = str(gameid).ljust(33)[:33]
    secs = str(int(seconds)).ljust(21)[:21]

    if lastplayed:
        try:
            ts = int(datetime.datetime.fromisoformat(lastplayed).timestamp())
        except:
            ts = 0
    else:
        ts = 0

    last = str(ts).ljust(20)[:20]
    return gid + secs + last

def write_pcsx2_time(gameid, seconds, lastplayed):
    if not os.path.exists(PCSX2_PLAYTIME):
        return

    with open(PCSX2_PLAYTIME, "rb") as f:
        raw = f.read()

    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    new_line = format_pcsx2_line(gameid, seconds, lastplayed).encode("ascii")

    lines = raw.split(newline)

    out = []
    found = False

    for l in lines:
        if not l.strip():
            continue

        gid = l[:33].decode("ascii", errors="ignore").strip()

        if gid == gameid:
            out.append(new_line)
            found = True
        else:
            out.append(l)

    if not found:
        out.append(new_line)

    with open(PCSX2_PLAYTIME, "wb") as f:
        f.write(newline.join(out) + newline)

# ============================================================
# ================== SCREENSHOT SYNC ENGINE ==================
# ============================================================

_COMPRESSED_IMAGE_CACHE = {}
def compress_and_copy_image(src, dst):

    TARGET_WIDTH = None
    TARGET_HEIGHT = 1080
    BITS_PER_CHANNEL = 6

    PNGQUANT_PATH = "pngquant.exe"
    PNGQUANT_QUALITY = "60-90"

    def compute_target_size(w, h):
        if TARGET_WIDTH and TARGET_HEIGHT:
            return TARGET_WIDTH, TARGET_HEIGHT
        if TARGET_WIDTH:
            scale = TARGET_WIDTH / w
            return TARGET_WIDTH, int(h * scale)
        if TARGET_HEIGHT:
            scale = TARGET_HEIGHT / h
            return int(w * scale), TARGET_HEIGHT
        return w, h

    def reduce_bit_depth(img, bits):
        if bits >= 8:
            return img

        levels = 1 << bits
        step = 256 // levels

        def q(v):
            return min(255, (v // step) * step)

        if img.mode == "RGBA":
            r, g, b, a = img.split()
            return Image.merge("RGBA", (r.point(q), g.point(q), b.point(q), a))

        if img.mode == "RGB":
            r, g, b = img.split()
            return Image.merge("RGB", (r.point(q), g.point(q), b.point(q)))

        return img

    cached = _COMPRESSED_IMAGE_CACHE.get(src)
    if cached is not None:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(cached)
        return True

    with Image.open(src) as img:
        img = img.convert("RGBA")
        tw, th = compute_target_size(*img.size)
        img = img.resize((tw, th), Image.BICUBIC)

        buf = BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()

    if shutil.which(PNGQUANT_PATH):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)

        subprocess.run(
            [
                PNGQUANT_PATH,
                "--force",
                "--quality", PNGQUANT_QUALITY,
                "--speed", "1",
                "--output", dst,
                "--", dst,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        with open(dst, "rb") as f:
            final = f.read()
    else:
        img = Image.open(BytesIO(data))
        img = reduce_bit_depth(img, BITS_PER_CHANNEL)

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True, compress_level=7)
        final = buf.getvalue()

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(final)

    _COMPRESSED_IMAGE_CACHE[src] = final
    return True


    rows = load_local()
    if not rows:
        print("local_games.txt not found or empty.")
        return

    image_exts = (".png", ".jpg", ".jpeg")
    ps2_id_pat = re.compile(VALID_GAMEID_PATTERNS[1], re.I)

    def normalize_id(s):
        return re.sub(r"[_\-.]", "", s.upper())

    def strip_lb_suffix(name):
        return re.sub(r"-\d+$", "", name)

    # ==================================================
    # LOAD LAUNCHBOX TITLES (ROM stem → Title)
    # ==================================================
    lb_title_map = {}

    if src_key in ("RA", "LB"):
        for plat, xmlfile in LAUNCHBOX_PLATFORMS.items():
            path = os.path.join(LAUNCHBOX_DATA_DIR, xmlfile)
            if not os.path.exists(path):
                continue

            try:
                tree = ET.parse(path)
                root = tree.getroot()
            except:
                continue

            for g in root.findall("Game"):
                app = g.findtext("ApplicationPath", "").strip()
                title = g.findtext("Title", "").strip()

                if not app or not title:
                    continue

                stem = os.path.splitext(os.path.basename(app))[0]
                lb_title_map[stem] = title

    copied_paths = []

    for line in rows:
        try:
            platform, _, gameid, file = [x.strip() for x in line.split("|")]
        except:
            continue

        rom_stem = os.path.splitext(os.path.basename(file))[0]
        lb_title = lb_title_map.get(rom_stem)
        src = None

        # --------------------------------------------------
        # LaunchBox gameplay images → RetroArch
        # --------------------------------------------------
        if src_key == "LB":
            root = os.path.join(LAUNCHBOX_IMG_DIR, platform, LAUNCHBOX_SCREEN_SUBDIR)
            if os.path.isdir(root):

                # 1) ROM filename priority
                for f in os.listdir(root):
                    b, e = os.path.splitext(f)
                    if e.lower() in image_exts and filenames_equivalent(
                        strip_lb_suffix(b), rom_stem, strip_ext=False
                    ):
                        src = os.path.join(root, f)
                        break

                # 2) LaunchBox XML title fallback
                if not src and lb_title:
                    for f in os.listdir(root):
                        b, e = os.path.splitext(f)
                        if e.lower() in image_exts and filenames_equivalent(
                            strip_lb_suffix(b), lb_title, strip_ext=False
                        ):
                            src = os.path.join(root, f)
                            break

        # --------------------------------------------------
        # RetroArch gameplay thumbnails → LaunchBox
        # --------------------------------------------------
        if not src and src_key == "RA":
            root = os.path.join(RETROARCH_IMG_DIR, platform, RETROARCH_SCREEN_SUBDIR)
            if os.path.isdir(root):

                # 1) LaunchBox XML title priority
                if lb_title:
                    for f in os.listdir(root):
                        b, e = os.path.splitext(f)
                        if e.lower() in image_exts and filenames_equivalent(
                            strip_lb_suffix(b), lb_title, strip_ext=False
                        ):
                            src = os.path.join(root, f)
                            break

                # 2) ROM filename fallback
                if not src:
                    for f in os.listdir(root):
                        b, e = os.path.splitext(f)
                        if e.lower() in image_exts and filenames_equivalent(
                            b, sanitize_rom_filename(rom_stem), strip_ext=False
                        ):
                            src = os.path.join(root, f)
                            break

        # --------------------------------------------------
        # RetroArch raw screenshots
        # --------------------------------------------------
        if not src and src_key in ("RA_RAW", "ALL"):
            root = os.path.join(RETROARCH_SCREEN_DIR, platform)
            if os.path.isdir(root):
                for r, _, files in os.walk(root):
                    for f in files:
                        b, e = os.path.splitext(f)
                        if e.lower() in image_exts and re.sub(
                            r"-\d{6}-\d{6}$", "", b
                        ) == rom_stem:
                            src = os.path.join(r, f)
                            break
                    if src:
                        break

        # --------------------------------------------------
        # Dolphin screenshots
        # --------------------------------------------------
        if not src and src_key in ("DOLPHIN", "ALL"):
            root = os.path.join(DOLPHIN_SCREEN_DIR, gameid)
            if os.path.isdir(root):
                for f in os.listdir(root):
                    if f.lower().endswith(image_exts):
                        src = os.path.join(root, f)
                        break

        # --------------------------------------------------
        # PCSX2 screenshots
        # --------------------------------------------------
        if not src and src_key in ("PCSX2", "ALL"):
            if os.path.isdir(PCSX2_SCREEN_DIR):
                for f in os.listdir(PCSX2_SCREEN_DIR):
                    if not f.lower().endswith(image_exts):
                        continue
                    m = ps2_id_pat.search(f)
                    if m and normalize_id(m.group(0)) == normalize_id(gameid):
                        src = os.path.join(PCSX2_SCREEN_DIR, f)
                        break

        if not src:
            continue

        ext = os.path.splitext(src)[1]

        temp_src = None
        if mode == 3 and src_key in ("RA_RAW", "DOLPHIN", "PCSX2", "ALL"):
            temp_src = os.path.join(
                os.path.dirname(__file__),
                f"__tmp_{os.getpid()}_{rom_stem}.png"
            )
            compress_and_copy_image(src, temp_src)

        targets = [mode] if mode in (1, 2) else [1, 2]

        for t in targets:
            if t == 1:
                tgt_root = os.path.join(
                    LAUNCHBOX_IMG_DIR, platform, LAUNCHBOX_SCREEN_SUBDIR
                )
                name = make_launchbox_image_name(platform, rom_stem, ext)
                if not name:
                    continue
                os.makedirs(tgt_root, exist_ok=True)

            else:
                tgt_platform_root = os.path.join(RETROARCH_IMG_DIR, platform)

                if not os.path.isdir(tgt_platform_root) and src_key == "ALL" and SYNC_BOTH_ACTIVE:
                    continue

                tgt_root = os.path.join(
                    tgt_platform_root, RETROARCH_SCREEN_SUBDIR
                )
                os.makedirs(tgt_root, exist_ok=True)
                name = sanitize_rom_filename(rom_stem) + ext

            dst = os.path.join(tgt_root, name)

            if temp_src:
                shutil.copy2(temp_src, dst)
            elif src_key in ("RA_RAW", "DOLPHIN", "PCSX2", "ALL"):
                compress_and_copy_image(src, dst)
            else:
                shutil.copy2(src, dst)

            copied_paths.append(dst)

        if temp_src and os.path.exists(temp_src):
            os.remove(temp_src)

    if src_key in ("RA_RAW", "DOLPHIN", "PCSX2", "ALL"):
        for p in copied_paths:
            print(p)

    if mode == 1:
        print(f"Copied {len(copied_paths)} screenshots to LaunchBox gameplay picture folder.")
    elif mode == 2:
        print(f"Copied {len(copied_paths)} screenshots to RetroArch gameplay picture folder.")

def sync_screenshots(mode, src_key):
    rows = load_local()
    if not rows:
        print("local_games.txt not found or empty.")
        return

    image_exts = (".png", ".jpg", ".jpeg")
    ps2_id_pat = re.compile(VALID_GAMEID_PATTERNS[1], re.I)

    proc_file = os.path.join(os.path.dirname(__file__), "processedscreens.txt")

    # --------------------------------------------------
    # Load processed registry:
    # (platform, target, identity) -> timestamp
    # --------------------------------------------------
    processed = {}
    if os.path.isfile(proc_file):
        with open(proc_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    plat, target, ident, ts = line.split("|", 3)
                    processed[(plat, target, ident)] = ts
                except:
                    continue

    def save_processed():
        with open(proc_file, "w", encoding="utf-8") as f:
            for (plat, target, ident), ts in sorted(processed.items()):
                f.write(f"{plat}|{target}|{ident}|{ts}\n")

    def extract_ts(name):
        m = re.search(r"-(\d{6})-(\d{6})$", os.path.splitext(name)[0])
        if not m:
            return None
        return m.group(1) + m.group(2)

    def extract_identity(name):
        base = os.path.splitext(name)[0]

        # RetroArch raw screenshots
        if re.search(r"-\d{6}-\d{6}$", base):
            return re.sub(r"-\d{6}-\d{6}$", "", base)

        # Dolphin / PCSX2 (strip trailing timestamp block)
        return re.sub(r"[_-]\d{8}[_-]\d{6}$", "", base)

    def normalize_id(s):
        return re.sub(r"[_\-.]", "", s.upper())

    def strip_lb_suffix(name):
        return re.sub(r"-\d+$", "", name)

    # ==================================================
    # LOAD LAUNCHBOX TITLES (ROM stem → Title)
    # ==================================================
    lb_title_map = {}

    if src_key in ("RA", "LB"):
        for plat, xmlfile in LAUNCHBOX_PLATFORMS.items():
            path = os.path.join(LAUNCHBOX_DATA_DIR, xmlfile)
            if not os.path.exists(path):
                continue
            try:
                tree = ET.parse(path)
                root = tree.getroot()
            except:
                continue

            for g in root.findall("Game"):
                app = g.findtext("ApplicationPath", "").strip()
                title = g.findtext("Title", "").strip()
                if not app or not title:
                    continue
                stem = os.path.splitext(os.path.basename(app))[0]
                lb_title_map[stem] = title

    copied_paths = []

    for line in rows:
        try:
            platform, _, gameid, file = [x.strip() for x in line.split("|")]
        except:
            continue

        rom_stem = os.path.splitext(os.path.basename(file))[0]
        lb_title = lb_title_map.get(rom_stem)

        src = None
        src_name = None

        # --------------------------------------------------
        # RetroArch raw screenshots
        # --------------------------------------------------
        if src_key in ("RA_RAW", "ALL"):
            root = os.path.join(RETROARCH_SCREEN_DIR, platform)
            if os.path.isdir(root):
                best = None
                for r, _, files in os.walk(root):
                    for f in files:
                        b, e = os.path.splitext(f)
                        if e.lower() not in image_exts:
                            continue
                        if re.sub(r"-\d{6}-\d{6}$", "", b) != rom_stem:
                            continue
                        ts = extract_ts(f)
                        if not ts:
                            continue
                        if not best or ts > best[0]:
                            best = (ts, os.path.join(r, f), f)
                if best:
                    src = best[1]
                    src_name = best[2]

        # --------------------------------------------------
        # Dolphin screenshots
        # --------------------------------------------------
        if not src and src_key in ("DOLPHIN", "ALL"):
            root = os.path.join(DOLPHIN_SCREEN_DIR, gameid)
            if os.path.isdir(root):
                best = None
                for f in os.listdir(root):
                    if not f.lower().endswith(image_exts):
                        continue
                    ts = extract_ts(f)
                    if not ts:
                        continue
                    if not best or ts > best[0]:
                        best = (ts, os.path.join(root, f), f)
                if best:
                    src = best[1]
                    src_name = best[2]

        # --------------------------------------------------
        # PCSX2 screenshots
        # --------------------------------------------------
        if not src and src_key in ("PCSX2", "ALL"):
            if os.path.isdir(PCSX2_SCREEN_DIR):
                best = None
                for f in os.listdir(PCSX2_SCREEN_DIR):
                    if not f.lower().endswith(image_exts):
                        continue
                    m = ps2_id_pat.search(f)
                    if not m or normalize_id(m.group(0)) != normalize_id(gameid):
                        continue
                    ts = extract_ts(f)
                    if not ts:
                        continue
                    if not best or ts > best[0]:
                        best = (ts, os.path.join(PCSX2_SCREEN_DIR, f), f)
                if best:
                    src = best[1]
                    src_name = best[2]

        if not src or not src_name:
            continue

        identity = extract_identity(src_name)
        ts = extract_ts(src_name)
        if not identity or not ts:
            continue

        ext = os.path.splitext(src)[1]

        targets = [mode] if mode in (1, 2) else [1, 2]

        for t in targets:
            if t == 1:
                target_name = "LaunchBox"
                tgt_root = os.path.join(
                    LAUNCHBOX_IMG_DIR, platform, LAUNCHBOX_SCREEN_SUBDIR
                )
                name = make_launchbox_image_name(platform, rom_stem, ext)
                if not name:
                    continue
            else:
                target_name = "RetroArch"
                tgt_root = os.path.join(
                    RETROARCH_IMG_DIR, platform, RETROARCH_SCREEN_SUBDIR
                )
                name = sanitize_rom_filename(rom_stem) + ext

            os.makedirs(tgt_root, exist_ok=True)

            key = (platform, target_name, identity)
            prev_ts = processed.get(key)

            dst = os.path.join(tgt_root, name)

            if prev_ts and ts <= prev_ts and os.path.exists(dst):
                continue

            if src_key in ("RA_RAW", "DOLPHIN", "PCSX2", "ALL"):
                compress_and_copy_image(src, dst)
            else:
                shutil.copy2(src, dst)

            copied_paths.append(dst)
            processed[key] = ts

    save_processed()

    if src_key in ("RA_RAW", "DOLPHIN", "PCSX2", "ALL"):
        for p in copied_paths:
            print(p)

    if mode == 1:
        print(f"Copied {len(copied_paths)} screenshots to LaunchBox gameplay picture folder.")
    elif mode == 2:
        print(f"Copied {len(copied_paths)} screenshots to RetroArch gameplay picture folder.")

# ============================================================
# ===================== COVER ENGINE ========================
# ============================================================

# ROMs whose covers should NOT be renamed to GameID
# Patterns are matched against ROM filename stem (no extension)
COVER_GAMEID_EXCEPTIONS = [
    r"^CodeBreaker v\d+",
]

def keep_rom_named_cover(stem):
    """
    Return True if this ROM's cover should keep the ROM-based
    filename instead of being renamed to GameID.
    """
    for pat in COVER_GAMEID_EXCEPTIONS:
        if re.match(pat, stem, re.I):
            return True
    return False

def sync_covers(src_name, src_root, tgt_name, tgt_root):
    rows = load_playtime_export()
    if not rows:
        print("playtime_export.txt not found or empty.")
        return

    local_rows = load_local()
    allowed_platforms = set()

    for line in local_rows:
        try:
            platform, _, _, _ = [x.strip() for x in line.split("|", 3)]
            allowed_platforms.add(platform)
        except:
            continue

    if tgt_name == "PCSX2":
        allowed_platforms &= set(SYSTEMS["PS2"]["platforms"])
    elif tgt_name == "Dolphin":
        allowed_platforms &= set(
            SYSTEMS["GC"]["platforms"] + SYSTEMS["WII"]["platforms"]
        )

    def strip_lb_suffix(name):
        return re.sub(r"-\d+$", "", name)

    def norm(s):
        return re.sub(r"[^A-Z0-9]", "", s.upper())

    # --------------------------------------------------
    # Load LaunchBox ROM stem → Title map
    # --------------------------------------------------
    lb_title_map = {}

    if tgt_name == "LaunchBox":
        for platform, xmlfile in LAUNCHBOX_PLATFORMS.items():
            path = os.path.join(LAUNCHBOX_DATA_DIR, xmlfile)
            if not os.path.exists(path):
                continue
            try:
                tree = ET.parse(path)
                root = tree.getroot()
            except:
                continue

            for g in root.findall("Game"):
                app = g.findtext("ApplicationPath", "").strip()
                title = g.findtext("Title", "").strip()
                if not app or not title:
                    continue
                stem = os.path.splitext(os.path.basename(app))[0]
                lb_title_map[(platform, stem)] = title

    entries = []
    for line in rows:
        try:
            platform, _, gameid, _, _, file = [x.strip() for x in line.split("|")]
            if platform in allowed_platforms:
                entries.append((platform, gameid, file))
        except:
            continue

    copied = 0
    image_exts = (".png", ".jpg", ".jpeg", ".webp")

    for platform, gameid, file in entries:
        rom_stem = os.path.splitext(os.path.basename(file))[0]
        src = None

        # --------------------------------------------------
        # Resolve source directory
        # --------------------------------------------------
        if src_name == "LaunchBox":
            src_dir = os.path.join(src_root, platform, LAUNCHBOX_COVER_SUBDIR)
        elif src_name == "RetroArch":
            src_dir = os.path.join(src_root, platform, RETROARCH_COVER_SUBDIR)
        else:
            src_dir = src_root

        # --------------------------------------------------
        # Dolphin: flat directory, filenames are GameID.png
        # --------------------------------------------------
        if src_name == "Dolphin" and os.path.isdir(src_dir):
            gid = norm(gameid)
            for fname in os.listdir(src_dir):
                base, ext = os.path.splitext(fname)
                if ext.lower() in image_exts and norm(base) == gid:
                    src = os.path.join(src_dir, fname)
                    break

        # --------------------------------------------------
        # PCSX2: flat directory, filenames are GameID
        # --------------------------------------------------
        if not src and src_name == "PCSX2" and os.path.isdir(src_dir):
            gid = norm(gameid)
            for fname in os.listdir(src_dir):
                base, ext = os.path.splitext(fname)
                if ext.lower() in image_exts and norm(base) == gid:
                    src = os.path.join(src_dir, fname)
                    break

        # --------------------------------------------------
        # Title-based fallback (LB / RA sources only)
        # --------------------------------------------------
        if not src:
            lb_title = lb_title_map.get((platform, rom_stem))
            if lb_title and os.path.isdir(src_dir):
                for fname in os.listdir(src_dir):
                    base, ext = os.path.splitext(fname)
                    if ext.lower() not in image_exts:
                        continue
                    if filenames_equivalent(strip_lb_suffix(base), lb_title, strip_ext=False):
                        src = os.path.join(src_dir, fname)
                        break

        if not src:
            continue

        ext = os.path.splitext(src)[1]

        # --------------------------------------------------
        # Target naming
        # --------------------------------------------------
        if tgt_name == "LaunchBox":
            tgt_dir = os.path.join(tgt_root, platform, LAUNCHBOX_COVER_SUBDIR)
            os.makedirs(tgt_dir, exist_ok=True)
            dst_name = make_launchbox_image_name(platform, rom_stem, ext)
            if not dst_name:
                continue

        elif tgt_name == "RetroArch":
            tgt_dir = os.path.join(tgt_root, platform, RETROARCH_COVER_SUBDIR)
            os.makedirs(tgt_dir, exist_ok=True)
            dst_name = sanitize_rom_filename(rom_stem) + ext

        else:
            tgt_dir = tgt_root
            dst_name = rom_stem + ext

        shutil.copy2(src, os.path.join(tgt_dir, dst_name))
        copied += 1

    if tgt_name == "LaunchBox":
        print(f"Copied {copied} covers to LaunchBox cover art folder.")
    elif tgt_name == "RetroArch":
        print(f"Copied {copied} covers to RetroArch cover art folder.")
    else:
        print(f"Copied {copied} covers.")
       
# ============================================================
# ===================== RENAME ENGINE ========================
# ============================================================

STEM_RE = re.compile(r"^(.*\.)[^.]+$")
BIN_TRACK_RE = re.compile(r"^(.*?)(\s+\(Track\s+\d+\))\.bin$", re.I)
CUE_RE = re.compile(r"^(.*)\.cue$", re.I)

# ---------- Stem helpers ----------

def cue_base(filename):
    return filename.rsplit(".", 1)[0]

def bin_base(filename):
    m = BIN_TRACK_RE.match(filename)
    if not m:
        return None, None
    return m.group(1), m.group(2)

# ---------- Rename plans ----------

def build_rom_rename_plan(rom_dir, old_filename, new_filename):
    plan = []

    # IMPORTANT:
    # ROM filenames must remain untouched.
    # RetroArch sanitization applies ONLY to thumbnails.
    oldBase = old_filename.rsplit(".", 1)[0]
    newBase = new_filename.rsplit(".", 1)[0]

    oldExt = old_filename.rsplit(".", 1)[1].lower()
    newExt = new_filename.rsplit(".", 1)[1].lower()

    oldCue = oldExt == "cue"

    if oldCue:
        oldCueBase = cue_base(old_filename)
        newCueBase = cue_base(new_filename)

    # --------------------------------------------------
    # RECURSIVE SCAN (supports game subfolders)
    # --------------------------------------------------
    for dirpath, _, files in os.walk(rom_dir):
        for fname in files:
            src = os.path.join(dirpath, fname)

            # Exact file rename (handles extension-only changes)
            if fname == old_filename:
                plan.append((src, os.path.join(dirpath, new_filename)))
                continue

            # Normal ROM + multi-dot save files
            if fname.startswith(oldBase + "."):
                newName = newBase + fname[len(oldBase):]
                if newName != fname:
                    plan.append((src, os.path.join(dirpath, newName)))
                continue

            # Cue track bins
            if oldCue:
                base, track = bin_base(fname)
                if base == oldCueBase:
                    newName = newCueBase + track + ".bin"
                    if newName != fname:
                        plan.append((src, os.path.join(dirpath, newName)))

    return plan

# ---------- Apply renames ----------

def apply_renames(rename_plan):
    targets = set(dst for _, dst in rename_plan)
    if len(targets) != len(rename_plan):
        raise RuntimeError("Filename collision in rename plan")

    for src, dst in rename_plan:
        if os.path.exists(dst):
            raise RuntimeError(f"Target already exists: {dst}")

    for src, dst in rename_plan:
        os.rename(src, dst)

# ---------- Save files ----------

def rename_save_files(old_filename, new_filename, platform=None, system=None):
    saves_root = os.path.join(RETROARCH_DIR, "saves")
    if not os.path.isdir(saves_root):
        return

    oldStem = old_filename.rsplit(".", 1)[0]
    newStem = new_filename.rsplit(".", 1)[0]

    roots = []

    # root saves dir
    roots.append(saves_root)

    # platform saves
    if platform:
        plat_dir = os.path.join(saves_root, platform)
        if os.path.isdir(plat_dir):
            roots.append(plat_dir)

    # core saves
    if system:
        for core in SYSTEM_TO_CORES.get(system, []):
            core_dir = os.path.join(saves_root, core)
            if os.path.isdir(core_dir):
                roots.append(core_dir)

    for root in roots:
        for dirpath, _, files in os.walk(root):
            for fname in files:
                base, ext = os.path.splitext(fname)

                if base != oldStem:
                    continue

                newName = newStem + ext
                if newName == fname:
                    continue

                src = os.path.join(dirpath, fname)
                dst = os.path.join(dirpath, newName)

                if not os.path.exists(dst):
                    os.rename(src, dst)

# ---------- Log files ----------

def rename_retroarch_logs(old_file, new_file, platform=None, system=None):
    oldbase = os.path.splitext(old_file)[0]
    newbase = os.path.splitext(new_file)[0]

    if not os.path.isdir(RETROARCH_LOG_DIR):
        return

    roots = [RETROARCH_LOG_DIR]

    # platform logs
    if platform:
        plat_dir = os.path.join(RETROARCH_LOG_DIR, platform)
        if os.path.isdir(plat_dir):
            roots.append(plat_dir)

    # core logs
    if system:
        for core in SYSTEM_TO_CORES.get(system, []):
            core_dir = os.path.join(RETROARCH_LOG_DIR, core)
            if os.path.isdir(core_dir):
                roots.append(core_dir)

    for root in roots:
        for dirpath, _, files in os.walk(root):
            for fname in files:
                if fname != oldbase + ".lrtl":
                    continue

                src = os.path.join(dirpath, fname)
                dst = os.path.join(dirpath, newbase + ".lrtl")

                if not os.path.exists(dst):
                    os.rename(src, dst)

# ---------- CUE rewriting ----------

def rewrite_cue_file(cue_path, oldBase, newBase):
    lines = []

    with open(cue_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            stripped = line.strip()
            if stripped.upper().startswith("FILE ") and '"' in line:
                try:
                    prefix, rest = line.split('"', 1)
                    filename, suffix = rest.split('"', 1)
                    if filename.startswith(oldBase):
                        filename = newBase + filename[len(oldBase):]
                    line = prefix + '"' + filename + '"' + suffix
                except:
                    pass
            lines.append(line)

    with open(cue_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

# ---------- Stem replacement ----------

def replace_stem_in_file(path, oldStem, newStem):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    if oldStem not in text:
        return False

    text = text.replace(oldStem, newStem)

    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    return True

def replace_stem_in_tree(root, oldStem, newStem, exts=None):
    """
    Replace filename stem matches in a directory tree using
    tolerant RetroArch-safe matching.
    """
    if not os.path.isdir(root):
        return

    for dirpath, _, files in os.walk(root):
        for fname in files:
            base, ext = os.path.splitext(fname)

            if exts and ext.lower() not in exts:
                continue

            if not filenames_equivalent(base, oldStem, strip_ext=False):
                continue

            newName = newStem + ext
            if newName == fname:
                continue

            src = os.path.join(dirpath, fname)
            dst = os.path.join(dirpath, newName)

            if not os.path.exists(dst):
                os.rename(src, dst)

# ============================================================
# ===================== MODIFY PLANNER ======================
# ============================================================

def rename_platform_images(platform, old_file, new_file):

    oldStem, _ = os.path.splitext(old_file)
    newStem, _ = os.path.splitext(new_file)

    roots = []

    # RetroArch thumbnails (SANITIZED)
    if "RETROARCH_IMG_DIR" in globals():
        ra_root = os.path.join(RETROARCH_IMG_DIR, platform)
        if os.path.isdir(ra_root):
            roots.append(("retroarch", ra_root))

    # Additional raw images directory (KEEP &)
    if ADITIONAL_IMG_DIR:
        raw_root = os.path.join(ADITIONAL_IMG_DIR, platform)
        if os.path.isdir(raw_root):
            roots.append(("raw", raw_root))

    for kind, root in roots:
        if kind == "retroarch":
            targetStem = sanitize_rom_filename(newStem)
        else:
            targetStem = newStem

        for dirpath, _, files in os.walk(root):
            for fname in files:
                base, ext = os.path.splitext(fname)

                if not filenames_equivalent(base, oldStem, strip_ext=False):
                    continue

                newName = targetStem + ext
                if newName == fname:
                    continue

                src = os.path.join(dirpath, fname)
                dst = os.path.join(dirpath, newName)

                if not os.path.exists(dst):
                    os.rename(src, dst)

    # --------------------------------------------------
    # RetroArch RAW screenshots with timestamp suffix
    # <ROM>-XXXXXX-XXXXXX.png
    # --------------------------------------------------
    if not RETROARCH_SCREEN_DIR:
        return

    plat_root = os.path.join(RETROARCH_SCREEN_DIR, platform)
    if not os.path.isdir(plat_root):
        return

    ts_re = re.compile(r"^(.*?)(-\d{6}-\d{6})$")

    for dirpath, _, files in os.walk(plat_root):
        for fname in files:
            base, ext = os.path.splitext(fname)
            if ext.lower() not in (".png", ".jpg", ".jpeg"):
                continue

            m = ts_re.match(base)
            if not m:
                continue

            stem, ts = m.groups()

            if not filenames_equivalent(stem, oldStem, strip_ext=False):
                continue

            newName = newStem + ts + ext
            if newName == fname:
                continue

            src = os.path.join(dirpath, fname)
            dst = os.path.join(dirpath, newName)

            if not os.path.exists(dst):
                os.rename(src, dst)


def build_modify_plans(old_lines, new_lines, local_rows, play_rows):
    def parse(row):
        parts = [x.strip() for x in row.split("|")]
        if len(parts) != 6:
            raise ValueError("Invalid row: " + row)
        return parts

    # build local map (robust: tolerate malformed lines)
    local_map = {}
    for r in local_rows:
        parts = [x.strip() for x in r.split("|", 3)]
        if len(parts) < 4:
            # skip malformed local row
            continue
        p, t, g, f = parts[:4]
        local_map[(p, t, g, f)] = r

    # build playtime map (skip malformed play rows)
    play_map = {}
    for r in play_rows:
        try:
            p, t, g, pt, lp, f = parse(r)
        except ValueError:
            continue
        play_map[(p, t, g, f)] = r

    replacements_local = {}
    replacements_play  = {}
    rename_jobs = []
    time_jobs = []   # (platform, gameid, newfile, seconds, lastplayed)

    # --------------------------------------------------
    # processed screenshots registry
    # --------------------------------------------------
    proc_file = os.path.join(os.path.dirname(__file__), "processedscreens.txt")
    proc_lines = []
    if os.path.isfile(proc_file):
        with open(proc_file, "r", encoding="utf-8") as f:
            proc_lines = [l.rstrip("\n") for l in f]

    proc_updated = False

    for old, new in zip(old_lines, new_lines):
        op, ot, og, opt, olp, of = parse(old)
        np, nt, ng, npt, nlp, nf = parse(new)

        # Identity must match
        if (op, ot, og) != (np, nt, ng):
            raise RuntimeError(
                "Identity change not allowed:\n"
                + old + "\n" + new
            )

        key = (op, ot, og, of)
        if key not in local_map:
            raise RuntimeError(
                "Original not found in local_games.txt:\n" + old
            )

        system = PLATFORM_TO_SYSTEM.get(op)

        # --------------------------------------------------
        # 🚫 HARD BLOCK: MAME-based systems
        # --------------------------------------------------
        if system in ("ARCADE", "GW"):
            if ot != nt:
                raise RuntimeError(
                    f"Title rename is not allowed for {op} (MAME-based system)"
                )
            if of != nf:
                raise RuntimeError(
                    f"ROM rename is not allowed for {op} (MAME-based system)"
                )

        # --------------------------------------------------
        # local_games.txt
        # --------------------------------------------------
        replacements_local[local_map[key]] = (
            f"{op} | {ot} | {og} | {nf}"
        )

        # --------------------------------------------------
        # playtime_export.txt
        # --------------------------------------------------
        old_play = play_map.get(key)
        if old_play:
            if not npt and not nlp:
                _, _, _, pt, lp, _ = parse(old_play)
                npt, nlp = pt, lp

            replacements_play[old_play] = (
                f"{op} | {ot} | {og} | {npt} | {nlp} | {nf}"
            )
        else:
            if npt or nlp:
                replacements_play[
                    f"{op} | {ot} | {og} | 0 |  | {of}"
                ] = (
                    f"{op} | {ot} | {og} | {npt} | {nlp} | {nf}"
                )

        # --------------------------------------------------
        # Filename rename (non-MAME only)
        # --------------------------------------------------
        if of != nf:
            rom_dir = os.path.join(GAMES_DIR, op)
            rename_jobs.extend(
                expand_multidisc_renames(rom_dir, of, nf)
            )

            # ----------------------------------------------
            # processedscreens.txt update: update identity prefix
            # ----------------------------------------------
            old_base = os.path.splitext(of)[0]
            new_base = os.path.splitext(nf)[0]

            new_proc = []
            for line in proc_lines:
                try:
                    plat, target, ident, ts = line.split("|", 3)
                except:
                    # preserve malformed/unknown lines unchanged
                    new_proc.append(line)
                    continue

                if plat == op and ident.startswith(old_base):
                    ident = new_base + ident[len(old_base):]
                    proc_updated = True

                new_proc.append(f"{plat}|{target}|{ident}|{ts}")

            proc_lines = new_proc

        # --------------------------------------------------
        # Playtime propagation
        # --------------------------------------------------
        if (npt or nlp) or (opt != npt or olp != nlp):
            seconds = parse_seconds(npt)
            time_jobs.append((op, og, nf, seconds, nlp))

    # --------------------------------------------------
    # Write processed screen updates
    # --------------------------------------------------
    if proc_updated:
        with open(proc_file, "w", encoding="utf-8") as f:
            for l in proc_lines:
                f.write(l + "\n")

    return replacements_local, replacements_play, rename_jobs, time_jobs

def run_modify_direct(old_lines, new_lines):
    local_rows = load_local()
    play_rows = load_playtime_export()

    (
        replacements_local,
        replacements_play,
        rename_jobs,
        time_jobs,
        processed_renames,
    ) = build_modify_plans(old_lines, new_lines, local_rows, play_rows)

    # ----------------------------------
    # ROM renames + filesystem effects
    # ----------------------------------
    apply_rename_jobs(rename_jobs)

    # ----------------------------------
    # Databases
    # ----------------------------------
    replace_lines_in_file(LOCAL_DB, replacements_local)
    replace_lines_in_file(PLAYTIME_EXPORT, replacements_play)

    # ----------------------------------
    # processedscreens.txt
    # ----------------------------------
    proc_file = os.path.join(os.path.dirname(__file__), "processedscreens.txt")
    proc_lines = []

    if os.path.isfile(proc_file):
        with open(proc_file, "r", encoding="utf-8") as f:
            proc_lines = [l.rstrip("\n") for l in f]

    if proc_lines and processed_renames:
        new_proc = []

        for line in proc_lines:
            try:
                plat, target, ident, ts = line.split("|", 3)
            except:
                new_proc.append(line)
                continue

            for p, oldf, newf in processed_renames:
                if plat != p:
                    continue

                old_base = os.path.splitext(oldf)[0]
                new_base = os.path.splitext(newf)[0]

                if ident.startswith(old_base):
                    ident = new_base + ident[len(old_base):]

            new_proc.append(f"{plat}|{target}|{ident}|{ts}")

        with open(proc_file, "w", encoding="utf-8") as f:
            for l in new_proc:
                f.write(l + "\n")

    # ----------------------------------
    # Playtime propagation
    # ----------------------------------
    for platform, gameid, filename, seconds, lastplayed in time_jobs:
        write_retroarch_time(filename, seconds, lastplayed)
        write_launchbox_time(platform, gameid, filename, seconds, lastplayed)

        system = PLATFORM_TO_SYSTEM.get(platform)

        if use_standalone_emulator(system):
            if system in ("GC", "WII"):
                write_dolphin_time(gameid, seconds)
            if system == "PS2":
                write_pcsx2_time(gameid, seconds, lastplayed)


# ============================================================
# ===================== COMMAND ENGINE ======================
# ============================================================

# ---------- Scanner ----------
def run_scanner(force=False):
    env = os.environ.copy()
    if force:
        env["FORCE_RESCAN"] = "1"
    run_scanner_process(env=env)

def cmd_rescan():
    run_scanner(force=True)

    # Rebuild playtime export after rescan
    print()
    cmd_export_playtime()

# ---------- Paths check ----------
def cmd_check_paths():
    print("\n=== System Paths ===\n")

    def status(path):
        return f" {Fore.LIGHTGREEN_EX}OK{Style.RESET_ALL} " if os.path.exists(path) else f" {Fore.LIGHTRED_EX}XX{Style.RESET_ALL} "

    def row(label, path):
        rows.append((status(path), label, path))

    rows = []

    row("RetroArch Directory:", SETUP["RETROARCH_DIR"])
    row("RetroArch Games Directory:", SETUP["GAMES_DIR"])

    for plat in PLATFORMS_ORDERED:
        path = os.path.join(SETUP["GAMES_DIR"], plat)
        row(f"{plat} Directory:", path)

    row("RetroArch Playlists Directory:", SETUP["RETROARCH_PLAYLIST_DIR"])
    row("RetroArch Logs Directory:", SETUP["RETROARCH_LOG_DIR"])
    row("Dolphin Directory:", SETUP["DOLPHIN_DIR"])
    row("Dolphin playtime:", SETUP["DOLPHIN_PLAYTIME"])
    row("PCSX2 Directory:", SETUP["PCSX2_DIR"])
    row("PCSX2 playtime:", SETUP["PCSX2_PLAYTIME"])
    row("LaunchBox Data:", SETUP["LAUNCHBOX_DATA_DIR"])

    # ---------- PC Games ----------
    row("Minecraft Directory:", SETUP.get("MINECRF_DIR"))
    row("WoW Retail Directory:", SETUP.get("WOWRE_DIR"))
    row("WoW Classic Era Directory:", SETUP.get("WOWERA_DIR"))
    row("WoW Classic Progression Directory:", SETUP.get("WOWCLA_DIR"))


    width = max(len(r[1]) for r in rows) + 2
    for s, label, path in rows:
        print(f"[{s}] {label:<{width}} {path}")

    print("\n=== LaunchBox Platform XML ===\n")

    xml_rows = []
    for plat, fname in SETUP["LAUNCHBOX_PLATFORMS"].items():
        path = os.path.join(SETUP["LAUNCHBOX_DATA_DIR"], fname)
        xml_rows.append((
            f" {Fore.LIGHTGREEN_EX}OK{Style.RESET_ALL} " if os.path.exists(path) else f" {Fore.LIGHTRED_EX}XX{Style.RESET_ALL} ",
            plat,
            fname
        ))

    w = max(len(r[1]) for r in xml_rows) + 2
    for s, plat, fname in xml_rows:
        print(f"[{s}] {plat:<{w}} {fname}")

    print("\nStatus:", "ALL SYSTEMS OK" if all(
        os.path.exists(p) for _, _, p in rows
    ) else "ERRORS FOUND")

# ---------- Change Retroarch labels ----------

def backup_retroarch_labels():
    """
    Create a timestamped backup of all RetroArch playlist labels.
    """
    if not os.path.isdir(RETROARCH_PLAYLIST_DIR):
        return None

    # Backup folder inside script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    backup_dir = os.path.join(script_dir, "backup")
    os.makedirs(backup_dir, exist_ok=True)

    stamp = datetime.datetime.now().strftime("%Y_%m_%d-%H_%M_%S")
    out_file = os.path.join(backup_dir, f"label_backup_{stamp}.txt")

    lines = []

    for fname in sorted(os.listdir(RETROARCH_PLAYLIST_DIR)):
        if not fname.lower().endswith(".lpl"):
            continue

        path = os.path.join(RETROARCH_PLAYLIST_DIR, fname)

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            continue

        for entry in data.get("items", []):
            crc = entry.get("crc32", "").strip()
            label = entry.get("label", "").strip()

            if not crc or not label:
                continue

            lines.append(
                f'{fname}, "crc32": "{crc}", "label": "{label}"'
            )

    with open(out_file, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")

    return out_file
    
def restore_labels_from_oldest_backup():
    """
    Restore playlist labels using the oldest label backup.
    Matches by (playlist filename + crc32).
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    backup_dir = os.path.join(script_dir, "backup")

    if not os.path.isdir(backup_dir):
        print("No backup folder found.")
        return

    backups = sorted(
        f for f in os.listdir(backup_dir)
        if f.startswith("label_backup_") and f.endswith(".txt")
    )

    if not backups:
        print("No label backups found.")
        return

    oldest = os.path.join(backup_dir, backups[0])

    restore_map = {}  # (playlist, crc32) -> label

    with open(oldest, "r", encoding="utf-8") as f:
        for line in f:
            try:
                playlist, rest = line.split(",", 1)
                crc = rest.split('"crc32": "')[1].split('"')[0]
                label = rest.split('"label": "')[1].rsplit('"', 1)[0]
                restore_map[(playlist.strip(), crc.strip())] = label
            except:
                continue

    restored = 0

    for fname in os.listdir(RETROARCH_PLAYLIST_DIR):
        if not fname.lower().endswith(".lpl"):
            continue

        path = os.path.join(RETROARCH_PLAYLIST_DIR, fname)

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            continue

        changed = False

        for item in data.get("items", []):
            key = (fname, item.get("crc32", "").strip())
            if key in restore_map:
                new_label = restore_map[key]
                if item.get("label") != new_label:
                    item["label"] = new_label
                    changed = True
                    restored += 1

        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Restored {restored} labels from backup: {backups[0]}")

def set_labels_to_rom_filename():
    """
    Set labels to ROM filename stem.

    Exceptions:
    - Arcade platforms always use database title
    - 3DS strips ".standard"
    """
    db = {}

    # Load database map (filename → title)
    try:
        with open("local_games.txt", "r", encoding="utf-8") as f:
            for line in f:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) != 4:
                    continue
                title = parts[1]
                filename = parts[3]
                db[filename] = title
    except:
        print("local_games.txt not found.")
        return

    updated = 0

    for playlist in os.listdir(RETROARCH_PLAYLIST_DIR):
        if not playlist.lower().endswith(".lpl"):
            continue

        playlist_name = os.path.splitext(playlist)[0]
        path = os.path.join(RETROARCH_PLAYLIST_DIR, playlist)

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            continue

        changed = False
        is_arcade = playlist_name in ARCADE_PLATFORMS
        is_3ds = "3ds" in playlist_name.lower()

        for item in data.get("items", []):
            rom_path = item.get("path", "").strip()
            if not rom_path:
                continue

            filename = os.path.basename(rom_path)

            # Arcade → force DB title
            if is_arcade and filename in db:
                new_label = db[filename]
            else:
                stem = os.path.splitext(filename)[0]

                if is_3ds and stem.endswith(".standard"):
                    stem = stem[:-9]

                new_label = stem

            if item.get("label") != new_label:
                item["label"] = new_label
                changed = True
                updated += 1

        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Updated {updated} labels to ROM filenames.")

def set_labels_to_database_titles():
    """
    Set RetroArch playlist labels using local_games.txt database titles.
    Matches by filename.
    """
    db = {}

    # Build filename → title map
    try:
        with open("local_games.txt", "r", encoding="utf-8") as f:
            for line in f:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) != 4:
                    continue

                title = parts[1]
                filename = parts[3]

                db[filename] = title
    except:
        print("local_games.txt not found.")
        return

    updated = 0

    for fname in os.listdir(RETROARCH_PLAYLIST_DIR):
        if not fname.lower().endswith(".lpl"):
            continue

        path = os.path.join(RETROARCH_PLAYLIST_DIR, fname)

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            continue

        changed = False

        for item in data.get("items", []):
            rom_path = item.get("path", "").strip()
            if not rom_path:
                continue

            filename = os.path.basename(rom_path)

            if filename not in db:
                continue

            new_label = db[filename]

            if item.get("label") != new_label:
                item["label"] = new_label
                changed = True
                updated += 1

        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Updated {updated} labels from database.")

def cmd_change_labels():
    backup_file = backup_retroarch_labels()

    if backup_file:
        print(f"\nAll labels have been backed up → {backup_file}")
    else:
        print("\nNo playlists found to back up.")

    print(f"{Fore.LIGHTRED_EX}[WARNING]{Style.RESET_ALL} This will overwrite ALL current Retroarch game labels.\n")

    while True:
        print("Select source for labels")
        print("1) Restore labels from oldest backup")
        print("2) Set labels to match ROM filename")
        print("3) Set labels to match database title")
        print("4) Exit")

        choice = input("\nSelect option: ").strip()

        if choice == "4":
            print("Change labels cancelled.")
            return

        if choice == "1":
            restore_labels_from_oldest_backup()
            return

        if choice == "2":
            set_labels_to_rom_filename()
            return

        if choice == "3":
            set_labels_to_database_titles()
            return

        print("\nInvalid selection.\n")

# ---------- Export ----------

def cmd_export_playtime():
    print("Loading playtime sources.")

    rows = load_local()

    if not rows:
        if SCANNER_EXEC:
            print("local_games.txt is empty or missing")
            return
        else:
            rows = []

    ra = load_retroarch_playtime()
    pcsx2 = load_pcsx2_playtime()
    dolphin = load_dolphin_playtime()
    lb = load_launchbox_lastplayed()
    minecraft = load_minecraft_playtime()
    wow_retail = load_wow_playtime(SETUP.get("WOWRE_DIR"))
    wow_era    = load_wow_playtime(SETUP.get("WOWERA_DIR"))
    wow_classic = load_wow_playtime(SETUP.get("WOWCLA_DIR"))

    CODEWORDS = [
        "(patched)", "[patched]", "(hack)", "[hack]",
    ]

    out = []
    printed = []
    pc_rows = []

    # key -> (bracket_count, has_codeword, row_color, row_plain)
    best = {}

    for line in rows:
        try:
            platform, title, game_id, file = [x.strip() for x in line.split("|", 3)]
        except:
            continue

        system = PLATFORM_TO_SYSTEM.get(platform)

        seconds = 0
        last_played = ""

        rom_stem = os.path.splitext(os.path.basename(file))[0]

        # ---------- RetroArch ----------
        if rom_stem in ra:
            seconds = ra[rom_stem].get("seconds", 0)
            lp = ra[rom_stem].get("last_played", "")
            if lp:
                last_played = lp

        # ---------- Standalone emulators ----------
        if use_standalone_emulator(system):

            # ---------- PCSX2 ----------
            if system == "PS2" and game_id in pcsx2:
                seconds, lp = pcsx2[game_id]
                if lp:
                    last_played = lp

            # ---------- Dolphin (GC/Wii) ----------
            elif system in ("GC", "WII") and game_id in dolphin:
                seconds = dolphin[game_id]

                # last-played now matched by ROM filename stem
                if rom_stem in lb:
                    last_played = lb[rom_stem]

        # ---------- Low-playtime filtering ----------
        if seconds < 300 and not PRINT_ALL:
            continue

        sep_color = f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} "
        sep_plain = " | "

        row_plain = (
            f"{platform}"
            f"{sep_plain}{title}"
            f"{sep_plain}{game_id}"
            f"{sep_plain}{format_playtime(seconds)}"
            f"{sep_plain}{last_played}"
            f"{sep_plain}{file}"
        )

        row_color = (
            f"{platform}"
            f"{sep_color}{title}"
            f"{sep_color}{game_id}"
            f"{sep_color}{format_playtime(seconds)}"
            f"{sep_color}{last_played}"
            f"{sep_color}{file}"
        )

        if not PRINT_ALL:
            key = (game_id, seconds)

            bracket_count = title.count("[")
            title_l = title.lower()
            has_codeword = any(cw in title_l for cw in CODEWORDS)

            prev = best.get(key)
            if prev:
                prev_brackets, prev_has_codeword, _, _ = prev

                if bracket_count > prev_brackets:
                    continue
                if bracket_count == prev_brackets:
                    if has_codeword and not prev_has_codeword:
                        continue
                    if has_codeword == prev_has_codeword:
                        continue

            best[key] = (bracket_count, has_codeword, row_color, row_plain)

        else:
            printed.append((row_color, row_plain))

    # =========================================================
    # PC GAMES (COLLECT ONLY)
    # =========================================================

    if minecraft:
        seconds, last_played = minecraft
        if seconds >= 500 or PRINT_ALL:
            row_plain = (
                "PC - Minecraft | Minecraft Java Edition | MINECRAFT-JAVA | "
                f"{format_playtime(seconds)} | {last_played} | Minecraft.exe"
            )
            row_color = (
                f"PC - Minecraft"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} Minecraft Java Edition"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} MINECRAFT-JAVA"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} {format_playtime(seconds)}"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} {last_played}"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} Minecraft.exe"
            )
            pc_rows.append((row_color, row_plain))

    if wow_retail:
        seconds, last_played = wow_retail
        if seconds >= 500 or PRINT_ALL:
            row_plain = (
                "PC - World of Warcraft | World of Warcraft | WOW-RETAIL | "
                f"{format_playtime(seconds)} | {last_played} | Wow.exe"
            )
            row_color = (
                f"PC - World of Warcraft"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} World of Warcraft"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} WOW-RETAIL"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} {format_playtime(seconds)}"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} {last_played}"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} Wow.exe"
            )
            pc_rows.append((row_color, row_plain))

    if wow_era:
        seconds, last_played = wow_era
        if seconds >= 500 or PRINT_ALL:
            row_plain = (
                "PC - World of Warcraft | World of Warcraft Classic Era | WOW-CLASSIC-ERA | "
                f"{format_playtime(seconds)} | {last_played} | WowClassic.exe"
            )
            row_color = (
                f"PC - World of Warcraft"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} World of Warcraft Classic Era"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} WOW-CLASSIC-ERA"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} {format_playtime(seconds)}"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} {last_played}"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} WowClassic.exe"
            )
            pc_rows.append((row_color, row_plain))

    if wow_classic:
        seconds, last_played = wow_classic
        if seconds >= 500 or PRINT_ALL:
            row_plain = (
                "PC - World of Warcraft | World of Warcraft Classic | WOW-CLASSIC | "
                f"{format_playtime(seconds)} | {last_played} | WowClassic.exe"
            )
            row_color = (
                f"PC - World of Warcraft"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} World of Warcraft Classic"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} WOW-CLASSIC"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} {format_playtime(seconds)}"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} {last_played}"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} WowClassic.exe"
            )
            pc_rows.append((row_color, row_plain))

    # =========================================================
    # EMIT RESULTS
    # =========================================================

    if PRINT_ALL:
        for row_color, row_plain in printed:
            print(row_color)
            out.append(row_plain)
    else:
        for _, _, row_color, row_plain in best.values():
            print(row_color)
            out.append(row_plain)

    for row_color, row_plain in pc_rows:
        print(row_color)
        out.append(row_plain)

    save_playtime_export(out)
    print(f"Created {PLAYTIME_EXPORT} ({len(out)} entries)")

# ---------- Sync ----------

def cmd_sync():
    """
    Sync playtime and last-played data into LaunchBox XML ONLY.
    No scanning. No exporting. No implicit side effects.
    """
    print("Syncing playtime to LaunchBox...")

    rows = load_playtime_export()
    if not rows:
        print("No playtime data found. Run export first.")
        return

    wow_seconds = 0
    wow_last = ""

    for row in rows:
        try:
            platform, title, gameid, pt, lp, file = \
                [x.strip() for x in row.split("|")]
        except:
            continue

        seconds = parse_seconds(pt)

        # ---------- Windows: Minecraft ----------
        if platform == "PC - Minecraft":
            write_launchbox_windows_time(
                ["Minecraft: Java Edition", "Minecraft"],
                seconds,
                lp
            )
            continue

        # ---------- Windows: World of Warcraft (merge) ----------
        if platform == "PC - World of Warcraft":
            wow_seconds += seconds
            if lp and (not wow_last or lp > wow_last):
                wow_last = lp
            continue

        # ---------- Normal LaunchBox platforms ----------
        write_launchbox_time(
            platform,
            gameid,
            file,
            seconds,
            lp
        )

    # ---------- Emit merged WoW ----------
    if wow_seconds:
        write_launchbox_windows_time(
            ["World of Warcraft"],
            wow_seconds,
            wow_last
        )

    print("LaunchBox sync complete.")

# ---------- Link pictures ----------

def cmd_link_pictures():
    print("\nLink pictures to Retroarch and Launchbox\n")

    options = [
        ("LaunchBox", LAUNCHBOX_IMG_DIR, True),
        ("RetroArch", RETROARCH_IMG_DIR, True),
        ("Additional", ADITIONAL_IMG_DIR, False),
        ("All", None, None),
        ("Exit", None, None),
    ]

    VALID_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
    dash2_re = re.compile(r"-\d{2}$")
    tag_re = re.compile(r"[\[\(].*?[\]\)]")

    print("Select image source:")
    for i, (name, path, _) in enumerate(options, 1):
        print(f"{i}) {name}")
        if path:
            print(f"     {status_ok()} {path}")

    choice = input("> ").strip()
    if choice == "5":
        return

    IMAGE_SOURCES = []
    if choice == "1":
        IMAGE_SOURCES.append((LAUNCHBOX_IMG_DIR, True))
    elif choice == "2":
        IMAGE_SOURCES.append((RETROARCH_IMG_DIR, True))
    elif choice == "3":
        IMAGE_SOURCES.append((ADITIONAL_IMG_DIR, False))
    elif choice == "4":
        IMAGE_SOURCES.extend([
            (LAUNCHBOX_IMG_DIR, True),
            (RETROARCH_IMG_DIR, True),
            (ADITIONAL_IMG_DIR, False),
        ])
    else:
        return

    rows = load_local()
    if not rows:
        print("local_games.txt not found or empty.")
        return

    roms_by_platform = {}
    titles_by_platform = {}

    # ==================================================
    # LOAD LAUNCHBOX TITLES FROM XML (ROM stem → Title)
    # Use the LAUNCHBOX_PLATFORMS mapping key (plat) as the
    # platform key so it matches local_games.txt platform values.
    # ==================================================
    lb_title_map = {}

    for plat, xmlfile in LAUNCHBOX_PLATFORMS.items():
        path = os.path.join(LAUNCHBOX_DATA_DIR, xmlfile)
        if not os.path.exists(path):
            continue

        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except:
            continue

        for g in root.findall("Game"):
            app = g.findtext("ApplicationPath", "").strip()
            title = g.findtext("Title", "").strip()
            # intentionally use 'plat' (the LAUNCHBOX_PLATFORMS key) here
            if not app or not title:
                continue

            stem = os.path.splitext(os.path.basename(app))[0]
            lb_title_map.setdefault(plat, {})[stem] = title

    # ==================================================
    # normalizer helper
    # ==================================================
    def normalize_text(s):
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))

        s = dash2_re.sub("", s)
        s = tag_re.sub("", s)

        s = s.lower()

        # remove common leading/trailing articles anywhere
        for article in ("the", "die", "les"):
            s = re.sub(rf"\b{article}\b", "", s)

        # remove punctuation and separators (including comma)
        s = re.sub(r"[.,\-_\&:\[\]\(\)\s]", "", s)

        return s

    # Build reverse mapping: platform -> normalized title -> stem
    title_to_stem = {}
    for platform, m in lb_title_map.items():
        rev = {}
        for stem, title in m.items():
            rev[normalize_text(title)] = stem
            rev[title] = stem
        title_to_stem[platform] = rev

    # ==================================================
    # LOAD ROMS FROM local_games.txt (ROM is authoritative)
    # ==================================================
    for line in rows:
        try:
            platform, _, _, file = [x.strip() for x in line.split("|")]
        except:
            continue

        if BIN_TRACK_RE.match(file):
            continue

        stem, _ = os.path.splitext(file)
        roms_by_platform.setdefault(platform, []).append(stem)

        xml_title = lb_title_map.get(platform, {}).get(stem)
        if xml_title:
            titles_by_platform.setdefault(platform, []).append(xml_title)

    planned_jobs = []
    total = 0

    for platform, rom_stems in roms_by_platform.items():
        platform_printed = False
        titles = titles_by_platform.get(platform, [])

        for root, strict in IMAGE_SOURCES:
            if not root:
                continue

            if strict:
                if root == RETROARCH_IMG_DIR:
                    subdirs = [
                        (RETROARCH_COVER_SUBDIR, False),
                        (RETROARCH_SCREEN_SUBDIR, False),
                    ]
                else:
                    subdirs = [
                        (LAUNCHBOX_COVER_SUBDIR, True),
                        (LAUNCHBOX_SCREEN_SUBDIR, True),
                    ]
            else:
                subdirs = [(None, False)]

            for subdir, is_launchbox in subdirs:
                img_dir = (
                    os.path.join(root, platform, subdir)
                    if subdir else root
                )
                if not os.path.isdir(img_dir):
                    continue

                files = [
                    f for f in os.listdir(img_dir)
                    if os.path.splitext(f)[1].lower() in VALID_IMAGE_EXTS
                ]

                file_bases = {os.path.splitext(f)[0]: f for f in files}
                pool = titles if is_launchbox else rom_stems

                # Reserve exact matches so we don't accidentally reassign them
                reserved_bases = set()
                for item in pool:
                    if is_launchbox:
                        exact_names = {
                            item,
                            item.replace("'", "_"),
                            item.replace(":", "_"),
                        }
                        for base in file_bases:
                            if dash2_re.sub("", base) in exact_names:
                                reserved_bases.add(base)
                    else:
                        exact_names = {
                            item,
                            item.replace("&", "_"),
                        }
                        for base in file_bases:
                            if base in exact_names:
                                reserved_bases.add(base)

                for item in pool:
                    if is_launchbox:
                        exact_names = {
                            item,
                            item.replace("'", "_"),
                            item.replace(":", "_"),
                        }
                        if any(dash2_re.sub("", b) in exact_names for b in reserved_bases):
                            continue
                    else:
                        exact_names = {
                            item,
                            item.replace("&", "_"),
                        }
                        if any(b in exact_names for b in reserved_bases):
                            continue

                    item_norm = normalize_text(item)
                    matches = []
                    for f in files:
                        base = os.path.splitext(f)[0]
                        if base in reserved_bases:
                            continue

                        # allow -XX stripped filename to match
                        if (
                            normalize_text(base) == item_norm or
                            normalize_text(dash2_re.sub("", base)) == item_norm
                        ):
                            matches.append(f)

                    if len(matches) != 1:
                        continue

                    src = matches[0]
                    ext = os.path.splitext(src)[1]

                    # Determine destination naming correctly:
                    # - Titles are only used to FIND the source image
                    # - LaunchBox destination names for screenshots/covers must be ROM-stem based
                    if is_launchbox:
                        rom_stem = None
                        # primary: lookup via normalized title in title_to_stem keyed by LAUNCHBOX_PLATFORMS key
                        rom_stem = title_to_stem.get(platform, {}).get(item_norm)
                        if not rom_stem:
                            rom_stem = title_to_stem.get(platform, {}).get(item)
                        if not rom_stem:
                            # fallback: scan lb_title_map entries for platform
                            lbmap = lb_title_map.get(platform, {})
                            for s, t in lbmap.items():
                                if normalize_text(t) == item_norm and s in rom_stems:
                                    rom_stem = s
                                    break
                        if not rom_stem:
                            # cannot determine rom stem → skip this item
                            continue

                        dst = make_launchbox_image_name(platform, rom_stem, ext)
                    else:
                        # non-launchbox targets use rom stem when pool is rom_stems,
                        # otherwise fall back to a sanitized title-based filename
                        if item in rom_stems:
                            base_name = item
                        else:
                            base_name = item.replace("&", "_")
                        dst = base_name + ext

                    if not dst:
                        continue
                    if src == dst:
                        continue

                    planned_jobs.append(
                        (os.path.join(img_dir, src), os.path.join(img_dir, dst))
                    )

                    if not platform_printed:
                        print(f"[PLATFORM] {platform}")
                        platform_printed = True

                    print(f"   [PATH] {img_dir}")
                    print(f"      [RENAME] {src} -> {dst}")
                    total += 1

    if total == 0:
        print("Planned renames: 0")
        return

    print(f"\nPlanned renames: {total}")
    resp = input("Apply these changes? [Y/N]: ").strip().lower()
    if resp not in ("y", "yes"):
        print("Cancelled.")
        return

    for src, dst in planned_jobs:
        if not os.path.exists(dst):
            os.rename(src, dst)

    print(f"Applied {len(planned_jobs)} renames.")

# ---------- Covers ----------

def cmd_sync_covers():
    print(f"\n{Fore.LIGHTRED_EX}[WARNING]{Style.RESET_ALL} This will overwrite all images in the target folder.\n")

    options = [
        ("LaunchBox", LAUNCHBOX_IMG_DIR),
        ("RetroArch", RETROARCH_IMG_DIR),
        ("Dolphin", DOLPHIN_IMG_DIR),
        ("PCSX2", PCSX2_IMG_DIR),
        ("Exit", None),
    ]

    def incompatible(src, tgt):
        if src == tgt:
            return True
        if src == "PCSX2" and tgt == "Dolphin":
            return True
        if src == "Dolphin" and tgt == "PCSX2":
            return True
        return False

    def system_platforms(src):
        if src == "PCSX2":
            return SYSTEMS["PS2"]["platforms"]
        if src == "Dolphin":
            return SYSTEMS["GC"]["platforms"] + SYSTEMS["WII"]["platforms"]
        return []

    def path_status(path):
        return status_ok() if path and os.path.isdir(path) else status_xx()

    def dim_status_plain(path):
        return "[ OK ]" if path and os.path.isdir(path) else "[ XX ]"

    # =========================================================
    # SOURCE SELECTION
    # =========================================================
    print("Select source of cover files")

    for i, (name, root) in enumerate(options, 1):
        print(f"{i}) {name:<12}")
        if root:
            print(f"     {path_status(root)} {root}")

    while True:
        try:
            c = int(input("\nSelect option: ").strip())
            if 1 <= c <= len(options):
                src_name, src_root = options[c - 1]
                if not src_root:
                    return
                break
        except:
            pass
        print("Invalid selection.")

    # =========================================================
    # TARGET SELECTION
    # =========================================================
    print("\nSelect target folder")

    plats = system_platforms(src_name)

    for i, (name, root) in enumerate(options, 1):
        disabled = incompatible(src_name, name)
        prefix = Fore.LIGHTBLACK_EX if disabled else ""
        suffix = Style.RESET_ALL if disabled else ""

        print(prefix + f"{i}) {name:<12}")

        if not root:
            if disabled:
                print(suffix, end="")
            continue

        # Expanded platform targets (Dolphin / PCSX2 → LB / RA)
        if src_name in ("PCSX2", "Dolphin") and name in ("LaunchBox", "RetroArch"):
            subdir = (
                RETROARCH_COVER_SUBDIR
                if name == "RetroArch"
                else LAUNCHBOX_COVER_SUBDIR
            )

            for p in plats:
                full = os.path.join(root, p, subdir)
                if disabled:
                    print(prefix + "     " + dim_status_plain(full) + " " + full)
                else:
                    print("     " + path_status(full) + " " + full)

        # Normal single-path target
        else:
            if disabled:
                print(prefix + "     " + dim_status_plain(root) + " " + root)
            else:
                print("     " + path_status(root) + " " + root)

        if disabled:
            print(suffix, end="")

    while True:
        try:
            c = int(input("\nSelect option: ").strip())
            if 1 <= c <= len(options):
                tgt_name, tgt_root = options[c - 1]
                if incompatible(src_name, tgt_name):
                    print("Invalid target for selected source.")
                    continue
                if not tgt_root:
                    return
                break
        except:
            pass
        print("Invalid selection.")

    sync_covers(src_name, src_root, tgt_name, tgt_root)


# ---------- Gameplay ----------

def cmd_sync_screenshots():
    global SYNC_BOTH_ACTIVE

    print(f"\n{Fore.LIGHTRED_EX}[WARNING]{Style.RESET_ALL} This will overwrite all images in the target folder. \nAlways uses the most recent screenshot for each game.\n")

    # =========================================================
    # SOURCE SELECTION
    # =========================================================
    sources = [
        ("LaunchBox", "LB"),
        ("RetroArch", "RA"),
        ("All screenshots", "ALL"),
        ("RetroArch screenshots", "RA_RAW"),
        ("Dolphin screenshots", "DOLPHIN"),
        ("PCSX2 screenshots", "PCSX2"),
        ("Exit", None),
    ]

    print("Select source of gameplay images:")
    for i, (name, key) in enumerate(sources, 1):
        print(f"{i}) {name}")
        if key == "LB":
            print(f"     {status_ok()} {LAUNCHBOX_IMG_DIR}\\<platform>\\{LAUNCHBOX_SCREEN_SUBDIR}")
        elif key == "RA":
            print(f"     {status_ok()} {RETROARCH_IMG_DIR}\\<platform>\\{RETROARCH_SCREEN_SUBDIR}")
        elif key == "ALL":
            print(f"     {status_ok()} {RETROARCH_SCREEN_DIR}\\<platform>")
            print(f"     {status_ok()} {DOLPHIN_SCREEN_DIR}")
            print(f"     {status_ok()} {PCSX2_SCREEN_DIR}")
        elif key == "RA_RAW":
            print(f"     {status_ok()} {RETROARCH_SCREEN_DIR}\\<platform>")
        elif key == "DOLPHIN":
            print(f"     {status_ok()} {DOLPHIN_SCREEN_DIR}")
        elif key == "PCSX2":
            print(f"     {status_ok()} {PCSX2_SCREEN_DIR}")

    while True:
        raw = input("\nSelect option: ").strip()
        try:
            c = int(raw)
        except:
            print("Invalid selection.")
            continue

        if not (1 <= c <= len(sources)):
            print("Invalid selection.")
            continue

        _, src_key = sources[c - 1]
        if src_key is None:
            return
        break

    # =========================================================
    # TARGET SELECTION
    # =========================================================
    print("\nSelect target folder")

    if src_key == "LB":
        allowed = {2, 7}
    elif src_key == "RA":
        allowed = {1, 7}
    else:
        allowed = {1, 2, 3, 7}

    def target_disabled(code):
        return code not in allowed

    def print_target(code, name):
        disabled = target_disabled(code)

        if disabled:
            prefix = Fore.LIGHTBLACK_EX
            suffix = Style.RESET_ALL
            status = "[ -- ]"
        else:
            prefix = ""
            suffix = ""
            status = status_ok()

        print(prefix + f"{code}) {name}")

        # -----------------------------------------------------
        # Only show concrete platform paths for Dolphin / PCSX2
        # -----------------------------------------------------
        platforms = None
        show_generic = True

        if src_key == "PCSX2":
            platforms = SYSTEMS["PS2"]["platforms"]
            show_generic = False

        elif src_key == "DOLPHIN":
            platforms = SYSTEMS["GC"]["platforms"] + SYSTEMS["WII"]["platforms"]
            show_generic = False

        if name in ("LaunchBox", "Both"):
            if platforms and not show_generic:
                for p in platforms:
                    path = os.path.join(
                        LAUNCHBOX_IMG_DIR, p, LAUNCHBOX_SCREEN_SUBDIR
                    )
                    print(prefix + f"     {status} {path}")
            else:
                print(prefix + f"     {status} {LAUNCHBOX_IMG_DIR}\\<platform>\\{LAUNCHBOX_SCREEN_SUBDIR}")

        if name in ("RetroArch", "Both"):
            if platforms and not show_generic:
                for p in platforms:
                    path = os.path.join(
                        RETROARCH_IMG_DIR, p, RETROARCH_SCREEN_SUBDIR
                    )
                    print(prefix + f"     {status} {path}")
            else:
                print(prefix + f"     {status} {RETROARCH_IMG_DIR}\\<platform>\\{RETROARCH_SCREEN_SUBDIR}")

        if disabled:
            print(suffix, end="")

    print_target(1, "LaunchBox")
    print_target(2, "RetroArch")
    print_target(3, "Both")
    print("7) Exit")

    while True:
        raw = input("\nSelect option: ").strip()
        try:
            c = int(raw)
        except:
            print("Invalid selection.")
            continue

        if c == 7:
            return

        if c not in (1, 2, 3):
            print("Invalid selection.")
            continue

        if target_disabled(c):
            print("Invalid target for selected source.")
            continue

        if c == 1:
            sync_screenshots(1, src_key)
        elif c == 2:
            sync_screenshots(2, src_key)
        elif c == 3:
            SYNC_BOTH_ACTIVE = True
            sync_screenshots(1, src_key)
            print()
            sync_screenshots(2, src_key)
            SYNC_BOTH_ACTIVE = False

        return

# ---------- Modify ----------

def apply_rename_jobs(rename_jobs):
    for rom_dir, old_file, new_file in rename_jobs:
        # ----------------------------------
        # Resolve platform robustly
        # ----------------------------------
        platform = None
        cur = rom_dir

        # Walk upwards until we find a known platform folder
        while cur and cur != os.path.dirname(cur):
            name = os.path.basename(cur)
            if name in PLATFORM_TO_SYSTEM:
                platform = name
                break
            cur = os.path.dirname(cur)

        system = PLATFORM_TO_SYSTEM.get(platform) if platform else None

        # ----------------------------------
        # ROM files
        # ----------------------------------
        plan = build_rom_rename_plan(rom_dir, old_file, new_file)
        apply_renames(plan)

        # ----------------------------------
        # RetroArch saves & logs (scoped helpers)
        # ----------------------------------
        rename_save_files(old_file, new_file, platform, system)
        rename_retroarch_logs(old_file, new_file, platform, system)

        # ----------------------------------
        # Platform images (thumbnails / screenshots)
        # ----------------------------------
        if platform:
            rename_platform_images(platform, old_file, new_file)

        # ----------------------------------
        # CUE → BIN handling
        # ----------------------------------
        if old_file.lower().endswith(".cue"):
            rewrite_cue_file(
                os.path.join(rom_dir, new_file),
                cue_base(old_file),
                cue_base(new_file)
            )

        # ----------------------------------
        # RetroArch playlists (.lpl)
        # ----------------------------------
        if os.path.isdir(RETROARCH_PLAYLIST_DIR):
            for dirpath, _, files in os.walk(RETROARCH_PLAYLIST_DIR):
                for fname in files:
                    if fname.lower().endswith(".lpl"):
                        path = os.path.join(dirpath, fname)
                        replace_stem_in_file(path, old_file, new_file)

        # ----------------------------------
        # LaunchBox XML (ApplicationPath ONLY)
        # ----------------------------------
        if os.path.isdir(LAUNCHBOX_DATA_DIR):
            for dirpath, _, files in os.walk(LAUNCHBOX_DATA_DIR):
                for fname in files:
                    if not fname.lower().endswith(".xml"):
                        continue

                    path = os.path.join(dirpath, fname)
                    try:
                        tree = ET.parse(path)
                        root = tree.getroot()
                    except:
                        continue

                    changed = False

                    for g in root.findall("Game"):
                        app = g.findtext("ApplicationPath", "")
                        if not app:
                            continue

                        if os.path.basename(app) != old_file:
                            continue

                        g.find("ApplicationPath").text = os.path.join(
                            os.path.dirname(app),
                            new_file
                        )
                        changed = True

                    if changed:
                        indent_xml(root)
                        tree.write(path, encoding="utf-8", xml_declaration=True)

        # ----------------------------------
        # RetroArch core configs (stem-based)
        # ----------------------------------
        if system:
            oldStem = old_file.rsplit(".", 1)[0]
            newStem = new_file.rsplit(".", 1)[0]

            for core in SYSTEM_TO_CORES.get(system, []):
                core_cfg_dir = os.path.join(RETROARCH_CFG_DIR, core)
                if os.path.isdir(core_cfg_dir):
                    replace_stem_in_tree(core_cfg_dir, oldStem, newStem)

def is_disc_tag_removed(old_file, new_file):
    """
    Return True if old_file has a disc tag and new_file does not.
    """
    def has_disc(name):
        return re.search(r"\b(disc|disk|cd)\s*\d+\b", name, re.I) is not None

    return has_disc(old_file) and not has_disc(new_file)

def cmd_modify(arg=None):
    if arg:
        return
        
    print("Paste OLD rows from playtime_export.txt. Finish with an empty line.")
    old_lines = []
    while True:
        line = input()
        if not line.strip():
            break
        old_lines.append(line.strip())

    print("\nPaste NEW edited rows.")
    new_lines = []
    while len(new_lines) < len(old_lines):
        line = input()
        if not line.strip():
            continue
        new_lines.append(line.strip())

    local_rows = load_local()
    play_rows = load_playtime_export()

    try:
        replacements_local, replacements_play, rename_jobs, time_jobs = \
            build_modify_plans(old_lines, new_lines, local_rows, play_rows)
    except Exception as e:
        print(e)
        return

    ext_changes = []
    for _, old_file, new_file in rename_jobs:
        if os.path.splitext(old_file)[1].lower() != os.path.splitext(new_file)[1].lower():
            ext_changes.append((old_file, new_file))

    disc_removals = []
    for _, old_file, new_file in rename_jobs:
        if is_disc_tag_removed(old_file, new_file):
            disc_removals.append((old_file, new_file))

    if disc_removals:
        print(f"\n{Fore.LIGHTRED_EX}[WARNING]{Style.RESET_ALL}")
        for o, n in disc_removals:
            print(f"  {o} → {n}")
        resp = input(
            "\nYou are about to remove a disc # tag. "
            "Are you sure you want to continue? Y/N: "
        ).strip().lower()
        if resp != "y":
            print("Modify cancelled.")
            return

    if ext_changes:
        print("\nYou are about to change the file extension of some files:")
        for o, n in ext_changes:
            print(f"  {o} → {n}")
        resp = input("\nAre you sure you want to continue? Y/N: ").strip().lower()
        if resp != "y":
            print("Modify cancelled.")
            return

    try:
        # Backup saves only (never ROMs)
        backup_tree_once(os.path.join(RETROARCH_DIR, "saves"))

        apply_rename_jobs(rename_jobs)

        for platform, gameid, filename, seconds, lastplayed in time_jobs:
            write_retroarch_time(filename, seconds, lastplayed)
            write_launchbox_time(platform, gameid, filename, seconds, lastplayed)

            system = PLATFORM_TO_SYSTEM.get(platform)

            if system in ("GC", "WII"):
                write_dolphin_time(gameid, seconds)

            if system == "PS2":
                write_pcsx2_time(gameid, seconds, lastplayed)

        replace_lines_in_file(LOCAL_DB, replacements_local)
        replace_lines_in_file(PLAYTIME_EXPORT, replacements_play)

        idx = next_history_index()
        for o, n in zip(old_lines, new_lines):
            if o != n:
                write_history(HISTORY, o, n, idx)
                idx += 1

        print(f"Modify complete: {len(old_lines)} entries updated")

    except Exception as e:
        print("ERROR:", e)

# ---------- Revert ----------

def cmd_revert(arg=None):
    if not arg:
        print("Usage: revert <number>")
        return

    try:
        target = int(arg)
    except:
        print("Usage: revert <number>")
        return

    if not os.path.exists(HISTORY):
        print("No history file found.")
        return

    with open(HISTORY, "r", encoding="utf-8") as f:
        lines = [x.rstrip("\n") for x in f]

    if target < 1 or target > len(lines):
        print("Invalid history number.")
        return

    entry = lines[target - 1]
    idx, rest = entry.split(".", 1)
    old, new = [x.strip() for x in rest.split("→", 1)]

    print("Reverting:")
    print(new)
    print("→")
    print(old)

    run_modify_direct([new], [old])

    lines[target - 1] = f"{idx}. {new} → {old}"

    with open(HISTORY, "w", encoding="utf-8") as f:
        for l in lines:
            f.write(l + "\n")

    print("Revert complete.")

# ============================================================
# ======================= BACKUP =============================
# ============================================================

BACKUP_ROOT = "backup"
BACKUP_WINDOW = 60 * 60  # 60 minutes


def _parse_backup_time(name):
    try:
        stamp = name.replace("backup_", "")
        return time.mktime(time.strptime(stamp, "%Y_%m_%d-%H_%M"))
    except:
        return None


def get_active_backup_dir():
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    now = time.time()

    best_time = None
    best_path = None

    for name in os.listdir(BACKUP_ROOT):
        if not name.startswith("backup_"):
            continue
        path = os.path.join(BACKUP_ROOT, name)
        if not os.path.isdir(path):
            continue

        t = _parse_backup_time(name)
        if t is None:
            continue

        if best_time is None or t > best_time:
            best_time = t
            best_path = path

    # If last backup is recent enough, reuse it
    if best_time and now - best_time <= BACKUP_WINDOW:
        return best_path

    # Otherwise create a new one
    stamp = time.strftime("%Y_%m_%d-%H_%M", time.localtime(now))
    path = os.path.join(BACKUP_ROOT, f"backup_{stamp}")
    os.makedirs(path, exist_ok=True)
    return path

def backup_file_once(src):
    if not os.path.exists(src):
        return

    root = get_active_backup_dir()

    drive, path = os.path.splitdrive(os.path.abspath(src))
    drive = drive.replace(":", "")
    rel = path.lstrip("\\/")

    dst = os.path.join(root, drive, rel)

    if os.path.exists(dst):
        return

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)

def backup_tree_once(src_dir):
    if not os.path.isdir(src_dir):
        return

    root = get_active_backup_dir()

    drive, path = os.path.splitdrive(os.path.abspath(src_dir))
    drive = drive.replace(":", "")
    rel = path.lstrip("\\/")

    dst = os.path.join(root, drive, rel)

    if os.path.exists(dst):
        return

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copytree(src_dir, dst)

# ---------- Full manual backup ----------

def cmd_backup():
    root = get_active_backup_dir()
    print("Creating full backup in:", root)

    # RetroArch metadata (never saves)
    backup_tree_once(RETROARCH_PLAYLIST_DIR)
    backup_tree_once(os.path.join(RETROARCH_PLAYLIST_DIR, "logs"))

    # Dolphin / PCSX2
    backup_file_once(DOLPHIN_PLAYTIME)
    backup_file_once(PCSX2_PLAYTIME)

    # LaunchBox database
    backup_tree_once(LAUNCHBOX_DATA_DIR)
    backup_retroarch_labels()

    print("Backup complete.")

# ============================================================
# ========================= UI ==============================
# ============================================================

COMMANDS = {
    "check paths": cmd_check_paths,
    "rescan": cmd_rescan,
    "backup": cmd_backup,
    "modify": cmd_modify,
    "history": show_history,
    "revert": cmd_revert,
    "link pictures": cmd_link_pictures,
    "sync covers": cmd_sync_covers,
    "sync screens": cmd_sync_screenshots,
    "sync playtime": cmd_sync,
    "change labels": cmd_change_labels,
    "help": lambda: print("""
Commands:

  help             - Show this screen  
  check paths      - Verify all emulator and platform paths
  rescan           - Refresh game library
  backup           - Snapshot all emulator + LaunchBox data

  modify           - Batch edit playtime | last played | filename
  history          - Show modification log
  revert <n>       - Undo or redo a modification (check history)

  link pictures    - Rename and link existing images to ROMs
  sync covers      - Sync game covers between platforms (link pictures first)
  sync screens     - Sync gameplay pictures between Retroarch/Launchbox or use own screenshots
  sync playtime    - Sync playtime from emulators into LaunchBox
  change labels    - Backup and modify Retroarch labels

  exit             - Quit
""")
}

def main():
    # --------------------------------------------------
    # Ensure local_games.txt exists (scanner only)
    # --------------------------------------------------
    if SCANNER_EXEC and not os.path.exists(LOCAL_DB):
        print("local_games.txt not found. Running game scanner...")
        run_scanner_process()

    # --------------------------------------------------
    # Always refresh playtime on startup
    # --------------------------------------------------
    print("Importing playtime...")
    cmd_export_playtime()

    print("\nGameIndex ready. Type 'help' to see available commands.")

    while True:
        raw = input("> ").strip()
        if not raw:
            continue

        low = raw.lower()

        if low in ("exit", "quit"):
            break

        match = None
        for name in sorted(COMMANDS.keys(), key=len, reverse=True):
            if low == name or low.startswith(name + " "):
                match = name
                break

        if not match:
            print("Unknown command")
            continue

        arg = raw[len(match):].strip()
        if arg:
            COMMANDS[match](arg)
        else:
            COMMANDS[match]()

if __name__ == "__main__":
    main()