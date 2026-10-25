"""Templates: what "create --template" sets (built in and your own), the
list, and the script that installs a template's packages on the VM (run
under sh against fake apt-get, pacman and sudo)."""
import json
import os
import subprocess

from tests.helpers import write_exe
from tests.test_create import CreateBase
from tests.test_idle_notify import var

RECORD = '#!/bin/sh\necho "$(basename "$0") $*" >>"$LOG"\n'


class TemplateTests(CreateBase):
    def virt_install(self):
        return next(c["argv"] for c in self.sb.calls() if c["prog"] == "virt-install")

    def test_create_with_a_template(self):
        self.create("web", "--template", "ml", "--cpus", "4")  # an option changes it
        argv = self.virt_install()
        self.assertEqual(argv[argv.index("--memory") + 1], "16384")
        self.assertEqual(argv[argv.index("--vcpus") + 1], "4")
        vol = next(c["argv"] for c in self.sb.calls() if c["prog"] == "virsh" and c["argv"][:3] == ["vol-create-as", "default", "web.qcow2"])
        self.assertEqual(vol[3], "60G")
        self.assertIn("PORTS=8888\n", (self.sb.conf / "hosts" / "web.conf").read_text())

    def test_your_own_template(self):
        d = self.sb.conf / "templates"
        d.mkdir(parents=True)
        (d / "stack.conf").write_text("# ours\nDESCRIPTION=Our stack\nIMAGE=debian13\nMEMORY=8192\nPORTS=3000\n"
                                      "PACKAGES=node postgresql\nSETUP=npm i -g pnpm\n")
        out = self.sb.run("templates", check=0).stdout
        self.assertRegex(out, r"\nstack +debian13 +8 GB +2 +20 GB +3000 +Our stack\n")
        self.assertRegex(out, r"\nnode +debian13 +4 GB +2 +20 GB +3000,5173 +Node.js web app")
        t = {x["name"]: x for x in json.loads(self.sb.run("templates", "--json", check=0).stdout)}
        self.assertEqual(t["stack"]["packages"], ["node", "postgresql"])
        self.assertEqual(t["stack"]["setup"], "npm i -g pnpm")
        self.assertEqual(sorted(t), ["desktop", "go", "ml", "node", "python", "rust", "stack"])
        self.assertEqual([x["name"] for x in json.loads(self.sb.run("images", "--json", check=0).stdout)["templates"]],
                         ["node", "python", "ml", "go", "rust", "desktop", "stack"])
        self.create("s", "--template", "stack")
        argv = self.virt_install()
        self.assertEqual(argv[argv.index("--memory") + 1], "8192")
        (d / "bad.conf").write_text("COLOUR=blue\n")
        self.assertIn("unknown key COLOUR", self.sb.run("create", "x", "--template", "bad", "--no-setup").stderr)
        self.assertIn("no template named 'nope'", self.sb.run("create", "x", "--template", "nope", "--no-setup").stderr)
        self.assertIn("vmforai templates", self.sb.run("help", "templates", check=0).stdout)


class TemplateScriptTests(CreateBase):
    def run_script(self, manager, *args):
        bin_ = self.sb.tmp / "vmbin"
        log = self.sb.tmp / "calls"
        write_exe(bin_ / manager, RECORD)
        write_exe(bin_ / "sudo", '#!/bin/sh\nexec "$@"\n')
        write_exe(bin_ / "npm", RECORD)
        # Only the fake VM's tools: never this computer's own apt-get or pacman.
        env = dict(os.environ, PATH=f"{bin_}:{self.sb.remote_bin}", HOME=str(self.sb.remote_home), LOG=str(log))
        r = subprocess.run(["sh", "-c", var("TEMPLATE_SH"), "x", *args], env=env, capture_output=True, text=True)
        return r, (log.read_text() if log.exists() else "")

    def test_debian_names(self):
        r, calls = self.run_script("apt-get", "npm i -g pnpm", "node", "python", "build", "htop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("apt-get install -y -qq -o Dpkg::Use-Pty=0 nodejs npm python3 python3-venv python3-pip "
                      "build-essential htop", calls)
        self.assertIn("npm i -g pnpm", calls)  # SETUP, at the end

    def test_other_distributions(self):
        for manager, line in (("dnf", "dnf install -y nodejs nodejs-npm python3 python3-pip gcc gcc-c++ make golang "
                                      "java-latest-openjdk-devel"),
                              ("zypper", "zypper --non-interactive install nodejs-default npm-default python3 python3-pip "
                                         "gcc gcc-c++ make go java-devel"),
                              ("apk", "apk add nodejs npm python3 py3-pip build-base go openjdk21")):
            with self.subTest(manager):
                for f in ("dnf", "zypper", "apk"):
                    (self.sb.tmp / "vmbin" / f).unlink(missing_ok=True)
                (self.sb.tmp / "calls").unlink(missing_ok=True)
                r, calls = self.run_script(manager, "", "node", "python", "build", "go", "java")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(line, calls)

    def test_arch_names(self):
        r, calls = self.run_script("pacman", "", "python", "build", "go")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pacman -S --needed --noconfirm python python-pip base-devel go", calls)
