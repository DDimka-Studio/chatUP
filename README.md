# chatup

Turn an AI chat answer into real files, in one paste.

If you use an AI in a browser or app (no coding agent, no subscription extras), you usually copy code file by file. **chatup** replaces that: the AI gives you one command, you paste it into a terminal inside your project folder, and every file lands in the right place.

## Install

chatup is a single file and uses only Python's standard library (Python 3.8+). It installs **no packages**, so the "externally managed environment" error of modern Linux distributions never applies to it, and you never need `--break-system-packages`.

Get the files (assuming the repository is called `chatup`):

```bash
git clone https://github.com/DDimka-Studio/chatUP.git
cd chatup
```

**Linux / macOS**: run the installer from the folder with `chatup.py`:

```bash
. ./install.sh        # note the leading dot: it also fixes PATH in THIS terminal
# or: sh install.sh
```

It copies chatup to `~/.local/bin`, tells you the one line your shell file needs (`~/.bashrc`, `~/.zshrc`, `config.fish`, or `~/.profile`) and asks whether to add it for you. Nothing outside your home folder is touched, no `sudo`. A plain `sh install.sh` cannot change the terminal you are in (no script can), so it prints the `export` line for you to run. Remove it with `sh install.sh --uninstall`.

**Windows**: put `chatup.py` somewhere on your PATH and run it as `python chatup.py`.

**Python projects the AI builds** get their own virtual environment (`python3 -m venv .venv`) instead of touching system Python. The risk-mode skill tells the AI to do this. If the AI forgets, chatup rescues it: a bare `pip install` (or `python3 -m pip install`) creates `.venv` when missing and runs pip from there, later `python`/`python3` calls use it too, and `--user` / `--break-system-packages` are dropped. The plan shows the rewritten commands and a note, so nothing happens behind your back. Commands that name a path (`.venv/bin/pip`) are never touched. Which folder is used: the `venv =` setting if you have one, otherwise an existing `.venv`, `venv`, `env` or `penv` (in that order), otherwise a new `.venv`.

## Use

1. Give your AI the contents of `SKILL.md` (safe mode: files only).
2. Ask for a project: "make me a small todo app".
3. Open a terminal in an empty project folder and paste the command the AI gives you.
4. chatup shows exactly what it will create, and asks `Go ahead? [y/N]`.

Try it without changing anything: add `--dry-run`.

## Letting the AI run things (optional, at your own risk)

Give the AI `SKILL_RISK.md` instead. The command can then also append to files, run programs and run temporary scripts. Before anything runs, chatup shows the full plan and you must **type this phrase yourself**:

```
I UNDERSTAND AND ACCEPT THE RISK
```

When it finishes, chatup prints a short report (exit codes, the end of the output, secrets masked). Paste it back into the chat and the AI can fix what failed.

**Scripts** (`@@@ script ...`, or `run` of a file such as `bash install.sh`, `python3 setup.py`, `./run.sh`) get your keyboard by default, because scripts often ask questions. Add `batch` (`@@@ run batch sh build.sh`) to run one unattended, with no keyboard.

**Interactive commands** (`@@@ run interactive apt install ...`): the program is attached to your keyboard and screen. You answer its questions (`[Y/n]`, passwords, choices), never the AI, and the transcript goes into the report.

**Privileged commands** (`sudo`, `doas`, `apt`, `pacman`, `emerge`, `dnf`, `systemctl`, ...) are marked `PRIV-RUN`. On top of the plan phrase you must type a second one right before each of them runs:

```
I ALLOW THIS PRIVILEGED COMMAND
```

The skill tells the AI to use these only when there is no unprivileged way, and to keep each one to a single command.

chatup also looks inside `@@@ script` blocks and inside script files it is told to run (`bash install.sh`, `./setup.sh`): if they call `sudo`, `doas` or `pkexec`, the step becomes privileged and interactive, so you get the password prompt instead of three "a terminal is required" errors. A `sudo` that a program builds at run time cannot be seen in advance; the report then says what to change (`HINT:`).

**Dangerous commands** (`dd`, disk tools like `fdisk` and `wipefs`, `shutdown`, `reboot`, `nohup`, `tmux`, ...) are marked `DANGER-RUN` and are *not* forbidden, also not when wrapped in `sudo`. They need a third, deliberate confirmation right before each one runs: type `I KNOW THIS CAN DESTROY DATA`, then type the program's name. The skill tells the AI to include one only when you explicitly asked for exactly that action.

What stays refused: running chatup inside chatup, shell `-c` one-liners (use a script), pipes and redirects (use a script), system paths for normal commands (put `sudo` in front to touch them: `sudo cp app /usr/local/bin/` works, `sudo rm` outside the project counts as dangerous), and anything you ban yourself with `no-allow-command` in `.chatup.conf`. Your own ban cannot be overridden by a phrase.

**Temporary scripts** (`@@@ script python3`): the code is shown in the plan, written to `.chatup/tmp/`, run, then deleted.

**Placeholders**: `@@@ ask NAME | Question? | default` asks you before the plan; `{{NAME}}` is then filled in everywhere. Built-ins: `{{chatup.year}}`, `{{chatup.date}}`, `{{chatup.dir}}`. Unknown `{{...}}` (templates!) are left alone.

## Project settings (`.chatup.conf`)

Optional file in the project folder. It can only make chatup **stricter**, so nothing in a chat answer can loosen it (and chatup refuses to write this file itself).

```
# ask before EVERY step, on top of the plan confirmation
permission = request
# only these paths may be written (* also matches across folders)
allow = src/**, tests/**, README.md
# these may never be written, also not deleted/moved by commands
deny = secrets/**, *.pem
# programs that may never run here
no-allow-command = curl, wget
# name of your Python virtual environment (default: .venv)
venv = penv
```

`permission = deny` makes the folder read-only for chatup. A looser `permission = allow` is reserved for the future and currently ignored.

## Moving context between chats (`SKILL_CONTEXT.md`)

Give any AI `SKILL_CONTEXT.md` and say "save this conversation". It splits everything it knows from that chat into small self-contained files (goal and state, decisions, facts, your preferences, open questions, code...) plus an index, and hands them over as one chatup command. Paste it into a terminal and you get a folder like `context-2026-10-06/`.

To continue elsewhere, open a new chat and paste only what you need: `00-index.md` for an overview, or just one part (say `02-decisions.md`) together with the starter prompt from the index. Every part starts with a two-line header, so it also makes sense on its own.

Copy a part to the clipboard instead of selecting text by hand:

```bash
pbcopy < context-2026-10-06/02-decisions.md          # macOS
xclip -selection clipboard < context-2026-10-06/02-decisions.md   # Linux (X11)
wl-copy < context-2026-10-06/02-decisions.md         # Linux (Wayland)
```

It only uses what is visible in that conversation, and it is told to leave out passwords and private data. Read the files before you share them anyway.

## Safety, in plain words

- Nothing happens until you confirm. The answer is read from your real terminal, so a pasted command cannot answer for you. There is no option to skip this.
- Files stay inside the current folder. No `..`, no absolute paths, nothing in `.git/`, no writing through symlinks.
- Overwritten files are backed up in `.chatup/backups/`. Everything done is logged in `.chatup/journal.jsonl`.
- chatup shows the commands it will run, not what is inside the files they run. Only run code from sources you trust.
- Dangerous programs need three separate typed confirmations (plan phrase, danger phrase, program name).
- Programs run without a shell, with a timeout, with no keyboard input, and without secret-looking environment variables (`KEY`, `TOKEN`, ...). Use `--keep-env` to pass them on.
- Files that run code later (Makefile, package.json, `*.sh`, CI workflows...) are marked `[!]`. Overwriting an existing one needs the phrase.
- The blocklist (`sudo`, `rm` outside the project, `bash -c`, ...) is best-effort. **The real protection is you reading the plan.** Only run commands from sources you trust.

## Forgiving by design

AI answers are rarely perfect, so chatup tolerates harmless slips and tells you what it did: chat text around the command, a forgotten `@@@ end`, Windows-style paths, `cd dir && make` (split into steps), duplicate files (the later one wins). Anything that touches safety is still refused.

## Options

| Option | What it does |
| --- | --- |
| `--dry-run` | Show the plan (including script code and placeholder values), change nothing |
| `--keep-going` | Don't stop at the first failing command |
| `--keep-env` | Let commands see secret-looking environment variables |

## Format (for tool and skill authors)

```
chatup <<'CHATUP_EOF'
@@@ chatup 1
@@@ file src/main.py | Entry point
print("hello")
@@@ end
@@@ run python3 src/main.py
CHATUP_EOF
```

`@@@ file`, `@@@ append` and `@@@ script` take content until `@@@ end`; `@@@ run [timeout=N] [interactive] <command>` runs one program; `@@@ ask NAME | question | default` asks you. A content line that must begin with `@@@` is written with one extra `@`.

## License

Copyright (C) 2026 [DDimka-Studio](https://github.com/DDimka-Studio).

GNU General Public License v3.0 or later (GPL-3.0-or-later). See [LICENSE](LICENSE).
