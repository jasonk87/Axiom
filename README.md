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
- lightweight repo awareness
- artifact persistence for plans, results, indexes, previews, and command logs
- patch-style change preview before execution
- phased execution reporting
- explicit verification profiles with stronger built-in checks
- command policy enforcement
- bounded repair planning and one-shot repair attempts
- optional local LLM plan generation through Ollama with strict validation and bounded retries
- optional local LLM plan review / self-critique with the same validation and bounded retry discipline

This project is not a chatbot shell. It is a controlled task system.

## Project Structure

```text
Axiom/
|-- artifact_manager.py
|-- command_policy.py
|-- context_builder.py
|-- failure_classifier.py
|-- main.py
|-- models.py
|-- mode_manager.py
|-- orchestrator.py
|-- permission_manager.py
|-- planner.py
|-- preview_manager.py
|-- repair_manager.py
|-- repo_indexer.py
|-- scope_manager.py
|-- snapshot_manager.py
|-- start_axiom.py
|-- terminal_runner.py
|-- ui_server.py
|-- ui/
|-- verification_manager.py
|-- workspace_manager.py
`-- README.md
```

## Requirements

- Python 3.10+

No third-party dependencies are required for this phase.

For optional local model planning, Axiom can also call a locally running Ollama instance. That integration is disabled by default and only activates when explicitly enabled through environment variables.

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

## Lightweight Repo Indexing

Repo indexing is optional and read-only.

When enabled, Axiom collects lightweight structure data such as:

- file paths
- file extensions
- top-level directories
- likely entry files
- likely config files
- likely test files
- lightweight Python symbols using `ast` where practical

Repo indexing:

- respects active scope
- may include protected readable paths
- marks protected indexed files clearly
- stores the full index as an artifact
- passes only a compact summary into planning and task context

This phase does not build a semantic graph, call graph, or deep dependency model.

## Artifact Storage

Axiom stores system artifacts under:

```text
.axiom/artifacts/<run_id>/
```

Artifacts are kept separate from normal workspace mutations.

Examples include:

- `repo_index_<timestamp>_<suffix>.json`
- `plan_<timestamp>_<suffix>.json`
- `preview_<timestamp>_<suffix>.json`
- `failure_report_<timestamp>_<suffix>.json`
- `repair_plan_<timestamp>_<suffix>.json`
- `repair_result_<timestamp>_<suffix>.json`
- `command_log_<timestamp>_<suffix>.json`
- `result_<timestamp>_<suffix>.json`
- `llm_attempt_<timestamp>_<suffix>.json`
- `llm_validation_<timestamp>_<suffix>.json`

## Local LLM Plan Validation

Axiom can optionally ask a local Ollama model to propose a structured plan object before execution planning continues.

This layer is intentionally narrow:

- the local model is only used for structured plan generation in this phase
- the local model may optionally run a second advisory review pass over the accepted plan
- all returned output is validated before use
- malformed or incomplete output is retried with explicit correction feedback
- retries are bounded
- if the model still fails, Axiom records the failure clearly and falls back to the built-in planner
- review fallback never blocks or rewrites the accepted plan automatically in this phase

The model is never the authority. System rules for approval, scope, protected paths, command policy, verification, and cancellation remain backend-enforced.

### Supported Validation Checks

- JSON parseability
- top-level object shape
- required keys present
- field types correct
- disallowed extra structure rejected for plan steps
- constrained verdict and severity enums for advisory review output

### Local LLM Configuration

Set these environment variables to enable the local model path:

- `AXIOM_LLM_ENABLED=true`
- `AXIOM_LLM_REVIEW_ENABLED=true`
- `AXIOM_LLM_PROVIDER=ollama`
- `AXIOM_LLM_BASE_URL=http://127.0.0.1:11434`
- `AXIOM_LLM_MODEL=llama3.2:3b`
- `AXIOM_LLM_TIMEOUT_SECONDS=20`
- `AXIOM_LLM_RETRY_LIMIT=2`
- `AXIOM_LLM_TEMPERATURE=0.1`

If `AXIOM_LLM_ENABLED` is not set, Axiom continues using only the built-in deterministic planner.

## Planning And Execution Structure

Generated plans can now be grouped into phases such as:

- `understand`
- `modify`
- `verify`
- `repair_follow_up`

Each plan step includes:

- `id`
- `type`
- `title`
- `description`
- `dependencies`
- `scope_hint`
- `expected_outcome`
- `phase`
- `risk_hint`
- `approval_hint`

Execution remains sequential. Axiom reports both phase-level and step-level results, including where execution stopped or failed.

## Change Preview

When `--preview-changes` is enabled for an IMPLEMENT run, Axiom produces a read-only preview before approval.

Preview behavior:

- shows intended file creates and writes when they are known
- shows intended command executions
- prefers a concise unified-diff-style preview for direct file writes
- labels blocked writes when scope or protected-path rules would prevent the write
- stores full preview details as an artifact instead of dumping everything inline

Preview never modifies project files and does not replace plan approval.

## Verification Profiles

### `none`

- no post-execution verification runs

### `basic`

- verifies direct file-write outcomes using built-in existence and content checks
- verifies command success for direct command execution
- may also run explicitly configured verification commands
- records results against the verification step and phase in the run summary

### `commands_only`

- runs only explicit user-provided verification commands
- allowed only in `implement` mode
- each command result is captured structurally and stored as an artifact

## Failure Handling And Repair

When IMPLEMENT execution fails, Axiom now:

1. classifies the failure
2. writes a failure report artifact
3. generates a narrow repair plan
4. optionally performs at most one automatic repair attempt
5. records the final outcome clearly

Supported failure categories include:

- `command_execution_failure`
- `verification_failure`
- `write_policy_block`
- `scope_violation`
- `protected_path_violation`
- `parse_or_analysis_failure`
- `command_policy_block`
- `unknown_failure`

Important repair behavior:

- repair never broadens scope or permissions
- policy, scope, and protected-path violations are reported rather than bypassed
- automatic repair is bounded to one attempt
- if repair fails, Axiom stops and reports clearly

## Supported Task Handling

The foundation safely plans any task, but direct execution remains intentionally narrow:

- analyze a file in `conversation` mode
- generate a structured plan in `plan` mode
- create a file in `implement` mode
- modify or overwrite a file in `implement` mode
- run a shell command in `implement` mode
- restore a snapshot in `implement` mode

## CLI Usage

### Plan mode with repo awareness

```bash
python main.py --project /path/to/project --mode plan --build-repo-index --task "Plan a refactor"
```

### Implement mode with safe command policy

```bash
python main.py --project /path/to/project --mode implement --command-policy safe --task "Run command rm -rf dist"
```

### Implement mode with preview before approval

```bash
python main.py --project /path/to/project --mode implement --preview-changes --task "Create a file named docs/demo.txt with hello world"
```

### Implement mode with file overwrite

```bash
python main.py --project /path/to/project --mode implement --scope docs --preview-changes --verify-profile basic --task "Modify docs/demo.txt to contain: hello world"
```

### Implement mode with phased approval

```bash
python main.py --project /path/to/project --mode implement --approval-mode phased --task "Create a file named docs/demo.txt with hello world"
```

### Implement mode with auto-repair enabled

```bash
python main.py --project /path/to/project --mode implement --auto-repair yes --task "Run command python flaky.py"
```

### Implement mode with command-based verification

```bash
python main.py --project /path/to/project --mode implement --verify-profile commands_only --verify-command "python --version" --task "Run command python --version"
```

## UI Shell

Axiom also includes a first local UI shell built with React and TypeScript.

The UI is a thin layer over the existing Python backend. It does not reimplement the orchestrator. It uses a local bridge server in [ui_server.py](C:\Users\Owner\Desktop\Axiom\ui_server.py) and the React app in [ui](C:\Users\Owner\Desktop\Axiom\ui).

### One-Command Launch

For normal local development, start Axiom with one command:

```bash
python start_axiom.py
```

This launcher:

- starts the Python backend bridge
- starts the Vite frontend dev server
- waits until both are reachable
- opens the browser automatically
- streams prefixed logs
- shuts both processes down cleanly on `Ctrl+C`

Useful launcher variants:

```bash
python start_axiom.py --no-browser
python start_axiom.py --backend-port 9000 --frontend-port 5174
python start_axiom.py --mode built
```

### Launch The UI In Development

The manual development flow still works if you need it:

1. Start the Python bridge server:

```bash
python ui_server.py --host 127.0.0.1 --port 8765
```

2. In a second terminal, start the React frontend:

```bash
cd ui
npm install
npm run dev
```

3. Open the URL printed by Vite, usually:

```text
http://127.0.0.1:5173
```

### Build The UI For Static Serving

```bash
cd ui
npm install
npm run build
```

After building, the Python bridge server will also serve the compiled frontend from `ui/dist`.

### Launch Built Mode

If `ui/dist` already exists, you can run the backend as a single static server:

```bash
python start_axiom.py --mode built
```

Or directly:

```bash
python ui_server.py --host 127.0.0.1 --port 8765
```

In built mode, only the Python server is needed because it serves the compiled frontend assets from `ui/dist`.

### UI Capabilities In This First Shell

- choose a project path
- load a file tree
- set scope and protected paths
- enter a task and mode/settings
- prepare an IMPLEMENT run without auto-executing it
- inspect the generated plan and preview before approval
- approve the overall plan
- approve phases one by one when phased approval is enabled
- observe local-model planning, validation, retry, and fallback activity directly in the session stream when enabled
- observe advisory plan review, findings, retries, and fallback directly in the session stream when review is enabled
- inspect step results, phase progress, artifacts, repair/failure summaries, and command output

## CLI Options

- `--scope path1,path2,path3`: optional files or folders that define the task scope
- `--protect path1,path2,path3`: optional files or folders that are always write-protected
- `--verify-profile none|basic|commands_only`: post-execution verification strategy
- `--verify-command "..."`: explicit verification command, repeat the flag for multiple commands
- `--build-repo-index`: build a lightweight read-only repo index for the current run
- `--command-policy permissive|safe`: command policy mode for IMPLEMENT command execution
- `--preview-changes`: show a change preview before plan approval
- `--approval-mode normal|phased`: approve once for the whole run or before each phase in IMPLEMENT mode
- `--auto-repair yes|no`: allow at most one automatic repair attempt after an IMPLEMENT failure

## Output

Every task returns structured JSON containing:

- task interpretation
- selected mode
- approval mode
- command policy
- effective scope
- protected paths
- verification profile
- context package
- repo index summary
- change preview
- plan
- phase results
- step results
- blocked action attempts
- files read
- files modified
- commands run
- artifact references
- initial execution result
- failure classification
- repair summary
- final execution result
- snapshot reference

Large raw data stays in artifacts. The main task result keeps concise summaries and references to stored artifacts.

## Design Notes

This phase intentionally avoids:

- persistent project memory
- subagents
- parallel execution
- heavy semantic analysis
- true sandboxing
- GUI work

One known limitation remains: shell commands may still have side effects outside the workspace manager, so scope and protected-path enforcement currently apply most strongly to Axiom-managed file operations, repo indexing, repair writes, preview checks, and snapshot restore rather than arbitrary shell behavior.
