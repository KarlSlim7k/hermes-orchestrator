"""Wrapper de gh CLI (T-11).

Ejecuta comandos de GitHub CLI y parsea la salida para operaciones
como consultas de repositorio, issues, y datos generales.
"""

import json
import subprocess
from typing import Optional, Any
from dataclasses import dataclass


class GHCLIError(Exception):
    """Error ejecutando gh CLI."""
    def __init__(self, message: str, exit_code: int, stderr: str):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


@dataclass
class GHCommandResult:
    """Resultado de un comando gh."""
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    parsed_json: Optional[dict] = None


class GitHubClient:
    """Wrapper para gh CLI.

    Proporciona metodos de alto nivel para interactuar con GitHub
    a traves de la CLI oficial.
    """

    def __init__(self, gh_path: str = "gh", timeout: int = 60):
        self.gh_path = gh_path
        self.timeout = timeout

    def _run(
        self,
        args: list[str],
        workdir: Optional[str] = None,
        expect_json: bool = False,
    ) -> GHCommandResult:
        """Ejecutar un comando gh.

        Args:
            args: Argumentos para gh (sin el prefijo 'gh').
            workdir: Directorio de trabajo.
            expect_json: Si True, intenta parsear stdout como JSON.

        Returns:
            GHCommandResult con el resultado.

        Raises:
            GHCLIError: Si el comando falla.
        """
        cmd = [self.gh_path] + args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=workdir,
            )
        except subprocess.TimeoutExpired:
            raise GHCLIError(
                f"gh CLI timed out after {self.timeout}s",
                exit_code=-1,
                stderr="",
            )

        parsed: Optional[dict] = None
        if expect_json and proc.stdout.strip():
            try:
                parsed = json.loads(proc.stdout.strip())
            except json.JSONDecodeError:
                pass

        return GHCommandResult(
            success=proc.returncode == 0,
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            parsed_json=parsed,
        )

    def _run_checked(
        self,
        args: list[str],
        workdir: Optional[str] = None,
        expect_json: bool = False,
    ) -> GHCommandResult:
        """Ejecutar y levantar excepcion si falla."""
        result = self._run(args, workdir, expect_json)
        if not result.success:
            raise GHCLIError(
                f"gh {' '.join(args)} failed (exit {result.exit_code})",
                exit_code=result.exit_code,
                stderr=result.stderr,
            )
        return result

    # -- Auth --

    def auth_status(self) -> dict:
        """Verificar estado de autenticacion."""
        result = self._run(["auth", "status"], expect_json=False)
        # auth status no tiene flag --json, parseamos texto.
        return {
            "success": result.success,
            "output": result.stdout,
            "error": result.stderr,
        }

    # -- Repositorio --

    def repo_view(self, repo: Optional[str] = None) -> dict:
        """Obtener informacion del repositorio."""
        args = ["repo", "view", "--json", "name,owner,url,defaultBranchRef"]
        if repo:
            args.extend([repo])
        result = self._run_checked(args, expect_json=True)
        return result.parsed_json or {}

    def repo_list(self, limit: int = 10, owner: Optional[str] = None) -> list[dict]:
        """Listar repositorios."""
        args = ["repo", "list", "--json", "nameWithOwner,url,visibility", "--limit", str(limit)]
        if owner:
            args.extend(["--owner", owner])
        result = self._run_checked(args, expect_json=True)
        data = result.parsed_json or []
        return data if isinstance(data, list) else []

    # -- Issues --

    def issue_list(
        self,
        repo: Optional[str] = None,
        state: str = "open",
        limit: int = 20,
    ) -> list[dict]:
        """Listar issues de un repositorio."""
        args = [
            "issue", "list",
            "--json", "number,title,state,author,createdAt",
            "--state", state,
            "--limit", str(limit),
        ]
        if repo:
            args.extend(["--repo", repo])
        result = self._run_checked(args, expect_json=True)
        data = result.parsed_json or []
        return data if isinstance(data, list) else []

    def issue_view(self, number: int, repo: Optional[str] = None) -> dict:
        """Ver detalle de un issue."""
        args = ["issue", "view", str(number), "--json", "number,title,body,state,author,comments"]
        if repo:
            args.extend(["--repo", repo])
        result = self._run_checked(args, expect_json=True)
        return result.parsed_json or {}

    def issue_create(
        self,
        title: str,
        body: str,
        repo: Optional[str] = None,
        labels: Optional[list[str]] = None,
    ) -> dict:
        """Crear un issue."""
        args = ["issue", "create", "--title", title, "--body", body]
        if repo:
            args.extend(["--repo", repo])
        if labels:
            args.extend(["--label", ",".join(labels)])
        result = self._run_checked(args, expect_json=True)
        return result.parsed_json or {}

    # -- Utilidades --

    def api(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[dict] = None,
    ) -> Any:
        """Hacer una llamada directa a la API de GitHub."""
        args = ["api", "--method", method, endpoint]
        if params:
            args.extend(["-f", json.dumps(params)])
        result = self._run_checked(args, expect_json=True)
        return result.parsed_json

    def version(self) -> str:
        """Obtener version de gh CLI."""
        result = self._run(["--version"])
        return result.stdout.strip().split("\n")[0] if result.success else "unknown"
