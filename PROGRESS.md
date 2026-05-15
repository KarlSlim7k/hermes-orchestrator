# Progreso del proyecto: hermes-orchestrator

## Resumen

Este archivo registra el estado del avance del proyecto. Sirve para que cualquier agente o sesión pueda retomar el trabajo desde el punto exacto donde quedó.

## Estado general: Sprint 1 - 75% completado

| Épica | Estado | Detalle |
|-------|--------|---------|
| Épica 1: Núcleo del orquestador | En progreso | T-01, T-02, T-03 completadas. T-04 pendiente. T-05 incluida en T-03. |
| Épica 2: Integración con agentes CLI | Pendiente | Adaptadores con archivos vacíos. |
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

### T-04. Router de intención
- Prioridad: media
- Archivo destino: `src/orchestrator/router.py` (vacío)
- Próxima tarea recomendada.

### T-06 a T-10: Agentes CLI
- Archivos vacíos en `src/agents/`

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
