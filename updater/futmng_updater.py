# -*- coding: utf-8 -*-
"""FUTMNG self-updater.

Launched by FUTMNG after a GitHub Release ZIP has been downloaded. It waits for
FUTMNG to close, backs up files that will be overwritten, overlays the new
release, then restarts the application.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # tasklist is present on supported Windows versions and needs no package.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=flags,
        )
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_for_exit(pid: int, timeout: float = 30.0) -> None:
    end = time.time() + timeout
    while time.time() < end and process_alive(pid):
        time.sleep(0.25)
    # Give Windows a moment to release Python/Tk resources.
    time.sleep(0.5)


def locate_payload(extract_root: Path) -> Path:
    """Find the folder that actually contains app/FUTMNG.py."""
    direct = extract_root / "app" / "FUTMNG.py"
    if direct.exists():
        return extract_root
    candidates = []
    for child in extract_root.iterdir():
        if child.is_dir() and (child / "app" / "FUTMNG.py").exists():
            candidates.append(child)
    if len(candidates) == 1:
        return candidates[0]
    for p in extract_root.rglob("FUTMNG.py"):
        if p.parent.name == "app":
            return p.parent.parent
    raise RuntimeError("Le ZIP ne contient pas une installation FUTMNG valide (app/FUTMNG.py absent).")


def should_skip(rel: Path) -> bool:
    parts = {part.lower() for part in rel.parts}
    return bool(parts & {".git", "backups", "cache", "user-data", "userdata", "logs"})


def overlay(payload: Path, install_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = install_root / "backups" / f"before-update-{stamp}"
    copied = 0

    for src in payload.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(payload)
        if should_skip(rel):
            continue
        dst = install_root / rel
        if dst.exists():
            backup = backup_root / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    if copied == 0:
        raise RuntimeError("Aucun fichier FUTMNG n'a été copié depuis la mise à jour.")
    return backup_root


def restart_futmng(install_root: Path) -> None:
    bat = install_root / "OUVRIR FUTMNG.bat"
    if os.name == "nt" and bat.exists():
        os.startfile(str(bat))  # type: ignore[attr-defined]
        return
    app = install_root / "app" / "FUTMNG.py"
    subprocess.Popen([sys.executable, str(app)], cwd=str(install_root))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True, dest="zip_path")
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()

    zip_path = Path(args.zip_path).resolve()
    install_root = Path(args.install_root).resolve()
    log_dir = install_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "update-last.log"

    try:
        wait_for_exit(args.pid)
        if not zip_path.exists():
            raise RuntimeError(f"ZIP de mise à jour introuvable : {zip_path}")
        with tempfile.TemporaryDirectory(prefix="futmng-update-") as tmp:
            extract_root = Path(tmp)
            with zipfile.ZipFile(zip_path, "r") as archive:
                archive.extractall(extract_root)
            payload = locate_payload(extract_root)
            backup = overlay(payload, install_root)
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass
        log_file.write_text(
            f"OK\nInstallation: {install_root}\nSauvegarde: {backup}\nDate: {datetime.now().isoformat()}\n",
            encoding="utf-8",
        )
        if args.restart:
            restart_futmng(install_root)
        return 0
    except Exception as exc:
        log_file.write_text(
            f"ERROR\n{exc}\nDate: {datetime.now().isoformat()}\n",
            encoding="utf-8",
        )
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, str(exc), "FUTMNG - Mise à jour", 0x10)
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
