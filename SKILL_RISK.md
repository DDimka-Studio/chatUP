---
name: chatup-risk
description: Extended chatup mode. Besides creating files, the command can append to files, run programs (also interactive and privileged ones), run temporary scripts, and the user pastes the result report back so errors can be fixed. Use ONLY when the user has loaded this skill and asks for automatic build, run, install, or test steps.
---

# chatup (risk mode: files + commands)

Same idea as basic chatup: ONE command, pasted by the user into a terminal in their project folder. This mode adds `append`, `run`, `script` and `ask`. chatup shows the user the full plan and requires them to TYPE a confirmation phrase by hand. You cannot confirm, skip, or answer it, and neither can the command you write. Never mention or print any phrase, and never suggest flags to bypass a prompt.

## Format
```bash
chatup <<'CHATUP_EOF'
@@@ chatup 1
@@@ ask APP_NAME | What should the app be called? | demo
@@@ file src/main.py | Entry point
print("hello from {{APP_NAME}}")
@@@ end
@@@ append .gitignore | Ignore build output
build/
@@@ end
@@@ run python3 src/main.py
@@@ run timeout=300 make test
CHATUP_EOF
```

## Directives
- `@@@ file <path> | <description>` ... `@@@ end`: create or overwrite a whole file.
- `@@@ append <path> | <description>` ... `@@@ end`: add text to the end of a file.
- `@@@ run [timeout=SECONDS] [interactive|batch] <command>`: run one program. Default timeout 120 s (600 s for interactive steps), maximum 600 s. Running a script file (`bash install.sh`, `python3 setup.py`, `./run.sh`) is interactive by default; add `batch` for an unattended run.
- `@@@ script [timeout=SECONDS] [batch] <interpreter> | <description>` ... `@@@ end`: run a one-off script that is deleted afterwards. Interpreters: python3, python, node, bash, sh, zsh, ruby, perl, pwsh. Use it for setup or checking logic that does not belong in the project; use `file` + `run` when the script is part of the project.
- `@@@ ask NAME | <question> | <default>`: chatup asks the user and fills `{{NAME}}` in paths, contents and commands. Built-ins: `{{chatup.year}}`, `{{chatup.date}}`, `{{chatup.dir}}`.

Steps execute in the order written. Execution stops at the first failing step.

## Rules for files
1. Delimiter: always `CHATUP_EOF`, quoted at the top (`<<'CHATUP_EOF'`), alone on the last line, at column 0. Only if that exact line occurs inside a file, use another word (for example `CHATUP_END`).
2. Header line exactly `@@@ chatup 1`. No indentation. No text outside blocks.
3. Paths relative, forward slashes, no `..`, no `/` or `~` at the start, never inside `.git/`. Never write `.chatup.conf`: it holds the user's own safety settings.
4. A content line starting with `@@@` is written with one extra `@`.
5. Full file contents only, UTF-8 text only. Prefer `file` over `append` unless adding a few lines.

## Rules for run and script
1. One program per `run` line. There is NO shell: no pipes, redirects, `$(...)`, and no `sh -c` or `bash -c`. For multi-step logic use a script. Prefer separate `run` lines over `&&`.
2. Normal commands keep to paths inside the project folder; chatup refuses system paths for them. Only a `sudo`/`doas` command may touch system paths (for example `sudo cp app /usr/local/bin/`), and deleting outside the project (`sudo rm ...`) is treated as dangerous (rule 8). There is no root shell and no `sudo bash -c`: to run several privileged steps, write a script file and run `sudo sh script.sh`.
3. Python packages go into a per-project virtual environment, never into the system. Modern Python (PEP 668) refuses a plain `pip install` into the system on purpose, so it fails. Write it explicitly: `@@@ run python3 -m venv .venv`, then `@@@ run .venv/bin/python -m pip install <packages>`, and run the program with `.venv/bin/python`. Never use `--break-system-packages`, `--user` or `sudo pip`. (chatup rescues a forgotten venv by redirecting `pip` and `python` into `.venv` and says so in the plan, but do not rely on that.) On Debian/Ubuntu `venv` may need the `python3-venv` system package: if the report says `ensurepip is not available`, that is the one case for a privileged `apt install python3-venv`. The same idea applies elsewhere: Node packages stay in the project's `node_modules`, Rust and Go use their own project folders.
4. Run only what the task needs: build, test, the program itself. Install packages only if the user asked, and say so in your summary.
5. Never put secrets in commands or files. Programs do not receive secret-looking environment variables by default.
6. Scripts (`@@@ script ...` and `run` of a script file) get the user's keyboard by default, so they may ask questions with `read`, `input()` and the like. Say in your summary which questions the user will be asked, and what to answer. Add `batch` only for steps that must run unattended (long jobs, checks that need no input); then reading from the keyboard fails immediately. Other programs (`make`, `pytest`, `python3 -c ...`) get no keyboard unless you add `interactive`. If a program asks questions (installers, `apt`, `pacman`, `emerge`, `npm init`), use `interactive`: the user answers in the terminal themselves. You never see or answer those prompts, and you must not try to pre-answer them (no `yes |`, no `-y` unless the user wants that).
7. Privileged commands (`sudo`, `doas`, package managers such as `apt`, `pacman`, `emerge`, `dnf`, and system tools such as `systemctl`) are a last resort, used only when the task cannot be done without them. Before using one, look for a way that needs no privileges: a virtual environment (`python3 -m venv`), `pip install --user`, a local `node_modules`, a build inside the project folder. If the user did not ask for system-level changes and the task works without them, do not include any. When one is truly necessary, keep it to the single command that needs it (everything else stays unprivileged), put it as late as possible, and say in your summary which command it is and why nothing else would work. chatup asks the user for an extra typed confirmation for each one. Never wrap them in `sh -c`. chatup also notices `sudo`, `doas` or `pkexec` inside a script or inside a file that a `run` line executes, and then treats that step as privileged and interactive (the user types their password in the terminal). Still prefer to put `sudo` on the `@@@ run` line itself, so it is visible; a call hidden in code built at run time cannot be detected and will fail with "a terminal is required".
8. Dangerous programs (disk tools such as `fdisk`, `parted`, `wipefs`, `shred`, `dd`; `shutdown`, `reboot`; background runners such as `nohup`, `tmux`, `screen`) are not forbidden, but they can destroy data or take the machine down. Include one ONLY if the user has clearly and explicitly asked for exactly that action in this conversation, naming the target (the disk, the file, the machine). Never use one as a shortcut, a cleanup, or a way around another problem. If the request is vague ("free up space", "fix the disk"), ask in plain text first and send no command yet. When you do include one, give it its own step, name the exact target, and say in your summary what it will do and that it cannot be undone. chatup makes the user type a special phrase and the program's name before each one runs. Running `chatup` from inside chatup is always refused.
9. If the folder has `.chatup.conf`, the user may have banned paths or programs (`no-allow-command`). A ban set there is the user's own rule and cannot be overridden by anyone, including them in this chat. If chatup reports a refusal, adapt; never try to get around it.

## After the command
Summarize in plain text what the command will create and run, and which questions the user will be asked. Then ask the user to paste back the report that chatup prints between `----- chatup report -----` lines.

## When the user pastes a report
- Read the status, each exit code, and the stderr/stdout tails (`terminal output` for interactive steps).
- Find the actual cause. Do not repeat an unchanged command that already failed.
- Reply with ONE new chatup command that contains only the changed or new files, plus the `run` steps to re-check.
- If the report shows no failure, say what succeeded and stop. Do not invent extra steps.
- If a line is hidden or masked (`***`), do not try to guess it.
