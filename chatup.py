#!/usr/bin/env python3
# chatup - turns what an AI wrote in a chat into real files on your computer.
# Copyright (C) 2026  DDimka-Studio
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""chatup - turns what an AI wrote in a chat into real files on your computer.

The idea: the AI gives you ONE command. You paste it into a terminal that is
already inside your project folder, and chatup puts every file where it belongs.

    chatup <<'CHATUP_EOF'
    @@@ chatup 1
    @@@ file src/app.py | Main entry point
    print("hello")
    @@@ end
    CHATUP_EOF

What the AI can write between the markers:
    @@@ file <path> | <description>     make a file, or replace one   (up to "@@@ end")
    @@@ append <path> | <description>   add text to the end of a file (up to "@@@ end")
    @@@ run [timeout=N] [interactive|batch] <command>
                                        run one program (no shell involved)
    @@@ script [timeout=N] [batch] <interpreter> | <description>
                                        run a throwaway script, deleted afterwards (up to "@@@ end")
    @@@ ask NAME | <question> | <default>
                                        ask YOU something and use the answer as {{NAME}}

If a line of file content has to start with "@@@", write one extra "@" in front.
Ready-made placeholders: {{chatup.year}}, {{chatup.date}}, {{chatup.dir}}.

Your own rules for a project (optional file .chatup.conf in the current folder).
It can only make chatup stricter, never looser:
    permission = request            ask before EVERY step, not just once for the whole plan
    permission = deny               never change anything in this folder
    allow = src/**, README.md       only these paths may be written
    deny = secrets/**, *.pem        these paths may never be written
    no-allow-command = curl, wget   programs that may never run here
    venv = penv                     name of your Python virtual environment

How we keep you safe (this is the whole point of the tool):
  * Nothing happens until YOU confirm. Your answer is read from the real
    terminal, not from the pasted text, so the pasted command can't say "yes"
    for you. There is no flag that skips this, on purpose.
  * Plain plans (new files, or overwrites that keep a backup) need a "y".
  * Riskier plans (running anything, appending to files, overwriting a build
    or startup file) need a whole phrase typed by hand.
  * Privileged commands (sudo, package managers, systemctl...) need a second
    phrase right before each one runs.
  * Dangerous commands (dd, disk tools, shutdown, nohup...) aren't forbidden,
    but need a third phrase plus the program's name, typed right before each one.
    The only things that stay refused are programs YOU banned in .chatup.conf.
  * Interactive programs talk to your keyboard directly. The AI never sees or
    answers their questions. Scripts are interactive by default (they often ask
    things); 'batch' runs one unattended, with no keyboard.
  * Files stay inside the current folder, and programs run without a shell.
  * Our list of blocked commands is only a best effort. The real protection is
    the plan you read and the phrases you type. So please read the plan.

Needs Python 3.8+ and nothing else (standard library only).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

__version__ = "0.1.0"
MANIFEST_VERSION = "1"
CONFIRM_PHRASE = "I UNDERSTAND AND ACCEPT THE RISK"
PRIV_PHRASE = "I ALLOW THIS PRIVILEGED COMMAND"
DANGER_PHRASE = "I KNOW THIS CAN DESTROY DATA"
CONFIG_NAME = ".chatup.conf"
INTERACTIVE_TIMEOUT_S = 600
SCRIPT_PREVIEW_LINES = 60

# Size limits use plain decimal units: 1 MB = 1,000,000 bytes.
MAX_MANIFEST_BYTES = 20_000_000
MAX_FILE_BYTES = 2_000_000
MAX_ACTIONS = 500
MAX_PATH_LEN = 240
DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 600
REPORT_TAIL_LINES = 40
REPORT_LINE_CHARS = 300
TAIL_READ_BYTES = 200_000  # we only ever read this much of a program's output back
DESC_MAX_CHARS = 100

# Files that run code later on their own (build, install, new terminal, CI, git hooks...).
# Creating one is fine but gets a [!]; overwriting an existing one needs the typed phrase.
SENSITIVE_NAMES = {
    "makefile", "gnumakefile", "justfile", "rakefile", "gemfile", "dockerfile",
    "docker-compose.yml", "docker-compose.yaml", "cmakelists.txt", "build.rs",
    "build.gradle", "build.gradle.kts", "pom.xml", "setup.py", "setup.cfg",
    "pyproject.toml", "package.json", "conftest.py", "tox.ini", "noxfile.py",
    "sitecustomize.py", "usercustomize.py", ".envrc", ".bashrc", ".zshrc",
    ".profile", ".bash_profile", ".npmrc", ".pre-commit-config.yaml",
}
SENSITIVE_SUFFIXES = (".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1", ".pth", ".mk")
SENSITIVE_PREFIXES = (".github/workflows/", ".githooks/", ".husky/", ".vscode/")

# Dangerous: these can wipe data, shut the machine down, or keep running in the background.
# Allowed, but only after you type a special phrase AND the program's name, every single time.
DANGEROUS_PROGRAMS = {
    "fdisk", "parted", "format", "diskpart", "dd", "wipefs", "shred", "shutdown",
    "reboot", "halt", "poweroff", "init", "eval", "nohup", "setsid", "screen",
    "tmux", "at",
}
# Never allowed: chatup starting chatup would only be confusing.
NEVER_PROGRAMS = {"chatup"}
# These need their own typed confirmation, one per command.
PRIVILEGED = {
    "sudo", "su", "doas", "pkexec", "runas",
    "apt", "apt-get", "aptitude", "dpkg", "pacman", "yay", "paru", "emerge", "dnf",
    "yum", "zypper", "rpm", "apk", "snap", "flatpak", "xbps-install", "nix-env",
    "systemctl", "service", "launchctl", "mount", "umount", "chown", "chgrp",
    "crontab", "useradd", "userdel", "usermod", "passwd", "visudo", "iptables",
    "ufw", "netsh", "reg", "regedit", "modprobe", "insmod", "sysctl",
    "update-grub", "grub-install",
}
DELETE_PROGRAMS = {"rm", "rmdir", "rd", "del", "erase"}
WRAPPERS = {"sudo", "doas", "pkexec"}  # programs that just launch another program
WRAPPER_VALUE_FLAGS = {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-r", "-t"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "cmd", "powershell", "pwsh"}
SCRIPT_EXT = {
    "python": ".py", "python3": ".py", "py": ".py", "node": ".js", "bash": ".sh",
    "sh": ".sh", "zsh": ".sh", "ruby": ".rb", "perl": ".pl", "pwsh": ".ps1",
    "powershell": ".ps1",
}
PLACEHOLDER_RE = re.compile(r"\{\{(chatup\.(?:year|date|dir)|[A-Za-z_][A-Za-z0-9_]*)\}\}")
ASK_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VENV_CANDIDATES = (".venv", "venv", "env", "penv")  # we check these names in this order
SUDO_INSIDE_RE = re.compile(r"\b(?:sudo|doas|pkexec)\b\s+\S")
NO_TTY_RE = re.compile(r"a terminal is required to read the password|no tty present and no askpass|"
                       r"a password is required|you must have a tty", re.IGNORECASE)
CODE_SUFFIXES = (".sh", ".bash", ".zsh", ".py", ".rb", ".pl", ".js", ".ps1")
PYTHON_NAME_RE = re.compile(r"^(python(\d+(\.\d+)*)?|py)$")
PIP_NAME_RE = re.compile(r"^pip(\d+(\.\d+)*)?$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# Programs whose arguments (apart from flags) are all file paths. Without sudo, every path must stay inside the project.
PATH_ARG_PROGRAMS = {
    "rm", "rmdir", "rd", "del", "erase", "mv", "move", "cp", "copy", "ln",
    "chmod", "touch", "mkdir",
}
SHELL_OPERATORS = {"|", "||", "&", "&&", ";", ">", ">>", "<", "2>", "2>&1"}
WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{n}" for n in range(1, 10)} | {f"LPT{n}" for n in range(1, 10)}

SECRET_ENV_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.IGNORECASE)
KV_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\b(\s*[=:]\s*)([^\s,;]+)"
)
TOKEN_RES = [
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{16,}"),
]


class ChatupError(Exception):
    """Something we can explain in plain words (bad manifest, wrong folder...), shown without a scary traceback."""


@dataclass
class Action:
    kind: str                      # "file" | "append" | "run" | "script" | "ask"
    line_no: int
    path: str = ""                 # (for "ask": the placeholder name)
    desc: str = ""                 # (for "ask": the question)
    content: str = ""              # (for "ask": the default answer)
    command: str = ""
    interpreter: str = ""          # only for "script"
    ext: str = ""                  # only for "script": file ending of the temporary file
    argv: list = field(default_factory=list)
    timeout: int = DEFAULT_TIMEOUT_S
    interactive: bool = False
    batch: bool = False            # the AI said 'run this without me': no keyboard
    timeout_given: bool = False
    privileged: bool = False
    danger_name: str = ""         # filled in when the program is one of the dangerous ones
    priv_reason: str = ""         # why we treat this step as privileged even though the command itself isn't sudo
    exists: bool = False
    sensitive: bool = False
    risky: bool = False
    label: str = ""


@dataclass
class Config:
    """Your project's own rules from .chatup.conf. They can only make things stricter."""
    permission: str = "confirm"    # confirm (default) | request | deny
    allow: list = field(default_factory=list)
    deny: list = field(default_factory=list)
    no_allow_command: set = field(default_factory=set)
    venv: str = ""            # folder name of this project's virtual environment
    notes: list = field(default_factory=list)
    found: bool = False


def load_config(root: Path) -> Config:
    cfg = Config()
    path = root / CONFIG_NAME
    if not path.is_file():
        return cfg
    cfg.found = True
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ChatupError(f"I can't read {CONFIG_NAME}: {exc}") from None
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ChatupError(f"{CONFIG_NAME} line {no}: expected 'setting = value'")
        key = key.strip().lower().replace("_", "-")
        value = value.strip()
        items = [v.strip() for v in value.split(",") if v.strip()]
        if key == "permission":
            mode = value.lower()
            if mode in ("confirm", "request", "deny"):
                cfg.permission = mode
            elif mode == "allow":
                cfg.notes.append(f"{CONFIG_NAME}: 'permission = allow' is not available yet; asking as usual")
            else:
                raise ChatupError(f"{CONFIG_NAME} line {no}: permission must be request or deny")
        elif key == "allow":
            cfg.allow += items
        elif key == "deny":
            cfg.deny += items
        elif key == "venv":
            if "/" in value or "\\" in value or not value:
                raise ChatupError(f"{CONFIG_NAME} line {no}: venv must be a plain folder name like .venv or penv")
            try:
                cfg.venv = normalize_rel_path(value)
            except ChatupError as exc:
                raise ChatupError(f"{CONFIG_NAME} line {no}: {exc}") from None
        elif key == "no-allow-command":
            cfg.no_allow_command |= {i.lower() for i in items}
        else:
            cfg.notes.append(f"{CONFIG_NAME} line {no}: unknown setting '{clean_text(key, 30)}' ignored")
    return cfg


def path_matches(rel: str, patterns: list) -> bool:
    """Does this path match one of the allow/deny patterns? ('*' also matches across folders.)"""
    low = rel.lower()
    for pat in patterns:
        p = pat.strip().replace("\\", "/").lower()
        if p.startswith("./"):
            p = p[2:]
        if not p:
            continue
        if p.endswith("/"):
            if low.startswith(p):
                return True
        elif fnmatch.fnmatchcase(low, p) or low.startswith(p + "/"):
            return True
    return False


def say(text: str = "") -> None:
    print(text, flush=True)


def clean_text(text: str, limit: int = DESC_MAX_CHARS) -> str:
    """Throw away control and invisible characters.

    Why: descriptions come from the AI and get printed in the plan. Without
    this, a hidden terminal escape sequence could repaint the screen and make
    the plan look different from what will really happen.
    """
    return "".join(ch for ch in text if ch.isprintable())[:limit]


# ---------------------------------------------------------------- parsing

def split_chain(command: str) -> list:
    """Turn 'a && b ; c' into separate steps.

    Why: AIs love chaining commands this way. We already stop at the first
    failure, so running the pieces one by one means the same thing, and you see
    every step in the plan. If the line can't be parsed we hand it back as it
    is, and check_command explains what's wrong.
    """
    try:
        tokens = shlex.split(command, posix=(os.name != "nt"))
    except ValueError:
        return [command]
    if not any(t in ("&&", ";") for t in tokens):
        return [command]
    groups: list = []
    cur: list = []
    for tok in tokens:
        if tok in ("&&", ";"):
            if cur:
                groups.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        groups.append(cur)
    join = subprocess.list2cmdline if os.name == "nt" else shlex.join
    return [join(g) for g in groups]


def take_options(rest: str) -> tuple:
    """Peel 'timeout=N', 'interactive' and 'batch' off the start of a directive."""
    timeout = None
    interactive = False
    batch = False
    while True:
        m = re.match(r"^timeout=(\d+)\s+(.*)$", rest)
        if m:
            timeout = min(max(int(m.group(1)), 1), MAX_TIMEOUT_S)
            rest = m.group(2)
            continue
        m = re.match(r"^interactive\s+(.*)$", rest)
        if m:
            interactive = True
            rest = m.group(1)
            continue
        m = re.match(r"^batch\s+(.*)$", rest)
        if m:
            batch = True
            rest = m.group(1)
            continue
        return timeout, interactive, batch, rest.strip()


def parse_manifest(text: str) -> tuple:
    """Read the manifest. Returns (actions, notes): small slips become friendly notes, not errors."""
    notes: list = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the final newline ends the last line, it isn't an extra empty one
    actions: list = []
    stray = 0

    def read_block(start: int, name: str, header_no: int) -> tuple:
        """Gather the file's lines until '@@@ end'. Returns (text, index_of_next_line)."""
        body: list = []
        i = start
        closed = False
        while i < len(lines):
            cur = lines[i]
            if cur.rstrip() == "@@@ end":
                closed = True
                i += 1
                break
            if cur.startswith("@@@@"):
                cur = cur[1:]  # a content line that only looks like a directive: drop the extra @
            elif cur.startswith("@@@ "):
                # A new directive starts here, so the AI forgot "@@@ end". No problem, we close the block.
                notes.append(f"line {header_no}: '{clean_text(name, 60)}' had no '@@@ end'; I closed it at line {i + 1}")
                closed = True
                break  # leave this line alone, it belongs to the next block
            body.append(cur)
            i += 1
        if not closed:
            notes.append(f"line {header_no}: '{clean_text(name, 60)}' had no '@@@ end'; I used everything up to the end. "
                         "If the paste was cut off, it may be incomplete")
        return "\n".join(body) + ("\n" if body else ""), i

    i = 0
    while i < len(lines):
        line, line_no = lines[i], i + 1
        if not line.strip():
            i += 1
            continue
        if not line.startswith("@@@ "):
            stray += 1  # chat chatter, ``` fences, "Here you go:" and so on: we just skip it
            i += 1
            continue
        verb, _, rest = line[4:].strip().partition(" ")
        rest = rest.strip()
        i += 1
        if verb == "chatup":  # the header line; we only care if the version is one we don't know
            if rest and rest != MANIFEST_VERSION:
                notes.append(f"line {line_no}: manifest version '{clean_text(rest, 20)}' is unknown to me; trying anyway")
        elif verb in ("file", "append"):
            path, _, desc = rest.partition("|")
            content, i = read_block(i, path.strip(), line_no)
            actions.append(Action(verb, line_no, path=path.strip(), desc=clean_text(desc.strip()), content=content))
        elif verb == "script":
            head, _, desc = rest.partition("|")
            timeout, interactive, batch, interp = take_options(head.strip())
            if interactive and batch:
                raise ChatupError(f"line {line_no}: 'interactive' and 'batch' cannot be combined")
            if not interp or " " in interp:
                raise ChatupError(f"line {line_no}: '@@@ script' needs just an interpreter name, like python3")
            content, i = read_block(i, interp, line_no)
            interactive = interactive or (os.name == "posix" and not batch)  # scripts often ask questions, so they get your keyboard
            actions.append(Action("script", line_no, desc=clean_text(desc.strip()), content=content, interpreter=interp,
                                  interactive=interactive, batch=batch, timeout_given=timeout is not None,
                                  timeout=timeout or (INTERACTIVE_TIMEOUT_S if interactive else DEFAULT_TIMEOUT_S)))
        elif verb == "run":
            timeout, interactive, batch, command = take_options(rest)
            if interactive and batch:
                raise ChatupError(f"line {line_no}: 'interactive' and 'batch' cannot be combined")
            parts = split_chain(command)
            if len(parts) > 1:
                notes.append(f"line {line_no}: split a chained command into {len(parts)} separate steps")
            for part in parts:
                actions.append(Action("run", line_no, command=part, interactive=interactive, batch=batch,
                                      timeout_given=timeout is not None,
                                      timeout=timeout or (INTERACTIVE_TIMEOUT_S if interactive else DEFAULT_TIMEOUT_S)))
        elif verb == "ask":
            fields = [p.strip() for p in rest.split("|")]
            if not ASK_NAME_RE.match(fields[0]):
                raise ChatupError(f"line {line_no}: '@@@ ask' needs a name like PROJECT_NAME")
            question = clean_text(fields[1], 160) if len(fields) > 1 and fields[1] else f"Value for {fields[0]}"
            default = clean_text(fields[2], 200) if len(fields) > 2 else ""
            actions.append(Action("ask", line_no, path=fields[0], desc=question, content=default))
        else:
            notes.append(f"line {line_no}: ignored unknown directive '{clean_text(verb, 20)}'")
        if len(actions) > MAX_ACTIONS:
            raise ChatupError(f"too many steps (limit {MAX_ACTIONS}); ask the chat to split the project into several commands")
    if stray:
        notes.append(f"ignored {stray} line(s) outside the file blocks (probably chat text)")
    if not actions:
        raise ChatupError("I found no '@@@ file' blocks in what was pasted")
    return actions, notes


# ---------------- Python virtual environments
def venv_python_path(name: str) -> str:
    return f"{name}/Scripts/python.exe" if os.name == "nt" else f"{name}/bin/python"


def join_command(argv: list) -> str:
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def pick_venv(root: Path, cfg: Config) -> str:
    """Which virtual environment to use: your setting first, then one that already exists, otherwise a fresh .venv."""
    if cfg.venv:
        return cfg.venv
    for name in VENV_CANDIDATES:
        if (root / name / "pyvenv.cfg").is_file():
            return name
    return VENV_CANDIDATES[0]


def adapt_python(actions: list, root: Path, notes: list, cfg: Config) -> None:
    """Make pip and python use this project's own virtual environment.

    Why: modern Python (PEP 668) refuses 'pip install' into the system on
    purpose, and AIs often don't know that. Instead of letting it fail, we create
    the environment if it's missing and use it. All of this is rewritten BEFORE
    the plan is shown, so you see the real commands. We leave alone anything
    that names a path (.venv/bin/pip) and anything after a 'cd' into a subfolder.
    """
    venv = pick_venv(root, cfg)
    venv_ready = (root / venv / "pyvenv.cfg").is_file()
    in_subdir = False
    noted_python = False
    out: list = []
    for a in actions:
        if a.kind == "script":
            name = program_name(a.interpreter)
            if venv_ready and not in_subdir and PYTHON_NAME_RE.match(name) and "/" not in a.interpreter and "\\" not in a.interpreter:
                a.interpreter = venv_python_path(venv)
                notes.append(f"line {a.line_no}: the script uses this project's {venv} Python")
            out.append(a)
            continue
        if a.kind != "run":
            out.append(a)
            continue
        try:
            argv = shlex.split(a.command, posix=(os.name != "nt"))
        except ValueError:
            out.append(a)
            continue
        if not argv:
            out.append(a)
            continue
        if argv[0] == "cd":
            if len(argv) > 1 and argv[1] not in (".", "./"):
                in_subdir = True
            out.append(a)
            continue
        if in_subdir or "/" in argv[0] or "\\" in argv[0]:
            out.append(a)
            continue
        prog = program_name(argv[0])
        is_python = bool(PYTHON_NAME_RE.match(prog))
        is_pip = bool(PIP_NAME_RE.match(prog)) or (is_python and argv[1:3] == ["-m", "pip"])
        if is_python and argv[1:3] == ["-m", "venv"]:
            target = argv[3] if len(argv) > 3 else ""
            if target == venv:
                venv_ready = True
            elif target in VENV_CANDIDATES and not cfg.venv and not venv_ready:
                venv, venv_ready = target, True  # the AI picked a normal name on its own, so we follow it
            out.append(a)
            continue
        if not (is_pip or (is_python and venv_ready)):
            out.append(a)
            continue
        if is_pip:
            rest = argv[1:] if PIP_NAME_RE.match(prog) else argv[3:]
            dropped = [x for x in rest if x in ("--break-system-packages", "--user")]
            rest = [x for x in rest if x not in dropped]
            if not venv_ready:
                maker = f"{'python' if os.name == 'nt' else 'python3'} -m venv {shell_quote(venv)}"
                out.append(replace(a, command=maker, interactive=False, timeout=DEFAULT_TIMEOUT_S))
                venv_ready = True
                notes.append(f"line {a.line_no}: modern Python refuses 'pip install' into the system, so I create "
                             f"this project's own {venv} first and install there")
            if dropped:
                notes.append(f"line {a.line_no}: dropped {' '.join(dropped)} (not needed, and unsafe, inside {venv})")
            a.command = join_command([venv_python_path(venv), "-m", "pip"] + rest)
            notes.append(f"line {a.line_no}: pip runs from {venv}: {clean_text(a.command, 80)}")
        else:
            a.command = join_command([venv_python_path(venv)] + argv[1:])
            if not noted_python:
                notes.append(f"line {a.line_no}: python runs from this project's {venv}, where the packages are")
                noted_python = True
        out.append(a)
    actions[:] = out


# ---------------- placeholders
def shell_quote(value: str) -> str:
    return subprocess.list2cmdline([value]) if os.name == "nt" else shlex.quote(value)


def resolve_asks(actions: list, root: Path, dry_run: bool) -> dict:
    """Ask you every '@@@ ask' question (before the plan is shown), then drop those steps."""
    now = dt.datetime.now()
    values = {"chatup.year": str(now.year), "chatup.date": now.strftime("%Y-%m-%d"), "chatup.dir": root.name}
    def expand(text: str) -> str:
        return PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)

    for a in [x for x in actions if x.kind == "ask"]:
        a.desc = clean_text(expand(a.desc), 160)
        a.content = clean_text(expand(a.content), 200)  # a default answer may use the ready-made placeholders, like {{chatup.date}}
        if dry_run:
            values[a.path] = a.content or f"<{a.path}>"
            continue
        if not sys.stdout.isatty():
            raise ChatupError("I need to ask you a question, but the output is redirected")
        suffix = f" [{a.content}]" if a.content else ""
        answer = read_terminal_line(f"{a.desc}{suffix}: ")
        if answer is None:
            raise ChatupError("I couldn't reach a terminal to ask you a question")
        answer = answer.strip() or a.content
        if not answer or len(answer) > 200 or not all(ch.isprintable() for ch in answer):
            raise ChatupError(f"'{a.path}' needs a short plain-text answer")
        values[a.path] = answer
    actions[:] = [x for x in actions if x.kind != "ask"]
    return values


def apply_placeholders(actions: list, values: dict) -> None:
    """Fill in {{NAME}} only for names we know, so files that use {{...}} for something else stay untouched."""
    def sub(text: str, quote: bool = False) -> str:
        def one(m):
            value = values.get(m.group(1))
            if value is None:
                return m.group(0)
            return shell_quote(value) if quote else value
        return PLACEHOLDER_RE.sub(one, text)

    for a in actions:
        a.path = sub(a.path)
        a.desc = clean_text(sub(a.desc))
        a.content = sub(a.content)
        a.interpreter = sub(a.interpreter)
        a.command = sub(a.command, quote=True)  # quoted, so an answer with spaces stays ONE argument


# ---------------------------------------------------------------- validation

def normalize_rel_path(raw: str) -> str:
    """Turn a path into a clean relative one with forward slashes, or complain if it's not allowed."""
    p = raw.strip()
    if not p:
        raise ChatupError("empty path")
    if len(p) > MAX_PATH_LEN:
        raise ChatupError("path is too long")
    if not all(ch.isprintable() for ch in p):
        raise ChatupError("path contains control characters")
    p = p.replace("\\", "/")  # AIs often write Windows-style paths, so we accept those too
    if p.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", p):
        raise ChatupError(f"absolute paths are not allowed: {p}")
    parts = []
    for part in p.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ChatupError(f"'..' is not allowed in paths: {p}")
        if part.endswith((".", " ")):
            raise ChatupError(f"path part ends with a dot or space (Windows rewrites it): {p}")
        if ":" in part:
            raise ChatupError(f"':' is not allowed in paths: {p}")
        if part.split(".")[0].upper() in WIN_RESERVED:
            raise ChatupError(f"reserved device name in path: {p}")
        if part.lower() in (".git", ".chatup", CONFIG_NAME):
            raise ChatupError(f"writing into '{part}' is not allowed: {p}")
        parts.append(part)
    if not parts:
        raise ChatupError(f"path points to the project folder itself: {p}")
    return "/".join(parts)


def resolve_inside(root: Path, rel: str) -> Path:
    """Find the real place on disk for a relative path, and refuse tricks that escape the project through symlinks."""
    target = root.joinpath(*rel.split("/"))
    real_root = Path(os.path.realpath(root))
    real_parent = Path(os.path.realpath(target.parent))
    try:
        real_parent.relative_to(real_root)
    except ValueError:
        raise ChatupError(f"path leaves the project folder through a symlink: {rel}") from None
    if target.is_symlink():
        raise ChatupError(f"refusing to write through a symlink: {rel}")
    return target


def is_sensitive(rel: str) -> bool:
    low = rel.lower()
    name = low.rsplit("/", 1)[-1]
    return name in SENSITIVE_NAMES or name.endswith(SENSITIVE_SUFFIXES) or low.startswith(SENSITIVE_PREFIXES)


def shell_runs_inline_code(prog: str, args: list) -> bool:
    for arg in args:
        if not arg.startswith(("-", "/")):
            break  # the first non-flag word is the script; any flags after it belong to the script
        low = arg.lower()
        if low in {"-c", "/c", "/k", "-command", "-encodedcommand", "-ec"}:
            return True
        if prog in {"powershell", "pwsh"} and low == "-e":
            return True
        if prog not in {"cmd", "powershell", "pwsh"} and re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", arg):
            return True  # flags glued together, like -lc or -ec
    return False


def program_name(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".com"):
        if name.endswith(ext):
            name = name[: -len(ext)]
    return name


def wrapped_program(args: list) -> int:
    """Where in args the real program is that a wrapper like sudo will start (or -1)."""
    skip = False
    for idx, arg in enumerate(args):
        if skip:
            skip = False
        elif arg.startswith("-"):
            skip = arg in WRAPPER_VALUE_FLAGS
        elif "=" not in arg:
            return idx
    return -1


def check_command(command: str, cfg: Config) -> tuple:
    """Check one 'run' line. Returns (argv, privileged, danger_name), or complains with ChatupError."""
    if not command.strip():
        raise ChatupError("empty command")
    if not all(ch.isprintable() for ch in command):
        raise ChatupError("command contains control characters")
    try:
        argv = shlex.split(command, posix=(os.name != "nt"))
    except ValueError as exc:
        raise ChatupError(f"cannot parse command: {exc}") from None
    if os.name == "nt":
        argv = [a[1:-1] if len(a) >= 2 and a[0] == a[-1] and a[0] in "\"'" else a for a in argv]
    if not argv:
        raise ChatupError("empty command")
    if any(tok in SHELL_OPERATORS for tok in argv):
        raise ChatupError("pipes and redirects (| > <) are not supported because commands run without a shell: "
                          "put them in a script file and run that")
    if argv[0] == "cd":
        if len(argv) != 2:
            raise ChatupError("'cd' needs exactly one folder")
        if argv[1] in (".", "./"):
            return ["cd", "."], False, ""
        return ["cd", normalize_rel_path(argv[1].rstrip("/\\"))], False, ""
    if ".." in argv[0].replace("\\", "/").split("/"):
        raise ChatupError("'..' is not allowed in the program path")
    prog = program_name(argv[0])
    names = [prog]
    eff, eff_args = prog, argv[1:]
    if prog in WRAPPERS:  # look at the program sudo/doas will actually start, too
        idx = wrapped_program(argv[1:])
        if idx < 0:
            raise ChatupError(f"'{prog}' needs a command to run after it (a bare root shell can't be started from chatup: open one yourself)")
        eff = program_name(argv[1:][idx])
        eff_args = argv[1:][idx + 1:]
        names.append(eff)
    if "pip" in cfg.no_allow_command and argv[1:3] == ["-m", "pip"]:
        raise ChatupError("program 'pip' is blocked")
    privileged = False
    danger_name = ""
    for name in names:
        if name in NEVER_PROGRAMS or name in cfg.no_allow_command:
            raise ChatupError(f"program '{name}' is blocked")
        if name in DANGEROUS_PROGRAMS or name.startswith("mkfs"):
            danger_name = name
        if name in PRIVILEGED:
            privileged = True
    if any(a.lower().lstrip("-") == "break-system-packages" for a in argv[1:]):
        raise ChatupError("'--break-system-packages' can break your system Python: use a virtual environment "
                          "instead (python3 -m venv .venv, then .venv/bin/pip install ...)")
    if prog == "su" and any(a in ("-c", "--command") for a in argv[1:]):
        raise ChatupError("'su -c' runs hidden shell code and is blocked")
    if eff in SHELLS and shell_runs_inline_code(eff, eff_args):
        raise ChatupError("inline shell code (-c, /c, -Command) is blocked: write the commands into a script file and run that "
                          "(for root: '@@@ run sudo sh script.sh')")
    if eff in PATH_ARG_PROGRAMS:
        deleting = eff in DELETE_PROGRAMS
        outside = False
        for arg in eff_args:
            if arg.startswith("-"):
                continue
            if os.name == "nt" and re.fullmatch(r"/[A-Za-z?]", arg):
                continue
            if arg in (".", "./") and not deleting:
                continue  # '.' (the project folder) is fine to copy into, but never to delete
            try:
                rel = normalize_rel_path(arg.rstrip("/\\") or "/")
            except ChatupError:
                if not privileged:
                    raise ChatupError(f"'{eff}' argument '{clean_text(arg, 60)}' must be a plain path inside the project; "
                                      "for system paths the command needs sudo") from None
                outside = True  # this is exactly what sudo is for, and its own typed phrase covers it
                continue
            if cfg.deny and path_matches(rel, cfg.deny):
                raise ChatupError(f"'{rel}' is blocked by 'deny' in {CONFIG_NAME}")
        if outside and deleting and not danger_name:
            danger_name = eff  # deleting things outside the project gets the strictest check
    return argv, privileged, danger_name


def script_path_of(argv: list) -> str:
    """Which code file a command runs ('./x.sh', 'bash x.sh', 'python3 x.py'), or '' if none."""
    if not argv:
        return ""
    if argv[0].lower().endswith(CODE_SUFFIXES):
        return argv[0]
    if program_name(argv[0]) in SCRIPT_EXT or program_name(argv[0]) in SHELLS:
        for arg in argv[1:]:
            if arg.startswith("-"):
                continue
            return arg if arg.lower().endswith(CODE_SUFFIXES) else ""
    return ""


def calls_sudo(text: str) -> bool:
    """Does this script call sudo/doas/pkexec? (Lines that are only comments don't count.)"""
    return any(SUDO_INSIDE_RE.search(line) for line in text.splitlines() if not line.lstrip().startswith("#"))


def code_text_of(argv: list, root: Path, manifest_files: dict) -> tuple:
    """For 'bash install.sh' or './install.sh': find the file it runs and return (path, text), or ('', '')."""
    for arg in argv[:4]:
        if not arg.lower().endswith(CODE_SUFFIXES):
            continue
        try:
            rel = normalize_rel_path(arg)
        except ChatupError:
            continue
        if rel in manifest_files:
            return rel, manifest_files[rel]
        target = root.joinpath(*rel.split("/"))
        try:
            if target.is_file() and target.stat().st_size <= 1_000_000:
                return rel, target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return "", ""


def prepare(actions: list, root: Path, notes: list, cfg: Config) -> list:
    """Check every step and fill in what the plan needs. Returns a list of problems (empty if all is fine)."""
    errors = []
    manifest_files: dict = {}
    for a in actions:
        if a.kind == "file":
            with contextlib.suppress(ChatupError):
                manifest_files[normalize_rel_path(a.path)] = a.content
    planned: set = set()      # files that earlier steps in this manifest will create
    file_paths: set = set()
    for a in actions:
        try:
            if a.kind == "run":
                a.argv, a.privileged, a.danger_name = check_command(a.command, cfg)
                is_cd = a.argv[0] == "cd"
                if is_cd and a.interactive:
                    raise ChatupError("'cd' cannot be interactive")
                a.risky = not is_cd  # moving around inside the project does no harm
                script_file = "" if is_cd else script_path_of(a.argv)
                if script_file and not a.batch and not a.interactive and os.name == "posix":
                    a.interactive = True  # a script may ask questions, so give it your keyboard
                    if not a.timeout_given:
                        a.timeout = INTERACTIVE_TIMEOUT_S
                    notes.append(f"line {a.line_no}: {clean_text(script_file, 60)} is a script, so it gets your keyboard in case it asks "
                                 "questions (put 'batch' after '@@@ run' to run it unattended)")
                if not a.privileged and not is_cd:
                    code_path, code = code_text_of(a.argv, root, manifest_files)
                    if code and calls_sudo(code):
                        a.privileged = True
                        a.priv_reason = f"{code_path} calls sudo inside"
                        notes.append(f"line {a.line_no}: {code_path} calls sudo, so this step is privileged and interactive: "
                                     "you will type your password in the terminal")
                if a.privileged and os.name == "posix":
                    a.interactive = True  # you need to be able to type a password or answer questions
                    if not a.timeout_given:
                        a.timeout = INTERACTIVE_TIMEOUT_S  # installing things can take longer than two minutes
                if a.interactive and os.name != "posix":
                    raise ChatupError("interactive commands work on Linux and macOS only for now")
                continue
            if a.kind == "script":
                name = program_name(a.interpreter)
                if name not in SCRIPT_EXT:
                    raise ChatupError(f"I can't run scripts with '{name}' (supported: {', '.join(sorted(SCRIPT_EXT))})")
                if name in NEVER_PROGRAMS or name in cfg.no_allow_command:
                    raise ChatupError(f"program '{name}' is blocked")
                if len(a.content.encode("utf-8")) > MAX_FILE_BYTES:
                    raise ChatupError("script is larger than 2 MB")
                a.ext = SCRIPT_EXT[name]
                a.argv = [a.interpreter]
                a.risky = True
                if calls_sudo(a.content):
                    a.privileged = True
                    a.priv_reason = "the script calls sudo inside"
                    notes.append(f"line {a.line_no}: the script calls sudo, so this step is privileged and interactive: "
                                 "you will type your password in the terminal")
                    if os.name == "posix":
                        a.interactive = True
                if a.interactive and os.name != "posix":
                    raise ChatupError("interactive commands work on Linux and macOS only for now")
                continue
            if a.path.endswith("/"):
                raise ChatupError("path must name a file, not a folder")
            a.path = normalize_rel_path(a.path)
            if cfg.deny and path_matches(a.path, cfg.deny):
                raise ChatupError(f"{a.path} is blocked by 'deny' in {CONFIG_NAME}")
            if cfg.allow and not path_matches(a.path, cfg.allow):
                raise ChatupError(f"{a.path} is not covered by 'allow' in {CONFIG_NAME}")
            target = resolve_inside(root, a.path)
            if len(a.content.encode("utf-8")) > MAX_FILE_BYTES:
                raise ChatupError("file is larger than 2 MB")
            if a.kind == "file":
                if a.path in file_paths:
                    notes.append(f"line {a.line_no}: {a.path} appears twice; the later block wins")
                file_paths.add(a.path)
            if target.is_dir():
                raise ChatupError(f"{a.path} is an existing folder")
            a.exists = target.exists() or a.path in planned
            a.sensitive = is_sensitive(a.path)
            if a.kind == "file":
                a.label = "OVERWRITE" if a.exists else "new"
                a.risky = a.exists and a.sensitive
            else:
                a.label = "APPEND" if a.exists else "new"
                a.risky = True
            planned.add(a.path)
        except ChatupError as exc:
            errors.append(f"line {a.line_no}: {exc}")
    return errors


# ---------------------------------------------------------------- terminal I/O

def read_terminal_line(prompt: str):
    """Read one line from the real terminal, never from stdin.

    Why: stdin is the pasted manifest itself. If we read your answer from
    there, the pasted text could "answer" for you. Reading from the terminal
    device means a human really typed it. Returns None if there is no terminal.
    """
    if os.name == "nt":
        import msvcrt  # Windows console API: reads the keyboard even when stdin is a pipe

        sys.stdout.write(prompt)
        sys.stdout.flush()
        chars: list = []
        while True:
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                sys.stdout.write("\n")
                return "".join(chars)
            if ch == "\x03":
                return None
            if ch in ("\x00", "\xe0"):
                msvcrt.getwch()  # special keys send two codes; throw the second one away
                continue
            if ch == "\b":
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            chars.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()
    try:
        # Two separate handles, because Python can't open a terminal in "r+" text mode.
        with open("/dev/tty", "w", encoding="utf-8", errors="replace") as out, \
                open("/dev/tty", "r", encoding="utf-8", errors="replace") as inp:
            out.write(prompt)
            out.flush()
            line = inp.readline()
    except OSError:
        return None
    return line.rstrip("\n") if line else None


def confirm(needs_phrase: bool) -> bool:
    if not sys.stdout.isatty():
        say("I can't ask you to confirm here (the output is redirected), so I'm stopping. Nothing was changed.")
        return False
    if needs_phrase:
        say("")
        say("Heads up: this plan runs programs or changes existing files in ways that can affect your computer.")
        say("Please read every line above, and go on only if you trust whoever wrote this command.")
        say(f"To continue, type exactly: {CONFIRM_PHRASE}")
        say("(anything else cancels)")
        answer = read_terminal_line("> ")
        ok = answer is not None and answer.strip() == CONFIRM_PHRASE
    else:
        answer = read_terminal_line("Go ahead? [y/N] ")
        ok = answer is not None and answer.strip().lower() in ("y", "yes")
    if answer is None:
        say("I couldn't reach a terminal to ask you, so nothing was changed.")
    return ok


def is_cd(a: Action) -> bool:
    return a.kind == "run" and bool(a.argv) and a.argv[0] == "cd"


def runs_something(a: Action) -> bool:
    return a.kind == "script" or (a.kind == "run" and not is_cd(a))


def describe_action(a: Action) -> str:
    if is_cd(a):
        return f"cd {a.argv[1]}"
    if a.kind == "run":
        kind = "DANGEROUS command: " if a.danger_name else "privileged command: " if a.privileged else "run: "
        return kind + a.command
    if a.kind == "script":
        extra = ", calls sudo" if a.privileged else ""
        return f"run a temporary {a.interpreter} script ({len(a.content.splitlines())} lines{extra})"
    return f"{a.label.lower()} {a.path}"


def print_plan(actions: list, root: Path, cfg: Config, keep_going: bool, keep_env: bool) -> None:
    say(f"chatup {__version__} - this is what will happen in: {root}")
    if cfg.found:
        say(f"  Project settings: {CONFIG_NAME} (permission = {cfg.permission})")
    for n, a in enumerate(actions, 1):
        tags = [f"timeout {a.timeout}s"] + (["privileged"] if a.privileged and a.kind == "script" else []) \
            + (["interactive"] if a.interactive else [])
        if is_cd(a):
            say(f"  {n:>3}. CD         {a.argv[1]}")
        elif a.kind == "run":
            label = "DANGER-RUN" if a.danger_name else "PRIV-RUN" if a.privileged else "RUN"
            say(f"  {n:>3}. {label:<10} {a.command}   ({', '.join(tags)})")
        elif a.kind == "script":
            body = a.content.splitlines()
            desc = f"  - {a.desc}" if a.desc else ""
            say(f"  {n:>3}. SCRIPT     {a.interpreter}, {len(body)} lines, temporary   ({', '.join(tags)}){desc}")
            for line in body[:SCRIPT_PREVIEW_LINES]:
                say("         | " + clean_text(line.replace("\t", "    "), 200))
            if len(body) > SCRIPT_PREVIEW_LINES:
                say(f"         | ... ({len(body) - SCRIPT_PREVIEW_LINES} more lines not shown)")
        else:
            flag = "  [!]" if a.sensitive else ""
            desc = f"  - {a.desc}" if a.desc else ""
            say(f"  {n:>3}. {a.label:<10} {a.path}{flag}{desc}")
    files = [a for a in actions if a.kind in ("file", "append")]
    runs = [a for a in actions if runs_something(a)]
    say(f"  total: {len(files)} file(s), {len(runs)} command(s) to run")
    if any(a.sensitive for a in files):
        say("  [!] = can execute code later (build, install, shell start, git hooks, CI).")
    if any(a.label == "OVERWRITE" for a in files):
        say("  Overwritten files are backed up in .chatup/backups/.")
    if any(a.danger_name for a in actions):
        say("  DANGER-RUN = can destroy data or take the computer down: you must type a special phrase and the program's name.")
    if any(a.privileged for a in actions):
        say("  PRIV-RUN = privileged command: right before it runs you must type a separate phrase.")
    if any(a.interactive for a in actions):
        say("  interactive = the program talks to you directly; you answer its questions, never the AI.")
    if any(runs_something(a) for a in actions):
        say("  Note: this shows the commands, not what is inside the files they run. Review code you don't trust.")
    if keep_going:
        say("  Option --keep-going is ON: a failing command will not stop the rest.")
    if keep_env:
        say("  Option --keep-env is ON: secret-looking environment variables are passed to commands.")


# ---------------------------------------------------------------- masking

def mask_secrets(text: str, root: Path, extra_values: list) -> str:
    text = text.replace(str(root), "<project>")
    with contextlib.suppress(RuntimeError, KeyError):
        home = str(Path.home())
        if len(home) > 3:
            text = text.replace(home, "~")
    for value in extra_values:
        text = text.replace(value, "***")
    text = KV_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    for pattern in TOKEN_RES:
        text = pattern.sub("***", text)
    return text


def format_tail(raw: bytes, truncated_start: bool, root: Path, extra_values: list) -> str:
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if truncated_start and lines:
        lines = lines[1:]  # the first line got cut in half, so we drop it
    kept = []
    in_key = False
    for line in lines:
        # A private key spans many lines: hide the whole block, not only its first line.
        if "-----BEGIN" in line and "PRIVATE KEY" in line:
            in_key = True
        if in_key:
            kept.append("*** private key block hidden ***" if "-----BEGIN" in line else "")
            if "-----END" in line:
                in_key = False
            continue
        kept.append(line)
    omitted = max(0, len(kept) - REPORT_TAIL_LINES)
    kept = kept[-REPORT_TAIL_LINES:]
    out = []
    if omitted:
        out.append(f"... ({omitted} earlier line(s) omitted)")
    for line in kept:
        line = mask_secrets(line, root, extra_values)
        if len(line) > REPORT_LINE_CHARS:
            line = line[:REPORT_LINE_CHARS] + " ...[cut]"
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------- execution

def journal(root: Path, record: dict) -> None:
    """Add one line to .chatup/journal.jsonl. If this fails we shrug and carry on: the journal must never break a run."""
    try:
        meta = root / ".chatup"
        meta.mkdir(exist_ok=True)
        gi = meta / ".gitignore"
        if not gi.exists():
            gi.write_text("*\n", encoding="utf-8")
        record = dict(record, ts=dt.datetime.now().isoformat(timespec="seconds"))
        with open(meta / "journal.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def write_bytes_atomic(target: Path, data: bytes) -> None:
    """Write into a temporary file next to the target, then swap it in.

    Why: if the computer crashes or you hit Ctrl+C halfway, you should never
    end up with a half-written source file.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".chatup-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if target.exists():
            shutil.copymode(str(target), tmp)  # keep the file's old permissions
        else:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)  # mkstemp makes the file private (0600); normal files shouldn't be
        os.replace(tmp, str(target))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def backup_file(root: Path, rel: str, target: Path, stamp: str) -> None:
    dest = root / ".chatup" / "backups" / stamp / Path(*rel.split("/"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(target), str(dest))
    gi = root / ".chatup" / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")


def apply_file_action(a: Action, root: Path, stamp: str) -> str:
    target = resolve_inside(root, a.path)  # check again right before writing, just to be sure
    data = a.content.encode("utf-8")
    existed = target.exists()
    if existed:
        backup_file(root, a.path, target, stamp)
    if a.kind == "append" and existed:
        old = target.read_bytes()
        sep = b"\n" if old and not old.endswith(b"\n") and data else b""
        data = old + sep + data
    write_bytes_atomic(target, data)
    if a.kind == "append":
        return "appended" if existed else "created"
    return "overwrote" if existed else "created"


def kill_tree(proc: subprocess.Popen) -> None:
    if hasattr(os, "killpg"):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)  # the command and everything it started
    else:
        with contextlib.suppress(OSError):
            proc.kill()


def read_tail(fh) -> tuple:
    size = fh.seek(0, os.SEEK_END)
    start = max(0, size - TAIL_READ_BYTES)
    fh.seek(start)
    return fh.read(), start > 0


def run_command(a: Action, root: Path, cwd: Path, env: dict, extra_values: list) -> dict:
    started = time.monotonic()
    result = {"exit": None, "timed_out": False, "stdout": "", "stderr": "", "secs": 0.0}
    # Output goes to temporary files instead of pipes, so even a very chatty
    # program can't eat our memory. We only read back the last part.
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        kwargs: dict = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True  # its own process group, so on a timeout we can stop it and everything it started
        else:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            proc = subprocess.Popen(
                a.argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                stdout=out_f, stderr=err_f, **kwargs,
            )
        except FileNotFoundError:
            result.update(exit=127, stderr=f"command not found: {a.argv[0]}")
            result["secs"] = time.monotonic() - started
            return result
        except OSError as exc:
            result.update(exit=126, stderr=f"cannot start command: {exc}")
            result["secs"] = time.monotonic() - started
            return result
        try:
            code = proc.wait(timeout=a.timeout)
        except subprocess.TimeoutExpired:
            result["timed_out"] = True
            kill_tree(proc)
            code = proc.wait()
        except BaseException:
            kill_tree(proc)
            raise
        raw_out, cut_out = read_tail(out_f)
        raw_err, cut_err = read_tail(err_f)
    result["exit"] = code
    result["secs"] = time.monotonic() - started
    result["stdout"] = format_tail(raw_out, cut_out, root, extra_values)
    result["stderr"] = format_tail(raw_err, cut_err, root, extra_values)
    return result


def child_environment(keep_env: bool) -> tuple:
    """Build the environment programs will see, and collect secret values to hide in the report."""
    if keep_env:
        return dict(os.environ), []
    clean = {}
    hidden = []
    for key, value in os.environ.items():
        if SECRET_ENV_RE.search(key):
            if len(value) >= 8:
                hidden.append(value)
        else:
            clean[key] = value
    return clean, hidden


def change_dir(cwd: Path, root: Path, rel: str) -> Path:
    if rel == ".":
        return cwd
    new = Path(os.path.realpath(cwd.joinpath(*rel.split("/"))))
    try:
        new.relative_to(Path(os.path.realpath(root)))
    except ValueError:
        raise ChatupError(f"'cd {rel}' would leave the project folder") from None
    if not new.is_dir():
        raise ChatupError(f"folder not found: {rel}")
    return new


def clean_transcript(raw: bytes) -> bytes:
    """Tidy up what the terminal recorded: remove colour codes, and keep only the final version of lines that were redrawn (progress bars)."""
    text = ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).replace("\r\n", "\n")
    return "\n".join(ln.split("\r")[-1] for ln in text.split("\n")).encode("utf-8")


def run_interactive(a: Action, root: Path, cwd: Path, env: dict, extra_values: list) -> dict:
    """Run a program connected to your real keyboard and screen (Linux/macOS).

    The program gets its own pseudo-terminal. Keys come only from /dev/tty,
    never from the manifest, so the AI can't type an answer for you.
    """
    import fcntl
    import pty
    import select
    import termios
    import tty

    started = time.monotonic()
    result = {"exit": None, "timed_out": False, "stdout": "", "stderr": "", "secs": 0.0}
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        result.update(exit=1, stderr="no terminal is available for an interactive command")
        return result
    pid, master = pty.fork()
    if pid == 0:  # this is the child: it turns into the command
        try:
            os.chdir(str(cwd))
            os.execvpe(a.argv[0], a.argv, env)
        except OSError:
            os._exit(127)
    transcript = bytearray()
    trimmed = False
    deadline = started + a.timeout
    timed_out = False
    old_attrs = termios.tcgetattr(tty_fd)
    try:
        with contextlib.suppress(OSError):  # same window size, so full-screen programs look right
            size = fcntl.ioctl(tty_fd, termios.TIOCGWINSZ, b"\0" * 8)
            fcntl.ioctl(master, termios.TIOCSWINSZ, size)
        tty.setraw(tty_fd)  # pass every key straight through (even Ctrl+C goes to the program)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                timed_out = True
                break
            ready, _, _ = select.select([master, tty_fd], [], [], min(left, 1.0))
            if master in ready:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    data = b""  # Linux says EIO when the program has closed its end: that just means 'finished'
                if not data:
                    break
                os.write(tty_fd, data)
                transcript += data
                if len(transcript) > 2 * TAIL_READ_BYTES:
                    del transcript[:-TAIL_READ_BYTES]
                    trimmed = True
            if tty_fd in ready:
                try:
                    keys = os.read(tty_fd, 1024)
                except OSError:
                    keys = b""
                if keys:
                    os.write(master, keys)
    finally:
        termios.tcsetattr(tty_fd, termios.TCSADRAIN, old_attrs)
        os.close(tty_fd)
    status = 0
    while True:  # pick up the exit status; if the program is still hanging around after the timeout, stop it
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            break
        if timed_out or time.monotonic() >= deadline:
            timed_out = True
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)
            status = os.waitpid(pid, 0)[1]
            break
        time.sleep(0.05)
    os.close(master)
    if os.WIFEXITED(status):
        code = os.WEXITSTATUS(status)
    elif os.WIFSIGNALED(status):
        code = 128 + os.WTERMSIG(status)
    else:
        code = 1
    result.update(exit=code, timed_out=timed_out, secs=time.monotonic() - started)
    result["stdout"] = format_tail(clean_transcript(bytes(transcript)), trimmed, root, extra_values)
    return result


def ensure_chatup_dir(root: Path) -> Path:
    meta = root / ".chatup"
    meta.mkdir(exist_ok=True)
    gi = meta / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
    return meta


def run_step(a: Action, n: int, root: Path, cwd: Path, env: dict, secret_values: list, stamp: str) -> dict:
    """Run one 'run' or 'script' step and return what happened."""
    runner = run_interactive if a.interactive else run_command
    if a.kind == "run":
        res = runner(a, root, cwd, env, secret_values)
        shown = a.command
        journal(root, {"event": "run", "command": mask_secrets(a.command, root, secret_values),
                       "exit": res["exit"], "secs": round(res["secs"], 2)})
    else:
        tmp_dir = ensure_chatup_dir(root) / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        script = tmp_dir / f"{stamp}-{n}{a.ext}"
        data = a.content.encode("utf-8")
        script.write_bytes(data)
        try:
            res = runner(replace(a, argv=[a.interpreter, str(script)]), root, cwd, env, secret_values)
        finally:
            with contextlib.suppress(OSError):
                script.unlink()  # temporary really means temporary
        shown = f"{a.interpreter} <temporary script, {len(a.content.splitlines())} lines>"
        journal(root, {"event": "script", "interpreter": a.interpreter, "sha256": hashlib.sha256(data).hexdigest(),
                       "exit": res["exit"], "secs": round(res["secs"], 2)})
    res.update(n=n, type="run", command=mask_secrets(shown, root, secret_values), timeout=a.timeout,
               interactive=a.interactive)
    if not a.interactive and NO_TTY_RE.search(res["stdout"] + res["stderr"]):
        res["hint"] = ("Something inside this step called sudo, but only interactive steps get your keyboard. "
                       "Ask the AI to put 'interactive' after '@@@ run' and sudo on the command itself.")
    return res


def ask_step(n: int, what: str) -> str:
    """permission = request: ask about one single step. Returns 'yes', 'no' or 'quit'."""
    answer = read_terminal_line(f"Step {n}: {what}\n  Allow this step? [y = yes, n = skip it, q = stop here] ")
    if answer is None:
        return "quit"
    word = answer.strip().lower()
    if word in ("y", "yes"):
        return "yes"
    return "quit" if word in ("q", "quit") else "no"


def confirm_privileged(a: Action) -> bool:
    say("")
    say(f"PRIVILEGED STEP: {a.command if a.kind == 'run' else describe_action(a)}")
    if a.priv_reason:
        say(f"({a.priv_reason})")
    say("This can change your system. The program asks for your password or confirmation itself: you answer it, not the AI.")
    say(f"To allow this one command, type exactly: {PRIV_PHRASE}")
    answer = read_terminal_line("> ")
    return answer is not None and answer.strip() == PRIV_PHRASE


def confirm_dangerous(a: Action) -> bool:
    """Third level of 'are you sure?': the phrase AND the program's name, typed by hand, for every dangerous command."""
    say("")
    say(f"DANGEROUS COMMAND: {a.command}")
    say("This kind of command can destroy data or take your computer down, and it cannot be undone.")
    say("Continue only if you asked for exactly this, and you know what it will touch.")
    say(f"Type exactly: {DANGER_PHRASE}")
    if (read_terminal_line("> ") or "").strip() != DANGER_PHRASE:
        return False
    answer = read_terminal_line(f"Now type the name of the program to confirm it ({a.danger_name}): ")
    return answer is not None and answer.strip() == a.danger_name


def execute(actions: list, root: Path, cfg: Config, keep_going: bool, keep_env: bool) -> tuple:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    env, secret_values = child_environment(keep_env)
    results: list = []
    failed = False
    stopped = False
    cwd = root
    for n, a in enumerate(actions, 1):
        what = describe_action(a)
        if stopped or (failed and not keep_going):
            results.append({"n": n, "type": "skipped", "text": what})
            continue
        if cfg.permission == "request":
            decision = ask_step(n, what)
            if decision == "quit":
                stopped = True
                results.append({"n": n, "type": "skipped", "text": f"{what} (you stopped here)"})
                continue
            if decision == "no":
                results.append({"n": n, "type": "skipped", "text": f"{what} (you said no)"})
                continue
        if a.danger_name and not confirm_dangerous(a):
            failed = True
            results.append({"n": n, "type": "skipped", "text": f"{what} (not confirmed)"})
            continue
        if a.privileged and not confirm_privileged(a):
            failed = True
            results.append({"n": n, "type": "skipped", "text": f"{what} (not confirmed)"})
            continue
        if is_cd(a):
            try:
                cwd = change_dir(cwd, root, a.argv[1])
            except ChatupError as exc:
                failed = True
                results.append({"n": n, "type": "error", "text": str(exc)})
            else:
                results.append({"n": n, "type": "cd", "text": a.argv[1]})
        elif runs_something(a):
            res = run_step(a, n, root, cwd, env, secret_values, stamp)
            if res["exit"] != 0:
                failed = True
            results.append(res)
        else:
            try:
                verb = apply_file_action(a, root, stamp)
            except (OSError, ChatupError) as exc:
                failed = True
                results.append({"n": n, "type": "error", "text": f"{a.path}: {exc}"})
                journal(root, {"event": "error", "path": a.path, "error": str(exc)})
                continue
            results.append({"n": n, "type": "write", "verb": verb, "path": a.path})
            journal(root, {"event": verb, "path": a.path, "bytes": len(a.content.encode("utf-8"))})
    return results, failed


def print_results(results: list, failed: bool, had_runs: bool) -> None:
    say("")
    say("----- chatup report (paste this into the chat) -----" if had_runs else "----- chatup result -----")
    say(f"status: {'FAILED' if failed else 'OK'}")
    for r in results:
        if r["type"] == "write":
            say(f"[{r['n']}] {r['verb']} {r['path']}")
        elif r["type"] == "cd":
            say(f"[{r['n']}] cd {r['text']}")
        elif r["type"] == "error":
            say(f"[{r['n']}] ERROR {r['text']}")
        elif r["type"] == "skipped":
            say(f"[{r['n']}] skipped: {r['text']}")
        else:
            note = f" TIMED OUT after {r['timeout']}s" if r["timed_out"] else ""
            say(f"[{r['n']}] run: {r['command']}")
            say(f"    exit code: {r['exit']}{note}   time: {r['secs']:.1f}s")
            if r.get("hint"):
                say(f"    HINT: {r['hint']}")
            for name in ("stdout", "stderr"):
                if r[name].strip():
                    title = "terminal output" if r.get("interactive") else name
                    say(f"    --- {title} (last {REPORT_TAIL_LINES} lines) ---")
                    for line in r[name].splitlines():
                        say("    " + line)
    say("----- end of report -----" if had_runs else "----- end -----")
    say("")
    written = sum(1 for r in results if r["type"] == "write")
    if failed and had_runs:
        say("Something went wrong. Copy the report above into the chat; the AI can usually fix it from that.")
    elif failed:
        say("Something went wrong; the details are above.")
    elif had_runs:
        say(f"All done ({written} file(s) written). If the chat asked for the result, copy the report above and send it.")
    else:
        say(f"All done: {written} file(s) written. If the chat told you to build or run something, do that next.")


# ---------------------------------------------------------------- entry point

def refuse_unsuitable_root(root: Path) -> None:
    home = None
    with contextlib.suppress(RuntimeError, KeyError):
        home = Path(os.path.realpath(Path.home()))
    real = Path(os.path.realpath(root))
    if real == real.parent or (home is not None and real == home):
        raise ChatupError("run chatup inside a project folder, not in your home folder or the disk root")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chatup",
        description="Turn an AI chat answer into real files. Open a terminal in your project folder, "
                    "paste the whole command the chat gave you, and chatup shows what it will do "
                    "and asks before touching anything.",
        epilog="Anything that runs programs asks you to type a confirmation phrase by hand. "
               "There is no option to skip it.",
    )
    p.add_argument("--dry-run", action="store_true", help="only show the plan, change nothing")
    p.add_argument("--keep-going", action="store_true", help="do not stop at the first failing command")
    p.add_argument("--keep-env", action="store_true",
                   help="let commands see secret-looking environment variables (KEY, TOKEN, ...)")
    p.add_argument("--version", action="version", version=f"chatup {__version__}")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    if sys.stdin.isatty():
        say("Nothing to unpack: chatup needs the command from the chat.")
        say("Paste the whole thing, from the line starting with 'chatup <<' to the last line, in one go.")
        say("More help: chatup --help")
        return 2
    try:
        root = Path.cwd()
        refuse_unsuitable_root(root)
        cfg = load_config(root)
        if cfg.permission == "deny" and not args.dry_run:
            say(f"This folder's {CONFIG_NAME} says 'permission = deny', so chatup will not change anything here.")
            return 1
        raw = sys.stdin.buffer.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ChatupError("what was pasted is larger than 20 MB")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ChatupError("the text is not valid UTF-8") from None
        actions, notes = parse_manifest(text)
        had_asks = any(a.kind == "ask" for a in actions)
        values = resolve_asks(actions, root, args.dry_run)
        if not actions:
            raise ChatupError("the command only asks questions; there is nothing to create or run")
        apply_placeholders(actions, values)
        adapt_python(actions, root, notes, cfg)
        if had_asks and args.dry_run:
            notes.append("dry run: questions were answered with their default values")
        if cfg.permission == "deny":
            notes.append(f"{CONFIG_NAME} says 'permission = deny': a real run would be refused")
        errors = prepare(actions, root, notes, cfg)
        if errors:
            say("I can't use this command, so nothing was changed:")
            for err in errors:
                say(f"  - {err}")
            say("")
            say("Copy these lines into the chat and ask it to send the command again.")
            return 1
        notes.extend(cfg.notes)
        print_plan(actions, root, cfg, args.keep_going, args.keep_env)
        if notes:
            say("  Notes:")
            for note in notes:
                say(f"   - {note}")
        if args.dry_run:
            say("(dry run: nothing was changed)")
            return 0
        if not confirm(needs_phrase=any(a.risky for a in actions)):
            say("Okay, cancelled. Nothing was changed.")
            return 1
        results, failed = execute(actions, root, cfg, args.keep_going, args.keep_env)
        print_results(results, failed, had_runs=any(runs_something(a) for a in actions))
        return 1 if failed else 0
    except ChatupError as exc:
        say(f"I couldn't use this command: {exc}")
        say("Nothing was changed. Ask the chat to send it again.")
        return 1
    except KeyboardInterrupt:
        say("\nStopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
