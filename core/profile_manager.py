"""
Profile manager — load/save/list config profiles backed by profiles/*.json.
Each profile maps a subset of .env keys; unaffected keys are left intact.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_PROFILES_DIR = Path(__file__).parent.parent / "profiles"
_ENV_FILE     = Path(__file__).parent.parent / ".env"

# Keys that profiles are allowed to touch
_PROFILE_KEYS = {
    "MAX_DRAWDOWN_PCT",
    "TRAILING_STOP_ATR_MULTIPLIER",
    "TIMEFRAME_SHORT",
    "TIMEFRAME_MEDIUM",
    "TIMEFRAME_LONG",
}

_META_KEYS = {"name", "description"}


def list_profiles() -> list[dict]:
    """Return list of all profiles with their metadata + settings."""
    _PROFILES_DIR.mkdir(exist_ok=True)
    profiles = []
    for p in sorted(_PROFILES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data["_file"] = p.stem
            profiles.append(data)
        except Exception as e:
            logger.warning(f"Gagal membaca profile {p.name}: {e}")
    return profiles


def load_profile(profile_name: str) -> dict:
    """
    Apply profile to .env. Returns the profile dict.
    Raises FileNotFoundError if profile doesn't exist.
    """
    path = _PROFILES_DIR / f"{profile_name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Profile '{profile_name}' tidak ditemukan: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    settings = {k: v for k, v in data.items() if k in _PROFILE_KEYS}

    _apply_to_env(settings)
    logger.info(f"Profile '{profile_name}' diaktifkan: {settings}")
    return data


def save_current_as_profile(name: str, description: str) -> Path:
    """
    Read current .env values and save as profiles/<slug>.json.
    Returns the path written.
    """
    slug = name.lower().replace(" ", "_").replace("/", "_")
    env  = _read_env()
    profile = {
        "name":        name,
        "description": description,
    }
    for key in _PROFILE_KEYS:
        if key in env:
            profile[key] = env[key]

    _PROFILES_DIR.mkdir(exist_ok=True)
    out = _PROFILES_DIR / f"{slug}.json"
    out.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Profile '{name}' disimpan ke {out}")
    return out


def get_active_profile() -> str | None:
    """
    Return name of the first profile whose settings match the current .env,
    or None if no exact match.
    """
    env = _read_env()
    for p in list_profiles():
        settings = {k: v for k, v in p.items() if k in _PROFILE_KEYS}
        if all(env.get(k) == v for k, v in settings.items()):
            return p.get("name", p.get("_file"))
    return None


# ── .env helpers ──────────────────────────────────────────────────────────────

def _read_env() -> dict[str, str]:
    """Parse .env file into a {KEY: VALUE} dict (no interpolation)."""
    if not _ENV_FILE.exists():
        return {}
    result = {}
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result


def _apply_to_env(settings: dict[str, str]) -> None:
    """Write key=value pairs into .env, preserving all other lines."""
    if not _ENV_FILE.exists():
        lines = []
    else:
        lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()

    remaining = dict(settings)  # keys not yet found in file
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in remaining:
            new_lines.append(f"{key}={remaining.pop(key)}")
        else:
            new_lines.append(line)

    # Append any keys that weren't already in the file
    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")

    _ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
