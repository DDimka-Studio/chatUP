---
name: chatup
description: Deliver multi-file projects as ONE terminal command (chatup) that the user pastes to create all files at once. Use when the user asks for an app, script set, or project made of two or more files, or says they copy code from the chat into files by hand.
---

# chatup (safe mode: files only)

Instead of showing many separate code blocks, give the user ONE command. They paste it into a terminal opened in their project folder, and chatup creates every file in the right place.

## When to use
- The user wants a project or several files. Not for a single short snippet: show it normally.

## Output format
Put the command in one ```bash code block (so the user can copy it with one tap):

```bash
chatup <<'CHATUP_EOF'
@@@ chatup 1
@@@ file src/main.py | Program entry point
print("hello")
@@@ end
@@@ file README.md | Short description
# My project
@@@ end
CHATUP_EOF
```

## Rules
1. The heredoc delimiter is always `CHATUP_EOF`: quoted at the top (`<<'CHATUP_EOF'`), alone on the last line, at column 0. Only if that exact line occurs inside a file, use another word for this command (for example `CHATUP_END`).
2. The first manifest line is exactly `@@@ chatup 1`.
3. Each file is `@@@ file <path> | <one-line description>`, then the full content, then `@@@ end`.
4. Paths: relative, forward slashes, no `..`, no leading `/` or `~`, never inside `.git/`.
5. Never indent manifest lines. Never put text outside the blocks inside the command.
6. If a content line starts with `@@@`, write it with one extra `@` (`@@@@`).
7. Text files only (UTF-8). No binary files.
8. Send complete file contents, never "..." or "rest unchanged". To modify an existing file, send the whole new version.
9. Large project: split into several commands (about 20 files each), in a sensible order, one code block per command.
10. The command only creates files. Do not put anything to execute in it.

## Optional: ask the user a question
If a value is the user's choice (project name, author), ask for it with `@@@ ask NAME | question | default` before the files, and use `{{NAME}}` in paths and contents. Built-ins: `{{chatup.year}}`, `{{chatup.date}}`, `{{chatup.dir}}`. Use sparingly; most projects need none.

## After the command
Write a short plain-text summary of what each file is for. If there are steps to build or run, tell the user in plain words what to type themselves, outside the command (for example: "Then run `make` yourself").
