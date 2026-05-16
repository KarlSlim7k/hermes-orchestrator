"""Configuracion del orquestador (T-06bis).

Carga y valida la configuracion del sistema desde archivos YAML/JSON,
variables de entorno y valores por defecto.
"""

import os
import json
from pathlib import Path
from typing import Optional, Any, Dict

try:
    import yaml  # type: ignore[import-not-found]
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    yaml = None  # type: ignore[assignment]

from src.core.models import (
    SystemConfig,
    AgentConfig,
    AgentCapability,
    SecurityPolicy,
    ChannelConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "").lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_str(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name) or default


# ---------------------------------------------------------------------------
# Carga desde archivo
# ---------------------------------------------------------------------------

def _load_file(path: str) -> dict:
    """Cargar configuracion desde un archivo YAML o JSON."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    suffix = p.suffix.lower()

    if suffix in (".yaml", ".yml"):
        if not HAS_YAML:
            raise ImportError(
                "PyYAML is required for YAML config files. "
                "Install with: pip install pyyaml"
            )
        with open(p, "r") as f:
            return yaml.safe_load(f) or {}  # type: ignore[union-attr]

    if suffix == ".json":
        with open(p, "r") as f:
            return json.load(f)

    raise ValueError(f"Unsupported config format: {suffix}. Use .yaml, .yml, or .json")


# ---------------------------------------------------------------------------
# Merge de overrides desde variables de entorno
# ---------------------------------------------------------------------------

_ENV_PREFIX = "HERMES"


def _apply_env_overrides(data: dict) -> dict:
    """Aplicar overrides desde variables de entorno HERMES_*."""
    # Canales
    if _env_bool(f"{_ENV_PREFIX}_TELEGRAM_ENABLED"):
        data.setdefault("channels", {})["telegram_enabled"] = True
    token = _env_str(f"{_ENV_PREFIX}_TELEGRAM_TOKEN")
    if token:
        data.setdefault("channels", {})["telegram_token"] = token
    if _env_bool(f"{_ENV_PREFIX}_WEB_ENABLED"):
        data.setdefault("channels", {})["web_enabled"] = True
    port = _env_int(f"{_ENV_PREFIX}_WEB_PORT", 0)
    if port:
        data.setdefault("channels", {})["web_port"] = port

    # Seguridad
    if _env_bool(f"{_ENV_PREFIX}_CONFIRM_COMMIT"):
        data.setdefault("security", {})["require_confirmation_for_commit"] = True
    if _env_bool(f"{_ENV_PREFIX}_CONFIRM_PUSH"):
        data.setdefault("security", {})["require_confirmation_for_push"] = True
    if _env_bool(f"{_ENV_PREFIX}_CONFIRM_PR"):
        data.setdefault("security", {})["require_confirmation_for_pr"] = True
    max_tasks = _env_int(f"{_ENV_PREFIX}_MAX_CONCURRENT_TASKS", 0)
    if max_tasks:
        data.setdefault("security", {})["max_concurrent_tasks"] = max_tasks

    # Repo
    repo = _env_str(f"{_ENV_PREFIX}_REPO_PATH")
    if repo:
        data["repository_path"] = repo

    # Default agent
    default = _env_str(f"{_ENV_PREFIX}_DEFAULT_AGENT")
    if default:
        data["default_agent"] = default

    return data


# ---------------------------------------------------------------------------
# Configuracion por defecto
# ---------------------------------------------------------------------------

def _default_config() -> dict:
    """Retornar configuracion por defecto."""
    return {
        "default_agent": "opencode",
        "repository_path": os.getcwd(),
        "security": {
            "require_confirmation_for_commit": True,
            "require_confirmation_for_push": True,
            "require_confirmation_for_pr": True,
            "max_concurrent_tasks": 3,
        },
        "channels": {
            "telegram_enabled": False,
            "telegram_token": None,
            "web_enabled": False,
            "web_port": 8000,
        },
        "agents": [
            {
                "name": "opencode",
                "cli_command": "opencode",
                "capabilities": ["analysis", "planning", "editing", "testing", "git_ops"],
                "supports_progress": True,
                "progress_pattern": None,
                "timeout_seconds": 600,
                "workdir": None,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Carga de capabilities desde strings
# ---------------------------------------------------------------------------

def _parse_capabilities(caps: list[str]) -> list[AgentCapability]:
    """Convertir lista de strings a AgentCapability enums."""
    result = []
    for c in caps:
        try:
            result.append(AgentCapability(c.lower()))
        except ValueError:
            pass  # Ignorar capacidades desconocidas
    return result


def _build_agent(agent_data: dict) -> AgentConfig:
    """Construir AgentConfig desde un dict."""
    caps = agent_data.get("capabilities", [])
    if isinstance(caps[0], str) if caps else False:
        caps = _parse_capabilities(caps)
    return AgentConfig(
        name=agent_data["name"],
        cli_command=agent_data.get("cli_command", agent_data["name"]),
        capabilities=caps,
        supports_progress=agent_data.get("supports_progress", False),
        progress_pattern=agent_data.get("progress_pattern"),
        timeout_seconds=agent_data.get("timeout_seconds", 600),
        workdir=agent_data.get("workdir"),
    )


def _build_security(data: dict) -> SecurityPolicy:
    return SecurityPolicy(
        require_confirmation_for_commit=data.get("require_confirmation_for_commit", True),
        require_confirmation_for_push=data.get("require_confirmation_for_push", True),
        require_confirmation_for_pr=data.get("require_confirmation_for_pr", True),
        max_concurrent_tasks=data.get("max_concurrent_tasks", 3),
    )


def _build_channels(data: dict) -> ChannelConfig:
    return ChannelConfig(
        telegram_enabled=data.get("telegram_enabled", False),
        telegram_token=data.get("telegram_token"),
        web_enabled=data.get("web_enabled", False),
        web_port=data.get("web_port", 8000),
    )


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def load_config(
    config_path: Optional[str] = None,
    env_overrides: bool = True,
) -> SystemConfig:
    """Cargar la configuracion del sistema.

    Orden de precedencia (mayor a menor):
    1. Variables de entorno HERMES_*
    2. Archivo de configuracion (YAML/JSON)
    3. Valores por defecto

    Args:
        config_path: Ruta al archivo de configuracion. Si None, usa defaults.
        env_overrides: Si True, aplica overrides de variables de entorno.

    Returns:
        SystemConfig validada.

    Raises:
        FileNotFoundError: Si el archivo no existe.
        ValidationError: Si la configuracion es invalida.
    """
    # 1. Start with defaults
    data = _default_config()

    # 2. Merge file config
    if config_path:
        file_data = _load_file(config_path)
        _deep_merge(data, file_data)

    # 3. Apply env overrides
    if env_overrides:
        data = _apply_env_overrides(data)

    # 4. Build model
    agents = [_build_agent(a) for a in data.get("agents", [])]
    security = _build_security(data.get("security", {}))
    channels = _build_channels(data.get("channels", {}))

    return SystemConfig(
        agents=agents,
        default_agent=data.get("default_agent", "opencode"),
        repository_path=data.get("repository_path", os.getcwd()),
        security=security,
        channels=channels,
    )


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base in-place, recursing into nested dicts."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def save_config(config: SystemConfig, path: str) -> None:
    """Guardar configuracion a un archivo YAML o JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "default_agent": config.default_agent,
        "repository_path": config.repository_path,
        "security": config.security.model_dump(),
        "channels": config.channels.model_dump(),
        "agents": [
            {
                "name": a.name,
                "cli_command": a.cli_command,
                "capabilities": [c.value for c in a.capabilities],
                "supports_progress": a.supports_progress,
                "progress_pattern": a.progress_pattern,
                "timeout_seconds": a.timeout_seconds,
                "workdir": a.workdir,
            }
            for a in config.agents
        ],
    }

    suffix = p.suffix.lower()
    if suffix in (".yaml", ".yml"):
        if not HAS_YAML:
            raise ImportError("PyYAML is required. pip install pyyaml")
        with open(p, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)  # type: ignore[union-attr]
    elif suffix == ".json":
        with open(p, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    else:
        raise ValueError(f"Unsupported format: {suffix}. Use .yaml or .json")


def get_agent_config(config: SystemConfig, name: Optional[str] = None) -> AgentConfig:
    """Obtener la configuracion de un agente por nombre.

    Args:
        config: SystemConfig cargada.
        name: Nombre del agente. Si None, usa el default_agent.

    Returns:
        AgentConfig del agente solicitado.

    Raises:
        ValueError: Si el agente no existe.
    """
    target = name or config.default_agent
    for agent in config.agents:
        if agent.name == target:
            return agent
    raise ValueError(f"Agent '{target}' not found in config")
