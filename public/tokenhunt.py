#!/usr/bin/env python3
"""tokenhunt: small multi-account client for a Token Hunt server (clean-room).

Written only against the server's public contract (routes/api.php, the
provisioning controller and its form requests) and Anthropic's documented
OAuth endpoints as the server itself uses them. It is not derived from any
other client.

Accounts are kept as "slots" in ~/.config/<namespace>/accounts.json (0600).
Exactly one slot is live: its tokens sit where Claude Code reads its login.

Commands:
  status                   local configuration
  list [--json]            every slot with plan and 5h/7d usage
  current                  the active slot
  add NAME                 save the Claude login that is active right now
  switch TARGET            make a slot the live Claude Code login
  remove TARGET            delete a slot
  alias TARGET [ALIAS]     set or clear a short name
  usage show|refresh       cached / fresh 5h and 7d usage
  sync                     capture live tokens into their slot, fix stale labels
  setup                    install the account an admin provisioned for you

TARGET is a slot's index, name, alias or email.
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

VERSION = "0.2.0"
DEFAULT_NS = "token_hunt"
KEYCHAIN_SERVICE = "Claude Code-credentials"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_BETA = "oauth-2025-04-20"
# Anthropic's edge rejects the stock urllib/curl User-Agent with a bare 429.
ANTHROPIC_UA = "claude-cli/2.1.206 (external, cli)"
DEFAULT_SCOPES = ["user:inference", "user:profile"]
REFRESH_SCOPES = ["user:profile", "user:inference", "user:sessions:claude_code",
                  "user:mcp_servers", "user:file_upload"]
EXPIRY_SKEW_MS = 120_000


def api_base():
    return os.environ.get("TOKENHUNT_ANTHROPIC_API", "https://api.anthropic.com")


def token_url():
    return os.environ.get("TOKENHUNT_TOKEN_URL", "https://platform.claude.com/v1/oauth/token")


class Fail(Exception):
    pass


# --------------------------------------------------------------- paths & io

def home():
    return os.path.expanduser("~")


def ns_dir(ns):
    return os.path.join(home(), ".config", ns)


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def write_private(path, text):
    """Atomic write, mode 0600."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def claude_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home(), ".claude")


def credentials_path():
    return os.path.join(claude_dir(), ".credentials.json")


def claude_json_path():
    # With CLAUDE_CONFIG_DIR set, Claude Code keeps .claude.json inside it.
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        return os.path.join(os.environ["CLAUDE_CONFIG_DIR"], ".claude.json")
    return os.path.join(home(), ".claude.json")


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
    value = hashlib.sha256(("%s|%s|%s" % (seed, os.environ.get("USER", ""), ns)).encode()).hexdigest()
    write_private(path, value)
    return value


# -------------------------------------------------------------------- http

def http(method, url, headers=None, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read() or b"{}"
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read() or b"{}")
        except ValueError:
            payload = {}
        return error.code, payload
    except (urllib.error.URLError, OSError) as error:
        raise Fail("network error: %s" % getattr(error, "reason", error))


def hub_call(base, token, method, path, query=None, body=None):
    url = base + path
    if query:
        url += "?" + "&".join("%s=%s" % (k, urllib.request.quote(str(v))) for k, v in query.items())
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
               "User-Agent": "tokenhunt/" + VERSION}
    return http(method, url, headers, body)


def anthropic_get(path, access_token):
    headers = {"Authorization": "Bearer " + access_token, "anthropic-beta": OAUTH_BETA,
               "User-Agent": ANTHROPIC_UA, "Accept": "application/json"}
    return http("GET", api_base() + path, headers)


def refresh_tokens(refresh_token):
    """Exchange a refresh token. Rotates it: the caller must store the result."""
    status, payload = http("POST", token_url(), {"User-Agent": ANTHROPIC_UA}, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": OAUTH_CLIENT_ID,
        "scope": " ".join(REFRESH_SCOPES),
    })
    if status in (400, 401):
        raise Fail("Anthropic rejected the refresh token (needs a new login)")
    if status == 429:
        raise Fail("rate limited by Anthropic, try again shortly")
    if status != 200 or "access_token" not in payload:
        raise Fail("token refresh failed (HTTP %s)" % status)
    return payload


# ------------------------------------------------------- live Claude login

def read_live():
    """The credentials Claude Code is using now, or None."""
    if platform.system() == "Darwin":
        result = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                                capture_output=True, text=True)
        raw = result.stdout.strip() if result.returncode == 0 else ""
    else:
        raw = read_text(credentials_path())
    if not raw:
        return None
    try:
        oauth = json.loads(raw).get("claudeAiOauth") or {}
    except ValueError:
        return None
    return oauth if oauth.get("accessToken") else None


def write_live(slot):
    """Make this slot's tokens the ones Claude Code uses."""
    payload = json.dumps({"claudeAiOauth": {
        "accessToken": slot["access_token"],
        "refreshToken": slot["refresh_token"],
        "expiresAt": int(slot.get("expires_at") or 0),
        "scopes": slot.get("scopes") or DEFAULT_SCOPES,
    }})
    if platform.system() == "Darwin":
        subprocess.run(["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE,
                        "-a", os.environ.get("USER", ""), "-w", payload], check=True)
        return "macOS Keychain (%s)" % KEYCHAIN_SERVICE
    write_private(credentials_path(), payload)
    return credentials_path()


def set_claude_label(slot):
    """Point ~/.claude.json's oauthAccount at this slot.

    /status shows that cached label, not the token's owner, so it must follow
    the switch. profileFetchedAt is dropped so Claude Code reloads the rest.
    """
    path = claude_json_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return False
    account = data.setdefault("oauthAccount", {})
    account.update({k: v for k, v in {
        "emailAddress": slot.get("email"),
        "accountUuid": slot.get("account_uuid"),
        "organizationUuid": slot.get("org_uuid"),
        "organizationName": slot.get("org_name"),
        "organizationType": slot.get("org_type"),
        "organizationRateLimitTier": slot.get("rate_tier"),
    }.items() if v})
    for stale in ("seatTier", "userRateLimitTier", "billingType", "displayName",
                  "fullName", "profileFetchedAt"):
        account.pop(stale, None)
    write_private(path, json.dumps(data, indent=2) + "\n")
    return True


def live_label_email():
    try:
        with open(claude_json_path(), "r", encoding="utf-8") as handle:
            return (json.load(handle).get("oauthAccount") or {}).get("emailAddress") or ""
    except (OSError, ValueError):
        return ""


# ------------------------------------------------------------- slot store

def store_path(ns):
    return os.path.join(ns_dir(ns), "accounts.json")


def load_store(ns):
    try:
        with open(store_path(ns), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    data.setdefault("active", None)
    data.setdefault("slots", [])
    return data


def save_store(ns, store):
    write_private(store_path(ns), json.dumps(store, indent=2) + "\n")


def find_slot(store, target):
    """Resolve a slot by 1-based index, name, alias or email."""
    slots = store["slots"]
    if target.isdigit() and 1 <= int(target) <= len(slots):
        return slots[int(target) - 1]
    lowered = target.lower()
    for slot in slots:
        if lowered in (slot["name"].lower(), (slot.get("alias") or "").lower(),
                       (slot.get("email") or "").lower()):
            return slot
    raise Fail("no slot matches %r (see: tokenhunt list)" % target)


def same_account(a, b):
    if a.get("account_uuid") and b.get("account_uuid"):
        return a["account_uuid"] == b["account_uuid"]
    return bool(a.get("email")) and a.get("email", "").lower() == (b.get("email") or "").lower()


def unique_name(store, wanted):
    base = re.sub(r"[^A-Za-z0-9._-]", "", wanted) or "account"
    names = {s["name"].lower() for s in store["slots"]}
    name, n = base, 2
    while name.lower() in names:
        name, n = "%s-%d" % (base, n), n + 1
    return name


def slot_from_tokens(access, refresh, expires_ms, scopes, identity):
    return {
        "name": "", "alias": "",
        "email": identity.get("email"), "account_uuid": identity.get("account_uuid"),
        "org_uuid": identity.get("org_uuid"), "org_name": identity.get("org_name"),
        "org_type": identity.get("org_type"), "rate_tier": identity.get("rate_tier"),
        "access_token": access, "refresh_token": refresh,
        "expires_at": int(expires_ms or 0), "scopes": scopes or DEFAULT_SCOPES,
        "usage": None, "added_at": int(time.time()),
    }


def identify(access_token):
    """Who owns this access token, from the profile endpoint."""
    status, profile = anthropic_get("/api/oauth/profile", access_token)
    if status != 200:
        return None
    account, org = profile.get("account") or {}, profile.get("organization") or {}
    return {"email": account.get("email"), "account_uuid": account.get("uuid"),
            "org_uuid": org.get("uuid"), "org_name": org.get("name"),
            "org_type": org.get("organization_type"), "rate_tier": org.get("rate_limit_tier")}


def is_expired(expires_ms):
    return int(expires_ms or 0) <= int(time.time() * 1000) + EXPIRY_SKEW_MS


def capture_live(ns, store, create=True):
    """Store the live login's tokens in their slot (creating one if new).

    Returns the slot, or None when there is no live login or its owner cannot
    be determined. Never discards a login it cannot identify.
    """
    live = read_live()
    if live is None:
        return None
    identity = None
    if not is_expired(live.get("expiresAt")):
        identity = identify(live["accessToken"])
    if identity is None:
        for slot in store["slots"]:
            if slot["refresh_token"] == live.get("refreshToken"):
                identity = {k: slot.get(k) for k in ("email", "account_uuid", "org_uuid",
                                                      "org_name", "org_type", "rate_tier")}
                break
    if identity is None and live.get("refreshToken"):
        # Expired and unknown: refreshing identifies it. Only safe because the
        # result is stored right away, so the rotated token is not lost.
        try:
            fresh = refresh_tokens(live["refreshToken"])
        except Fail:
            return None
        live = {"accessToken": fresh["access_token"], "refreshToken": fresh["refresh_token"],
                "expiresAt": int(time.time() * 1000) + int(fresh.get("expires_in", 28800)) * 1000,
                "scopes": (fresh.get("scope") or "").split() or None}
        write_live({"access_token": live["accessToken"], "refresh_token": live["refreshToken"],
                    "expires_at": live["expiresAt"], "scopes": live["scopes"]})
        identity = identify(live["accessToken"]) or {
            "email": (fresh.get("account") or {}).get("email_address"),
            "account_uuid": (fresh.get("account") or {}).get("uuid"),
            "org_uuid": (fresh.get("organization") or {}).get("uuid"),
            "org_name": (fresh.get("organization") or {}).get("name")}
    if not identity or not (identity.get("email") or identity.get("account_uuid")):
        return None
    for slot in store["slots"]:
        if same_account(slot, identity):
            slot.update(access_token=live["accessToken"], refresh_token=live.get("refreshToken", slot["refresh_token"]),
                        expires_at=int(live.get("expiresAt") or 0))
            for key in ("org_uuid", "org_name", "org_type", "rate_tier", "account_uuid"):
                slot[key] = identity.get(key) or slot.get(key)
            return slot
    if not create:
        return None
    slot = slot_from_tokens(live["accessToken"], live.get("refreshToken"), live.get("expiresAt"),
                            live.get("scopes"), identity)
    slot["name"] = unique_name(store, (identity.get("email") or "account").split("@")[0])
    store["slots"].append(slot)
    return slot


def activate(ns, store, slot):
    """Make `slot` live. The outgoing login must already be captured."""
    where = write_live(slot)
    labelled = set_claude_label(slot)
    store["active"] = slot["name"]
    return where, labelled


# -------------------------------------------------------------- usage data

def parse_usage(payload):
    out = {"fetched_at": int(time.time())}
    for key, bucket in (("five_hour", "5h"), ("seven_day", "7d")):
        part = payload.get(key) or {}
        out["util_" + bucket] = None if part.get("utilization") is None else int(round(part["utilization"]))
        out["reset_" + bucket] = part.get("resets_at")
    return out


def ensure_fresh(slot, is_active):
    """A usable access token for the slot, refreshing inactive slots only.

    The active slot's tokens belong to Claude Code, which refreshes them
    itself; refreshing here would rotate the refresh token underneath it.
    """
    if not is_expired(slot["expires_at"]):
        return True
    if is_active:
        return False
    fresh = refresh_tokens(slot["refresh_token"])
    slot["access_token"] = fresh["access_token"]
    slot["refresh_token"] = fresh.get("refresh_token") or slot["refresh_token"]
    slot["expires_at"] = int(time.time() * 1000) + int(fresh.get("expires_in", 28800)) * 1000
    return True


def refresh_usage(slot, is_active):
    try:
        if not ensure_fresh(slot, is_active):
            return "token expired: open Claude Code once to refresh it"
        status, payload = anthropic_get("/api/oauth/usage", slot["access_token"])
    except Fail as error:
        return str(error)
    if status == 429:
        return "rate limited, try again shortly"
    if status in (401, 403):
        return "token rejected (needs a new login)"
    if status != 200:
        return "usage request failed (HTTP %s)" % status
    slot["usage"] = parse_usage(payload)
    return None


# ----------------------------------------------------------------- display

def fmt_pct(value):
    return "  -" if value is None else "%3d%%" % value


def fmt_reset(iso):
    if not iso:
        return "-"
    try:
        stamp = time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except ValueError:
        return "-"
    left = int(stamp - time.time())
    if left <= 0:
        return "now"
    days, rem = divmod(left, 86400)
    hours, rem = divmod(rem, 3600)
    return "%dd%02dh" % (days, hours) if days else "%dh%02dm" % (hours, rem // 60)


def plan_of(slot):
    tier = (slot.get("rate_tier") or "")
    for marker, label in (("max_20x", "max 20x"), ("max_5x", "max 5x")):
        if marker in tier:
            return label
    return {"claude_pro": "pro", "claude_team": "team", "claude_max": "max",
            "claude_enterprise": "enterprise"}.get(slot.get("org_type") or "", "-")


def slot_state(slot, store):
    if store["active"] == slot["name"]:
        return "ACTIVE"
    return "READY" if not is_expired(slot["expires_at"]) else "STALE"


# ------------------------------------------------------------ command impl

def cmd_status(args):
    directory = ns_dir(args.ns)
    token = read_text(os.path.join(directory, "token"))
    base = find_base_url(args.ns, args.base)
    store = load_store(args.ns)
    print("tokenhunt %s" % VERSION)
    print("namespace      : %s (%s)" % (args.ns, directory))
    print("server         : %s" % (base or "unknown (use --base)"))
    print("hook token     : %s" % ("present" if token else "MISSING"))
    print("hook script    : %s" % ("present" if os.path.exists(os.path.join(directory, "send-hook.sh")) else "missing"))
    creds = credentials_path()
    print("claude creds   : %s (%s)" % (creds, "present" if os.path.exists(creds) else "absent"))
    print("slots          : %d (active: %s)" % (len(store["slots"]), store["active"] or "none"))
    return 0 if token and base else 1


def cmd_list(args):
    store = load_store(args.ns)
    if args.json:
        safe = [{k: v for k, v in dict(s, index=i + 1, state=slot_state(s, store),
                                       active=store["active"] == s["name"]).items()
                 if k not in ("access_token", "refresh_token")}
                for i, s in enumerate(store["slots"])]
        print(json.dumps({"active": store["active"], "slots": safe}, indent=2))
        return 0
    if not store["slots"]:
        print("No slots yet. Log in with Claude Code, then: tokenhunt add NAME")
        return 0
    print(" %-3s %-14s %-30s %-8s %-7s %-5s %-5s %s" % ("#", "NAME", "EMAIL", "PLAN", "STATE", "5H", "7D", "RESET(7d)"))
    for i, slot in enumerate(store["slots"], 1):
        usage = slot.get("usage") or {}
        name = slot["name"] + ("/" + slot["alias"] if slot.get("alias") else "")
        print(" %-3d %-14s %-30s %-8s %-7s %-5s %-5s %s" % (
            i, name[:14], (slot.get("email") or "?")[:30], plan_of(slot), slot_state(slot, store),
            fmt_pct(usage.get("util_5h")), fmt_pct(usage.get("util_7d")), fmt_reset(usage.get("reset_7d"))))
    label = live_label_email()
    active = next((s for s in store["slots"] if s["name"] == store["active"]), None)
    if active and label and label.lower() != (active.get("email") or "").lower():
        print("\nnote: Claude Code's label says %s but the active slot is %s. Run: tokenhunt sync" % (label, active["email"]))
    return 0


def cmd_current(args):
    store = load_store(args.ns)
    slot = next((s for s in store["slots"] if s["name"] == store["active"]), None)
    if not slot:
        print("no active slot")
        return 1
    print("%s  %s  (%s)" % (slot["name"], slot.get("email") or "?", plan_of(slot)))
    return 0


def cmd_add(args):
    store = load_store(args.ns)
    slot = capture_live(args.ns, store, create=True)
    if slot is None:
        raise Fail("no identifiable Claude login to save. Log in with Claude Code first.")
    if args.name:
        if args.name.lower() != slot["name"].lower():
            if any(s["name"].lower() == args.name.lower() for s in store["slots"] if s is not slot):
                raise Fail("a slot named %r already exists" % args.name)
            slot["name"] = args.name
    store["active"] = slot["name"]
    save_store(args.ns, store)
    print("saved %s as slot %r (now active)" % (slot.get("email"), slot["name"]))
    return 0


def cmd_switch(args):
    store = load_store(args.ns)
    target = find_slot(store, args.target)
    outgoing = capture_live(args.ns, store, create=True)
    if outgoing is None and read_live() is not None and not args.force:
        raise Fail("the current Claude login cannot be identified, so switching could lose it. "
                   "Run `tokenhunt add NAME` first, or pass --force.")
    if outgoing is not None:
        store["active"] = outgoing["name"]
    if outgoing is target or (outgoing is not None and same_account(outgoing, target)):
        save_store(args.ns, store)
        print("%s is already active" % target["name"])
        return 0
    where, labelled = activate(args.ns, store, target)
    save_store(args.ns, store)
    print("switched to %s (%s) -> %s" % (target["name"], target.get("email"), where))
    if not labelled:
        print("note: ~/.claude.json was not updated (not found); /status may show the old label")
    print("Restart Claude Code (a running session can overwrite ~/.claude.json).")
    return 0


def cmd_remove(args):
    store = load_store(args.ns)
    target = find_slot(store, args.target)
    if store["active"] == target["name"]:
        raise Fail("%r is the active slot; switch to another one first" % target["name"])
    store["slots"].remove(target)
    save_store(args.ns, store)
    print("removed %s" % target["name"])
    return 0


def cmd_alias(args):
    store = load_store(args.ns)
    target = find_slot(store, args.target)
    alias = args.alias or ""
    if alias and any((s.get("alias") or "").lower() == alias.lower() or s["name"].lower() == alias.lower()
                     for s in store["slots"] if s is not target):
        raise Fail("%r is already used by another slot" % alias)
    target["alias"] = alias
    save_store(args.ns, store)
    print("alias for %s: %s" % (target["name"], alias or "(cleared)"))
    return 0


def cmd_usage(args):
    store = load_store(args.ns)
    if args.action == "show":
        print(json.dumps([{"name": s["name"], "email": s.get("email"), "usage": s.get("usage")}
                          for s in store["slots"]], indent=2))
        return 0
    capture_live(args.ns, store, create=False)
    problems = 0
    for slot in store["slots"]:
        error = refresh_usage(slot, store["active"] == slot["name"])
        if error:
            problems += 1
            print("%-14s %s" % (slot["name"], error), file=sys.stderr)
    save_store(args.ns, store)
    args.json = False
    cmd_list(args)
    return 1 if problems else 0


def cmd_sync(args):
    store = load_store(args.ns)
    slot = capture_live(args.ns, store, create=True)
    if slot is None:
        raise Fail("no identifiable live Claude login")
    changed = []
    if store["active"] != slot["name"]:
        store["active"] = slot["name"]
        changed.append("active slot is now %s" % slot["name"])
    label = live_label_email()
    if label.lower() != (slot.get("email") or "").lower() and not args.dry_run:
        if set_claude_label(slot):
            changed.append("label %s -> %s" % (label or "(none)", slot["email"]))
    elif label.lower() != (slot.get("email") or "").lower():
        changed.append("would change label %s -> %s" % (label or "(none)", slot["email"]))
    if not args.dry_run:
        save_store(args.ns, store)
    print("in sync" if not changed else "\n".join(changed))
    return 0


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


def cmd_setup(args):
    token = read_text(os.path.join(ns_dir(args.ns), "token"))
    if not token:
        raise Fail("No hook token at %s/token. Install the hook first." % ns_dir(args.ns))
    base = find_base_url(args.ns, args.base)
    if not base:
        raise Fail("Cannot determine the server URL. Pass --base https://host")
    device = device_fingerprint(args.ns)

    status, payload = hub_call(base, token, "GET", "/api/provisioned", query={"device_id": device})
    if status == 401:
        raise Fail("The server rejected the hook token. Regenerate it on your profile page.")
    if status != 200:
        raise Fail("Unexpected server response (%s)." % status)

    for item in payload.get("remove") or []:
        print("note: the admin removed you from org %s; nothing was changed locally." % item.get("org_uuid"))
    grant = choose(payload.get("accounts") or [], args.account)
    if grant is None:
        print("Nothing to install: no Claude account is provisioned for this machine yet.")
        print("Ask an admin to add you under Accounts > Members with provisioning on.")
        return 0

    print("Account to install: %s" % grant.get("email"))
    print("Your current Claude login is saved as a slot first, so you can switch back with `tokenhunt switch`.")
    if args.dry_run:
        print("--dry-run: stopping before any change.")
        return 0
    if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
        print("Aborted; nothing changed.")
        return 1

    store = load_store(args.ns)
    saved = capture_live(args.ns, store, create=True)
    if saved is None and read_live() is not None and not args.force:
        raise Fail("The current Claude login cannot be identified, so it could not be saved. "
                   "Run `tokenhunt add NAME` first, or pass --force to replace it anyway.")
    if saved is not None:
        print("saved current login %s as slot %r" % (saved.get("email"), saved["name"]))

    access, refresh = grant["access_token"], grant["refresh_token"]
    expires_ms = int(grant.get("expires_at") or 0) * 1000
    if is_expired(expires_ms):
        fresh = refresh_tokens(refresh)
        access, refresh = fresh["access_token"], fresh.get("refresh_token") or refresh
        expires_ms = int(time.time() * 1000) + int(fresh.get("expires_in", 28800)) * 1000
    identity = identify(access) or {"email": grant.get("email"), "org_uuid": grant.get("org_uuid")}
    new_slot = slot_from_tokens(access, refresh, expires_ms, None, identity)
    existing = next((s for s in store["slots"] if same_account(s, identity)), None)
    if existing is not None:
        existing.update({k: new_slot[k] for k in ("access_token", "refresh_token", "expires_at")})
        new_slot = existing
    else:
        new_slot["name"] = unique_name(store, (identity.get("email") or "account").split("@")[0])
        store["slots"].append(new_slot)

    where, labelled = activate(args.ns, store, new_slot)
    save_store(args.ns, store)
    print("installed %s -> %s" % (new_slot.get("email"), where))
    if not labelled:
        print("note: ~/.claude.json not found; /status may show the old label until Claude Code runs")

    org_uuid = grant.get("org_uuid") or identity.get("org_uuid")
    status, _ = hub_call(base, token, "POST", "/api/provisioned/confirm",
                         body={"set_up": [{"org_uuid": org_uuid}], "device_id": device})
    if status != 200:
        print("Installed, but the server did not confirm (%s). Re-run setup to retry." % status, file=sys.stderr)
        return 1
    print("confirmed with the server. Restart Claude Code to pick up the new login.")
    return 0


# -------------------------------------------------------------------- main

def build_parser():
    parser = argparse.ArgumentParser(prog="tokenhunt", description=__doc__.split("\n")[0])
    parser.add_argument("--ns", default=os.environ.get("TOKENHUNT_NS", DEFAULT_NS))
    parser.add_argument("--base", default="")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    listing = sub.add_parser("list")
    listing.add_argument("--json", action="store_true")
    sub.add_parser("current")
    add = sub.add_parser("add")
    add.add_argument("name", nargs="?", default="")
    switch = sub.add_parser("switch")
    switch.add_argument("target")
    switch.add_argument("--force", action="store_true", help="switch even if the current login cannot be saved")
    remove = sub.add_parser("remove")
    remove.add_argument("target")
    alias = sub.add_parser("alias")
    alias.add_argument("target")
    alias.add_argument("alias", nargs="?", default="")
    usage = sub.add_parser("usage")
    usage.add_argument("action", choices=["show", "refresh"])
    sync = sub.add_parser("sync")
    sync.add_argument("--dry-run", action="store_true")
    setup = sub.add_parser("setup")
    setup.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    setup.add_argument("--dry-run", action="store_true", help="claim and show, change nothing")
    setup.add_argument("--account", default="", help="install only this account email")
    setup.add_argument("--force", action="store_true", help="replace the current login even if it cannot be saved")
    return parser


COMMANDS = {"status": cmd_status, "list": cmd_list, "current": cmd_current, "add": cmd_add,
            "switch": cmd_switch, "remove": cmd_remove, "alias": cmd_alias, "usage": cmd_usage,
            "sync": cmd_sync, "setup": cmd_setup}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except Fail as error:
        print("tokenhunt: %s" % error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
