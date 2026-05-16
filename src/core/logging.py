"""Sistema de logging del orquestador (T-06bis).

Configuracion de logging estructurado con soporte para:
- Console output con colores (desarrollo)
- File output con rotacion (produccion)
- Formato JSON para agregacion (ELK, Loki, etc.)
- Niveles configurables por modulo
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional


# ---------------------------------------------------------------------------
# Formateadores
# ---------------------------------------------------------------------------

# Formato legible para consola (desarrollo).
CONSOLE_FORMAT = (
    "%(asctime)s  %(levelname)-8s  %(name)-25s  %(message)s"
)

# Formato compacto para archivos (produccion).
FILE_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)-25s | %(funcName)-20s | %(message)s"
)

# Formato JSON para agregacion (ELK/Loki).
JSON_FORMAT = "json"  # Sentinel value


class ColoredFormatter(logging.Formatter):
    """Formateador con colores ANSI para consola."""

    COLORS = {
        logging.DEBUG:    "\033[36m",   # cyan
        logging.INFO:     "\033[32m",   # green
        logging.WARNING:  "\033[33m",   # yellow
        logging.ERROR:    "\033[31m",   # red
        logging.CRITICAL: "\033[1;31m", # bold red
    }
    RESET = "\033[0m"

    def __init__(self, fmt: str, use_color: bool = True):
        super().__init__(fmt)
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        if not self.use_color:
            return super().format(record)

        color = self.COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        record.name = f"{color}{record.name}{self.RESET}"
        return super().format(record)


class JsonFormatter(logging.Formatter):
    """Formateador JSON para agregacion de logs."""

    def format(self, record: logging.LogRecord) -> str:
        import json as _json
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "task_id"):
            log_entry["task_id"] = getattr(record, "task_id", None)  # type: ignore[arg-type]
        return _json.dumps(log_entry, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    json_format: bool = False,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
    module_levels: Optional[dict[str, str]] = None,
) -> logging.Logger:
    """Configurar el sistema de logging.

    Args:
        level: Nivel de log global (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Ruta al archivo de log. Si None, solo consola.
        json_format: Si True, usa formato JSON (para agregadores).
        max_bytes: Tamano maximo del archivo de log antes de rotar.
        backup_count: Numero de archivos de backup a mantener.
        module_levels: Mapa de nombre_modulo -> nivel para overrides.

    Returns:
        Logger raiz configurado.
    """
    root_logger = logging.getLogger("hermes")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Limpiar handlers previos (evita duplicados en reload).
    root_logger.handlers.clear()

    # -- Console handler --
    if json_format:
        console_fmt = JsonFormatter()
    else:
        console_fmt = ColoredFormatter(CONSOLE_FORMAT, use_color=sys.stdout.isatty())

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # -- File handler (opcional) --
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        if json_format:
            file_fmt = JsonFormatter()
        else:
            file_fmt = logging.Formatter(FILE_FORMAT)

        file_handler = RotatingFileHandler(
            str(log_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(file_fmt)
        root_logger.addHandler(file_handler)

    # -- Module-level overrides --
    if module_levels:
        for mod_name, mod_level in module_levels.items():
            mod_logger = logging.getLogger(mod_name)
            mod_logger.setLevel(getattr(logging, mod_level.upper(), logging.INFO))

    # -- Silence noisy third-party loggers --
    for noisy in ("urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Obtener un logger hijo bajo el namespace 'hermes'.

    Args:
        name: Nombre del modulo (e.g. 'orchestrator.router').

    Returns:
        Logger configurado como 'hermes.<name>'.
    """
    return logging.getLogger(f"hermes.{name}")


def get_task_logger(task_id: str) -> logging.LoggerAdapter:
    """Obtener un logger adaptado que inyecta task_id en cada log.

    Args:
        task_id: ID de la tarea para contexto.

    Returns:
        LoggerAdapter con task_id en extra.
    """
    logger = get_logger(f"task.{task_id[:8]}")
    return logging.LoggerAdapter(logger, {"task_id": task_id})


# ---------------------------------------------------------------------------
# Helpers de conveniencia
# ---------------------------------------------------------------------------

def log_event(
    logger: logging.Logger,
    task_id: str,
    event: str,
    details: Optional[str] = None,
) -> None:
    """Log un evento de tarea con formato estandarizado.

    Args:
        logger: Logger a usar.
        task_id: ID de la tarea.
        event: Tipo de evento (e.g. 'started', 'completed').
        details: Detalles adicionales opcionales.
    """
    msg = f"[TASK:{task_id[:8]}] {event}"
    if details:
        msg += f" — {details}"
    logger.info(msg)
