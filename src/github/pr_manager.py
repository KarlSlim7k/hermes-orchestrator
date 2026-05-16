"""Gestion de Pull Requests (T-13).

Crea, lista y gestiona PRs a traves de gh CLI.
"""

import json
import subprocess
from typing import Optional
from dataclasses import dataclass


class PRError(Exception):
    """Error gestionando PRs."""
    def __init__(self, message: str, exit_code: int, stderr: str):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


@dataclass
class PRInfo:
    """Informacion de un Pull Request."""
    number: int
    title: str
    state: str
    url: str
    head: str
    base: str
    body: str = ""
    author: str = ""
    created_at: str = ""
    merged: bool = False


class PRManager:
    """Gestiona el ciclo de vida de Pull Requests via gh CLI."""

    def __init__(self, gh_path: str = "gh", timeout: int = 60):
        self.gh_path = gh_path
        self.timeout = timeout

    def _run(self, args: list[str], workdir: Optional[str] = None) -> tuple[bool, str, str, int]:
        """Ejecutar gh y retornar (success, stdout, stderr, exit_code)."""
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
            raise PRError(
                f"gh timed out after {self.timeout}s",
                exit_code=-1,
                stderr="",
            )

        return (
            proc.returncode == 0,
            proc.stdout,
            proc.stderr,
            proc.returncode,
        )

    def _run_checked(self, args: list[str], workdir: Optional[str] = None) -> str:
        """Ejecutar y lanzar excepcion si falla."""
        ok, stdout, stderr, code = self._run(args, workdir)
        if not ok:
            raise PRError(
                f"gh {' '.join(args)} failed (exit {code}): {stderr.strip()}",
                exit_code=code,
                stderr=stderr,
            )
        return stdout

    def create(
        self,
        title: str,
        body: str,
        base: str = "main",
        head: Optional[str] = None,
        repo: Optional[str] = None,
        draft: bool = False,
        workdir: Optional[str] = None,
    ) -> PRInfo:
        """Crear un Pull Request.

        Args:
            title: Titulo del PR.
            body: Descripcion del PR.
            base: Rama base (default: main).
            head: Rama source (default: rama actual).
            repo: owner/repo (default: repo actual).
            draft: Si True, crear como draft PR.
            workdir: Directorio del repositorio.

        Returns:
            PRInfo con los datos del PR creado.
        """
        args = [
            "pr", "create",
            "--title", title,
            "--body", body,
            "--base", base,
            "--json", "number,title,state,url,headRefName,baseRefName",
        ]
        if head:
            args.extend(["--head", head])
        if repo:
            args.extend(["--repo", repo])
        if draft:
            args.append("--draft")

        stdout = self._run_checked(args, workdir)

        try:
            data = json.loads(stdout.strip())
        except json.JSONDecodeError:
            # Si no es JSON, parsear URL de la salida.
            url = ""
            for line in stdout.strip().split("\n"):
                if "github.com" in line and "/pull/" in line:
                    url = line.strip()
                    break
            data = {"url": url, "title": title}

        return PRInfo(
            number=int(data.get("number", 0)),
            title=data.get("title", title),
            state=data.get("state", "open"),
            url=data.get("url", ""),
            head=data.get("headRefName", head or ""),
            base=data.get("baseRefName", base),
            body=body,
        )

    def view(self, pr_number: int, repo: Optional[str] = None) -> PRInfo:
        """Ver informacion de un PR."""
        args = [
            "pr", "view", str(pr_number),
            "--json", "number,title,state,url,headRefName,baseRefName,body,author,createdAt,mergedAt",
        ]
        if repo:
            args.extend(["--repo", repo])

        stdout = self._run_checked(args)
        data = json.loads(stdout.strip())

        return PRInfo(
            number=data["number"],
            title=data["title"],
            state=data["state"],
            url=data["url"],
            head=data.get("headRefName", ""),
            base=data.get("baseRefName", ""),
            body=data.get("body", ""),
            author=data.get("author", {}).get("login", ""),
            created_at=data.get("createdAt", ""),
            merged=bool(data.get("mergedAt")),
        )

    def list(
        self,
        state: str = "open",
        limit: int = 20,
        repo: Optional[str] = None,
    ) -> list[PRInfo]:
        """Listar PRs."""
        args = [
            "pr", "list",
            "--state", state,
            "--limit", str(limit),
            "--json", "number,title,state,url,headRefName,baseRefName",
        ]
        if repo:
            args.extend(["--repo", repo])

        stdout = self._run_checked(args)
        data = json.loads(stdout.strip())
        if not isinstance(data, list):
            return []

        return [
            PRInfo(
                number=pr["number"],
                title=pr["title"],
                state=pr["state"],
                url=pr["url"],
                head=pr.get("headRefName", ""),
                base=pr.get("baseRefName", ""),
            )
            for pr in data
        ]

    def merge(
        self,
        pr_number: int,
        method: str = "merge",
        delete_branch: bool = False,
        workdir: Optional[str] = None,
    ) -> bool:
        """Merge un PR.

        Args:
            pr_number: Numero del PR.
            method: merge, squash, o rebase.
            delete_branch: Si True, eliminar la rama despues.

        Returns:
            True si el merge fue exitoso.
        """
        args = ["pr", "merge", str(pr_number), f"--{method}"]
        if delete_branch:
            args.append("--delete-branch")

        ok, _, stderr, code = self._run(args, workdir)
        return ok

    def comment(
        self,
        pr_number: int,
        body: str,
        workdir: Optional[str] = None,
    ) -> bool:
        """Agregar un comentario a un PR."""
        args = ["pr", "comment", str(pr_number), "--body", body]
        ok, _, _, _ = self._run(args, workdir)
        return ok

    def checks(
        self,
        pr_number: int,
        repo: Optional[str] = None,
    ) -> list[dict]:
        """Obtener estado de los checks de un PR."""
        args = [
            "pr", "checks", str(pr_number),
            "--json", "name,status,conclusion",
        ]
        if repo:
            args.extend(["--repo", repo])

        stdout = self._run_checked(args)
        data = json.loads(stdout.strip())
        return data if isinstance(data, list) else []
