# bmfote

Cloud-synced experiential memory for AI agents, powered by Turso (libSQL).

Two phases: deploy a server once per team, then connect each machine.

---

## Part 1: Deploy the server

You need a Turso database and any Docker-compatible host. Three steps.

### Step 1 — Create a Turso database

```bash
turso db create bmfote-memory
turso db show bmfote-memory --url              # -> libsql://...
turso db tokens create bmfote-memory --expiration none
```

Save the URL and token. You'll pass them to the server as environment variables.

### Step 2 — Apply the schema and generate an API token

```bash
git clone https://github.com/bmfote/bmfote && cd bmfote
turso db shell bmfote-memory < engine/schema.sql
openssl rand -hex 32    # save this — every client needs it
```

### Step 3 — Deploy the server

The server is a single `Dockerfile`. Pick your provider:

<details>
<summary><strong>Railway</strong></summary>

```bash
railway init
railway variables --set TURSO_DATABASE_URL=libsql://...
railway variables --set TURSO_AUTH_TOKEN=...
railway variables --set API_TOKEN=...
railway up
railway domain                                 # your public URL
```
</details>

<details>
<summary><strong>Fly.io</strong></summary>

```bash
fly launch --no-deploy
fly secrets set TURSO_DATABASE_URL=libsql://... \
                TURSO_AUTH_TOKEN=... \
                API_TOKEN=...
fly deploy
```
</details>

<details>
<summary><strong>Bare Docker</strong></summary>

```bash
docker build -t bmfote .
docker run -d -p 8000:8000 \
  -e TURSO_DATABASE_URL=libsql://... \
  -e TURSO_AUTH_TOKEN=... \
  -e API_TOKEN=... \
  bmfote
```
</details>

### Required environment variables

| Var | Required | Purpose |
|---|---|---|
| `TURSO_DATABASE_URL` | yes | `libsql://...` from `turso db show` |
| `TURSO_AUTH_TOKEN` | yes | from `turso db tokens create` |
| `API_TOKEN` | yes | shared secret clients send as `Authorization: Bearer` |
| `PORT` | no | defaults to 8000; providers set this automatically |

The server **fails closed**: it refuses to start without `API_TOKEN` in cloud mode.

### Verify

```bash
curl https://your-domain/health
curl -H "Authorization: Bearer $API_TOKEN" https://your-domain/api/stats
```

---

## Part 2: Connect a machine

On each machine you want to connect to the same memory:

```bash
npx bmfote setup --url https://your-domain --token <API_TOKEN>
```

This configures Claude Code on the current machine:
- Registers an MCP server (`bmfote-memory`) that exposes 5 memory tools
- Installs hooks at `~/.claude/hooks/bmfote-*.sh` for automatic session sync
- Writes `~/.claude/bmfote.env` with the URL and token
- Merges hook entries into `~/.claude/settings.json`

Safe to re-run. Run once per machine.

---

## Local development

```bash
source .venv/bin/activate      # Python 3.12
python -m engine.server        # starts on PORT from .env (default 8026)
```

Local dev uses an embedded libSQL replica at `engine/local-replica.db` that syncs
to your Turso database. Auth is optional locally (no `API_TOKEN` required).
