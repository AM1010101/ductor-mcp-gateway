"""Command-line entry point. Secrets are read from environment or files, never argv."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from . import __version__
from .app import create_application
from .config import generate_token_file, load_bearer_token, load_settings, load_upstream_token


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ductor-mcp-gateway")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="serve authenticated MCP Streamable HTTP")
    serve.add_argument(
        "--config",
        type=Path,
        help="TOML configuration path (or DUCTOR_MCP_GATEWAY_CONFIG)",
    )

    generate = subcommands.add_parser("generate-token", help="write a new bearer token file")
    generate.add_argument(
        "--output",
        type=Path,
        default=Path("gateway.token"),
        help="token file destination (default: ./gateway.token)",
    )
    generate.add_argument(
        "--force",
        action="store_true",
        help="replace an existing regular token file",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "generate-token":
        try:
            path = generate_token_file(args.output, force=args.force)
        except FileExistsError:
            print("Token file already exists; use --force to replace it.")
            return 2
        print(f"Token written to {path} with mode 0600.")
        return 0

    settings, config_dir = load_settings(args.config)
    token = load_bearer_token(settings.auth, config_dir)
    upstream_token = load_upstream_token(settings.ductor, config_dir)
    logging.basicConfig(
        level=getattr(logging, settings.logging.level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("mcp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = create_application(settings, bearer_token=token, ductor_bearer_token=upstream_token)
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        limit_concurrency=settings.limits.max_concurrent_requests,
    )
    return 0
