# claude.ai in Chrome

claude.ai keeps the account in four cookies: `sessionKey`, `sessionKeyLC`, `routingHint` and
`lastActiveOrg`. A fifth one, `__Host-ant_trusted_device`, is set when claude.ai registers the browser
for the Remote Control of the account. The companion swaps those five on every switch, so claude.ai in
Chrome follows without a sign-in, each account gets its own Remote Control registration back, and the
page keeps the settings of the browser.

## The parts

- The extension, `share/chrome-extension` (Manifest V3: `cookies`, `nativeMessaging`, `alarms`,
  `declarativeNetRequest`, claude.ai only). Its key fixes the id `klclebpiaondgkoelekdoimhcboaenal`.
- The native host, `share/chrome-host.py`. Chrome starts it, and `claude-switch` reaches it through the
  socket `~/.claude-accounts/.chrome.sock`.
- The saved sessions, `~/.claude-accounts/.web/<accountUuid>.json`, mode 600.

## A swap

1. The host names the open session: its `sessionKey` among the saved ones, else `GET
   https://claude.ai/api/account` with those cookies, the only place they are sent. Only a 401 or 403
   in JSON with `error.details.error_code` `account_session_invalid` means signed out. A Cloudflare
   page, another refusal, a 5xx or no network means nobody can tell, and nothing changes.
2. The extension blocks every claude.ai request with a session rule and waits a second. A page that
   sends half a swapped session gets it revoked (seen on 2026-10-30).
3. The five cookies are read. When the `sessionKey` changed in the meantime, the swap stops.
4. The open session is saved under its account, the saved one of the target goes in with `sessionKey`
   last, and the result is checked. A cookie of it that ran out on its own, an old registration for
   instance, is left out, since Chrome would not keep it.
5. claude.ai is unblocked and its tabs reload. A host that stops halfway lifts the block at its next
   start.

Nobody logs out, so every saved session stays valid on the server. A web session lasts 28 days from
the last activity on claude.ai. A saved session that expired is dropped, and claude.ai asks for one
sign-in.

When Chrome is closed, the switch is left in `~/.claude-accounts/.chrome-pending` and the host applies
it when Chrome starts, retried every 30 seconds for 10 minutes. A newer switch replaces it.

`~/.local/share/claude-switch/chrome-host.py --status` prints the account open in Chrome and the
saved ones.

## Remote Control

The first time an account opens a Remote Control session in Chrome, claude.ai asks for a fresh sign-in
and registers the browser for that account: a device key in its own site storage, one per account, and
the `__Host-ant_trusted_device` cookie, one per browser. The switch carries that cookie with its
account, so a registered account opens its remote sessions again without a sign-in. An account that
never registered in this browser has no cookie to bring back, so claude.ai asks for that one sign-in.
It also ends the session that was open, and the next switch saves the new one.

## Limits

- One Chrome profile follows at a time. A second profile that loads the extension takes the switch
  over.
- The Claude in Chrome extension keeps its own login and can't be switched from outside. When its
  account changes it deletes its scheduled tasks, so it is left alone.
- A change to `chrome-host.py` reaches Chrome when the running host stops: `install.sh` stops it, and
  the extension starts the new one within 10 seconds. A change to the extension needs Reload in
  `chrome://extensions`, or a Chrome restart.
