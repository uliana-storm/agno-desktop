"""
Master FastAPI server for Tony-deployed web apps.

Runs persistently on port 8090. Tony deploys dynamic web apps by
dropping files into subdirectories. The server auto-mounts them
without a restart.

No click tracking. No database. Pure server infrastructure.
"""

import importlib.util
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

PROJECTS_DIR = Path(__file__).parent / "projects"
PROJECTS_DIR.mkdir(exist_ok=True)

mounted_projects: set[str] = set()


def load_project_router(project_path: Path, app: FastAPI) -> bool:
    """
    Attempts to load and mount a project's routes.py as a sub-application.
    Returns True if successfully mounted, False otherwise.
    """
    project_name = project_path.name
    routes_file = project_path / "routes.py"

    if not routes_file.exists():
        # No routes.py — mount as static files only if HTML exists
        html_files = list(project_path.glob("*.html"))
        if html_files:
            app.mount(
                f"/projects/{project_name}",
                StaticFiles(directory=str(project_path), html=True),
                name=project_name,
            )
            mounted_projects.add(project_name)
            print(f"[project_server] Mounted static: /projects/{project_name}")
            return True
        return False

    # Load routes.py as a module
    spec = importlib.util.spec_from_file_location(
        f"projects.{project_name}.routes",
        routes_file,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"projects.{project_name}.routes"] = module

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        print(f"[project_server] Failed to load {project_name}/routes.py: {e}")
        return False

    if not hasattr(module, "router"):
        print(f"[project_server] {project_name}/routes.py has no 'router' attribute — skipping")
        return False

    app.include_router(
        module.router,
        prefix=f"/projects/{project_name}",
    )

    # Also mount static assets in the same directory
    app.mount(
        f"/projects/{project_name}/static",
        StaticFiles(directory=str(project_path)),
        name=f"{project_name}-static",
    )

    mounted_projects.add(project_name)
    print(f"[project_server] Mounted dynamic: /projects/{project_name}")
    return True


def scan_and_mount(app: FastAPI):
    """
    Scans PROJECTS_DIR and mounts any unmounted projects.
    Safe to call repeatedly.
    """
    if not PROJECTS_DIR.exists():
        return

    for project_path in PROJECTS_DIR.iterdir():
        if not project_path.is_dir():
            continue
        if project_path.name in mounted_projects:
            continue
        load_project_router(project_path, app)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Mount all existing projects on startup
    scan_and_mount(app)
    yield


app = FastAPI(
    title="Agent Server",
    description="Master server for Tony-deployed web apps",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return JSONResponse({
        "status": "running",
        "mounted_projects": sorted(list(mounted_projects)),
        "port": 8090,
    })


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


@app.post("/reload")
async def reload_projects():
    """
    Call this endpoint to scan for new projects without restarting.
    Tony can call this via ShellTools after deploying new files.
    """
    before = len(mounted_projects)
    scan_and_mount(app)
    after = len(mounted_projects)
    return JSONResponse({
        "status": "ok",
        "new_projects_mounted": after - before,
        "total_mounted": after,
    })