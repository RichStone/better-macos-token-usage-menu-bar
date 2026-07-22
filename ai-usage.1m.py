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

import calendar
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import date, datetime

CACHE_FILE = os.path.expanduser("~/.cache/ai-usage-bar/state.json")
STALE_AFTER = 30 * 60  # mark cached data stale after 30 min
CLAUDE_POLL = 5 * 60  # Anthropic's usage endpoint 429s under 1-minute polling; ask it less often

# Monthly billing-cycle renewal day (1-31) per provider, shown in the dropdown as
# the next occurrence (neither usage API reports it). Configure without editing this
# file — a re-download would overwrite it — by creating the JSON config below with
# {"claude_renewal_day": 1, "codex_renewal_day": 10}. The constants are the fallback
# when a key is absent; None hides the row. Days past a month's length clamp to its
# last day (e.g. 31 -> Feb 28).
CONFIG_FILE = os.path.expanduser("~/.config/ai-usage-bar/config.json")
CLAUDE_RENEWAL_DAY = None
CODEX_RENEWAL_DAY = None


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
    os.chmod(tmp, 0o600)  # cached responses hold account metadata (email, plan, ids)
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
            delay = int(retry) if retry.isdigit() else 300
            # Anthropic has been seen sending Retry-After: ~14 hours. Honoring that
            # literally starves the widget all night; a 15-min cap is polite enough.
            state["claude_backoff_until"] = time.time() + min(delay, 900)
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
    return "–" if v is None else str(round(v))


def age_text(seconds):
    return f"{seconds / 3600:.1f}h" if seconds >= 3600 else f"{max(seconds, 60) / 60:.0f}m"


def read_config():
    """User settings from CONFIG_FILE (renewal days etc.); {} if absent/unreadable."""
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def valid_day(val):
    """Coerce a config/constant value to a 1-31 day-of-month, or None if unusable."""
    try:
        d = int(val)
    except (TypeError, ValueError):
        return None
    return d if 1 <= d <= 31 else None


def next_renewal(day):
    """Next date on/after today falling on `day` of the month (clamped to the
    month's length), formatted 'Mon D'. None when the day isn't configured."""
    if not day:
        return None
    today = date.today()
    def on(y, m):
        return date(y, m, min(day, calendar.monthrange(y, m)[1]))
    d = on(today.year, today.month)
    if d < today:
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        d = on(y, m)
    return d.strftime("%b %-d")


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
    """Remaining % for a codex window; None when the API doesn't report that window
    (some plans only get a weekly limit — no session window exists to report)."""
    return left(window.get("used_percent")) if window else None


# Everything is "% remaining". These thresholds and the palette are shared by the
# dropdown row text and the menu-bar status dots.
LOW, MID = 20, 60          # remaining < LOW = red, < MID = orange, >= MID = default
COLOR_RED = "#e06c75"      # calm reddish
COLOR_ORANGE = "#d9902b"   # orangeish


def color_for(remaining):
    if remaining is None:
        return None
    if remaining < LOW:
        return COLOR_RED
    if remaining < MID:
        return COLOR_ORANGE
    return None


def dot_for(remaining):
    """Colored status light for a reported limit. Emoji keep their own color
    regardless of the title's single text color, so every meter signals
    independently: 🔴 critical / 🟠 low / 🎾 healthy."""
    if remaining < LOW:
        return "🔴"
    if remaining < MID:
        return "🟠"
    return "🎾"


def cell(remaining, unknown):
    """(value, dot) for one limit in the menu bar title.
    - unknown (provider data stale or never fetched): a plain '–', never a
      healthy-looking ball — an unavailable meter must not read as full.
    - reported-absent (remaining is None but the provider answered): no ceiling
      on this meter, so 🎾 stands in for the number and carries the status itself.
    - a real value: the number plus its colored dot."""
    if unknown:
        return "–", ""
    if remaining is None:
        return "🎾", ""
    return str(round(remaining)), dot_for(remaining)


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

    cfg = read_config()
    claude_day = valid_day(cfg.get("claude_renewal_day", CLAUDE_RENEWAL_DAY))
    codex_day = valid_day(cfg.get("codex_renewal_day", CODEX_RENEWAL_DAY))

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

    # One status dot per limit, bookending each provider's two numbers: session's
    # dot on the left, weekly's on the right (│ is U+2502, not a literal pipe —
    # SwiftBar treats "|" as its parameter separator). SwiftBar allows only one
    # text color on the title, but emoji keep their own, so every meter signals
    # independently: 🎾 healthy / 🟠 low / 🔴 critical, a bare 🎾 for a meter with
    # no ceiling, and "–" when a provider's data is momentarily unavailable.
    cc_unknown = not claude or claude_stale
    cx_unknown = not codex or codex_stale
    cc_sv, cc_sd = cell(cc_s, cc_unknown)
    cc_wv, cc_wd = cell(cc_w, cc_unknown)
    cx_sv, cx_sd = cell(cx_s, cx_unknown)
    cx_wv, cx_wd = cell(cx_w, cx_unknown)
    title = (f"{cc_sd}CC{cc_sv}│{cc_wv}{cc_wd}"
             f" {cx_sd}Cx{cx_sv}│{cx_wv}{cx_wd}")
    print(f"{title} | font=Menlo size=12")
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
    renewal = next_renewal(claude_day)
    if renewal:
        print(line(f"{'Renews':<8} {renewal}  ·  monthly plan", mono=True))
    if claude_err:
        print(line(f"⚠ {claude_err}", color="#febc2e"))
        if claude:
            print(line(f"showing data from {age_text(now - state.get('claude', {}).get('ts', now))} ago", color="gray"))
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
                print(line(f"{label:<8} –  ·  not reported by the API", mono=True))
        for extra_lim in codex.get("additional_rate_limits") or []:
            rl = extra_lim.get("rate_limit") or {}
            es, ew = codex_windows(rl)
            s, w = win_left(es), win_left(ew)
            s_txt = "–" if s is None else f"{pct(s)}%"
            w_txt = "–" if w is None else f"{pct(w)}%"
            print(line(f"{extra_lim.get('limit_name', 'other')}: session {s_txt} · weekly {w_txt} left",
                       color=color_for(min((v for v in (s, w) if v is not None), default=None)), mono=True))
        credits = codex.get("credits") or {}
        if credits.get("has_credits"):
            print(line(f"Credits  {credits.get('balance')}", mono=True))
        resets = (codex.get("rate_limit_reset_credits") or {}).get("available_count")
        if resets:
            print(line(f"Reset credits available: {resets}", mono=True))
    renewal = next_renewal(codex_day)
    if renewal:
        print(line(f"{'Renews':<8} {renewal}  ·  monthly plan", mono=True))
    if codex_err:
        print(line(f"⚠ {codex_err}", color="#febc2e"))
        if codex:
            print(line(f"showing data from {age_text(now - state.get('codex', {}).get('ts', now))} ago", color="gray"))
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
        print("CC?│? Cx?│? | font=Menlo size=12")
        print("---")
        print("plugin crashed | color=#ff5f57")
        for tb_line in traceback.format_exc().strip().splitlines():
            print(line(tb_line, color="gray", mono=True))
        print("---")
        print(line("retry now · refresh", refresh=True))
