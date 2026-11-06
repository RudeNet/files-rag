"""Runtime settings. Every value can be overridden with an environment variable."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name, default):
    return os.environ.get(name, default)


# Data
CATALOG_DB = Path(_env("FILERAG_CATALOG_DB", ROOT / "catalog.db"))   # files, chunks, FTS index, embeddings
CHATS_DB = Path(_env("FILERAG_CHATS_DB", ROOT / "chats.db"))         # chats, notes, API keys
SETTINGS_FILE = Path(_env("FILERAG_SETTINGS", ROOT / "settings.json"))  # values changed in the UI

# Ollama (local only)
OLLAMA_HOST = _env("OLLAMA_HOST", "http://127.0.0.1:11434")
CHAT_MODEL = _env("FILERAG_CHAT_MODEL", "qwen3:14b")
EMBED_MODEL = _env("FILERAG_EMBED_MODEL", "nomic-embed-text")
# nomic-embed-text expects task prefixes; set both to "" for models that don't.
EMBED_DOC_PREFIX = _env("FILERAG_EMBED_DOC_PREFIX", "search_document: ")
EMBED_QUERY_PREFIX = _env("FILERAG_EMBED_QUERY_PREFIX", "search_query: ")

# Generation options passed to Ollama. num_gpu=999 offloads every layer to CUDA.
NUM_CTX = int(_env("FILERAG_NUM_CTX", "32768"))
NUM_GPU = int(_env("FILERAG_NUM_GPU", "999"))
TEMPERATURE = float(_env("FILERAG_TEMPERATURE", "0.2"))
THINK = _env("FILERAG_THINK", "0") == "1"   # qwen3-style reasoning mode
KEEP_ALIVE = _env("FILERAG_KEEP_ALIVE", "30m")

# Mapping: every file under a mapped folder is recorded (name, extension, size, dates). Files whose text can
# be read are also split into chunks, full-text indexed and (optionally) embedded for content search.
CONTENT_INDEX = _env("FILERAG_CONTENT_INDEX", "1") == "1"       # read file contents at all
EMBED_CONTENT = _env("FILERAG_EMBED_CONTENT", "1") == "1"       # semantic vectors (keyword search works without)
MAX_CONTENT_MB = int(_env("FILERAG_MAX_CONTENT_MB", "20"))      # larger files are mapped by name only
SNIFF_UNKNOWN = _env("FILERAG_SNIFF_UNKNOWN", "1") == "1"       # unknown extensions: index if the bytes look like text
FIND_DUPLICATES = _env("FILERAG_FIND_DUPLICATES", "1") == "1"   # hash files that share a size with another file
READ_MEDIA = _env("FILERAG_READ_MEDIA", "1") == "1"             # image size, EXIF date taken and camera
LIST_ARCHIVES = _env("FILERAG_LIST_ARCHIVES", "1") == "1"       # names inside .zip / .tar archives are searchable
SKIP_HIDDEN = _env("FILERAG_SKIP_HIDDEN", "1") == "1"           # skip dot-folders / dot-files
# Folder names never descended into (comma-separated, case-insensitive).
EXCLUDE_DIRS = _env("FILERAG_EXCLUDE_DIRS", ".git,.svn,.hg,node_modules,__pycache__,.venv,venv,.idea,.vscode,"
                    ".mypy_cache,.pytest_cache,.tox,$RECYCLE.BIN,System Volume Information")
# Semantic vectors for the first N chunks of each file (~150 KB of text); keyword search covers the whole file.
EMBED_FILE_CHUNKS = int(_env("FILERAG_EMBED_FILE_CHUNKS", "100"))
CHUNK_CHARS = int(_env("FILERAG_CHUNK_CHARS", "1500"))
CHUNK_OVERLAP = int(_env("FILERAG_CHUNK_OVERLAP", "200"))

# Indexing / retrieval
EMBED_BATCH = int(_env("FILERAG_EMBED_BATCH", "32"))
EMBED_MAX_CHARS = int(_env("FILERAG_EMBED_MAX_CHARS", "2000"))
CANDIDATES = int(_env("FILERAG_CANDIDATES", "60"))   # per retriever, before fusion
MAX_TOOL_ROUNDS = int(_env("FILERAG_MAX_TOOL_ROUNDS", "8"))
# Answer length: steers the model and caps generated tokens (writing is the slow part on a 16 GB GPU).
ANSWER_LENGTH = _env("FILERAG_ANSWER_LENGTH", "normal")
ANSWER_LENGTHS = {
    # level: (max tokens per model call, instruction)
    "concise": (400, "Answer length: CONCISE - every word costs time, so be terse. Lead with the direct answer in "
                     "one sentence, then at most 5 short bullets with citations. No introduction, no closing "
                     "summary, no generic advice. The user can ask for details."),
    "normal": (1200, "Answer length: NORMAL. Be focused: about 300 words, or a table plus a few bullets. Avoid "
                     "filler, repetition and generic advice."),
    "detailed": (-1, "Answer length: DETAILED. Thorough analysis is welcome when useful, but no filler."),
}


def answer_length():
    return ANSWER_LENGTHS.get(ANSWER_LENGTH, ANSWER_LENGTHS["normal"])


def exclude_dirs():
    return {d.strip().lower() for d in EXCLUDE_DIRS.split(",") if d.strip()}


# External API (/api/v1, API-key protected); off until enabled in Settings.
API_ENABLED = _env("FILERAG_API_ENABLED", "0") == "1"
# Tokens of NUM_CTX kept free for tool schemas (~1k) and the model's answer.
CONTEXT_RESERVE = int(_env("FILERAG_CONTEXT_RESERVE", "3500"))


def ollama_options():
    return {"num_ctx": NUM_CTX, "num_gpu": NUM_GPU, "temperature": TEMPERATURE}
