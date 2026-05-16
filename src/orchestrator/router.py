"""Router de intencion (T-04).

Clasifica mensajes de usuario en tipos de tarea y selecciona el agente
mas adecuado segun las capacidades configuradas.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Tuple

from src.core.models import (
    Task,
    TaskType,
    TaskStatus,
    AgentConfig,
    AgentCapability,
)
from src.orchestrator.task_manager import TaskManager


class IntentClassificationError(Exception):
    """Raised when the user intent cannot be classified."""


class NoAgentAvailableError(Exception):
    """Raised when no configured agent has the required capability."""


class IntentRouter:
    """Routes user messages to the appropriate task type and agent.

    Uses keyword matching and capability resolution to decide:
    1. What type of task the user wants (TaskType).
    2. Which agent is best suited for it.
    3. Whether confirmation is required.
    """

    # Keyword patterns mapped to TaskType (order matters for scoring).
    INTENT_PATTERNS: dict = {
        TaskType.ANALYSIS: [
            r"\banaliz[aai]r?\b",
            r"\bexplic[aao]r?\b",
            r"\bque\s+hace\b",
            r"\bcomo\s+funcion[aao]?\b",
            r"\bentend[eie]r?\b",
            r"\brevis[aao]r?\b",
            r"\binspect(?:ion|ar|)\b",
            r"\breview\b",
        ],
        TaskType.PLANNING: [
            r"\bplan(?:ear|ifica|ificaci[oó]n|)\b",
            r"\bdiseñ[aao]r?\b",
            r"\barquitectur[a]\b",
            r"\bestrategi[a]\b",
            r"\bprepar[aao]r?\s+el\b",
        ],
        TaskType.TESTING: [
            r"\btest(?:ea|ear|s|ing|)\b",
            r"\bprobar\b",
            r"\bverific[aao]r?\b",
            r"\bcorr[eie](r\s+los|r\s+las|\s+los|\s+las)\s+tests\b",
            r"\bcorrer\s+(los\s+)?tests\b",
            r"\bcobertur[a]\b",
        ],
        TaskType.MODIFICATION: [
            r"\bcrea[r]\b",
            r"\bmodific[aao]r?\b",
            r"\bcambi[aao]r?\b",
            r"\bimplement[aao]r?\b",
            r"\bagreg[aao]r?\b",
            r"\baregl[aao]r?\b",
            r"\bcorr[eie]r?\b",
            r"\bfij[aao]r?\b",
            r"\brefactor\b",
            r"\bwrite\b",
            r"\bfix\b",
        ],
        TaskType.COMMIT: [
            r"\bcommit\b",
            r"\bcommitea[r]?\b",
        ],
        TaskType.PUSH: [
            r"\bpush\b",
            r"\bsub[eie]r?\s+(al\s+)?(github|remoto)\b",
        ],
        TaskType.PULL_REQUEST: [
            r"\bpull\s*request\b",
            r"\bpr\b",
            r"\bcrear?\s+(un\s+)?pr\b",
            r"\bmerge(?:a[r]?)?\b",
        ],
    }

    # Capability required for each task type.
    TYPE_TO_CAPABILITY: dict = {
        TaskType.ANALYSIS: AgentCapability.ANALYSIS,
        TaskType.PLANNING: AgentCapability.PLANNING,
        TaskType.MODIFICATION: AgentCapability.EDITING,
        TaskType.TESTING: AgentCapability.TESTING,
        TaskType.COMMIT: AgentCapability.GIT_OPS,
        TaskType.PUSH: AgentCapability.GIT_OPS,
        TaskType.PULL_REQUEST: AgentCapability.GIT_OPS,
    }

    # Task types that always require user confirmation.
    CONFIRMATION_REQUIRED: set = {
        TaskType.COMMIT,
        TaskType.PUSH,
        TaskType.PULL_REQUEST,
    }

    def __init__(self, agents: List[AgentConfig], task_manager: TaskManager):
        """Initialize the router with available agents and a task manager.

        Args:
            agents: List of configured agent profiles.
            task_manager: TaskManager instance for persisting routed tasks.
        """
        self.agents = agents
        self.task_manager = task_manager

    def classify_intent(self, message: str) -> TaskType:
        """Classify a user message into a TaskType using keyword matching.

        Scores each pattern match; returns the highest-scoring task type.
        If no pattern matches, defaults to MODIFICATION.

        Args:
            message: The raw user message text.

        Returns:
            The matched TaskType.
        """
        lower = message.lower()
        scores: dict = {t: 0 for t in TaskType}

        for task_type, patterns in self.INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, lower):
                    scores[task_type] += 1

        # Tie-break: prefer git ops when explicit, then modification.
        best_type = max(scores, key=lambda t: scores[t])
        if scores[best_type] == 0:
            # No match -- default to MODIFICATION as the most common intent.
            return TaskType.MODIFICATION
        return best_type

    def resolve_agent(self, task_type: TaskType) -> AgentConfig:
        """Select the best agent for a given task type based on capabilities.

        Returns the first agent (in config order) that has the required
        capability. Raises NoAgentAvailableError if none match.

        Args:
            task_type: The classified task type.

        Returns:
            The selected AgentConfig.

        Raises:
            NoAgentAvailableError: If no agent supports the required capability.
        """
        required = self.TYPE_TO_CAPABILITY.get(task_type)
        if required is None:
            raise IntentClassificationError(
                f"No capability mapping for task type: {task_type}"
            )

        for agent in self.agents:
            if required in agent.capabilities:
                return agent

        raise NoAgentAvailableError(
            f"No agent configured with capability '{required.value}' "
            f"for task type '{task_type.value}'"
        )

    def needs_confirmation(self, task_type: TaskType) -> bool:
        """Determine if a task type requires user confirmation before execution."""
        return task_type in self.CONFIRMATION_REQUIRED

    def route(
        self,
        user_message: str,
        repository: str,
        branch: Optional[str] = None,
        priority: int = 0,
        force_agent: Optional[str] = None,
    ) -> Task:
        """Full routing pipeline: classify, resolve agent, create task.

        Args:
            user_message: The raw user message.
            repository: Path to the target repository.
            branch: Optional target branch name.
            priority: Task priority (0=normal, 1=high, 2=urgent).
            force_agent: Optional agent name override (bypasses resolution).

        Returns:
            The created and persisted Task instance.

        Raises:
            NoAgentAvailableError: If no suitable agent is found.
            ValueError: If force_agent name is not configured.
        """
        task_type = self.classify_intent(user_message)

        if force_agent:
            agent = self._find_agent_by_name(force_agent)
        else:
            agent = self.resolve_agent(task_type)

        task = Task(
            id=str(uuid.uuid4())[:8],
            user_message=user_message,
            status=TaskStatus.PENDING,
            task_type=task_type,
            agent_name=agent.name,
            repository=repository,
            branch=branch,
            priority=priority,
            requires_confirmation=self.needs_confirmation(task_type),
            metadata={
                "intent_confidence": self._intent_confidence(user_message),
                "routed_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        return self.task_manager.create_task(task)

    def _find_agent_by_name(self, name: str) -> AgentConfig:
        """Look up an agent by its configured name.

        Raises:
            ValueError: If the name does not match any configured agent.
        """
        for agent in self.agents:
            if agent.name == name:
                return agent
        available = [a.name for a in self.agents]
        raise ValueError(
            f"Agent '{name}' not found. Available: {available}"
        )

    def _intent_confidence(self, message: str) -> float:
        """Return a rough confidence score for the classification (0.0-1.0).

        Based on how many distinct intent categories matched and the
        relative score of the winner.
        """
        lower = message.lower()
        scores: dict = {t: 0 for t in TaskType}
        for task_type, patterns in self.INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, lower):
                    scores[task_type] += 1

        total = sum(scores.values())
        if total == 0:
            return 0.3  # Default fallback confidence.
        best = max(scores.values())
        # Normalize: if only one category matched, higher confidence.
        n_matched = sum(1 for s in scores.values() if s > 0)
        ambiguity_penalty = max(0.0, (n_matched - 1) * 0.15)
        return round(min(1.0, best / max(total, 1) - ambiguity_penalty), 2)
