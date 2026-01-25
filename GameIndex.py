import os
import re
import sys
import time
import json
import zlib
import struct
import string
import shutil
import datetime
import subprocess
import configparser
import xml.etree.ElementTree as ET
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
# =============== VERIFY / REPAIR CORE PATHS =================
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
# =============== PROGRAM ROOT AUTO-DETECTION ===============
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
# =============== EARLY PATH VERIFICATION ====================
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

LOCAL_DB         = "local_games.txt"
HISTORY          = "history.txt"
PLAYTIME_EXPORT  = "playtime_export.txt"
SCANNER_EXEC, SCANNER_SCRIPT = resolve_scanner()

PRINT_ALL = bool(SETUP.get("PRINT_ALL", False))

RETROARCH_DIR         = SETUP["RETROARCH_DIR"]
RETROARCH_CFG_DIR     = SETUP["RETROARCH_CFG_DIR"]
RETROARCH_PLAYLIST_DIR = SETUP["RETROARCH_PLAYLIST_DIR"]
RETROARCH_LOG_DIR     = SETUP["RETROARCH_LOG_DIR"]

DOLPHIN_DIR      = SETUP["DOLPHIN_DIR"]
DOLPHIN_PLAYTIME = SETUP["DOLPHIN_PLAYTIME"]

PCSX2_DIR        = SETUP["PCSX2_DIR"]
PCSX2_PLAYTIME   = SETUP["PCSX2_PLAYTIME"]

LAUNCHBOX_DATA_DIR   = SETUP["LAUNCHBOX_DATA_DIR"]
LAUNCHBOX_PLATFORMS  = SETUP["LAUNCHBOX_PLATFORMS"]
GAMES_DIR           = SETUP["GAMES_DIR"]


# ============================================================
# ========================= SYSTEMS ==========================
# ============================================================

SYSTEMS = {
    "ARCADE": {
        "platforms": [
            "FBNeo - Arcade Games",
        ],
        "cores": [
            "FinalBurn Neo",
            "MAME",
        ],
    },

    "GW": {
        "platforms": [
            "Handheld Electronic Game",
        ],
        "cores": [
            "MAME",
        ],
    },

    "GB": {
        "platforms": [
            "Nintendo - Game Boy",
        ],
        "cores": [
            "Gambatte",
            "SameBoy",
            "Gearboy",
            "TGB Dual",
        ],
    },

    "GBC": {
        "platforms": [
            "Nintendo - Game Boy Color",
        ],
        "cores": [
            "Gambatte",
            "SameBoy",
            "Gearboy",
            "TGB Dual",
        ],
    },

    "GBC": {
        "platforms": [
            "Nintendo - Game Boy Color",
        ],
        "cores": [
            "Gambatte",
            "SameBoy",
            "Gearboy",
            "TGB Dual",
        ],
    },

    "GBA": {
        "platforms": [
            "Nintendo - Game Boy Advance",
        ],
        "cores": [
            "mGBA",
            "gpSP",
            "VBA Next",
            "VBA-M",
        ],
    },

    "NDS": {
        "platforms": [
            "Nintendo - Nintendo DS",
        ],
        "cores": [
            "melonDS",
            "DeSmuME",
            "DeSmuME 2015",
        ],
    },

    "3DS": {
        "platforms": [
            "Nintendo - Nintendo 3DS",
        ],
        "cores": [
            "Citra",
        ],
    },

    "NES": {
        "platforms": [
            "Nintendo - Nintendo Entertainment System",
        ],
        "cores": [
            "Nestopia",
            "FCEUmm",
            "QuickNES",
        ],
    },

    "SNES": {
        "platforms": [
            "Nintendo - Super Nintendo Entertainment System",
        ],
        "cores": [
            "Snes9x",
            "Snes9x 2005",
            "Snes9x 2010",
            "bsnes",
            "bsnes HD",
        ],
    },

    "N64": {
        "platforms": [
            "Nintendo - Nintendo 64",
        ],
        "cores": [
            "Mupen64Plus-Next",
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
        ],
    },

    "WII": {
        "platforms": [
            "Nintendo - Wii",
        ],
        "cores": [
            "Dolphin",
        ],
    },

    "MasterSys": {
        "platforms": [
            "Sega - Master System - Mark III",
        ],
        "cores": [
            "Genesis Plus GX",
            "SMS Plus GX",
        ],
    },

    "GameGear": {
        "platforms": [
            "Sega - Game Gear",
        ],
        "cores": [
            "Genesis Plus GX",
            "SMS Plus GX",
        ],
    },

    "Genesis": {
        "platforms": [
            "Sega - Mega Drive - Genesis",
        ],
        "cores": [
            "Genesis Plus GX",
            "PicoDrive",
        ],
    },

    "SegaCD": {
        "platforms": [
            "Sega - Mega-CD - Sega CD",
        ],
        "cores": [
            "Genesis Plus GX",
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
            "Kronos",
            "YabaSanshiro",
        ],
    },

    "Dreamcast": {
        "platforms": [
            "Sega - Dreamcast",
        ],
        "cores": [
            "Flycast",
            "Flycast GLES2",
            "Redream",
        ],
    },

    "PSX": {
        "platforms": [
            "Sony - PlayStation",
        ],
        "cores": [
            "Beetle PSX",
            "Beetle PSX HW",
            "SwanStation",
            "PCSX-ReARMed",
        ],
    },

    "PS2": {
        "platforms": [
            "Sony - PlayStation 2",
        ],
        "cores": [
            "PCSX2",
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
# ===================== PLAYTIME READERS =====================
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

    allowed_roots = [logs_root]

    for platform, system in PLATFORM_TO_SYSTEM.items():
        plat_dir = os.path.join(logs_root, platform)
        if os.path.isdir(plat_dir):
            allowed_roots.append(plat_dir)

        cores = SYSTEM_TO_CORES.get(system, [])
        for core in cores:
            core_dir = os.path.join(logs_root, core)
            if os.path.isdir(core_dir):
                allowed_roots.append(core_dir)

    for root in allowed_roots:
        for _, _, files in os.walk(root):
            for fname in files:
                if not fname.lower().endswith(".lrtl"):
                    continue

                path = os.path.join(root, fname)
                rom = os.path.splitext(fname)[0]

                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    runtime = data.get("runtime", "")
                    last = data.get("last_played", "")

                    # Parse H:MM:SS
                    seconds = 0
                    if runtime:
                        parts = runtime.split(":")
                        if len(parts) == 3:
                            h, m, s = map(int, parts)
                            seconds = h * 3600 + m * 60 + s

                    out[rom] = {
                        "seconds": seconds,
                        "last_played": last
                    }

                except Exception:
                    pass

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
            gameid = g.findtext("Version", "").strip()
            last = g.findtext("LastPlayedDate", "").strip()
            if gameid and last:
                data[gameid] = normalize_launchbox_time(last)

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
    NOT by GameID. GameID is ignored by design.
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

def detect_newline(path):
    with open(path, "rb") as f:
        data = f.read()
    return b"\r\n" if b"\r\n" in data else b"\n"

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

    newline = detect_newline(PCSX2_PLAYTIME)
    new_line = format_pcsx2_line(gameid, seconds, lastplayed).encode("ascii")

    with open(PCSX2_PLAYTIME, "rb") as f:
        raw = f.read()

    lines = raw.split(b"\r\n" if newline == b"\r\n" else b"\n")

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

# ============================================================
# ===================== RENAME ENGINE ========================
# ============================================================

STEM_RE = re.compile(r"^(.*\.)[^.]+$")
BIN_TRACK_RE = re.compile(r"^(.*?)(\s+\(Track\s+\d+\))\.bin$", re.I)
CUE_RE = re.compile(r"^(.*)\.cue$", re.I)


# ---------- Stem helpers ----------

def split_stem(filename):
    base, ext = filename.rsplit(".", 1)
    return base + ".", ext.lower()

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

    oldBase = old_filename.rsplit(".", 1)[0]
    newBase = new_filename.rsplit(".", 1)[0]

    oldExt = old_filename.rsplit(".", 1)[1].lower()
    newExt = new_filename.rsplit(".", 1)[1].lower()

    oldCue = oldExt == "cue"

    if oldCue:
        oldCueBase = cue_base(old_filename)
        newCueBase = cue_base(new_filename)

    for fname in os.listdir(rom_dir):
        src = os.path.join(rom_dir, fname)

        # Exact file rename (handles extension-only changes)
        if fname == old_filename:
            plan.append((src, os.path.join(rom_dir, new_filename)))
            continue

        # Normal ROM + multi-dot save files
        if fname.startswith(oldBase + "."):
            newName = newBase + fname[len(oldBase):]
            if newName != fname:
                plan.append((src, os.path.join(rom_dir, newName)))

        # Cue track bins
        if oldCue:
            base, track = bin_base(fname)
            if base == oldCueBase:
                newName = newCueBase + track + ".bin"
                if newName != fname:
                    plan.append((src, os.path.join(rom_dir, newName)))

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
def rename_save_files(old_file, new_file):
    oldbase = os.path.splitext(old_file)[0]
    newbase = os.path.splitext(new_file)[0]

    saves_root = os.path.join(RETROARCH_DIR, "saves")

    # Determine platform from ROM path context
    # Caller guarantees correct working directory
    platform = None
    for plat in PLATFORMS_ORDERED:
        plat_dir = os.path.join(GAMES_DIR, plat)
        if os.path.isdir(plat_dir):
            platform = plat
            break

    allowed_roots = [saves_root]

    if platform:
        system = PLATFORM_TO_SYSTEM.get(platform)
        if system:
            cores = SYSTEM_TO_CORES.get(system, [])

            # Platform-named save folder
            plat_path = os.path.join(saves_root, platform)
            if os.path.isdir(plat_path):
                allowed_roots.append(plat_path)

            # Core-named save folders
            for core in cores:
                core_path = os.path.join(saves_root, core)
                if os.path.isdir(core_path):
                    allowed_roots.append(core_path)

    for root in allowed_roots:
        for dirpath, _, files in os.walk(root):
            for fname in files:
                if fname.startswith(oldbase + "."):
                    src = os.path.join(dirpath, fname)
                    dst = os.path.join(dirpath, newbase + fname[len(oldbase):])
                    if not os.path.exists(dst):
                        os.rename(src, dst)

# ---------- Log files ----------
def rename_retroarch_logs(old_file, new_file):
    oldbase = os.path.splitext(old_file)[0]
    newbase = os.path.splitext(new_file)[0]

    if not os.path.isdir(RETROARCH_LOG_DIR):
        return

    for dirpath, _, files in os.walk(RETROARCH_LOG_DIR):
        for fname in files:
            if fname == oldbase + ".lrtl":
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

def replace_stem_in_tree(root, oldStem, newStem, extensions=None, *_):
    for dirpath, _, files in os.walk(root):
        for name in files:
            if extensions and not name.lower().endswith(extensions):
                continue
            replace_stem_in_file(os.path.join(dirpath, name), oldStem, newStem)

# ============================================================
# ===================== MODIFY PLANNER ======================
# ============================================================

def parse_seconds(value):
    """
    Parse playtime into seconds.

    Accepted formats:
      - 123456
      - 123456s
      - 1234h
      - 1234h 56m
      - 1234h 56m 07s
      - 1.234h 56m 07s   (thousands-separated hours)
    """
    if not value:
        return 0

    v = value.strip().lower()

    # ---------- pure seconds ----------
    if v.endswith("s") and v[:-1].isdigit():
        return int(v[:-1])

    if v.isdigit():
        return int(v)

    # ---------- h / m / s format ----------
    h = m = s = 0

    # hours (allow thousands separators)
    mh = re.search(r'([\d\.]+)\s*h', v)
    if mh:
        try:
            h = int(mh.group(1).replace(".", ""))
        except:
            h = 0

    mm = re.search(r'(\d+)\s*m', v)
    if mm:
        try:
            m = int(mm.group(1))
        except:
            m = 0

    ms = re.search(r'(\d+)\s*s', v)
    if ms:
        try:
            s = int(ms.group(1))
        except:
            s = 0

    if h or m or s:
        return h * 3600 + m * 60 + s

    return 0

def build_modify_plans(old_lines, new_lines, local_rows, play_rows):
    def parse(row):
        parts = [x.strip() for x in row.split("|")]
        if len(parts) != 6:
            raise ValueError("Invalid row: " + row)
        return parts

    local_map = {}
    for r in local_rows:
        p, t, g, f = [x.strip() for x in r.split("|")]
        local_map[(p, t, g, f)] = r

    play_map = {}
    for r in play_rows:
        p, t, g, pt, lp, f = parse(r)
        play_map[(p, t, g, f)] = r

    replacements_local = {}
    replacements_play  = {}
    rename_jobs = []
    time_jobs = []   # (platform, gameid, newfile, seconds, lastplayed)

    for old, new in zip(old_lines, new_lines):
        op, ot, og, opt, olp, of = parse(old)
        np, nt, ng, npt, nlp, nf = parse(new)

        # Identity (platform / title / gameid) must match
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
            rename_jobs.append((rom_dir, of, nf))

        # --------------------------------------------------
        # Playtime propagation
        # --------------------------------------------------
        if (npt or nlp) or (opt != npt or olp != nlp):
            seconds = parse_seconds(npt)
            time_jobs.append((op, og, nf, seconds, nlp))

    return replacements_local, replacements_play, rename_jobs, time_jobs

def run_modify_direct(old_lines, new_lines):
    local_rows = load_local()
    play_rows = load_playtime_export()

    replacements_local, replacements_play, rename_jobs, time_jobs = \
        build_modify_plans(old_lines, new_lines, local_rows, play_rows)

    # ---- Renames ----
    apply_rename_jobs(rename_jobs)

    # ---- Databases ----
    replace_lines_in_file(LOCAL_DB, replacements_local)
    replace_lines_in_file(PLAYTIME_EXPORT, replacements_play)

    # ---- Playtime ----
    for platform, gameid, filename, seconds, lastplayed in time_jobs:
        write_retroarch_time(filename, seconds, lastplayed)
        write_launchbox_time(platform, gameid, filename, seconds, lastplayed)

        system = PLATFORM_TO_SYSTEM.get(platform)

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


# ---------- Export ----------

def cmd_export_playtime():
    print("Loading playtime sources...")

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
    printed = []  # (row_color, row_plain)

    # Only used when PRINT_ALL is False
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

        # ---------- RetroArch ----------
        rom = os.path.splitext(os.path.basename(file))[0]
        if rom in ra:
            seconds = ra[rom].get("seconds", 0)
            lp = ra[rom].get("last_played", "")
            if lp:
                last_played = lp

        # ---------- PCSX2 ----------
        if system == "PS2" and game_id in pcsx2:
            seconds, lp = pcsx2[game_id]
            if lp:
                last_played = lp

        # ---------- Dolphin ----------
        if system in ("GC", "WII") and game_id in dolphin:
            seconds = dolphin[game_id]
            if game_id in lb:
                last_played = lb[game_id]

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

                # 1. fewer brackets wins
                if bracket_count > prev_brackets:
                    continue
                if bracket_count < prev_brackets:
                    pass
                else:
                    # 2. no codeword wins
                    if has_codeword and not prev_has_codeword:
                        continue
                    if not has_codeword and prev_has_codeword:
                        pass
                    else:
                        # 3. first wins
                        continue

            best[key] = (bracket_count, has_codeword, row_color, row_plain)

        else:
            printed.append((row_color, row_plain))

    # ---------- Minecraft ----------
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

            print(row_color)
            out.append(row_plain)

    # ---------- World of Warcraft (Retail) ----------
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

            print(row_color)
            out.append(row_plain)

    # ---------- World of Warcraft (Classic Era / Events) ----------
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

            print(row_color)
            out.append(row_plain)

    # ---------- World of Warcraft (Classic Progression) ----------
    if wow_classic:
        seconds, last_played = wow_classic

        if seconds >= 500 or PRINT_ALL:
            row_plain = (
                "PC - World of Warcraft | World of Warcraft Classic Progression | WOW-CLASSIC | "
                f"{format_playtime(seconds)} | {last_played} | WowClassic.exe"
            )

            row_color = (
                f"PC - World of Warcraft"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} World of Warcraft Classic Progression"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} WOW-CLASSIC"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} {format_playtime(seconds)}"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} {last_played}"
                f" {Fore.LIGHTBLACK_EX}|{Style.RESET_ALL} WowClassic.exe"
            )

            print(row_color)
            out.append(row_plain)


    # ---------- Emit results ----------
    if PRINT_ALL:
        for row_color, row_plain in printed:
            print(row_color)
            out.append(row_plain)
    else:
        for _, _, row_color, row_plain in best.values():
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

# ---------- Modify ----------

def apply_rename_jobs(rename_jobs):
    for rom_dir, old_file, new_file in rename_jobs:
        plan = build_rom_rename_plan(rom_dir, old_file, new_file)
        apply_renames(plan)
        rename_save_files(old_file, new_file)
        rename_retroarch_logs(old_file, new_file)

        if old_file.lower().endswith(".cue"):
            rewrite_cue_file(
                os.path.join(rom_dir, new_file),
                cue_base(old_file),
                cue_base(new_file)
            )

        replace_stem_in_tree(
            RETROARCH_PLAYLIST_DIR,
            old_file,
            new_file
        )

        replace_stem_in_tree(
            LAUNCHBOX_DATA_DIR,
            old_file,
            new_file,
            (".xml",)
        )

        oldStem, _ = split_stem(old_file)
        newStem, _ = split_stem(new_file)

        # ---- Scoped save files ----
        replace_stem_in_tree(
            os.path.join(RETROARCH_DIR, "saves"),
            oldStem,
            newStem
        )

        platform = os.path.basename(rom_dir)
        system = PLATFORM_TO_SYSTEM.get(platform)
        if not system:
            continue

        cores = SYSTEM_TO_CORES.get(system)
        if not cores:
            continue

        saves_root = os.path.join(RETROARCH_DIR, "saves")
        logs_root = os.path.join(RETROARCH_PLAYLIST_DIR, "logs")

        # Platform-named folders
        plat_save = os.path.join(saves_root, platform)
        plat_log = os.path.join(logs_root, platform)

        if os.path.isdir(plat_save):
            replace_stem_in_tree(plat_save, oldStem, newStem)

        if os.path.isdir(plat_log):
            replace_stem_in_tree(plat_log, oldStem, newStem)

        # Core-named folders
        for core in cores:
            core_save = os.path.join(saves_root, core)
            core_log = os.path.join(logs_root, core)

            if os.path.isdir(core_save):
                replace_stem_in_tree(core_save, oldStem, newStem)

            if os.path.isdir(core_log):
                replace_stem_in_tree(core_log, oldStem, newStem)

        # ---- Scoped RetroArch configs ----
        for core in cores:
            core_cfg_dir = os.path.join(RETROARCH_CFG_DIR, core)
            if not os.path.isdir(core_cfg_dir):
                continue

            replace_stem_in_tree(
                core_cfg_dir,
                oldStem,
                newStem
            )

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

    print("Backup complete.")

# ============================================================
# ========================= UI ==============================
# ============================================================

COMMANDS = {
    "check paths": cmd_check_paths,
    "rescan": cmd_rescan,
    "sync": cmd_sync,
    "modify": cmd_modify,
    "history": show_history,
    "revert": cmd_revert,
    "backup": cmd_backup,
    "help": lambda: print("""
Commands:

  help            - Show this screen  
  check paths     - Verify all emulator and platform paths
  rescan          - Refresh game library
  sync            - Sync playtime from emulators into LaunchBox
  modify          - Batch edit playtime | last played | filename
  history         - Show modification log
  revert <n>      - Undo or redo a modification (check history)
  backup          - Snapshot all emulator + LaunchBox data
  exit            - Quit
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