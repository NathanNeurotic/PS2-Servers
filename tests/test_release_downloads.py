import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "release-downloads.sh"


def bash_command():
    if os.name == "nt":
        try:
            exec_path = subprocess.check_output(
                ["git", "--exec-path"], text=True).strip()
            candidate = Path(exec_path).resolve().parents[2] / "bin" / "bash.exe"
        except (OSError, subprocess.CalledProcessError, IndexError):
            candidate = None
        if candidate is not None and candidate.is_file():
            return str(candidate)
        raise unittest.SkipTest("Git Bash is required for release workflow tests")
    bash = shutil.which("bash")
    if bash is None:
        raise unittest.SkipTest("Bash is required for release workflow tests")
    return bash


def shell_path(path):
    if os.name != "nt":
        return str(path)
    return subprocess.check_output(
        [bash_command(), "-c", 'cygpath -u "$1"', "cygpath", str(path)],
        text=True).strip()


def workflow_script(text, step_name):
    step = text.split(f"      - name: {step_name}\n", 1)[1]
    block = step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
    return "\n".join(
        line[10:] for line in block.splitlines()
        if line.startswith(" " * 10) or not line.strip())


class ReleaseDownloadTests(unittest.TestCase):
    def test_source_and_platform_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "PS2Servers-source.zip").touch()
            Path(temp, "PS2ServersEdge-test-arm64-pi3-pi4-pi5-nas.tar.gz").touch()
            Path(temp, "PS2ServersEdge-test-windows-64bit.zip").touch()
            script_copy = Path(temp, "release-downloads.sh")
            shutil.copy(SCRIPT, script_copy)
            command = "source ./release-downloads.sh; row 'Source / inspection' PS2Servers-source.zip '' ''; edge_rows"
            env = dict(os.environ, RELEASE_TAG="v0.0.0-test")
            output = subprocess.check_output(
                [bash_command(), "-c", command], cwd=temp, text=True, env=env)
        self.assertIn(
            "| Source / inspection | [`PS2Servers-source.zip`]"
            "(https://github.com/NathanNeurotic/PS2-Servers/releases/download/"
            "v0.0.0-test/PS2Servers-source.zip) |", output)
        self.assertIn(
            "[`PS2ServersEdge-test-arm64-pi3-pi4-pi5-nas.tar.gz`]"
            "(https://github.com/NathanNeurotic/PS2-Servers/releases/download/"
            "v0.0.0-test/PS2ServersEdge-test-arm64-pi3-pi4-pi5-nas.tar.gz)", output)
        self.assertIn("| Raspberry Pi 3/4/5, ARM64 NAS |", output)
        self.assertIn("| Windows, 64-bit (headless) |", output)

    def test_every_edge_target_has_a_purpose(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for label in (
            "pc-64bit-amd64", "pc-32bit-i386", "arm64-pi3-pi4-pi5-nas",
            "armv7-pi2-32bit-routers", "armv6-pi1-pi-zero",
            "armv5-pogoplug-sheevaplug-dockstar",
            "router-mips-little-endian-mt7621", "router-mips-big-endian-ath79",
            "edgerouter-octeon-mips64", "mips64-little-endian", "riscv64-sbc",
            "windows-64bit", "windows-32bit", "windows-arm64",
            "macos-apple-silicon", "macos-intel",
        ):
            self.assertIn(label, text)

    def test_release_notes_stay_a_changelog_and_table(self):
        common = (ROOT / ".github" / "release-notes-common.md").read_text(encoding="utf-8")
        self.assertIn("## Changelog", common)
        self.assertNotIn("Security transparency", common)
        for workflow in ("release.yml", "release-on-main.yml"):
            text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
            self.assertIn("| Platform / purpose | Asset |", text)
            self.assertIn("release-downloads.sh", text)
            self.assertIn("edge_rows", text)
            self.assertNotIn("<details>", text)

    def test_tagged_edge_artifacts_are_collected_before_publish(self):
        workflows = ROOT / ".github" / "workflows"
        release = (workflows / "release.yml").read_text(encoding="utf-8")
        edge_job = release.split("  edge:\n", 1)[1]
        self.assertIn("uses: ./.github/workflows/edge-build.yml", edge_job)
        self.assertIn("if: startsWith(github.ref, 'refs/tags/')", edge_job)
        publish = release.split("  release:\n", 1)[1]
        self.assertIn("      - edge\n", publish)
        self.assertIn("needs.edge.result == 'success'", publish)
        reusable = (workflows / "edge-build.yml").read_text(encoding="utf-8")
        self.assertNotIn("Attach Edge artifact to tagged release", reusable)
        validation = (workflows / "edge.yml").read_text(encoding="utf-8")
        build = validation.split("  build:\n", 1)[1]
        self.assertIn("if: github.ref_type != 'tag'", build)

    def test_workflows_generate_complete_notes_and_links(self):
        with tempfile.TemporaryDirectory() as temp:
            assets = Path(temp, "release-assets")
            assets.mkdir()
            for name in (
                "PS2Servers-windows-x64-portable.zip",
                "PS2Servers-windows-x86.zip",
                "PS2Servers-linux-x64",
                "PS2Servers-x86_64.AppImage",
                "PS2Servers-macos-arm64.zip",
                "PS2Servers-macos-x64.zip",
                "PS2Servers-source.zip",
                "PS2ServersEdge-test-arm64-pi3-pi4-pi5-nas.tar.gz",
            ):
                Path(assets, name).write_bytes(b"fixture")
            Path(temp, ".github").mkdir()
            shutil.copy(ROOT / ".github" / "release-notes-common.md",
                        Path(temp, ".github", "release-notes-common.md"))
            output = Path(temp, "github-output")
            env = dict(
                os.environ,
                GITHUB_WORKSPACE=shell_path(ROOT),
                RUNNER_TEMP=".",
                GITHUB_OUTPUT=shell_path(output),
                GITHUB_SHA="abcdef1234567890abcdef1234567890abcdef12",
                GITHUB_RUN_NUMBER="77",
                TAG="v0.0.0-test",
            )
            for workflow, tag in (
                ("release.yml", "v0.0.0-test"),
                ("release-on-main.yml", "main-77-abcdef1"),
            ):
                with self.subTest(workflow=workflow):
                    text = (ROOT / ".github" / "workflows" / workflow).read_text(
                        encoding="utf-8")
                    if workflow == "release-on-main.yml":
                        result = subprocess.run(
                            [bash_command(), "-c",
                             workflow_script(text, "Generate SHA256 checksums")],
                            cwd=temp, env=env, capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        lines = output.read_text(encoding="utf-8").splitlines()
                        start = lines.index("downloads_table<<PS2SERVERS_EOF") + 1
                        end = lines.index("PS2SERVERS_EOF", start)
                        env["DOWNLOADS_TABLE"] = "\n".join(lines[start:end])
                        env["COMMIT_SHA"] = env["GITHUB_SHA"]
                        step_name = "Assemble release notes"
                    else:
                        step_name = "Compose release notes"
                    result = subprocess.run(
                        [bash_command(), "-c", workflow_script(text, step_name)],
                        cwd=temp, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(output.exists(), (
                        f"stdout={result.stdout}\nstderr={result.stderr}\n"
                        f"files={list(Path(temp).rglob('*'))}"))
                    notes = next(
                        line.split("=", 1)[1]
                        for line in output.read_text(encoding="utf-8").splitlines()
                        if line.startswith("path="))
                    body = Path(temp, notes).read_text(encoding="utf-8")
                    self.assertEqual(body.count("## Changelog"), 1)
                    self.assertNotIn("Security transparency", body)
                    for name in (
                        "PS2Servers-windows-x64-portable.zip",
                        "PS2Servers-source.zip",
                        "PS2Servers-x86_64.AppImage",
                        "PS2ServersEdge-test-arm64-pi3-pi4-pi5-nas.tar.gz",
                    ):
                        link = (f"[`{name}`](https://github.com/NathanNeurotic/"
                                f"PS2-Servers/releases/download/{tag}/{name})")
                        self.assertEqual(body.count(link), 1, body)
                    fallback = ("[`PS2Servers-windows-x86.zip`]"
                                f"(https://github.com/NathanNeurotic/PS2-Servers/"
                                f"releases/download/{tag}/PS2Servers-windows-x86.zip)"
                                " — portable build unavailable in this release")
                    self.assertIn(fallback, body)


if __name__ == "__main__":
    unittest.main()
