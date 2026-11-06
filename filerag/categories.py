"""File categories by extension. Every mapped file gets one, so files can be found and counted by kind."""

CATEGORIES = {
    # category: (label, extensions)
    "document": ("Documents", "pdf doc docx odt rtf pages epub mobi djvu xps oxps wpd"),
    "spreadsheet": ("Spreadsheets", "xls xlsx xlsm xlsb ods numbers csv tsv"),
    "presentation": ("Presentations", "ppt pptx pptm odp key"),
    "text": ("Text & notes", "txt md markdown rst log nfo text org adoc asciidoc tex rtx srt vtt sub"),
    "code": ("Source code", "py pyw ipynb js mjs cjs jsx ts tsx java kt kts scala groovy c h cc cpp cxx hpp hh hxx "
             "cs vb fs go rs swift m mm rb php pl pm lua r jl dart ex exs erl hs clj elm sh bash zsh fish ps1 psm1 "
             "psd1 bat cmd vbs asm s sql html htm xhtml css scss sass less vue svelte astro gradle cmake mk make "
             "dockerfile tf hcl proto graphql gql sol v sv vhd vhdl zig nim cr"),
    "config": ("Config & data", "json jsonl ndjson yaml yml toml ini cfg conf config env properties xml xsd xsl "
               "plist reg inf csproj sln vcxproj props targets lock editorconfig gitignore gitattributes "
               "npmrc"),
    "email": ("E-mail", "eml msg mbox emlx"),
    "image": ("Images", "jpg jpeg png gif bmp tif tiff webp heic heif svg ico psd ai eps raw cr2 nef arw dng "
              "xcf avif jfif"),
    "audio": ("Audio", "mp3 wav flac aac ogg oga m4a wma opus aiff aif mid midi"),
    "video": ("Video", "mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp vob"),
    "archive": ("Archives", "zip rar 7z tar gz tgz bz2 xz zst lz cab iso img dmg jar war whl nupkg apk"),
    "executable": ("Programs", "exe msi dll sys com scr app deb rpm appimage so dylib bin elf ocx drv"),
    "database": ("Databases", "db sqlite sqlite3 mdb accdb dbf frm ibd parquet feather h5 hdf5 pkl pickle "
                 "npy npz mat sav db-shm db-wal sqlite-shm sqlite-wal"),
    "build": ("Build output", "o obj lst ihx hex rel rst sym noi cdb map lk adb pdb ilk exp tlog lastbuildstate "
              "dep idb ipch pch gch class pyc pyo elc a lib nupkg_metadata cache_v2"),
    "font": ("Fonts", "ttf otf woff woff2 eot fon"),
    "design": ("3D & design", "dwg dxf stl obj fbx blend 3ds skp step stp iges igs sketch fig xd indd"),
    "disk": ("System & temp", "tmp temp bak old swp cache crdownload part dmp lnk url ds_store"),
}

LABELS = {k: v[0] for k, v in CATEGORIES.items()} | {"other": "Other"}
EXT_CATEGORY = {ext: cat for cat, (_, exts) in CATEGORIES.items() for ext in exts.split()}
# file names without a (useful) extension
NAME_CATEGORY = {"dockerfile": "code", "makefile": "code", "cmakelists.txt": "code", "readme": "text",
                 "license": "text", "changelog": "text", ".gitignore": "config", ".env": "config"}
ORDER = list(CATEGORIES) + ["other"]


def ext_of(name: str) -> str:
    """Lower-case extension without the dot ('' when none). '.gitignore' -> 'gitignore'."""
    n = name.lower()
    if "." not in n.strip("."):
        return n.lstrip(".") if n.startswith(".") else ""
    return n.rsplit(".", 1)[1]


def category_of(name: str) -> str:
    n = name.lower()
    if n in NAME_CATEGORY:
        return NAME_CATEGORY[n]
    return EXT_CATEGORY.get(ext_of(name), "other")


def resolve(value: str | None):
    """A category id from a loose user/model value ('Images', 'image', 'photos', 'code') or None."""
    if not value:
        return None
    v = value.strip().lower().rstrip("s")
    aliases = {"photo": "image", "picture": "image", "pic": "image", "music": "audio", "sound": "audio",
               "movie": "video", "doc": "document", "word": "document", "excel": "spreadsheet",
               "sheet": "spreadsheet", "powerpoint": "presentation", "slide": "presentation", "source": "code",
               "program": "executable", "app": "executable", "binarie": "executable", "zip": "archive",
               "compressed": "archive", "mail": "email", "note": "text", "setting": "config", "data": "config",
               "3d": "design", "cad": "design", "temp": "disk", "system": "disk"}
    for cat, label in LABELS.items():
        if v in (cat, cat.rstrip("s"), label.lower().rstrip("s")):
            return cat
    return aliases.get(v)
