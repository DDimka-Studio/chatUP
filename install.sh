#!/bin/sh
# chatup installer for Linux and macOS.
# Copyright (C) 2026  DDimka-Studio
# License: GPL-3.0-or-later, see the LICENSE file.
#
#   sh install.sh               installs chatup, then tells you what to add to your shell file
#   . ./install.sh              the same, and chatup also starts working in THIS terminal right away
#   sh install.sh --uninstall   takes it off again
#
# It only uses tools every system already has: no pip, no sudo, and nothing
# outside your home folder. Want another place than ~/.local/bin? Set
# CHATUP_BIN_DIR=/some/folder first.
#
# It is safe to "source": in that case it never calls `exit` (that would close
# your terminal!) and never uses `set -e`. When it's done it also cleans its own
# helper names out of your shell, so nothing is left behind.

_cu_is_sourced() {
    if [ -n "${BASH_VERSION:-}" ]; then
        [ "${BASH_SOURCE:-$0}" != "$0" ]
    elif [ -n "${ZSH_EVAL_CONTEXT:-}" ]; then
        case "$ZSH_EVAL_CONTEXT" in *:file*) return 0 ;; esac
        return 1
    else
        return 1
    fi
}

_cu_self_dir() {
    if [ -n "${BASH_VERSION:-}" ]; then
        _cu_p="${BASH_SOURCE:-$0}"
    elif [ -n "${ZSH_VERSION:-}" ]; then
        eval '_cu_p=${(%):-%x}'
    else
        _cu_p="$0"
    fi
    case "$_cu_p" in
        */*) (cd "${_cu_p%/*}" 2>/dev/null && pwd) ;;
        *) pwd ;;
    esac
}

_cu_install() {
    _cu_src_dir=$(_cu_self_dir)
    _cu_src="$_cu_src_dir/chatup.py"
    _cu_bin="${CHATUP_BIN_DIR:-$HOME/.local/bin}"
    _cu_target="$_cu_bin/chatup"

    if [ "${1:-}" = "--uninstall" ]; then
        if [ -f "$_cu_target" ]; then
            rm -f "$_cu_target" && echo "Removed $_cu_target"
        else
            echo "Nothing to remove: $_cu_target does not exist."
        fi
        echo "If you added a PATH line to your shell file, remove it by hand"
        echo "(look for the comment 'added by the chatup installer')."
        return 0
    fi

    if [ ! -f "$_cu_src" ]; then
        echo "I can't find chatup.py next to the installer (looked in $_cu_src_dir)."
        return 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "chatup needs Python 3.8 or newer, but python3 was not found."
        echo "Install it with your system's package manager, then run this again."
        return 1
    fi
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
        echo "Your python3 is older than 3.8. Please install a newer one and run this again."
        return 1
    fi

    mkdir -p "$_cu_bin" || return 1
    cp "$_cu_src" "$_cu_target" && chmod 755 "$_cu_target" || return 1
    echo "Installed: $_cu_target"
    "$_cu_target" --version 2>/dev/null

    case ":$PATH:" in
        *":$_cu_bin:"*)
            echo "$_cu_bin is already on your PATH. All set: try   chatup --help"
            return 0
            ;;
    esac

    # Write the folder the way people do in shell files: with $HOME in front when possible.
    case "$_cu_bin" in
        "$HOME"/*)
            _cu_rest=${_cu_bin#"$HOME"/}
            _cu_shown="\$HOME/$_cu_rest"
            ;;
        *) _cu_shown="$_cu_bin" ;;
    esac

    case "${SHELL:-}" in
        */zsh) _cu_rc="$HOME/.zshrc" ;;
        */bash)
            if [ "$(uname -s)" = "Darwin" ]; then _cu_rc="$HOME/.bash_profile"; else _cu_rc="$HOME/.bashrc"; fi
            ;;
        */fish) _cu_rc="$HOME/.config/fish/config.fish" ;;
        *) _cu_rc="$HOME/.profile" ;;
    esac
    case "${SHELL:-}" in
        */fish) _cu_line="fish_add_path \"$_cu_shown\"" ;;
        *) _cu_line="export PATH=\"$_cu_shown:\$PATH\"" ;;
    esac

    echo ""
    echo "$_cu_bin is not on your PATH yet."
    if _cu_is_sourced; then
        PATH="$_cu_bin:$PATH"
        export PATH
        echo "Done for THIS terminal: chatup works here right now."
    else
        echo "To use chatup in THIS terminal right now, run:"
        echo "    $_cu_line"
        echo "(or run the installer as   . ./install.sh   to do it automatically)"
    fi
    echo ""
    echo "To keep it in every new terminal, this line has to be in $_cu_rc:"
    echo "    $_cu_line"

    if grep -qF "added by the chatup installer" "$_cu_rc" 2>/dev/null; then
        echo "It looks like that file already has a chatup line. Open a new terminal."
        return 0
    fi

    _cu_ans=""
    if [ -t 1 ] && (: < /dev/tty) 2>/dev/null; then
        printf 'Add it to %s for you? [y/N] ' "$_cu_rc"
        read -r _cu_ans < /dev/tty
    fi
    case "$_cu_ans" in
        y|Y|yes|YES)
            mkdir -p "$(dirname "$_cu_rc")" || return 1
            printf '\n# added by the chatup installer\n%s\n' "$_cu_line" >> "$_cu_rc" || return 1
            echo "Added to $_cu_rc. New terminals will find chatup."
            ;;
        *)
            echo "Okay, I did not touch $_cu_rc. Add the line above yourself when you like."
            ;;
    esac
    return 0
}

_cu_install "$@"
_cu_status=$?
if _cu_is_sourced; then _cu_sourced=1; else _cu_sourced=0; fi
unset -f _cu_install _cu_is_sourced _cu_self_dir 2>/dev/null
unset _cu_src_dir _cu_src _cu_bin _cu_target _cu_rest _cu_shown _cu_rc _cu_line _cu_ans _cu_p 2>/dev/null
if [ "$_cu_sourced" = 1 ]; then
    eval "unset _cu_status _cu_sourced; return $_cu_status"
else
    exit "$_cu_status"
fi
