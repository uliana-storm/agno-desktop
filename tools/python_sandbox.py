"""Restricted Python execution for Tony — stdout capture, scoped file I/O, no pip install."""

import builtins as _builtins
import io
import runpy
import signal
from contextlib import redirect_stderr, redirect_stdout
from functools import wraps
from pathlib import Path
from typing import Any, Optional

from agno.tools.python import PythonTools, warn
from agno.utils.log import log_debug, log_info, logger

_MAX_OUTPUT_CHARS = 50_000

_ALLOWED_BUILTINS = (
    "abs",
    "all",
    "any",
    "bool",
    "bytes",
    "chr",
    "dict",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "hash",
    "hex",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "oct",
    "ord",
    "pow",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "type",
    "zip",
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "EOFError",
    "Exception",
    "FileNotFoundError",
    "ImportError",
    "IndexError",
    "KeyError",
    "LookupError",
    "MemoryError",
    "NameError",
    "OSError",
    "OverflowError",
    "PermissionError",
    "RuntimeError",
    "StopIteration",
    "SyntaxError",
    "TypeError",
    "ValueError",
    "ZeroDivisionError",
)

_BLOCKED_IMPORTS = frozenset({
    "subprocess",
    "shutil",
    "os",
    "socket",
    "urllib",
    "http",
    "ftplib",
    "telnetlib",
    "ssl",
    "ctypes",
    "mmap",
    "resource",
    "gc",
})


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    top_level = name.split(".", 1)[0]
    if top_level in _BLOCKED_IMPORTS:
        raise ImportError(f"Import of '{name}' is not allowed in the sandbox")
    return _builtins.__import__(name, globals, locals, fromlist, level)


def _safe_open(base_dir: Path):
    base_dir = base_dir.resolve()
    real_open = _builtins.open

    def open(path, mode="r", *args, **kwargs):  # noqa: A001
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = (base_dir / resolved).resolve()
        else:
            resolved = resolved.resolve()
        try:
            resolved.relative_to(base_dir)
        except ValueError:
            raise PermissionError(
                f"File access outside sandbox directory is not allowed: {path}"
            ) from None
        return real_open(resolved, mode, *args, **kwargs)

    return open


def build_safe_globals(base_dir: Path) -> dict[str, Any]:
    builtins_dict = {
        name: getattr(_builtins, name)
        for name in _ALLOWED_BUILTINS
        if hasattr(_builtins, name)
    }
    builtins_dict["__import__"] = _safe_import
    builtins_dict["open"] = _safe_open(base_dir)
    return {"__builtins__": builtins_dict}


class _TimeoutError(Exception):
    pass


def _timeout(seconds=30):
    """Decorator to add timeout to function execution using SIGALRM."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            def handler(signum, frame):
                raise _TimeoutError(f"Code execution exceeded {seconds} seconds")
            old_handler = signal.signal(signal.SIGALRM, handler)
            signal.alarm(seconds)
            try:
                return func(*args, **kwargs)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
        return wrapper
    return decorator


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    # Account for truncation suffix length to stay under limit
    suffix = "\n...(truncated)"
    return text[:limit - len(suffix)] + suffix


def _format_run_result(
    stdout: str,
    stderr: str,
    variable_to_return: Optional[str],
    locals_after: dict[str, Any],
) -> str:
    parts: list[str] = []
    out = _truncate(stdout)
    err = _truncate(stderr)
    if out:
        parts.append(out)
    if err:
        parts.append(f"[stderr]\n{err}")

    if variable_to_return:
        variable_value = locals_after.get(variable_to_return)
        if variable_value is None:
            return f"Variable {variable_to_return} not found"
        parts.append(f"[{variable_to_return}]\n{_truncate(str(variable_value))}")

    if parts:
        return "\n".join(parts)
    return "successfully ran python code (no output)"


class SandboxPythonTools(PythonTools):
    """PythonTools with working builtins, captured stdout, and pip install disabled."""

    def __init__(self, base_dir: Path, **kwargs):
        resolved = base_dir.resolve()
        super().__init__(
            base_dir=resolved,
            safe_globals=build_safe_globals(resolved),
            safe_locals={},
            restrict_to_base_dir=True,
            add_instructions=True,
            instructions=(
                "Python sandbox cwd is output/ only. Do not read or write projects/ or knowledge/ "
                "paths here — use scoped read_file / save_file / append_file instead."
            ),
            exclude_tools=[
                "pip_install_package",
                "uv_pip_install_package",
                "read_file",
                "list_files",
            ],
            **kwargs,
        )

    @_timeout(30)
    def run_python_code(self, code: str, variable_to_return: Optional[str] = None) -> str:
        try:
            warn()
            log_debug(f"Running sandbox code:\n\n{code}\n\n")
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                exec(code, self.safe_globals, self.safe_locals)
            return _format_run_result(
                stdout_buf.getvalue(),
                stderr_buf.getvalue(),
                variable_to_return,
                self.safe_locals,
            )
        except _TimeoutError as e:
            return f"Error: Code execution timeout - {e}"
        except Exception as e:
            logger.exception("Error running python code")
            return f"Error running python code: {e}"

    def save_to_file_and_run(
        self,
        file_name: str,
        code: str,
        variable_to_return: Optional[str] = None,
        overwrite: bool = True,
    ) -> str:
        try:
            warn()
            safe, file_path = self._check_path(file_name, self.base_dir, self.restrict_to_base_dir)
            if not safe:
                return f"Error: Path '{file_name}' is outside the allowed base directory"
            log_debug(f"Saving code to {file_path}")
            if not file_path.parent.exists():
                file_path.parent.mkdir(parents=True, exist_ok=True)
            if file_path.exists() and not overwrite:
                return f"File {file_name} already exists"
            file_path.write_text(code, encoding="utf-8")
            log_info(f"Saved: {file_path}")
            log_info(f"Running {file_path}")

            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                globals_after_run = runpy.run_path(
                    str(file_path),
                    init_globals=self.safe_globals,
                    run_name="__main__",
                )

            if variable_to_return:
                variable_value = globals_after_run.get(variable_to_return)
                if variable_value is None:
                    return f"Variable {variable_to_return} not found"
                return _format_run_result(
                    stdout_buf.getvalue(),
                    stderr_buf.getvalue(),
                    variable_to_return,
                    {variable_to_return: variable_value},
                )
            return _format_run_result(
                stdout_buf.getvalue(),
                stderr_buf.getvalue(),
                None,
                {},
            )
        except Exception as e:
            logger.exception("Error saving and running code")
            return f"Error saving and running code: {e}"

    def run_python_file_return_variable(
        self, file_name: str, variable_to_return: Optional[str] = None
    ) -> str:
        try:
            warn()
            safe, file_path = self._check_path(file_name, self.base_dir, self.restrict_to_base_dir)
            if not safe:
                return f"Error: Path '{file_name}' is outside the allowed base directory"
            log_info(f"Running {file_path}")

            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                globals_after_run = runpy.run_path(
                    str(file_path),
                    init_globals=self.safe_globals,
                    run_name="__main__",
                )

            if variable_to_return:
                variable_value = globals_after_run.get(variable_to_return)
                if variable_value is None:
                    return f"Variable {variable_to_return} not found"
                return _format_run_result(
                    stdout_buf.getvalue(),
                    stderr_buf.getvalue(),
                    variable_to_return,
                    {variable_to_return: variable_value},
                )
            return _format_run_result(
                stdout_buf.getvalue(),
                stderr_buf.getvalue(),
                None,
                {},
            )
        except Exception as e:
            logger.exception("Error running file")
            return f"Error running file: {e}"
