# Automator — PiKVM Visual-Language MCP (Portfolio Project)

This repository is a personal portfolio project demonstrating a small, practical
tooling stack for automating and inspecting a remote machine using PiKVM and
MCP (Model Call Protocol) tooling. It provides screen-capture, visual analysis,
and HID control helpers wrapped as MCP tools so they can be integrated into
experiments, demos, or other personal automation workflows.

Highlights

- Self-contained MCP server for PiKVM screenshot capture and HID control
- Tools to capture, analyze, ground, and execute UI-directed actions
- Designed for offline/self-directed usage with optional external model hooks

Tech stack

- Python 3.14
- Minimal dependencies (see `pyproject.toml`)
- PiKVM-compatible HTTP endpoints for screenshots and HID events

Quick start

1. Create a local virtual environment and install dependencies:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Set required environment variables (or create a local `.env` file):

- `PI_KVM_USER` (required)
- `PI_KVM_PASS` (required)
- `PI_KVM_URL` (optional, default: `https://10.0.0.168`)

3. Run the MCP server locally:

```sh
python -m pikvm_vl_mcp.server
```

Files of interest

- `pyproject.toml` — project metadata and dependencies
- `pikvm_vl_mcp/server.py` — entrypoint that runs the MCP server
- `pikvm_vl_mcp/__init__.py` — package init and exports

How it works (brief)

The server exposes MCP-style tools such as `capture_pikvm_screenshot`,
`analyze_pikvm_screen`, and `execute_pikvm_computer_action`. By default the
workflow is self-directed: screenshots are captured and stored locally, and
analysis tools return grounded coordinates and textual guidance without
automatically delegating to remote LLM/VL models unless explicitly enabled.

License

This repository is released under the MIT License. See `LICENSE` for details.

Notes

- This project is intended as a demonstrative portfolio piece. Remove any
  sensitive credentials before publishing the repository publicly.

If you'd like I can (a) create a GitHub repository for this project and push
the current workspace as the initial commit, or (b) just prepare the files and
leave pushing to you. Tell me which option you prefer.
