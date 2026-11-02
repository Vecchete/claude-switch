#!/usr/bin/env bash
# Usage: test-completion.sh COMPLETION. The bash completion on a scratch HOME, called directly and through
# readline in a real interactive bash.
set -u
COMPLETION=$(readlink -f "$1")
T=$(mktemp -d "${TMPDIR:-/tmp}/claude-switch-completion.XXXXXX")
trap 'rm -rf "$T"' EXIT
export HOME=$T/home SHELL=/bin/bash INPUTRC=/dev/null
STORE=$HOME/.claude-accounts
mkdir -p "$STORE"
# A name a shell would run runs here, where the test looks for what it leaves.
cd "$T" || exit 1

fails=0 passes=0
check() {
    if eval "$2" >/dev/null 2>&1; then passes=$((passes + 1)); else fails=$((fails + 1)); printf 'FAIL %s\n' "$1"; fi
}
# An account file, with a login the completion must never need. NAME EMAIL
account() {
    jq -n --arg name "$1" --arg email "$2" '{name: $name, login: {claudeAiOauth: {accessToken: "secret"}},
        oauthAccount: {emailAddress: $email}}' > "$STORE/$2.json"
}
# What the completion offers for LINE with the cursor at its end, sorted. LINE [BASH_OPTION...]
offers() {
    COMP_LINE=$1 COMP_POINT=${#1} bash --norc --noprofile "${@:2}" -c \
        'source "$0"; _claude_switch; [ "${#COMPREPLY[@]}" -eq 0 ] || printf "%s\n" "${COMPREPLY[@]}"' \
        "$COMPLETION" 2> "$T/err" | LC_ALL=C sort
}
expect() {
    printf '%s\n' "$@" | LC_ALL=C sort
}
# Types KEYS in an interactive bash that loaded the completion, and prints the arguments claude-switch got.
typed() {
    rm -f "$T/args"
    printf '%s\nexit\n' "$1" | script -qfc "bash --noprofile --rcfile $(printf %q "$T/rc") -i" /dev/null > "$T/screen" 2>&1
    cat "$T/args" 2>/dev/null
}
printf 'source %q\nclaude-switch() { printf "[%%s]" "$@" > %q; }\n' "$COMPLETION" "$T/args" > "$T/rc"

# Two emails with the same beginning, a name with a space, a name a shell would run, and no name.
account "" lucas@example.com
account work lucas@example.org
account "my laptop" other@example.net
account '$(touch PWNED)' x@example.com
LAPTOP='my\ laptop' RUN='\$\(touch\ PWNED\)'
ACCOUNTS=(lucas@example.com work lucas@example.org "$LAPTOP" other@example.net "$RUN" x@example.com)
COMMANDS=(list login rename refresh help)
BEFORE=$(cd "$STORE" && ls -A && md5sum ./*.json)

offers "claude-switch " > "$T/got"
check "first word: the commands and every account" 'expect "${COMMANDS[@]}" "${ACCOUNTS[@]}" | diff - "$T/got"'
check "an account without a name: its email only" '! grep -qx "" "$T/got"'
offers "claude-switch lo" > "$T/got"
check "first word: by its beginning" 'expect login | diff - "$T/got"'
offers "claude-switch -" > "$T/got"
check "an option: -h and --help" 'expect -h --help | diff - "$T/got"'
offers "claude-switch login " > "$T/got"
check "login: the accounts only" 'expect "${ACCOUNTS[@]}" | diff - "$T/got"'
offers "claude-switch rename wo" > "$T/got"
check "rename: an account" 'expect work | diff - "$T/got"'
offers "claude-switch refresh " > "$T/got"
check "refresh: --force" 'expect --force | diff - "$T/got"'
offers "claude-switch list " > "$T/got"
check "list: nothing" '[ ! -s "$T/got" ]'
offers "claude-switch rename work " > "$T/got"
check "rename: nothing for the new name" '[ ! -s "$T/got" ]'
offers "claude-switch work " > "$T/got"
check "a switch: nothing after the name" '[ ! -s "$T/got" ]'
offers "claude-switch lucas@example.o" > "$T/got"
check "past an @: from the @ on, readline keeps what is before" 'expect @example.org | diff - "$T/got"'
offers "claude-switch my" > "$T/got"
check "a space: escaped" 'expect "$LAPTOP" | diff - "$T/got"'
offers "claude-switch my\\ l" > "$T/got"
check "an escaped space: read as a space" 'expect "$LAPTOP" | diff - "$T/got"'
offers 'claude-switch "my l' > "$T/got"
check "in quotes: as it is, readline closes them" 'expect "my laptop" | diff - "$T/got"'
offers 'claude-switch $' > "$T/got"
check "a name a shell would run: escaped" 'expect "$RUN" | diff - "$T/got"'
check "nothing a name holds is run" '[ ! -e "$T/PWNED" ]'
check "a Tab changes nothing" '[ "$(cd "$STORE" && ls -A && md5sum ./*.json)" = "$BEFORE" ]'

got=$(typed $'claude-switch lu\tc\t')
check "readline: an email, past its @" '[ "$got" = "[lucas@example.com]" ]'
got=$(typed $'claude-switch my\t')
check "readline: a name with a space" '[ "$got" = "[my laptop]" ]'
got=$(typed $'claude-switch "my l\t')
check "readline: a name in quotes" '[ "$got" = "[my laptop]" ]'
got=$(typed $'claude-switch log\two\t')
check "readline: a command, then an account" '[ "$got" = "[login][work]" ]'
got=$(typed $'claude-switch refresh --\t')
check "readline: an option" '[ "$got" = "[refresh][--force]" ]'
got=$(typed $'claude-switch $\t')
check "readline: a name a shell would run stays a name" '[ "$got" = "[\$(touch PWNED)]" ]'
check "readline: nothing a name holds is run" '[ ! -e "$T/PWNED" ]'

mv "$STORE" "$STORE.away"
offers "claude-switch " -O failglob > "$T/got"
check "no account yet: the commands" 'expect "${COMMANDS[@]}" | diff - "$T/got"'
check "no account yet: no error, even with failglob" '[ ! -s "$T/err" ]'
mv "$STORE.away" "$STORE"

printf '%d passed, %d failed\n' "$passes" "$fails"
[ "$fails" -eq 0 ]
