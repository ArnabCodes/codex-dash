# codex-dash

`codex-dash` is a local-first terminal dashboard for Codex CLI sessions. It indexes local Codex threads, shows active/stale sessions, token and rate-limit snapshots, account context, project grouping, and pooled session state from other machines.

The dashboard never SSHes or fetches remote data while opening the UI. Each machine runs `codex-dash` locally and writes JSON snapshots into its own board folder. Cross-machine awareness comes from syncing those JSON files between machines.

## Install

Recommended with `pipx`:

```sh
pipx install git+https://github.com/ArnabCodes/codex-dash.git
```

With `pip`:

```sh
python -m pip install --user git+https://github.com/ArnabCodes/codex-dash.git
```

From a local clone:

```sh
python -m pip install --user .
```

Windows source-clone shim installer:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-codex-dash.ps1 -AddToPath
```

Linux/macOS source-clone shim installer:

```sh
sh ./install-codex-dash.sh
```

## Quick Start

```sh
codex-dash refresh
codex-dash
codex-dash keys
```

By default, board data is stored at:

```text
~/.codex/instance-board
```

Override it with:

```sh
export CODEX_BOARD_HOME=/path/to/codex-board
```

On PowerShell:

```powershell
$env:CODEX_BOARD_HOME = "D:\Synced\codex-board"
```

## What It Shows

- local and synced Codex sessions
- active/recent/stale state
- working/waiting/done/closed activity inferred from rollout events
- token usage and context-window use from Codex `token_count` events
- rate-limit usage, reset timing, and plan type when present
- current Codex account label from local `auth.json`
- project/subproject grouping
- project context summaries
- local, remote, SSH, and tmux origin labels

## Keys

Run:

```sh
codex-dash keys
```

Common keys:

- `j` / `k` or arrow keys: move selection
- `h` / `l`: move focus between Projects and Sessions
- `Tab` / `Shift-Tab`: switch Projects/Sessions focus
- `[` / `]`: cycle project filter
- `/`: search sessions
- `s`: cycle status filter
- `S`: cycle sort mode
- `a`: show all projects and statuses
- `x`: clear filters
- `c`: create a project and Markdown context file
- `p`: assign selected session to the current/project id
- `r`: refresh local session export in the background
- `o`: open/attach selected SSH or tmux session when metadata exists
- `Enter`: resume selected session
- `?`: show key overlay
- `q` / `Esc`: quit

Mouse support:

- click a project to filter
- click a session row to select it

## Projects

Projects are read from:

```text
~/.codex/instance-board/projects.yaml
```

Use `projects.example.yaml` as a template:

```yaml
projects:
  - id: example
    name: Example Project
    roots:
      - C:\path\to\project
    subprojects:
      - id: default
        name: Default
        cwd: C:\path\to\project
```

Create a project:

```sh
codex-dash project add my-project --name "My Project" --context "Top-level context"
```

Assign a session:

```sh
codex-dash assign <session-id-or-prefix> my-project
```

Project context Markdown files live under:

```text
~/.codex/instance-board/projects/
```

## Multi-Machine Pooling

Install `codex-dash` on each machine. Then sync only these board files:

```text
~/.codex/instance-board/machines/*.json
~/.codex/instance-board/sessions/*.json
```

For Windows-to-Windows SSH setups, use:

```powershell
.\sync-instances.ps1 -Targets host1,host2
```

The helper refreshes local and remote exports, pulls remote machine/session JSON, then pushes the pooled JSON back. It does not copy Codex auth, full profiles, project context, or rollout transcripts.

## SSH And Tmux Metadata

Launch future sessions through the wrapper to label origins and record attach commands:

```sh
codex-dash launch --origin ssh --origin-hint laptop -- codex
codex-dash launch --origin tmux --tmux-session main --attach-command "tmux attach -t main" -- codex
```

In the TUI, press `o` to run a recorded attach command. `Enter` still performs normal local `codex resume`.

## Commands

```sh
codex-dash
codex-dash --plain
codex-dash --auto-refresh 5
codex-dash refresh
codex-dash list
codex-dash keys
codex-dash pick
codex-dash resume <session-id-or-prefix>
codex-dash attach <session-id-or-prefix>
codex-dash sync <ssh-target>
codex-dash watch --sync-target <ssh-target>
codex-dash where
```

## Live Refresh

For near-immediate local updates, run a watcher on each machine:

```sh
codex-dash watch
```

The watcher polls only Codex's own state database and rollout files, debounces changes, refreshes the local board JSON, and writes a heartbeat every few seconds even when nothing changes. It does not make the dashboard wait on remote machines.

To also pool another installed machine over SSH:

```sh
codex-dash watch --sync-target arnabthinkpad
```

If SSH logs into a different account than the desktop user running Codex, pin the remote state roots explicitly:

```sh
codex-dash watch --sync-target arnabthinkpad \
  --remote-codex-home C:/Users/Administrator/.codex \
  --remote-board-path C:/Users/Administrator/.codex/instance-board
```

For a one-shot sync:

```sh
codex-dash sync arnabthinkpad
```

With explicit remote paths:

```sh
codex-dash sync arnabthinkpad \
  --remote-codex-home C:/Users/Administrator/.codex \
  --remote-board-path C:/Users/Administrator/.codex/instance-board
```

Repeat `--sync-target` or pass multiple targets to `sync` for more machines. Only `machines/*.json` and `sessions/*.json` are copied.

For repeated use, configure peers once in:

```text
~/.codex/instance-board/peers.json
```

Example:

```json
{
  "peers": [
    {
      "target": "arnabthinkpad",
      "local_codex_home": "C:/Users/arnab/.codex",
      "local_board_path": "C:/Users/arnab/.codex/instance-board",
      "remote_codex_home": "C:/Users/Administrator/.codex",
      "remote_board_path": "C:/Users/Administrator/.codex/instance-board"
    }
  ]
}
```

Then these commands use the configured peers:

```sh
codex-dash sync
codex-dash watch
```

## Privacy

The repository intentionally ignores local board state such as `sessions/`, `machines/`, `projects/`, summaries, assignments, and launch metadata. Those files may contain private paths, prompts, account labels, and machine names.

`codex-dash` reads local Codex files but does not upload them. Cross-machine pooling only happens when you explicitly sync the board JSON files.
