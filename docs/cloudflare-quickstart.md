# Cloudflare quick start

This guide takes an existing Ductor installation from local-only access to an
authenticated MCP endpoint at `https://ductor-mcp.example.com/mcp` without
opening an inbound port on the server.

You will create two independent authentication layers:

1. Cloudflare Access checks a service token at the public hostname.
2. Ductor MCP Gateway checks its own bearer token before forwarding to Ductor.

Replace `ductor-mcp.example.com` everywhere below with a hostname in a domain
already managed by your Cloudflare account.

## Before you start

You need:

- Ductor running on this server with its internal API on `127.0.0.1:8799`.
- Python 3.11 or newer, Git, and root or sudo access for systemd setup.
- A Cloudflare account with an active domain.
- `cloudflared` installed and access to the Cloudflare Zero Trust dashboard.
  Use Cloudflare's [official installation instructions](https://developers.cloudflare.com/tunnel/downloads/)
  if it is not already installed.

Confirm Ductor is listening locally:

```console
ss -ltn | grep '127.0.0.1:8799'
```

## 1. Install the gateway

The commands below use `/opt` for the application and a dedicated service user.

```console
sudo useradd --system --home /var/lib/ductor-mcp-gateway \
  --create-home --shell /usr/sbin/nologin ductor-mcp
sudo git clone https://github.com/AM1010101/ductor-mcp-gateway.git \
  /opt/ductor-mcp-gateway
sudo python3 -m venv /opt/ductor-mcp-gateway/.venv
sudo /opt/ductor-mcp-gateway/.venv/bin/pip install \
  /opt/ductor-mcp-gateway
sudo chown -R root:root /opt/ductor-mcp-gateway
```

If `python3 -m venv` reports that `ensurepip` is unavailable, install your
distribution's Python venv package first, such as `python3-venv` on Ubuntu.

## 2. Create the private configuration

Create the configuration directory and copy the example:

```console
sudo install -d -m 0750 -o root -g ductor-mcp /etc/ductor-mcp-gateway
sudo install -m 0640 -o root -g ductor-mcp \
  /opt/ductor-mcp-gateway/config.example.toml \
  /etc/ductor-mcp-gateway/config.toml
```

Edit `/etc/ductor-mcp-gateway/config.toml` and make these changes:

```toml
[server]
host = "127.0.0.1"
port = 8798
allow_non_loopback = false
allowed_hosts = [
  "127.0.0.1:8798",
  "localhost:8798",
  "ductor-mcp.example.com",
]

[policy]
allowed_agents = ["main"]
```

Leave only the agents and tools that the remote MCP client actually needs.

Generate the gateway bearer token directly into the protected directory:

```console
sudo /opt/ductor-mcp-gateway/.venv/bin/ductor-mcp-gateway generate-token \
  --output /etc/ductor-mcp-gateway/gateway.token
sudo chown ductor-mcp:ductor-mcp /etc/ductor-mcp-gateway/gateway.token
sudo chmod 0600 /etc/ductor-mcp-gateway/gateway.token
```

The example configuration resolves `gateway.token` relative to its own
directory, so no token value belongs in the TOML file.

## 3. Give the sidecar Ductor's internal credential

Current Ductor installations protect the internal API with
`DUCTOR_INTERAGENT_TOKEN`. The gateway needs the same value, but it must not be
printed, pasted into the repository, or placed on a command line.

Create `/etc/ductor-mcp-gateway/environment` with this single entry:

```text
DUCTOR_INTERAGENT_TOKEN=<the token used by the running Ductor service>
```

Then protect it:

```console
sudo chown root:ductor-mcp /etc/ductor-mcp-gateway/environment
sudo chmod 0640 /etc/ductor-mcp-gateway/environment
```

If Ductor already loads the token from an environment file, copy it locally on
the server without displaying it in a terminal or chat. Do not reuse this token
as the public gateway bearer token.

## 4. Install and start the systemd service

```console
sudo install -m 0644 \
  /opt/ductor-mcp-gateway/deploy/ductor-mcp-gateway.service \
  /etc/systemd/system/ductor-mcp-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable --now ductor-mcp-gateway
sudo systemctl status ductor-mcp-gateway --no-pager
```

Test both authentication outcomes locally. The first request must return `401`:

```console
curl -i http://127.0.0.1:8798/health
```

The authenticated request should return `200` without putting the token on the
command line:

```console
curl --config - <<'EOF'
url = "http://127.0.0.1:8798/health"
header = "Authorization: Bearer REPLACE_WITH_GATEWAY_TOKEN"
EOF
```

For real operation, configure your MCP client's secret store directly rather
than reading or pasting the token into shell history.

## 5. Create the Cloudflare Tunnel

First check whether this server already runs a connector:

```console
systemctl list-units 'cloudflared*' --no-pager
```

Cloudflare supports multiple published application routes on one tunnel, but
only one default `cloudflared` system service can be installed on a machine. If
a healthy tunnel already runs here, add this route to that tunnel instead of
installing another default service.

In the Cloudflare dashboard:

1. Open **Networking > Tunnels**.
2. Select **Create a tunnel**, choose **Cloudflared**, and name it
   `ductor-mcp-gateway`. Skip this step and select the existing tunnel if this
   server already has a healthy connector.
3. Choose your server's operating system and copy the displayed installation
   command. It contains the tunnel token, so run it only on this server and do
   not save it in shell history, chat, or source control.
4. Add a published application route:
   - Subdomain: `ductor-mcp`
   - Domain: your Cloudflare-managed domain
   - Service type: `HTTP`
   - URL: `127.0.0.1:8798`
5. Save the route and confirm the tunnel reports **Healthy**.

Cloudflare's current [Tunnel setup guide](https://developers.cloudflare.com/tunnel/setup/)
documents the dashboard-managed flow and the generated service-install command.

The connector is outbound-only. Do not open port `8798` in UFW, iptables, your
cloud security group, or your router.

## 6. Protect the whole hostname with Cloudflare Access

In **Access controls > Applications**:

1. Add a **Self-hosted** application.
2. Set the application domain to `ductor-mcp.example.com`.
3. Leave the application path empty so Access protects `/mcp`, `/health`, and
   every future route.
4. Create a Cloudflare Access **Service Token** for your MCP client.
5. Add a **Service Auth** policy whose Include rule selects that service token.
6. Do not add a Bypass policy for `/health`.

Record the service token's client ID and client secret in the MCP client's
secret store. Cloudflare only displays the client secret when it is created.

## 7. Configure the MCP client

The MCP endpoint is:

```text
https://ductor-mcp.example.com/mcp
```

Configure the client to send all three headers on every MCP request:

```text
CF-Access-Client-Id: <Cloudflare service-token client ID>
CF-Access-Client-Secret: <Cloudflare service-token client secret>
Authorization: Bearer <gateway bearer token>
```

Do not use Cloudflare's single-header service-token mode because it consumes the
`Authorization` header that the gateway needs for its independent bearer token.

## 8. Verify from another machine

Without any credentials, Cloudflare Access must reject the request before it
reaches the gateway:

```console
curl -i https://ductor-mcp.example.com/health
```

With valid Cloudflare credentials but no gateway bearer token, the gateway must
return `401`:

```console
curl -i https://ductor-mcp.example.com/health \
  -H 'CF-Access-Client-Id: <client-id>' \
  -H 'CF-Access-Client-Secret: <client-secret>'
```

Finally, connect using the MCP client with all three headers and confirm that
`tools/list` shows only the tools allowed in `config.toml`.

Confirm the origin remains private from the server itself:

```console
ss -ltn | grep ':8798'
```

The listening address must be `127.0.0.1:8798`, never `0.0.0.0:8798` or
`[::]:8798`.

## Troubleshooting

### Cloudflare returns 502

The tunnel cannot reach the gateway. Check:

```console
sudo systemctl status ductor-mcp-gateway --no-pager
sudo journalctl -u ductor-mcp-gateway -n 100 --no-pager
curl -i http://127.0.0.1:8798/health
```

### The gateway returns `Invalid host header`

Add the exact public hostname to `server.allowed_hosts` and restart:

```console
sudo systemctl restart ductor-mcp-gateway
```

### Health returns 503

The gateway is running but cannot authenticate to or reach Ductor. Confirm
Ductor listens on `127.0.0.1:8799` and that the sidecar receives the current
`DUCTOR_INTERAGENT_TOKEN`.

### The MCP client cannot connect

Confirm it supports Streamable HTTP and can attach three static headers to every
request. Check Cloudflare Access logs first, then the sidecar audit log:

```console
sudo journalctl -u ductor-mcp-gateway -f
```

### A tool or agent is missing

The gateway advertises only `policy.allowed_tools`, and message/task operations
also require the relevant names in `policy.allowed_agents`. Update the policy
and restart the service.

For locally managed tunnels, browser origins, caching guidance, and additional
security detail, see [Cloudflare deployment reference](cloudflare.md).
