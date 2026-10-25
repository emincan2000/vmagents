"""tools: development tools in a VM, with the distribution's packages on
Linux (the script really runs against a fake apt-get) and Scoop on Windows."""
from tests.helpers import SandboxTest, write_exe
from tests.test_stop import Base
from tests.test_windows import decode

RECORD = '#!/bin/sh\necho "$(basename "$0") $*" >>"$HOME/calls"\n'


class LinuxTests(Base):
    def setUp(self):
        super().setUp()
        for tool in ("apt-get", "sudo"):
            write_exe(self.sb.remote_bin / tool, RECORD + ('exec "$@"\n' if tool == "sudo" else ""))
        self.sb.env.update(STUB_SSH_EXEC="1")

    def test_install(self):
        r = self.sb.run("tools", "vm", "node", "python", "htop", check=0)
        self.assertIn("Done: node python htop on vm.", r.stdout)
        calls = (self.sb.remote_home / "calls").read_text()
        self.assertIn("apt-get install -y -qq -o Dpkg::Use-Pty=0 nodejs npm python3 python3-venv python3-pip htop",
                      calls)

    def test_failure_and_names(self):
        write_exe(self.sb.remote_bin / "apt-get", "#!/bin/sh\nexit 100\n")
        r = self.sb.run("tools", "vm", "node")
        self.assertEqual(r.returncode, 1)
        self.assertIn("installing them on vm failed", r.stderr)
        self.assertEqual(self.sb.run("tools", "vm", "a;b").returncode, 2)
        self.assertIn("Tools for a VM: node python go rust java build desktop", self.sb.run("tools", "vm", check=0).stdout)

    def test_vm_that_was_off_goes_off_again(self):
        self.sb.set_vm("shut off")
        self.sb.run("tools", "vm", "go", check=0)
        self.assertIn("start", self.virsh_cmds())
        self.assertEqual(self.state(), "shut off")


class WindowsTests(SandboxTest):
    def setUp(self):
        super().setUp()
        self.sb.run("add", "w", "--host", "192.0.2.8", "--user", "emin", "--os", "windows", check=0)

    def test_scoop(self):
        self.sb.run("tools", "w", "node", "rust", "java", "7zip", check=0)
        script = decode(self.sb.calls("ssh")[-1]["argv"][-1])[1]
        self.assertIn("$tools = @('node','rust','java','7zip')", script)
        self.assertIn("https://get.scoop.sh", script)
        self.assertIn("-RunAsAdmin", script)
        for name in ('"nodejs-lts"', '"rustup-gnu"', '"java/temurin-lts-jdk"', "scoop bucket add java",
                     "& scoop install @pkgs"):
            self.assertIn(name, script)

    def test_help(self):
        self.assertIn("vmforai tools NAME TOOL", self.sb.run("help", "tools", check=0).stdout)
