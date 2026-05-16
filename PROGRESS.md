# Progreso del proyecto: hermes-orchestrator

## Resumen

Este archivo registra el estado del avance del proyecto. Sirve para que cualquier agente o sesión pueda retomar el trabajo desde el punto exacto donde quedó.

## Estado general: Sprint 5 — Interfaces y End-to-End

| Épica | Estado | Detalle |
|-------|--------|---------|
| Épica 1: Núcleo del orquestador | ✅ Completada | T-01 a T-04 + T-06bis |
| Épica 2: Integración con agentes CLI | ✅ Completada | T-06, T-07, T-08, T-09, T-10 |
| Épica 3: Integración con GitHub | ✅ Completada | T-11 a T-14 |
| Épica 4: Notificaciones | ✅ Completada | T-15 a T-17 |
| Épica 5: Integración end-to-end | ⏳ En progreso | T-16bis hecha. Faltan T-17bis, T-18, T-19, T-20 |

## Tareas completadas

### Épica 1 — Núcleo
- **T-01** Modelos de datos ✅ — `src/core/models.py` (Task, TaskEvent, AgentConfig, Notification, SystemConfig)
- **T-02** Máquina de estados ✅ — `src/orchestrator/state_machine.py`
- **T-03** Task Manager ✅ — `src/orchestrator/task_manager.py` (CRUD + SQLite)
- **T-04** Router de intención ✅ — `src/orchestrator/router.py` (keyword classification + agent resolution)
- **T-06bis** Config y Logging ✅ — `src/core/config.py`, `src/core/logging.py` (YAML/JSON config, env overrides, structured logging)

### Épica 2 — Agentes CLI
- **T-06** Interfaz base de agente ✅ — `src/agents/base.py` (BaseAgent abstracta, AgentResult, stream_events)
- **T-07** Adaptador Codex CLI ✅ — `src/agents/codex_adapter.py`
- **T-08** Adaptador OpenCode CLI ✅ — `src/agents/opencode_adapter.py`
- **T-09** Detección de progreso ✅ — `src/agents/progress_tracker.py`
- **T-10** Manejo de errores ✅ — `src/agents/error_handler.py`

### Épica 3 — GitHub
- **T-11** Wrapper gh CLI ✅ — `src/github/client.py` (GitHubClient, GHCommandResult)
- **T-12** Operaciones git locales ✅ — `src/github/git_ops.py` (commit, push, ramas)
- **T-13** Gestión de PR ✅ — `src/github/pr_manager.py` (create, view, list, merge, checks)
- **T-14** Flujo de confirmación ✅ — `src/github/confirmation.py`

### Épica 4 — Notificaciones
- **T-15** Sistema de notificación ✅ — `src/notifications/notifier.py` (Notifier, ConsoleChannel, NotificationChannelBase)
- **T-16** Bot de Telegram ✅ — `src/notifications/channels.py` (TelegramChannel con inline keyboards, polling)
- **T-17** Panel web básico ✅ — `src/interfaces/web/ui.py` (HTML dashboard, stats, auto-refresh, /api/tasks)

## Tareas pendientes

### T-16bis: Interfaz Telegram (bot receptor) ✅
- Archivos: `src/interfaces/telegram.py`
- Bot con polling HTTP directo (sin dependencias externas)
- Clasificacion de mensajes via IntentRouter
- Ejecucion async de tareas con TaskExecutor
- Comandos: /help, /status, /tasks, /cancel
- Confirmaciones inline para operaciones git (aprobar/rechazar)
- Filtrado por chat_id
- Tests: `tests/test_interfaces/test_telegram.py` (22 tests)

### T-17bis: Web App entry point ⏳
- Archivo: `src/interfaces/web/app.py` (vacío)
- Falta: App que arranca el servidor web conectando TaskManager + Notifier + UI

### T-18: Flujo completo de prueba
- Tipo: integración
- Dependencias: T-03, T-07, T-15
- Criterio: orden → agente → notificación → resultado

### T-19: Flujo con GitHub
- Tipo: integración
- Dependencias: T-18, T-13, T-14
- Criterio: commit + push + PR con confirmación del usuario

### T-20: Documentación de uso
- Tipo: documentación
- Dependencias: T-18, T-19
- Criterio: README actualizado con guía de uso

## Tests

- **Total:** 235 tests pasando ✅
- Core/Orchestrator: 30 tests
- Router: 25 tests
- Agentes: tests varios
- GitHub: tests varios
- Notificaciones: tests varios
- Config: 17 tests
- Logging: 13 tests
- Telegram Bot: 22 tests (nuevos)

## Configuración del proyecto

- Repo: https://github.com/KarlSlim7k/hermes-orchestrator
- Rama: main
- Git user: Karol Nahum Delgado Bernal
- Git email: a23050014@perote.tecnm.mx
- Python: 3.11+
- Último commit: `7bd9db6`

## Próximos pasos inmediatos

1. **T-17bis**: Web App entry point (conectar TaskManager + Notifier + UI)
2. **T-18**: Flujo end-to-end básico (orden → agente → resultado)
3. **T-19**: Flujo GitHub completo con confirmación
4. **T-20**: README y documentación de uso
