# Better MacOS Token Usage Menu Bar

All your AI coding limits in the macOS menu bar — **Claude Code, OpenAI Codex, and GitHub Copilot** — with zero credential prompts, ever.

```
CC49│88-Cx78│23        ← menu bar: Claude session│weekly – Codex session│weekly
```

All numbers are **% remaining** (how much you have left, not how much you used). The title turns orange below 30% left and red below 10%. Click it for details:

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

## Why not CodexBar & friends?

Apps that read the Claude Code OAuth token from the macOS Keychain re-trigger the credential prompt on every app update (each re-signed binary invalidates the "Always Allow"). This plugin reads the Keychain through Apple's own `/usr/bin/security` — which is never re-signed — so you approve **once** and never see the prompt again. Codex and Copilot tokens live in plain config files; no Keychain involved at all.

## Install

```sh
brew install --cask swiftbar
mkdir -p ~/.swiftbar
curl -o ~/.swiftbar/ai-usage.1m.py https://raw.githubusercontent.com/RichStone/better-macos-token-usage-menu-bar/main/ai-usage.1m.py
chmod +x ~/.swiftbar/ai-usage.1m.py
defaults write com.ameba.SwiftBar PluginDirectory "$HOME/.swiftbar"
open -a SwiftBar
```

On the first refresh macOS asks for Keychain access — click **Always Allow**. That's the last prompt you'll ever see.

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
- **Codex windows are identified by duration** (5h vs 7d), not by their position in the response — the API nulls out idle windows and promotes whatever remains, so position lies. A missing window renders as "100% left · untouched".
- **Tokens are never refreshed by the script** (that would invalidate your CLI's session). If a token expires, the dropdown says so — running the CLI once fixes it.
- **Nothing leaves your machine** except the HTTPS calls to the three providers. Last-known-good data is cached in `~/.cache/ai-usage-bar/state.json`.
- If the script ever crashes, the menu bar shows `CC?│?-Cx?│?` and the dropdown shows the traceback with a retry item.

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

## Credits

API endpoints discovered by reading the source of [steipete/CodexBar](https://github.com/steipete/CodexBar). Built with [SwiftBar](https://github.com/swiftbar/SwiftBar).

## License

[MIT](LICENSE)
