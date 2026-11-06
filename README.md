# kgromero-mcp

<!-- mcp-name: io.github.romero927/kgromero-mcp -->

An [MCP](https://modelcontextprotocol.io) server for [kgromero.com](https://kgromero.com). It lets AI assistants (Claude, Cursor, etc.) answer questions about Kyle Romero's experience, skills, projects, and portfolio from the content on the site.

## Use it

The server is hosted at **`https://kgromero.com/mcp`** (Streamable HTTP, read-only, no sign-in).

```bash
claude mcp add --transport http kgromero https://kgromero.com/mcp
```

In Claude (web/desktop): Settings → Connectors → Add custom connector → paste the URL. In Cursor and other clients:

```json
{ "mcpServers": { "kgromero": { "url": "https://kgromero.com/mcp" } } }
```

Or run it locally from [PyPI](https://pypi.org/project/kgromero-mcp/) over stdio:

```bash
uvx kgromero-mcp
```

```json
{ "mcpServers": { "kgromero": { "command": "uvx", "args": ["kgromero-mcp"] } } }
```

It's also listed in the [MCP Registry](https://registry.modelcontextprotocol.io) as `io.github.romero927/kgromero-mcp`.

## How it works

Data comes from the live site and is cached for an hour:

- `https://kgromero.com/api/resume`: resume in [JSON Resume](https://jsonresume.org) format
- `https://kgromero.com/api/site.json`: the rest of the home page (title timeline, skill categories, side projects, documents, interests, travel)
- `https://kgromero.com/kgromero.md`: resume as Markdown

If the site can't be reached, the server uses the copies bundled in `src/kgromero_mcp/snapshot/`.

## Tools

| Tool | What it returns |
| --- | --- |
| `get_profile` | Name, title, summary, about, location, current role, years of experience |
| `get_contact` | Email, phone, LinkedIn/GitHub/Keyoxide, vCard link |
| `list_experience` | One entry per employer with dates and length |
| `get_experience(company_or_id)` | Full highlights and tech stack for a role |
| `get_career_timeline` | Every title held, including promotions |
| `get_education` | Degrees |
| `get_skills(filter?)` | Skills by category, and tech used on the job with where and how recently |
| `list_side_projects` | Personal projects with GitHub links |
| `list_documents(group?)` | Resume downloads plus the portfolio (diagrams, write-ups, lab reports) by employer |
| `get_document(document, pages?, as_images?)` | Reads a listed document: PDF text, diagrams as images, PDF pages rendered as images (up to 5 per call) |
| `get_interests` | Hobbies and favorite tech news sites |
| `get_travel(lived_only?)` | Places lived and visited |
| `search(query)` | Keyword search across all of the above |

**Resources:** `resume://kgromero/json`, `resume://kgromero/markdown`, `resume://kgromero/site`

**Prompt:** `assess_job_fit(job_description)`: compares the resume to a job posting.

## Run it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/kgromero-mcp            # stdio (for local clients)
.venv/bin/kgromero-mcp --http     # Streamable HTTP at http://127.0.0.1:8000/mcp
```

### Claude Code

```bash
claude mcp add kgromero -- /path/to/kgromero-mcp/.venv/bin/kgromero-mcp
```

### Claude Desktop / other clients

```json
{
  "mcpServers": {
    "kgromero": { "command": "/path/to/kgromero-mcp/.venv/bin/kgromero-mcp" }
  }
}
```

### Settings

| Env var | Default | Purpose |
| --- | --- | --- |
| `KGROMERO_SITE_URL` | `https://kgromero.com` | Point at another deployment, e.g. `http://localhost:5173` |
| `KGROMERO_OFFLINE` | unset | Set to `1` to skip the network and use the bundled snapshot |

## Deployment

Runs on Fly.io as the `kgromero-mcp` app (`Dockerfile`, `fly.toml`). The machine stops when idle and starts on the next request. `kgromero.com/mcp` forwards to `https://kgromero-mcp.fly.dev/mcp` through a route in the site repo (`src/routes/mcp/+server.js`).

```bash
fly deploy --remote-only
```

## Releasing

1. Bump the version in `pyproject.toml` and in both places in `server.json`.
2. Commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`.

`.github/workflows/publish.yml` checks the versions match the tag, runs the tests, publishes to PyPI, and then updates the MCP Registry listing. PyPI uses trusted publishing (GitHub environment `pypi`) and the registry uses GitHub OIDC, so no secrets are stored.

## Development

```bash
.venv/bin/pytest
```

Tests run offline against the snapshot. To refresh it after editing the site:

```bash
curl -s https://kgromero.com/api/resume    > src/kgromero_mcp/snapshot/kgromero.json
curl -s https://kgromero.com/api/site.json > src/kgromero_mcp/snapshot/site.json
curl -s https://kgromero.com/kgromero.md   > src/kgromero_mcp/snapshot/kgromero.md
```
