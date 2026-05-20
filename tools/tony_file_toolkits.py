"""Scoped file tools — one read_file / save_file API with an explicit scope."""

from pathlib import Path
from typing import Literal

from agno.tools import Toolkit
from agno.tools.file import FileTools

from server.paths import KNOWLEDGE_DIR, OUTPUT_DIR, PROJECTS_DIR

FileScope = Literal["knowledge", "projects", "output"]

_SCOPES: dict[str, tuple[Path, tuple[str, ...]]] = {
    "knowledge": (KNOWLEDGE_DIR, ("knowledge/",)),
    "projects": (PROJECTS_DIR, ("projects/",)),
    "output": (OUTPUT_DIR, ("output/",)),
}

_PATH_GUIDE = """
## File paths (scoped file tools)

Use `read_file`, `list_files`, `search_files`, and `save_file` with **scope** and a path relative to that root.

| scope | root | path examples | do not prefix path with |
|-------|------|---------------|-------------------------|
| `knowledge` | knowledge/ | index.md, core/voice.md | knowledge/ |
| `projects` | projects/ | my-project/workfile.md | projects/ |
| `output` | output/ | reports/foo.html | output/ |

Tony: `save_file` only with scope `projects`. After creating a project, append its registry entry to `projects/index.md`.
Jarvis: read-only — use `read_file` / `list_files` / `search_files` only (no `save_file`).
"""


def _strip_prefix(path: str, prefix: str) -> str:
    p = path.strip().lstrip("/")
    if p.startswith(prefix):
        return p[len(prefix) :]
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
            tools.append(self.save_file)
        save_note = (
            "save_file only supports scope=projects. "
            if allow_save
            else "Do not call save_file — read-only file access. "
        )
        super().__init__(
            name="scoped_files",
            tools=tools,
            instructions=(
                f"{agent_label} file tools: pass scope (knowledge | projects | output) and a relative path. "
                f"{save_note}"
                "Example: read_file(scope='knowledge', path='index.md')."
            ),
        )

    def _resolve(self, scope: str) -> tuple[_ScopedFiles | None, str | None]:
        key = (scope or "").strip().lower()
        if key not in self._roots:
            allowed = ", ".join(sorted(self._roots))
            return None, f"error: invalid scope '{scope}'. Use one of: {allowed}"
        return self._roots[key], None

    def read_file(self, scope: FileScope, path: str, encoding: str = "utf-8") -> str:
        """Read a file under scope (knowledge | projects | output)."""
        root, err = self._resolve(scope)
        if err:
            return err
        return root.read(path, encoding=encoding)

    def save_file(
        self,
        scope: FileScope,
        path: str,
        contents: str,
        overwrite: bool = True,
        encoding: str = "utf-8",
    ) -> str:
        """Write a file (Tony only — scope must be projects)."""
        if not self._allow_save:
            return "error: save_file is not available for this agent."
        if (scope or "").strip().lower() != "projects":
            return (
                "error: save_file only supports scope='projects'. "
                "Use FileGenerationTools for reports under output/reports/."
            )
        root, err = self._resolve(scope)
        if err:
            return err
        return root.save(contents, path, overwrite=overwrite, encoding=encoding)

    def list_files(self, scope: FileScope, directory: str = ".") -> str:
        """List files under a directory within scope."""
        root, err = self._resolve(scope)
        if err:
            return err
        return root.list_dir(directory)

    def search_files(self, scope: FileScope, pattern: str) -> str:
        """Glob search within scope (e.g. **/index.md)."""
        root, err = self._resolve(scope)
        if err:
            return err
        return root.search(pattern)


def tony_file_toolkit() -> ScopedFileToolkit:
    return ScopedFileToolkit(allow_save=True, agent_label="Tony")


def jarvis_file_toolkit() -> ScopedFileToolkit:
    return ScopedFileToolkit(allow_save=False, agent_label="Jarvis")


def tony_file_toolkits() -> list[Toolkit]:
    return [tony_file_toolkit()]


def file_path_guide() -> str:
    return _PATH_GUIDE
