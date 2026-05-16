# Progreso del proyecto: hermes-orchestrator

## Resumen

Este archivo registra el estado del avance del proyecto. Sirve para que cualquier agente o sesión pueda retomar el trabajo desde el punto exacto donde quedó.

## Estado general: Sprint 3 - 100% completado

| Épica | Estado | Detalle |
|-------|--------|---------|
| Épica 1: Núcleo del orquestador | Completada | T-01 a T-04 completadas. |
| Épica 2: Integración con agentes CLI | Completada | T-06, T-07, T-08, T-09, T-10 completadas. |
| Épica 3: Integración con GitHub | Pendiente | Sin implementar. |
| Épica 4: Notificaciones | Pendiente | Sin implementar. |
| Épica 5: Integración end-to-end | Pendiente | Sin implementar. |

## Tareas completadas

### T-01. Modelos de datos ✅
- Estado: Done
- Archivo: `src/core/models.py`
- Modelos: Task, TaskEvent, AgentConfig, Notification, SystemConfig, enums
- Validaciones: prioridad, transiciones
- Commit: `00dca36`

### T-02. Máquina de estados ✅
- Estado: Done
- Archivo: `src/orchestrator/state_machine.py`
- Transiciones válidas definidas
- InvalidTransitionError para transiciones inválidas
- Commit: `00dca36`

### T-03. Task Manager ✅
- Estado: Done
- Archivo: `src/orchestrator/task_manager.py`
- CRUD completo con SQLite
- Persistencia de eventos
- Integración con state machine
- Tests: `tests/test_core_orchestrator.py`
- Commit: `00dca36`

### T-05. Persistencia SQLite ✅ (incluida en T-03)
- Estado: Done (cubierta por T-03)
- Backend SQLite en memoria por defecto, configurable a archivo.

## Tareas pendientes

### T-04. Router de intención ✅
- Estado: Done
- Archivo: `src/orchestrator/router.py`
- Clasificacion de intencion por keywords con scoring
- Seleccion de agente por capacidades (AgentCapability)
- Pipeline completo: classify -> resolve -> create task
- Confianza de clasificacion (0.0-1.0)
- Override de agente con force_agent
- Tests: `tests/test_router.py` (25 tests)

### T-06. Interfaz base de agente ✅
- Estado: Done
- Archivos: `src/agents/base.py`, `src/agents/__init__.py`
- Clase abstracta BaseAgent con metodos: build_command, parse_result, parse_progress
- run_sync: ejecucion sincrona con timeout
- stream_events: yield de eventos en tiempo real (progreso, stdout)
- Dataclasses: AgentResult, AgentProgress, AgentStreamEvent
- Tests: `tests/test_agents/test_agent_base.py` (25 tests)

### T-08. Adaptador de OpenCode CLI ✅
- Estado: Done
- Archivo: `src/agents/opencode_adapter.py`
- Usa `opencode run --format json` para salida estructurada
- Parsea eventos JSON: step_start, text, step_finish, error
- Extrae session_id, tokens, costo de step_finish
- Auto-approve con --dangerously-skip-permissions
- Deteccion de archivos modificados en texto de salida
- Cubierto por tests en `tests/test_agents/test_agent_base.py`

### T-11 a T-14: GitHub
- Archivos vacíos en `src/github/`

### T-15 a T-17: Notificaciones
- Archivos vacíos en `src/notifications/`

### T-18 a T-20: Integración
- Pendientes

## Configuración del proyecto

- Repo: https://github.com/KarlSlim7k/hermes-orchestrator
- Rama: main
- Git user: Karol Nahum Delgado Bernal
- Git email: a23050014@perote.tecnm.mx
- Python: 3.11+
- Último commit: `00dca36`

## Próximos pasos inmediatos

1. Implementar T-04: Router de intención
2. Implementar T-06: Interfaz base de agente
3. Implementar T-08: Adaptador de OpenCode CLI (agente disponible en el sistema)
4. Conectar task manager con agente OpenCode para flujo end-to-end básico
