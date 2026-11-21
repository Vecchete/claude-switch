# claude.ai in Chrome

claude.ai keeps the signed-in account in four cookies: `sessionKey`, `sessionKeyLC`, `routingHint` and
`lastActiveOrg`. A fifth one, `__Host-ant_trusted_device`, is set when claude.ai registers the browser
for the Remote Control of an account. A companion extension swaps those five cookies on every switch, so
claude.ai in Chrome follows the switch without a sign-in, and the page keeps the settings of the browser.

## Setup

1. Run `./install.sh`. It installs the extension and the native messaging host in
   `~/.local/share/claude-switch`, and writes the manifest of the host for Google Chrome and Chrome Beta,
   when `~/.config/google-chrome` or `~/.config/google-chrome-beta` exists.
2. Open `chrome://extensions`, turn on Developer mode, click Load unpacked and pick
   `~/.local/share/claude-switch/chrome-extension`.

The key in the manifest of the extension fixes its id, `klclebpiaondgkoelekdoimhcboaenal`, so the host
manifest can name it. To see which account Chrome is on and which sessions are saved:

```bash
~/.local/share/claude-switch/chrome-host.py --status
```

## The parts

- The extension, `share/chrome-extension`, Manifest V3. It asks for `cookies`, `nativeMessaging`,
  `alarms` and `declarativeNetRequest`, and for claude.ai only.
- The native host, `share/chrome-host.py`. Chrome starts it for the extension, and claude-switch reaches
  it through the socket `~/.claude-accounts/.chrome.sock`.
- The saved sessions, `~/.claude-accounts/.web/<account>.json`, mode 600. `claude-switch delete` takes the
  session of the account it deletes, unless another account kept here is the same login.

## A swap

1. The host names the session open in Chrome: its `sessionKey` among the saved ones, or else the answer
   of `GET https://claude.ai/api/account` with those cookies, the only place they are sent. Only a 401 or
   403 answer in JSON with the error code `account_session_invalid` means signed out. A Cloudflare page,
   another refusal, a server error or no network means nobody can tell, and nothing changes.
2. The extension blocks every request to claude.ai and waits a second. A page that sends half a swapped
   session gets that session revoked by claude.ai.
3. The five cookies are read. When `sessionKey` changed in the meantime, the swap stops.
4. The open session is saved under its account. The saved session of the target goes in, `sessionKey`
   last, and the result is checked. A cookie that expired on its own is left out, since Chrome would
   refuse it.
5. claude.ai is unblocked and its tabs reload. A host that stops halfway lifts the block at its next
   start, and the extension lifts it after 20 seconds anyway.

Nobody signs out, so every saved session stays valid on the server. A claude.ai session lasts 28 days
from its last use on claude.ai. When a saved session expired, it's dropped and claude.ai asks for one
sign-in.

When Chrome is closed, the switch waits in `~/.claude-accounts/.chrome-pending`, and the host applies it
when Chrome starts, trying every 30 seconds for 10 minutes. A newer switch replaces it.

A sign-in that adds an account goes through the browser, which can leave claude.ai on the new account while
the machine stays on its own. Without `--switch`, `claude-switch login` then runs `chrome-host.py --back`
with the active account: claude.ai goes back to it when a session of it is saved, and otherwise stays on the
account open there, rather than signed out.

## Remote Control

The first time an account opens a Remote Control session in Chrome, claude.ai asks for a fresh sign-in
and registers the browser for that account. It keeps a device key in its own site storage, one per
account, and the `__Host-ant_trusted_device` cookie, one per browser. The switch carries that cookie with
its account, so a registered account opens its remote sessions again without a sign-in. An account that
never registered in this browser has no cookie to bring back, so claude.ai asks for that one sign-in. It
also ends the session that was open, and the next switch saves the new one.

## Privacy

The extension only sees claude.ai. The host reads and writes the claude.ai cookies, sends them only to
`https://claude.ai/api/account` to name a session it doesn't know, and keeps the saved sessions in
`~/.claude-accounts/.web`, readable only by you.

## Limits

- Only Google Chrome and Chrome Beta get the host manifest. Chromium, Brave and the other browsers built
  on Chromium are not set up.
- One Chrome profile follows at a time. A second profile that loads the extension takes the switch over.
- The Claude in Chrome extension keeps its own login and can't be switched from outside. When its
  account changes it deletes its scheduled tasks, so claude-switch leaves it alone.
- A new `chrome-host.py` reaches Chrome when the running host stops. `install.sh` stops it, and the
  extension starts the new one within 10 seconds. A new version of the extension needs Reload in
  `chrome://extensions`, or a restart of Chrome.
