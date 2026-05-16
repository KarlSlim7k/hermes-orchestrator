"""Operaciones git locales (T-12).

Gestiona ramas, commits, y push usando git CLI.
"""

import subprocess
from typing import Optional
from dataclasses import dataclass


class GitError(Exception):
    """Error ejecutando git."""
    def __init__(self, message: str, exit_code: int, stderr: str):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


@dataclass
class GitResult:
    """Resultado de una operacion git."""
    success: bool
    stdout: str
    stderr: str
    exit_code: int


class GitOperations:
    """Operaciones git locales para el orquestador.

    Maneja creacion de ramas, commits, push, y verificacion
    de estado del repositorio.
    """

    def __init__(
        self,
        workdir: str,
        git_path: str = "git",
        timeout: int = 60,
    ):
        self.workdir = workdir
        self.git_path = git_path
        self.timeout = timeout

    def _run(self, args: list[str]) -> GitResult:
        """Ejecutar un comando git."""
        cmd = [self.git_path] + args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.workdir,
            )
        except subprocess.TimeoutExpired:
            raise GitError(
                f"git timed out after {self.timeout}s",
                exit_code=-1,
                stderr="",
            )

        return GitResult(
            success=proc.returncode == 0,
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
        )

    def _run_checked(self, args: list[str]) -> GitResult:
        """Ejecutar y lanzar excepcion si falla."""
        result = self._run(args)
        if not result.success:
            raise GitError(
                f"git {' '.join(args)} failed (exit {result.exit_code}): {result.stderr.strip()}",
                exit_code=result.exit_code,
                stderr=result.stderr,
            )
        return result

    # -- Estado --

    def current_branch(self) -> str:
        """Obtener nombre de la rama actual."""
        result = self._run_checked(["branch", "--show-current"])
        return result.stdout.strip()

    def is_dirty(self) -> bool:
        """Verificar si hay cambios sin commitear."""
        result = self._run(["status", "--porcelain"])
        return bool(result.stdout.strip())

    def changed_files(self) -> list[str]:
        """Obtener lista de archivos modificados."""
        result = self._run(["diff", "--name-only", "HEAD"])
        if not result.stdout.strip():
            # Tambien check staged files.
            result = self._run(["diff", "--cached", "--name-only"])
        return [f for f in result.stdout.strip().split("\n") if f]

    # -- Ramas --

    def create_branch(self, branch_name: str, from_branch: Optional[str] = None) -> bool:
        """Crear una nueva rama."""
        args = ["branch", branch_name]
        if from_branch:
            args.append(from_branch)
        result = self._run(args)
        return result.success

    def checkout(self, branch_name: str) -> bool:
        """Cambiar a una rama."""
        result = self._run(["checkout", branch_name])
        return result.success

    def create_and_checkout(self, branch_name: str) -> bool:
        """Crear y cambiar a una nueva rama."""
        result = self._run(["checkout", "-b", branch_name])
        return result.success

    def delete_branch(self, branch_name: str, force: bool = False) -> bool:
        """Eliminar una rama."""
        flag = "-D" if force else "-d"
        result = self._run(["branch", flag, branch_name])
        return result.success

    def list_branches(self, remote: bool = False) -> list[str]:
        """Listar ramas."""
        args = ["branch"]
        if remote:
            args.append("-r")
        result = self._run_checked(args)
        return [
            line.strip().removeprefix("* ").strip()
            for line in result.stdout.strip().split("\n")
            if line.strip()
        ]

    # -- Staging y Commit --

    def add(self, paths: Optional[list[str]] = None) -> bool:
        """Stage archivos. Si paths es None, stagea todo (.)."""
        args = ["add"]
        if paths:
            args.extend(paths)
        else:
            args.append(".")
        result = self._run(args)
        return result.success

    def commit(
        self,
        message: str,
        allow_empty: bool = False,
    ) -> GitResult:
        """Hacer commit con un mensaje."""
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        result = self._run(args)
        return result

    def commit_checked(
        self,
        message: str,
        allow_empty: bool = False,
    ) -> GitResult:
        """Commit con verificacion de exito."""
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        return self._run_checked(args)

    # -- Push y Pull --

    def push(
        self,
        remote: str = "origin",
        branch: Optional[str] = None,
        set_upstream: bool = True,
        force: bool = False,
    ) -> GitResult:
        """Push a un repositorio remoto."""
        args = ["push"]
        if force:
            args.append("--force")
        if set_upstream:
            args.append("-u")
        args.append(remote)
        if branch:
            args.append(branch)
        return self._run_checked(args)

    def pull(
        self,
        remote: str = "origin",
        branch: Optional[str] = None,
    ) -> GitResult:
        """Pull de un repositorio remoto."""
        args = ["pull"]
        if remote:
            args.append(remote)
        if branch:
            args.append(branch)
        return self._run_checked(args)

    # -- Utilidades --

    def get_remote_url(self, remote: str = "origin") -> Optional[str]:
        """Obtener URL de un remoto."""
        result = self._run(["remote", "get-url", remote])
        if result.success:
            return result.stdout.strip()
        return None

    def latest_commit_hash(self) -> str:
        """Obtener hash del ultimo commit."""
        result = self._run_checked(["log", "-1", "--format=%H"])
        return result.stdout.strip()

    def commit_log(self, count: int = 10) -> list[str]:
        """Obtener log de commits recientes."""
        result = self._run_checked([
            "log", f"-{count}", "--oneline", "--format=%h %s",
        ])
        return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]

    def ensure_clean_or_stash(self) -> bool:
        """Si hay cambios sucios, hacer stash. Retorna True si se puede operar."""
        if not self.is_dirty():
            return True
        result = self._run(["stash", "push", "-m", "auto-stash by hermes-orchestrator"])
        return result.success
