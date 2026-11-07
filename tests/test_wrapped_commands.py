"""Tests for commands hidden behind wrapper programs (env, nice, timeout, xargs...).

Run from the repository folder:   python3 -m unittest discover -s tests -v
They only check the parser (check_command); no command is ever executed.
"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("chatup", ROOT / "chatup.py")
chatup = importlib.util.module_from_spec(spec)
import sys
sys.modules["chatup"] = chatup
spec.loader.exec_module(chatup)


def cfg(**kw):
    c = chatup.Config()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


class Refused(AssertionError):
    pass


def check(cmd, config=None):
    try:
        return chatup.check_command(cmd, config or cfg())
    except chatup.ChatupError as exc:
        raise Refused(str(exc))


class PlainCommandsStillWork(unittest.TestCase):
    def test_ordinary_commands_pass(self):
        for cmd in ["python3 -m venv .venv", ".venv/bin/pip install requests", "make test", "ls -la",
                    "python3 src/main.py", "pytest -q", "rm -rf build/", "cp a.txt sub/",
                    "node index.js", "npm test", "python3 -c \"print(1)\""]:
            with self.subTest(cmd=cmd):
                check(cmd)

    def test_plain_sudo_is_still_privileged(self):
        _, privileged, _ = check("sudo apt-get install htop")
        self.assertTrue(privileged)


class WrappedPrivilegedAndDangerous(unittest.TestCase):
    def test_env_sudo_is_privileged(self):
        _, privileged, _ = check("env sudo rm -rf /etc/foo")
        self.assertTrue(privileged)

    def test_nice_apt_is_privileged(self):
        _, privileged, _ = check("nice apt-get install htop")
        self.assertTrue(privileged)

    def test_timeout_systemctl_is_privileged(self):
        _, privileged, _ = check("timeout 60 systemctl stop cron")
        self.assertTrue(privileged)

    def test_env_dd_is_dangerous(self):
        _, _, danger = check("env dd if=/dev/zero of=/dev/sda")
        self.assertEqual(danger, "dd")

    def test_xargs_rm_outside_project_refused(self):
        # without sudo, deleting outside the project is simply refused
        with self.assertRaises(Refused):
            check("xargs rm -rf /home")

    def test_xargs_rm_outside_project_with_sudo_is_dangerous(self):
        _, privileged, danger = check("xargs sudo rm -rf /opt/old")
        self.assertTrue(privileged)
        self.assertTrue(danger)


class WrappedShellsAndDeletes(unittest.TestCase):
    def test_env_sh_c_refused(self):
        with self.assertRaises(Refused):
            check('env sh -c "echo hi"')

    def test_timeout_sh_c_refused(self):
        with self.assertRaises(Refused):
            check('timeout 5 sh -c "echo hi"')

    def test_find_exec_sh_c_refused(self):
        with self.assertRaises(Refused):
            check('find . -exec sh -c "echo x" ;')

    def test_env_rm_outside_project_refused(self):
        with self.assertRaises(Refused):
            check("env rm -rf /home")


class ConfigBansCannotBeWrapped(unittest.TestCase):
    def test_banned_program_refused_directly(self):
        with self.assertRaises(Refused):
            check("curl https://example.com", cfg(no_allow_command={"curl"}))

    def test_banned_program_refused_inside_env(self):
        with self.assertRaises(Refused):
            check("env curl https://example.com", cfg(no_allow_command={"curl"}))


class ParentDirectoryInArguments(unittest.TestCase):
    def test_bash_parent_script_refused(self):
        with self.assertRaises(Refused):
            check("bash ../../outside.sh")

    def test_relative_inside_project_ok(self):
        check("bash scripts/setup.sh")


if __name__ == "__main__":
    unittest.main()
