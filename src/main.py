"""Entry point principal del orquestador Hermes.

Coordina todos los componentes:
  Config → TaskManager → Router → Agents → Notifier → Interfaces

Uso:
  python -m src.main                    # Con config por defecto
  python -m src.main --config path.yaml # Con archivo de config
  python -m src.main --web-only         # Solo panel web
  python -m src.main --no-web           # Sin panel web
"""

import argparse
import asyncio
import signal
import sys
import os
from pathlib import Path
from typing import Optional

from src.core.models import AgentConfig
from src.core.config import load_config, get_agent_config, SystemConfig
from src.core.logging import setup_logging, get_logger
from src.orchestrator.task_manager import TaskManager
from src.orchestrator.router import IntentRouter
from src.interfaces.telegram import TelegramBot, TaskExecutor
from src.interfaces.web.app import WebApp
from src.notifications.notifier import Notifier, ConsoleChannel
from src.notifications.channels import TelegramChannel
from src.core.models import NotificationChannel as NC
from src.agents.base import BaseAgent

logger = get_logger("main")


class HermesOrchestrator:
    """Orquestador principal que conecta todos los componentes."""

    def __init__(self, config: SystemConfig, db_path: str = ":memory:"):
        """
        Args:
            config: Configuracion del sistema.
            db_path: Ruta de la base de datos SQLite.
        """
        self.config = config
        self.task_manager = TaskManager(db_path=db_path)
        self.notifier = Notifier()
        self.agent_registry: dict[str, BaseAgent] = {}
        self.router: Optional[IntentRouter] = None
        self.executor: Optional[TaskExecutor] = None
        self.telegram_bot: Optional[TelegramBot] = None
        self.web_app: Optional[WebApp] = None
        self._running = False

    def setup(self, register_default_agent: bool = True):
        """Inicializar todos los componentes.

        Args:
            register_default_agent: Si True, registra el agente de la config.
        """
        # Registrar canales de notificacion
        self._setup_notifier()

        # Registrar agentes
        if register_default_agent:
            self._setup_agents()

        # Crear router
        agent_configs = self.config.agents
        self.router = IntentRouter(
            agents=agent_configs,
            task_manager=self.task_manager,
        )

        # Crear executor
        self.executor = TaskExecutor(
            task_manager=self.task_manager,
            agent_registry=self.agent_registry,
            notifier=self.notifier,
        )

        logger.info(
            f"Orchestrator setup complete: "
            f"{len(self.agent_registry)} agents, "
            f"{len(self.notifier.get_channels())} channels"
        )

    def _setup_notifier(self):
        """Configurar canales de notificacion."""
        # Console channel siempre activo (fallback)
        self.notifier.register(ConsoleChannel())

        # Telegram channel si esta configurado
        if self.config.channels.telegram_enabled and self.config.channels.telegram_token:
            tg = TelegramChannel(
                token=self.config.channels.telegram_token,
                chat_id="0",  # Se sobreescribe cuando el bot se conecta
                enabled=True,
            )
            self.notifier.register(tg)
            logger.info("Telegram notification channel registered")

    def _setup_agents(self):
        """Registrar agentes desde la configuracion."""
        for agent_cfg in self.config.agents:
            try:
                agent = self._build_agent(agent_cfg)
                self.agent_registry[agent_cfg.name] = agent
                logger.info(f"Agent '{agent_cfg.name}' registered")
            except Exception as e:
                logger.warning(f"Failed to register agent '{agent_cfg.name}': {e}")

    def _build_agent(self, config: AgentConfig) -> BaseAgent:
        """Construir un agente desde su configuracion."""
        # Import aqui para evitar circular imports
        from src.agents.opencode_adapter import OpenCodeAdapter
        from src.agents.codex_adapter import CodexAdapter

        adapters = {
            "opencode": OpenCodeAdapter,
            "codex": CodexAdapter,
        }

        adapter_class = adapters.get(config.name.lower())
        if adapter_class is None:
            raise ValueError(f"No adapter for agent: {config.name}")

        return adapter_class(
            cli_command=config.cli_command,
            workdir=config.workdir,
            timeout_seconds=config.timeout_seconds,
            auto_approve=True,
        )

    def start_web(self, host: str = "0.0.0.0", port: Optional[int] = None):
        """Iniciar el panel web."""
        effective_port = port or self.config.channels.web_port
        self.web_app = WebApp(
            task_manager=self.task_manager,
            notifier=self.notifier,
            host=host,
            port=effective_port,
        )
        self.web_app.start(background=True)
        logger.info(f"Web panel started on http://{host}:{effective_port}")

    def start_telegram(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        """Iniciar el bot de Telegram."""
        effective_token = token or self.config.channels.telegram_token
        if not effective_token:
            logger.warning("No Telegram token configured, skipping bot")
            return

        self.telegram_bot = TelegramBot(
            token=effective_token,
            router=self.router,
            executor=self.executor,
            notifier=self.notifier,
            chat_id=chat_id,
            default_repository=self.config.repository_path,
        )
        # Iniciar polling en background
        asyncio.create_task(self.telegram_bot.run())
        logger.info("Telegram bot started")

    def stop(self):
        """Detener todos los componentes."""
        self._running = False
        if self.web_app:
            self.web_app.stop()
        if self.telegram_bot:
            self.telegram_bot.stop()
        logger.info("Orchestrator stopped")

    def run(self, web: bool = True, telegram: bool = True):
        """Loop principal.

        Args:
            web: Iniciar panel web.
            telegram: Iniciar bot de Telegram.
        """
        self._running = True

        if web:
            self.start_web()

        if telegram:
            self.start_telegram()

        # Mantener vivo hasta senal de interrupcion
        self._wait_for_signal()

    def _wait_for_signal(self):
        """Esperar Ctrl+C o SIGTERM."""
        def _signal_handler(sig, frame):
            logger.info("Shutdown signal received")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        logger.info("Orchestrator running. Press Ctrl+C to stop.")
        try:
            while self._running:
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Hermes Orchestrator")
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Ruta al archivo de configuracion (YAML/JSON)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=":memory:",
        help="Ruta a la base de datos SQLite",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host para el panel web",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=None,
        help="Puerto para el panel web",
    )
    parser.add_argument(
        "--telegram-token",
        type=str,
        default=None,
        help="Token del bot de Telegram (override)",
    )
    parser.add_argument(
        "--chat-id",
        type=str,
        default=None,
        help="Chat ID permitido (override)",
    )
    parser.add_argument(
        "--web-only",
        action="store_true",
        help="Iniciar solo el panel web",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="No iniciar el panel web",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Nivel de log",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Archivo de log",
    )
    parser.add_argument(
        "--json-log",
        action="store_true",
        help="Usar formato JSON para logs",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Setup logging
    setup_logging(
        level=args.log_level,
        log_file=args.log_file,
        json_format=args.json_log,
    )

    # Load config
    config_path = args.config
    if config_path is None:
        # Buscar config.yaml en directorio actual o config/
        for candidate in ["config.yaml", "config/config.yaml"]:
            if os.path.exists(candidate):
                config_path = candidate
                break

    try:
        if config_path:
            config = load_config(config_path)
            logger.info(f"Loaded config from {config_path}")
        else:
            config = load_config(config_path=None)
            logger.info("Using default configuration")
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)

    # Apply CLI overrides
    if args.telegram_token:
        config.channels.telegram_token = args.telegram_token
        config.channels.telegram_enabled = True
    if args.chat_id:
        # El chat_id se pasa al bot directamente
        pass

    # Create orchestrator
    orchestrator = HermesOrchestrator(
        config=config,
        db_path=args.db,
    )
    orchestrator.setup()

    # Start
    web = not args.no_web
    telegram = not args.web_only
    orchestrator.run(web=web, telegram=telegram)


if __name__ == "__main__":
    main()
