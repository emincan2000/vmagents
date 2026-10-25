"""The terminal UI (vmforai ui), driven with Textual's test pilot against a
fake vmforai that answers --json, records what the UI runs, and in the
UI's terminal tabs behaves like a small interactive program. Needs Textual
and pyte (skipped otherwise): uv run --with 'textual>=8,<9' --with pyte
python -m unittest tests.test_ui"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import write_exe
from tests.test_idle_notify import var

try:
    import pyte  # noqa: F401
    import textual  # noqa: F401
    HAVE_TEXTUAL = True
except ImportError:
    HAVE_TEXTUAL = False

FAKE = r'''#!/usr/bin/env python3
import json, os, signal, sys, time
d = os.environ["FAKE_UI_DIR"]
a = sys.argv[1:]
with open(os.path.join(d, "calls.jsonl"), "a") as f:
    f.write(json.dumps(a) + "\n")
def show(name):
    sys.stdout.write(open(os.path.join(d, name)).read())
if a[:2] == ["list", "--json"]: show("list.json")
elif a[:2] == ["images", "--json"]: show("images.json")
elif a[:1] == ["snapshots"]: show("snaps.json")
elif a[:1] == ["create"]:
    print("Creating VM " + a[1], flush=True)
    time.sleep(float(os.environ.get("FAKE_CREATE_SLEEP", "0")))
    while os.environ.get("FAKE_CREATE_GATE") and not os.path.exists(os.environ["FAKE_CREATE_GATE"]):
        time.sleep(0.1)  # until the test lets it end
    print("Done. Open Claude on the VM with:  vmforai " + a[1], flush=True)
    sys.exit(int(os.environ.get("FAKE_CREATE_RC", "0")))
elif a[:1] == ["history"] and "--json" in a:
    print(json.dumps([{"id": "s-new", "time": 1760800000, "start": "", "dir": "/home/me", "messages": 2,
                       "first": "fix the tests"},
                      {"id": "s-old", "time": 1760700000, "start": "", "dir": "/home/me", "messages": 5,
                       "first": "add a health endpoint"}]))
elif a[:1] == ["history"]:
    print("> asked in " + a[2] + "\n\n* Bash(npm test)\nanswered " + a[2] + "\n\nGo on with it: vmforai dev --resume " + a[2])
elif a[:1] == ["changes"]:
    show("changes.txt")
elif a[:2] == ["phone", "notify"]:  # on once turned on
    flag = os.path.join(d, "notes-on")
    if a[2:3] not in (["--status"], ["off"]):
        open(flag, "w").close()
    on = os.path.exists(flag)
    print(json.dumps({"on": True, "topic": "vmforai-t", "sent": True} if on else {"on": False}))
elif a[:1] == ["phone"] and "--json" in a:
    if os.environ.get("FAKE_PHONE_ERR") and "--lan" not in a:
        print("vmforai: Tailscale is not installed; ...", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"on": True, "listen": "lan" if "--lan" in a else "tailscale", "address": "100.64.0.7",
                      "port": 7700, "url": "http://100.64.0.7:7700/#t=" + ("NEW" if "--new" in a else "KEY"),
                      "qr": ["█▀▀█", "█▄▄█"], "ssh": [{"name": "dev", "port": 7701, "user": "me"}]}))
elif a[:1] == ["try"] and "--clean" not in a:
    print("Made 2 copies of dev: dev-t1 dev-t2", flush=True)
    print("Run 3 on dev-t1: Claude Code works on it, without asking (in tmux session run-3).", flush=True)
    print("Run 1 on dev-t2: Codex works on it, without asking (in tmux session run-1).", flush=True)
elif a[:1] == ["project"]:
    print("Sending main of " + a[2] + " to " + a[1] + "...", flush=True)
    sys.exit(0 if "--discard" in a else int(os.environ.get("FAKE_PROJECT_RC", "0")))
elif a[:1] == ["open"]:
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    for p in [x for x in a[2:] if x.isdigit()]:
        print(f"Port {p} of {a[1]}: http://localhost:{p}", flush=True)
    print("Stop with Ctrl-C.", flush=True)
    time.sleep(30)
elif a[:1] in (["shell"], ["login"]) or "--hold" in a:
    # In a terminal tab: says what it is, then echoes lines until "exit".
    print("\x1b[32msession:\x1b[0m " + " ".join(a), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if line == "exit":
            break
        print("you typed: " + line, flush=True)
else:
    print("ok")
'''

VM = {"name": "dev", "default": True, "host": "", "user": "me", "port": 22, "domain": "dev", "uri": "qemu:///system",
      "os": "linux", "dir": "", "session": "claude", "agent": "claude", "agents": ["claude"], "ports": [3000],
      "autostop": True, "notify": True, "clipboard": True, "idle": 30, "autosnap": 3, "created": True,
      "state": "running", "memory_mb": 4096, "cpus": 2, "snapshots": 2}
IMAGES = {"images": [{"name": "debian13", "description": "Debian 13", "downloaded": True},
                     {"name": "arch", "description": "Arch Linux", "downloaded": False}],
          "templates": [{"name": "node", "description": "Node.js web app", "image": "debian13", "memory": 4096,
                         "cpus": 2, "disk": 20, "agents": ["claude"], "ports": [3000, 5173],
                         "packages": ["node", "build"], "setup": ""},
                        {"name": "ml", "description": "Python for ML", "image": "arch", "memory": 65536, "cpus": 8,
                         "disk": 60, "agents": ["claude", "aider"], "ports": [8888], "packages": ["python"],
                         "setup": ""}],
          "host": {"memory_mb": 16384, "memory_available_mb": 9000, "cpus": 8, "libvirt": True, "pool": "default",
                   "pool_available_gb": 100, "missing": [],
                   "agents": ["claude", "codex", "gemini", "opencode", "aider"]}}
CHANGES = """Changes on dev in ~/projects/app (sent: 1a2b3c4):
  5d6e7f8 add the health route
  9a8b7c6 Work not committed on the VM
 src/server.ts | 3 ++-
 1 file changed, 2 insertions(+), 1 deletion(-)

diff --git a/src/server.ts b/src/server.ts
index 1111111..2222222 100644
--- a/src/server.ts
+++ b/src/server.ts
@@ -1,3 +1,4 @@
 const app = express();
-app.listen(3000);
+app.get("/health", (_req, res) => res.json({ ok: true }));
+app.listen(3000);
"""
SNAPS = [{"name": "auto-1", "time": "2026-10-16 09:00:00", "state": "shutoff", "description": ""},
         {"name": "snap-2", "time": "2026-10-16 10:00:00", "state": "running", "description": "before refactor"}]


@unittest.skipUnless(HAVE_TEXTUAL, "needs Textual and pyte")
class UITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="vmforai-ui-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.write(list=[VM, dict(VM, name="srv", default=False, domain="", host="192.0.2.5", state=None,
                                  memory_mb=None, cpus=None, created=False, ports=[])])
        self.write(images=IMAGES, snaps=SNAPS)
        fake = write_exe(self.d / "vmforai", FAKE)
        self.env = {"FAKE_UI_DIR": str(self.d), "VMFORAI_BIN": str(fake), "VMFORAI_CONF_DIR": str(self.d / "conf"),
                    "LANG": "C.UTF-8", "XDG_CACHE_HOME": str(self.d / "cache")}
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.addCleanup(self.restore_env)
        (self.d / "changes.txt").write_text(CHANGES)
        src = self.d / "ui.py"
        src.write_text(var("UI_PY"))
        spec = importlib.util.spec_from_file_location("vmforai_ui", src)
        self.ui = importlib.util.module_from_spec(spec)
        cwd = os.getcwd()
        os.chdir(self.d)  # not in a git repository: no project suggested
        try:
            spec.loader.exec_module(self.ui)
        finally:
            os.chdir(cwd)
        self.app = self.ui.VmforaiUI()

    def restore_env(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def write(self, **files):
        for name, data in files.items():
            (self.d / f"{name}.json").write_text(json.dumps(data))

    def calls(self, first=None):
        f = self.d / "calls.jsonl"
        calls = [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
        return [c for c in calls if first is None or c[:1] == [first]]

    async def wait_for(self, pilot, cond, seconds=5):
        for _ in range(int(seconds * 10)):
            if cond():
                return True
            await pilot.pause(0.1)
        return cond()

    def start(self):
        return self.app.run_test(size=(120, 40))

    async def ready(self, pilot):
        await self.wait_for(pilot, lambda: self.app.vms and self.app.info)

    async def submit(self, pilot):
        """OK in the dialog on top, by Enter in its first field: unlike a click
        it does not depend on where the button is (an error above moves it)."""
        fields = self.app.screen.query(self.ui.Input)
        if not fields:  # a dialog without text fields: its button
            await pilot.click("#ok")
            return
        fields.first().focus()
        await pilot.press("enter")

    def text(self, widget_id):
        return str(self.app.screen.query_one(widget_id).render())

    def screen_text(self, term):
        return "\n".join(term.vt.display) if term.vt else ""

    def tab_labels(self):
        return [t.label.plain for t in self.app.query_one(self.ui.Tabs).query(self.ui.Tab)]

    # --- the list
    async def test_list_and_vim_keys(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            self.assertEqual(self.app.query_one("#vms").row_count, 2)
            details = self.text("#details")
            self.assertIn("4 GB RAM · 2 CPUs · 2 snapshots", details)
            self.assertIn("Ports: 3000 (while connected)", details)
            await pilot.press("j")
            await pilot.pause(0.2)
            self.assertIn("me@192.0.2.5", self.text("#details"))
            await pilot.press("k")
            await pilot.pause(0.2)
            self.assertEqual(self.app.selected_name(), "dev")

    async def test_language(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("L")
            await pilot.pause(0.3)
            self.assertIn("Açılan: [b]Claude Code[/b]".replace("[b]", "").replace("[/b]", ""), self.text("#details"))
            self.assertIn("Yeni VM", self.app.export_screenshot().replace("&#160;", " "))
            self.assertEqual(json.loads((self.d / "conf" / "ui.json").read_text()), {"lang": "tr"})
            await pilot.press("n")  # forms follow
            await pilot.pause(0.3)
            self.assertIn("Oluştur", self.app.export_screenshot().replace("&#160;", " "))
            await pilot.press("escape")
            await pilot.press("L")
            await pilot.pause(0.3)
            self.assertIn("Opens: Claude Code", self.text("#details"))
        # the next start remembers it
        (self.d / "conf" / "ui.json").write_text(json.dumps({"lang": "tr"}))
        app = self.ui.VmforaiUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.wait_for(pilot, lambda: app.vms)
            self.assertEqual(self.ui.LANG, "tr")

    # --- terminals inside the UI
    async def test_open_shell_and_agents_as_tabs(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            self.assertEqual(self.tab_labels(), ["1 VMs"])
            await pilot.press("enter")  # how to open it?
            await pilot.pause(0.3)
            self.assertIsInstance(self.app.screen, self.ui.OpenAs)
            await pilot.press("enter")  # the first: Shell
            await pilot.pause(0.3)
            term = self.app.terminals("dev")[0]
            self.assertEqual(self.app.current_pane(), term.id)
            await self.wait_for(pilot, lambda: "session: shell dev" in self.screen_text(term))
            await pilot.press(*"hello", "enter")
            await self.wait_for(pilot, lambda: "you typed: hello" in self.screen_text(term))
            self.assertIn("you typed: hello", self.screen_text(term))
            # q, n, d, 1... are for the terminal here, not the list
            await pilot.press("q", "1", "enter")
            await self.wait_for(pilot, lambda: "you typed: q1" in self.screen_text(term))
            self.assertTrue(self.app.is_running)
            await pilot.press("f3")  # Codex, as another tab
            await pilot.pause(0.3)
            shell, codex = self.app.terminals("dev")
            self.assertEqual([shell.kind, codex.kind], ["shell", "codex"])
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 dev · Shell", "3 dev · Codex"])
            self.assertEqual(self.app.current_pane(), codex.id)
            await self.wait_for(pilot, lambda: "session: dev --hold --codex" in self.screen_text(codex))
            await pilot.press("f7")  # the tab before
            await pilot.pause(0.2)
            self.assertEqual(self.app.current_pane(), shell.id)
            self.assertTrue(shell.has_focus)
            await pilot.press("f7", "f7")  # the list, then round to the last tab
            await pilot.pause(0.2)
            self.assertEqual(self.app.current_pane(), codex.id)
            await pilot.press("ctrl+right_square_bracket")  # the list; both keep running
            await pilot.pause(0.3)
            self.assertEqual(self.app.current_pane(), "home")
            self.assertTrue(self.app.query_one("#vms").has_focus)
            self.assertIn("Open here: Shell, Codex", self.text("#details"))
            await pilot.press("ctrl+right_square_bracket")  # and back to the last tab
            await pilot.pause(0.2)
            self.assertEqual(self.app.current_pane(), codex.id)
            await pilot.press("ctrl+right_square_bracket", "2")  # on the list, 2: the second tab
            await pilot.pause(0.2)
            self.assertEqual(self.app.current_pane(), shell.id)
            await pilot.press("ctrl+right_square_bracket", "enter")  # Enter on dev: straight back, no question
            await pilot.pause(0.3)
            self.assertEqual(self.app.current_pane(), shell.id)
            await pilot.press(*"exit", "enter")  # the shell ends: its tab closes, Codex shows
            await self.wait_for(pilot, lambda: len(self.app.terminals()) == 1)
            await pilot.pause(0.2)
            self.assertEqual(self.app.current_pane(), codex.id)
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 dev · Codex"])
            await pilot.press(*"exit", "enter")  # the last one: back to the list
            await self.wait_for(pilot, lambda: not self.app.terminals())
            await pilot.pause(0.2)
            self.assertEqual(self.app.current_pane(), "home")
            self.assertEqual(self.tab_labels(), ["1 VMs"])

    async def test_open_as_with_keys(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("l")  # l opens, like Enter
            await pilot.pause(0.3)
            await pilot.press("j", "l")  # Claude Code
            await pilot.pause(0.5)
            term = self.app.terminals("dev")[0]
            await self.wait_for(pilot, lambda: "session: dev --hold --claude" in self.screen_text(term))
            await pilot.press("ctrl+right_square_bracket")
            await pilot.pause(0.2)
            await pilot.press("j", "s")  # s: a shell on srv, directly
            await pilot.pause(0.5)
            term = self.app.terminals("srv")[0]
            await self.wait_for(pilot, lambda: "session: shell srv" in self.screen_text(term))
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 dev · Claude Code", "3 srv · Shell"])
            await pilot.press("f8")  # tabs of all VMs in one row
            await pilot.pause(0.2)
            self.assertEqual(self.app.current_pane(), "home")
            await pilot.press("h")  # nothing on the list
            await pilot.press("q")  # quit asks: terminals are open
            await pilot.pause(0.2)
            self.assertIsInstance(self.app.screen, self.ui.Confirm)
            await pilot.press("y")
            await pilot.pause(0.5)
            self.assertFalse(self.app.is_running)

    async def test_working_and_waiting(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("s")  # a shell on dev
            await self.wait_for(pilot, lambda: self.app.terminals("dev"))
            await pilot.press("f2")  # and Claude Code, shown
            await self.wait_for(pilot, lambda: len(self.app.terminals("dev")) == 2)
            shell, claude = self.app.terminals("dev")
            import time
            now = time.monotonic()
            shell.reads.extend([now - 0.3, now - 0.2, now - 0.1, now])  # output keeps coming: it works
            self.app.watch_terminals()
            await pilot.pause(0.1)
            self.assertTrue(shell.working)
            self.assertRegex(self.tab_labels()[1], r"^2 [⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] dev · Shell$")
            shell.reads.clear()
            shell.last_active = now - 4  # then quiet: waiting for you
            self.app.watch_terminals()
            await pilot.pause(0.1)
            self.assertTrue(shell.attention)
            self.assertEqual(self.tab_labels()[1], "2 ● dev · Shell")
            self.assertIn("dev · Shell is waiting for you (2).", [n.message for n in self.app._notifications])
            await pilot.press("ctrl+right_square_bracket")
            await pilot.pause(0.2)
            self.assertIn("Shell waiting", self.text("#details"))
            self.assertIn("●", str(self.app.main.query_one("#vms").get_row_at(0)[0]))
            await pilot.press("2")  # seen: the dot goes
            await pilot.pause(0.2)
            self.assertFalse(shell.attention)
            self.assertEqual(self.tab_labels()[1], "2 dev · Shell")
            shell.feed(b"\x07")  # a bell where you are: nothing
            await pilot.pause(0.2)
            await pilot.press("f7")  # (to the list)
            claude.feed(b"\x07")  # a bell elsewhere: it needs you
            await pilot.pause(0.2)
            self.assertTrue(claude.attention)
            self.assertFalse(shell.attention)

    async def test_typing_is_not_working(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("s")
            await self.wait_for(pilot, lambda: self.app.terminals("dev"))
            term = self.app.terminals("dev")[0]
            await self.wait_for(pilot, lambda: "session:" in self.screen_text(term))
            await pilot.pause(0.6)
            for word in ("a", "b", "c", "d", "e"):
                await pilot.press(word, "enter")
            await self.wait_for(pilot, lambda: "you typed: e" in self.screen_text(term))
            self.assertFalse(term.busy(__import__("time").monotonic()))  # the echo of your keys

    # --- projects
    async def test_project(self):
        repo = self.d / "code" / "app"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        os.environ["FAKE_PROJECT_RC"] = "5"
        self.addCleanup(os.environ.pop, "FAKE_PROJECT_RC")
        async with self.start() as pilot:
            await self.ready(pilot)
            self.assertIn("Project: none", self.text("#details"))
            await pilot.press("p")
            await pilot.pause(0.3)
            dialog = self.app.screen
            self.assertEqual(dialog.query_one("#f-folder").value, "")
            dialog.query_one("#f-folder").value = str(self.d)
            await self.submit(pilot)
            await pilot.pause(0.1)
            self.assertIn("not in a git repository", str(dialog.query_one("#error").render()))
            dialog.query_one("#f-folder").value = str(repo)
            await self.submit(pilot)
            self.assertTrue(await self.wait_for(pilot, lambda: isinstance(self.app.screen, self.ui.Confirm), 20))
            self.assertIn("not taken back", str(self.app.screen.query_one("Static").render()))
            await pilot.press("y")  # send anyway
            await self.wait_for(pilot, lambda: len(self.calls("project")) == 2)
            self.assertEqual(self.calls("project"), [["project", "dev", str(repo)],
                                                     ["project", "dev", str(repo), "--discard"]])

    async def test_changes(self):
        self.write(list=[dict(VM, project="/home/me/code/app", dir="~/projects/app"),
                         dict(VM, name="srv", default=False, domain="", host="192.0.2.5", state=None)])
        async with self.start() as pilot:
            await self.ready(pilot)
            self.assertIn("Project: /home/me/code/app → ~/projects/app on the VM", self.text("#details"))
            await pilot.press("c")
            await pilot.pause(0.5)
            pane = self.app.main.query_one(self.ui.Changes)
            await self.wait_for(pilot, lambda: pane.state == "changes")
            self.assertEqual(self.app.current_pane(), pane.id)
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 dev · Changes"])
            self.assertIn("1 file changed, 2 insertions(+), 1 deletion(-)", str(pane.query_one("#chstatus").render()))
            log = pane.query_one(self.ui.DiffLog)
            self.assertTrue(log.has_focus)
            self.assertIn("+app.get", "\n".join(str(l.text) for l in log.lines))
            await pilot.press("t")  # take it as a branch
            await self.wait_for(pilot, lambda: self.calls("take"))
            self.assertEqual(self.calls("take"), [["take", "dev"]])
            await pilot.press("P")  # a pull request: asks first
            await pilot.pause(0.2)
            self.assertIsInstance(self.app.screen, self.ui.Confirm)
            await pilot.press("y")
            await self.wait_for(pilot, lambda: len(self.calls("take")) == 2)
            self.assertEqual(self.calls("take")[-1], ["take", "dev", "--pr"])
            await pilot.press("escape")
            await pilot.pause(0.2)
            self.assertEqual(self.app.current_pane(), "home")
            await pilot.press("c")  # again: the same tab, refreshed
            await pilot.pause(0.3)
            self.assertEqual(len(self.app.main.query(self.ui.Changes)), 1)
            self.assertEqual(len(self.calls("changes")), 2)
            await pilot.press("ctrl+right_square_bracket", "j", "c")  # srv has no project
            await pilot.pause(0.3)
            self.assertEqual(len(self.calls("changes")), 2)
        (self.d / "changes.txt").write_text("No changes on dev since the project was sent.\n")
        app = self.ui.VmforaiUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.wait_for(pilot, lambda: app.vms)
            await pilot.press("c")
            await pilot.pause(0.5)
            pane = app.main.query_one(self.ui.Changes)
            await self.wait_for(pilot, lambda: pane.state == "none")
            await pilot.press("t")
            await pilot.pause(0.3)
            self.assertEqual(self.calls("take"), [["take", "dev"], ["take", "dev", "--pr"]])  # nothing to take

    async def test_yolo(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            self.assertIn("Agents: ask before acting", self.text("#details"))
            await pilot.press("Y")  # asks first
            await pilot.pause(0.2)
            self.assertIsInstance(self.app.screen, self.ui.Confirm)
            await pilot.press("y")
            await self.wait_for(pilot, lambda: self.calls("yolo"))
            self.assertEqual(self.calls("yolo"), [["yolo", "dev", "on"]])
        self.write(list=[dict(VM, yolo=True)])
        app = self.ui.VmforaiUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.wait_for(pilot, lambda: app.vms)
            self.assertIn("act without asking", str(app.main.query_one("#details").render()))
            await pilot.press("Y")  # off: no question
            await self.wait_for(pilot, lambda: len(self.calls("yolo")) == 2)
            self.assertEqual(self.calls("yolo")[-1], ["yolo", "dev", "off"])

    async def test_task(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("a")  # which agent? the VM's own first
            await pilot.pause(0.3)
            self.assertIsInstance(self.app.screen, self.ui.Pick)
            await pilot.press("j", "enter")  # Codex
            await pilot.pause(0.3)
            dialog = self.app.screen
            await self.submit(pilot)
            await pilot.pause(0.1)
            self.assertIn("Write the task", str(dialog.query_one("#error").render()))
            dialog.query_one("#f-task").value = "make the tests pass"
            await self.submit(pilot)
            await self.wait_for(pilot, lambda: self.app.terminals("dev"))
            term = self.app.terminals("dev")[0]
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 dev · Task"])
            await self.wait_for(pilot, lambda: "session: run dev --wait --hold --codex -- make the tests pass"
                                in self.screen_text(term))
            await pilot.press("ctrl+right_square_bracket", "a", "enter")  # a second task: a tab of its own
            await pilot.pause(0.3)
            self.app.screen.query_one("#f-task").value = "and the docs"
            await self.submit(pilot)
            await self.wait_for(pilot, lambda: len(self.app.terminals("dev")) == 2)
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 dev · Task", "3 dev · Task"])

    async def test_try_on_copies(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("x")
            await pilot.pause(0.3)
            dialog = self.app.screen
            self.assertEqual(dialog.query_one("#f-count").value, "2")
            dialog.query_one("#f-count").value = "3"
            dialog.query_one("#f-memory").value = "4096"
            dialog.query_one("#f-task").value = "make it faster"
            dialog.query_one("#f-count").focus()
            await pilot.press("enter")  # (the OK button is below the fold at this size)
            await pilot.pause(0.1)
            self.assertIn("do not fit: 9000 MB are free", str(dialog.query_one("#error").render()))
            dialog.query_one("#f-count").value = "2"
            dialog.query_one("#f-memory").value = "2048"
            dialog.query_one("#agent-codex").value = True
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertIsInstance(self.app.screen, self.ui.Confirm)  # dev is running: shut it down first?
            await pilot.press("y")
            await self.wait_for(pilot, lambda: len(self.app.terminals()) == 2)
            self.assertEqual(self.calls("try"), [["try", "dev", "2", "--memory", "2048", "--agents", "claude,codex",
                                                  "--stop", "--", "make it faster"]])
            self.assertEqual(self.tab_labels()[1:], ["2 dev-t1 · Task", "3 dev-t2 · Task"])
            term = self.app.terminals("dev-t1")[0]
            await self.wait_for(pilot, lambda: "session: runs dev-t1 3 -f --hold" in self.screen_text(term))
        # With copies there, x offers to delete them.
        self.write(list=[VM, dict(VM, name="dev-t1", default=False, try_="dev")])
        lst = json.loads((self.d / "list.json").read_text())
        lst[1]["try"] = lst[1].pop("try_")
        self.write(list=lst)
        app = self.ui.VmforaiUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.wait_for(pilot, lambda: app.vms)
            await pilot.press("x")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, self.ui.Confirm)
            await pilot.press("tab", "enter")  # Delete (Cancel has the focus)
            await self.wait_for(pilot, lambda: ["try", "dev", "--clean"] in self.calls("try"))
            await pilot.press("j")
            await pilot.pause(0.3)
            self.assertIn("A copy of dev", str(app.main.query_one("#details").render()))

    async def test_network(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            self.assertIn("Network: open", self.text("#details"))
            await pilot.press("i")
            await pilot.pause(0.2)
            self.assertIsInstance(self.app.screen, self.ui.Confirm)
            await pilot.press("y")
            await self.wait_for(pilot, lambda: self.calls("net"))
            self.assertEqual(self.calls("net"), [["net", "dev", "restricted"]])
        self.write(list=[dict(VM, net="restricted")])
        (self.d / "cache" / "vmforai").mkdir(parents=True)
        (self.d / "cache" / "vmforai" / "net-dev.log").write_text("1 old.example:443\n2 evil.example:443\n")
        app = self.ui.VmforaiUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.wait_for(pilot, lambda: app.vms)
            details = str(app.main.query_one("#details").render())
            self.assertIn("restricted", details)
            self.assertIn("refused lately: evil.example:443, old.example:443", details)
            await pilot.press("i", "enter")  # allow a host: the last one refused, ready
            await pilot.pause(0.3)
            self.assertEqual(app.screen.query_one("#f-host").value, "evil.example")
            await pilot.press("enter")
            await self.wait_for(pilot, lambda: len(self.calls("net")) == 2)
            self.assertEqual(self.calls("net")[-1], ["net", "dev", "allow", "evil.example"])
            await pilot.press("i", "j", "j", "enter")  # open it again
            await self.wait_for(pilot, lambda: len(self.calls("net")) == 3)
            self.assertEqual(self.calls("net")[-1], ["net", "dev", "open"])

    async def test_load(self):
        self.write(list=[dict(VM, cpu_ns=1_000_000_000, rss_mb=2150)])
        async with self.start() as pilot:
            await self.ready(pilot)
            import time as clock
            vm = dict(VM, cpu_ns=6_000_000_000, rss_mb=2150)  # 5 s of CPU since, on 2 CPUs
            self.app.samples["dev"] = (clock.monotonic() - 5, 1_000_000_000)
            self.app.measure([vm])
            self.app._seen = None
            self.app.show_vms([vm])
            await pilot.pause(0.2)
            self.assertAlmostEqual(self.app.load["dev"], 50, delta=2)
            self.assertIn("%", str(self.app.main.query_one("#vms").get_row_at(0)[4]))
            self.assertIn("Now: CPU 50% · takes 2.1 GB of memory here", self.text("#details"))
            self.app.measure([dict(VM, state="shut off", cpu_ns=None)])  # off: no reading
            self.assertNotIn("dev", self.app.load)

    async def test_history(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("H")
            await pilot.pause(0.3)
            pane = self.app.main.query_one(self.ui.History)
            await self.wait_for(pilot, lambda: len(pane.sessions) == 2)
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 dev · History"])
            log = pane.query_one("#transcript")
            lines = lambda: "\n".join(str(l.text) for l in log.lines)
            await self.wait_for(pilot, lambda: "answered s-new" in lines())
            self.assertIn("> asked in s-new", lines())
            self.assertNotIn("Go on with it", lines())
            await pilot.press("j")  # the older one
            await self.wait_for(pilot, lambda: "answered s-old" in lines())
            await pilot.press("r")  # go on with it, in a tab
            await self.wait_for(pilot, lambda: self.app.terminals("dev"))
            term = self.app.terminals("dev")[0]
            await self.wait_for(pilot, lambda: "session: dev --hold --claude --no-tmux -- --resume s-old"
                                in self.screen_text(term))

    async def test_phone(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("m")
            await pilot.pause(0.3)
            self.assertIsInstance(self.app.screen, self.ui.Phone)
            await self.wait_for(pilot, lambda: "#t=KEY" in self.text("#phone-more"))
            self.assertIn("█▀▀█", self.text("#qr"))
            self.assertIn("Over Tailscale", self.text("#phone-text"))
            self.assertIn("SSH app into dev: ssh -p 7701 me@100.64.0.7", self.text("#phone-more"))
            await pilot.press("n")  # a new link
            await self.wait_for(pilot, lambda: "#t=NEW" in self.text("#phone-more"))
            await pilot.press("o")  # off
            await pilot.pause(0.3)
            self.assertNotIsInstance(self.app.screen, self.ui.Phone)
            self.assertEqual([c for c in self.calls("phone") if c[1:2] != ["notify"]],
                             [["phone", "--json"], ["phone", "--json", "--new"], ["phone", "off"]])

    async def test_phone_notifications(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("m")
            await self.wait_for(pilot, lambda: "b turns them on" in self.text("#phone-notes"))
            await pilot.press("b")
            self.assertTrue(await self.wait_for(pilot, lambda: "vmforai-t" in self.text("#phone-notes")))
            self.assertIn(["phone", "notify", "--json"], self.calls("phone"))
            await pilot.press("b")  # on: a test
            self.assertTrue(await self.wait_for(pilot, lambda: ["phone", "notify", "test", "--json"] in self.calls("phone")))

    async def test_phone_without_tailscale(self):
        os.environ["FAKE_PHONE_ERR"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_PHONE_ERR", None)
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("m")
            await self.wait_for(pilot, lambda: "Tailscale is not installed" in self.text("#phone-text"))
            self.assertFalse(self.app.screen.query_one("#lan").has_class("hidden"))
            await pilot.press("w")  # the local network then
            await self.wait_for(pilot, lambda: "same Wi-Fi" in self.text("#phone-text"))
            self.assertTrue(self.app.screen.query_one("#lan").has_class("hidden"))

    async def test_task_on_windows(self):
        self.write(list=[dict(VM, os="windows", agents=[])])
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("a")  # only Claude Code there: no agent to choose
            await pilot.pause(0.3)
            self.assertIsInstance(self.app.screen, self.ui.Ask)
            self.app.screen.query_one("#f-task").value = "fix the build"
            await self.submit(pilot)
            await self.wait_for(pilot, lambda: self.app.terminals("dev"))
            term = self.app.terminals("dev")[0]
            await self.wait_for(pilot, lambda: "session: run dev --wait --hold --claude -- fix the build"
                                in self.screen_text(term))

    async def test_fix_an_issue(self):
        self.write(list=[dict(VM, project="/home/me/app", test="npm test")])
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("f")
            await pilot.pause(0.3)
            dialog = self.app.screen
            self.assertIsInstance(dialog, self.ui.Ask)
            self.assertEqual(dialog.query_one("#f-test").value, "npm test")  # the one used last time
            await self.submit(pilot)
            await pilot.pause(0.1)
            self.assertIn("Give the issue", str(dialog.query_one("#error").render()))
            dialog.query_one("#f-issue").value = "#42"
            dialog.query_one("#f-test").value = "make check"
            await self.submit(pilot)
            await self.wait_for(pilot, lambda: self.app.terminals("dev"))
            term = self.app.terminals("dev")[0]
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 dev · Issue"])
            await self.wait_for(pilot, lambda: "session: fix dev 42 --hold --test make check" in self.screen_text(term))

    async def test_fix_needs_a_project(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("f")
            await pilot.pause(0.3)
            self.assertNotIsInstance(self.app.screen, self.ui.Ask)
            self.assertEqual(self.app.terminals("dev"), [])

    async def test_terminal_select_and_copy(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("enter", "enter")  # a shell
            await pilot.pause(0.3)
            term = self.app.terminals("dev")[0]
            await self.wait_for(pilot, lambda: "session: shell dev" in self.screen_text(term))
            y = next(i for i, line in enumerate(term.vt.display) if "session: shell dev" in line)
            x = term.vt.display[y].index("shell")
            copied = []
            self.app.copy_to_clipboard = copied.append
            await pilot.mouse_down(term, offset=(x, y))
            await pilot.hover(term, offset=(x + 5, y))
            await pilot.mouse_up(term, offset=(x + 5, y))
            await pilot.pause(0.1)
            self.assertEqual(copied, [])  # letting go does not copy
            await pilot.press("left_control")  # Ctrl alone: the selection stays
            await pilot.press("ctrl+shift+c")  # copies, and the selection goes
            await pilot.pause(0.1)
            self.assertEqual(copied, ["shell"])
            self.assertIsNone(term.text_selection)
            await pilot.mouse_down(term, offset=(x, y))
            await pilot.hover(term, offset=(x + 5, y))
            await pilot.mouse_up(term, offset=(x + 5, y))
            await pilot.press("a")  # another key: the selection goes, the key goes on
            self.assertIsNone(term.text_selection)
            await pilot.press("enter")
            await self.wait_for(pilot, lambda: "you typed: a" in self.screen_text(term))
            self.assertIn("you typed: a", self.screen_text(term))

    async def test_terminal_keys(self):
        k = self.ui.key_bytes
        self.assertEqual(k("ctrl+c", None), b"\x03")
        self.assertEqual(k("up", None), b"\x1b[A")
        self.assertEqual(k("up", None, cursor_keys=True), b"\x1bOA")
        self.assertEqual(k("ctrl+left", None), b"\x1b[1;5D")
        self.assertEqual(k("enter", "\r"), b"\r")
        self.assertEqual(k("ü", "ü"), "ü".encode())
        self.assertEqual(k("alt+b", None), b"\x1bb")
        self.assertEqual(k("f5", None), b"\x1b[15~")

    # --- new VM
    async def test_new_vm(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("n")
            await pilot.pause(0.3)
            form = self.app.screen
            await pilot.press("ctrl+s")
            await pilot.pause(0.2)
            self.assertIn("Give the VM a name", self.text("#error"))
            for name, err in (("dev", "There is a VM named dev"), ("a b", "letters, digits")):
                form.query_one("#name").value = name
                await pilot.press("ctrl+s")
                await pilot.pause(0.2)
                self.assertIn(err, self.text("#error"))
            await pilot.press("d")  # a list key: types into the name, deletes nothing
            await pilot.pause(0.1)
            self.assertEqual(self.calls("delete"), [])
            form.query_one("#name").value = "web"
            form.query_one("#image").value = "arch"
            form.query_one("#memory").value = 8192
            form.query_one("#cpus").value = 4
            form.query_one("#disk").value = "40"
            form.query_one("#agent-opencode").value = True
            form.query_one("#ports").value = "3000,5173"
            form.query_one("#idle").value = 0
            form.query_one("#project").value = str(self.d)
            await pilot.press("ctrl+s")
            await pilot.pause(0.2)
            self.assertIn("is not a git repository", self.text("#error"))
            form.query_one("#project").value = ""
            await pilot.pause(0.2)
            self.assertIn("vmforai create web --image arch --memory 8192 --cpus 4 --disk 40 --agents claude,opencode "
                          "--idle off --ports 3000,5173", self.text("#command"))
            await pilot.press("ctrl+s")  # creating: a tab of its own
            await self.wait_for(pilot, lambda: self.app.creating() and self.app.creating()[0].rc is not None)
            screen = self.app.creating()[0]
            self.assertEqual(self.app.current_pane(), screen.id)
            self.assertEqual(screen.rc, 0)
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 web · ready"])
            self.assertEqual(self.calls("create")[-1], ["create", "web", "--image", "arch", "--memory", "8192",
                                                        "--cpus", "4", "--disk", "40", "--agents", "claude,opencode",
                                                        "--idle", "off", "--ports", "3000,5173"])
            self.assertFalse(screen.query_one("#open").disabled)
            await pilot.click("#login")  # logging in runs in a terminal tab too
            await pilot.pause(0.5)
            term = self.app.terminals("web")[0]
            await self.wait_for(pilot, lambda: "session: login web" in self.screen_text(term))
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 web · Log in"])  # the creating tab is done

    async def test_create_in_the_background(self):
        gate = self.d / "create-may-end"
        os.environ["FAKE_CREATE_GATE"] = str(gate)
        self.addCleanup(os.environ.pop, "FAKE_CREATE_GATE")
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("n")
            await pilot.pause(0.3)
            self.app.screen.query_one("#name").value = "web"
            await pilot.press("ctrl+s")
            await pilot.pause(0.3)
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 web · creating…"])
            await pilot.press("escape")  # the list is usable meanwhile
            await pilot.pause(0.2)
            self.assertEqual(self.app.current_pane(), "home")
            await pilot.press("j")
            await pilot.pause(0.2)
            self.assertEqual(self.app.selected_name(), "srv")
            await pilot.press("q")  # quitting would stop it: asks
            self.assertTrue(await self.wait_for(
                pilot, lambda: "still being created" in str(self.app.screen.query_one("Static").render())))
            await pilot.press("n")
            gate.touch()
            self.assertTrue(await self.wait_for(pilot, lambda: self.app.creating()[0].rc is not None, seconds=10))
            await pilot.pause(0.2)
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 web · ready"])
            self.assertEqual(self.app.current_pane(), "home")
            await pilot.press("2")
            await pilot.pause(0.2)
            await pilot.click("#back")  # Close
            await pilot.pause(0.3)
            self.assertEqual(self.tab_labels(), ["1 VMs"])
            self.assertEqual(self.app.current_pane(), "home")

    async def test_new_vm_from_a_template(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("n")
            await pilot.pause(0.3)
            form = self.app.screen
            form.query_one("#template").value = "ml"
            await pilot.pause(0.2)
            self.assertEqual(form.query_one("#image").value, "arch")
            self.assertEqual(form.query_one("#memory").value, 12288)  # the most that fits here (16 GB)
            self.assertEqual(form.query_one("#cpus").value, 8)
            self.assertEqual(form.query_one("#disk").value, "60")
            self.assertTrue(form.query_one("#agent-aider").value)
            self.assertEqual(form.query_one("#ports").value, "8888")
            form.query_one("#name").value = "lab"
            await pilot.pause(0.2)
            self.assertIn("--ports 8888 --template ml", self.text("#command"))

    async def test_new_vm_checks(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("n")
            await pilot.pause(0.3)
            form = self.app.screen
            form.query_one("#name").value = "x"
            for field, value, err in (("#disk", "200", "the libvirt pool has 100 GB free"),
                                      ("#ports", "web", "numbers separated by commas"),
                                      ("#agent-claude", False, "at least one AI assistant")):
                if field == "#ports":
                    form.query_one("#disk").value = "20"
                if field == "#agent-claude":
                    form.query_one("#ports").value = ""
                form.query_one(field).value = value
                await pilot.press("ctrl+s")
                await pilot.pause(0.2)
                self.assertIn(err, self.text("#error"))
            self.assertEqual(self.calls("create"), [])

    async def test_failed_create(self):
        os.environ["FAKE_CREATE_RC"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_CREATE_RC")
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("n")
            await pilot.pause(0.3)
            self.app.screen.query_one("#name").value = "web"
            await pilot.press("ctrl+s")
            await self.wait_for(pilot, lambda: self.app.creating() and self.app.creating()[0].rc is not None)
            self.assertIn("failed", self.text("#status"))
            self.assertTrue(self.app.screen.query_one("#open").disabled)
            self.assertEqual(self.tab_labels(), ["1 VMs", "2 web · failed"])

    # --- snapshots
    async def test_snapshots(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("u")
            await pilot.pause(0.5)
            screen = self.app.screen
            self.assertIsInstance(screen, self.ui.Snapshots)
            table = screen.query_one("#snaps")
            await self.wait_for(pilot, lambda: table.row_count == 2)
            self.assertEqual(screen.selected()["name"], "snap-2")  # newest first
            await pilot.press("j", "enter")  # auto-1
            await pilot.pause(0.2)
            await pilot.press("enter")  # Cancel has the focus: nothing happens
            await pilot.pause(0.2)
            self.assertEqual(self.calls("undo"), [])
            await pilot.press("l")
            await pilot.pause(0.2)
            await pilot.press("y")
            await self.wait_for(pilot, lambda: self.calls("undo"))
            self.assertEqual(self.calls("undo"), [["undo", "dev", "auto-1", "--yes"]])
            await pilot.press("n")  # take one, with a name and a label
            await pilot.pause(0.2)
            dialog = self.app.screen
            dialog.query_one("#f-name").value = "auto-x y"
            await self.submit(pilot)
            await pilot.pause(0.1)
            self.assertIn("letters, digits", str(dialog.query_one("#error").render()))
            dialog.query_one("#f-name").value = "clean-install"
            dialog.query_one("#f-label").value = "fresh, with Node"
            await self.submit(pilot)
            await self.wait_for(pilot, lambda: self.calls("snapshot"))
            self.assertEqual(self.calls("snapshot")[-1], ["snapshot", "dev", "--name", "clean-install",
                                                          "fresh, with Node"])
            await pilot.press("e")  # label of the selected one
            await pilot.pause(0.2)
            self.app.screen.query_one("#f-label").value = "worked here"
            await pilot.press("enter")
            await self.wait_for(pilot, lambda: len(self.calls("snapshot")) == 2)
            self.assertEqual(self.calls("snapshot")[-1][:3], ["snapshot", "dev", "--label"])
            self.assertEqual(self.calls("snapshot")[-1][4], "worked here")
            await pilot.press("d")
            await pilot.pause(0.2)
            await pilot.press("y")
            await self.wait_for(pilot, lambda: len(self.calls("snapshot")) == 3)
            self.assertEqual(self.calls("snapshot")[-1][2], "--delete")
            await pilot.press("h")
            await pilot.pause(0.2)
            self.assertNotIsInstance(self.app.screen, self.ui.Snapshots)

    async def test_start_stop_and_resize(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("t")
            await self.wait_for(pilot, lambda: self.calls("stop"))
            self.assertEqual(self.calls("stop"), [["stop", "dev"]])
            await pilot.press("r")
            await pilot.pause(0.2)
            self.app.screen.query_one("#memory").value = 8192
            await self.submit(pilot)
            await self.wait_for(pilot, lambda: self.calls("resize"))
            self.assertEqual(self.calls("resize"), [["resize", "dev", "--memory", "8192", "--cpus", "2", "--stop"]])

    async def test_not_libvirt(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("j", "t", "u", "r")
            await pilot.pause(0.3)
            self.assertEqual(self.calls("start") + self.calls("stop") + self.calls("resize") + self.calls("snapshots"), [])

    async def test_ports(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("o")
            await pilot.pause(0.2)
            dialog = self.app.screen
            self.assertEqual(dialog.query_one("#f-ports").value, "3000")
            dialog.query_one("#f-ports").value = "3000,8080"
            dialog.query_one("#browser").value = False
            await self.submit(pilot)
            await self.wait_for(pilot, lambda: len(self.app.forwards.get("dev", [])) == 2)
            self.assertIn(["open", "dev", "3000", "8080", "--keep", "--no-browser"], self.calls("open"))
            self.assertIn(["ports", "dev", "3000,8080"], self.calls("ports"))  # "every time" was on
            self.assertIn("port 8080 at http://localhost:8080", self.text("#details"))
            proc = self.app.forwards["dev"][0][0]
            await pilot.press("o")
            await pilot.pause(0.2)
            await pilot.press("y")  # stop
            await self.wait_for(pilot, lambda: proc.poll() is not None)
            self.assertEqual(proc.poll(), 0)
            self.assertEqual(self.app.forwards["dev"], [])

    async def test_delete(self):
        async with self.start() as pilot:
            await self.ready(pilot)
            await pilot.press("d")
            await pilot.pause(0.2)
            self.assertTrue(self.app.screen.query_one("#yes").disabled)
            await pilot.press(*"dev")
            await pilot.pause(0.1)
            await pilot.press("enter")
            await self.wait_for(pilot, lambda: self.calls("delete"))
            self.assertEqual(self.calls("delete"), [["delete", "dev", "--yes"]])
            await pilot.press("j", "d")  # not made by vmforai: only the profile
            await pilot.pause(0.2)
            await pilot.press("y")
            await self.wait_for(pilot, lambda: self.calls("rm"))
            self.assertEqual(self.calls("rm"), [["rm", "srv"]])

    async def test_no_vms(self):
        self.write(list=[])
        async with self.start() as pilot:
            await self.wait_for(pilot, lambda: self.app.info)
            await pilot.pause(0.5)
            self.assertIn("No VMs yet", self.text("#details"))
            await pilot.press("enter", "t")
            await pilot.pause(0.2)
            self.assertNotIsInstance(self.app.screen, self.ui.OpenAs)

    async def test_line_drawing(self):
        t = self.ui.Terminal(["true"], "x")
        t.vt = self.ui.pyte.HistoryScreen(20, 3, history=10)
        t.stream = self.ui.pyte.ByteStream(t.vt)
        t.feed(b"a\x1b(0qqx\x1b")  # tmux's borders, the last sequence cut in two
        t.feed(b"(Bq \xe2\x94\x80")
        self.assertEqual(t.vt.display[0].rstrip(), "a──│q ─")
