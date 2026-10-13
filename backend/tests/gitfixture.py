"""Builds a local bare 'GitHub' remote with a base commit and a PR head commit."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]


def git(cwd, *args) -> str:
    return subprocess.run(["git", *GIT_ID, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@dataclass
class Remote:
    url: str
    base_sha: str
    head_sha: str
    pr: int = 1


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def make_remote(tmp_path: Path, head_files: dict[str, str] | None = None, symlinks: dict[str, str] | None = None) -> Remote:
    src = tmp_path / "src-repo"
    src.mkdir()
    git(src, "init", "-q")
    write(src, "app.py", "import os\n\n\ndef main():\n    return 1\n")
    write(src, "lib/util.py", "def f():\n    return 2\n")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "base")
    base_sha = git(src, "rev-parse", "HEAD")

    git(src, "checkout", "-q", "-b", "feature")
    # modify line 5 of app.py, append two lines to util.py
    write(src, "app.py", "import os\n\n\ndef main():\n    return os.system('x')\n")
    write(src, "lib/util.py", "def f():\n    return 2\n\n\ndef g(x):\n    return eval(x)\n")
    for rel, text in (head_files or {}).items():
        write(src, rel, text)
    for rel, target in (symlinks or {}).items():
        os.symlink(target, src / rel)
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "head")
    head_sha = git(src, "rev-parse", "HEAD")

    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    git(bare, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(bare, "config", "uploadpack.allowFilter", "true")
    git(bare, "update-ref", "refs/pull/1/head", head_sha)
    return Remote(url=f"file://{bare}", base_sha=base_sha, head_sha=head_sha)
