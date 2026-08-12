# Cloudflare Tunnel and Access deployment

Keep the gateway bound to `127.0.0.1:8798`. A Cloudflare Tunnel makes an
outbound connection, so the sidecar does not need a public listener or an open
inbound firewall port.

## 1. Configure the gateway hostname

Add the exact public hostname to the MCP transport allowlist. Keep the local
hostnames for direct administration:

```toml
[server]
host = "127.0.0.1"
port = 8798
allowed_hosts = [
  "127.0.0.1:8798",
  "localhost:8798",
  "ductor-mcp.example.com",
]
```

Do not disable DNS-rebinding protection. If a browser-based MCP client sends an
`Origin` header, add its exact HTTPS origin under `allowed_origins` as well.

## 2. Publish every route through one tunnel rule

For a locally managed tunnel, use a hostname rule without a path condition.
That sends `/mcp`, `/health`, and any future route through the same protected
origin. The final catch-all rule is required by `cloudflared`.

```yaml
tunnel: <TUNNEL-UUID>
credentials-file: /etc/cloudflared/<TUNNEL-UUID>.json

ingress:
  - hostname: ductor-mcp.example.com
    service: http://127.0.0.1:8798
  - service: http_status:404
```

Validate before starting the tunnel:

```console
cloudflared tunnel ingress validate
cloudflared tunnel ingress rule https://ductor-mcp.example.com/mcp
cloudflared tunnel ingress rule https://ductor-mcp.example.com/health
```

Cloudflare's current configuration reference documents hostname-wide ingress
rules and the mandatory final catch-all:
<https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/>.

## 3. Protect the hostname, not only `/mcp`

Create one Cloudflare Access self-hosted application for
`ductor-mcp.example.com` and leave its Path field empty. This protects the apex
hostname and all paths, including `/health`. Do not create a Bypass policy for
health checks. Cloudflare documents the empty-path behavior here:
<https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/>.

For machine clients, create a Service Token and a `Service Auth` policy. Clients
send both Cloudflare headers on every request:

```text
CF-Access-Client-Id: <client-id>
CF-Access-Client-Secret: <client-secret>
Authorization: Bearer <gateway-token>
```

The Cloudflare credentials and the gateway bearer token are independent layers.
Do not configure Cloudflare to consume the `Authorization` header for its single-
header service-token mode: this gateway needs that header for its own bearer
token. Use the standard two Cloudflare headers documented at
<https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/>.

Confirm that the chosen MCP client supports all three static headers. Keep both
secrets in its secret store, never in a command line or checked-in config.

## 4. Operational checks

- Disable caching for this hostname; MCP POST/GET/DELETE traffic and session
  headers must reach the origin.
- Test an unauthenticated request and verify Cloudflare Access rejects it before
  it reaches the sidecar.
- Test an Access-authenticated request without the gateway bearer token and
  verify the sidecar returns `401`.
- Monitor Access logs and the sidecar's prompt-free audit events.
- Rotate Cloudflare and gateway tokens independently.

