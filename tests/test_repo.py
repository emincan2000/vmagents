"""repo: someone's GitHub repository, tried on the VM. The fake VM really
runs the script; "GitHub" is a folder of bare repositories (git's insteadOf),
make and git are real, the other build tools record what they were asked."""
import json
import os
import shutil
import subprocess

from tests.helpers import write_exe
from tests.test_stop import Base
from tests.test_tools import RECORD

MAKEFILE = "all:\n\t@echo built it\ncheck:\n\t@echo 'Tests: 1 failed, 3 passed, 4 total'\n"
# Records like RECORD; "python3 -m venv DIR" leaves an activate script there.
PYTHON3 = RECORD + '[ "$1 $2" = "-m venv" ] && mkdir -p "$3/bin" && : >"$3/bin/activate"\nexit 0\n'
GH = '#!/bin/sh\necho "$*" >>"$GH_CALLS"\n[ "$1" = api ] && echo ada\nexit 0\n'


class RepoTests(Base):
    def setUp(self):
        super().setUp()
        for tool in ("apt-get", "sudo"):
            write_exe(self.sb.remote_bin / tool, RECORD + ('exec "$@"\n' if tool == "sudo" else ""))
        for tool in ("git", "make", "tee", "echo", "false", "printf"):
            os.symlink(shutil.which(tool) or tool, self.sb.remote_bin / tool)
        self.github = self.sb.tmp / "github"
        self.sb.env.update(STUB_SSH_EXEC="1", GIT_CONFIG_COUNT="1",
                           GIT_CONFIG_KEY_0=f"url.file://{self.github}/.insteadOf",
                           GIT_CONFIG_VALUE_0="https://github.com/")

    def publish(self, repo, files):
        """OWNER/NAME on the fake GitHub, with FILES (path: text) in a new commit."""
        work = self.sb.tmp / "work" / repo
        bare = self.github / f"{repo}.git"
        if not bare.exists():
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
            subprocess.run(["git", "clone", "-q", bare, work], check=True, capture_output=True)
        for path, text in files.items():
            (work / path).write_text(text)
            if path in ("configure",):
                (work / path).chmod(0o755)
        git = ["git", "-C", work, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-qm", "x"], check=True)
        subprocess.run(git + ["push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)

    def calls(self, prog):
        f = self.sb.remote_home / "calls"
        return [c for c in (f.read_text().splitlines() if f.exists() else []) if c.startswith(prog + " ")]

    def test_make_project(self):
        self.publish("ada/proj", {"Makefile": MAKEFILE})
        r = self.sb.run("repo", "vm", "https://github.com/ada/proj", check=0)
        self.assertIn("ada/proj on vm, in ~/repos/proj (make): build ok, tests ok (3 passed, 1 failed)", r.stdout)
        self.assertIn("built it", r.stdout)
        # no C compiler on the VM: installed first, as "vmforai tools vm build" would
        self.assertIn("build-essential", self.calls("apt-get")[-1])
        log = next((self.sb.conf / "repos").glob("proj-*.log")).read_text()
        self.assertIn("+ make check", log)
        self.assertEqual((self.sb.remote_home / "repos/proj/.git/vmforai-last").read_text().split("\t")[:5],
                         ["make", "ok", "ok", "3", "1"])

        # Again, after a new commit that breaks the tests: the clone is brought up to date.
        self.publish("ada/proj", {"Makefile": MAKEFILE.replace("@echo 'Tests", "@false\n\t@echo 'Tests")})
        r = self.sb.run("repo", "vm", "ada/proj")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Updating ~/repos/proj", r.stdout)
        self.assertIn("build ok, tests fail", r.stdout)

        rows = self.sb.run("repos", "vm", check=0).stdout
        self.assertRegex(rows, r"proj +make +ok +fail .* ada/proj.git")

    def test_counting_tests(self):
        """The counts, from the summaries of ctest, cargo, pytest, meson, go, node --test and mocha."""
        results = ("100% tests passed out of 4\n90% tests passed, 1 tests failed out of 10\n"
                   "test result: FAILED. 2 passed; 1 failed; 0 ignored\n==== 3 passed, 2 failed in 0.5s ====\n"
                   "Ok:                 5\nFail:               1\nok  \tpkg/a\t0.1s\nFAIL\tpkg/b\t0.2s\n"
                   "ok 1 - a TAP line, not go\n# pass 2\n# fail 0\n  4 passing (8ms)\n  1 failing\n")
        self.publish("ada/proj", {"Makefile": MAKEFILE.replace("@echo 'Tests: 1 failed, 3 passed, 4 total'", "@cat results"),
                                  "results": results})
        r = self.sb.run("repo", "vm", "ada/proj", check=0)
        self.assertIn("tests ok (30 passed, 7 failed)", r.stdout)

    def test_build_systems(self):
        for tool in ("cmake", "ninja", "meson", "cargo", "go", "npm", "pip", "pio", "cc"):
            self.sb.remote_tool(tool, RECORD)
        write_exe(self.sb.remote_bin / "python3", PYTHON3)
        cases = [("cmake", {"CMakeLists.txt": ""}, "cmake -S . -B build-vmforai -G Ninja"),
                 ("meson", {"meson.build": ""}, "meson setup build-vmforai"),
                 ("cargo", {"Cargo.toml": ""}, "cargo build"),
                 ("go", {"go.mod": ""}, "go build ./..."),
                 ("npm", {"package.json": "{}"}, "npm run build --if-present"),
                 ("python", {"pyproject.toml": "[dependency-groups]\ntests = [\"freezegun\"]\n"}, "pip install -q --group tests"),
                 ("platformio", {"platformio.ini": ""}, "pio run"),
                 ("autotools", {"configure": "#!/bin/sh\nprintf 'all:\\n\\t@echo ok\\n' >Makefile\n"}, None)]
        for system, files, call in cases:
            with self.subTest(system):
                self.publish(f"ada/{system}", files)
                r = self.sb.run("repo", "vm", f"ada/{system}", "--build-only", "--json", check=0)
                out = json.loads(r.stdout)
                self.assertEqual((out["system"], out["build"], out["tests"]), (system, "ok", "skip"))
                if call:
                    prog = call.split()[0]
                    self.assertIn(call, self.calls(prog))
        # what autotools lacked came from the packages
        self.assertIn("autoconf automake libtool", " ".join(self.calls("apt-get")))
        self.sb.run("repo", "vm", "ada/cmake", "--sanitize", "--build-only", check=0)
        self.assertIn("cmake -S . -B build-vmforai-asan -G Ninja", self.calls("cmake"))

    def test_no_build_system(self):
        self.publish("ada/odd", {"README.md": "# Odd\n\n## Building\nRun ./build-it.\n\n## License\nMIT\n"})
        r = self.sb.run("repo", "vm", "ada/odd")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Run ./build-it.", r.stdout)
        self.assertNotIn("MIT", r.stdout)
        self.assertIn("how to build it is up to you", r.stderr)

    def test_fork(self):
        self.publish("ada/proj", {"Makefile": MAKEFILE})
        write_exe(self.sb.bin / "gh", GH)
        self.sb.env.update(GH_CALLS=str(self.sb.tmp / "gh-calls"))
        r = self.sb.run("repo", "vm", "ada/proj", "--fork", "--test-cmd", "echo mine", check=0)
        self.assertIn("repo fork ada/proj --clone=false", (self.sb.tmp / "gh-calls").read_text())
        config = (self.sb.remote_home / "repos/proj/.git/config").read_text()
        self.assertIn("url = https://github.com/ada/proj.git", config)
        self.assertIn('[remote "fork"]\n\turl = https://github.com/ada/proj.git', config)
        self.assertIn("+ sh -c echo mine", r.stdout)
        self.assertNotIn("make check", r.stdout)

    def test_a_clone_of_something_else(self):
        self.publish("ada/proj", {"Makefile": MAKEFILE})
        self.publish("bob/proj", {"Makefile": MAKEFILE})
        self.sb.run("repo", "vm", "ada/proj", check=0)
        r = self.sb.run("repo", "vm", "bob/proj")
        self.assertEqual(r.returncode, 1)
        self.assertIn("~/repos/proj is a clone of https://github.com/ada/proj.git, not https://github.com/bob/proj.git",
                      r.stdout)

    def test_arguments(self):
        self.assertEqual(self.sb.run("repo", "vm").returncode, 2)
        self.assertEqual(self.sb.run("repo", "vm", "a/b;c").returncode, 2)
        self.assertEqual(self.sb.run("repo", "vm", "a/b", "--depth", "0").returncode, 2)
        self.assertIn("No repositories on vm yet", self.sb.run("repos", "vm", check=0).stdout)
        self.assertIn("vmforai repo NAME OWNER/REPO", self.sb.run("help", "repo", check=0).stdout)
