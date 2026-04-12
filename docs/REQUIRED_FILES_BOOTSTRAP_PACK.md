# Required Files Bootstrap Pack

This repository includes a fixed bootstrap pack for fresh target repositories
that want to use `ai-dev-orchestrator` safely.

## Canonical Required Files

- `AGENTS.md`
  Primary repo-side operating contract for Codex or bridge-driven coding
  agents.
- `.github/copilot-instructions.md`
  GitHub Copilot-facing companion instructions that stay aligned with
  `AGENTS.md`.

The canonical machine-readable source of truth is
`bridge/required_files_bootstrap/required_files_pack.json`.
Each required file entry declares the repo path, purpose, template source, and
minimum heading anchors that must exist after bootstrap.

## Fixed Bootstrap Pack

- Prompt template:
  `bridge/required_files_bootstrap/copilot_bootstrap_prompt_template.md`
- File templates:
  `bridge/required_files_bootstrap/templates/`
- Builder and materializer:
  `scripts/required_files_bootstrap.py`

The generated artifact is a Copilot-facing bootstrap instruction that embeds the
exact file payloads and stable markers used by the deterministic local
materializer.

## Usage

1. Build the bootstrap artifact:

   `python3 scripts/required_files_bootstrap.py build-prompt --target-repo-name sample-repo --target-repo-path /ABSOLUTE/PATH/TO/sample-repo --output /ABSOLUTE/PATH/TO/bootstrap.md`

2. Materialize the required files into the target repo:

   `python3 scripts/required_files_bootstrap.py materialize --prompt /ABSOLUTE/PATH/TO/bootstrap.md --target-repo-path /ABSOLUTE/PATH/TO/sample-repo`

3. Validate that the required files and anchors exist:

   `python3 scripts/required_files_bootstrap.py validate --target-repo-path /ABSOLUTE/PATH/TO/sample-repo`

For a bounded local end-to-end proof, run `smoke-test` to emit an inspectable
bootstrap artifact, create the files in a scratch repo, and write a summary of
the result.
