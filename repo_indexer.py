from __future__ import annotations

import ast
from pathlib import Path

from models import RepoIndexSummary
from scope_manager import ScopeManager, ScopeViolationError
from workspace_manager import WorkspaceManager


class RepoIndexer:
    ENTRY_FILE_NAMES = {"main.py", "app.py", "manage.py", "__main__.py", "package.json"}
    CONFIG_EXTENSIONS = {".ini", ".toml", ".yaml", ".yml", ".json", ".cfg"}

    def __init__(
        self,
        project_root: str,
        workspace: WorkspaceManager,
        scope_manager: ScopeManager,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace = workspace
        self.scope_manager = scope_manager

    def build_summary(self) -> tuple[RepoIndexSummary, dict]:
        files: list[dict] = []
        extension_counts: dict[str, int] = {}
        top_level_directories: set[str] = set()
        likely_entry_files: list[str] = []
        likely_config_files: list[str] = []
        likely_test_files: list[str] = []
        python_symbols: dict[str, dict[str, list[str]]] = {}
        protected_files_indexed: list[str] = []
        notes: list[str] = []

        for path in self.project_root.rglob("*"):
            if not path.is_file():
                continue
            if ".axiom_snapshots" in path.parts or ".axiom" in path.parts:
                continue
            try:
                self.scope_manager.enforce_read(path)
            except ScopeViolationError:
                continue

            relative = self._relative(path)
            suffix = path.suffix.lower() or "<no_extension>"
            extension_counts[suffix] = extension_counts.get(suffix, 0) + 1
            top_level_directories.add(path.relative_to(self.project_root).parts[0] if len(path.relative_to(self.project_root).parts) > 1 else ".")

            is_protected = relative in self.scope_manager.protected_path_labels() or any(
                relative == protected or relative.startswith(f"{protected}/")
                for protected in self.scope_manager.protected_path_labels()
            )
            if is_protected:
                protected_files_indexed.append(relative)

            if path.name in self.ENTRY_FILE_NAMES:
                likely_entry_files.append(relative)
            if path.suffix.lower() in self.CONFIG_EXTENSIONS or path.name.startswith(".env") or "config" in relative.lower():
                likely_config_files.append(relative)
            if "test" in path.name.lower() or "tests" in relative.lower():
                likely_test_files.append(relative)
            if path.suffix.lower() == ".py":
                symbols = self._extract_python_symbols(path)
                if symbols["functions"] or symbols["classes"]:
                    python_symbols[relative] = symbols

            files.append(
                {
                    "path": relative,
                    "extension": suffix,
                    "protected": is_protected,
                }
            )

        if not files:
            notes.append("No readable files were indexed under the current scope.")
        if self.scope_manager.describe_effective_scope()["mode"] == "selected_paths":
            notes.append("Repo index is scope-limited and may omit files outside the active scope.")

        summary = RepoIndexSummary(
            generated=True,
            total_files=len(files),
            top_level_directories=sorted(top_level_directories),
            file_extensions=dict(sorted(extension_counts.items())),
            likely_entry_files=sorted(set(likely_entry_files)),
            likely_config_files=sorted(set(likely_config_files)),
            likely_test_files=sorted(set(likely_test_files)),
            python_symbols=python_symbols,
            protected_files_indexed=sorted(set(protected_files_indexed)),
            notes=notes,
        )
        payload = {
            "summary": summary.to_dict(),
            "files": files,
        }
        return summary, payload

    def _extract_python_symbols(self, path: Path) -> dict[str, list[str]]:
        try:
            source = path.read_text(encoding="utf-8")
            self.workspace.track_read_path(path)
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeDecodeError):
            return {"functions": [], "classes": []}

        functions: list[str] = []
        classes: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                functions.append(node.name)
            elif isinstance(node, ast.AsyncFunctionDef):
                functions.append(node.name)
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
        return {
            "functions": functions,
            "classes": classes,
        }

    def _relative(self, path: Path) -> str:
        return str(path.relative_to(self.project_root)).replace("\\", "/")
