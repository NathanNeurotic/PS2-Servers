"""Edge's SMB server must put the same bytes on the wire as the Python one.

The Python SMBv1 server is the implementation that has actually been validated
against consoles. Edge's Go port therefore has to match *it*, not the SMB
specification -- where the two disagree, the specification is not what OPL was
tested against.

So this compares the two implementations directly rather than asserting either
against a written-down expectation. A test that only checked the Go code
against hand-copied hex would drift the moment the Python side was corrected,
and would not notice.

Concentrates on the framing and the header because they are the base every
command sits on: an error there is not one broken command, it is a connection
that desynchronises and a console that hangs partway through a game load.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EDGE = os.path.join(_ROOT, "native", "ps2servers-edge")
_REF = os.path.join(_ROOT, "smbv1_server", "smbserver_opl.py")


def _load_reference():
    spec = importlib.util.spec_from_file_location("smbref", _REF)
    module = importlib.util.module_from_spec(spec)
    sys.modules["smbref"] = module
    spec.loader.exec_module(module)
    return module


# Cases are (command, status, tid, pid, uid, mid). Chosen to exercise the parts
# that are easy to port wrong rather than to be exhaustive: a failure status
# (the split encoding), success (everything zero), and distinct id fields so a
# transposition shows up.
HEADER_CASES = [
    ("ComNegotiate", "SMB_COM_NEGOTIATE", "StatusObjectNameNotFound",
     "STATUS_OBJECT_NAME_NOT_FOUND", 0x0102, 0x0304, 0x0506, 0x0708),
    ("ComEcho", "SMB_COM_ECHO", "StatusSuccess", "STATUS_SUCCESS", 1, 2, 3, 4),
    ("ComReadAndX", "SMB_COM_READ_ANDX", "StatusAccessDenied",
     "STATUS_ACCESS_DENIED", 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF),
    ("ComNTCreateAndX", "SMB_COM_NT_CREATE_ANDX", "StatusNotImplemented",
     "STATUS_NOT_IMPLEMENTED", 0, 0, 0, 0),
]


class SmbWireParity(unittest.TestCase):
    def setUp(self):
        if not shutil.which("go"):
            self.skipTest("no Go toolchain on PATH")
        if not os.path.isdir(_EDGE):
            self.skipTest("Edge source not present")
        self.ref = _load_reference()

    def _run_go(self, body):
        """Run a Go program inside the Edge module and return its stdout.

        Inside the module because internal/ packages are importable only from
        within the module that declares them.
        """
        program = (
            "package main\n\nimport (\n\t\"encoding/hex\"\n\t\"fmt\"\n\t\"bytes\"\n"
            "\t\"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/smb\"\n)\n\n"
            "var _ = hex.EncodeToString\nvar _ = bytes.NewBuffer\n\n"
            "func main() {\n" + body + "\n}\n"
        )
        with tempfile.TemporaryDirectory(dir=_EDGE) as tmp:
            src = os.path.join(tmp, "main.go")
            with open(src, "w", encoding="utf-8") as handle:
                handle.write(program)
            result = subprocess.run(["go", "run", src], cwd=_EDGE,
                                    capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            self.fail("go run failed, so the two implementations were never "
                      "compared:\n" + result.stderr[-800:])
        return result.stdout

    def test_headers_match_byte_for_byte(self):
        body = "\n".join(
            'fmt.Printf("%x\\n", smb.PackHeader(smb.{}, smb.{}, {}, {}, {}, {}))'.format(
                go_cmd, go_status, tid, pid, uid, mid)
            for go_cmd, _py_cmd, go_status, _py_status, tid, pid, uid, mid in HEADER_CASES)
        lines = [l.strip() for l in self._run_go(body).splitlines() if l.strip()]
        self.assertEqual(len(lines), len(HEADER_CASES))

        for line, case in zip(lines, HEADER_CASES):
            _go_cmd, py_cmd, _go_status, py_status, tid, pid, uid, mid = case
            expected = self.ref.pack_header(
                getattr(self.ref, py_cmd), getattr(self.ref, py_status),
                tid, pid, uid, mid)
            with self.subTest(command=py_cmd, status=py_status):
                self.assertEqual(
                    line, expected.hex(),
                    f"Go and Python disagree on the {py_cmd}/{py_status} header")

    def test_framing_matches(self):
        # The 24-bit length is the only big-endian field in the protocol.
        # Little-endian here announces the wrong size and hangs the peer.
        payloads = [b"", b"\xaa\xbb\xcc", bytes(range(256))]
        # %-formatting, not .format(): the Go source is full of braces.
        body = "\n".join(
            'func() { var b bytes.Buffer; smb.WriteMessage(&b, []byte{%s}); '
            'fmt.Printf("%%x\\n", b.Bytes()) }()' % (
                ",".join(str(byte) for byte in payload),)
            for payload in payloads)
        lines = [l.strip() for l in self._run_go(body).splitlines() if l.strip()]
        self.assertEqual(len(lines), len(payloads))

        for line, payload in zip(lines, payloads):
            expected = (b"\x00" + len(payload).to_bytes(3, "big") + payload).hex()
            with self.subTest(length=len(payload)):
                self.assertEqual(line, expected,
                                 "Go and Python disagree on session framing")

    def test_command_and_status_constants_agree(self):
        # A transposed opcode would send a valid-looking reply to the wrong
        # request, which is far harder to diagnose than a malformed one.
        pairs = [
            ("ComClose", "SMB_COM_CLOSE"), ("ComCheckDirectory", "SMB_COM_CHECK_DIRECTORY"),
            ("ComTransaction", "SMB_COM_TRANSACTION"), ("ComEcho", "SMB_COM_ECHO"),
            ("ComOpenAndX", "SMB_COM_OPEN_ANDX"), ("ComReadAndX", "SMB_COM_READ_ANDX"),
            ("ComWriteAndX", "SMB_COM_WRITE_ANDX"), ("ComTrans2", "SMB_COM_TRANS2"),
            ("ComTreeDisconnect", "SMB_COM_TREE_DISCONNECT"),
            ("ComNegotiate", "SMB_COM_NEGOTIATE"),
            ("ComSessionSetupAndX", "SMB_COM_SESSION_SETUP_ANDX"),
            ("ComLogoffAndX", "SMB_COM_LOGOFF_ANDX"),
            ("ComTreeConnectAndX", "SMB_COM_TREE_CONNECT_ANDX"),
            ("ComNTCreateAndX", "SMB_COM_NT_CREATE_ANDX"),
            ("ComQueryInformationDisk", "SMB_COM_QUERY_INFORMATION_DISK"),
            ("ComNone", "SMB_COM_NONE"),
            ("StatusSuccess", "STATUS_SUCCESS"),
            ("StatusNotImplemented", "STATUS_NOT_IMPLEMENTED"),
            ("StatusAccessDenied", "STATUS_ACCESS_DENIED"),
            ("StatusObjectNameNotFound", "STATUS_OBJECT_NAME_NOT_FOUND"),
            ("Flags2", "FLAGS2"), ("Flags1Reply", "FLAGS1_REPLY"),
        ]
        body = "\n".join(
            'fmt.Printf("%d\\n", uint64(smb.{}))'.format(go_name)
            for go_name, _py in pairs)
        values = [int(l) for l in self._run_go(body).splitlines() if l.strip()]
        self.assertEqual(len(values), len(pairs))

        for value, (go_name, py_name) in zip(values, pairs):
            with self.subTest(constant=go_name):
                self.assertEqual(value, int(getattr(self.ref, py_name)),
                                 f"{go_name} != {py_name}")


if __name__ == "__main__":
    unittest.main()
