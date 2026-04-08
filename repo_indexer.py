from __future__ import annotations

import ast
from pathlib import Path

from models import RepoIndexSummary
from scope_manager import ScopeManager, ScopeViolationError
from workspace_manager import WorkspaceManager
from vector_store import LocalVectorStore


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
        reverse_dependencies: dict[str, list[str]] = {}

        import threading

        from llm_client import LLMSettings

        settings = LLMSettings.from_env()
        vector_store = None
        if settings.enabled and getattr(settings, "embedding_model", None):
            try:
                vector_store = LocalVectorStore(self.project_root, settings)
            except Exception as e:
                print(f"[RepoIndexer] Failed to initialize vector store: {e}")

        files_to_embed = []

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
            top_level_directories.add(
                path.relative_to(self.project_root).parts[0]
                if len(path.relative_to(self.project_root).parts) > 1
                else "."
            )

            is_protected = (
                relative in self.scope_manager.protected_path_labels()
                or any(
                    relative == protected or relative.startswith(f"{protected}/")
                    for protected in self.scope_manager.protected_path_labels()
                )
            )
            if is_protected:
                protected_files_indexed.append(relative)

            if path.name in self.ENTRY_FILE_NAMES:
                likely_entry_files.append(relative)
            if (
                path.suffix.lower() in self.CONFIG_EXTENSIONS
                or path.name.startswith(".env")
                or "config" in relative.lower()
            ):
                likely_config_files.append(relative)
            if "test" in path.name.lower() or "tests" in relative.lower():
                likely_test_files.append(relative)
            if path.suffix.lower() == ".py":
                symbols = self._extract_python_symbols(path)
                if symbols["functions"] or symbols["classes"] or symbols.get("imports"):
                    python_symbols[relative] = symbols

            if vector_store is not None and suffix in {
                ".py",
                ".md",
                ".txt",
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
                ".json",
                ".yaml",
                ".yml",
                ".css",
                ".html",
            }:
                files_to_embed.append((relative, path))

            files.append(
                {
                    "path": relative,
                    "extension": suffix,
                    "protected": is_protected,
                }
            )

        # Build reverse dependencies map
        for relative_path, symbols in python_symbols.items():
            for imported_module in symbols.get("imports", []):
                # Convert module name to potential file path (e.g. models -> models.py)
                # This is a simple approximation
                potential_file = imported_module.replace(".", "/") + ".py"
                if potential_file not in reverse_dependencies:
                    reverse_dependencies[potential_file] = []
                reverse_dependencies[potential_file].append(relative_path)

        if not files:
            notes.append("No readable files were indexed under the current scope.")
        if self.scope_manager.describe_effective_scope()["mode"] == "selected_paths":
            notes.append(
                "Repo index is scope-limited and may omit files outside the active scope."
            )

        # If the local LLM is enabled and configured for embeddings, sync the vector store in the background
        if vector_store is not None and files_to_embed:

            def _embed_and_sync(v_store, f_to_embed):
                try:
                    for rel_path, full_path in f_to_embed:
                        try:
                            content = full_path.read_text(encoding="utf-8")
                            v_store.add_document(rel_path, content)
                        except Exception:
                            pass
                    try:
                        v_store.sync_index()
                    except Exception as e:
                        print(f"[RepoIndexer] Background vector store sync failed: {e}")
                except Exception as e:
                    print(f"[RepoIndexer] Background embedding thread failed completely: {e}")

            # Fire and forget thread for embedding
            thread = threading.Thread(
                target=_embed_and_sync, args=(vector_store, files_to_embed), daemon=True, name="RepoIndexerEmbeddingThread"
            )
            thread.start()
            # Store the thread reference in case it needs to be tracked/joined later
            self._background_thread = thread

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
            reverse_dependencies=reverse_dependencies,
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
            return {"functions": [], "classes": [], "imports": []}

        functions: list[str] = []
        classes: list[str] = []
        imports: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                functions.append(node.name)
            elif isinstance(node, ast.AsyncFunctionDef):
                functions.append(node.name)
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return {
            "functions": functions,
            "classes": classes,
            "imports": imports,
        }

    def _relative(self, path: Path) -> str:
        return str(path.relative_to(self.project_root)).replace("\\", "/")
