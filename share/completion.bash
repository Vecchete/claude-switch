# Bash completion of claude-switch: its commands, then the accounts in ~/.claude-accounts by name or email.
# install.sh puts it where bash-completion loads it at the first Tab. It reads the account files with jq
# and never runs claude-switch, so a Tab changes nothing.
#
# The words are matched here and not with compgen -W, which runs what its words hold: an account name is
# free text.

# Splits the line up to the cursor the way the shell will read it, expanding nothing. WORDS gets the words
# without their quotes, the last one is completed. QUOTE is the quote left open there. BASE is the start of
# that word readline leaves as it is, so the replies leave it out: up to the open quote, or up to the last
# word break outside quotes. Readline replaces from a $ or an @ on, the @ of an email for instance, and
# from after the other breaks.
_claude_switch_words() {
    local line=${COMP_LINE:0:COMP_POINT} char index word="" started="" broken=""
    WORDS=() QUOTE="" BASE=""
    for ((index = 0; index < ${#line}; index++)); do
        char=${line:index:1}
        if [ -n "$QUOTE" ]; then
            if [ "$char" = "$QUOTE" ]; then
                QUOTE=""
            else
                word+=$char
            fi
        elif [ "$char" = '\' ]; then
            index=$((index + 1))
            word+=${line:index:1} started=yes
        elif [[ $char == [[:blank:]] ]]; then
            [ -z "$started" ] || WORDS+=("$word")
            word="" started="" broken=""
        elif [[ $char == [\"\'] ]]; then
            QUOTE=$char BASE=$word started=yes
        else
            if [[ $COMP_WORDBREAKS == *"$char"* ]]; then
                case $char in
                    [\$@]) broken=$word ;;
                    *) broken=$word$char ;;
                esac
            fi
            word+=$char started=yes
        fi
    done
    [ -n "$QUOTE" ] || BASE=$broken
    WORDS+=("$word")
}

# Each name and email of the accounts, one per line.
_claude_switch_accounts() {
    local files
    mapfile -t files < <(compgen -G "$HOME/.claude-accounts/*.json")
    [ "${#files[@]}" -eq 0 ] || jq -r '.name, .oauthAccount.emailAddress | strings | select(length > 0)' \
        "${files[@]}" 2>/dev/null
}

_claude_switch() {
    local WORDS QUOTE BASE candidate
    _claude_switch_words
    COMPREPLY=()
    while IFS= read -r candidate; do
        [[ $candidate == "${WORDS[-1]}"* ]] || continue
        candidate=${candidate#"$BASE"}
        # In quotes readline takes it as it is and closes them, outside it must be escaped.
        if [ -n "$QUOTE" ]; then
            COMPREPLY+=("$candidate")
        else
            COMPREPLY+=("$(printf '%q' "$candidate")")
        fi
    done < <(
        case ${#WORDS[@]}:${WORDS[1]-} in
            2:-*) printf '%s\n' -h --help ;;
            2:*)
                printf '%s\n' list login rename refresh help
                _claude_switch_accounts
                ;;
            3:login | 3:rename) _claude_switch_accounts ;;
            3:refresh) printf '%s\n' --force ;;
        esac
    )
}
complete -F _claude_switch claude-switch
