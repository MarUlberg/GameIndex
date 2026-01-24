import os
import re
import sys
import time
import zlib
import shlex
import struct
import string
import argparse
import subprocess
import configparser
from colorama import Fore, Style, init
init()

# ============================================================
# ========================== SETUP ===========================
# ============================================================

def resource_path(relative_path):
    """
    Get absolute path to resource, works for dev and for PyInstaller EXE
    """
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(__file__), relative_path)

BASE_DIR = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__)
os.chdir(BASE_DIR)

CONFIG_FILE = "specialconfig.txt" if os.path.exists("specialconfig.txt") else "config.txt"

PRINT_ALL = True # If True shows every game as it is scanned
SKIP_SCAN = False # If True skips ROM scan and moves on to GameID.py (For testing)
SKIP_DATABASE = False # If True matching GameID vs Database (For testing)
DEBUG = True # Shows source of info

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
# ========================== PATHS ===========================
# ============================================================

_setup = load_setup(CONFIG_FILE)

RETROARCH_DIR = _setup["RETROARCH_DIR"]
GAMES_DIR     = _setup["GAMES_DIR"]
DOLPHIN_TOOL = _setup["DOLPHIN_TOOL"]

# ============================================================
# ====================== SHARED HELPERS ======================
# ============================================================

def find_games(root, exts):
    """Recursively yield files matching extensions"""
    if not exts:
        return
    for d, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(exts):
                yield os.path.join(d, f)

def get_gameid_and_title_from_gameid_py(path, system, gameidkey):
    try:
        out = run_gameid(path, gameidkey[0])
        data = parse_gameid_output(out)
    except Exception:
        return None, None, None, None, None

    game_id = None
    title = None

    # --------------------------------------------------
    # Game ID extraction + normalization
    # --------------------------------------------------
    if system == "GC":
        # Wii / GameCube: 4-char ID + 2-char maker
        gid = data.get("cart_id") or data.get("game_id")
        maker = data.get("maker")
        if gid and maker:
            game_id = (gid + maker).upper()
        elif gid:
            game_id = gid.upper()

    else:
        game_id = data.get("serial") or data.get("game_id")
        if game_id:
            game_id = game_id.upper()

            # 🔑 Normalize Sega-family IDs HERE
            if system in ("Genesis", "SegaCD", "Saturn", "Dreamcast"):
                game_id = normalize_sega_id(game_id)

    # --------------------------------------------------
    # Title extraction + cleanup (fixed-width safe)
    # --------------------------------------------------
    title = data.get("title") or data.get("internal")

    if title:
        title = title.replace("\x00", "")
        title = re.sub(r"\s+", " ", title).strip()

        m = re.match(r"^(.*?),\s*(THE|A|AN)(.*)$", title, re.I)
        if m:
            base, art, rest = m.groups()
            title = f"{art.title()} {base}{rest}"
    else:
        title = None

    # --------------------------------------------------
    # Explicit source marking
    # --------------------------------------------------
    game_id_source = "gameid.py" if game_id else None
    title_source = "gameid.py" if title else None

    return game_id, game_id_source, title, title_source, data.get("crc")

# Case-insensitive, literal match
CODEWORDS = [
    "(patched)", "[patched]", "(hack)", "[hack]",
]

def split_filename(filename):

    name = filename

    # -----------------------------------------------
    # Remove codewords first (even if bracketed)
    # -----------------------------------------------
    lowered = name.lower()
    for cw in CODEWORDS:
        cw_l = cw.lower()
        if cw_l in lowered:
            name = re.sub(
                re.escape(cw),
                "",
                name,
                flags=re.I
            )
            lowered = name.lower()

    # -----------------------------------------------
    # Extract remaining [tags]
    # -----------------------------------------------
    tags = re.findall(r"\[[^\]]+\]", name)

    base = name
    for t in tags:
        base = base.replace(t, "")

    base = re.sub(r"\s+", " ", base).strip()
    return base, tags


def normalize_db_lookup_id(game_id, system):
    if not game_id:
        return None

    game_id = game_id.upper()

    if system in ("GB", "GBC"):
        if "-" in game_id:
            return game_id.split("-", 1)[1]
        return game_id

    if system == "NDS":
        if "-" in game_id:
            return game_id.split("-", 1)[1]
        return game_id

    return game_id

def clean_title(base):
    # Takes a filename with no [tags] and returns a clean title.
    title = os.path.splitext(base)[0]

    # Remove known dump suffixes
    title = re.sub(r"\.(standard|trimmed|encrypted|decrypted)$", "", title, flags=re.I)

    # Remove stacked extensions (.bin.cue, .iso.zip, etc)
    title = re.sub(
        r"\.(iso|bin|cue|chd|gcm|wbfs|zip|7z|gba|gbc|gb|nes|sfc|smc|z64|n64|v64)$",
        "",
        title,
        flags=re.I,
    )

    # Remove No-Intro prefixes
    title = re.sub(r"^\d{3,5}\s*-\s*", "", title)

    # Normalize trailing articles: "Sims, The" → "The Sims"
    # Supports English, French, and German
    m = re.match(r"^(.*?),\s*(THE|A|AN|LES|DIE)(.*)$", title, re.I)
    if m:
        base, art, rest = m.groups()
        title = f"{art.title()} {base}{rest}"

    # Normalize subtitle separator: " - " → ": "
    title = re.sub(r"\s+-\s+", ": ", title)

    # Remove parentheses (regions, revs, etc)
    title = re.sub(r"\s*\([^)]*\)", "", title)

    # Normalize whitespace
    title = re.sub(r"\s+", " ", title).strip()

    # Pokémon typography
    title = re.sub(r"\bPokemon\b", "Pokémon", title, flags=re.I)
    title = re.sub(r"\bPokémon\s*-\s*", "Pokémon: ", title)

    return title


def detect_sector_mode(cue):
    sector, offset = 2352, 24
    with open(cue, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            u = line.upper()
            if "MODE1/2048" in u:
                return 2048, 0
            if "MODE2/2352" in u:
                return 2352, 24
    return sector, offset
    

# ---------------------- CRC32 -------------------------------

def crc32_file(path, skip_header=0):
    crc = 0
    with open(path, "rb") as f:
        if skip_header:
            f.seek(skip_header)
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
    return f"{crc & 0xffffffff:08x}"   # lowercase hex

# ============================================================
# ======================== OVERRIDE ==========================
# ============================================================

def scan_override(filename):
    """
    Detect special override titles (e.g. CodeBreaker).
    Returns (gameid_title, game_id, gameid_source) or None.
    """
    cb = re.search(
        r"(code[\s._-]*breaker|codebreaker|cb)[\s._-]*(?:version|ver|v)?[\s._-]*(\d+(?:\.\d+)?)",
        filename,
        re.I
    )
    if cb:
        v = cb.group(2)
        return (
            f"CodeBreaker v{v}",
            f"CODE-BRK{v.replace('.', '')}",
        )

    return None

# ============================================================
# ================== GAMEID + FALLBACK CORE =================
# ============================================================

GAMEID_SCRIPT = resource_path("GameID.py")
SUPPORTED_GAMEID_EXTS = (".iso", ".cue", ".bin", ".gen", ".md", ".n64", ".z64", ".gba", ".gbc", ".gb", ".sfc", ".smc", ".nes")

# ---------- database.txt ----------
DB = {}

parser = configparser.ConfigParser(interpolation=None)

with open(resource_path("database.txt"), "r", encoding="utf-8") as f:
    lines = f.readlines()

# Skip non-data lines before first section header
while lines and not lines[0].lstrip().startswith("["):
    lines.pop(0)

parser.read_string("".join(lines))


for section in parser.sections():
    DB[section] = {}
    for gid, name in parser.items(section):
        gid = gid.upper()

        # GB / GBC store BOTH forms
        if section in ("Nintendo - Game Boy", "Nintendo - Game Boy Color"):
            if "-" in gid:
                DB[section][gid.split("-", 1)[1]] = name.strip()
            DB[section][gid] = name.strip()
        else:
            DB[section][gid] = name.strip()

def lookup_db_title(game_id, system):
    if not game_id:
        return None

    cfg = SYSTEMS.get(system)
    if not cfg:
        return None

    sections = cfg.get("db_sections")
    if not sections:
        return None

    gid = normalize_db_lookup_id(game_id, system)
    if not gid:
        return None

    gid = gid.upper()

    for section in sections:
        db = DB.get(section)
        if not db:
            continue

        value = db.get(gid)
        if not value:
            continue

        # ==================================================
        # Game & Watch: "alias | Title"
        # ==================================================
        if section == "Handheld Electronic Game":
            if "|" in value:
                return value.split("|", 1)[1].strip()
            return value.strip()

        return value.strip()

    return None

# ---------- run GameID.py ----------
def run_gameid(path, system):
    try:
        if getattr(sys, "frozen", False):
            # EXE mode → call GameID.exe
            cmd = [os.path.join(os.path.dirname(sys.executable), "GameID.exe")]
        else:
            # PY mode → call GameID.py
            cmd = [sys.executable, GAMEID_SCRIPT]

        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # EXACTLY what you typed manually
        stdin_data = f"{path}\n{system}\n"

        out, err = p.communicate(stdin_data)

        return out or ""

    except Exception:
        return ""


# ---------- Parse GameID.py output ----------
def parse_gameid_output(text):
    data = {
        "game_id": None,
        "gameid_source": None,
        "title": None,
        "title_source": None,
        "crc": None,
    }

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        lower = line.lower()

        # --------------------------------------------------
        # Serial / ID
        # --------------------------------------------------
        if lower.startswith(("id", "serial")):
            val = line.split(None, 1)[1].strip().upper()

            # Accept Nintendo-style short IDs
            if re.fullmatch(r"(AGB-)?[A-Z0-9]{4}", val):
                data["game_id"] = val
                data["gameid_source"] = "gameid.py"
                continue

            # Accept Sega IDs (raw, normalize later)
            if re.search(r"\b(T|MK|HDR)[\s\-_.]?\d{3,7}", val):
                data["game_id"] = val
                data["gameid_source"] = "gameid.py"
                continue

            continue

        # --------------------------------------------------
        # Manufacturer code (GB/GBC only, 4 chars)
        # --------------------------------------------------
        if lower.startswith("manufacturer_code"):
            parts = line.split(None, 1)
            if len(parts) == 2:
                val = parts[1].strip().upper()
                if re.fullmatch(r"[A-Z0-9]{4}", val):
                    data["game_id"] = val
                    data["gameid_source"] = "gameid.py"
            continue

        # --------------------------------------------------
        # Title
        # --------------------------------------------------
        if lower.startswith("title"):
            val = line.split(None, 1)[1].strip()
            if val:
                data["title"] = val
                data["title_source"] = "gameid.py"
            continue

        # --------------------------------------------------
        # CRC32
        # --------------------------------------------------
        if lower.startswith("crc32"):
            val = line.split(None, 1)[1].strip()
            if re.fullmatch(r"[0-9a-fA-F]{8}", val):
                data["crc"] = val.lower()
            continue

    return data
    
# ============================================================
# =========================== ARCADE =========================
# ============================================================

# No scanner

# ============================================================
# ======================= GAME & WATCH =======================
# ============================================================

def scan_gamewatch(path):
    """
    Match filename alias (e.g. gnw_egg) to Game & Watch DB ID.
    Returns the DB key (Game ID), not the alias.
    """
    section = DB.get("Handheld Electronic Game", {})
    name = os.path.splitext(os.path.basename(path))[0].lower()

    for gid, value in section.items():
        # value: "gnw_egg | Egg"
        alias = value.split("|", 1)[0].strip().lower()
        if alias == name:
            return gid

    return None


# ============================================================
# ==================== NINTENDO GAME BOY =====================
# ============================================================

def scan_gb(path):
    try:
        with open(path, "rb") as f:
            f.seek(0x013F)
            raw = f.read(5)

        if len(raw) != 5:
            return None

        id_bytes = raw[:4]
        flag = raw[4]

        # ID must be 4 uppercase ASCII letters
        if not re.fullmatch(rb"[A-Z]{4}", id_bytes):
            return None

        # Next byte must be a valid CGB compatibility flag
        # 0x80 = CGB supported
        # 0xC0 = CGB only
        if flag not in (0x80, 0xC0):
            return None

        return id_bytes.decode("ascii")

    except Exception:
        return None

# ============================================================
# ================ NINTENDO GAME BOY ADVANCE =================
# ============================================================

def scan_gba(path):
    try:
        with open(path, "rb") as f:
            f.seek(0x00AC)
            raw = f.read(4)

        if len(raw) != 4:
            return None

        if not re.fullmatch(rb"[A-Z0-9]{4}", raw):
            return None

        gid = raw.decode("ascii")
        return f"AGB-{gid}"

    except Exception:
        return None

# ============================================================
# ======================= NINTENDO DS ========================
# ============================================================

def scan_ds(path):
    try:
        with open(path, "rb") as f:
            f.seek(0x000C)
            raw = f.read(4)

        if len(raw) != 4:
            return None

        gid = raw.decode("ascii", "ignore").upper()

        # Must be 4 uppercase alphanumeric ASCII
        if not re.fullmatch(r"[A-Z0-9]{4}", gid):
            return None

        if path.lower().endswith(".dsi"):
            return f"TWL-{gid}"
        else:
            return f"NTR-{gid}"

    except Exception:
        return None

# ============================================================
# ======================= NINTENDO 3DS =======================
# ============================================================

def load_3ds_serial_database(path=None):
    if path is None:
        path = resource_path("3dsserialdatabase.txt")

    db = {}

    if not os.path.exists(path):
        return db

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue

            k, v = line.split("=", 1)
            k = k.strip().upper()
            v = v.strip().upper()

            if re.fullmatch(r"[0-9A-F]{16}", k) and re.fullmatch(r"(CTR|KTR|BBB)-[A-Z0-9]{4}", v):
                db[k] = v

    return db
THREEDS_SERIAL_DB = load_3ds_serial_database()

def scan_3ds(path):
    """
    Read NCSD/NCCH Title ID at 0x108, convert to DB key format and return the mapped serial.
    Returns the serial (e.g. CTR-XXXX) when found, otherwise None to allow CRC fallback.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0x108)
            raw = f.read(8)

        if len(raw) != 8:
            return None

        # Reject all-zero Title IDs
        if raw == b"\x00" * 8:
            return None

        # EXACT legacy behavior: uppercase hex (DB keys are uppercased)
        title_id = raw[::-1].hex().upper()

        # Use the module-level cache
        serial = THREEDS_SERIAL_DB.get(title_id)
        if serial:
            return serial

        # No mapping found -> return None so the pipeline can fall back to CRC
        return None

    except Exception:
        return None

# ============================================================
# ============================ NES ===========================
# ============================================================

# No scanner

# ============================================================
# ============================ SNES ==========================
# ============================================================

# No scanner

# ============================================================
# ===================== VIRTUAL BOY ==========================
# ============================================================

# No scanner

# ============================================================
# ===================== NINTENDO 64 ==========================
# ============================================================

def scan_n64(path):
    try:
        with open(path, "rb") as f:
            data = f.read(256)

        if len(data) < 64:
            return None

        magic = data[:4]

        # ----------------------------
        # Normalize to z64
        # ----------------------------
        if magic == b"\x80\x37\x12\x40":
            norm = data

        elif magic == b"\x37\x80\x40\x12":
            norm = bytearray(len(data))
            for i in range(0, len(data), 2):
                if i + 1 < len(data):
                    norm[i] = data[i + 1]
                    norm[i + 1] = data[i]
            norm = bytes(norm)

        elif magic == b"\x40\x12\x37\x80":
            norm = bytearray(len(data))
            for i in range(0, len(data), 2):
                if i + 1 < len(data):
                    norm[i] = data[i + 1]
                    norm[i + 1] = data[i]
            norm = bytes(norm)

        else:
            return None

        # ----------------------------
        # Extract header ID + region
        # ----------------------------
        core = norm[0x3B:0x3F].decode("ascii", "ignore").strip().upper()
        region_byte = norm[0x3E]

        REGION_MAP = {
            0x45: "USA",  # E
            0x50: "EUR",  # P
            0x4A: "JPN",  # J
        }

        region = REGION_MAP.get(region_byte)
        if not region:
            return None

        if len(core) == 4 and core.isalnum():
            return f"NUS-{core}-{region}"

    except Exception:
        pass

    return None

# ============================================================
# ===================== DOLPHIN HELPER =======================
# ============================================================

RE_DOLPHIN_ID    = re.compile(r"^Game ID:\s*([A-Z0-9]{6})", re.I)
RE_DOLPHIN_TITLE = re.compile(r"^Internal Name:\s*(.+)", re.I)

def run_dolphin_tool(path):
    try:
        p = subprocess.run(
            [DOLPHIN_TOOL, "header", "-i", os.path.basename(path)],
            cwd=os.path.dirname(path),
            capture_output=True,
            text=True,
            timeout=15
        )
    except Exception:
        return None, None, None, None

    game_id = None
    title = None

    for line in p.stdout.splitlines():
        line = line.strip()

        # Game ID
        if line.startswith("Game ID:"):
            game_id = line.split(":", 1)[1].strip().upper()

        # Internal disc name (Wii / GC)
        elif line.startswith("Internal Name:"):
            title = line.split(":", 1)[1].strip()

    # --------------------------------------------------
    # Title cleanup (same rules as GameID.py)
    # --------------------------------------------------
    if title:
        # Remove fixed-width padding if present
        title = title.replace("\x00", "")

        # Collapse whitespace
        title = re.sub(r"\s+", " ", title).strip()

        # Normalize trailing articles
        m = re.match(r"^(.*?),\s*(THE|A|AN)(.*)$", title, re.I)
        if m:
            base, art, rest = m.groups()
            title = f"{art.title()} {base}{rest}"
    else:
        title = None

    # --------------------------------------------------
    # Explicit source marking
    # --------------------------------------------------
    game_id_source = "dolphintool" if game_id else None
    title_source = "dolphintool" if title else None

    return game_id, game_id_source, title, title_source

# ============================================================
# ==================== NINTENDO GAMECUBE =====================
# ============================================================

def scan_gamecube(path):
    try:
        ext = os.path.splitext(path)[1].lower()

        if ext not in (".iso", ".gcm"):
            return None

        with open(path, "rb") as f:
            f.seek(0x0000)
            header = f.read(0x40)

        if len(header) < 0x40:
            return None

        raw_id = header[0x00:0x06].decode("ascii", "ignore").strip()

        if len(raw_id) != 6 or not raw_id.isalnum():
            return None

        return raw_id.upper()

    except Exception:
        return None

# ============================================================
# ====================== NINTENDO WII ========================
# ============================================================
      
def scan_wii(path):
    try:
        ext = os.path.splitext(path)[1].lower()

        if ext == ".iso":
            header_offset = 0x0000
        elif ext == ".wbfs":
            header_offset = 0x0200
        else:
            return None

        with open(path, "rb") as f:
            f.seek(header_offset)
            header = f.read(0x100)

        if len(header) < 0x100:
            return None

        raw_id = header[0x00:0x06].decode("ascii", "ignore").strip()

        # Wii GameID sanity check
        if len(raw_id) != 6 or not raw_id.isalnum():
            return None

        return raw_id.upper()

    except Exception:
        return None

# ============================================================
# ====================== SEGA HELPERS ========================
# ============================================================

def normalize_sega_id(gid):
    if not gid:
        return None

    g = gid.upper().strip()

    # ------------------------------------------
    # Strip Sega CD / Genesis header prefixes
    # Example: "GM T-93265-00"
    # ------------------------------------------
    g = re.sub(r"^GM\s+", "", g)

    # ------------------------------------------
    # Remove revision suffixes (-00, -01, etc)
    # ------------------------------------------
    g = re.sub(r"-\d{2}$", "", g)

    # ------------------------------------------
    # Canonical formatting
    # ------------------------------------------
    g = g.replace("_", "-").replace(".", "")

    # Txxxx[x] or Txxxxx[x] → T-xxxx[x] / T-xxxxx[x]
    g = re.sub(r"^(T)(\d{4,7}[A-Z]?)$", r"\1-\2", g)

    # MKxxxxx → MK-xxxxx
    g = re.sub(r"^(MK)(\d+)$", r"\1-\2", g)

    # HDRxxxx → HDR-xxxx
    g = re.sub(r"^(HDR)(\d+)$", r"\1-\2", g)

    return g

# ============================================================
# ======================= MEGA DRIVE =========================
# ============================================================

def megadrive_smd_scan(path):
    SYSTEM = "Genesis"
    try:
        with open(path, "rb") as f:
            f.seek(512)  # SMD copier header
            block = f.read(0x4000)
            if len(block) < 0x4000:
                return None

        # Descramble ONE block
        odd  = block[:0x2000]
        even = block[0x2000:]

        descrambled = bytearray()
        for o, e in zip(odd, even):
            descrambled.append(e)
            descrambled.append(o)

        # Extended scan window
        window = descrambled[0x100:0x300]
        text = window.decode("ascii", "ignore")

        idx = text.find("GM ")
        if idx == -1:
            return None

        # Grab exactly 11 bytes starting at GM
        raw = text[idx:idx + 11]

        # Keep printable ASCII only
        raw = "".join(c for c in raw if 32 <= ord(c) < 127)

        # Strip leading GM
        raw = raw[3:] if raw.startswith("GM ") else raw

        # Normalize MK 12345 → MK-12345
        raw = re.sub(r"\bMK\s+(\d+)", r"MK-\1", raw)

        return raw.strip()

    except Exception:
        return None

def megadrive_header_scan(path):
    SYSTEM = "Genesis"
    try:
        with open(path, "rb") as f:
            f.seek(0x180)      # slightly early for safety
            raw = f.read(0x30)

        text = raw.decode("ascii", "ignore")
        text = "".join(c for c in text if 32 <= ord(c) < 127)
        text = text.upper().replace("_", " ")
        text = " ".join(text.split())

        # Strip revision suffixes
        text = re.sub(r"-\d{2}\b", "", text)

        # Normalize "MK 00001121" → "MK-00001121"
        text = re.sub(r"\bMK\s+(\d+)\b", r"MK-\1", text)

        # Strip leading GM token only
        text = re.sub(r"^GM\s+", "", text)

        # 🔑 Strip leading "0000" only
        text = re.sub(r"^0000", "", text)
        
        m = GAMEID_RE[SYSTEM].search(text)
        if m:
            gid = m.group(1).upper()

            return gid

    except Exception:
        pass

    return None

def scan_megadrive(path):
    try:
        if path.lower().endswith(".smd"):
            gid = megadrive_smd_scan(path)
        else:
            gid = megadrive_header_scan(path)

        if gid:
            return normalize_sega_id(gid)

    except Exception:
        pass

    return None

# ============================================================
# ========================= SEGA CD ==========================
# ============================================================

def scan_segacd(path):
    """
    Sega CD / Mega-CD scanner.
    Reads the IP (boot) header in sector 0 and extracts the product code.
    """
    try:
        ext = os.path.splitext(path)[1].lower()
        data_path = path
        sector = None
        offset = None

        # ---------------------------------
        # CUE → Track 01 BIN + sector mode
        # ---------------------------------
        if ext == ".cue":
            bin_path = None
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.upper().startswith("FILE"):
                        parts = line.split('"')
                        if len(parts) >= 2:
                            bin_path = os.path.join(
                                os.path.dirname(path),
                                parts[1]
                            )
                            break

            if not bin_path or not os.path.exists(bin_path):
                return None

            sector, offset = detect_sector_mode(path)
            data_path = bin_path
            ext = ".bin"

        # ---------------------------------
        # BIN-only not supported (by design)
        # ---------------------------------
        if ext != ".bin" or sector is None:
            return None

        # ---------------------------------
        # Read IP header (sector 0)
        # ---------------------------------
        with open(data_path, "rb") as f:
            f.seek(offset)
            raw = f.read(2048)

        if len(raw) < 256:
            return None

        text = raw.decode("ascii", "ignore").upper()
        text = "".join(c for c in text if 32 <= ord(c) < 127)

        # ---------------------------------
        # Extract Sega CD product code
        # Example: "GM T-93265-00"
        # ---------------------------------
        m = re.search(
            r"GM\s+(T[\s\-]?\d{4,7}[A-Z]?|MK[\s\-]?\d+|HDR[\s\-]?\d+)",
            text
        )
        if not m:
            return None

        return normalize_sega_id(m.group(1))

    except Exception:
        return None

# ============================================================
# ========================= SATURN ===========================
# ============================================================

SATURN_SCAN_LIMIT = 512 * 1024  # 512 KB (IP.BIN is very early)

def scan_saturn(path):
    SYSTEM = "Saturn"
    try:
        data_path = path

        # If CUE, resolve Track 01 BIN
        if path.lower().endswith(".cue"):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.upper().startswith("FILE"):
                        data_path = os.path.join(
                            os.path.dirname(path),
                            line.split('"')[1]
                        )
                        break

        with open(data_path, "rb") as f:
            f.seek(48)
            raw = f.read(10)

        game_id = raw.decode("ascii", "ignore").strip()

        if game_id:
            return game_id.upper()

    except Exception:
        pass

    return None

# ============================================================
# ======================= DREAMCAST ==========================
# ============================================================

def scan_dreamcast(path):
    try:
        ext = os.path.splitext(path)[1].lower()
        base = os.path.dirname(path)

        # ======================
        # CUE / BIN
        # ======================
        if ext == ".cue":
            bin_path = None
            with open(path, "r", errors="ignore") as f:
                for line in f:
                    if line.upper().startswith("FILE"):
                        parts = line.split('"')
                        if len(parts) >= 2:
                            bin_path = os.path.join(base, parts[1])
                            break

            if not bin_path or not os.path.exists(bin_path):
                return None

            with open(bin_path, "rb") as b:
                sector = b.read(2048)

            raw = sector[79:88].decode("ascii", "ignore").strip().upper()
            return normalize_sega_id(raw)

        # ======================
        # GDI
        # ======================
        if ext == ".gdi":
            with open(path, "r", errors="ignore") as f:
                lines = [l.strip() for l in f if l.strip()]

            for line in lines[1:]:
                parts = line.split()
                if len(parts) < 6:
                    continue

                track_type = int(parts[1])
                sector_size = int(parts[3])

                filename = " ".join(parts[4:-1]).strip('"')
                bin_path = os.path.join(base, filename)

                # IP.BIN is always track 1 (type 0)
                if track_type != 0 or not os.path.exists(bin_path):
                    continue

                with open(bin_path, "rb") as b:
                    raw = b.read(sector_size)

                # normalize to 2048-byte user data
                if sector_size >= 2352:
                    sector = raw[16:16 + 2048]
                else:
                    sector = raw[:2048]

                raw_id = sector[63:72].decode("ascii", "ignore").strip().upper()
                return normalize_sega_id(raw_id)

    except Exception:
        pass

    return None

# ============================================================
# ====================== SONY HELPERS ========================
# ============================================================

def normalize_sony_id(gid):
    if not gid:
        return None
    gid = gid.upper()
    gid = re.sub(r"^([A-Z]{4})[_\-\.]?", r"\1-", gid)
    gid = gid.replace(".", "")
    return gid

# ============================================================
# ======================= PLAYSTATION ========================
# ============================================================

PSX_SCAN_LIMIT = 64 * 1024 * 1024   # 64 MB (reduce for speed, accuracy loss under 8 MB)
PSX_SCAN_CHUNK = 512 * 1024   # 512 kB (reduce for speed, accuracy loss under 128 kB)

def psx_read_system_cnf(bin_path, sector, offset):
    SYSTEM = "PSX"
    try:
        with open(bin_path, "rb") as f:
            # Root directory record from PVD
            f.seek((16 * sector) + offset + 156)
            root = f.read(34)

            root_lba  = int.from_bytes(root[2:6], "little")
            root_size = int.from_bytes(root[10:14], "little")

            f.seek((root_lba * sector) + offset)
            data = f.read(root_size)

        idx = data.find(b"SYSTEM.CNF")
        if idx == -1:
            return None

        entry = data[idx - 33 : idx + 32]
        lba  = int.from_bytes(entry[2:6], "little")
        size = int.from_bytes(entry[10:14], "little")

        with open(bin_path, "rb") as f:
            f.seek((lba * sector) + offset)
            cnf = f.read(size)

        return cnf.decode("ascii", "ignore")

    except Exception:
        return None

def psx_scan_raw(bin_path):
    SYSTEM = "PSX"
    scanned = 0
    buf = b""
    try:
        with open(bin_path, "rb") as f:
            while scanned < PSX_SCAN_LIMIT:
                chunk = f.read(PSX_SCAN_CHUNK)
                if not chunk:
                    break
                scanned += len(chunk)
                buf += chunk
                m = GAMEID_RE[SYSTEM].search(buf.decode("ascii", "ignore"))
                if m:
                    return m.group(1)
                buf = buf[-1024:]
    except Exception:
        pass
    return None
    
def scan_psx(path):
    SYSTEM = "PSX"

    try:
        bin_path = None
        sector, offset = 2352, 24

        # -----------------------------------------
        # Resolve BIN (CUE-only, inline)
        # -----------------------------------------
        if path.lower().endswith(".cue"):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "BINARY" in line.upper():
                        bin_path = os.path.join(
                            os.path.dirname(path),
                            line.split('"')[1]
                        )
                        break

            if not bin_path:
                return None

            sector, offset = detect_sector_mode(path)

        elif path.lower().endswith(".bin"):
            bin_path = path

        else:
            return None

        # -----------------------------------------
        # SYSTEM.CNF (authoritative)
        # -----------------------------------------
        cnf = psx_read_system_cnf(bin_path, sector, offset)
        if cnf:
            m = GAMEID_RE[SYSTEM].search(cnf)
            if m:
                return normalize_sony_id(m.group(1))

        # -----------------------------------------
        # Raw fallback
        # -----------------------------------------
        raw = psx_scan_raw(bin_path)
        if raw:
            return normalize_sony_id(raw)

    except Exception:
        pass

    return None


# ============================================================
# ======================= PLAYSTATION 2 ======================
# ============================================================

PS2_SCAN_LIMIT = 2 * 1024 * 1024   # 2 MB (reduce for speed, accuracy loss under 500 kB)

def scan_ps2(path):
    SYSTEM = "PS2"
    try:
        with open(path, "rb") as f:
            data = f.read(PS2_SCAN_LIMIT)

        text = data.decode("ascii", "ignore")
        m = GAMEID_RE[SYSTEM].search(text)

        if m:
            return normalize_sony_id(m.group(1))

    except Exception:
        pass

    return None

# ============================================================
# ==================== PLAYSTATION PORTABLE ==================
# ============================================================

PSP_SCAN_LIMIT = 512 * 1024   # 512 kB (reduce for speed, accuracy loss under 128 kB)

def scan_psp(path):
    SYSTEM = "PSP"

    try:
        with open(path, "rb") as f:
            data = f.read(512 * 1024)

        text = data.decode("ascii", "ignore")
        m = GAMEID_RE[SYSTEM].search(text)

        if m:
            gid = normalize_sony_id(m.group(1))
            return gid

    except Exception:
        pass

    return None

# ============================================================
# ========================= SCANNER ==========================
# ============================================================
 
ENABLED_SYSTEMS = {
    "ARCADE",
    "GW",
    "GB",
    "GBC",
    "GBA",
    "NDS",
    "3DS",
    "NES",
    "SNES",
    "VB",
    "N64",
    "GC",
    "WII",
    "MasterSys",
    "GameGear",
    "Genesis",
    "SegaCD",
    "32X",
    "Saturn",
    "Dreamcast",
    "PSX",
    "PS2",
    "PSP",
}


    
def scan_systems():

    for system_key, cfg in SYSTEMS.items():

        # TEMP: restrict which systems are scanned
        if ENABLED_SYSTEMS and system_key not in ENABLED_SYSTEMS:
            continue

        SYSTEM = system_key
        display = cfg["display"]
        root = cfg["root"]
        exts = cfg["exts"]
        sysdb = cfg["db_sections"]
        pat = GAMEID_RE.get(SYSTEM)
        gameidkey = cfg["gameid"]
        scanner = cfg.get("scanner")

        if not root or not os.path.isdir(root):
            continue

        if not exts:
            continue

        for path in find_games(root, exts):
            filename = os.path.basename(path)

            gameid_title = None
            title_source = None
            game_id = None
            gameid_source = None
            crc_gameid = None
            gameidpy_title = None
            dolphintool_title = None
            title_source = None
            
            base, tags = split_filename(filename)
            filename_title = clean_title(base)
            
            # ==============================================
            # 1) Override
            # ==============================================
            override = scan_override(filename)
            if override:
                override_title, override_id = override
                gameid_title= override_title
                game_id = override_id
                
                yield (
                    display,
                    gameid_title,
                    "override",
                    game_id,
                    "override",
                    filename
                )
                continue               
                
            if not SKIP_SCAN:

                # ==============================================
                # 2) Filename fast scan
                # ==============================================
                if not game_id:
                    m = pat.search(filename)
                    if m:
                        game_id = m.group(1)
                        gameid_source = "filename"
                    
                # ==================================================
                # 3) CHD / CSO → filename → CRC
                # ==================================================
                if filename.lower().endswith((".chd", ".cso", ".vb", ".vboy", ".gg")):
                    gameid_title = " ".join([filename_title] + tags)
                    game_id = crc32_file(path)
                    
                    yield (
                        display,
                        gameid_title,
                        "filename",
                        game_id,
                        "crc",
                        filename
                    )
                    continue             

                # ==============================================
                # 4) System scanner (container / header logic)
                # ==============================================
                if not game_id and scanner:
                    try:
                        gid = scanner(path)
                        if gid:
                            game_id = gid
                            gameid_source = "scanner"
                    except Exception:
                        pass

                # ==============================================
                # 5) Dolphin Tool (GC / WII only)
                # ==============================================
                if not game_id and SYSTEM in ("GC", "WII"):
                    gid_d, gid_d_src, title_d, title_d_src = run_dolphin_tool(path)

                    if gid_d:
                        game_id = gid_d.upper()
                        gameid_source = "dolphintool"

                    if title_d and not title_d.isupper():
                        dolphintool_title = title_d

            # ==============================================
            # 6) GameID.py
            # ==============================================
            gameid_path = path

            if path.lower().endswith(".cue"):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if "BINARY" in line.upper():
                            gameid_path = os.path.join(
                                os.path.dirname(path),
                                line.split('"')[1]
                            )
                            break

            if not game_id and path.lower().endswith(SUPPORTED_GAMEID_EXTS) and gameidkey:
                gid2, gid2_src, title2, title2_src, crc_gameid = get_gameid_and_title_from_gameid_py(gameid_path, SYSTEM, gameidkey)
                if gid2:
                    game_id = gid2
                    gameid_source = "gameid.py"

                if title2 and not title2.isupper():
                    gameidpy_title = clean_title(title2)

            # ==============================================
            # 7) CRC fallback
            # ==============================================
            if not game_id:
                if crc_gameid:
                    game_id = crc_gameid.lower()
                    gameid_source = "gameid.py"
                else:
                    game_id = crc32_file(path)
                    gameid_source = "crc"

            ################################################
            # Resolve Title
            ################################################

            # --------------------------------------------------
            # 8) CODEWORD OVERRIDE → FORCE FILENAME
            # --------------------------------------------------
            for cw in CODEWORDS:
                if cw.lower() in filename.lower():
                    gameid_title = " ".join([filename_title] + tags)
                    title_source = "filename"

            # --------------------------------------------------
            # 9) Database
            # --------------------------------------------------
            if not gameid_title and not SKIP_DATABASE and game_id:
                db_title = lookup_db_title(game_id, SYSTEM)
                if db_title:
                    gameid_title = " ".join([db_title] + tags)
                    title_source = "database"

            # --------------------------------------------------
            # 10) GameID.py (EARLY, if already run)
            # --------------------------------------------------
            if not gameid_title and gameidpy_title and gameid_source == "gameid.py":
                
                gameid_title = " ".join([gameidpy_title] + tags)
                title_source = "gameid.py"

            # --------------------------------------------------
            # 11) Dolphintool (early, if already run)
            # --------------------------------------------------
            if not gameid_title and dolphintool_title and gameid_source == "dolphintool":
                
                gameid_title = " ".join([dolphintool_title] + tags)
                title_source = "dolphintool"

            # --------------------------------------------------
            # 10) GameID.py (LATE, if not already run)
            # --------------------------------------------------
            if not gameid_title and path.lower().endswith(SUPPORTED_GAMEID_EXTS) and gameidkey:
                gid2, gid2_src, title2, title2_src, crc_gameid = \
                    get_gameid_and_title_from_gameid_py(gameid_path, SYSTEM, gameidkey)

                if title2 and not title2.isupper():
                    gameidpy_title = clean_title(title2)
                    if gameidpy_title:
                        gameid_title = " ".join([gameidpy_title] + tags)
                        title_source = "gameid.py"

            # --------------------------------------------------
            # filename (final fallback)
            # --------------------------------------------------
            
            if not gameid_title:
                gameid_title = " ".join([filename_title] + tags)
                title_source = "filename"

            yield (
                display,
                gameid_title,
                title_source,
                game_id,
                gameid_source,
                filename
            )


# ============================================================
# ========================= SYSTEMS ==========================
# ============================================================

SYSTEMS = {

#    "SYSTEM": {
#        "display": "Manufacturer - System Name",
#        "root": _setup["SYS_DIR"],
#        "exts": .iso,
#        "db_sections": ["Manufacturer - System Name"],
#        "id_pattern": Regular expression describing a valid game ID, 
#        "gameid": (system_arg, supports_id, supports_title, supports_crc),
#        "scanner": scan_system,
#    },

    "ARCADE": {
        "display": "FBNeo - Arcade Games",
        "root": _setup["ARC_DIR"],
        "exts": (".zip",),
        "db_sections": ["FBNeo - Arcade Games"],
        "id_pattern": r"[a-z0-9_]+",
        "gameid": (None, False, False, False),
        "scanner": None,
    },

    "GW": {
        "display": "Handheld Electronic Game",
        "root": _setup["NGW_DIR"],
        "exts": (".zip",),
        "db_sections": ["Handheld Electronic Game"],
        "id_pattern": r"[A-Z]{2}-[0-9]{2,3}[A-Z]?",
        "gameid": (None, False, False, False),
        "scanner": scan_gamewatch,
    },

    "GB": {
        "display": "Nintendo - Game Boy",
        "root": _setup["GB_DIR"],
        "exts": (".gb",".gbc"),
        "db_sections": ["Nintendo - Game Boy", "Nintendo - Game Boy Color"],
        "id_pattern": r"(?:CGB|DMG)-[A-Z0-9]{4}",
        "gameid": ("GBC", True, True, True),
        "scanner": scan_gb,
    },

    "GBC": {
        "display": "Nintendo - Game Boy Color",
        "root": _setup["GBC_DIR"],
        "exts": (".gb",".gbc"),
        "db_sections": ["Nintendo - Game Boy", "Nintendo - Game Boy Color"],
        "id_pattern": r"(?:CGB|DMG)-[A-Z0-9]{4}",
        "gameid": ("GBC", True, True, True),
        "scanner": scan_gb,
    },

    "GBA": {
        "display": "Nintendo - Game Boy Advance",
        "root": _setup["GBA_DIR"],
        "exts": (".gba",),
        "db_sections": ["Nintendo - Game Boy Advance"],
        "id_pattern": r"AGB-[A-Z0-9]{4}",
        "gameid": ("GBA", True, True, True),
        "scanner": scan_gba,
    },

    "NDS": {
        "display": "Nintendo - Nintendo DS",
        "root": _setup["NDS_DIR"],
        "exts": (".nds",),
        "db_sections": ["Nintendo - Nintendo DS"],
        "id_pattern": r"[A-Z]{4}[A-Z0-9]{4}",
        "gameid": (None, False, False, False),
        "scanner": scan_ds,
    },

    "3DS": {
        "display": "Nintendo - Nintendo 3DS",
        "root": _setup["N3DS_DIR"],
        "exts": (".3ds",),
        "db_sections": ["Nintendo - Nintendo 3DS"],
        "id_pattern": r"(?:CTR|KTR|BBB)-[A-Z0-9]{4}",
        "gameid": (None, False, False, False),
        "scanner": scan_3ds,
    },

    "NES": {
        "display": "Nintendo - Nintendo Entertainment System",
        "root": _setup["NES_DIR"],
        "exts": (".nes",),
        "db_sections": ["Nintendo - Nintendo Entertainment System"],
        "id_pattern": r"$^",
        "gameid": ("NES", True, True, True),
        "scanner": None,
    },

    "SNES": {
        "display": "Nintendo - Super Nintendo Entertainment System",
        "root": _setup["SNES_DIR"],
        "exts": (".sfc", ".smc"),
        "db_sections": ["Nintendo - Super Nintendo Entertainment System"],
        "id_pattern": r"(?:SHVC|SNSP|SNS|SFT)[-_]?[A-Z0-9]{2,6}",
        "gameid": ("SNES", True, True, True),
        "scanner": None,
    },

    "VB": {
        "display": "Nintendo - Virtual Boy",
        "root": _setup["NVB_DIR"],
        "exts": (".vb", ".vboy", ".bin"),
        "db_sections": ["Nintendo - Virtual Boy"],
        "id_pattern": r"[A-Z0-9]{3,8}",
        "gameid": (None, False, False, False),
        "scanner": None,
    },

    "N64": {
        "display": "Nintendo - Nintendo 64",
        "root": _setup["N64_DIR"],
        "exts": (".z64", ".n64", ".v64"),
        "db_sections": ["Nintendo - Nintendo 64"],
        "id_pattern": r"NUS-[A-Z0-9]{4}",
        "gameid": ("N64", True, True, True),
        "scanner": scan_n64,
    },

    "GC": {
        "display": "Nintendo - GameCube",
        "root": _setup["NGC_DIR"],
        "exts": (".iso", ".gcm", ".rvz", ".wbfs"),
        "db_sections": ["Nintendo - GameCube and Nintendo - Wii"],
        "id_pattern": r"[A-Z]{4}[0-9]{2}",
        "gameid": ("GC", True, True, True),
        "scanner": scan_gamecube,
    },

    "WII": {
        "display": "Nintendo - Wii",
        "root": _setup["WII_DIR"],
        "exts": (".iso", ".wbfs", ".rvz"),
        "db_sections": ["Nintendo - GameCube and Nintendo - Wii"],
        "id_pattern": r"[A-Z]{4}[0-9]{2}",
        "gameid": ("GC", True, True, True),
        "scanner": scan_wii,
    },

    "MasterSys": {
        "display": "Sega - Master System - Mark III",
        "root": _setup["SMS_DIR"],
        "exts": (".sms", ".bin"),
        "db_sections": ["Sega - Master System - Mark III"],
        "id_pattern": r"$^",
        "gameid": (None, False, False, False),
        "scanner": None,
    },
        
    "GameGear": {
        "display": "Sega - Game Gear",
        "root": _setup["SGG_DIR"],
        "exts": (".gg",),
        "db_sections": None,
        "id_pattern": r"$^",
        "gameid": (None, False, False, False),
        "scanner": None,
    },

    "Genesis": {
        "display": "Sega - Mega Drive - Genesis",
        "root": _setup["SMD_DIR"],
        "exts": (".md", ".bin", ".smd", ".gen"),
        "db_sections": ["Sega - Mega Drive - Genesis"],
        "id_pattern": r"(?:T-[0-9]{4,7}[A-Z]?|MK-[0-9]{5,8}|HDR-[0-9]{4,6}|\b(?!19(?:8[0-9]|9[0-9]))[0-9]{4}\b)",
        "gameid": ("Genesis", True, True, True),
        "scanner": scan_megadrive,
    },
    
    "SegaCD": {
        "display": "Sega - Mega-CD - Sega CD",
        "root": _setup["SCD_DIR"],
        "exts": (".cue", ".iso", ".chd"),
        "db_sections": ["Sega - Mega-CD - Sega CD"],
        "id_pattern": r"(?:T-[0-9]{4,7}[A-Z]?|MK-[0-9]{5,8}|HDR-[0-9]{4,6})",
        "gameid": ("SegaCD", True, True, True),
        "scanner": scan_segacd,
    },

    "32X": {
        "display": "Sega - 32X",
        "root": _setup["S32X_DIR"],
        "exts": (".32x", ".bin", ".md"),
        "db_sections": ["Sega - 32X"],
        "id_pattern": r"(?:T-[0-9]{4,7}[A-Z]?|MK-[0-9]{5,8}|HDR-[0-9]{4,6})",
        "gameid": ("Genesis", True, True, True),
        "scanner": scan_megadrive,
    },

    "Saturn": {
        "display": "Sega - Saturn",
        "root": _setup["SSA_DIR"],
        "exts": (".cue", ".iso", ".chd"),
        "db_sections": ["Sega - Saturn"],
        "id_pattern": r"(?:T-[0-9]{7}|GS-[0-9]{4}|MK-[0-9]{3}|SGS-[0-9]{3})",
        "gameid": ("Saturn", True, True, True),
        "scanner": scan_saturn,
    },

    "Dreamcast": {
        "display": "Sega - Dreamcast",
        "root": _setup["SDC_DIR"],
        "exts": (".gdi", ".cue", ".chd"),
        "db_sections": ["Sega - Dreamcast"],
        "id_pattern": r"(?:T-[0-9]{4,5}[A-Z]?|HDR-[0-9]{4,6}|MK-[0-9]{5,8})",
        "gameid": (None, False, False, False),
        "scanner": scan_dreamcast,
    },

    "PSX": {
        "display": "Sony - PlayStation",
        "root": _setup["PSX_DIR"],
        "exts": (".cue", ".iso", ".chd"),
        "db_sections": ["Sony - PlayStation"],
        "id_pattern": r"(?:SLUS|SLES|SLPS|SLPM|SCUS|SCES|SCED|SCPS|SLED|HDR|PCPX|PAPX|PBPX|DTL)[_\-\.]?\d{3}[_\-\.]?\d{2}",
        "gameid": ("PSX", True, True, True),
        "scanner": scan_psx,
    },

    "PS2": {
        "display": "Sony - PlayStation 2",
        "root": _setup["PS2_DIR"],
        "exts": (".iso", ".chd"),
        "db_sections": ["Sony - PlayStation 2"],
        "id_pattern": r"(?:SLES|SLPM|SLUS|SLPS|SCED|SCES|SCUS|SLKA|SCPS|SLED|SCKA|SCAJ|PCPX|PAPX|PBPX|SCCS|TCES|SCPN|TLES|PSXC|SCPM)[_\-\.]?\d{3}[_\-\.]?\d{2}",
        "gameid": ("PS2", True, True, True),
        "scanner": scan_ps2,
    },

    "PSP": {
        "display": "Sony - PlayStation Portable",
        "root": _setup["PSP_DIR"],
        "exts": (".iso", ".cso", ".chd"),
        "db_sections": ["Sony - PlayStation Portable"],
        "id_pattern": r"(?:ULES|ULJM|ULUS|ULJS|UCES|UCUS|ULKS|UCAS|UCJS|ULAS|UCKS|UCET|UCED|UCJP|UCJX|ULET|UCJB|ROSE|UTST|NPEG|NPUG|NPJG|NPJG|NPHG|HONEY|KAD)[_\-\.]?\d{3}[_\-\.]?\d{2}",
        "gameid": ("PSP", True, True, True),
        "scanner": scan_psp,
    },
}
 
# ============================================================
# ============ NORMALIZE MERGED SYSTEM ROOTS =================
# ============================================================

def same_path(a, b):
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))

gb_root  = SYSTEMS.get("GB", {}).get("root")
gbc_root = SYSTEMS.get("GBC", {}).get("root")

if same_path(gb_root, gbc_root):
    # Merged GB/GBC folders → scan once (GB wins)
    SYSTEMS.pop("GBC", None)


GAMEID_RE = {}
for sys_key, cfg in SYSTEMS.items():
    pat = cfg["id_pattern"]
    if not pat:
        continue
        
    GAMEID_RE[sys_key] = re.compile(rf"(?<![A-Z0-9])({pat})(?![A-Z0-9])")


# ============================================================
# ============================ MAIN =========================
# ============================================================

OUTPUT_FILE = "local_games.txt"

def main():
    results = []

    # Get rows from unified scanner
    rows = scan_systems()

    for row in rows:
        # Accept both 6-tuple and legacy 5-tuple for backward-compatibility
        if len(row) == 6:
            platform, gameid_title, title_source, game_id, gameid_source, filename = row
        elif len(row) == 5:
            platform, gameid_title, game_id, gameid_source, filename = row
            title_source = None
        else:
            if DEBUG:
                print(f"Skipping row with unexpected shape ({len(row)}): {row}")
            continue

        # find system + root from platform display name
        system_key = None
        root = None
        for sysk, cfg in SYSTEMS.items():
            if cfg.get("display") == platform:
                system_key = sysk
                root = cfg.get("root")
                break

        path = os.path.join(root, filename) if root else None

        if not gameid_title:
            base, tags = split_filename(filename)
            filename_title = clean_title(base)
            gameid_title = " ".join([filename_title] + tags)
            title_source = "filename"

        game_id = game_id or "N/A"
        gameid_source = gameid_source or "unknown"
        title_source = title_source or "unknown"

        sep = f" {Fore.BLACK}|{Style.RESET_ALL} "

        if PRINT_ALL:
            if DEBUG:
                print(
                    f"{platform}"
                    f"{sep}{gameid_title} {Fore.LIGHTBLACK_EX}({title_source}){Style.RESET_ALL}"
                    f"{sep}{game_id} {Fore.LIGHTBLACK_EX}({gameid_source}){Style.RESET_ALL}"
                    f"{sep}{filename}",
                    flush=True
                )
            else:
                print(
                    f"{platform}"
                    f"{sep}{gameid_title}"
                    f"{sep}{game_id}"
                    f"{sep}{filename}"
                )

        # Store full internal result (unchanged)
        results.append((
            platform,
            gameid_title,
            title_source,
            game_id,
            gameid_source,
            filename
        ))

    # --------------------------------------------------
    # Write output file (NO source columns)
    # --------------------------------------------------
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("Platform | Title | GameID | File\n\n")
        for p, t, ts, gid, gsrc, fn in results:
            f.write(f"{p} | {t} | {gid} | {fn}\n")

    print(f"\nDone. {len(results)} games written to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
#input()