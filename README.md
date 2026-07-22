# Better MacOS Token Usage Menu Bar

All your AI coding limits in the macOS menu bar — **Claude Code, OpenAI Codex, and GitHub Copilot** — with zero credential prompts, ever.

```
CC49│88 Cx22🟠│78    ← menu bar: Claude session│weekly, Codex session│weekly
```

All numbers are **% remaining** (how much you have left, not how much you used). Each provider carries its own status dot so one running low never recolors the other: 🟠 below 60% left, 🔴 below 20%, nothing when healthy. (SwiftBar allows only one text color on the menu bar title, so per-provider signaling uses dots; the dropdown rows below tint the actual numbers with the same thresholds.) Click it for details:

```
Claude Code
Session  49% left  ·  resets 15:20
Weekly   88% left  ·  resets Sun 03:00
Weekly   58% left  ·  Fable only
Extra    $12.34 of $300.00 left  (4%)

Codex (pro)
Session  78% left  ·  resets 19:03
Weekly   23% left  ·  resets Sat 10:20

Copilot (individual)
Premium  92% left  ·  1382 of 1500  ·  resets Aug 1
```

## Why

I built this after using [CodexBar](https://github.com/steipete/CodexBar), which is impressive in scope but didn't fit how I want to glance at limits:

- **It felt convoluted.** I want one fixed-width string and one dropdown, not a multi-provider app with modes and settings.
- **The menu bar only shows the active model.** I want *all* my session and weekly meters visible at once, side by side.
- **Inconsistent meter direction.** Limits count down toward 0 while extra usage counts up from $0. Here **everything reads as "how much is left"** — percentages and extra-usage dollars alike.
- **Worst of all: the recurring Keychain prompts.** Every CodexBar update re-signs the app, which invalidates its "Always Allow" Keychain approval, so the credential prompt keeps coming back. This plugin reads the Keychain through Apple's own `/usr/bin/security` — a binary that never changes — so you approve **once** and never see the prompt again. Codex and Copilot tokens live in plain config files; no Keychain involved at all. (This convenience has a real trade-off — see [Security](#security).)

## Install

```sh
brew install --cask swiftbar
mkdir -p ~/.swiftbar
curl -o ~/.swiftbar/ai-usage.1m.py https://raw.githubusercontent.com/RichStone/better-macos-token-usage-menu-bar/main/ai-usage.1m.py
chmod +x ~/.swiftbar/ai-usage.1m.py
defaults write com.ameba.SwiftBar PluginDirectory "$HOME/.swiftbar"
open -a SwiftBar
```

On the first refresh macOS asks for Keychain access — click **Always Allow**. That's the last prompt you'll ever see (understand what you're approving: [Security](#security)).

You're curl-ing a script that will run every minute — it's ~300 lines of dependency-free Python, so read it first.

Requirements: macOS with python3 (ships with the Xcode Command Line Tools) and whichever CLIs you use logged in — `claude`, `codex`, and/or Copilot in any editor. Providers you don't use just show a warning row; everything else keeps working.

## How it works

Every minute SwiftBar runs the script, which calls the same usage APIs the official `/usage` screens use:

| Provider | Endpoint | Auth source |
|---|---|---|
| Claude Code | `api.anthropic.com/api/oauth/usage` | Keychain item `Claude Code-credentials` via `/usr/bin/security` |
| Codex | `chatgpt.com/backend-api/wham/usage` | `~/.codex/auth.json` |
| Copilot | `api.github.com/copilot_internal/user` | `~/.config/github-copilot/apps.json` (tries all entries, remembers the working one) |

Details worth knowing:

- **Claude is polled at most every 5 minutes** (the endpoint rate-limits under minutely polling); Codex and Copilot every minute. On HTTP 429 the script backs off and serves cached numbers, marked "showing cached from …".
- **Codex windows are identified by duration** (5h vs 7d), not by their position in the response — the API nulls out unreported windows and promotes whatever remains, so position lies. A missing window means the API isn't reporting that limit at all (some plans currently get only a weekly window) and renders as "–  ·  not reported by the API".
- **Tokens are never refreshed by the script** (that would invalidate your CLI's session). If a token expires, the dropdown says so — running the CLI once fixes it.
- **Nothing leaves your machine** except the HTTPS calls to the three providers. Last-known-good data is cached in `~/.cache/ai-usage-bar/state.json`.
- If the script ever crashes, the menu bar shows `CC?│?-Cx?│?` and the dropdown shows the traceback with a retry item.

## Caveats

- **The APIs are undocumented and drift.** When a provider changes a response shape, the widget degrades visibly (a `–`, a warning row with data age, or a traceback in the dropdown) rather than crashing or showing wrong numbers — but it may need a small code fix to catch up.
- **Rate limits cost freshness, not uptime.** On HTTP 429 the widget serves cached data and retries within 15 minutes, ignoring absurd `Retry-After` values (14 hours has been observed).
- **Expired tokens aren't auto-refreshed** (deliberately — see Security). The dropdown will tell you to run the CLI once.
- **The icon-visibility fix runs at boot.** If SwiftBar is quit and reopened mid-session (e.g. by its updater), the icon can come back hidden until the next boot — see Troubleshooting.

## Troubleshooting

**Icon vanished after a reboot?** SwiftBar records the status item as removed every time it quits, so it can come back hidden. Fix (find your key name with `defaults read com.ameba.SwiftBar | grep Visible`):

```sh
killall SwiftBar
defaults write com.ameba.SwiftBar "NSStatusItem VisibleCC Item-1" -bool true
defaults write com.ameba.SwiftBar "NSStatusItem Preferred PositionCC Item-1" -float 1
open -a SwiftBar
```

`Preferred Position 1` also pins the icon to the rightmost slot macOS allows third-party items. For a permanent fix, wrap those lines in a launchd agent (or login script) that runs them before starting SwiftBar.

**Everything easy to tweak** at the top of the script: poll interval, stale threshold, color thresholds in `color_for()`, and the title format string in `main()`.

## Security

An honest assessment — including the parts that should give you pause.

**Credit where it's due.** Of the three CLIs, Claude Code has the most secure token storage by default: it is the only one that puts its OAuth token in the macOS Keychain instead of a plain file on disk. That's exactly why it's the only provider with a permission prompt to deal with at all — and why this tool's central trade-off (below) exists: for convenience, we partially flatten Claude's stronger default down to the level the other two chose from the start.

**What happens to your tokens.** On each refresh the script reads a token, holds it in process memory for a single HTTPS request to the provider that issued it, and exits. Tokens are never written to disk, never logged, and never sent anywhere except three hardcoded endpoints (`api.anthropic.com`, `chatgpt.com`, `api.github.com`) over TLS via the system trust store. The script is one file of stdlib-only Python — no pip dependencies, so the supply chain you need to trust is this file plus SwiftBar. Nothing auto-updates and the script cannot fetch or execute remote code; updates only happen when you pull them.

**The real trade-off: "Always Allow" on the Keychain.** This is the one place the tool *weakens* your default security posture. Claude Code stores its OAuth token in the Keychain precisely so that each app must be individually approved to read it. Approving `/usr/bin/security` permanently removes that tripwire: afterwards, **any process running as your user can read the Claude Code token silently** with the same one-liner this script uses — the prompt that would have flagged it is exactly what you disabled. Two things put that in perspective rather than excuse it: (1) your Codex and Copilot tokens already sit in plain files (`~/.codex/auth.json`, `~/.config/github-copilot/apps.json`) that any user-land process can read — that is those tools' default token storage on every machine, not something this plugin sets up — after Always Allow, the Claude token is simply as exposed as the other two already are; (2) malware running as your user has many comparable options anyway. But if an attacker-shaped process on your Mac is in your threat model, don't grant Always Allow — click Allow per prompt or remove the Claude section from the script. A stolen token here doesn't expose your password, but it does let someone use these AI services as you and burn your quota — treat it as sensitive.

**How CodexBar handles the same problem.** It can't use this trick, because its Keychain access is tied to its own app signature: it offers a prompt-policy setting (controls *when* it asks, not the cause), a documented workaround of adding CodexBar.app to the Keychain item's Access Control list — narrower than our approach (only that one app can read the token) but fragile, since macOS drops the grant every time the app updates and re-signs — and a last-resort toggle to skip the Keychain entirely in favor of browser cookies or CLI probing. Granting `/usr/bin/security` is the opposite point on the same trade-off curve: broader access, but durable. Nothing gets you both narrow and permanent.

**What's cached on disk.** `~/.cache/ai-usage-bar/state.json` (file mode 600) keeps the last successful API responses so the widget survives network hiccups. It contains no tokens, but the provider responses include account metadata — plan names, account IDs, and (for Codex) your email. Delete it anytime.

**Unofficial APIs.** All three endpoints are internal/undocumented — the same read-only calls the official `/usage` screens make, but not a supported integration. They can change shape or disappear without notice (one already changed shape once during development), and strictly speaking, internal endpoints may not be covered by the providers' terms of service. Read-only usage polling is a mild case, but you should know it's not blessed.

**Residual gaps, stated plainly:** the Always Allow exposure above is permanent until you delete the Keychain item's ACL entry (or the item itself); the script trusts whatever is in the three local auth files without verifying what process put it there; and SwiftBar executes any script in your plugin folder, so that folder's write permissions are part of your attack surface.

## Credits

API endpoints discovered by reading the source of [steipete/CodexBar](https://github.com/steipete/CodexBar). Built with [SwiftBar](https://github.com/swiftbar/SwiftBar).

## License

[MIT](LICENSE)
