"""Check out a PR head into a read-only, sanitised working tree.

Layout per run:  <run_root>/src  (working tree: scanned and read by Claude)
                 <run_root>/git  (git dir, kept outside the tree)
The GitHub token only ever travels in GIT_CONFIG_* env vars: never argv, never .git/config.
"""

import base64
import os
import shutil
import stat
import subprocess
from pathlib import Path

CLAUDE_CONFIG_FILES = {"CLAUDE.md", "CLAUDE.local.md", ".mcp.json"}
# Files a PR could use to hide its own findings from the scanners.
SCANNER_CONTROL_FILES = {".semgrepignore", ".gitleaks.toml", ".gitleaksignore", "osv-scanner.toml", ".gitignore"}
REMOVED_FILES = CLAUDE_CONFIG_FILES | SCANNER_CONTROL_FILES
CLAUDE_CONFIG_DIRS = {".claude"}
GIT_TIMEOUT_S = 300


class GitError(RuntimeError):
    pass


def git_dir_for(workspace: Path) -> Path:
    return workspace.parent / "git"


def git_env(workspace: Path, token: str = "") -> dict[str, str]:
    config = [("core.symlinks", "false"), ("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false")]
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        config.append(("http.extraheader", f"Authorization: Basic {basic}"))
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "GIT_DIR": str(git_dir_for(workspace)),
        "GIT_WORK_TREE": str(workspace),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_COUNT": str(len(config)),
    }
    for i, (k, v) in enumerate(config):
        env[f"GIT_CONFIG_KEY_{i}"] = k
        env[f"GIT_CONFIG_VALUE_{i}"] = v
    return env


def run_git(workspace: Path, args: list[str], token: str = "") -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=workspace, env=git_env(workspace, token),
            capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} timed out") from None
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-3:]
        msg = " | ".join(tail)
        if token:
            msg = msg.replace(token, "***")
        raise GitError(f"git {args[0]} failed: {msg}")
    return proc.stdout


def sanitise(workspace: Path) -> list[str]:
    """Remove repo-supplied Claude config, scanner suppression files and any symlinks. Returns removed relative paths."""
    removed = []
    for root, dirs, files in os.walk(workspace, topdown=True):
        rel_root = Path(root).relative_to(workspace)
        for d in list(dirs):
            p = Path(root) / d
            if p.is_symlink() or d in CLAUDE_CONFIG_DIRS:
                p.unlink() if p.is_symlink() else shutil.rmtree(p)
                removed.append((rel_root / d).as_posix())
                dirs.remove(d)
        for f in files:
            p = Path(root) / f
            if p.is_symlink() or f in REMOVED_FILES:
                p.unlink()
                removed.append((rel_root / f).as_posix())
    return sorted(removed)


def make_read_only(workspace: Path) -> None:
    for root, dirs, files in os.walk(workspace, topdown=False):
        for f in files:
            os.chmod(Path(root) / f, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        for d in dirs:
            os.chmod(Path(root) / d, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    os.chmod(workspace, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


def checkout(remote_url: str, base_sha: str, head_sha: str, workspace: Path, token: str) -> list[str]:
    workspace.mkdir(parents=True)
    run_git(workspace, ["init", "-q"])
    run_git(workspace, ["remote", "add", "origin", remote_url])
    run_git(workspace, ["fetch", "-q", "--no-tags", "--filter=blob:none", "origin", base_sha, head_sha], token)
    run_git(workspace, ["checkout", "-q", "--detach", head_sha], token)
    removed = sanitise(workspace)
    make_read_only(workspace)
    return removed
