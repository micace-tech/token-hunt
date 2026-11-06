"""End-to-end tests for public/tokenhunt.py against fake hub + Anthropic servers.

Run: python3 -I -m unittest discover -s tests/tokenhunt
Everything happens in a throwaway HOME; nothing real is touched.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "public", "tokenhunt.py")
FAR = int(time.time() * 1000) + 6 * 3600 * 1000
PAST = int(time.time() * 1000) - 3600 * 1000

IDENT = {
    "AT_A": {"email": "a@x.com", "uuid": "ua", "org": "oa", "tier": "default_claude_max_5x", "type": "claude_team"},
    "AT_B": {"email": "b@x.com", "uuid": "ub", "org": "ob", "tier": "default_claude_ai", "type": "claude_pro"},
    "AT_B2": {"email": "b@x.com", "uuid": "ub", "org": "ob", "tier": "default_claude_ai", "type": "claude_pro"},
    "AT_B3": {"email": "b@x.com", "uuid": "ub", "org": "ob", "tier": "default_claude_ai", "type": "claude_pro"},
}


class Fake(BaseHTTPRequestHandler):
    log = []
    refreshes = 0
    profile_ok = True

    def reply(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def bearer(self):
        return (self.headers.get("Authorization") or "").replace("Bearer ", "")

    def do_GET(self):
        Fake.log.append(("GET", self.path.split("?")[0]))
        token = self.bearer()
        if self.path.startswith("/api/provisioned"):
            if token != "hub-token":
                return self.reply(401, {"error": "unauthenticated"})
            return self.reply(200, {"accounts": [{
                "email": "b@x.com", "org_uuid": "ob", "access_token": "AT_B", "refresh_token": "RT_B",
                "expires_at": int(time.time()) + 7200}], "memberships": [], "remove": []})
        if self.path.startswith("/api/oauth/profile"):
            who = IDENT.get(token)
            if not who or not Fake.profile_ok:
                return self.reply(401, {})
            return self.reply(200, {"account": {"uuid": who["uuid"], "email": who["email"]},
                                    "organization": {"uuid": who["org"], "name": who["email"] + " org",
                                                     "organization_type": who["type"], "rate_limit_tier": who["tier"]}})
        if self.path.startswith("/api/oauth/usage"):
            if token not in IDENT:
                return self.reply(401, {})
            pct = {"AT_A": 40.0}.get(token, 7.0)
            return self.reply(200, {"five_hour": {"utilization": pct, "resets_at": "2099-01-01T00:00:00+00:00"},
                                    "seven_day": {"utilization": pct + 1, "resets_at": "2099-01-02T00:00:00+00:00"}})
        self.reply(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        Fake.log.append(("POST", self.path, body))
        if self.path == "/api/provisioned/confirm":
            return self.reply(200, {"confirmed": 1})
        if self.path == "/token":
            Fake.refreshes += 1
            new = {"RT_B": "RT_B2", "RT_B2": "RT_B3"}.get(body.get("refresh_token"))
            if new is None:
                return self.reply(400, {"error": "invalid_grant"})
            at = "AT_" + new[3:]
            return self.reply(200, {"access_token": at, "refresh_token": new, "expires_in": 28800,
                                    "account": {"email_address": "b@x.com", "uuid": "ub"},
                                    "organization": {"uuid": "ob", "name": "b org"}})
        self.reply(404, {})

    def log_message(self, *args):
        pass


class TokenHuntTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Fake)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Fake.log, Fake.refreshes, Fake.profile_ok = [], 0, True
        self.home = tempfile.mkdtemp(prefix="th-test-")
        ns = os.path.join(self.home, ".config", "token_hunt")
        os.makedirs(ns)
        with open(os.path.join(ns, "token"), "w") as handle:
            handle.write("hub-token")
        os.makedirs(os.path.join(self.home, ".claude"))
        self.live_creds(access="AT_A", refresh="RT_A", expires=FAR)
        self.write_label("a@x.com")
        self.env = dict(os.environ, HOME=self.home, USER="tester",
                        TOKENHUNT_URL="http://127.0.0.1:%d" % self.port,
                        TOKENHUNT_ANTHROPIC_API="http://127.0.0.1:%d" % self.port,
                        TOKENHUNT_TOKEN_URL="http://127.0.0.1:%d/token" % self.port)
        self.env.pop("CLAUDE_CONFIG_DIR", None)

    # helpers ---------------------------------------------------------------
    def run_cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, "-I", SCRIPT, *args], env=self.env,
                              capture_output=True, text=True, input=stdin, timeout=60)

    def live_creds(self, access, refresh, expires):
        path = os.path.join(self.home, ".claude", ".credentials.json")
        with open(path, "w") as handle:
            json.dump({"claudeAiOauth": {"accessToken": access, "refreshToken": refresh,
                                         "expiresAt": expires, "scopes": ["user:inference"]}}, handle)

    def read_live(self):
        with open(os.path.join(self.home, ".claude", ".credentials.json")) as handle:
            return json.load(handle)["claudeAiOauth"]

    def write_label(self, email):
        with open(os.path.join(self.home, ".claude.json"), "w") as handle:
            json.dump({"oauthAccount": {"emailAddress": email, "seatTier": "old", "profileFetchedAt": 1}, "x": 1}, handle)

    def label(self):
        with open(os.path.join(self.home, ".claude.json")) as handle:
            return json.load(handle)

    def store(self):
        with open(os.path.join(self.home, ".config", "token_hunt", "accounts.json")) as handle:
            return json.load(handle)

    def slot(self, email):
        return next(s for s in self.store()["slots"] if s["email"] == email)

    # tests -----------------------------------------------------------------
    def test_add_saves_the_live_login_as_the_active_slot(self):
        result = self.run_cli("add", "base")
        self.assertEqual(result.returncode, 0, result.stderr)
        store = self.store()
        self.assertEqual(store["active"], "base")
        self.assertEqual(store["slots"][0]["email"], "a@x.com")
        self.assertEqual(store["slots"][0]["org_type"], "claude_team")
        mode = os.stat(os.path.join(self.home, ".config", "token_hunt", "accounts.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_list_json_never_exposes_tokens(self):
        self.run_cli("add", "base")
        result = self.run_cli("list", "--json")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("AT_A", result.stdout)
        self.assertNotIn("RT_A", result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["slots"][0]["email"], "a@x.com")
        self.assertTrue(data["slots"][0]["active"])

    def test_setup_keeps_the_old_login_as_a_slot_and_activates_the_grant(self):
        result = self.run_cli("setup", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        emails = sorted(s["email"] for s in self.store()["slots"])
        self.assertEqual(emails, ["a@x.com", "b@x.com"])
        self.assertEqual(self.store()["active"], self.slot("b@x.com")["name"])
        self.assertEqual(self.read_live()["accessToken"], "AT_B")
        self.assertEqual(self.label()["oauthAccount"]["emailAddress"], "b@x.com")
        self.assertNotIn("seatTier", self.label()["oauthAccount"])
        self.assertEqual(self.label()["x"], 1)
        confirm = [e for e in Fake.log if e[0] == "POST" and e[1] == "/api/provisioned/confirm"]
        self.assertEqual(confirm[0][2]["set_up"], [{"org_uuid": "ob"}])

    def test_setup_dry_run_changes_nothing(self):
        result = self.run_cli("setup", "--dry-run")
        self.assertEqual(result.returncode, 0)
        self.assertIn("b@x.com", result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config", "token_hunt", "accounts.json")))
        self.assertEqual(self.read_live()["accessToken"], "AT_A")

    def test_switch_round_trip_captures_tokens_claude_code_rotated(self):
        self.run_cli("setup", "--yes")
        # Claude Code refreshes the live (B) login on its own...
        self.live_creds(access="AT_B2", refresh="RT_B2", expires=FAR)
        a_name = self.slot("a@x.com")["name"]
        result = self.run_cli("switch", a_name)
        self.assertEqual(result.returncode, 0, result.stderr)
        # ...and switching away must keep that rotation, not the stale grant.
        self.assertEqual(self.slot("b@x.com")["refresh_token"], "RT_B2")
        self.assertEqual(self.read_live()["accessToken"], "AT_A")
        self.assertEqual(self.label()["oauthAccount"]["emailAddress"], "a@x.com")
        self.assertEqual(self.store()["active"], a_name)

    def test_switch_by_index_email_and_alias(self):
        self.run_cli("setup", "--yes")
        self.assertEqual(self.run_cli("alias", "a@x.com", "work").returncode, 0)
        for target in ("work", "a@x.com", "1"):
            self.run_cli("switch", self.slot("b@x.com")["name"])
            self.assertEqual(self.run_cli("switch", target).returncode, 0, target)
            self.assertEqual(self.read_live()["accessToken"], "AT_A")

    def test_switch_refuses_when_the_current_login_cannot_be_saved(self):
        self.run_cli("add", "base")
        self.run_cli("setup", "--yes")
        Fake.profile_ok = False
        self.live_creds(access="AT_UNKNOWN", refresh="RT_UNKNOWN", expires=FAR)
        result = self.run_cli("switch", "base")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be identified", result.stderr)
        self.assertEqual(self.read_live()["accessToken"], "AT_UNKNOWN")

    def test_usage_refresh_rotates_inactive_slots_but_never_the_active_one(self):
        self.run_cli("add", "base")                      # A active, token fresh
        self.run_cli("setup", "--yes")                   # B active, A inactive
        store = self.store()
        for slot in store["slots"]:
            if slot["email"] == "a@x.com":
                slot["expires_at"] = PAST                # A is stale -> must be refreshed? RT_A is unknown to fake
        with open(os.path.join(self.home, ".config", "token_hunt", "accounts.json"), "w") as handle:
            json.dump(store, handle)
        result = self.run_cli("usage", "refresh")
        self.assertEqual(Fake.refreshes, 1)              # only the inactive stale slot was refreshed
        self.assertIn("rejected the refresh token", result.stderr)   # the fake rejects RT_A
        self.assertEqual(self.slot("b@x.com")["usage"]["util_5h"], 7)
        self.assertEqual(self.read_live()["accessToken"], "AT_B")    # live login untouched

    def test_usage_refresh_fills_the_list(self):
        self.run_cli("add", "base")
        result = self.run_cli("usage", "refresh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(" 40%", result.stdout)
        self.assertEqual(self.slot("a@x.com")["usage"]["util_7d"], 41)

    def test_remove_refuses_the_active_slot(self):
        self.run_cli("setup", "--yes")
        active = self.store()["active"]
        refused = self.run_cli("remove", active)
        self.assertNotEqual(refused.returncode, 0)
        other = self.slot("a@x.com")["name"]
        self.assertEqual(self.run_cli("remove", other).returncode, 0)
        self.assertEqual(len(self.store()["slots"]), 1)

    def test_sync_repairs_a_stale_label(self):
        self.run_cli("add", "base")
        self.write_label("someone-else@x.com")
        result = self.run_cli("sync")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.label()["oauthAccount"]["emailAddress"], "a@x.com")

    def test_current_and_unknown_target(self):
        self.run_cli("add", "base")
        self.assertIn("a@x.com", self.run_cli("current").stdout)
        self.assertNotEqual(self.run_cli("switch", "nope").returncode, 0)

    def test_bad_hub_token_is_reported(self):
        with open(os.path.join(self.home, ".config", "token_hunt", "token"), "w") as handle:
            handle.write("wrong")
        result = self.run_cli("setup", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rejected the hook token", result.stderr)


if __name__ == "__main__":
    unittest.main()
