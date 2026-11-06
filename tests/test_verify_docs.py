import importlib.util
from pathlib import Path
import tempfile
import unittest


class VerifyDocsTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("verify_docs", self.repo / "tools/verify_docs.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_docs_dir_is_repository_relative(self):
        self.assertEqual(self.module.DOCS_DIR, self.repo / "docs")

    def test_broken_link_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = Path(temp)
            (docs / "index.html").write_text('<a href="missing.html">missing</a>', encoding="utf-8")
            self.assertNotEqual(self.module.verify_docs(docs), 0)

    def test_valid_link_returns_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = Path(temp)
            (docs / "index.html").write_text('<h1 id="top">Top</h1><a href="#top">top</a>', encoding="utf-8")
            self.assertEqual(self.module.verify_docs(docs), 0)


if __name__ == "__main__":
    unittest.main()
