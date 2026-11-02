# Claude Desktop

Claude Desktop has its own sign-in, apart from Claude Code. Its Code tab runs its own copy of Claude
Code with the token of Desktop's account, while the settings, MCP servers and sessions in `~/.claude`
and `~/.claude.json` are shared with the CLI. So Desktop follows every switch too, and the Code tab
runs on the same account as the terminal.

Only the official app counts here: Anthropic's Linux beta from its apt repository
(`https://downloads.claude.ai/claude-desktop/apt/stable`, signing key
`31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE`).

## What belongs to the account

Desktop keeps everything in `~/.config/Claude`. Three things in it belong to the account, read in the
4.4212.0 app:

- the claude.ai session cookies in `Cookies` (`sessionKey`, `sessionKeyLC`, `routingHint`,
  `lastActiveOrg` and the `sessionKeyV2`, `sessionKeyV3` variants Desktop also reads), with
  `__Host-ant_trusted_device`, set when claude.ai registers Desktop for the Remote Control of the account
- the tokens of the Code tab, `oauth:tokenCacheV2` (and the older `oauth:tokenCache`) in `config.json`
- the account Desktop saw last, `lastKnownAccountUuid` in `config.json`

The cookies and the tokens are encrypted with one key that Desktop keeps in the desktop keyring
(`Claude Safe Storage`). The key is the same for every account, so `share/desktop.py` saves and puts
back the encrypted values as they are and never decrypts them.

`lastKnownAccountUuid` always moves with the rest. When it names the account Desktop finds signed in,
Desktop carries on as before. When it doesn't, Desktop takes the change for another person: it clears
its tokens, disconnects the connectors and resets the tool permissions.

## A switch

`claude-switch NAME` runs `desktop.py switch` with the account of NAME:

1. The open session is named by its cookies among the saved ones, else by `lastKnownAccountUuid`. A
   session nobody can name is never saved or replaced.
2. Already on that account: nothing to do, also with Desktop open.
3. Desktop open: it holds its cookies and writes `config.json` as it quits, so nothing can change under
   it. In a terminal, the switch asks whether to restart Desktop (`[y/N]`, no after 60 seconds). Yes
   closes it (TERM, the same as quitting it), switches it and starts it again, without the
   environment of the calling session. Code and Cowork tasks running in it stop. No, or no terminal,
   leaves it on its account, and `claude-switch NAME` brings it along later, once it is closed.
4. The open session is saved in `~/.claude-accounts/.desktop/<accountUuid>.json`, mode 600.
5. The saved one of the target goes in, the cookies first and `config.json` after, with TERM, INT and
   HUP held between the two. With no saved one, Desktop is left signed out, already expecting that
   account: sign in to it once, and the switch keeps it from then on.

The rest of the folder stays, so the window, the theme, the MCP servers of
`claude_desktop_config.json`, the extensions and the Code sessions are the same on every account.

Desktop is refused when its `config.json` can't be read or a `config.json.journal` is left over (Desktop
replays it at its next start). Starting and quitting Desktop once settles it.

## Limits

- The refresh timer doesn't touch Desktop. Its tokens are refreshed by Desktop itself, while it runs on
  that account. A saved sign-in that expired in the meantime means one sign-in in Desktop.
- Cookies saved by another version of the cookie database (24 today) are not put back, which also
  means one sign-in.
- The first Remote Control session of an account in Desktop asks for one sign-in, which registers
  Desktop for that account. From then on the registration moves with the account.
- When the keyring loses `Claude Safe Storage`, every saved Desktop sign-in is lost with it.
- The encryption is Chromium's OSCrypt (AES-128-CBC, fixed IV, `v11`), which is what makes a saved
  session recognizable by its bytes. Desktop 4.4212.0 runs Electron 44, and Electron 46 removes the
  synchronous `safeStorage` API. A Desktop update that changes how it encrypts can make the saved
  sign-ins unreadable, which means one sign-in per account.
- `desktop.py status` prints what Desktop is on, `desktop.py quit` and `desktop.py start` close and
  start it.
