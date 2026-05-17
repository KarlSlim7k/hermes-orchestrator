# Hermes Orchestrator

Orquestador de agentes de IA/CLI (Codex, OpenCode, Kiro) con control via Telegram, panel web, y flujos de GitHub (commit → push → PR) con confirmaciones.

## Arquitectura

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│  Telegram   │────▶│   Router    │────▶│ Task Manager  │
│   / Web UI  │     │ (Intent)    │     │  (SQLite)     │
└─────────────┘     └──────┬──────┘     └──────┬───────┘
                           │                   │
                    ┌──────▼──────┐     ┌──────▼───────┐
                    │   Agent     │     │ Notifier     │
                    │  Registry   │────▶│ (Console/    │
                    └─────────────┘     │  Telegram)   │
                                        └──────────────┘
```

## Requisitos

- **Python 3.11+**
- **git** CLI
- **gh** CLI (opcional, para GitHub integration)
- **opencode** o **codex** CLI (al menos uno configurado)

## Instalación

```bash
# Clonar el repo
git clone https://github.com/KarlSlim7k/hermes-orchestrator.git
cd hermes-orchestrator

# Instalar dependencias
pip install -e .

# (Opcional) Para config YAML
pip install pyyaml
```

## Uso rapido

### 1. Panel web (solo)

```bash
python -m src.main --web-only --port 8000
```

Abre http://localhost:8000 para ver el dashboard.

### 2. Con Telegram

```bash
python -m src.main --telegram-token TU_TOKEN_AQUI --chat-id TU_CHAT_ID
```

Envia mensajes al bot y seran clasificados, ruteados a un agente, y ejecutados automaticamente.

### 3. Con archivo de configuracion

```bash
python -m src.main --config config/config.yaml
```

## Configuracion

### Archivo YAML (`config/config.yaml`)

```yaml
default_agent: opencode
repository_path: "."

security:
  require_confirmation_for_commit: true
  require_confirmation_for_push: true
  require_confirmation_for_pr: true
  max_concurrent_tasks: 3

channels:
  telegram_enabled: false
  telegram_token: null
  web_enabled: true
  web_port: 8000

agents:
  - name: opencode
    cli_command: opencode
    capabilities: [analysis, planning, editing, testing, git_ops]
    supports_progress: true
    timeout_seconds: 600
```

### Variables de entorno (override)

| Variable | Descripcion |
|----------|-------------|
| `HERMES_TELEGRAM_ENABLED` | Habilita canal Telegram (`true`/`false`) |
| `HERMES_TELEGRAM_TOKEN` | Token del bot |
| `HERMES_WEB_PORT` | Puerto del panel web |
| `HERMES_REPO_PATH` | Ruta del repositorio |
| `HERMES_DEFAULT_AGENT` | Agente por defecto |
| `HERMES_MAX_CONCURRENT_TASKS` | Max tareas simultaneas |

## Comandos CLI

```
python -m src.main [opciones]

Opciones:
  -c, --config PATH        Archivo de config (YAML/JSON)
  --db PATH                SQLite DB path (default: :memory:)
  --host HOST              Host del panel web (default: 0.0.0.0)
  -p, --port PORT          Puerto del panel web
  --telegram-token TOKEN   Token de Telegram (override)
  --chat-id ID             Chat ID permitido (override)
  --web-only               Solo panel web
  --no-web                 Sin panel web
  --log-level LEVEL        DEBUG|INFO|WARNING|ERROR
  --log-file PATH          Archivo de log
  --json-log               Formato JSON para logs
```

## Comandos del bot de Telegram

| Comando | Descripcion |
|---------|-------------|
| `/help` | Muestra comandos disponibles |
| `/status` | Estado de la ultima tarea |
| `/tasks` | Lista las 5 tareas recientes |
| `/cancel` | Cancela la tarea en ejecucion |

### Mensajes naturales

El bot clasifica tu intencion por keywords:

| Tu mensaje | Tipo de tarea | Agente | Confirmacion |
|-----------|--------------|--------|-------------|
| "Analiza el codigo en src/" | ANALYSIS | opencode | No |
| "Crea un modulo de logging" | MODIFICATION | opencode | No |
| "Corre los tests" | TESTING | opencode | No |
| "Commitea los cambios" | COMMIT | opencode | **Si** |
| "Crea un PR a main" | PULL_REQUEST | opencode | **Si** |

## API REST

| Endpoint | Metodo | Descripcion |
|----------|--------|-------------|
| `/` | GET | Panel visual HTML |
| `/api/tasks` | GET | Lista tareas (`?status=`, `?limit=`) |
| `/api/tasks` | POST | Crear tarea |
| `/api/tasks/:id` | GET | Detalle de tarea |
| `/api/tasks/:id/events` | GET | Eventos de tarea |
| `/api/tasks/:id/start` | POST | Iniciar tarea |
| `/api/tasks/:id/cancel` | POST | Cancelar tarea |
| `/api/notifications` | GET | Historial de notificaciones |
| `/health` | GET | Health check |

### Ejemplo: crear tarea via API

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "Analiza el codigo en src/core/",
    "task_type": "analysis",
    "agent_name": "opencode",
    "repository": "/path/to/repo"
  }'
```

## Flujo GitHub completo (T-19)

El `GitHubWorkflow` orquesta:

1. **Crear rama** automatica (`hermes/<task_id>`)
2. **Commit** con confirmacion
3. **Push** al remoto con confirmacion
4. **Pull Request** a la rama base con confirmacion

Cada paso requiere aprobacion del usuario (configurable). Los botones de Telegram permiten aprobar/rechazar directamente.

```python
from src.github.workflow import GitHubWorkflow
from src.github.git_ops import GitOperations
from src.github.pr_manager import PRManager
from src.github.confirmation import ConfirmationHandler

workflow = GitHubWorkflow(
    task_manager=tm,
    git_ops=GitOperations(workdir="/path/to/repo"),
    pr_manager=PRManager(),
    confirmation_handler=ConfirmationHandler(tm),
)

result = workflow.execute(
    task=task,
    commit_message="feat: add logging",
    pr_title="Add logging module",
)
```

## Estructura del proyecto

```
src/
├── core/
│   ├── models.py          # Pydantic models (Task, TaskEvent, etc.)
│   ├── config.py          # Config loader (YAML/JSON + env)
│   └── logging.py         # Structured logging
├── orchestrator/
│   ├── router.py          # Intent classification + agent resolution
│   ├── task_manager.py    # CRUD + SQLite persistence
│   └── state_machine.py   # State transitions
├── agents/
│   ├── base.py            # BaseAgent abstract class
│   ├── opencode_adapter.py
│   ├── codex_adapter.py
│   ├── progress_tracker.py
│   └── error_handler.py
├── github/
│   ├── client.py          # gh CLI wrapper
│   ├── git_ops.py         # Git operations (branch, commit, push)
│   ├── pr_manager.py      # PR lifecycle via gh
│   ├── confirmation.py    # Confirmation workflow
│   └── workflow.py        # Full commit→push→PR pipeline
├── notifications/
│   ├── notifier.py        # Multi-channel dispatcher
│   └── channels.py        # Telegram + Console channels
├── interfaces/
│   ├── telegram.py        # Telegram bot (message routing, commands)
│   └── web/
│       ├── app.py         # Web server + REST API
│       └── ui.py          # HTML dashboard
└── main.py                # CLI entry point
tests/
├── test_core/             # Config, logging
├── test_agents/           # Agent adapters
├── test_github/           # GitHub modules
├── test_notifications/    # Notification channels
├── test_interfaces/       # Telegram bot, Web API
└── test_integration/      # End-to-end flows
```

## Tests

```bash
# Todos los tests
python -m pytest tests/ -v

# Por modulo
python -m pytest tests/test_core/ -v
python -m pytest tests/test_integration/ -v

# Con coverage
pip install pytest-cov
python -m pytest tests/ --cov=src --cov-report=html
```

**277 tests pasando** (al momento del ultimo commit).

## Estado del proyecto

| Epica | Estado |
|-------|--------|
| Epica 1: Nucleo del orquestador | ✅ Completada |
| Epica 2: Agentes CLI | ✅ Completada |
| Epica 3: GitHub Integration | ✅ Completada |
| Epica 4: Notificaciones | ✅ Completada |
| Epica 5: End-to-end | ✅ Completada |

## Licencia

MIT

## Autor

Karol Nahum Delgado Bernal — [GitHub](https://github.com/KarlSlim7k)
