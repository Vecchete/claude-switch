# Automatic switching

With automatic switching on, claude-switch keeps the machine on the best account that has room left. When a
Claude Code session meets a limit, the machine moves to the next account and the session carries on by itself,
without a `continue` typed. When a better account has room again, the machine goes back to it.

```bash
claude-switch auto on            # turn it on
claude-switch auto               # how it stands
claude-switch auto order max     # Max and the other plans first, on this machine
claude-switch auto off           # turn it off
```

It's off until you turn it on.

## The best account with room

Only the week counts. The 5-hour window of a plan never decides the order and never shows in `list`. The
accounts are of three kinds, ranked by default in this order:

| Kind | What it is |
|---|---|
| `ultra` | A subscription with no 5-hour limit: Ultra, once the account is verified |
| `key` | An API key or a [gateway key](gateway-keys.md) |
| `max` | Max, Pro, Team and the other plans, which have a 5-hour limit |

A subscription counts as `ultra` when its plan is Ultra, or when its usage shows no 5-hour window. An Ultra
account that isn't verified yet still has a 5-hour limit, and when it meets it, it's refused like any other.

`claude-switch auto order KIND...` puts those kinds first, in the order given, on this machine. The kinds left
out follow in the default order, so `claude-switch auto order max` ranks Max and the other plans first, then
Ultra, then keys. `api` is taken as `key`. Without kinds, `claude-switch auto order` prints the order.

Within a kind, the account the machine is on stays while it has room, so one account is used up before the next
one starts. Between the others of that kind, the allowance that expires first goes first, so nothing that resets
goes unused:

- for subscriptions, the one whose week resets soonest,
- for keys, a gateway budget that resets, the soonest first, then a budget that never resets, then a gateway
  key with no budget, then a gateway that didn't answer, then a key whose credit nobody can read, an Anthropic
  API key for instance.

An account has room while:

- its week is under 100%, or a gateway key is under its budget,
- it's still signed in, and its sign-in hasn't ended,
- no session met one of its limits since that limit last reset, see below.

No API gives out the credit an Anthropic API key has left, so such a key counts as having room until a request
answers that its credit is too low.

### A switch you make yourself

A switch you type, `claude-switch NAME` or a number in the prompt, stays while automatic switching is on and that
account has room. Once it runs out, automatic switching takes over again. `claude-switch auto on` lets go of it
too.

### An account you add

Adding an account, with `claude-switch login`, `claude-switch key add` or `a` in the prompt, doesn't switch to
it. With automatic switching on, the new account is read and ranked with the others at once, and the machine
goes to it when it comes first. Added with `--switch`, or with yes to `Switch to it once it's added?` in the
prompt, the machine goes to it right away, and nothing holds it there, not even an account a switch by hand
held before: the next check moves the machine on when another account comes first.

## When it switches

Two things move the machine:

- **The watch.** The `claude-switch-watch` timer runs `claude-switch watch` every 5 minutes. It does nothing
  unless automatic switching is on and Claude Code has a session open: `~/.claude/sessions` lists one whose
  process still runs. Then it reads what the accounts have left and moves the machine when the best account
  isn't the active one, which is how it goes back to a better account once its limit resets.
- **The hook.** `claude-switch auto on` adds a `StopFailure` hook to `~/.claude/settings.json`. Claude Code runs
  it when a turn ends on an API error. On a usage limit, an empty credit, a refused sign-in, or an unknown error
  whose text says a budget ran out, which is how LiteLLM answers, the hook marks the active account as refused,
  moves the machine to the best account with room, and wakes the session.

A refused account waits until the limit that stopped the session resets. That's the reset time of the limit its
usage shows at 100%, the latest when several are, a model's weekly limit included. When nothing says when:

| What stopped the session | How long the account waits |
|---|---|
| A usage limit of a subscription, without a reading | 15 minutes |
| The rate limit of a key | 5 minutes |
| Credit too low | A day, or until `claude-switch key renew` |
| A gateway budget without a reset date | An hour |
| A refused sign-in | An hour, or until a new sign-in with `claude-switch login` |

### The session carries on

The hook runs in the background, with `asyncRewake`. Its exit status 2 wakes the session, even an idle one, and
Claude reads this sentence:

```text
claude-switch moved this machine to work-gw: personal reached its weekly limit, until Fri 22:00. The request that failed can go out again now: carry on with the task where it stopped.
```

The session sends its request again, on the new account: a running session takes in a new login, or a new key,
at its next request. Everything else claude-switch says while it switches goes to `~/.claude-accounts/.auto.log`,
since Claude reads what the hook says.

When several sessions meet the limit together, the first hook switches. The others see that the switch came after
their own request failed, so they only wake their session. A session is woken 3 times in 10 minutes at most, so
one every account refuses doesn't go round in circles.

When no account has room, nothing switches and the session isn't woken. Claude Code itself says when the limit
resets and carries on then. claude-switch shows a desktop notification, once a day, naming the account that has
room again first.

### What moves with an automatic switch

The same as with a switch you type, see [switch.md](switch.md), except for one thing: Claude Desktop restarts
without asking when the switch goes from one claude.ai account to another. Its Code and Cowork tasks stop with
it. A switch to or from a key leaves Desktop and Chrome alone. Every automatic switch shows a desktop
notification, and Remote Control needs `/remote-control` again in a session after a claude.ai account changed.

When the session that met the limit runs in Claude Desktop, in its Code tab, the hook runs inside Desktop, and
closing Desktop would end the hook with the session. So the machine moves on, Desktop stays on its account, and
the notification says so. That session isn't woken: it goes on with the login of Desktop, which only a restart
of Desktop changes. Close Desktop and run `claude-switch NAME` with the account the machine moved to, and Desktop
comes along.

## What it reads, and from where

| Account | What it reads | From |
|---|---|---|
| claude.ai subscription | The week, the 5-hour window and the weekly limits of models, with their reset times | `https://api.anthropic.com/api/oauth/usage`, the endpoint the `/usage` of Claude Code reads, with the access token of that account. For the account Claude Code is on, Claude Code's own last reading in `~/.claude.json` first, when it's the newer one |
| Gateway key | What the key spent, its budget and when it resets | `<gateway>/key/info`, then `/key/info` at the root of the gateway, with the key itself. LiteLLM answers it, other gateways may not |
| Anthropic API key, key in headers | Nothing | |

A login goes to Anthropic only, never to `ANTHROPIC_BASE_URL`, and a key only to its own gateway. Anthropic
doesn't document the usage endpoint and refuses frequent calls, so it's read at most every 4 minutes per account
while the watch runs, and not at all for an account that is full until its limit resets. `list` and the prompt
read it again when the last reading is older than 5 minutes. An access token that ran out is left to the
background refresh. A reading that fails keeps what was known.

## What list shows

`list` and the prompt have a `LEFT` column:

| `LEFT` | Means |
|---|---|
| `49% until Fri 22:00` | 49% of the week is left, and the week resets on Friday at 22:00 |
| `none until Fri 22:00` | The week is used up until then |
| `$21.80 until 01/12` | What the gateway budget has left, and when it resets |
| `no limit` | The gateway key has no budget |
| `refused until 17:20` | A session met a limit of this account, which waits until then |
| `signed out` | It needs a new sign-in, see [refresh.md](refresh.md) |
| `not readable` | An Anthropic API key: no API gives out the credit it has left, the Billing page of the Claude Console shows it |
| `-` | Nothing to read: a key in headers only, or no answer |

When an Anthropic API key is among the accounts, a line under the table says so once.

A time today shows as `17:20`, one this week as `Fri 22:00`, one later as `01/12`.

## Turning it on and off

`claude-switch auto on`:

1. adds the hook to `~/.claude/settings.json`, beside your other hooks, once, and the rest of the file stays as
   it is, with the same care as a [key switch](gateway-keys.md#how-settingsjson-is-written). A hook of
   claude-switch installed elsewhere before, or written the way the Windows version writes it, gives way to it.
   The sessions already open take it in too,
2. starts the `claude-switch-watch` timer,
3. moves the machine to the best account with room right away.

`claude-switch auto off` takes the hook out and stops the timer. A `settings.json` in a folder you can't write
stops `auto on` before anything changes. `install.sh` installs the timer and starts it again only while automatic
switching is on.

## Files

Beside the accounts, in `~/.claude-accounts`, mode 600 in a folder of mode 700:

| Path | What it holds |
|---|---|
| `.auto` | There while automatic switching is on |
| `.auto-order` | The order of `claude-switch auto order` |
| `.usage/<account>.json` | The last reading of an account |
| `.usage/<account>.refused.json` | Until when the account waits after a session met its limit |
| `.hold` | The account a switch you typed holds |
| `.switched`, `.woken/` | When the last switch happened, and when each session was woken |
| `.auto.log` | What the automatic switches did, cut to its last 2000 lines once it passes 1 MB |

## Limits

- Claude Code documents that it ignores the exit status of a `StopFailure` hook. The wake through `asyncRewake`
  was checked with Claude Code 2.1.329, and a later version could stop it. The watch still switches then, and a
  session stopped on a limit needs a `continue`.
- The usage endpoint isn't documented. When it changes or refuses, `list` shows `-` and only a limit a session
  meets switches.
- The watch runs only while a Claude Code session is open. Claude Desktop alone, or claude.ai in Chrome, doesn't
  count.
- A turn under way keeps the account it started on until its next request.
- Vertex and Bedrock are not kinds of account.
