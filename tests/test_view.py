"""The desktop of a Linux VM: the tool "desktop" sets it up (against a fake
apt-get, sudo, loginctl and systemctl), view brings it here, in a VNC viewer or
through the bridge to the browser (against the fake ssh, whose forwarded port
answers "hello from the VM"). The real thing was tried on a Debian 13 VM made
with --template desktop."""
import base64
import os
import socket
import time
from urllib.parse import urlsplit

from tests.helpers import VMFORAI, write_exe
from tests.ptyrun import run_in_pty
from tests.test_stop import Base
from tests.test_tools import RECORD


def http(port, request):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
        c.sendall(request.encode())
        data = b""
        while chunk := c.recv(65536):
            data += chunk
        return data


def ws_first_message(port, token):
    """Opens the bridge's WebSocket and returns the first message from the VM."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
        key = base64.b64encode(os.urandom(16)).decode()
        c.sendall(f"GET /ws?t={token} HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += c.recv(1)
        assert head.startswith(b"HTTP/1.1 101"), head
        b1, b2 = c.recv(2)
        return c.recv(b2 & 127)


class DesktopToolTests(Base):
    def setUp(self):
        super().setUp()
        for tool in ("apt-get", "sudo", "loginctl", "systemctl"):
            write_exe(self.sb.remote_bin / tool, RECORD + ('exec "$@"\n' if tool == "sudo" else ""))
        self.sb.env.update(STUB_SSH_EXEC="1")

    def test_install(self):
        r = self.sb.run("tools", "vm", "desktop", check=0)
        self.assertIn("Done: desktop on vm.", r.stdout)
        calls = (self.sb.remote_home / "calls").read_text()
        self.assertRegex(calls, r"apt-get install .* xfce4 xfce4-terminal dbus-x11 tigervnc-standalone-server "
                                r"firefox(-esr)?\n")
        self.assertIn("loginctl enable-linger", calls)
        self.assertIn("systemctl --user enable --now vmforai-desktop.service", calls)
        d = self.sb.remote_home / ".config/vmforai"
        self.assertEqual((d / "display-env").read_text(), "DISPLAY=:1\n")
        self.assertTrue(os.access(d / "desktop.sh", os.X_OK))
        self.assertIn(":1 -geometry 1600x900 -depth 24 -localhost", (d / "desktop.sh").read_text())
        unit = (self.sb.remote_home / ".config/systemd/user/vmforai-desktop.service").read_text()
        self.assertIn("ExecStart=%h/.config/vmforai/desktop.sh", unit)

    def test_template_and_help(self):
        self.assertRegex(self.sb.run("templates", check=0).stdout, r"desktop +debian13 +6 GB +4 +30 GB")
        self.assertIn("vmforai view [NAME]", self.sb.run("help", "view", check=0).stdout)

    def test_no_desktop_yet(self):
        r = self.sb.run("view", "vm")
        self.assertEqual(r.returncode, 1)
        self.assertIn("vm has no desktop yet; add one (XFCE, about 1 GB): vmforai tools vm desktop", r.stderr)


class ViewTests(Base):
    def view(self, *args, answers=()):
        return run_in_pty([str(VMFORAI), "view", *args], self.sb.env, str(self.sb.tmp), answers=answers)

    def forwarded(self):
        fwd = [c for c in self.sb.calls("ssh") if "-N" in c["argv"]][0]["argv"]
        local = next(a for a in fwd if a.endswith(":localhost:5901"))
        return int(local.split(":")[1])

    def test_in_a_viewer(self):
        write_exe(self.sb.bin / "remote-viewer", f'#!/bin/sh\necho "$@" >>{self.sb.tmp}/viewer\n')
        out = self.view("vm")
        self.assertIn("The screen of vm, in remote-viewer.", out)
        self.assertIn(f"vnc://127.0.0.1:{self.forwarded()}", (self.sb.tmp / "viewer").read_text())
        self.assertIn("VM testvm is off.", out)  # autostop afterwards
        self.assertEqual(self.state(), "shut off")

    def test_in_the_browser(self):
        write_exe(self.sb.bin / "remote-viewer", f'#!/bin/sh\necho "$@" >>{self.sb.tmp}/viewer\n')
        write_exe(self.sb.bin / "xdg-open", f'#!/bin/sh\necho "$@" >>{self.sb.tmp}/opened\n')
        seen = {}

        def look_then_stop():
            for _ in range(50):
                if (self.sb.tmp / "opened").exists():
                    break
                time.sleep(0.1)
            u = urlsplit((self.sb.tmp / "opened").read_text().strip())
            seen["page"] = http(u.port, "GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            seen["wrong key"] = http(u.port, "GET /ws?t=nope HTTP/1.1\r\nHost: x\r\n\r\n")
            seen["vnc"] = ws_first_message(u.port, u.fragment)
            return "\x03"
        out = self.view("vm", "--browser", "--keep", answers=[("closing that tab stops it", look_then_stop)])
        self.assertIn("The screen of vm: http://127.0.0.1:", out)
        self.assertFalse((self.sb.tmp / "viewer").exists())  # --browser: not the viewer
        self.assertIn(b"200 OK", seen["page"])
        self.assertIn(b"@novnc/novnc@1.7.0/core/rfb.js", seen["page"])
        self.assertIn(b"404", seen["wrong key"])
        self.assertEqual(seen["vnc"], b"hello from the VM\n")
        self.assertEqual(self.state(), "running")  # --keep

    def test_windows(self):
        self.sb.run("add", "w", "--host", "192.0.2.8", "--os", "windows", check=0)
        r = self.sb.run("view", "w")
        self.assertEqual(r.returncode, 1)
        self.assertIn("a Windows VM has a desktop of its own", r.stderr)
