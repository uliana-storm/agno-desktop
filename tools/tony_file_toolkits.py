"""Scoped file tools — one read_file / save_file API with an explicit scope."""

import base64
import codecs
from pathlib import Path
from typing import Literal, Optional

from agno.tools import Toolkit
from agno.tools.file import FileTools

from server.paths import KNOWLEDGE_DIR, OUTPUT_DIR, PROJECTS_DIR

FileScope = Literal["knowledge", "projects", "output"]

_SCOPES: dict[str, tuple[Path, tuple[str, ...]]] = {
    "knowledge": (KNOWLEDGE_DIR, ("knowledge/",)),
    "projects":  (PROJECTS_DIR,  ("projects/",)),
    "output":    (OUTPUT_DIR,    ("output/",)),
}

# read_file: cap raw file content before it enters agent context (~3k tokens)
_MAX_READ_CHARS = 12_000

# save_file / append_file: cap inline write content per call
_MAX_INLINE_SAVE_CHARS = 2_000

# save_file_base64: cap decoded binary size
_MAX_BASE64_SIZE_MB = 10

# search_files: cap number of results returned
_MAX_SEARCH_RESULTS = 50

_VALID_ENCODINGS = {"utf-8", "utf-8-sig", "latin-1", "ascii", "iso-8859-1"}

_PATH_GUIDE = """
## File paths (scoped file tools)

Use `read_file`, `list_files`, `search_files`, and `save_file` with **scope** and a path relative to that root.

| scope | root | path examples | do not prefix path with |
|-------|------|---------------|-------------------------|
| `knowledge` | knowledge/ | index.md, core/voice.md | knowledge/ |
| `projects` | projects/ | my-project/workfile.md | projects/ |
| `output` | output/ | reports/foo.html | output/ |

Tony: `save_file` only with scope `projects`. Keep `save_file` contents under 2000 characters.
For larger bodies use `save_file_base64` or `append_file` (section by section).
After creating a project, append a registry block to `projects/index.md` (see Tony project index schema — `##`, `keywords:`, `status:`, `summary:`, `workfile:`).
Jarvis: read-only — use `read_file` / `list_files` / `search_files` only (no `save_file`).
"""


def _validate_encoding(enc: str) -> str:
    """Validate and return safe encoding, falling back to utf-8."""
    if not enc:
        return "utf-8"
    enc_lower = enc.lower().replace("_", "-")
    if enc_lower in _VALID_ENCODINGS:
        return enc
    try:
        codecs.lookup(enc)
        return enc
    except LookupError:
        return "utf-8"


def _is_safe_path(base: Path, target: Path) -> bool:
    """Check if target is within base, handling symlinks securely."""
    try:
        base_resolved   = base.resolve()
        target_resolved = target.resolve()
        for part in target_resolved.parents:
            if part.is_symlink():
                return False
        if target_resolved.is_symlink():
            return False
        target_resolved.relative_to(base_resolved)
        return True
    except (ValueError, RuntimeError, OSError):
        return False


def resolve_scoped_path(
    scope: str, path: str
) -> tuple[Optional[Path], Optional[str]]:
    """Resolve scope + relative path to an absolute Path, or return an error string."""
    key = (scope or "").strip().lower()
    if key not in _SCOPES:
        allowed = ", ".join(sorted(_SCOPES))
        return None, f"error: invalid scope '{scope}'. Use one of: {allowed}"
    base_dir, prefixes = _SCOPES[key]
    rel = path
    for prefix in prefixes:
        rel = _strip_prefix(rel, prefix)
    base_dir_resolved = base_dir.resolve()
    resolved = (base_dir_resolved / rel).resolve()
    if not _is_safe_path(base_dir_resolved, resolved):
        return None, f"error: path escapes scope root or contains symlinks: {path}"
    return resolved, None


def _strip_prefix(path: str, prefix: str) -> str:
    p = path.strip().lstrip("/")
    if p.startswith(prefix):
        return p[len(prefix):]
    return p


class _ScopedFiles:
    def __init__(self, base_dir: Path, strip_prefixes: tuple[str, ...], enable_save: bool) -> None:
        self._strip = strip_prefixes
        self._ft = FileTools(
            base_dir=base_dir.resolve(),
            enable_save_file=enable_save,
            expose_base_directory=True,
        )

    def norm(self, file_name: str) -> str:
        p = file_name
        for prefix in self._strip:
            p = _strip_prefix(p, prefix)
        return p

    def read(self, file_name: str, encoding: str = "utf-8") -> str:
        return self._ft.read_file(self.norm(file_name), encoding=encoding)

    def save(self, contents: str, file_name: str, overwrite: bool = True, encoding: str = "utf-8") -> str:
        return self._ft.save_file(contents, self.norm(file_name), overwrite=overwrite, encoding=encoding)

    def list_dir(self, directory: str = ".") -> str:
        return self._ft.list_files(directory=directory)

    def search(self, pattern: str) -> str:
        return self._ft.search_files(pattern)


class ScopedFileToolkit(Toolkit):
    """File tools with scope selecting knowledge, projects, or output root."""

    def __init__(self, *, allow_save: bool, agent_label: str) -> None:
        self._allow_save = allow_save
        self._roots: dict[str, _ScopedFiles] = {
            name: _ScopedFiles(base, prefixes, enable_save=allow_save and name == "projects")
            for name, (base, prefixes) in _SCOPES.items()
        }
        tools = [self.read_file, self.list_files, self.search_files]
        if allow_save:
            tools.extend([self.save_file, self.save_file_base64, self.append_file])
        save_note = (
            "save_file only supports scope=projects. "
            if allow_save
            else "Do not call save_file — read-only file access. "
        )
        super().__init__(
            name="scoped_files",
            tools=tools,
            add_instructions=True,
            instructions=(
                f"{agent_label} file I/O: always pass scope AND path — never use file_name alone. "
                f"{save_note}"
                "Examples: read_file(scope='projects', path='index.md'); "
                "save_file(scope='projects', path='my-project/workfile.md', contents='<short skeleton>'); "
                "append_file(scope='projects', path='my-project/workfile.md', text='\\n## draft\\n...'); "
                "save_file_base64(scope='projects', path='my-project/workfile.md', contents_base64='...')."
            ),
        )

    def _resolve(self, scope: str) -> tuple[_ScopedFiles | None, str | None]:
        key = (scope or "").strip().lower()
        if key not in self._roots:
            allowed = ", ".join(sorted(self._roots))
            return None, f"error: invalid scope '{scope}'. Use one of: {allowed}"
        return self._roots[key], None

    def read_file(self, scope: FileScope, path: str, encoding: str = "utf-8") -> str:
        """Read a file under scope (knowledge | projects | output).
        Files larger than 12,000 chars are truncated with a notice.
        """
        root, err = self._resolve(scope)
        if err:
            return err
        content = root.read(path, encoding=encoding)
        if len(content) > _MAX_READ_CHARS:
            return (
                content[:_MAX_READ_CHARS]
                + f"\n\n[truncated — {len(content):,} chars total, "
                f"showing first {_MAX_READ_CHARS:,}. "
                f"Use append_file section-by-section or request a specific section.]"
            )
        return content

    def save_file(
        self,
        scope: FileScope,
        path: str,
        contents: str,
        overwrite: bool = True,
        encoding: str = "utf-8",
    ) -> str:
        """Write a file (Tony only — scope must be projects). Keep contents under 2000 chars."""
        if not self._allow_save:
            return "error: save_file is not available for this agent."
        if len(contents) > _MAX_INLINE_SAVE_CHARS:
            return (
                f"error: contents too long ({len(contents)} chars). "
                f"Use save_file_base64 for the full file, or append_file section-by-section "
                f"(max {_MAX_INLINE_SAVE_CHARS} chars per save_file call)."
            )
        if (scope or "").strip().lower() != "projects":
            return (
                "error: save_file only supports scope='projects'. "
                "Use FileGenerationTools for reports under output/reports/."
            )
        root, err = self._resolve(scope)
        if err:
            return err
        return root.save(contents, path, overwrite=overwrite, encoding=encoding)

    def save_file_base64(
        self,
        scope: FileScope,
        path: str,
        contents_base64: str,
        overwrite: bool = True,
        encoding: str = "utf-8",
    ) -> str:
        """Write a file from base64-encoded UTF-8 (Tony only — for large workfiles/reports)."""
        if not self._allow_save:
            return "error: save_file_base64 is not available for this agent."
        if (scope or "").strip().lower() != "projects":
            return "error: save_file_base64 only supports scope='projects'."
        root, err = self._resolve(scope)
        if err:
            return err
        try:
            raw = base64.b64decode(contents_base64.strip(), validate=True)
            if len(raw) > _MAX_BASE64_SIZE_MB * 1024 * 1024:
                return f"error: decoded file too large (max {_MAX_BASE64_SIZE_MB}MB)"
            text = raw.decode(encoding)
        except Exception as e:
            return f"error: invalid base64 or decoding failed: {e}"
        safe_encoding = _validate_encoding(encoding)
        return root.save(text, path, overwrite=overwrite, encoding=safe_encoding)

    def append_file(
        self,
        scope: FileScope,
        path: str,
        text: str,
        encoding: str = "utf-8",
    ) -> str:
        """Append text to a projects file (Tony only). Keep text under 2000 chars per call."""
        if not self._allow_save:
            return "error: append_file is not available for this agent."
        if len(text) > _MAX_INLINE_SAVE_CHARS:
            return (
                f"error: append text too long ({len(text)} chars). "
                f"Split into smaller append_file calls (max {_MAX_INLINE_SAVE_CHARS} each)."
            )
        if (scope or "").strip().lower() != "projects":
            return "error: append_file only supports scope='projects'."
        resolved, err = resolve_scoped_path(scope, path)
        if err:
            return err
        safe_encoding = _validate_encoding(encoding)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, "a", encoding=safe_encoding) as f:
            f.write(text)
        return f"Appended {len(text)} chars to {resolved}."

    def list_files(self, scope: FileScope, directory: str = ".") -> str:
        """List files under a directory within scope."""
        root, err = self._resolve(scope)
        if err:
            return err
        return root.list_dir(directory)

    def search_files(self, scope: FileScope, pattern: str) -> str:
        """Glob search within scope (e.g. **/index.md). Returns up to 50 matches."""
        root, err = self._resolve(scope)
        if err:
            return err
        raw = root.search(pattern)
        # Cap results to avoid long file lists entering context
        lines = [l for l in raw.splitlines() if l.strip()]
        if len(lines) > _MAX_SEARCH_RESULTS:
            truncated = "\n".join(lines[:_MAX_SEARCH_RESULTS])
            return (
                f"{truncated}\n"
                f"[{len(lines)} total matches — showing first {_MAX_SEARCH_RESULTS}. "
                f"Narrow the pattern to see more specific results.]"
            )
        return raw


def tony_file_toolkit() -> ScopedFileToolkit:
    return ScopedFileToolkit(allow_save=True, agent_label="Tony")


def jarvis_file_toolkit() -> ScopedFileToolkit:
    return ScopedFileToolkit(allow_save=False, agent_label="Jarvis")


def tony_file_toolkits() -> list[Toolkit]:
    return [tony_file_toolkit()]


def file_path_guide() -> str:
    return _PATH_GUIDE