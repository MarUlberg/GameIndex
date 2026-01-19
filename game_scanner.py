import os
import re
import time
import zlib
import shlex
import struct
import string
import subprocess
import configparser
from colorama import Fore, Style, init
init()

# ============================================================
# ========================== SETUP ===========================
# ============================================================

CONFIG_FILE = "config.txt"

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

ARC_DIR  = _setup["ARC_DIR"]

NGW_DIR  = _setup["NGW_DIR"]
GBC_DIR  = _setup["GBC_DIR"]
GBA_DIR  = _setup["GBA_DIR"]
NDS_DIR  = _setup["NDS_DIR"]
N3DS_DIR = _setup["N3DS_DIR"]

NES_DIR  = _setup["NES_DIR"]
SNES_DIR = _setup["SNES_DIR"]
NVB_DIR  = _setup["NVB_DIR"]
N64_DIR  = _setup["N64_DIR"]
NGC_DIR  = _setup["NGC_DIR"]
WII_DIR  = _setup["WII_DIR"]

SMD_DIR  = _setup["SMD_DIR"]
SSA_DIR  = _setup["SSA_DIR"]
SDC_DIR  = _setup["SDC_DIR"]

PSX_DIR  = _setup["PSX_DIR"]
PS2_DIR  = _setup["PS2_DIR"]
PSP_DIR  = _setup["PSP_DIR"]

DOLPHIN_TOOL = _setup["DOLPHIN_TOOL"]

# ============================================================
# ========================= SYSTEMS ==========================
# ============================================================

SYSTEMS = [
    # Arcade 
#    ("ARCADE",    "FBNeo - Arcade Games",                 ARC_DIR,   None),

    # Nintendo handhelds
    ("GW",        "Handheld Electronic Game",             NGW_DIR,   None),
    ("GBC",       "Nintendo - Game Boy",                  GBC_DIR,   (".gb", ".gbc")),
#    ("GBA",       "Nintendo - Game Boy Advance",          GBA_DIR,   (".gba",)),
#    ("NDS",       "Nintendo - Nintendo DS",               NDS_DIR,   (".nds",)),
#    ("3DS",       "Nintendo - Nintendo 3DS",              N3DS_DIR,  (".3ds",)),

    # Nintendo consoles
#    ("NES",       "Nintendo - Nintendo Entertainment System",        NES_DIR,  (".nes",)),
#    ("SNES",      "Nintendo - Super Nintendo Entertainment System",  SNES_DIR, (".sfc", ".smc")),
#    ("VB",        "Nintendo - Nintendo Virtual Boy",      NVB_DIR,   (".vb", ".vboy", ".bin")),
#    ("N64",       "Nintendo - Nintendo 64",               N64_DIR,   (".z64", ".n64", ".v64")),
#    ("GC",        "Nintendo - GameCube",                  NGC_DIR,   (".iso", ".gcm", ".rvz", ".wbfs")),
#    ("WII",       "Nintendo - Wii",                       WII_DIR,   (".iso", ".wbfs", ".rvz")),

    # Sega
#    ("Genesis",   "Sega - Mega Drive",                    SMD_DIR,   (".md", ".bin", ".smd", ".gen")),
#    ("Saturn",    "Sega - Saturn",                        SSA_DIR,   (".cue", ".iso", ".chd")),
#    ("Dreamcast", "Sega - Dreamcast",                     SDC_DIR,   (".gdi", ".cue", ".chd")),
    
    # Sony
#    ("PSX",       "Sony - PlayStation",                   PSX_DIR,   (".cue", ".iso", ".chd")),
#    ("PS2",       "Sony - PlayStation 2",                 PS2_DIR,   (".iso", ".chd")),
#    ("PSP",       "Sony - PlayStation Portable",          PSP_DIR,   (".iso", ".cso", ".chd")),
]

SYSTEM_TO_DB_SECTIONS = {
    "ARCADE": ["FBNeo - Arcade Games"],

    # Nintendo handhelds
    "GW":  ["Handheld Electronic Game"],
    "GB":  ["Nintendo - Game Boy", "Nintendo - Game Boy Color"],
    "GBC": ["Nintendo - Game Boy", "Nintendo - Game Boy Color"],
    "GBA": ["Nintendo - Game Boy Advance",],
    "NDS": ["Nintendo - Nintendo DS",],
    "3DS": ["Nintendo - Nintendo 3DS", ],

    # Nintendo consoles
    "NES":  ["Nintendo - Nintendo Entertainment System"],
    "SNES": ["Nintendo - Super Nintendo Entertainment System"],
    "N64":  ["Nintendo - Nintendo 64"],
    "VB":   ["Nintendo - Virtual Boy"],
    "GC":  ["Nintendo - GameCube and Nintendo - Wii"],
    "WII": ["Nintendo - GameCube and Nintendo - Wii"],

    # Sega
    "Genesis":   ["Sega - Mega Drive"],
    "Saturn":    ["Sega - Saturn"],
    "Dreamcast": ["Sega - Dreamcast"],
    
    # Sony
    "PSX": ["Sony - PlayStation"],
    "PS2": ["Sony - PlayStation 2"],
    "PSP": ["Sony - PlayStation Portable"],
}

# ============================================================
# ========================== REGEX ===========================
# ============================================================

# ----- GameID.py output -----
RE_TITLE     = re.compile(r"^title\s+(.+)", re.I)
RE_INT_TITLE = re.compile(r"^internal_title\s+(.+)", re.I)
RE_CRC       = re.compile(r"^crc32\s+([0-9a-f]{8})", re.I)
RE_ID        = re.compile(r"^ID\s+([A-Z0-9\-_.]+)", re.I)
RE_MAKER     = re.compile(r"^maker_code\s+([A-Z0-9]{2})", re.I)
RE_SERIAL    = re.compile(r"^serial\s+(.+)", re.I)

ID_PATTERNS = {
    "GW":        r"[A-Z]{2}-[0-9]{2,3}[A-Z]?",
    "GB":        r"DMG-[A-Z0-9]{4}",
    "GBC":       r"(?:CGB|DMG)-[A-Z0-9]{4}",
    "GBA":       r"AGB-[A-Z0-9]{4}",
    "NDS":       r"[A-Z]{4}[A-Z0-9]{4}",
    "3DS":       r"(?:CTR|KTR|BBB)-[A-Z0-9]{4}",
    "NES":       r"[A-Z0-9]{3,6}",
    "SNES":      r"(?:SHVC|SNSP|SNS|SFT)[-_]?[A-Z0-9]{2,6}",
    "VB":        r"[A-Z0-9]{3,8}",
    "N64":       r"NUS-[A-Z0-9]{4}",
    "GC":        r"[A-Z]{4}[0-9]{2}",
    "WII":       r"[A-Z]{4}[0-9]{2}",
    "Genesis":   r"(?:T-[0-9]{4,7}[A-Z]?|MK-[0-9]{5,8}|HDR-[0-9]{4,6}|\b[0-9]{4}\b)",
    "Saturn":    r"(?:T-[0-9]{7}|GS-[0-9]{4}|MK-[0-9]{3}|SGS-[0-9]{3})",
    "Dreamcast": r"(?:T-[0-9]{4,5}[A-Z]?|HDR-[0-9]{4,6}|MK-[0-9]{5,8})",
    "PSX":       r"(?:SLUS|SLES|SLPS|SLPM|SCUS|SCES|SCED|SCPS|SLED|HDR|PCPX|PAPX|PBPX|DTL)[_\-\.]?\d{3}[_\-\.]?\d{2}",
    "PS2":       r"(?:SLES|SLPM|SLUS|SLPS|SCED|SCES|SCUS|SLKA|SCPS|SLED|SCKA|SCAJ|PCPX|PAPX|PBPX|SCCS|TCES|SCPN|TLES|PSXC|SCPM)[_\-\.]?\d{3}[_\-\.]?\d{2}",
    "PSP":       r"(?:ULES|ULJM|ULUS|ULJS|UCES|UCUS|ULKS|UCAS|UCJS|ULAS|UCKS|UCET|UCED|UCJP|UCJX|ULET|UCJB|ROSE|UTST|HONEY|KAD)[_\-\.]?\d{3}[_\-\.]?\d{2}",
}

STRICT_ID_RE = {}
FILENAME_ID_RE = {}

for sys, pat in ID_PATTERNS.items():
    STRICT_ID_RE[sys] = re.compile(rf"^{pat}$")
    FILENAME_ID_RE[sys] = re.compile(rf"(?<![A-Z0-9])({pat})(?![A-Z0-9])")

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

def get_gameid_and_title_from_gameid_py(path, system):
    try:
        out = run_gameid(path, system)
        data = parse_gameid_output(out)
    except Exception:
        return None, None, None, None, None

    game_id = None
    title = None

    # --------------------------------------------------
    # Game ID extraction
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

    # --------------------------------------------------
    # Title extraction + cleanup (fixed-width safe)
    # --------------------------------------------------
    title = data.get("title") or data.get("internal")

    if title:
        # Remove NUL padding from fixed-width disc headers
        title = title.replace("\x00", "")

        # Collapse whitespace
        title = re.sub(r"\s+", " ", title).strip()

        # Normalize trailing articles: "Sims, The" → "The Sims"
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

def resolve_title(
    game_id,
    gameid_title,
    filename,
    path,
    system,
    gameid_source,
):
    base, tags = split_filename(filename)
    filename_title = clean_title(base)

    # --------------------------------------------------
    # CODEWORD OVERRIDE → FORCE FILENAME
    # --------------------------------------------------
    lowered = filename.lower()
    for cw in CODEWORDS:
        if cw.lower() in lowered:
            return (
                filename_title,
                "filename",
                game_id,
                gameid_source,
            )

    # --------------------------------------------------
    # override
    # --------------------------------------------------
    if gameid_title and gameid_source == "override":
        return (
            " ".join([gameid_title] + tags),
            "override",
            game_id,
            gameid_source,
        )

    # --------------------------------------------------
    # database
    # --------------------------------------------------
    if not SKIP_DATABASE and game_id:
        db_title = lookup_db_title(game_id, system)
        if db_title:
            return (
                " ".join([db_title] + tags),
                "database",
                game_id,
                gameid_source,
            )

    # --------------------------------------------------
    # gameid.py (early, if already run)
    # --------------------------------------------------
    if gameid_title and gameid_source == "gameid.py":
        return (
            " ".join([gameid_title] + tags),
            "gameid.py",
            game_id,
            gameid_source,
        )

    # --------------------------------------------------
    # dolphintool (early, if already run)
    # --------------------------------------------------
    if gameid_title and gameid_source == "dolphintool":
        return (
            " ".join([gameid_title] + tags),
            "dolphintool",
            game_id,
            gameid_source,
        )

    # --------------------------------------------------
    # LATE GameID.py escalation
    # --------------------------------------------------
    if (
        path
        and path.lower().endswith(SUPPORTED_GAMEID_EXTS)
        and gameid_source not in ("gameid.py", "dolphintool", "crc")
    ):
        gid2, gid2_src, title2, title2_src, _ = get_gameid_and_title_from_gameid_py(
            path, system.split(" - ")[-1]
        )

        if title2 and not title2.isupper():
            return (
                " ".join([title2] + tags),
                "gameid.py",
                gid2 or game_id,
                gid2_src or gameid_source,
            )

    # --------------------------------------------------
    # filename (final fallback)
    # --------------------------------------------------
    return (
        " ".join([filename_title] + tags),
        "filename",
        game_id,
        gameid_source,
    )


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
    
def resolve_bin(cue):
    with open(cue, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "BINARY" in line.upper():
                return os.path.join(
                    os.path.dirname(cue),
                    line.split('"')[1]
                )
    return None

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

def load_3ds_serial_database(path="3dsserialdatabase.txt"):
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

# ============================================================
# ================== GAMEID + FALLBACK CORE =================
# ============================================================

GAMEID_SCRIPT = "GameID.py"
SUPPORTED_GAMEID_EXTS = (".iso", ".cue", ".bin", ".gen", ".md", ".n64", ".z64", ".gba", ".gbc", ".gb", ".sfc", ".smc", ".nes")

# ---------- database.txt ----------
DB = {}

parser = configparser.ConfigParser(interpolation=None)

with open("database.txt", "r", encoding="utf-8") as f:
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

    sections = SYSTEM_TO_DB_SECTIONS.get(system)
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
            # Always return the title part
            if "|" in value:
                return value.split("|", 1)[1].strip()
            return value.strip()

        # ==================================================
        # Normal systems
        # ==================================================
        return value.strip()

    return None

# ---------- run GameID.py ----------
def run_gameid(path, system):
    try:
        p = subprocess.Popen(
            ["python", GAMEID_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        out, _ = p.communicate(f"{path}\n{system}\n")
        return out
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

        # Serial / ID (preferred)
        if lower.startswith(("id", "serial")):
            val = line.split(None, 1)[1].strip()

            # Accept GBA-style IDs
            if re.fullmatch(r"(AGB-)?[A-Z0-9]{4}", val):
                data["game_id"] = val
                data["gameid_source"] = "gameid.py"
            continue

        # Manufacturer code (GB/GBC only, 4 chars)
        if lower.startswith("manufacturer_code"):
            parts = line.split(None, 1)  # split on ANY whitespace
            if len(parts) == 2:
                val = parts[1].strip()
                if re.fullmatch(r"[A-Z0-9]{4}", val):
                    data["game_id"] = val
                    data["gameid_source"] = "gameid.py"
            continue

        # Title
        if lower.startswith("title"):
            val = line.split(None, 1)[1].strip()
            if val:
                data["title"] = val
                data["title_source"] = "gameid.py"
            continue

        # CRC32
        if lower.startswith("crc32"):
            val = line.split(None, 1)[1].strip()
            if re.fullmatch(r"[0-9a-fA-F]{8}", val):
                data["crc"] = val.lower()
            continue

    return data
    
# ============================================================
# =========================== ARCADE =========================
# ============================================================

def scan_arcade():
    SYSTEM = "ARCADE"
    out = []

    if not os.path.isdir(ARC_DIR):
        return out

    for filename in sorted(os.listdir(ARC_DIR)):
        if not filename.lower().endswith((".zip", ".7z")):
            continue

        path = os.path.join(ARC_DIR, filename)
        name = os.path.splitext(filename)[0]

        game_id = None
        gameid_title = None
        gameid_source = None

        # ==================================================
        # 1) Filename fast scan
        # ==================================================
        # Game & Watch titles have no internal IDs
        game_id = name
        gameid_source = "filename"

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            game_id = crc32_file(path)
            gameid_source = "crc"

        out.append((
            "FBNeo - Arcade Games",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ======================= GAME & WATCH =======================
# ============================================================

def gw_alias_to_id(name):
    """
    Match filename alias (e.g. gnw_egg) to Game & Watch DB ID.
    Returns ID or None.
    """
    section = DB.get("Handheld Electronic Game", {})
    name = name.lower()

    for gid, value in section.items():
        # value: "gnw_egg | Egg"
        alias = value.split("|", 1)[0].strip().lower()
        if alias == name:
            return gid

    return None

def scan_gamewatch():
    SYSTEM = "GW"
    out = []

    if not os.path.isdir(NGW_DIR):
        return out

    for filename in sorted(os.listdir(NGW_DIR)):
        if not filename.lower().endswith((".zip", ".7z")):
            continue

        path = os.path.join(NGW_DIR, filename)
        name = os.path.splitext(filename)[0]

        game_id = None
        gameid_title = None
        gameid_source = None

        # ==================================================
        # 1) Filename fast scan
        # ==================================================
        m = FILENAME_ID_RE[SYSTEM].search(filename)
        if m and m.group(1).isupper():
            game_id = m.group(1)
            gameid_source = "filename"

        # ==================================================
        # 2) Check database
        # ==================================================
        if not game_id:
            gid = gw_alias_to_id(name)
            if gid:
                game_id = gid
                gameid_source = "database"

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            game_id = crc32_file(path)
            gameid_source = "crc"

        out.append((
            "Handheld Electronic Game",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ==================== NINTENDO GAME BOY =====================
# ============================================================

def gbc_header_scan(path):
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


def scan_gb():
    SYSTEM = "GBC"
    out = []

    if not os.path.isdir(GBC_DIR):
        return out

    for path in find_games(GBC_DIR, (".gb", ".gbc")):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m and m.group(1).isupper():
                game_id = m.group(1)
                gameid_source = "filename"

            # ==================================================
            # 2) ROM header scan (GBC only)
            # ==================================================
            if not game_id and path.lower().endswith((".gb", ".gbc")):
                gid = gbc_header_scan(path)
                if gid:
                    game_id = gid
                    gameid_source = "rom_header"

        # ==================================================
        # 3) GameID.py
        # ==================================================
        if not game_id and path.lower().endswith(SUPPORTED_GAMEID_EXTS):
            gid2, gid2_src, title2, title2_src, crc_gameid = (
                get_gameid_and_title_from_gameid_py(path, SYSTEM)
            )

            # Accept GameID from serial / manufacturer_code
            if gid2:
                game_id = gid2
                gameid_source = gid2_src

            # Accept title only if not ALL CAPS
            if title2 and not title2.isupper():
                gameid_title = clean_title(title2)

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"

        out.append((
            "Nintendo - Game Boy",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ================ NINTENDO GAME BOY ADVANCE =================
# ============================================================

def gba_header_scan(path):
    try:
        with open(path, "rb") as f:
            f.seek(0x00AC)
            raw = f.read(4)

        if len(raw) != 4:
            return None

        # Must be 4 uppercase alnum ASCII
        if not re.fullmatch(rb"[A-Z0-9]{4}", raw):
            return None

        return raw.decode("ascii")

    except Exception:
        return None

def scan_gba():
    SYSTEM = "GBA"
    out = []

    if not os.path.isdir(GBA_DIR):
        return out

    for path in find_games(GBA_DIR, (".gba",)):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m and m.group(1).isupper():
                game_id = m.group(1)
                gameid_source = "filename"

            # ==================================================
            # 2) ROM header scan
            # ==================================================
            if not game_id:
                gid = gba_header_scan(path)
                if gid:
                    game_id = f"AGB-{gid}"
                    gameid_source = "rom_header"

        # ==================================================
        # 3) GameID.py (ID + title + crc)
        # ==================================================
        if not game_id and path.lower().endswith(SUPPORTED_GAMEID_EXTS):
            gid2, gid2_src, title2, title2_src, crc_gameid = (
                get_gameid_and_title_from_gameid_py(path, SYSTEM)
            )

            # Accept GameID.py ID (serial or ID)
            if gid2:
                game_id = gid2
                if not game_id.startswith("AGB-"):
                    game_id = f"AGB-{game_id}"
                gameid_source = gid2_src


            if title2 and not title2.isupper():
                gameid_title = clean_title(title2)

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"

        out.append((
            "Nintendo - Game Boy Advance",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ======================= NINTENDO DS ========================
# ============================================================

def nds_header_scan(path):
    try:
        with open(path, "rb") as f:
            f.seek(0x000C)
            raw = f.read(4)

        if len(raw) != 4:
            return None

        # Must be 4 uppercase alphanumeric ASCII
        if not re.fullmatch(rb"[A-Z0-9]{4}", raw):
            return None

        return raw.decode("ascii")

    except Exception:
        return None

def scan_ds():
    SYSTEM = "NDS"
    out = []

    if not os.path.isdir(NDS_DIR):
        return out

    for path in find_games(NDS_DIR, (".nds",)):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m and m.group(1).isupper():
                game_id = m.group(1)
                gameid_source = "filename"

            # ==================================================
            # 2) ROM header scan
            # ==================================================
            if not game_id:
                gid = nds_header_scan(path)
                if gid:
                    if path.lower().endswith(".dsi"):
                        game_id = f"TWL-{gid}"
                    else:
                        game_id = f"NTR-{gid}"
                    gameid_source = "rom_header"

        # ==================================================
        # 3) GameID.py (Nintendo DS has no support → skipped)
        # ==================================================

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"

        out.append((
            "Nintendo - Nintendo DS",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ======================= NINTENDO 3DS =======================
# ============================================================

def ctr_header_scan(path):
    try:
        with open(path, "rb") as f:
            f.seek(0x108)
            raw = f.read(8)

        if len(raw) != 8:
            return None

        # Reject all-zero Title IDs
        if raw == b"\x00" * 8:
            return None

        # NCSD Title ID is little-endian in file → convert to big-endian
        return raw[::-1].hex().upper()

    except Exception:
        return None

def scan_3ds():
    SYSTEM = "3DS"
    out = []

    serial_db = load_3ds_serial_database()

    if not os.path.isdir(N3DS_DIR):
        return out

    for path in find_games(N3DS_DIR, (".3ds", ".cci")):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan (rarely useful)
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m and m.group(1).isupper():
                game_id = m.group(1)
                gameid_source = "filename"

            # ==================================================
            # 2) ROM header scan (NCSD Title ID → serial DB)
            # ==================================================
            if not game_id:
                title_id = ctr_header_scan(path)
                if title_id:
                    game_id = serial_db.get(title_id, title_id)
                    gameid_source = "rom_header"

        # ==================================================
        # 3) GameID.py (Nintendo 3DS unsupported → skipped)
        # ==================================================

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            game_id = crc32_file(path)
            gameid_source = "crc"

        out.append((
            "Nintendo - Nintendo 3DS",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ============================ NES ===========================
# ============================================================

def scan_nes():
    SYSTEM = "NES"
    out = []

    if not os.path.isdir(NES_DIR):
        return out

    for path in find_games(NES_DIR, (".nes",)):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan (low confidence)
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m and m.group(1).isupper():
                game_id = m.group(1)
                gameid_source = "filename"

            # ==================================================
            # 2) ROM header scan (NES header to unreliable read)
            # ==================================================

        # ==================================================
        # 3) GameID.py
        # ==================================================
        if path.lower().endswith(SUPPORTED_GAMEID_EXTS):
            gid2, gid2_src, title2, title2_src, crc_gameid = (
                get_gameid_and_title_from_gameid_py(path, SYSTEM)
            )

            if title2 and not title2.isupper():
                gameid_title = clean_title(title2)

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"

        out.append((
            "Nintendo - Nintendo Entertainment System",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ============================ SNES ==========================
# ============================================================

def scan_snes():
    SYSTEM = "SNES"
    out = []

    if not os.path.isdir(SNES_DIR):
        return out

    for path in find_games(SNES_DIR, (".sfc", ".smc")):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m and m.group(1).isupper():
                game_id = m.group(1)
                gameid_source = "filename"

        # ==================================================
        # 3) GameID.py
        # ==================================================
        if path.lower().endswith(SUPPORTED_GAMEID_EXTS):
            gid2, gid2_src, title2, title2_src, crc_gameid = (
                get_gameid_and_title_from_gameid_py(path, SYSTEM)
            )

            # SNES: trust title, not GameID
            if title2 and not title2.isupper():
                gameid_title = clean_title(title2)

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"

        out.append((
            "Nintendo - Super Nintendo Entertainment System",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ===================== VIRTUAL BOY ==========================
# ============================================================

def scan_virtualboy():
    SYSTEM = "VB"
    out = []

    if not os.path.isdir(NVB_DIR):
        return out

    for path in find_games(NVB_DIR, (".vb", ".vboy", ".bin")):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            # Uppercase-only, must contain at least one digit
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m and m.group(1).isupper() and any(c.isdigit() for c in m.group(1)):
                game_id = m.group(1)
                gameid_source = "filename"

            # ==================================================
            # 2) ROM header scan (Virtual Boy has no header to read)
        # ==================================================
        # 3) GameID.py (Virtual Boy has no support → skipped)
        # ==================================================

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            game_id = crc32_file(path)
            gameid_source = "crc"

        out.append((
            "Nintendo - Virtual Boy",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ===================== NINTENDO 64 ==========================
# ============================================================

def n64_header_scan(path):
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

def scan_n64():
    SYSTEM = "N64"
    out = []

    if not os.path.isdir(N64_DIR):
        return out

    for path in find_games(N64_DIR, (".z64", ".n64", ".v64", ".rom", ".bin")):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m:
                game_id = m.group(1).upper()
                gameid_source = "filename"

            # ==================================================
            # 2) ROM header product code
            # ==================================================
            if not game_id:
                gid = n64_header_scan(path)
                if gid:
                    game_id = gid
                    gameid_source = "rom_header"

        # ==================================================
        # 3) GameID.py
        # ==================================================
        if not game_id and path.lower().endswith(SUPPORTED_GAMEID_EXTS):
            gid2, gid2_src, title2, title2_src, crc_gameid = get_gameid_and_title_from_gameid_py(path, SYSTEM)
            if gid2:
                game_id = gid2
                gameid_source = gid2_src

            if title2 and not title2.isupper():
                gameid_title = clean_title(title2)

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"

        out.append((
            "Nintendo - Nintendo 64",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

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

def gamecube_container_scan(path):
    try:
        ext = os.path.splitext(path)[1].lower()

        if ext not in (".iso", ".gcm"):
            return None, None

        with open(path, "rb") as f:
            f.seek(0x0000)
            header = f.read(0x40)

        if len(header) < 0x40:
            return None, None

        raw_id = header[0x00:0x06].decode("ascii", "ignore").strip()

        if len(raw_id) != 6 or not raw_id.isalnum():
            return None, None

        return raw_id.upper(), "disc_header"

    except Exception:
        return None, None
    
def scan_gamecube():
    SYSTEM = "GC"
    out = []

    if not os.path.isdir(NGC_DIR):
        return out

    for path in find_games(NGC_DIR, (".iso", ".gcm", ".rvz")):
        filename = os.path.basename(path)

        game_id = None
        gameid_source = None
        gameid_title = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m:
                game_id = m.group(1)
                gameid_source = "filename"

            # ==================================================
            # 2) Disc container scan
            # ==================================================
            if not game_id and path.lower().endswith((".iso", ".gcm")):
                gid, src = gamecube_container_scan(path)
                if gid:
                    game_id = gid
                    gameid_source = src

            # ==================================================
            # 2.1) Dolphin Tool
            # ==================================================
            if not game_id:
                gid_d, gid_d_src, title_d, title_d_src = run_dolphin_tool(path)

                if gid_d:
                    game_id = gid_d.upper()
                    gameid_source = gid_d_src

                if title_d and not title_d.isupper():
                    gameid_title = title_d

        # ==================================================
        # 3) GameID.py
        # ==================================================
        if not game_id and path.lower().endswith(SUPPORTED_GAMEID_EXTS):
            gid2, gid2_src, title2, title2_src, crc_gameid = get_gameid_and_title_from_gameid_py(path, SYSTEM)
            if gid2:
                game_id = gid2
                gameid_source = gid2_src
            if title2 and not title2.isupper():
                gameid_title = clean_title(title2)

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"

        out.append((
            "Nintendo - GameCube",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ====================== NINTENDO WII ========================
# ============================================================
      
def wii_container_scan(path):
    try:
        ext = os.path.splitext(path)[1].lower()

        if ext == ".iso":
            header_offset = 0x0000
        elif ext == ".wbfs":
            header_offset = 0x0200
        else:
            return None, None

        with open(path, "rb") as f:
            f.seek(header_offset)
            header = f.read(0x100)

        if len(header) < 0x100:
            return None, None

        raw_id = header[0x00:0x06].decode("ascii", "ignore").strip()

        # Wii GameID sanity check
        if len(raw_id) != 6 or not raw_id.isalnum():
            return None, None

        return raw_id.upper(), "rom_header"

    except Exception:
        return None, None


def scan_wii():
    SYSTEM = "WII"
    out = []

    if not os.path.isdir(WII_DIR):
        return out

    for path in find_games(WII_DIR, (".iso", ".wbfs", ".rvz")):
        filename = os.path.basename(path)

        game_id = None
        gameid_source = None
        gameid_title = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m:
                game_id = m.group(1)
                gameid_source = "filename"

            # ==================================================
            # 2) Disc container scan
            # ==================================================
            if not game_id and path.lower().endswith((".iso", ".wbfs")):
                gid, src = wii_container_scan(path)
                if gid:
                    game_id = gid
                    gameid_source = "disc_header"

            # ==================================================
            # 2.1) Dolphin Tool
            # ==================================================
            if not game_id:
                gid_d, gid_d_src, title_d, title_d_src = run_dolphin_tool(path)

                if gid_d:
                    game_id = gid_d.upper()
                    gameid_source = gid_d_src

                if title_d and not title_d.isupper():
                    gameid_title = title_d

        # ==================================================
        # 3) GameID.py
        # ==================================================
        if not game_id and path.lower().endswith(SUPPORTED_GAMEID_EXTS):
            gid2, gid2_src, title2, title2_src, crc_gameid = get_gameid_and_title_from_gameid_py(path, "GC")
            if gid2:
                game_id = gid2
                gameid_source = gid2_src
            if title2 and not title2.isupper():
                gameid_title = clean_title(title2)

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"

        out.append((
            "Nintendo - Wii",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ====================== SEGA HELPERS ========================
# ============================================================

def normalize_sega_id(gid):
    if not gid:
        return None

    g = gid.upper().replace("_", "-").replace(".", "")

    # Txxxx[x] or Txxxxx[x] → T-xxxx[x] / T-xxxxx[x]
    g = re.sub(r"^(T)(\d{4,5}[A-Z]?)$", r"\1-\2", g)

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
        
        m = FILENAME_ID_RE[SYSTEM].search(text)
        if m:
            gid = m.group(1).upper()

            return gid

    except Exception:
        pass

    return None

def scan_megadrive():
    SYSTEM = "Genesis"
    out = []

    if not os.path.isdir(SMD_DIR):
        return out

    for path in find_games(SMD_DIR, (".md", ".bin", ".smd", ".gen")):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m:
                cand = m.group(1).upper()
                if not re.fullmatch(r"\d{7,8}", cand):
                    game_id = cand
                    gameid_source = "filename"

            # ==================================================
            # 2) ROM header product code
            # ==================================================
            if not game_id:
                if path.lower().endswith(".smd"):
                    gid = megadrive_smd_scan(path)
                    if gid:
                        game_id = normalize_sega_id(gid)
                        gameid_source = "rom_header"
                else:
                    gid = megadrive_header_scan(path)
                    if gid:
                        game_id = normalize_sega_id(gid)
                        gameid_source = "rom_header"

        # ==================================================
        # 3) GameID.py
        # ==================================================
        if not game_id and path.lower().endswith(SUPPORTED_GAMEID_EXTS):
            gid2, gid2_src, title2, title2_src, crc_gameid = get_gameid_and_title_from_gameid_py(path, SYSTEM)
            if gid2:
                game_id = gid2
                gameid_source = "gameid.py"

            if title2 and not title2.isupper():
                gameid_title = clean_title(title2)

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"

        out.append((
            "Sega - Mega Drive",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out
    
# ============================================================
# ========================= SATURN ===========================
# ============================================================

SATURN_SCAN_LIMIT = 512 * 1024  # 512 KB (IP.BIN is very early)

def saturn_ip_bin_scan(path):
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

def scan_saturn():
    SYSTEM = "Saturn"
    out = []

    if not os.path.isdir(SSA_DIR):
        return out

    for path in find_games(SSA_DIR, (".cue", ".iso", ".chd")):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m:
                game_id = m.group(1)
                gameid_source = "filename"

            # ==================================================
            # CHD → CRC
            # ==================================================
            if filename.lower().endswith(".chd"):
                if not game_id:
                    game_id = crc32_file(path)
                    gameid_source = "crc"

            else:
                # ==================================================
                # 2) IP.BIN fast scan (ISO / BIN)
                # ==================================================
                if not game_id:
                    gid = saturn_ip_bin_scan(path)
                    if gid:
                        game_id = normalize_sega_id(gid)
                        gameid_source = "ip.bin"

        # ==================================================
        # 3) GameID.py
        # ==================================================
        if not game_id and path.lower().endswith(SUPPORTED_GAMEID_EXTS):
            gid2, gid2_src, title2, title2_src, crc_gameid = get_gameid_and_title_from_gameid_py(path, SYSTEM)
            if gid2:
                game_id = gid2
                gameid_source = "gameid.py"

            if title2 and not title2.isupper():
                gameid_title = clean_title(title2)

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"
                
        out.append((
            "Sega - Saturn",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ======================= DREAMCAST ==========================
# ============================================================

def dc_scan_tracks(path):
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

def scan_dreamcast():
    SYSTEM = "Dreamcast"
    out = []

    if not os.path.isdir(SDC_DIR):
        return out

    for path in find_games(SDC_DIR, (".gdi", ".cue", ".chd")):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m:
                game_id = m.group(1)
                gameid_source = "filename"

            # ==================================================
            # CHD → CRC
            # ==================================================
            if filename.lower().endswith(".chd"):
                if not game_id:
                    game_id = crc32_file(path)
                    gameid_source = "crc"
            else:
                # ==================================================
                # 2) IP.BIN
                # ==================================================
                if not game_id:
                    gid = dc_scan_tracks(path)
                    if gid:
                        game_id = normalize_sega_id(gid)
                        gameid_source = "ip.bin"

        # ==================================================
        # 3) GameID.py (Dreamcast has no support → skipped)
        # ==================================================

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            game_id = crc32_file(path)
            gameid_source = "crc"

        out.append((
            "Sega - Dreamcast",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

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

PS1_SCAN_LIMIT = 64 * 1024 * 1024   # 64 MB (reduce for speed, accuracy loss under 8 MB)
PS1_SCAN_CHUNK = 512 * 1024   # 512 kB (reduce for speed, accuracy loss under 128 kB)

def ps1_read_system_cnf(bin_path, sector, offset):
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

def ps1_scan_raw(bin_path):
    SYSTEM = "PSX"
    scanned = 0
    buf = b""
    try:
        with open(bin_path, "rb") as f:
            while scanned < PS1_SCAN_LIMIT:
                chunk = f.read(PS1_SCAN_CHUNK)
                if not chunk:
                    break
                scanned += len(chunk)
                buf += chunk
                m = STRICT_ID_RE[SYSTEM].search(buf.decode("ascii", "ignore"))
                if m:
                    return m.group(1)
                buf = buf[-1024:]
    except Exception:
        pass
    return None

def scan_ps1():
    SYSTEM = "PSX"
    out = []

    if not os.path.isdir(PSX_DIR):
        return out

    for path in find_games(PSX_DIR, (".cue", ".chd", ".iso")):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None
        
        # CodeBreaker Overide
        cb = re.search(r"(code[\s._-]*breaker|codebreaker|cb)[\s._-]*(?:version|ver|v)?[\s._-]*(\d+(?:\.\d+)?)", filename, re.I)
        if cb:
            v = cb.group(2)
            out.append(("Sony - PlayStation", f"CodeBreaker v{v}", f"CODE-BRK{v.replace('.', '')}", "override" , filename))
            continue
            
        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m and m.group(1).isupper():
                game_id = normalize_sony_id(m.group(1))
                gameid_source = "filename"

            # ==================================================
            # CHD → CRC
            # ==================================================
            if filename.lower().endswith(".chd"):
                if not game_id:
                    game_id = crc32_file(path)
                    gameid_source = "crc"
            else:
                # ==================================================
                # 2) SYSTEM.CNF + RAW
                # ==================================================
                if not game_id and path.lower().endswith(".cue"):
                    binp = resolve_bin(path)
                    if binp:
                        sector, offset = detect_sector_mode(path)
                        cnf = ps1_read_system_cnf(binp, sector, offset)
                        if cnf:
                            m = FILENAME_ID_RE[SYSTEM].search(cnf)
                            if m:
                                game_id = normalize_sony_id(m.group(1))
                                gameid_source = "system.cnf"

                        if not game_id:
                            raw = ps1_scan_raw(binp)
                            if raw:
                                game_id = normalize_sony_id(raw)
                                gameid_source = "raw"

        # ==================================================
        # 3) GameID.py
        # ==================================================
        if not game_id and path.lower().endswith(SUPPORTED_GAMEID_EXTS):
            gid2, gid2_src, title2, title2_src, crc_gameid = get_gameid_and_title_from_gameid_py(path, SYSTEM)
            if gid2:
                game_id = gid2
                gameid_source = gid2_src

            if title2 and not title2.isupper():
                gameid_title = clean_title(title2)

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"

        out.append((
            "Sony - PlayStation",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ======================= PLAYSTATION 2 ======================
# ============================================================

PS2_SCAN_LIMIT = 2 * 1024 * 1024   # 2 MB (reduce for speed, accuracy loss under 500 kB)

def ps2_iso_scan(path):
    SYSTEM = "PS2"
    try:
        with open(path, "rb") as f:
            data = f.read(PS2_SCAN_LIMIT)

        text = data.decode("ascii", "ignore")
        m = FILENAME_ID_RE[SYSTEM].search(text)

        if m:
            return m.group(1).upper()

    except Exception:
        pass

    return None

def scan_ps2():
    SYSTEM = "PS2"
    out = []

    if not os.path.isdir(PS2_DIR):
        return out

    for path in find_games(PS2_DIR, (".iso", ".chd")):
        filename = os.path.basename(path)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None
        
        # CodeBreaker Overide
        cb = re.search(r"(code[\s._-]*breaker|codebreaker|cb)[\s._-]*(?:version|ver|v)?[\s._-]*(\d+(?:\.\d+)?)", filename, re.I)
        if cb:
            v = cb.group(2)
            out.append(("Sony - Playstation 2", f"CodeBreaker v{v}", f"CODE-BRK{v.replace('.', '')}", "override" , filename))
            continue
 
        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m and m.group(1).isupper():
                game_id = game_id = normalize_sony_id(m.group(1))
                gameid_source = "filename"

            # ==================================================
            # CHD → CRC
            # ==================================================
            if filename.lower().endswith(".chd"):
                if not game_id:
                    game_id = crc32_file(path)
                    gameid_source = "crc"
            else:
                # ==================================================
                # 2) ISO fast scan (2 MB)
                # ==================================================
                if not game_id:
                    gid = ps2_iso_scan(path)
                    if gid:
                        m = FILENAME_ID_RE[SYSTEM].search(gid)
                        if m:
                            game_id = game_id = normalize_sony_id(m.group(1))
                            gameid_source = "iso"

        # ==================================================
        # 3) GameID.py
        # ==================================================
        if not game_id and path.lower().endswith(SUPPORTED_GAMEID_EXTS):
            gid2, gid2_src, title2, title2_src, crc_gameid = get_gameid_and_title_from_gameid_py(path, SYSTEM)
            if gid2:
                game_id = gid2
                gameid_source = gid2_src

            if title2 and not title2.isupper():
                gameid_title = clean_title(title2)

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"

        out.append((
            "Sony - Playstation 2",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ======================= PLAYSTATION PORTABLE ===============
# ============================================================

PSP_SCAN_LIMIT = 512 * 1024   # 512 kB (reduce for speed, accuracy loss under 128 kB)

def psp_iso_scan(path):
    SYSTEM = "PSP"
    try:
        with open(path, "rb") as f:
            data = f.read(PSP_SCAN_LIMIT)

        text = data.decode("ascii", "ignore")
        m = FILENAME_ID_RE[SYSTEM].search(text)

        if m:
            return m.group(1).upper()

    except Exception:
        pass

    return None

def scan_psp():
    SYSTEM = "PSP"
    out = []

    if not os.path.isdir(PSP_DIR):
        return out

    for filename in sorted(os.listdir(PSP_DIR)):
        if not filename.lower().endswith((".iso", ".cso", ".chd")):
            continue

        path = os.path.join(PSP_DIR, filename)

        game_id = None
        gameid_title = None
        gameid_source = None
        crc_gameid = None

        if not SKIP_SCAN:
            # ==================================================
            # 1) Filename fast scan
            # ==================================================
            m = FILENAME_ID_RE[SYSTEM].search(filename)
            if m and m.group(1).isupper():
                game_id = game_id = normalize_sony_id(m.group(1))
                gameid_source = "filename"

            # ==================================================
            # CHD / CSO → filename → CRC
            # ==================================================
            if filename.lower().endswith((".chd", ".cso")):
                if not game_id:
                    game_id = crc32_file(path)
                    gameid_source = "crc"

            else:
                # ==================================================
                # 2) ISO fast scan (ISO only)
                # ==================================================
                if not game_id and filename.lower().endswith(".iso"):
                    gid = psp_iso_scan(path)
                    if gid:
                        game_id = game_id = normalize_sony_id(gid)
                        gameid_source = "iso"

        # ==================================================
        # 3) GameID.py
        # ==================================================
        if not game_id and path.lower().endswith(SUPPORTED_GAMEID_EXTS):
            gid2, gid2_src, title2, title2_src, crc_gameid = get_gameid_and_title_from_gameid_py(path, SYSTEM)
            if gid2:
                game_id = gid2
                gameid_source = gid2_src

            if title2 and not title2.isupper():
                gameid_title = clean_title(title2)

        # ==================================================
        # 4) CRC fallback
        # ==================================================
        if not game_id:
            if crc_gameid:
                game_id = crc_gameid.lower()
                gameid_source = "gameid.py"
            else:
                game_id = crc32_file(path)
                gameid_source = "crc"

        out.append((
            "Sony - PlayStation Portable",
            gameid_title,
            game_id,
            gameid_source,
            filename
        ))

    return out

# ============================================================
# ===================== SCANNER REGISTRY =====================
# ============================================================

SCANNERS = {
    "ARCADE":    scan_arcade,

    "GW":        scan_gamewatch,
    "GBC":       scan_gb,
    "GBA":       scan_gba,
    "NDS":       scan_ds,
    "3DS":       scan_3ds,

    "NES":       scan_nes,
    "SNES":      scan_snes,
    "VB":        scan_virtualboy,
    "N64":       scan_n64,

    "GC":       scan_gamecube,
    "WII":       scan_wii,

    "Genesis":   scan_megadrive,
    "Saturn":    scan_saturn,
    "Dreamcast": scan_dreamcast,

    "PSX":       scan_ps1,
    "PS2":       scan_ps2,
    "PSP":       scan_psp,
}

# ============================================================
# ============================ MAIN =========================
# ============================================================

OUTPUT_FILE = "local_games.txt"

def main():
    results = []

    for system, platform, root, exts in SYSTEMS:
        if not os.path.isdir(root):
            continue

        scanner = SCANNERS.get(system)
        if not scanner:
            continue

        try:
            rows = scanner()
        except Exception:
            rows = []

        for platform2, gameid_title, game_id, gameid_source, filename in rows:
            if gameid_source == "override":
                final_title = gameid_title
                title_source = "override"
            else:
                path = os.path.join(root, filename)
                final_title, title_source, game_id, gameid_source = resolve_title(
                    game_id,
                    gameid_title,
                    filename,
                    path,
                    system,
                    gameid_source
                )

            sep = f" {Fore.BLACK}|{Style.RESET_ALL} "

            if PRINT_ALL:
                if DEBUG:
                    print(
                        f"{platform2}"
                        f"{sep}{final_title} {Fore.LIGHTBLACK_EX}({title_source}){Style.RESET_ALL}"
                        f"{sep}{game_id} {Fore.LIGHTBLACK_EX}({gameid_source}){Style.RESET_ALL}"
                        f"{sep}{filename}"
                    )
                else:
                    print(
                        f"{platform2}"
                        f"{sep}{final_title}"
                        f"{sep}{game_id}"
                        f"{sep}{filename}"
                    )

            results.append((platform2, final_title, title_source, game_id or "N/A", gameid_source or "unknown", filename))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    
        f.write("Platform | Title | GameID | File\n\n")
        for p, t, _, gid, _, fn in results:
            f.write(f"{p} | {t} | {gid} | {fn}\n")

    print(f"\nDone. {len(results)} games written to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
input()