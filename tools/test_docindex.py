import tempfile
import unittest
from pathlib import Path

from docindex import _parse_front_matter, _collect_headings, build_index, search_entries


class DocIndexTests(unittest.TestCase):
    def test_parse_front_matter_and_headings(self) -> None:
        content = """---
title: Demo
docset_version: 123
exported_at: 2025-11-12T00:00:00Z
doc_count: 3
file_size: 456
key_sections:
  - Overview
  - Details
---
## Table of Contents
- [Overview](#overview)
<a id="overview"></a>
## Overview
Some text.
<a id="details"></a>
### Details
"""
        lines = content.splitlines()
        front_matter, offset = _parse_front_matter(lines)
        self.assertIsNotNone(front_matter)
        assert front_matter
        self.assertEqual(front_matter["title"], "Demo")
        self.assertEqual(front_matter["doc_count"], 3)
        self.assertEqual(front_matter["key_sections"], ["Overview", "Details"])
        headings = _collect_headings(lines, offset)
        texts = [h.text for h in headings]
        self.assertIn("Overview", texts)
        self.assertIn("Details", texts)

    def test_build_index_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            docs_root = root / "docs" / "apple"
            docs_root.mkdir(parents=True)
            md_path = docs_root / "demo.md"
            md_path.write_text(
                """---
title: Demo
docset_version: 123
exported_at: 2025-11-12T00:00:00Z
doc_count: 1
file_size: 100
key_sections:
  - Demo Section
---
## Table of Contents
- [Demo Section](#demo-section)
<a id="demo-section"></a>
## Demo Section
""",
                encoding="utf-8",
            )
            index_path = root / "Build" / "DocIndex" / "index.json"
            index = build_index(docs_root, index_path)
            self.assertTrue(index_path.exists())
            self.assertEqual(len(index["entries"]), 1)
            results = search_entries(index, "demo")
            self.assertTrue(any("Demo Section" in result for result in results))


if __name__ == "__main__":
    unittest.main()
