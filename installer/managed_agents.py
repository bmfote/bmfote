"""Managed Agents API helper — wires cctx MCP into Anthropic-hosted agents.

One module for create/run/doctor. Idempotent: re-runs converge on the correct
shape. No state file — shared resources are discovered by name.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ANTHROPIC_API = "https://api.anthropic.com/v1"
BETA_HEADER = "managed-agents-2026-04-01"
VAULT_NAME = "cctx-default"
ENV_NAME = "cctx-default-env"
DEFAULT_MODEL = "claude-opus-4-6"


def _read_env_file(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _load_claude_cctx() -> dict:
    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        return {}
    try:
        data = json.loads(claude_json.read_text())
    except json.JSONDecodeError:
        return {}
    return data.get("mcpServers", {}).get("cctx-memory", {}) or {}


def _load_anthropic_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY") or _read_env_file(Path.home() / ".anthropic.env", "ANTHROPIC_API_KEY")
    if key:
        return key
    raise RuntimeError("ANTHROPIC_API_KEY not set (env var or ~/.anthropic.env)")


def _load_cctx_url() -> str:
    """Return the cctx base URL with no trailing slash.

    Source order: CCTX_URL env var → ~/.claude.json mcpServers.cctx-memory.url
    (stripped of the trailing /mcp/). Never defaults to a hardcoded host so this
    module is safe to distribute across workspaces.
    """
    url = os.environ.get("CCTX_URL")
    if not url:
        mcp_url = _load_claude_cctx().get("url", "")
        if mcp_url:
            url = mcp_url.rstrip("/")
            if url.endswith("/mcp"):
                url = url[: -len("/mcp")]
    if not url:
        raise RuntimeError("CCTX_URL not set and no cctx-memory MCP entry in ~/.claude.json")
    return url.rstrip("/")


def _load_cctx_token() -> str:
    token = os.environ.get("CCTX_TOKEN")
    if token:
        return token
    auth = _load_claude_cctx().get("headers", {}).get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    raise RuntimeError("CCTX_TOKEN not set and no Bearer token in ~/.claude.json")


def _cctx_mcp_url() -> str:
    return f"{_load_cctx_url()}/mcp/"


def _cctx_host() -> str:
    from urllib.parse import urlparse
    return urlparse(_load_cctx_url()).hostname or ""


def _api(method: str, path: str, body=None) -> dict:
    url = f"{ANTHROPIC_API}{path}"
    headers = {
        "x-api-key": _load_anthropic_key(),
        "anthropic-version": "2023-06-01",
        "anthropic-beta": BETA_HEADER,
        "content-type": "application/json",
    }
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        raise RuntimeError(f"{method} {path} → HTTP {e.code}: {err_body}") from None


def _list_all(path: str, key: str = "data") -> list:
    out = []
    cursor = None
    while True:
        q = f"?starting_after={cursor}" if cursor else ""
        resp = _api("GET", f"{path}{q}")
        out.extend(resp.get(key, []))
        cursor = resp.get("next_page")
        if not cursor:
            break
    return out


def ensure_vault() -> str:
    """Find-or-create the cctx vault and its static_bearer credential."""
    mcp_url = _cctx_mcp_url()
    vaults = _list_all("/vaults")
    vault = next((v for v in vaults if v.get("display_name") == VAULT_NAME and not v.get("archived_at")), None)
    if vault is None:
        vault = _api("POST", "/vaults", {"display_name": VAULT_NAME})
    vault_id = vault["id"]

    creds = _list_all(f"/vaults/{vault_id}/credentials")
    has_cred = any(
        c.get("auth", {}).get("mcp_server_url", "").rstrip("/") == mcp_url.rstrip("/")
        and not c.get("archived_at")
        for c in creds
    )
    if not has_cred:
        _api("POST", f"/vaults/{vault_id}/credentials", {
            "display_name": "cctx bearer",
            "auth": {
                "type": "static_bearer",
                "mcp_server_url": mcp_url,
                "token": _load_cctx_token(),
            },
        })
    return vault_id


def ensure_env() -> str:
    """Find-or-create the cctx environment with allowed_hosts populated."""
    host = _cctx_host()
    envs = _list_all("/environments")
    env = next((e for e in envs if e.get("name") == ENV_NAME and not e.get("archived_at")), None)
    body_config = {
        "type": "cloud",
        "networking": {
            "type": "limited",
            "allow_mcp_servers": True,
            "allow_package_managers": False,
            "allowed_hosts": [host],
        },
    }
    if env is None:
        env = _api("POST", "/environments", {
            "name": ENV_NAME,
            "description": "Default env for cctx-wired managed agents.",
            "config": body_config,
        })
        return env["id"]
    # Patch if allowed_hosts drifted
    net = env.get("config", {}).get("networking", {})
    if host not in (net.get("allowed_hosts") or []) or not net.get("allow_mcp_servers"):
        _api("POST", f"/environments/{env['id']}", {"config": body_config})
    return env["id"]


def _agent_tools(include_web: bool) -> list:
    tools = [{
        "type": "mcp_toolset",
        "mcp_server_name": "cctx",
        "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
    }]
    if include_web:
        tools.insert(0, {
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
            "configs": [
                {"enabled": True, "name": "web_search", "permission_policy": {"type": "always_allow"}},
                {"enabled": True, "name": "web_fetch", "permission_policy": {"type": "always_allow"}},
            ],
        })
    return tools


def create_agent(name: str, system: str, include_web: bool = False, model: str = DEFAULT_MODEL) -> dict:
    body = {
        "name": name,
        "description": "cctx-wired managed agent" + (" with web tools" if include_web else " (memory-only)"),
        "model": model,
        "system": system,
        "mcp_servers": [{"type": "url", "name": "cctx", "url": _cctx_mcp_url()}],
        "tools": _agent_tools(include_web),
    }
    return _api("POST", "/agents", body)


def doctor_agent(agent_id: str, fix: bool = False) -> dict:
    """Return drift report for an agent. If fix=True, PATCH to correct shape."""
    mcp_url = _cctx_mcp_url()
    agent = _api("GET", f"/agents/{agent_id}")
    drift = []

    mcp_servers = agent.get("mcp_servers") or []
    has_cctx_server = any(
        s.get("name") == "cctx" and s.get("url", "").rstrip("/") == mcp_url.rstrip("/")
        for s in mcp_servers
    )
    if not has_cctx_server:
        drift.append("mcp_servers missing cctx entry")

    tools = agent.get("tools") or []
    mcp_toolset = next((t for t in tools if t.get("type") == "mcp_toolset" and t.get("mcp_server_name") == "cctx"), None)
    if mcp_toolset is None:
        drift.append("tools missing mcp_toolset for cctx")
    else:
        pol = (mcp_toolset.get("default_config") or {}).get("permission_policy", {}).get("type")
        if pol != "always_allow":
            drift.append(f"mcp_toolset permission_policy is {pol!r}, want always_allow")

    report = {"agent_id": agent_id, "agent_name": agent.get("name"), "drift": drift, "fixed": False}
    if drift and fix:
        has_web = any(t.get("type") == "agent_toolset_20260401" for t in tools)
        _api("POST", f"/agents/{agent_id}", {
            "version": agent.get("version", 1),
            "mcp_servers": [{"type": "url", "name": "cctx", "url": mcp_url}],
            "tools": _agent_tools(include_web=has_web),
        })
        report["fixed"] = True
    return report


def run_agent(agent_id: str, prompt: str, timeout: int = 300, title: str | None = None) -> str:
    """Create session, post user message, poll until idle, return final agent.message text."""
    vault_id = ensure_vault()
    env_id = ensure_env()
    session = _api("POST", "/sessions", {
        "agent": {"type": "agent", "id": agent_id},
        "environment_id": env_id,
        "vault_ids": [vault_id],
        **({"title": title} if title else {}),
    })
    sess_id = session["id"]
    _api("POST", f"/sessions/{sess_id}/events", {
        "events": [{"type": "user.message", "content": [{"type": "text", "text": prompt}]}],
    })
    # Poll events — terminal state is a session.status_idle as the last event
    # after at least one user.message has been processed. Polling /sessions
    # for `status: idle` is unreliable because sessions start idle before the
    # user.message triggers work.
    deadline = time.time() + timeout
    events = []
    while time.time() < deadline:
        events = _api("GET", f"/sessions/{sess_id}/events").get("data", [])
        if events and events[-1].get("type") == "session.status_idle" and any(
            e.get("type") == "user.message" for e in events
        ):
            break
        time.sleep(3)
    else:
        raise RuntimeError(f"Session {sess_id} did not finish within {timeout}s")

    agent_msgs = [e for e in events if e.get("type") == "agent.message"]
    if not agent_msgs:
        errors = [e.get("error") for e in events if e.get("type") == "session.error"]
        raise RuntimeError(f"No agent.message in session {sess_id}. Errors: {errors}")
    last = agent_msgs[-1]
    return "".join(c.get("text", "") for c in last.get("content", []) if c.get("type") == "text")


def list_agents() -> list:
    """List workspace agents with a flag for cctx wiring."""
    mcp_url = _cctx_mcp_url()
    agents = _list_all("/agents")
    out = []
    for a in agents:
        if a.get("archived_at"):
            continue
        wired = any(
            s.get("name") == "cctx" and s.get("url", "").rstrip("/") == mcp_url.rstrip("/")
            for s in (a.get("mcp_servers") or [])
        )
        out.append({"id": a["id"], "name": a.get("name"), "cctx": wired})
    return out


def _cli(argv: list) -> int:
    if not argv:
        print("usage: cctx-agent {create|run|doctor|list} ...", file=sys.stderr)
        return 2
    cmd, args = argv[0], argv[1:]

    if cmd == "create":
        name = None
        system = "You are a memory retrieval agent backed by cctx. Search the user's experiential memory to answer questions."
        include_web = False
        i = 0
        while i < len(args):
            if args[i] == "--name":
                name = args[i + 1]; i += 2
            elif args[i] == "--system":
                system = args[i + 1]; i += 2
            elif args[i] == "--web":
                include_web = True; i += 1
            else:
                print(f"unknown arg: {args[i]}", file=sys.stderr); return 2
        if not name:
            print("missing --name", file=sys.stderr); return 2
        ensure_vault(); ensure_env()
        agent = create_agent(name, system, include_web=include_web)
        print(agent["id"])
        return 0

    if cmd == "run":
        if len(args) < 2:
            print("usage: cctx-agent run AGENT_ID \"PROMPT\"", file=sys.stderr); return 2
        agent_id, prompt = args[0], args[1]
        print(run_agent(agent_id, prompt))
        return 0

    if cmd == "doctor":
        if not args:
            print("usage: cctx-agent doctor AGENT_ID [--fix]", file=sys.stderr); return 2
        agent_id = args[0]
        fix = "--fix" in args[1:]
        report = doctor_agent(agent_id, fix=fix)
        print(json.dumps(report, indent=2))
        return 0 if not report["drift"] or report["fixed"] else 1

    if cmd == "list":
        for a in list_agents():
            flag = "✓" if a["cctx"] else " "
            print(f"[{flag}] {a['id']}  {a['name']}")
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
