# Claude Desktop

Claude Desktop has its own sign-in, apart from Claude Code. Its Code tab runs its own copy of Claude Code
with the token of the account Desktop is signed in to, while the settings, MCP servers and sessions in
`~/.claude` and `~/.claude.json` are shared with the command line. So Desktop follows every switch too,
and the Code tab runs on the same account as the terminal.

The supported app is Anthropic's own Linux beta, from its apt repository
(`https://downloads.claude.ai/claude-desktop/apt/stable`, signing key
`31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE`). Other builds were not tested.

## What belongs to the account

Desktop keeps everything in `~/.config/Claude`. Three things in that folder belong to the account, as read
in Desktop 4.4212.0:

- The claude.ai session cookies in `Cookies`: `sessionKey`, `sessionKeyLC`, `routingHint`,
  `lastActiveOrg` and the `sessionKeyV2` and `sessionKeyV3` variants Desktop also reads, with
  `__Host-ant_trusted_device`, set when claude.ai registers Desktop for the Remote Control of the account.
- The tokens of the Code tab, `oauth:tokenCacheV2` and the older `oauth:tokenCache` in `config.json`.
- The account Desktop saw last, `lastKnownAccountUuid` in `config.json`.

The cookies and the tokens are encrypted with one key that Desktop keeps in the desktop keyring, under
`Claude Safe Storage`. That key is the same for every account, so `desktop.py` saves and puts back the
encrypted values as they are and never decrypts them.

`lastKnownAccountUuid` always moves with the rest. When it names the account Desktop finds signed in,
Desktop carries on as before. When it doesn't, Desktop takes the change for another person: it clears its
tokens, disconnects the connectors and resets the tool permissions.

## A switch

`claude-switch NAME` runs `desktop.py switch` with the account of `NAME`:

1. The open session is named by its cookies among the saved ones, or else by `lastKnownAccountUuid`. A
   session nobody can name is never saved or replaced.
2. When Desktop is already on that account, there is nothing to do, also with Desktop open.
3. When Desktop is open, it holds its cookies and writes `config.json` as it quits, so nothing can change
   under it. In a terminal, the switch asks whether to restart Desktop. Yes is the default: Enter, or no
   answer within 60 seconds, restarts it. An [automatic switch](auto.md) restarts it without asking. A
   restart closes Desktop the way quitting it does, switches it and starts it again, without the
   environment of the calling shell, and in a systemd scope of its own, so the timer that made the switch
   doesn't take it along when it ends. Code and Cowork tasks running in Desktop stop. No, or a switch
   from a script without a terminal, leaves Desktop on its account, and running `claude-switch NAME` again
   once Desktop is closed brings it along.
4. The open session is saved in `~/.claude-accounts/.desktop/<account>.json`, mode 600.
5. The saved session of the target goes in, the cookies first and `config.json` after, with TERM, INT
   and HUP held between the two. When there is no saved session for that account yet, Desktop is left
   signed out, already expecting that account. Sign in to it once, and the switch keeps it from then on.

The rest of the folder stays, so the window, the theme, the MCP servers of `claude_desktop_config.json`,
the extensions and the Code sessions are the same on every account.

The switch refuses to touch Desktop when its `config.json` can't be read, or when a `config.json.journal`
is left over, which Desktop replays at its next start. Starting and quitting Desktop once settles both.

## desktop.py

`~/.local/share/claude-switch/desktop.py` runs on its own too:

| Command | What it does |
|---|---|
| `desktop.py status` | Prints what Desktop is on, as JSON |
| `desktop.py switch ACCOUNT` | Saves the open sign-in under its account and puts back the one of `ACCOUNT` |
| `desktop.py quit [SECONDS]` | Closes Desktop and waits until it's gone, 20 seconds at most by default |
| `desktop.py start` | Starts Desktop, without the environment of the calling shell, in a systemd scope of its own when the user's systemd runs |

## Limits

- The refresh timer doesn't touch Desktop. Desktop refreshes its own tokens while it runs on that
  account, so a saved sign-in that expired in the meantime means one sign-in in Desktop.
- Cookies saved by another version of the cookie database are not put back, which also means one
  sign-in.
- The first Remote Control session of an account in Desktop asks for one sign-in, which registers
  Desktop for that account. From then on the registration moves with the account.
- When the keyring loses `Claude Safe Storage`, every saved Desktop sign-in is lost with it.
- The encryption is the OSCrypt of Chromium (AES-128-CBC with a fixed IV, `v11`), which is what makes a
  saved session recognizable by its bytes. Desktop 4.4212.0 runs Electron 44, and Electron 46 removes the
  synchronous `safeStorage` API. A Desktop update that changes how it encrypts can make the saved sign-ins
  unreadable, which means one sign-in per account.
