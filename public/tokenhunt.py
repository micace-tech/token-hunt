#!/usr/bin/env python3
"""tokenhunt: minimal client for a Token Hunt server (clean-room).

Written only against the server's public contract (routes/api.php,
ProvisionedAccountController and its form requests), not against any
other client.

Commands:
  status   show local configuration (no network call that changes state)
  setup    claim the Claude grant an admin provisioned for this machine,
           write it where Claude Code reads credentials, then confirm.

Stdlib only; Python 3.8+.
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_NS = "token_hunt"
USER_AGENT = "tokenhunt/0.1"
KEYCHAIN_SERVICE = "Claude Code-credentials"
# Claude Code refreshes the token itself and rewrites its own scopes; these
# only need to let it start.
DEFAULT_SCOPES = ["user:inference", "user:profile"]


def ns_dir(ns):
    return os.path.join(os.path.expanduser("~"), ".config", ns)


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def find_base_url(ns, override):
    """Server origin: --base, $TOKENHUNT_URL, else the hook's baked API_URL."""
    url = override or os.environ.get("TOKENHUNT_URL") or ""
    if not url:
        hook = read_text(os.path.join(ns_dir(ns), "send-hook.sh"))
        match = re.search(r"^API_URL='([^']+)'", hook, re.M)
        url = match.group(1) if match else ""
    match = re.match(r"^(https?://[^/]+)", url)
    return match.group(1) if match else ""


def device_fingerprint(ns):
    """Stable per-machine id, generated once and kept next to the token."""
    path = os.path.join(ns_dir(ns), "device-id")
    existing = read_text(path)
    if existing:
        return existing
    seed = read_text("/etc/machine-id") or platform.node()
    seed = "%s|%s|%s" % (seed, os.environ.get("USER", ""), ns)
    value = hashlib.sha256(seed.encode()).hexdigest()
    os.makedirs(ns_dir(ns), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(value)
    os.chmod(path, 0o600)
    return value


def call(base, token, method, path, query=None, body=None):
    url = base + path
    if query:
        url += "?" + "&".join("%s=%s" % (k, urllib.request.quote(str(v))) for k, v in query.items())
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read() or b"{}")
        except ValueError:
            payload = {}
        return error.code, payload


def credentials_path():
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(config_dir, ".credentials.json")


def build_credentials(grant):
    expires = grant.get("expires_at")
    return {
        "claudeAiOauth": {
            "accessToken": grant["access_token"],
            "refreshToken": grant["refresh_token"],
            "expiresAt": int(expires) * 1000 if expires else 0,
            "scopes": DEFAULT_SCOPES,
        }
    }


def write_credentials(grant):
    """Write where Claude Code reads them; back up whatever was there."""
    payload = json.dumps(build_credentials(grant))
    if platform.system() == "Darwin":
        subprocess.run(
            ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE,
             "-a", os.environ.get("USER", ""), "-w", payload],
            check=True,
        )
        return "macOS Keychain (%s)" % KEYCHAIN_SERVICE
    path = credentials_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        backup = "%s.bak-%d" % (path, int(time.time()))
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
        print("backed up existing credentials -> " + backup)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(payload)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def choose(grants, wanted):
    usable = [g for g in grants if g.get("provider", "claude") == "claude" and g.get("access_token")]
    if wanted:
        usable = [g for g in usable if g.get("email", "").lower() == wanted.lower()]
    if len(usable) <= 1:
        return usable[0] if usable else None
    print("More than one account was provisioned for this machine:")
    for index, grant in enumerate(usable, 1):
        print("  %d) %s" % (index, grant.get("email")))
    answer = input("Install which one? [1-%d] " % len(usable)).strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(usable):
        return None
    return usable[int(answer) - 1]


def cmd_status(args):
    directory = ns_dir(args.ns)
    token = read_text(os.path.join(directory, "token"))
    base = find_base_url(args.ns, args.base)
    print("namespace      : %s (%s)" % (args.ns, directory))
    print("server         : %s" % (base or "unknown (use --base)"))
    print("hook token     : %s" % ("present" if token else "MISSING"))
    print("hook script    : %s" % ("present" if os.path.exists(os.path.join(directory, "send-hook.sh")) else "missing"))
    creds = credentials_path()
    print("claude creds   : %s (%s)" % (creds, "present" if os.path.exists(creds) else "absent"))
    return 0 if token and base else 1


def cmd_setup(args):
    token = read_text(os.path.join(ns_dir(args.ns), "token"))
    if not token:
        print("No hook token at %s/token. Install the hook first." % ns_dir(args.ns), file=sys.stderr)
        return 1
    base = find_base_url(args.ns, args.base)
    if not base:
        print("Cannot determine the server URL. Pass --base https://host", file=sys.stderr)
        return 1
    device = device_fingerprint(args.ns)

    status, payload = call(base, token, "GET", "/api/provisioned", query={"device_id": device})
    if status == 401:
        print("The server rejected the hook token. Regenerate it on your profile page.", file=sys.stderr)
        return 1
    if status != 200:
        print("Unexpected server response (%s)." % status, file=sys.stderr)
        return 1

    grants = payload.get("accounts") or []
    for item in payload.get("remove") or []:
        print("note: the admin removed you from org %s; nothing was changed locally." % item.get("org_uuid"))
    grant = choose(grants, args.account)
    if grant is None:
        print("Nothing to install: no Claude account is provisioned for this machine yet.")
        print("Ask an admin to add you under Accounts > Members with provisioning on.")
        return 0

    print("Account to install: %s" % grant.get("email"))
    print("This replaces the Claude Code login on this machine (a backup is kept).")
    if args.dry_run:
        print("--dry-run: stopping before any change.")
        return 0
    if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
        print("Aborted; nothing changed.")
        return 1

    location = write_credentials(grant)
    print("wrote credentials -> " + location)

    status, _ = call(base, token, "POST", "/api/provisioned/confirm",
                     body={"set_up": [{"org_uuid": grant["org_uuid"]}], "device_id": device})
    if status != 200:
        print("Installed, but the server did not confirm (%s). Re-run setup to retry." % status, file=sys.stderr)
        return 1
    print("confirmed with the server. Restart Claude Code to pick up the new login.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tokenhunt", description=__doc__.split("\n")[0])
    parser.add_argument("--ns", default=os.environ.get("TOKENHUNT_NS", DEFAULT_NS))
    parser.add_argument("--base", default="")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    setup = sub.add_parser("setup")
    setup.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    setup.add_argument("--dry-run", action="store_true", help="claim and show, change nothing")
    setup.add_argument("--account", default="", help="install only this account email")
    args = parser.parse_args(argv)
    return {"status": cmd_status, "setup": cmd_setup}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
