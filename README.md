# Axiom

Axiom is a local-first, gated autonomous coding workbench built around explicit control.

Phase 5 strengthens the foundation with:

- strict operating modes
- mandatory planning before execution
- explicit plan approval
- optional phased approval
- snapshots for rollback
- scoped file access
- protected write-blocked paths
- lightweight repo awareness and **Local RAG (Semantic Search)**
- artifact persistence for plans, results, indexes, previews, and command logs
- patch-style change preview before execution
- phased execution reporting
- explicit verification profiles with stronger built-in checks
- command policy enforcement
- bounded repair planning and one-shot repair attempts
- optional local LLM plan generation through Ollama with strict validation and bounded retries
- optional local LLM plan review / self-critique with the same validation and bounded retry discipline
- **Asynchronous interaction model** with support for "Steer" and "Queue"

This project is not a chatbot shell. It is a controlled task system.

## Project Structure

```text
Axiom/
|-- artifact_manager.py
|-- command_policy.py
|-- context_builder.py
|-- failure_classifier.py
|-- llm_client.py
|-- llm_compress_service.py
|-- llm_decompose_service.py
|-- llm_eval_service.py
|-- llm_implement_service.py
|-- llm_plan_service.py
|-- llm_replan_service.py
|-- llm_retry.py
|-- llm_review_service.py
|-- llm_settings_manager.py
|-- main.py
|-- mode_manager.py
|-- models.py
|-- orchestrator.py
|-- permission_manager.py
|-- phase_policy.py
|-- planner.py
|-- preview_manager.py
|-- project_manager.py
|-- repair_manager.py
|-- repo_indexer.py
|-- run_session.py
|-- scope_manager.py
|-- snapshot_manager.py
|-- start_axiom.py
|-- structured_output.py
|-- terminal_runner.py
|-- ui_server.py
|-- ui/
|-- vector_store.py
|-- verification_manager.py
|-- workspace_manager.py
`-- README.md
```

## Requirements

- Python 3.10+
- Node.js & npm (for the UI shell)

No third-party vector databases are required. Axiom uses a native Python local RAG implementation with JSON storage.

## Modes

### `conversation`

- file reads allowed
- analysis allowed
- no file writes to the user workspace
- no command execution
- Axiom may still write its own metadata artifacts under `.axiom/`

### `plan`

- file reads allowed
- structured plan generation allowed
- no file writes to the user workspace
- no command execution
- Axiom may still write its own metadata artifacts under `.axiom/`

### `implement`

- structured plan generation is mandatory
- plan approval is mandatory
- optional phased approval is available before each phase
- snapshot creation happens before execution
- file writes are allowed only after approval
- command execution is allowed only after approval
- bounded repair handling is available only in this mode

## Safety Model

Permissions are enforced programmatically through `PermissionManager`.

- `can_read_files`
- `can_write_files`
- `can_run_commands`

Scope and path protection are enforced programmatically through `ScopeManager`.

- optional scope limits reads and writes to selected files or folders
- protected paths remain readable when the mode allows reads
- protected paths are always write-blocked in this phase

Command policy is enforced programmatically through `CommandPolicy`.

- `permissive`: current execution behavior
- `safe`: blocks obviously risky command patterns such as destructive deletes, broad permission changes, and shutdown/reboot commands

Every workspace file write still checks both mode permissions and scope/protection rules. Safety does not depend on prompt wording.

## Repo Indexing & Local RAG

Repo indexing is optional and read-only.

When enabled, Axiom collects lightweight structure data (file paths, extensions, top-level directories, entry/config/test files, and Python symbols).

### Local RAG (Semantic Search)

If `AXIOM_LLM_ENABLED` is set and an embedding model is configured, Axiom builds a local vector store for the project. This allows:

- Semantic search across the codebase during planning.
- Retrieval of relevant code snippets to provide better context for complex tasks.
- Asynchronous indexing to prevent blocking the main thread.

Repo indexing:
- respects active scope
- stores the full index and vector store as artifacts under `.axiom/`
- passes a compact summary and relevant search results into planning

## UI Shell

Axiom includes a Material Design 3 UI shell built with React and TypeScript.

### One-Command Launch

For normal local development:

```bash
python start_axiom.py
```

### UI Features

- **Project Management**: Add and switch between multiple project roots via the sidebar.
- **Asynchronous Interaction**:
    - **Steer**: Inject feedback or instructions into an active task (Ctrl+Enter).
    - **Queue**: Sequence multiple tasks for execution (Enter).
- **Session History**: Hierarchical organization of sessions grouped by project.
- **Live Feedback**: High-visibility animated tracking bar for active runs.
- **Plan Inspection**: Review generated plans as "Guiding Features & Subtasks" with status badges.
- **Artifact Browser**: Inspect step results, command output, and repair summaries.

### Local LLM Configuration

Configure these environment variables for Ollama integration:

- `AXIOM_LLM_ENABLED=true`
- `AXIOM_LLM_REVIEW_ENABLED=true`
- `AXIOM_LLM_PROVIDER=ollama`
- `AXIOM_LLM_BASE_URL=http://127.0.0.1:11434`
- `AXIOM_LLM_MODEL=llama3.2:3b`
- `AXIOM_LLM_EMBEDDING_MODEL=nomic-embed-text`
- `AXIOM_LLM_COMPRESSION_ENABLED=true`
- `AXIOM_LLM_COMPRESSION_THRESHOLD=5`
- `AXIOM_LLM_TIMEOUT_SECONDS=20`
- `AXIOM_LLM_RETRY_LIMIT=2`
- `AXIOM_LLM_TEMPERATURE=0.1`

## Verification Profiles

- `none`: No post-execution verification runs.
- `basic`: Verifies direct file-write outcomes and command success.
- `commands_only`: Runs only explicit user-provided verification commands.

## Failure Handling And Repair

When IMPLEMENT execution fails, Axiom classifies the failure (e.g., `command_execution_failure`, `verification_failure`, `scope_violation`) and can optionally perform one automatic repair attempt if `--auto-repair yes` is set.

## CLI Usage

### Plan mode with repo awareness

```bash
python main.py --project /path/to/project --mode plan --build-repo-index --task "Plan a refactor"
```

### Implement mode with safe command policy

```bash
python main.py --project /path/to/project --mode implement --command-policy safe --task "Run command rm -rf dist"
```

### CLI Options

- `--scope path1,path2`: define task scope
- `--protect path1,path2`: write-protected paths
- `--verify-profile none|basic|commands_only`: verification strategy
- `--verify-command "..."`: explicit verification command
- `--build-repo-index`: build lightweight repo index
- `--command-policy permissive|safe`: command policy mode
- `--preview-changes`: show change preview before approval
- `--approval-mode normal|phased`: approval behavior
- `--auto-repair yes|no`: allow automatic repair attempt

## Design Notes

This phase focuses on a "gated" workflow where the system proposes actions and the user approves them. By combining mandatory planning, scoped access, and local LLM validation, Axiom provides a trustworthy autonomous environment.
