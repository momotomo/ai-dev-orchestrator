# Markdown Fidelity Feasibility

This document records the feasibility verdict for **Plan B**:
using a ChatGPT UI copy-response path instead of visible DOM text when the
bridge needs markdown-fidelity reply retrieval.

This phase is a bounded feasibility / fidelity test only.
It does **not** redesign the reply contract, issue create/close behavior, or
the broader state machine.

The first-party path remains:

- ChatGPT Projects
- Safari on macOS
- Codex CLI

The bridge remains a narrow first-party workflow, not a generic browser
automation framework.

## Question

Can the bridge safely treat a ChatGPT UI copy-response path as the primary
reply-retrieval route without switching to BODY/base64 transport first?

The acceptance bar for Plan B was:

- preserve `## Heading`
- preserve markdown list markers
- preserve inline code
- preserve fenced code
- preserve blank lines
- reproduce the same behavior over repeated retrievals
- avoid breaking same-chat, late completion, or project-page recovery

If that bar is not met, the project should switch to **Plan A:
BODY base64 transport**.

## Where The Current Runtime Loses Markdown

The current runtime still reads ChatGPT replies as visible page text:

- `scripts/_bridge_common.py`
  - `_build_visible_text_script()`
  - `read_chatgpt_conversation_dom()`
  - `_wait_for_chatgpt_reply_text()`
- `scripts/fetch_next_prompt.py`

`read_chatgpt_conversation_dom()` uses `innerText` / `textContent` from
`main`, `article`, and `body`.
That flattens rendered content before the bridge runs its reply-contract
parsing.

In practice this means:

- heading markers such as `##` disappear
- inline code backticks disappear
- fenced code markers disappear
- list markers may disappear entirely
- only the rendered surface text remains

The current visible-text route is therefore not safe for markdown-source
fidelity.

## Candidate Paths Tested

### 1. Visible DOM Text

This is the current runtime path.

Observed on a dedicated markdown fixture conversation:

```text
ChatGPT:
Thought for 4s
Heading
bullet one
bullet two
Inline x sample.
Python


実行する
print("hello")
```

Observed marker retention:

- `## Heading`: no
- markdown list marker: no
- inline code backticks: no
- fenced code marker: no
- blank lines: yes

This confirms the current route is lossy.

### 2. UI Copy Button + Clipboard

The bridge can find ChatGPT's visible copy button in Safari:

- `data-testid="copy-turn-action-button"`
- `aria-label="回答をコピーする"`

Using the copy button on the same fixture conversation produced markdown-shaped
text such as:

```md
## Heading

* bullet one
* bullet two

Inline `x` sample.

```python
print("hello")
```
```

This route preserved:

- heading marker
- markdown list structure
- inline code backticks
- fenced code markers
- blank lines

However, it did **not** meet the full acceptance bar:

- the copied list marker came back as `*`, not the original `-`
- repeated retrievals were not byte-identical
- one observed variant re-indented `Inline \`x\` sample.` under the second list
  item

Observed repeated outputs on the same reply alternated between:

- `tests/fixtures/markdown_fidelity/copied_reply_variant_a.md`
- `tests/fixtures/markdown_fidelity/copied_reply_variant_b.md`

Those two variants both keep markdown markers, but they hash differently and
therefore do not provide a stable lossless transport.

### 3. New-Chat / Project-Page Fresh-Fixture Send

We also tried using live Safari automation to generate a fresh fixture reply on:

- generic ChatGPT home
- the `ai-dev-orchestrator` project page
- an existing markdown fixture conversation

Those sends did not produce a stable enough fresh-fixture loop for this phase:

- generic home and project-page probes failed to confirm a new copied reply
- an existing conversation send did not reliably produce a new assistant-copy
  button count increase within the probe window

That does not prove the send path is impossible.
It does mean the repo does not yet have enough evidence to declare the full
Plan B route stable for normal bridge operation.

## Probe Support In Repo

The repo now includes a read-only probe:

- `scripts/markdown_fidelity_probe.py`

It can:

- optionally open a temporary Safari window for a supplied conversation URL
- read the latest assistant reply as visible text
- click the latest `回答をコピーする` button
- repeat the clipboard capture multiple times
- save visible/copy logs under `logs/`

Supporting observed fixtures live in:

- `tests/fixtures/markdown_fidelity/visible_reply.txt`
- `tests/fixtures/markdown_fidelity/copied_reply_variant_a.md`
- `tests/fixtures/markdown_fidelity/copied_reply_variant_b.md`

## Verdict

**Switch to Plan A (BODY base64 transport).**

Plan B is promising as a diagnostic or manual fallback, but it does **not**
clear the bar to become the primary bridge reply-retrieval path yet.

The deciding reasons are:

1. the current visible-text route is clearly lossy
2. the copy-button route is closer to lossless, but not stable enough
3. exact markdown list-marker fidelity (`-`) was not demonstrated
4. repeated retrievals from the same reply produced non-identical markdown
5. fresh-fixture live-send probing was not stable enough to promote this route
   as the normal runtime path

## What This Means Next

The next bounded runtime-ready slice should treat **Plan A** as the safer
default direction:

- move reply-body transport away from visible DOM text
- preserve markdown source explicitly
- keep same-chat, late completion, `submitted_unconfirmed`, and
  `pending_request_signal` semantics unchanged while changing only the transport
  layer

Plan B can stay available as a debugging aid or secondary diagnostic route, but
the repo should not make it the primary reply-fidelity mechanism on the
strength of this phase alone.
