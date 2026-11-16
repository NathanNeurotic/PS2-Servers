"""Offline submission-boundary, bounded diagnostic and presence acceptance."""
import dataclasses
import json
import os
import shlex
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from launcher.ra_account_worker import AccountResult, AccountTask, AccountWorker, _Lookup, valid_account_result
from launcher.ra_credentials import Credentials
from launcher.ra_diagnostics import Recording
from launcher.ra_evaluator import Evaluator
from launcher.ra_sets import SetContext, derive_watch, qualify_set
from launcher.ra_submissions import Submissions
from launcher.ra_watchlist import validate_watchlist
from tests.test_ra_sets import responses

ROOT = Path(__file__).resolve().parents[1]


class TestModeTests(unittest.TestCase):
    def task(self, operation):
        return AccountTask(1, 1, operation, credentials=Credentials("Synthetic", "fixture-token"),
            context=SetContext("Synthetic", 1, "a" * 32, 42, 21), test_mode=True,
            achievement_id=7 if operation == "award" else 0)

    def test_worker_rejects_every_test_side_effect_before_transport_or_credentials(self):
        with mock.patch("launcher.ra_account_worker.RASessionClient") as transport, \
             mock.patch("launcher.ra_account_worker.CredentialStore") as store:
            for operation in ("start_session", "award", "ping"):
                self.assertEqual(_Lookup("unused")(self.task(operation), threading.Event()).status, "failed")
            transport.assert_not_called()
            store.assert_not_called()

    def test_submission_queue_and_consumer_both_reject_test_work(self):
        worker = mock.Mock()
        submissions = Submissions("unused", threading.Event(), worker=worker, test_mode=True)
        try:
            for task in (self.task("award"), self.task("ping")):
                self.assertFalse(submissions.offer(dataclasses.replace(task, test_mode=False)))
                # Simulate already queued work at the consumer boundary.
                submissions._queue.put(task)
            submissions._queue.put(dataclasses.replace(self.task("award"), test_mode=False))
            # A sentinel proves that the worker consumed earlier queued items.
            drained = threading.Event()
            original = submissions._queue.get
            def get(**kwargs):
                value = original(**kwargs)
                if value.task_id == 2:
                    drained.set()
                return value
            submissions._queue.get = get
            submissions._queue.put(dataclasses.replace(self.task("ping"), task_id=2))
            self.assertTrue(drained.wait(2))
            worker.assert_not_called()
        finally:
            submissions.close()

    def test_encore_is_explicit_and_bound_to_the_request(self):
        context = self.task("award").context
        patch, prior = responses([{"ID": 7, "Flags": 3, "MemAddr": "0xH080000=1"}], [7])
        normal = qualify_set(context, patch, prior)
        encore = qualify_set(context, patch, prior, encore=True)
        self.assertFalse(normal.definitions)
        self.assertEqual(encore.definitions, ((7, "0xH080000=1"),))
        result = AccountResult(1, 1, "ok", qualified=encore)
        self.assertTrue(valid_account_result(result))
        worker = AccountWorker("unused", resolve=mock.Mock())
        worker._resolver = mock.Mock(return_value=result)
        task = dataclasses.replace(self.task("ping"), operation="set", test_mode=False)
        self.assertIsNone(worker(task, threading.Event()))

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("cc"), "Linux native linker test")
    def test_native_final_transport_rejects_test_submissions(self):
        rc = ROOT / "vendor/retroachievements/rcheevos"
        client = ROOT / "vendor/retroachievements/xerabora/client/src"
        with tempfile.TemporaryDirectory() as tmp:
            source, binary = Path(tmp) / "guard.c", Path(tmp) / "guard"
            source.write_text('''#include "ra.c"
#include <assert.h>
#include <stdarg.h>
static int calls, status;
int http_request(const char*a,const char*b,const char*c,const char*d,struct http_response*out) {
 (void)a;(void)b;(void)c;(void)d; calls++; memset(out,0,sizeof(*out));out->status=200;return 0; }
int log_trace_enabled(void) { return 0; }
void log_trace(const char *fmt, ...) { (void)fmt; }
static void reply(const rc_api_server_response_t*r,void*d) { (void)d; status=r->http_status_code; }
int main(void) {
 const char *blocked[]={"r=awardachievement&a=7", "u=S&r=awardachievement", "r=submitlbentry", "t=T&r=startsession&g=42", "r=ping"};
 unsigned i; rc_api_request_t request={0}; request.url="https://example.invalid";
 ra_set_test_mode(1);
 for(i=0;i<sizeof(blocked)/sizeof(blocked[0]);i++) {request.post_data=blocked[i];server_call(&request,reply,NULL,NULL);assert(calls==0 && status==403);}
 request.post_data="r=patch&g=42";server_call(&request,reply,NULL,NULL);assert(calls==1 && status==200);
 ra_set_test_mode(0);request.post_data=blocked[0];server_call(&request,reply,NULL,NULL);assert(calls==2 && status==200);
 return 0;
}''')
            command = ["cc", "-std=gnu99", "-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections"]
            for path in (client, rc / "include", rc / "src", rc / "src/rcheevos", rc / "src/rapi", client.parent.parent / "protocol"):
                command.extend(["-I", str(path)])
            subprocess.run(command + [str(source), "-o", str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, timeout=5)


class DiagnosticTests(unittest.TestCase):
    def test_history_is_bounded_and_preserves_gaps_replays_and_generation(self):
        watch = validate_watchlist(struct.pack("<5I", 0x4c574152, 1, 1, 1, 0x10080000))
        recording = Recording(watch, 7)
        recording.MAX_FRAMES = 3
        recording.MAX_BYTES = 2
        recording.record(1, b"\0")
        recording.record(3, b"\1")
        recording.record(3, b"\1")
        recording.record(4, b"\2")
        report = recording.report(profile="opl", game_id=42, image_hash="a" * 32)
        header = json.loads(report.splitlines()[0])
        self.assertEqual((header["frames"], header["retained_bytes"], header["sequence_gaps"], header["replays"]), (2, 2, 1, 1))
        self.assertNotIn(b"#1=", report)
        self.assertIn(b"#3=01 #4=02", report)
        fresh = Recording(watch, 8).report(profile="opl")
        self.assertNotIn(b"#3=", fresh)
        self.assertIn(b'"generation": 8', fresh)
        recording.MAX_EXPORT = 1024
        bounded = recording.report(profile="opl", conditions="x" * 4096)
        self.assertLessEqual(len(bounded), 1024)
        self.assertIn(b"truncated", bounded)


class NativeDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("RA_CC") or ("gcc" if os.name == "nt" else "cc")
        command = shlex.split(compiler)
        if not command or not shutil.which(command[0]):
            if os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform.startswith("linux"):
                raise AssertionError("Linux CI requires a native C compiler for diagnostic coverage")
            raise unittest.SkipTest("C compiler required for native diagnostic evaluation")
        from build.build_ra_evaluator import build
        with mock.patch.dict(os.environ, {"RA_CC": compiler}):
            cls.library = build()

    def test_managed_diagnostics_conditions_budget_and_equal_size_plan_reset(self):
        rc = ROOT / "vendor/retroachievements/rcheevos"
        client = ROOT / "vendor/retroachievements/xerabora/client/src"
        sources = sorted((rc / "src/rcheevos").glob("*.c"))
        sources += [rc / "src/rc_util.c", rc / "src/rc_compat.c", rc / "src/rhash/md5.c",
                    client / "watchlist.c", client / "snapshot.c", ROOT / "conformance/ra_dependency/diagnostics.c"]
        compiler = os.environ.get("RA_CC") or ("gcc" if os.name == "nt" else "cc")
        command = shlex.split(compiler) + ["-O1", "-std=gnu99"]
        for directory in (rc / "include", rc / "src", rc / "src/rcheevos", client, client.parent.parent / "protocol"):
            command.extend(["-I", str(directory)])
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "diagnostics"
            subprocess.run(command + list(map(str, sources)) + ["-o", str(binary), "-lm"], check=True)
            subprocess.run([str(binary)], check=True, timeout=15)

    def test_alternative_only_conditions_and_truncation_are_visible(self):
        watch = struct.pack("<5I", 0x4c574152, 1, 1, 1, 0x10080000)
        with Evaluator(self.library, {7: "S0xH080000=1"}, watch, 1) as runtime:
            runtime.evaluate(b"\0", generation=1, sequence=1)
            self.assertIn(b"alt type=", runtime.diagnostics())
            import ctypes
            describe = runtime._library.ps2servers_runtime_diagnostics
            describe.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
            describe.restype = ctypes.c_int
            buffer = ctypes.create_string_buffer(256)
            size = describe(runtime._runtime, buffer, len(buffer))
            self.assertLess(size, len(buffer))
        with Evaluator(self.library, {7: "_".join(["0xH080000=1"] * 10)}, watch, 1) as runtime:
            buffer = ctypes.create_string_buffer(256)
            size = describe(runtime._runtime, buffer, len(buffer))
            self.assertLess(size, len(buffer))
            self.assertIn(b"truncated", buffer.raw[:size])

    def test_real_rich_presence_reads_and_conditions_use_the_same_snapshot(self):
        context = SetContext("Synthetic", 1, "a" * 32, 42, 21)
        patch, prior = responses([{"ID": 7, "Flags": 3, "MemAddr": "0xH080000=1"}], [7])
        data = json.loads(patch)
        data["PatchData"]["RichPresencePatch"] = "Display:\nLevel @Number(0xH080001)"
        qualified = qualify_set(context, json.dumps(data).encode(), prior, encore=True)
        watch = derive_watch(self.library, qualified, profile="opl-v2")
        self.assertEqual(validate_watchlist(watch).direct_bytes, 2)
        with Evaluator(self.library, dict(qualified.definitions), watch, 1, qualified.rich_presence) as runtime:
            runtime.evaluate(b"\0\2", generation=1, sequence=1)
            result = runtime.evaluate(b"\1\3", generation=1, sequence=2)
            self.assertEqual(result.triggered, (7,))
            self.assertEqual(result.rich_presence, "Level 3")
            report = runtime.diagnostics(profile="opl-v2", game_id=42, image_hash=context.image_hash, test_mode=True)
            self.assertIn(b"hits=", report)
            self.assertIn(b"0x00080001/1", report)
            self.assertIn(b"#1=02 #2=03", report)
            for secret in (b"Synthetic", b"fixture-token", str(ROOT).encode()):
                self.assertNotIn(secret, report)
            # Invalid generations cannot contaminate the recording.
            self.assertFalse(runtime.evaluate(b"\0\7", generation=2, sequence=3).accepted)
            self.assertNotIn(b"#3=", runtime.diagnostics())


class ExportBoundaryTests(unittest.TestCase):
    def test_diagnostic_host_and_origin_are_validated_before_recording(self):
        import http.client
        from launcher.ra_web import RAState, RAWebServer
        callback = mock.Mock(return_value=b"local game memory")
        server = RAWebServer(0, RAState(), diagnostics=callback)
        server.start()
        port = server.server_port
        try:
            cases = [({}, 403), ({"Host": "rebound.example"}, 403),
                     ({"Host": f"127.0.0.1:{port + 1}"}, 403),
                     ({"Host": f"127.0.0.1:{port}", "Origin": "https://other.example"}, 403),
                     ({"Host": f"127.0.0.1:{port}", "Sec-Fetch-Site": "cross-site"}, 403),
                     ({"Host": f"127.0.0.1:{port}"}, 200),
                     ({"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}"}, 200)]
            accepted = 0
            for headers, expected in cases:
                with self.subTest(headers=headers):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                    try:
                        conn.putrequest("GET", "/diagnostics", skip_host=True)
                        for key, value in headers.items():
                            conn.putheader(key, value)
                        conn.endheaders()
                        response = conn.getresponse()
                        body = response.read()
                        self.assertEqual(response.status, expected)
                        accepted += expected == 200
                        self.assertEqual(callback.call_count, accepted)
                        if expected == 200:
                            self.assertEqual(body, b"local game memory")
                        else:
                            self.assertNotIn(b"local game memory", body)
                    finally:
                        conn.close()
        finally:
            server.close()

    def test_explicit_local_report_is_never_proxied_to_the_lan_viewer(self):
        import urllib.request
        import urllib.error
        from launcher.ra_web import RAState, RAWebServer
        from launcher.achievement_viewer import Viewer
        callback = mock.Mock(return_value=b"synthetic local report\n")
        server = RAWebServer(0, RAState(), diagnostics=callback)
        server.start()
        viewer = Viewer(server.server_port, port=0, host="127.0.0.1")
        viewer.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/diagnostics", timeout=3) as response:
                self.assertEqual(response.read(), b"synthetic local report\n")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            callback.assert_called_once()
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f"http://127.0.0.1:{viewer.server.server_port}/diagnostics", timeout=3)
            self.assertEqual(error.exception.code, 404)
            callback.assert_called_once()
        finally:
            viewer.close()
            server.close()
