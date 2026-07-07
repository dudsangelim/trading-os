"""Carregamento de config YAML, hash de config e git SHA."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config" / "local_arb.yaml"
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"config inválida em {path}: esperado mapping, veio {type(cfg).__name__}")
    return cfg


def config_hash(cfg: dict) -> str:
    """SHA-256 do dump JSON canônico (chaves ordenadas) da config."""
    canonical = json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def git_sha(repo_root: str | Path = REPO_ROOT, short: bool = True) -> str:
    """SHA do HEAD do repo; 'unknown' se git indisponível."""
    cmd = ["git", "rev-parse", "--short" if short else "HEAD"]
    if short:
        cmd.append("HEAD")
    try:
        out = subprocess.run(
            cmd, cwd=str(repo_root), capture_output=True, text=True, timeout=5, check=True
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"
