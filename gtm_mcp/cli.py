"""Command-line entry point: ``gtm-mcp [auth [--with-writes]|status|logout|help|serve]`` (default: serve on stdio)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from marketing_mcp_common.errors import AuthError
from marketing_mcp_common.google_auth import GoogleCredentialStore, GoogleOAuthDesktopFlow, GoogleTokenProvider

from . import config

HELP = f"""gtm-mcp {config.VERSION} — Google Tag Manager MCP server (runs on your own computer)

Commands
  gtm-mcp auth                Sign in with your Google account in the browser (one time), READ-ONLY scope.
  gtm-mcp auth --with-writes  Same, but also request the edit + version + publish scopes so the write tools work
                              (tags/triggers/variables in a workspace, create version, publish). Never requests
                              delete-container or user-management scopes.
                              Needs a Google Cloud OAuth client of type "Desktop app": pass --client-id/--client-secret
                              or set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET. The refresh token is stored in
                              {config.config_dir() / 'config.json'} (only readable by you).
  gtm-mcp status              Signed in? Which profile (read-only / read+write+publish)? Token refresh OK?
  gtm-mcp logout              Revoke the token at Google and delete the local config file.
  gtm-mcp serve               Start the MCP server on stdio (this is what Claude Desktop / Cursor run).
  gtm-mcp help                This text.

Environment variables (optional)
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN   use these credentials instead of the config file
  GOOGLE_SCOPES                space-separated scopes granted to that refresh token (lets the server know the profile)
  {config.ENV_CONFIG_DIR}   folder for config.json + audit.jsonl (default ~/.gtm-mcp)
  {config.ENV_AUDIT_DISABLED}   1 = turn off the local audit log
  {config.ENV_RATE_LIMIT_QPS}   client-side request rate (default {config.RATE_LIMIT_QPS} = Google's limit of 25 requests / 100 s)
  MCP_LOG_LEVEL   DEBUG | INFO | WARNING (logs go to stderr only)

Claude Desktop config example
  "gtm": {{ "command": "uvx", "args": ["--from", "git+https://github.com/Mezbaul-Hasan/GTM-MCP-Server", "gtm-mcp"] }}
"""


def _eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def _store() -> GoogleCredentialStore:
    return GoogleCredentialStore(config.config_dir(), env_prefix="GOOGLE")


def cmd_auth(args: argparse.Namespace) -> int:
    client_id = (args.client_id or os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (args.client_secret or os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    if not client_id:
        client_id = input("Google OAuth client ID (Desktop app): ").strip()
    if not client_secret:
        import getpass

        client_secret = getpass.getpass("Google OAuth client secret (input hidden): ").strip()
    if not client_id or not client_secret:
        _eprint("Both client id and client secret are required. Create them in Google Cloud Console → APIs & Services → Credentials → OAuth client ID → Desktop app.")
        return 2
    scopes = list(config.SCOPES_WRITES if args.with_writes else config.SCOPES_READONLY)
    _eprint(f"Requesting scopes: {'read + write + publish' if args.with_writes else 'read-only'}")
    flow = GoogleOAuthDesktopFlow(
        app_name=config.APP_NAME,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
        open_browser=not args.no_browser,
        login_hint=args.login_hint,
    )
    try:
        creds = flow.run(echo=_eprint)
    except AuthError as exc:
        _eprint(f"❌ {exc}")
        return 1
    path = _store().save(creds)
    profile = config.profile_for_scopes(creds.scopes)
    _eprint(f"✅ Signed in ({profile}). Credentials saved to {path}")
    _eprint(f"   Scopes: {' '.join(creds.scopes)}")
    if args.with_writes and profile == "read-only":
        _eprint("⚠️  Google granted the read-only scope only — did you untick the edit/publish boxes on the consent screen? Run `gtm-mcp auth --with-writes` again and tick all boxes.")
    if creds.refresh_token_expires_at:
        _eprint(
            "⚠️  Google says this refresh token EXPIRES on "
            f"{creds.refresh_token_expires_at} — your OAuth consent screen is in 'Testing' status. "
            "Publish it to 'In production' (Google Cloud Console → OAuth consent screen) so you do not have to sign in weekly."
        )
    try:
        GoogleTokenProvider(_store(), app_name=config.APP_NAME).get_access_token_sync(force_refresh=True)
        _eprint("✅ Token refresh works. Add gtm-mcp to your MCP client and ask it to run gtm_list_accounts.")
    except AuthError as exc:
        _eprint(f"⚠️  Saved, but a test refresh failed: {exc}")
        return 1
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    provider = GoogleTokenProvider(_store(), app_name=config.APP_NAME)
    info = provider.status()
    info["config_dir"] = str(config.config_dir())
    info["audit_log"] = str(config.config_dir() / "audit.jsonl")
    info["profile"] = config.profile_for_scopes(info.get("scopes")) if info.get("signed_in") else None
    info["rate_limit_qps"] = config.rate_limit_qps()
    if info.get("signed_in"):
        try:
            provider.get_access_token_sync(force_refresh=True)
            info["token_refresh"] = "ok"
        except AuthError as exc:
            info["token_refresh"] = f"failed: {exc}"
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0 if info.get("signed_in") and info.get("token_refresh", "ok") == "ok" else 1


def cmd_logout(args: argparse.Namespace) -> int:
    provider = GoogleTokenProvider(_store(), app_name=config.APP_NAME)
    result = provider.logout(revoke=not args.keep_google_grant)
    print(json.dumps(result, indent=2))
    return 0


def cmd_serve(_: argparse.Namespace) -> int:
    from .server import run_stdio

    run_stdio()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gtm-mcp", add_help=False)
    sub = parser.add_subparsers(dest="command")
    p_auth = sub.add_parser("auth", add_help=False)
    p_auth.add_argument("--client-id")
    p_auth.add_argument("--client-secret")
    p_auth.add_argument("--with-writes", action="store_true", help="also request edit.containers, edit.containerversions and publish scopes")
    p_auth.add_argument("--login-hint", help="pre-fill the Google account e-mail")
    p_auth.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    sub.add_parser("status", add_help=False)
    p_logout = sub.add_parser("logout", add_help=False)
    p_logout.add_argument("--keep-google-grant", action="store_true", help="delete the local file only; do not revoke at Google")
    sub.add_parser("serve", add_help=False)
    sub.add_parser("help", add_help=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        argv[0] = "help"
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        _eprint(HELP)
        return 2
    command = args.command or "serve"
    if command == "help":
        _eprint(HELP)
        return 0
    handlers = {"auth": cmd_auth, "status": cmd_status, "logout": cmd_logout, "serve": cmd_serve}
    return handlers[command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
