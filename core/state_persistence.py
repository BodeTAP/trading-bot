"""
State persistence — simpan/muat state kritis bot ke disk secara atomik.

State yang disimpan:
  - trailing_stop  : entry_price, highest, stop, atr, multiplier, pair
  - take_profit    : entry_price, target_price, take_profit_pct
  - pause_until    : waktu resume jika bot sedang dijeda

Tulis dilakukan secara atomik (tulis ke .tmp lalu rename) sehingga
file tidak corrupt jika server mati di tengah operasi write.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("logs/bot_state.json")


class StateStore:
    def __init__(self, filepath: Path = _DEFAULT_PATH):
        self._path = Path(filepath)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self._data["saved_at"] = datetime.now().isoformat()
        self._flush()

    def clear_key(self, key: str) -> None:
        if key in self._data:
            del self._data[key]
            self._data["saved_at"] = datetime.now().isoformat()
            self._flush()

    def all(self) -> dict:
        return dict(self._data)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"StateStore: gagal membaca {self._path}: {e} — mulai dari state kosong")
            return {}

    def _flush(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except Exception as e:
            logger.error(f"StateStore: gagal menyimpan state: {e}")


# Module-level singleton — dipakai oleh executor dan main
store = StateStore()
