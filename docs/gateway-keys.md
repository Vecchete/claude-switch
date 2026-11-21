# Gateway keys

Claude Code can reach Claude through an LLM gateway such as LiteLLM instead of a claude.ai login. The
key, the URL and the model names of the gateway go in the `env` of `~/.claude/settings.json`, and Claude
Code takes that key before the claude.ai login. claude-switch keeps such a key as an account, so a
switch moves between gateway keys and claude.ai accounts the same way.

## What a key holds

A gateway key account is the set of these entries of the `env`:

| Entry | What it is |
|---|---|
| `ANTHROPIC_AUTH_TOKEN` | The token, sent as `Authorization: Bearer` |
| `ANTHROPIC_API_KEY` | A token sent as `X-Api-Key` instead, a Claude Console API key for instance |
| `ANTHROPIC_BASE_URL` | The URL of the gateway |
| `ANTHROPIC_MODEL`, `ANTHROPIC_SMALL_FAST_MODEL` | The models the gateway serves |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL` | What the aliases `haiku`, `sonnet` and `opus` mean on that gateway |
| `ANTHROPIC_CUSTOM_HEADERS` | Headers, for a gateway that takes its key in a header |

The other entries of `env` and the rest of `settings.json` are yours, not the key's, and a switch leaves
them alone. Each key is one file, `~/.claude-accounts/key_<name>.json`, mode 600.

## Saving a key

`key import` saves the key `settings.json` holds now:

```bash
claude-switch key import work-gw
```

`key add` saves another one. It asks for the token on the terminal, where it shows as you type or paste it,
or reads it from the standard input. It never takes it from the command line, where the shell history and
the process list would keep it:

```bash
claude-switch key add team-gw --like work-gw
claude-switch key add other-gw --base-url https://gw.example --env ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet
claude-switch key add console --api-key
pass show llm/ci-gw | claude-switch key add ci-gw --like work-gw
```

| Option | What it does |
|---|---|
| `--like KEY` | Takes the URL, the model names and the headers of the key `KEY`. The token goes in the same variable as the token of `KEY`. |
| `--api-key` | Puts the token in `ANTHROPIC_API_KEY`, sent as `X-Api-Key`, instead of `ANTHROPIC_AUTH_TOKEN`. A Claude Console API key needs it. |
| `--base-url URL` | Sets `ANTHROPIC_BASE_URL`. |
| `--env VAR=VALUE` | Sets another entry of the table above. Repeat it for more entries. |

A gateway that takes its key in `ANTHROPIC_CUSTOM_HEADERS` is saved with `key import`, since `key add`
asks for a token.

The prompt saves a key too. `claude-switch`, then `a`, asks for the kind of account:

```text
   s) A subscription, signed in with the browser
   k) An API key or a gateway key

Which kind of account? k
Name of the key: console
Gateway URL (Enter for the Anthropic API):
Switch to it once it's added? [y/N]
Gateway key for console:
```

An empty URL keeps an Anthropic API key, as `key add NAME --api-key` does, and a URL keeps a gateway key, as
`key add NAME --base-url URL` does. The name is checked before the URL is asked, and a URL must start with
`https://` or `http://`. Yes to `Switch to it once it's added?` switches to the key once it's kept, as
`key add NAME --switch` does. Without it the machine stays on its account, and
[automatic switching](auto.md#an-account-you-add), when it's on, ranks the new key with the others.

`key renew NAME` gives a key a new token, asked the same way, in the variable its token was in. Its URL
and model names stay. When the key is the active one, `settings.json` gets the new token too.

`key remove NAME` forgets a key. When it's the active one, its entries are emptied in `settings.json`
first, so Claude Code uses the claude.ai login again. `claude-switch delete NAME`, or `d` in the prompt,
does the same, and takes the key's readings with it.

## Switching

```bash
claude-switch team-gw     # to a key
claude-switch personal    # to a claude.ai account
```

A switch to a key empties the gateway entries of `settings.json`, then puts in the ones of the key. A
switch to a claude.ai account switches the login, then empties the gateway entries, so Claude Code uses
the login again. claude.ai in Chrome and Claude Desktop stay on their account in both cases, and the
background refresh skips keys. A machine without gateway keys never gets `settings.json` rewritten.

The entries stay in `settings.json` as `""` rather than being taken out. Claude Code takes a change of
`env` into its running sessions, but a variable that is taken out stays set in a running session, so a
session started on a key would go on with it. Claude Code takes `""` as unset, so running sessions leave
the gateway too, and new sessions start on the login. `""` also cancels the same variable exported in
the shell.

Running sessions follow the token and the URL at their next request. The model names follow at the next
turn, through the aliases `sonnet`, `opus` and `haiku`. Two things don't follow:

- A turn already under way keeps the model it started with.
- A session that started with an `ANTHROPIC_MODEL` keeps that model. The switch says so, and `/model`
  in that session picks one of the new account.

These were seen with Claude Code 2.1.326 against a stand-in gateway.

## How the active key is found

`list` and the prompt mark the key whose entries are those of `settings.json`. A key is known by its
credential first, its token or, without a token, its headers. Then by its URL, then by the rest:

1. A key whose entries all match wins.
2. Then the one with the same credential and the same URL.
3. Then the only one with the same credential.

So two keys may share a token at two gateways, and entries you change by hand in `settings.json` still
name their key. Before any command, claude-switch saves those changes to the active key, so a switch
never loses them. Two keys with exactly the same entries could not be told apart, so `key add`,
`key import` and `key renew` refuse a second one.

## A token no account keeps

A switch replaces the token in `settings.json`. When no account keeps that token, a switch would lose it,
so every switch refuses, and `list` marks no account:

```text
claude-switch: ~/.claude/settings.json holds a gateway key no account keeps, nothing changed. Keep it with: claude-switch key import NAME. To forget it, remove it after that with: claude-switch key remove NAME
```

That happens when you write a key in `settings.json` by hand, when another tool writes one there, or when
you delete the file of the active key. Headers without a token never stop a switch, since a proxy in
front of claude.ai may need headers of its own.

## How settings.json is written

Running sessions read `settings.json` at any moment, so claude-switch writes it aside and renames it over
the old one, with the same mode, owner and group. When you can't give a file that owner or group,
claude-switch writes in place instead, which keeps them.

- A `settings.json` that is a symbolic link, from a dotfiles manager for instance, stays a link, and the
  file it points to is the one written.
- When that file sits in a folder you can't write, a read-only store for instance, the switch stops
  before anything changes.
- A missing or empty `settings.json` is taken as `{}`.
- A `settings.json` that is not valid JSON stops a switch to a key before anything changes.

## What goes before the account

Claude Code picks one credential, in this order (see its
[authentication docs](https://code.claude.com/docs/en/authentication)):

1. A cloud provider: `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` or `CLAUDE_CODE_USE_FOUNDRY`.
2. `ANTHROPIC_AUTH_TOKEN`.
3. `ANTHROPIC_API_KEY`.
4. `apiKeyHelper`.
5. `CLAUDE_CODE_OAUTH_TOKEN`.
6. A profile named in `ANTHROPIC_PROFILE`.
7. The claude.ai login.

After a switch, and in `list` and the prompt, claude-switch names what `settings.json` sets that goes
before the active account:

```text
claude-switch: ~/.claude/settings.json sets apiKeyHelper, which claude uses before personal.
```

A value in `settings.json` goes before the same variable exported in the shell, `""` included. So a
switch warns about a variable of your shell only when it goes before the account switched to and
`settings.json` doesn't set it. `CLAUDE_CONFIG_DIR` gets a warning on every switch, since a claude started
with it reads another folder:

```text
claude-switch: ANTHROPIC_AUTH_TOKEN is set in this shell, a claude started from here doesn't follow the switch.
```

## What a key has left

`list` and the prompt show what a gateway key has left of its budget, read from the `/key/info` of its
gateway with the key itself, LiteLLM's: what it spent, its budget and when the budget resets. Another
gateway may not answer it, so `LEFT` shows `-` for its keys. No API gives out the credit an Anthropic API
key has left, so `LEFT` shows `not readable` for one: the Billing page of the Claude Console shows it.
[Automatic switching](auto.md) counts a key as having room while it's under its budget, and an Anthropic
API key until a request says its credit is too low.

## Limits

- claude.ai in Chrome and Claude Desktop don't use gateway keys, so a switch to a key leaves them on
  their account.
- The [Windows port](../windows/README.md) has gateway keys too. There `settings.json` takes the ACL of its
  folder rather than a mode, owner and group, and one marked read-only stops a switch to or from a key
  before anything changes, see [What differs from Linux](../windows/README.md#what-differs-from-linux).
