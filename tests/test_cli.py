"""CLI tests with a fake ssh/virsh. Remote scripts really run, under sh,
in a fake VM home (see stubs.SSH, STUB_SSH_EXEC=1)."""
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests import stubs
from tests.helpers import SandboxTest, free_port, listening_port, write_exe
from tests.ptyrun import run_in_pty

# How vmforai starts claude: through a wrapper that adds the "vmforai login" token.
WRAP = ('[sh] [-c] [p=${1%/*}/display-env; [ -r "$p" ] && { set -a; . "$p"; set +a; }; p=${1%/*}/proxy-env; [ -r "$p" ] && { set -a; . "$p"; set +a; }; [ -r "$1" ] && { set -a; . "$1"; set +a; }; s=${1%/*}/claude-settings.json; shift; c=$1; shift; '
        'case ${c##*/} in claude) [ -r "$s" ] && exec "$c" --settings "$s" "$@" ;; esac; exec "$c" "$@"] [vmforai] ')


def settings(home):
    """The arguments with which vmforai's launcher passes its hooks to Claude."""
    return f" [--settings] [{home}/.config/vmforai/claude-settings.json]"


class ProfileTests(SandboxTest):
    def test_help_and_version(self):
        r = self.sb.run("help", check=0)
        self.assertIn("GET STARTED", r.stdout)
        self.assertIn("vmforai help COMMAND", r.stdout)
        self.assertRegex(self.sb.run("--version", check=0).stdout, r"^vmforai \d+\.\d+\.\d+\n$")

    def test_add_writes_profile(self):
        r = self.sb.run("add", "dev", "--host", "192.168.122.10", "--user", "ubuntu", "--port", "2222",
                        "--dir", "~/work", check=0)
        self.assertIn("Added VM 'dev'", r.stdout)
        text = (self.sb.conf / "hosts" / "dev.conf").read_text()
        self.assertIn("HOST=192.168.122.10\n", text)
        self.assertIn("USER=ubuntu\n", text)
        self.assertIn("PORT=2222\n", text)
        self.assertIn("DIR=~/work\n", text)
        self.assertNotIn("SESSION", text)

    def test_add_updates_existing_profile(self):
        self.sb.run("add", "dev", "--host", "10.0.0.5", "--user", "a", check=0)
        r = self.sb.run("add", "dev", "--port", "2200", "--no-tmux", check=0)
        self.assertIn("Updated VM 'dev'", r.stdout)
        text = (self.sb.conf / "hosts" / "dev.conf").read_text()
        for line in ("HOST=10.0.0.5", "USER=a", "PORT=2200", "SESSION="):
            self.assertIn(line + "\n", text)
        # Clearing the host is allowed only when a libvirt domain remains.
        self.assertEqual(self.sb.run("add", "dev", "--host", "").returncode, 1)
        self.sb.run("add", "dev", "--domain", "testvm", check=0)
        self.sb.run("add", "dev", "--host", "", check=0)
        self.assertNotIn("HOST=", (self.sb.conf / "hosts" / "dev.conf").read_text())

    def test_add_rejects_bad_values(self):
        bad = [
            (["add", "-x"], 2),
            (["add", "dev"], 1),  # no host or domain
            (["add", "../evil", "--host", "h"], 1),
            (["add", "dev", "--host", "-oProxyCommand=x"], 1),
            (["add", "dev", "--host", "h; rm -rf /"], 1),
            (["add", "dev", "--host", "h", "--port", "70000"], 1),
            (["add", "dev", "--host", "h", "--port", "22x"], 1),
            (["add", "dev", "--host", "h", "--user", "-l"], 1),
            (["add", "dev", "--host", "h", "--session", "a:b"], 1),
            (["add", "dev", "--host"], 2),
            (["add", "dev", "other", "--host", "h"], 2),
        ]
        for args, code in bad:
            with self.subTest(args=args):
                r = self.sb.run(*args)
                self.assertEqual(r.returncode, code, r.stderr)
        self.assertFalse((self.sb.conf / "hosts" / "dev.conf").exists())

    def test_profile_is_parsed_not_sourced(self):
        hosts = self.sb.conf / "hosts"
        hosts.mkdir(parents=True)
        pwned = self.sb.tmp / "pwned"
        (hosts / "x.conf").write_text(f"HOST=$(touch {pwned})\n")
        r = self.sb.run("connect", "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid host", r.stderr)
        (hosts / "x.conf").write_text("HOST=h\nEVIL=1\n")
        r = self.sb.run("connect", "x")
        self.assertIn("x.conf:2: unknown key EVIL", r.stderr)
        (hosts / "x.conf").write_text("# comment\n\nHOST=h\nnot a pair\n")
        r = self.sb.run("connect", "x")
        self.assertIn("x.conf:4: expected KEY=VALUE", r.stderr)
        self.assertFalse(pwned.exists())

    def test_list_default_and_rm(self):
        r = self.sb.run("list", check=0)
        self.assertIn("No VMs yet", r.stdout)
        self.sb.run("add", "a", "--host", "10.0.0.1", check=0)
        self.assertRegex(self.sb.run("list", check=0).stdout, r"\* a\s+\S+@10\.0\.0\.1")
        self.sb.run("add", "b", "--domain", "testvm", "--port", "2222", "--user", "u", check=0)
        out = self.sb.run("ls", check=0).stdout
        self.assertRegex(out, r"  a\s")  # two profiles, no default yet
        self.assertIn("u@(auto):2222  libvirt:testvm", out)
        r = self.sb.run()  # several profiles, no default
        self.assertEqual(r.returncode, 1)
        self.assertIn("several VMs and no default", r.stderr)
        self.sb.run("default", "b", check=0)
        self.assertRegex(self.sb.run("list", check=0).stdout, r"\* b\s")
        self.assertEqual(self.sb.run("default", "nope").returncode, 1)
        self.sb.run("rm", "b", check=0)
        self.assertFalse((self.sb.conf / "default").exists())
        self.assertEqual(self.sb.run("rm", "b").returncode, 1)
        self.assertRegex(self.sb.run("list", check=0).stdout, r"\* a\s")

    def test_help_for_every_command(self):
        for cmd in ("create", "delete", "images", "prune", "connect", "shell", "start", "stop", "autostop", "undo", "snapshot", "snapshots", "autosnap", "idle", "notify", "open", "ports", "resize", "ui", "clipboard", "agent", "doctor", "mcp", "add", "setup",
                    "autostart", "list", "default", "desktop", "edit", "forget", "rm"):
            with self.subTest(cmd=cmd):
                r = self.sb.run("help", cmd, check=0)
                self.assertTrue(r.stdout.startswith(f"vmforai {cmd}") or cmd == "list", r.stdout[:80])
                self.assertEqual(self.sb.run(cmd, "--help", check=0).stdout, r.stdout)
        self.assertIn("Examples:", self.sb.run("help", "create", check=0).stdout)
        r = self.sb.run("help", "bogus")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no help for 'bogus'", r.stderr)

    def test_help_flag_placement(self):
        self.sb.run("add", "vm", "--host", "192.0.2.7", "--os", "linux", check=0)
        # anywhere among a command's own options
        self.assertIn("vmforai create NAME", self.sb.run("create", "x", "--help", check=0).stdout)
        self.assertIn("vmforai add NAME", self.sb.run("add", "vm", "--port", "22", "-h", check=0).stdout)
        # but arguments after a VM name belong to Claude
        self.sb.run("vm", "--help", check=0)
        self.assertEqual(self.sb.calls("ssh")[-1]["argv"][-1].split()[-1], "'--help'")
        self.assertIn("vmforai connect", self.sb.run("connect", "--help", check=0).stdout)
        self.sb.run("connect", "vm", "--help", check=0)
        self.assertEqual(self.sb.calls("ssh")[-1]["argv"][-1].split()[-1], "'--help'")
        # and after -- nothing is help
        self.sb.run("add", "vm", "--dir", "x", check=0)
        self.assertEqual(self.sb.run("shell", "vm", "ls", "--help", check=0).stdout, "")

    def test_unknown_command(self):
        r = self.sb.run("frobnicate")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown command or VM: frobnicate", r.stderr)
        self.assertEqual(self.sb.run("--bogus").returncode, 2)

    def test_first_run_without_terminal(self):
        r = self.sb.run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("no VM configured", r.stderr)

    def test_forget_removes_only_this_host_key(self):
        self.sb.run("add", "a", "--host", "10.0.0.1", check=0)
        kh = self.sb.conf / "known_hosts"
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
        kh.write_text(f"vmforai-a {key}\nvmforai-b {key}\n")
        r = self.sb.run("forget", "a", check=0)
        self.assertIn("Forgot", r.stdout)
        self.assertEqual(kh.read_text(), f"vmforai-b {key}\n")
        self.assertFalse((self.sb.conf / "known_hosts.old").exists())


class ConnectTests(SandboxTest):
    def setUp(self):
        super().setUp()
        self.sb.run("add", "vm", "--host", "192.0.2.7", "--user", "dev", "--port", "2222", "--os", "linux", check=0)

    def ssh_argv(self):
        calls = self.sb.calls("ssh")
        self.assertEqual(len(calls), 1, calls)
        return calls[0]["argv"]

    def test_ssh_options(self):
        self.sb.run("vm", check=0)
        argv = self.ssh_argv()
        sep = argv.index("--")
        opts, host, cmd = argv[:sep], argv[sep + 1], argv[sep + 2:]
        self.assertEqual(host, "192.0.2.7")
        self.assertEqual(len(cmd), 1)
        self.assertTrue(cmd[0].startswith("sh -c '"))
        self.assertEqual(opts[opts.index("-l") + 1], "dev")
        self.assertEqual(opts[opts.index("-p") + 1], "2222")
        self.assertIn("HostKeyAlias=vmforai-vm", opts)
        self.assertIn(f"UserKnownHostsFile={self.sb.conf}/known_hosts", opts)
        self.assertIn("StrictHostKeyChecking=accept-new", opts)
        self.assertIn("-T", opts)  # not a terminal here
        self.assertNotIn("-i", opts)  # no key yet: let ssh use its defaults

    def test_key_is_used_when_present(self):
        (self.sb.home / ".ssh").mkdir()
        key = self.sb.home / ".ssh" / "vmforai_ed25519"
        key.write_text("x")
        self.sb.run("connect", "vm", check=0)
        opts = self.ssh_argv()
        self.assertEqual(opts[opts.index("-i") + 1], str(key))
        self.assertIn("IdentitiesOnly=yes", opts)

    def run_remote(self, *args, check=0):
        return self.sb.run(*args, env={"STUB_SSH_EXEC": "1"}, check=check)

    def test_arguments_survive_the_remote_shell(self):
        self.sb.remote_tool("claude")
        tricky = ["it's", "$HOME", "`id`", "a b", '"q"', "back\\slash", "*", ";touch /tmp/x", "", "-p"]
        self.sb.run("add", "vm", "--dir", "~/my 'proj' $x", check=0)
        self.run_remote("vm", "--no-tmux", "--", *tricky)
        expected = "claude" + settings(self.sb.remote_home) + "".join(f" [{a}]" for a in tricky)
        proj = self.sb.remote_home / "my 'proj' $x"
        self.assertTrue(proj.is_dir())
        self.assertEqual(self.sb.remote_log.read_text(), f"{expected} cwd=[{proj}]\n")

    def test_claude_args_after_name(self):
        self.sb.remote_tool("claude")
        self.run_remote("vm", "--no-tmux", "--continue")
        self.run_remote("connect", "--no-tmux", "vm", "--", "--hold")
        self.assertEqual([l.split(" cwd=")[0] for l in self.sb.remote_calls()],
                         [f"claude{settings(self.sb.remote_home)} [--continue]",
                          f"claude{settings(self.sb.remote_home)} [--hold]"])

    def test_tmux_session(self):
        self.sb.remote_tool("claude")
        self.sb.remote_tool("tmux")
        self.run_remote("vm", "--resume")
        claude = self.sb.remote_bin / "claude"
        home = self.sb.remote_home
        self.assertEqual(self.sb.remote_calls(),
                         [f"tmux [new-session] [-A] [-s] [claude] [-c] [{home}] {WRAP}[{home}/.config/vmforai/claude-env] "
                          f"[{claude}] [--resume] cwd=[{home}]"])
        self.sb.clear_log()
        self.sb.run("add", "vm", "--session", "work", check=0)
        self.run_remote("vm")
        self.assertIn("[-s] [work]", self.sb.remote_calls()[0])

    def test_claude_on_local_bin_is_found(self):
        write_exe(self.sb.remote_home / ".local" / "bin" / "claude", self.sb.recorder.read_text())
        self.run_remote("vm", "--no-tmux")
        self.assertTrue(self.sb.remote_calls()[0].startswith(f"claude{settings(self.sb.remote_home)} cwd="))

    def test_missing_claude(self):
        r = self.run_remote("vm", check=127)
        self.assertIn("Claude Code is not installed on the VM. Run: vmforai setup vm", r.stderr)

    def test_unknown_term_falls_back(self):
        self.sb.remote_tool("tput", "#!/bin/sh\n[ \"$2\" = xterm-256color ]\n")
        self.sb.remote_tool("claude", '#!/bin/sh\necho "TERM=$TERM" >>"$STUB_REMOTE_LOG"\n')
        self.sb.run("vm", "--no-tmux", env={"STUB_SSH_EXEC": "1", "TERM": "weird-term"}, check=0)
        self.assertEqual(self.sb.remote_calls(), ["TERM=xterm-256color"])

    def test_ssh_failure_hint_and_hold_without_terminal(self):
        r = self.sb.run("connect", "--hold", "vm", env={"STUB_SSH_RC": "255"}, timeout=10)
        self.assertEqual(r.returncode, 255)
        self.assertIn("SSH connection to dev@192.0.2.7:2222 failed. Try: vmforai doctor vm", r.stderr)

    def test_remote_exit_code_is_returned(self):
        self.assertEqual(self.sb.run("vm", env={"STUB_SSH_RC": "3"}).returncode, 3)

    def test_shell(self):
        self.sb.run("shell", "vm", check=0)
        self.assertIn("VMFORAI_NO_AUTOSTART=1 VMAGENTS_NO_AUTOSTART=1 VMCLAUDE_NO_AUTOSTART=1 exec", self.ssh_argv()[-1])
        self.sb.clear_log()
        self.sb.run("shell", "vm", "uname", "-a", check=0)
        self.assertEqual(self.sb.calls("ssh")[0]["argv"][-3:], ["192.0.2.7", "uname", "-a"])

    def test_single_profile_is_the_default(self):
        self.sb.run(check=0)
        self.assertEqual(self.ssh_argv()[self.ssh_argv().index("--") + 1], "192.0.2.7")


class LibvirtTests(SandboxTest):
    def setUp(self):
        super().setUp()
        self.listener, self.port = listening_port()
        self.addCleanup(self.listener.close)
        self.sb.run("add", "vm", "--domain", "testvm", "--port", str(self.port), "--uri", "qemu:///session",
                    check=0)

    def virsh_cmds(self):
        return [c["argv"][2] for c in self.sb.calls("virsh")]

    def test_starts_vm_and_waits_for_ip(self):
        self.sb.set_vm("shut off", ip="127.0.0.1", polls=1)
        r = self.sb.run("vm", check=0)
        self.assertIn("Starting VM testvm", r.stderr)
        self.assertIn("Waiting for testvm to get an IP address", r.stderr)
        cmds = self.virsh_cmds()
        self.assertEqual(cmds[:4], ["domstate", "snapshot-create-as", "snapshot-list", "start"])
        self.assertIn("domifaddr", cmds)
        self.assertTrue(all(c["argv"][:2] == ["-c", "qemu:///session"] for c in self.sb.calls("virsh")))
        argv = self.sb.calls("ssh")[0]["argv"]
        self.assertEqual(argv[argv.index("--") + 1], "127.0.0.1")

    def test_vm_that_stops_while_booting_fails_fast(self):
        self.sb.set_vm("shut off", ip="")
        r = self.sb.run("vm", env={"STUB_VIRSH_START_DIES": "1", "VMFORAI_BOOT_TIMEOUT": "60"}, timeout=20)
        self.assertEqual(r.returncode, 1)
        self.assertIn("VM testvm stopped while booting (state: shut off)", r.stderr)
        self.assertIn("virsh -c qemu:///session console testvm", r.stderr)

    def test_running_vm_is_not_started(self):
        self.sb.set_vm("running")
        self.sb.run("vm", check=0)
        self.assertNotIn("start", self.virsh_cmds())

    def test_paused_vm_is_resumed(self):
        self.sb.set_vm("paused")
        r = self.sb.run("vm", check=0)
        self.assertIn("resume", self.virsh_cmds())
        self.assertIn("Resuming", r.stderr)

    def test_fixed_host_skips_ip_detection(self):
        self.sb.run("add", "vm", "--host", "127.0.0.1", check=0)
        self.sb.set_vm("shut off", ip="")
        self.sb.run("vm", check=0)
        self.assertNotIn("domifaddr", self.virsh_cmds())
        self.assertIn("start", self.virsh_cmds())

    def test_no_ip_times_out_and_loopback_is_ignored(self):
        self.sb.set_vm("running", ip="")
        r = self.sb.run("vm", env={"VMFORAI_BOOT_TIMEOUT": "2"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("has no IPv4 address after 2s", r.stderr)
        self.assertEqual(self.sb.calls("ssh"), [])

    def test_no_ip_with_ufw_on_says_how_to_allow_it(self):
        write_exe(self.sb.bin / "systemctl", "#!/bin/sh\n[ \"$*\" = \"is-active --quiet ufw\" ]\n")
        self.sb.set_vm("running", ip="")
        r = self.sb.run("vm", env={"VMFORAI_BOOT_TIMEOUT": "2"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("has no IPv4 address after 2s", r.stderr)
        self.assertIn("sudo ufw allow in on virbr0 && sudo ufw route allow in on virbr0", r.stderr)

    def test_closed_port_times_out(self):
        self.sb.run("add", "vm", "--port", str(free_port()), check=0)
        self.sb.set_vm("running")
        r = self.sb.run("vm", env={"VMFORAI_BOOT_TIMEOUT": "2"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("no SSH server answers on 127.0.0.1:", r.stderr)
        self.assertIn("after 2s", r.stderr)

    def test_silent_port_is_not_ready(self):
        silent, port = listening_port(banner=None)
        self.addCleanup(silent.close)
        self.sb.run("add", "vm", "--port", str(port), check=0)
        self.sb.set_vm("running")
        r = self.sb.run("vm", env={"VMFORAI_BOOT_TIMEOUT": "2"})
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"no SSH server answers on 127.0.0.1:{port}", r.stderr)
        self.assertEqual(self.sb.calls("ssh"), [])

    def test_unknown_domain(self):
        self.sb.run("add", "vm", "--domain", "nope", check=0)
        self.sb.set_vm("running")
        r = self.sb.run("vm")
        self.assertEqual(r.returncode, 1)
        self.assertIn("libvirt: error: failed to get domain 'nope'", r.stderr)

    def test_missing_virsh(self):
        os.remove(self.sb.bin / "virsh")
        for tool in ("bash", "mkdir", "mktemp", "mv", "rm", "grep", "id", "awk", "timeout", "sleep", "cat"):
            os.symlink(shutil.which(tool), self.sb.bin / tool)
        self.sb.set_vm("running")
        r = self.sb.run("vm", env={"PATH": str(self.sb.bin)})
        self.assertEqual(r.returncode, 1)
        self.assertIn("virsh not found, but profile 'vm' uses libvirt domain 'testvm'", r.stderr)


class SetupTests(SandboxTest):
    def setUp(self):
        super().setUp()
        self.sb.run("add", "vm", "--host", "192.0.2.7", "--os", "linux", check=0)
        self.batch = self.sb.tmp / "batch"
        self.env = {"STUB_SSH_EXEC": "1", "STUB_SSH_BATCH_RC": str(self.batch)}
        for tool in ("sudo", "apt-get"):
            self.sb.remote_tool(tool)
        self.sb.remote_tool("curl", stubs.CURL)

    def setup(self, *args, batch="1 0", check=0):
        self.batch.write_text(batch)
        return self.sb.run("setup", "vm", *args, env=self.env, check=check)

    def test_full_setup(self):
        r = self.setup()
        key = self.sb.home / ".ssh" / "vmforai_ed25519"
        self.assertTrue(key.exists())
        self.assertEqual(oct(key.parent.stat().st_mode & 0o777), "0o700")
        pub = (self.sb.home / ".ssh" / "vmforai_ed25519.pub").read_text().strip()
        ak = self.sb.remote_home / ".ssh" / "authorized_keys"
        self.assertEqual(ak.read_text(), pub + "\n")
        self.assertEqual(oct(ak.stat().st_mode & 0o777), "0o600")
        calls = self.sb.remote_calls()
        self.assertIn("sudo [apt-get] [update] [-qq]", calls[0])
        self.assertIn("sudo [env] [DEBIAN_FRONTEND=noninteractive] [apt-get] [install] [-y] [-qq] [-o] [Dpkg::Use-Pty=0] [tmux]", calls[1])
        self.assertTrue(calls[2].startswith("curl -fsSL https://claude.ai/install.sh"))
        self.assertTrue((self.sb.remote_home / ".local" / "bin" / "claude").exists())
        self.assertIn("Installed Claude Code: 9.9.9 (Claude Code)", r.stdout + r.stderr)
        self.assertTrue((self.sb.remote_home / ".config" / "vmforai" / "autostart.sh").exists())
        self.assertIn("# >>> vmforai autostart >>>", (self.sb.remote_home / ".bashrc").read_text())
        self.assertIn("Done. Open Claude on the VM with:  vmforai vm", r.stdout)

    def test_setup_is_idempotent(self):
        (self.sb.remote_home / ".bashrc").write_text("alias ll='ls -l'")  # no trailing newline
        self.setup()
        self.sb.remote_tool("tmux")
        r = self.setup(batch="0")
        self.assertIn("key login already works", r.stderr)
        self.assertIn("Claude Code is already installed", r.stdout + r.stderr)
        self.assertEqual(len((self.sb.remote_home / ".ssh" / "authorized_keys").read_text().splitlines()), 1)
        rc = (self.sb.remote_home / ".bashrc").read_text()
        self.assertTrue(rc.startswith("alias ll='ls -l'\n# >>> vmforai autostart >>>\n"), rc)
        self.assertEqual(rc.count("vmforai autostart >>>"), 1)
        # ~/.local/bin (where Claude Code lives) is on PATH in every shell, not only for autostart.
        r = subprocess.run(["bash", "-c", f". {self.sb.remote_home}/.bashrc; echo $PATH"], capture_output=True, text=True,
                           env={"HOME": str(self.sb.remote_home), "PATH": "/usr/bin:/bin"}, stdin=subprocess.DEVNULL)
        self.assertEqual(r.stdout, f"{self.sb.remote_home}/.local/bin:/usr/bin:/bin\n")

    def test_authorized_keys_without_final_newline(self):
        ak = self.sb.remote_home / ".ssh" / "authorized_keys"
        ak.parent.mkdir()
        ak.write_text("ssh-rsa AAAAB3 other@host")
        self.setup("--no-install", "--no-autostart")
        lines = ak.read_text().splitlines()
        self.assertEqual(lines[0], "ssh-rsa AAAAB3 other@host")
        self.assertTrue(lines[1].startswith("ssh-ed25519 "))
        self.assertEqual(self.sb.remote_calls(), [])
        self.assertFalse((self.sb.remote_home / ".bashrc").exists())

    def test_key_still_rejected(self):
        r = self.setup(batch="1 1", check=1)
        self.assertIn("key login still fails", r.stderr)

    def test_existing_custom_key_is_required(self):
        self.sb.run("add", "vm", "--key", "~/.ssh/mine", check=0)
        r = self.setup(check=1)
        self.assertIn(f"key file not found: {self.sb.home}/.ssh/mine", r.stderr)

    def test_no_sudo(self):
        os.remove(self.sb.remote_bin / "sudo")
        r = self.setup()
        self.assertIn("cannot install tmux git without root or sudo", r.stderr)
        self.assertIn("tmux is not available", r.stderr)

    def test_installer_failure(self):
        self.sb.remote_tool("curl", "#!/bin/sh\nexit 22\n")
        r = self.setup(check=1)
        self.assertIn("Claude Code installation failed", r.stderr)

    def test_autostart_off_keeps_other_lines(self):
        self.setup("--no-install")
        rc = self.sb.remote_home / ".bashrc"
        rc.write_text("export A=1\n" + rc.read_text() + "export B=2\n")
        self.sb.run("autostart", "vm", "off", env=self.env, check=0)
        self.assertEqual(rc.read_text(), "export A=1\nexport B=2\n")
        self.assertFalse((self.sb.remote_home / ".config" / "vmforai" / "autostart.sh").exists())
        self.assertEqual(self.sb.run("autostart", "vm", "maybe").returncode, 2)

    def test_zshrc_gets_the_hook_too(self):
        (self.sb.remote_home / ".zshrc").write_text("# zsh\n")
        self.setup("--no-install")
        self.assertIn("vmforai/autostart.sh", (self.sb.remote_home / ".zshrc").read_text())


class AutostartHookTests(SandboxTest):
    """The login hook, in a real interactive shell on a pseudo-terminal."""

    def setUp(self):
        super().setUp()
        self.sb.run("add", "vm", "--host", "192.0.2.7", "--dir", "~/code", "--os", "linux", check=0)
        self.batch = self.sb.tmp / "batch"
        self.batch.write_text("0")
        self.sb.run("setup", "vm", "--no-install",
                    env={"STUB_SSH_EXEC": "1", "STUB_SSH_BATCH_RC": str(self.batch)}, check=0)
        (self.sb.remote_home / "code").mkdir()
        self.sb.remote_tool("claude")

    def login(self, shell="bash", extra_env=None, args=("-i",)):
        path = shutil.which(shell)
        if not path:
            self.skipTest(f"{shell} not installed")
        env = {
            "HOME": str(self.sb.remote_home),
            "PATH": str(self.sb.remote_bin),
            "SSH_CONNECTION": "10.0.0.1 50000 10.0.0.2 22",
            "TERM": "dumb",
            "SHELL": path,
            "STUB_REMOTE_LOG": str(self.sb.remote_log),
        }
        env.update(extra_env or {})
        # A known prompt, set after the hook ran; type "exit" only once it shows
        # (zsh drops input typed before its line editor starts).
        for rc in (".bashrc", ".zshrc"):
            f = self.sb.remote_home / rc
            if f.exists() and "READY" not in f.read_text():
                f.write_text(f.read_text() + "PS1='READY''> '\n")
        out = run_in_pty([path, *args], env, cwd=str(self.sb.remote_home), answers=[("READY> ", "exit\n")])
        return self.sb.remote_calls(), out

    def test_bash_login_opens_claude_in_tmux(self):
        self.sb.remote_tool("tmux")
        calls, _ = self.login()
        code = self.sb.remote_home / "code"
        self.assertEqual(len(calls), 1, calls)
        self.assertTrue(calls[0].startswith(
            f"tmux [new-session] [-A] [-s] [claude] [-c] [{code}] {WRAP}"
            f"[{self.sb.remote_home}/.config/vmforai/claude-env] [{self.sb.remote_bin}/claude]"), calls)

    def test_without_tmux_claude_runs_in_dir(self):
        calls, _ = self.login()
        self.assertEqual(calls, [f"claude cwd=[{self.sb.remote_home / 'code'}]"])

    def test_zsh_login(self):
        (self.sb.remote_home / ".zshrc").write_text("")
        self.sb.run("autostart", "vm", "on", env={"STUB_SSH_EXEC": "1"}, check=0)
        # Skip /etc/zsh/zshrc: Ubuntu's runs compinit, which asks about the temp dir.
        calls, _ = self.login("zsh", args=("--no-globalrcs", "-i"))
        self.assertEqual(calls, [f"claude cwd=[{self.sb.remote_home / 'code'}]"])

    def test_skipped_cases(self):
        cases = [
            {"VMFORAI_NO_AUTOSTART": "1"},
            {"LC_VMFORAI": "off"},
            {"TMUX": "/tmp/tmux-1/default,1,0"},
            {"SSH_CONNECTION": ""},
        ]
        for env in cases:
            with self.subTest(env=env):
                calls, _ = self.login(extra_env=env)
                self.assertEqual(calls, [])

    def test_non_interactive_command_is_untouched(self):
        env = {"HOME": str(self.sb.remote_home), "PATH": str(self.sb.remote_bin),
               "SSH_CONNECTION": "1 2 3 4", "STUB_REMOTE_LOG": str(self.sb.remote_log)}
        rc = self.sb.remote_home / ".bashrc"
        r = subprocess.run(["bash", "-c", f". {rc}; echo hi"], env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True)
        self.assertEqual(r.stdout, "hi\n")
        self.assertEqual(self.sb.remote_calls(), [])


class DoctorAndDesktopTests(SandboxTest):
    def test_doctor_all_good(self):
        listener, port = listening_port()
        self.addCleanup(listener.close)
        self.sb.run("add", "vm", "--host", "127.0.0.1", "--port", str(port), check=0)
        (self.sb.home / ".ssh").mkdir()
        (self.sb.home / ".ssh" / "vmforai_ed25519").write_text("x")
        batch = self.sb.tmp / "batch"
        batch.write_text("0")
        env = {"STUB_SSH_EXEC": "1", "STUB_SSH_BATCH_RC": str(batch)}
        r = self.sb.run("doctor", "vm", env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  Claude Code not installed on the VM", r.stdout)
        self.assertIn("warn  tmux not installed", r.stdout)
        self.sb.remote_tool("claude")
        self.sb.remote_tool("tmux")
        r = self.sb.run("doctor", "vm", env=env, check=0)
        self.assertIn(f"ok    SSH server answers on 127.0.0.1:{port}", r.stdout)
        self.assertIn("ok    Claude Code on the VM: 9.9.9 (Claude Code)", r.stdout)
        self.assertIn("ok    tmux on the VM: tmux 3.5", r.stdout)
        self.assertIn("All good.", r.stdout)

    def test_doctor_stopped_vm(self):
        self.sb.run("add", "vm", "--domain", "testvm", check=0)
        self.sb.set_vm("shut off")
        r = self.sb.run("doctor", "vm")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  libvirt domain testvm: shut off (connecting starts it)", r.stdout)
        self.assertNotIn("start", [c["argv"][2] for c in self.sb.calls("virsh")])

    def test_doctor_unreachable(self):
        self.sb.run("add", "vm", "--host", "127.0.0.1", "--port", str(free_port()), check=0)
        r = self.sb.run("doctor", "vm")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  no SSH server answers on 127.0.0.1:", r.stdout)
        self.assertEqual([c["argv"] for c in self.sb.calls("ssh")], [["-V"]])  # never connected

    def test_desktop_launcher(self):
        self.sb.run("add", "my-vm", "--host", "h", check=0)
        r = self.sb.run("desktop", "my-vm", check=0)
        f = self.sb.home / ".local" / "share" / "applications" / "vmforai-my-vm.desktop"
        text = f.read_text()
        self.assertIn(str(f), r.stdout)
        self.assertIn('Exec="%s" connect --hold my-vm\n' % Path(os.path.realpath(
            Path(__file__).parent.parent / "vmforai")), text)
        self.assertIn("Terminal=true\n", text)
        if shutil.which("desktop-file-validate"):
            v = subprocess.run(["desktop-file-validate", str(f)], capture_output=True, text=True)
            self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        self.sb.run("rm", "my-vm", check=0)
        self.assertFalse(f.exists())

    def test_desktop_escaping(self):
        r = subprocess.run(["bash", "-c", 'source <(sed "/^main \\"\\$@\\"; exit$/d" "$1"); desktop_escape "$2"',
                            "_", str(Path(__file__).parent.parent / "vmforai"), 'a "b" $c `d` \\e 50%'],
                           capture_output=True, text=True, env={"HOME": str(self.sb.home), "PATH": "/usr/bin:/bin"})
        self.assertEqual(r.stdout, '"a \\"b\\" \\$c \\`d\\` \\\\e 50%%"')


class FirstRunTests(SandboxTest):
    def test_wizard_adds_and_connects(self):
        self.sb.set_vm("shut off")
        port = free_port()  # refused at once, so OS detection does not wait
        env = dict(self.sb.env)
        out = run_in_pty(
            [str(Path(__file__).parent.parent / "vmforai")], env, cwd=str(self.sb.tmp),
            answers=[
                ("Name for this VM", "box\n"),
                ("libvirt VM to use", "\n"),
                ("Host or IP of the VM", "127.0.0.1\n"),
                ("User on the VM", "tester\n"),
                ("SSH port", f"{port}\n"),
                ("Set up the VM now", "n\n"),
            ])
        self.assertIn("libvirt VMs: testvm", out)
        text = (self.sb.conf / "hosts" / "box.conf").read_text()
        self.assertIn("HOST=127.0.0.1\n", text)
        self.assertIn("USER=tester\n", text)
        self.assertIn(f"PORT={port}\n", text)
        argv = self.sb.calls("ssh")[0]["argv"]
        self.assertIn("-t", argv)  # a real terminal this time
        self.assertEqual(argv[argv.index("--") + 1], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
