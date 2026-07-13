#!/usr/bin/env python3
# <xbar.title>Better MacOS Token Usage Menu Bar</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.author>Rich Steinmetz</xbar.author>
# <xbar.author.github>RichStone</xbar.author.github>
# <xbar.desc>Claude Code, Codex and GitHub Copilot usage limits (% remaining) from the official usage APIs. No credential prompts: Codex/Copilot auth comes from plain config files, Claude's from the Keychain via /usr/bin/security (one-time Always Allow).</xbar.desc>
# <xbar.abouturl>https://github.com/RichStone/better-macos-token-usage-menu-bar</xbar.abouturl>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime

CACHE_FILE = os.path.expanduser("~/.cache/ai-usage-bar/state.json")
STALE_AFTER = 30 * 60  # mark cached data stale after 30 min
CLAUDE_POLL = 5 * 60  # Anthropic's usage endpoint 429s under 1-minute polling; ask it less often


def load_state():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, CACHE_FILE)


def http_json(url, headers):
    req = urllib.request.Request(url, headers={"User-Agent": "ai-usage-bar", **headers})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def fetch_claude(state):
    """Returns (data, error). Token read via Apple's security tool — silent after one Always Allow."""
    if time.time() - state.get("claude", {}).get("ts", 0) < CLAUDE_POLL:
        return None, None  # cache is recent enough; main() falls back to it silently
    if state.get("claude_backoff_until", 0) > time.time():
        return None, "rate-limited, backing off"
    try:
        out = subprocess.run(
            ["/usr/bin/security", "find-generic-password",
             "-s", "Claude Code-credentials", "-a", os.environ.get("USER", ""), "-w"],
            capture_output=True, text=True, timeout=25)
        if out.returncode != 0:
            return None, "keychain read denied"
        token = json.loads(out.stdout.strip())["claudeAiOauth"]["accessToken"]
        return http_json("https://api.anthropic.com/api/oauth/usage",
                         {"Authorization": f"Bearer {token}",
                          "anthropic-beta": "oauth-2025-04-20"}), None
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry = e.headers.get("Retry-After", "")
            state["claude_backoff_until"] = time.time() + (int(retry) if retry.isdigit() else 300)
            return None, "rate-limited by Anthropic"
        if e.code == 401:
            return None, "token expired — run Claude Code once to refresh"
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)[:100]


def fetch_codex(state):
    if state.get("codex_backoff_until", 0) > time.time():
        return None, "rate-limited, backing off"
    try:
        with open(os.path.expanduser("~/.codex/auth.json")) as f:
            tokens = json.load(f)["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        if tokens.get("account_id"):
            headers["ChatGPT-Account-Id"] = tokens["account_id"]
        return http_json("https://chatgpt.com/backend-api/wham/usage", headers), None
    except urllib.error.HTTPError as e:
        if e.code == 429:
            state["codex_backoff_until"] = time.time() + 300
            return None, "rate-limited by OpenAI"
        if e.code == 401:
            return None, "token expired — run codex once to refresh"
        return None, f"HTTP {e.code}"
    except FileNotFoundError:
        return None, "~/.codex/auth.json not found"
    except Exception as e:
        return None, str(e)[:100]


def fetch_copilot(state):
    """Dropdown-only provider. Tries every oauth_token in Copilot's apps.json (editor logins
    accumulate there and stale ones 401), remembering which one worked."""
    if state.get("copilot_backoff_until", 0) > time.time():
        return None, "rate-limited, backing off"
    try:
        with open(os.path.expanduser("~/.config/github-copilot/apps.json")) as f:
            apps = json.load(f)
    except FileNotFoundError:
        return None, "no Copilot login found"
    except Exception as e:
        return None, str(e)[:100]
    tokens = [(k, v["oauth_token"]) for k, v in apps.items()
              if isinstance(v, dict) and v.get("oauth_token")]
    tokens.sort(key=lambda kv: kv[0] != state.get("copilot_token_key"))
    err = "no oauth tokens in apps.json"
    for key, tok in tokens:
        try:
            data = http_json("https://api.github.com/copilot_internal/user",
                             {"Authorization": f"token {tok}", "Accept": "application/json"})
            state["copilot_token_key"] = key
            return data, None
        except urllib.error.HTTPError as e:
            if e.code == 401:
                err = "tokens rejected — sign in to Copilot in an editor"
                continue
            if e.code == 429:
                state["copilot_backoff_until"] = time.time() + 300
                return None, "rate-limited by GitHub"
            return None, f"HTTP {e.code}"
        except Exception as e:
            return None, str(e)[:100]
    return None, err


def pct(v):
    return "?" if v is None else str(round(v))


def left(used):
    """Convert a used-% into a remaining-%. Everything displayed is 'how much is left'."""
    return None if used is None else max(0.0, 100.0 - used)


def codex_windows(rate_limit):
    """Returns (session, weekly) windows identified by duration, either may be None.
    The API nulls out idle windows and promotes whatever remains to primary_window,
    so primary/secondary position says nothing about which window it is."""
    session = weekly = None
    for w in (rate_limit.get("primary_window"), rate_limit.get("secondary_window")):
        if not isinstance(w, dict):
            continue
        if (w.get("limit_window_seconds") or 0) >= 100_000:
            weekly = w
        else:
            session = w
    return session, weekly


def win_left(window):
    """Remaining % for a codex window; a missing window means untouched, i.e. all left."""
    return left(window.get("used_percent")) if window else 100.0


def color_for(remaining):
    if remaining is None:
        return None
    if remaining <= 10:
        return "#ff5f57"
    if remaining <= 30:
        return "#febc2e"
    return None


def fmt_reset(value):
    """Accepts ISO string or epoch seconds, returns local 'HH:MM' today or 'Wed 14:00'."""
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value).astimezone()
        else:
            dt = datetime.fromisoformat(value).astimezone()
        if dt.date() == datetime.now().astimezone().date():
            return dt.strftime("%H:%M")
        if (dt.hour, dt.minute) == (0, 0):  # date-only values like Copilot's quota_reset_date
            return dt.strftime("%b %-d")
        return dt.strftime("%a %H:%M")
    except Exception:
        return "?"


def line(text, **params):
    # AppKit dims action-less (disabled) menu rows even when they have an explicit
    # color, so every row gets an action: real ones keep theirs, the rest get a
    # no-op click target to stay enabled and solid. Color pair = light,dark menus.
    parts = [p for p in [
        f"color={params.get('color') or '#000000,#ffffff'}",
        "font=Menlo size=12" if params.get("mono") else None,
        f"href={params['href']}" if params.get("href") else None,
        "refresh=true" if params.get("refresh") else None,
    ] if p]
    if not (params.get("href") or params.get("refresh")):
        parts.append("bash=/usr/bin/true terminal=false")
    return f"{text} | {' '.join(parts)}"


def main():
    state = load_state()
    now = time.time()

    claude, claude_err = fetch_claude(state)
    codex, codex_err = fetch_codex(state)
    copilot, copilot_err = fetch_copilot(state)

    for key, data in (("claude", claude), ("codex", codex), ("copilot", copilot)):
        if data is not None:
            state[key] = {"data": data, "ts": now}
    save_state(state)

    claude = claude or state.get("claude", {}).get("data")
    codex = codex or state.get("codex", {}).get("data")
    copilot = copilot or state.get("copilot", {}).get("data")
    claude_stale = claude_err and (now - state.get("claude", {}).get("ts", 0)) > STALE_AFTER
    codex_stale = codex_err and (now - state.get("codex", {}).get("ts", 0)) > STALE_AFTER

    cc_s = left((claude.get("five_hour") or {}).get("utilization")) if claude else None
    cc_w = left((claude.get("seven_day") or {}).get("utilization")) if claude else None
    cx = (codex or {}).get("rate_limit") or {}
    cx_session, cx_weekly = codex_windows(cx)
    cx_s = win_left(cx_session) if codex else None
    cx_w = win_left(cx_weekly) if codex else None

    if claude_stale:
        cc_s = cc_w = None
    if codex_stale:
        cx_s = cx_w = None

    # U+2502 stands in for "|" — SwiftBar treats a literal pipe as its parameter separator
    title = f"CC{pct(cc_s)}│{pct(cc_w)}-Cx{pct(cx_s)}│{pct(cx_w)}"
    lowest = min((v for v in (cc_s, cc_w, cx_s, cx_w) if v is not None), default=None)
    # The menu bar title must NOT get the default dropdown color: the bar decides
    # its own text color based on the wallpaper behind it.
    tcolor = color_for(lowest)
    print(f"{title} | font=Menlo size=12" + (f" color={tcolor}" if tcolor else ""))
    print("---")

    # --- Claude Code section ---
    print("Claude Code | size=13 color=#000000,#ffffff bash=/usr/bin/true terminal=false")
    if claude:
        fh, sd = claude.get("five_hour") or {}, claude.get("seven_day") or {}
        fh_left, sd_left = left(fh.get("utilization")), left(sd.get("utilization"))
        print(line(f"Session  {pct(fh_left)}% left  ·  resets {fmt_reset(fh.get('resets_at'))}",
                   color=color_for(fh_left), mono=True))
        print(line(f"Weekly   {pct(sd_left)}% left  ·  resets {fmt_reset(sd.get('resets_at'))}",
                   color=color_for(sd_left), mono=True))
        for lim in claude.get("limits") or []:
            if lim.get("kind") == "weekly_scoped":
                name = ((lim.get("scope") or {}).get("model") or {}).get("display_name") or "scoped"
                lim_left = left(lim.get("percent"))
                print(line(f"Weekly   {pct(lim_left)}% left  ·  {name} only",
                           color=color_for(lim_left), mono=True))
        extra = claude.get("extra_usage") or {}
        if extra.get("is_enabled"):
            dp = extra.get("decimal_places", 2)
            used = (extra.get("used_credits") or 0) / (10 ** dp)
            limit = (extra.get("monthly_limit") or 0) / (10 ** dp)
            u_left = left(extra.get("utilization"))
            remaining = max(0.0, limit - used)
            print(line(f"Extra    ${remaining:,.2f} of ${limit:,.2f} left  ({pct(u_left)}%)",
                       color=color_for(u_left), mono=True))
    if claude_err:
        print(line(f"⚠ {claude_err}", color="#febc2e"))
        if claude and not claude_stale:
            print(line(f"showing cached from {fmt_reset(state.get('claude', {}).get('ts'))}", color="gray"))
    print("---")

    # --- Codex section ---
    plan = f" ({codex.get('plan_type')})" if codex and codex.get("plan_type") else ""
    print(f"Codex{plan} | size=13 color=#000000,#ffffff bash=/usr/bin/true terminal=false")
    if codex:
        for label, w in (("Session", cx_session), ("Weekly", cx_weekly)):
            if w:
                wl = left(w.get("used_percent"))
                print(line(f"{label:<8} {pct(wl)}% left  ·  resets {fmt_reset(w.get('reset_at'))}",
                           color=color_for(wl), mono=True))
            else:
                print(line(f"{label:<8} 100% left  ·  untouched", mono=True))
        for extra_lim in codex.get("additional_rate_limits") or []:
            rl = extra_lim.get("rate_limit") or {}
            es, ew = codex_windows(rl)
            s, w = win_left(es), win_left(ew)
            print(line(f"{extra_lim.get('limit_name', 'other')}: session {pct(s)}% · weekly {pct(w)}% left",
                       color=color_for(min(s, w)), mono=True))
        credits = codex.get("credits") or {}
        if credits.get("has_credits"):
            print(line(f"Credits  {credits.get('balance')}", mono=True))
        resets = (codex.get("rate_limit_reset_credits") or {}).get("available_count")
        if resets:
            print(line(f"Reset credits available: {resets}", mono=True))
    if codex_err:
        print(line(f"⚠ {codex_err}", color="#febc2e"))
        if codex and not codex_stale:
            print(line(f"showing cached from {fmt_reset(state.get('codex', {}).get('ts'))}", color="gray"))
    print("---")

    # --- Copilot section (dropdown only, deliberately not in the menu bar title) ---
    cp_plan = f" ({copilot.get('copilot_plan')})" if copilot and copilot.get("copilot_plan") else ""
    print(f"Copilot{cp_plan} | size=13 color=#000000,#ffffff bash=/usr/bin/true terminal=false")
    if copilot:
        names = {"premium_interactions": "Premium", "chat": "Chat", "completions": "Complete"}
        unlimited = []
        for qid, snap in (copilot.get("quota_snapshots") or {}).items():
            if not isinstance(snap, dict):
                continue
            label = names.get(qid, qid)
            if snap.get("unlimited"):
                unlimited.append(label)
                continue
            rem = snap.get("percent_remaining")
            print(line(f"{label:<8} {pct(rem)}% left  ·  {snap.get('remaining')} of {snap.get('entitlement')}"
                       f"  ·  resets {fmt_reset(copilot.get('quota_reset_date'))}",
                       color=color_for(rem), mono=True))
        if unlimited:
            print(line(f"{' & '.join(unlimited)}: unlimited", color="gray", mono=True))
    if copilot_err:
        print(line(f"⚠ {copilot_err}", color="#febc2e"))
    print("---")

    print(line(f"Updated {datetime.now().strftime('%H:%M:%S')} · refresh", color="gray", refresh=True))
    print(line("claude.ai usage settings", href="https://claude.ai/settings/usage", color="gray"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never exit non-zero: SwiftBar would replace the whole widget with a "?" icon.
        # Keep a placeholder title and put the traceback in the dropdown instead.
        import traceback
        print("CC?│?-Cx?│? | font=Menlo size=12")
        print("---")
        print("plugin crashed | color=#ff5f57")
        for tb_line in traceback.format_exc().strip().splitlines():
            print(line(tb_line, color="gray", mono=True))
        print("---")
        print(line("retry now · refresh", refresh=True))
