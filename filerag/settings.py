"""Runtime-adjustable settings, persisted to settings.json and applied onto `config`.

Agents and the scanner read config at call time, so changes take effect without a restart.
"""
import json

from . import config

PATH = config.SETTINGS_FILE

# name -> (config attribute, type, validator)
FIELDS = {
    "chat_model": ("CHAT_MODEL", str, lambda v: bool(v.strip())),
    "temperature": ("TEMPERATURE", float, lambda v: 0.0 <= v <= 2.0),
    "num_ctx": ("NUM_CTX", int, lambda v: v in CONTEXT_CHOICES),
    "think": ("THINK", bool, lambda v: True),
    "max_tool_rounds": ("MAX_TOOL_ROUNDS", int, lambda v: 1 <= v <= 20),
    "answer_length": ("ANSWER_LENGTH", str, lambda v: v in config.ANSWER_LENGTHS),
    "api_enabled": ("API_ENABLED", bool, lambda v: True),   # external /api/v1 access (API key required)
    # mapping
    "content_index": ("CONTENT_INDEX", bool, lambda v: True),
    "embed_content": ("EMBED_CONTENT", bool, lambda v: True),
    "max_content_mb": ("MAX_CONTENT_MB", int, lambda v: 1 <= v <= 500),
    "sniff_unknown": ("SNIFF_UNKNOWN", bool, lambda v: True),
    "find_duplicates": ("FIND_DUPLICATES", bool, lambda v: True),
    "read_media": ("READ_MEDIA", bool, lambda v: True),
    "list_archives": ("LIST_ARCHIVES", bool, lambda v: True),
    "skip_hidden": ("SKIP_HIDDEN", bool, lambda v: True),
    "exclude_dirs": ("EXCLUDE_DIRS", str, lambda v: len(v) <= 4000),
}
CONTEXT_CHOICES = [8192, 16384, 24576, 32768]


def _bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def current():
    return {name: getattr(config, attr) for name, (attr, _, _) in FIELDS.items()}


def apply(values: dict):
    """Validate and apply a partial update; raises ValueError on bad input."""
    clean = {}
    for name, raw in values.items():
        if name not in FIELDS:
            raise ValueError(f"unknown setting {name!r}")
        attr, typ, ok = FIELDS[name]
        try:
            v = _bool(raw) if typ is bool else typ(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{name}: expected {typ.__name__}") from None
        if not ok(v):
            raise ValueError(f"{name}: value {raw!r} out of range")
        clean[name] = v
    for name, v in clean.items():
        setattr(config, FIELDS[name][0], v)
    return clean


def load():
    if PATH.exists():
        try:
            apply(json.loads(PATH.read_text(encoding="utf-8")))
        except (ValueError, OSError) as e:
            print(f"ignoring invalid {PATH.name}: {e}")


def save():
    PATH.write_text(json.dumps(current(), indent=2), encoding="utf-8")
