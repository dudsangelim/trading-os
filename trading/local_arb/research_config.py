"""Config das research routes: load do YAML + hash canônico.

Reusa `load_config`/`config_hash` do config.py — este módulo só fixa o path
default e valida a forma mínima (`routes:` mapping).
"""

from __future__ import annotations

from pathlib import Path

from .config import config_hash, load_config

DEFAULT_RESEARCH_CONFIG_PATH = Path(__file__).parent / "config" / "research_routes.yaml"


def load_research_config(path: str | Path = DEFAULT_RESEARCH_CONFIG_PATH) -> dict:
    cfg = load_config(path)
    routes = cfg.get("routes")
    if not isinstance(routes, dict) or not routes:
        raise ValueError(f"research config inválida em {path}: bloco 'routes' ausente/vazio")
    return cfg


def research_config_hash(cfg: dict) -> str:
    return config_hash(cfg)
