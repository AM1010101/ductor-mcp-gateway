# Ductor MCP Gateway

An independent Python 3.11+ sidecar that exposes Ductor's loopback internal HTTP
API as authenticated MCP Streamable HTTP. It does not import `ductor_bot`, edit
Ductor configuration, or run shell commands.

The default topology is:

```text
MCP client -> Bearer auth -> 127.0.0.1:8798/mcp
                              |
                              +-> policy + validation + audit
                                     |
                                     +-> http://127.0.0.1:8799
                                         Ductor internal API
```

## Security properties

- Every HTTP route, including `/health`, requires the gateway bearer token.
- Tokens come from a named environment variable or a mode-`0600` regular file.
  Token values are never accepted on argv or in TOML.
- Authentication compares fixed-length SHA-256 digests with
  `hmac.compare_digest`.
- The listener defaults to loopback. A non-loopback bind requires the explicit
  `allow_non_loopback=true` safety switch.
- MCP transport Host/Origin allowlists retain DNS-rebinding protection.
- The policy independently limits tool names and Ductor agent identities.
- Task list results are re-filtered by `parent_agent`; task create/resume/cancel
  preserve the caller's `from` ownership field exactly.
- The official MCP SDK limits request bodies. The edge also limits requests per
  minute, concurrent HTTP requests, active sessions, session idle time, upstream
  response size, and upstream request duration.
- Audit records contain event/result, tool name, request ID, agent/task IDs, and
  duration. They never contain bearer tokens, messages, prompts, task results, or
  upstream response bodies.
- The gateway exposes no raw shell or generic HTTP tool.

## Threat model

The bearer token is the MCP caller identity. Anyone holding it can use every
tool/agent allowed by that gateway instance, so use separate instances/tokens for
different trust domains. Policy is not per-user RBAC.

Ductor's checked source snapshot relies on loopback reachability; the live
runtime inspected during development additionally required its internal bearer
token. The sidecar supports both modes. A local process holding that upstream
credential may call Ductor directly and bypass this sidecar. OS account
isolation and host hardening remain required. The gateway does not make an
already compromised host safe.

TLS is intentionally left to a local reverse proxy, private overlay, or
Cloudflare Tunnel. Do not bind plain HTTP to a public interface. Rate/session
limits are in-memory and per process; run one worker unless you add an external
coordinating edge.

Tool output necessarily contains requested agent responses and task metadata.
Treat MCP clients as trusted data recipients. Prompt-free audit logs do not make
tool output non-sensitive.

## Install

```console
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

If the system Python lacks `ensurepip`, `uv venv .venv` followed by
`uv pip install --python .venv/bin/python -e '.[dev]'` is equivalent.

Generate a token without displaying it:

```console
.venv/bin/ductor-mcp-gateway generate-token --output ./gateway.token
```

Copy `config.example.toml` to a private deployment location, reduce
`allowed_agents` and `allowed_tools`, then start:

```console
DUCTOR_MCP_GATEWAY_CONFIG=/absolute/path/config.toml \
  .venv/bin/ductor-mcp-gateway serve
```

An environment token takes precedence over the token file. The environment
variable's name is configured by `auth.token_env` and defaults to
`DUCTOR_MCP_GATEWAY_TOKEN`.

The upstream Ductor credential is separate. By default the application reads
`DUCTOR_INTERAGENT_TOKEN`, matching current Ductor tool wrappers, and injects it
into the typed client. It can instead read a mode-`0600` file configured as
`ductor.token_file`; set `ductor.token_env = ""` if no environment lookup is
desired. Neither upstream credential source is passed on argv or logged.

All non-secret settings can also be overridden with environment variables. See
`src/ductor_mcp_gateway/config.py` for the explicit allowlist; common examples
are `DUCTOR_MCP_GATEWAY_DUCTOR_URL`, `DUCTOR_MCP_GATEWAY_ALLOWED_AGENTS`, and
`DUCTOR_MCP_GATEWAY_ALLOWED_TOOLS`. List values are comma-separated.

## MCP tools

- `ductor_agents_list`
- `ductor_agent_message`
- `ductor_agent_message_async`
- `ductor_tasks_create`
- `ductor_tasks_list`
- `ductor_tasks_resume`
- `ductor_tasks_cancel`

Message and task tools require an explicit `from_agent`. This retains Ductor's
ownership/routing semantics; both sender and recipient identities must pass
`allowed_agents`. Disabled tools are not advertised by `tools/list`.

## Service deployment

`deploy/ductor-mcp-gateway.service` is a hardened system-level systemd example.
Adjust the service account and paths, ensure that account can reach Ductor's
loopback listener, place the token in `/etc/ductor-mcp-gateway/` with ownership
for that account and mode `0600`, and make the current Ductor internal token
available through the unit's mode-`0600` `EnvironmentFile` or `ductor.token_file`.
Then install the unit. It intentionally does not share or modify Ductor's
installed service.

For public hostname deployment without opening a listener, see
[`docs/cloudflare.md`](docs/cloudflare.md). Protect the entire hostname in
Cloudflare Access so `/health` cannot become a bypass.

## Compatibility

The implementation targets the endpoint schemas documented by Ductor's
`docs/modules/multiagent.md` and implemented by
`ductor_bot/multiagent/internal_api.py` as inspected on 2026-08-12. The internal
API is not declared a stable external contract. This repository deliberately
copies no Ductor code and imports no Ductor package; schema drift therefore
fails closed as a typed upstream protocol error.

The dependency range `mcp>=1.29,<2` selects the official maintained Python MCP
SDK's stable 1.x line. MCP SDK v2 was still pre-release when this range was
chosen. Review the SDK migration guide and this gateway's middleware/lifespan
integration before widening the upper bound.

## Development checks

```console
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy src
```
