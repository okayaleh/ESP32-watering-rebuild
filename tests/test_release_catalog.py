"""Published update choices are ordered across pages without deleting history."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import sync_updates as sync

REPO = "supercrossed/ESP32-watering-rebuild"


def release(tag, published, prerelease=False):
    name = "planter-" + tag[1:] + ".zip"
    return {"tag_name": tag, "published_at": published, "draft": False,
            "prerelease": prerelease,
            "assets": [{"name": asset, "state": "uploaded", "size": size,
                        "browser_download_url": "https://github.com/%s/releases/download/%s/%s" % (REPO, tag, asset)}
                       for asset, size in ((name, 2000), (name + ".sha256", 100))]}


class ReleaseCatalogTests(unittest.TestCase):
    def fetch_pages(self, pages, page_size=2):
        calls = []
        def fetch(url, limit):
            index = len(calls)
            calls.append(url)
            self.assertEqual(url, "https://api.github.com/repos/%s/releases?per_page=%s&page=%s" % (REPO, page_size, index + 1))
            self.assertEqual(limit, 1024 * 1024)
            self.assertLess(index, len(pages), "Unnecessary page requested")
            value = pages[index]
            if isinstance(value, Exception):
                raise value
            return json.dumps(value).encode()
        return fetch, calls

    def test_all_pages_order_by_publication_not_tag_or_listing_order(self):
        pages = [[release("v9.0.0", "2020-01-01T00:00:00Z"),
                  release("v2.0.0", "2026-01-01T00:00:00Z")],
                 [release("v2.1.0-preview", "2026-03-01T00:00:00Z", True),
                  release("v1.9.0", "2025-12-01T00:00:00Z")],
                 [release("v1.0.0", "2026-04-01T00:00:00Z")]]
        fetch, calls = self.fetch_pages(pages)
        with patch.object(sync, "RELEASES_PER_PAGE", 2):
            choices = sync.list_releases(REPO, fetch)
        self.assertEqual(len(calls), 3)
        self.assertEqual([item["tag"] for item in choices], ["v1.0.0", "v2.1.0-preview", "v2.0.0"])
        self.assertTrue(choices[1]["prerelease"])

    def test_filter_drafts_unrelated_incomplete_and_unsafe_metadata_across_pages(self):
        invalid = []
        for field, value in (("draft", True), ("tag_name", "notes"),
                             ("assets", []), ("published_at", None),
                             ("published_at", "2026-19-99T00:00:00Z")):
            item = release("v2.0.0", "2026-01-01T00:00:00Z")
            item[field] = value
            invalid.append(item)
        for field, value in (("state", "uploading"), ("size", 0),
                             ("browser_download_url", "http://github.com/unsafe")):
            item = release("v2.0.0", "2026-01-01T00:00:00Z")
            item["assets"][0][field] = value
            invalid.append(item)
        duplicate = release("v2.0.0", "2026-01-01T00:00:00Z")
        duplicate["assets"].append(dict(duplicate["assets"][0]))
        invalid.append(duplicate)
        invalid.append({"unrelated": "record"})
        valid = release("v2.0.0-preview", "2025-01-01T00:00:00Z", True)
        pages = [invalid[index:index + 2] for index in range(0, len(invalid), 2)] + [[valid]]
        fetch, calls = self.fetch_pages(pages)
        with patch.object(sync, "RELEASES_PER_PAGE", 2):
            choices = sync.list_releases(REPO, fetch)
        self.assertEqual([item["tag"] for item in choices], ["v2.0.0-preview"])
        self.assertEqual(len(calls), len(pages))

    def test_network_or_page_errors_do_not_return_a_partial_catalog(self):
        first = [release("v1.0.0", "2026-01-01T00:00:00Z"),
                 release("v2.0.0", "2026-02-01T00:00:00Z")]
        for last in (OSError("GitHub unavailable"), {"message": "invalid response"}):
            fetch, _ = self.fetch_pages([first, last])
            with patch.object(sync, "RELEASES_PER_PAGE", 2), self.assertRaises((OSError, ValueError)):
                sync.list_releases(REPO, fetch)
        fetch, _ = self.fetch_pages([first])
        with patch.object(sync, "RELEASES_PER_PAGE", 2), patch.object(sync, "MAX_RELEASE_PAGES", 1):
            with self.assertRaisesRegex(ValueError, "page limit"):
                sync.list_releases(REPO, fetch)

    def test_new_repository_does_not_invent_previous_versions(self):
        fetch, _ = self.fetch_pages([[release("v2.0.0-preview", "2026-01-01T00:00:00Z", True)]])
        with patch.object(sync, "RELEASES_PER_PAGE", 2):
            choices = sync.list_releases(REPO, fetch)
        output = io.StringIO()
        with redirect_stdout(output):
            sync.print_releases(choices)
        self.assertIn("Newest: v2.0.0-preview | prerelease", output.getvalue())
        self.assertNotIn("Previous:", output.getvalue())
        self.assertIn("not a record of versions tested", output.getvalue())

    def test_empty_catalog_and_repeated_tag_are_handled(self):
        fetch, _ = self.fetch_pages([[]], page_size=sync.RELEASES_PER_PAGE)
        self.assertEqual(sync.list_releases(REPO, fetch), [])
        output = io.StringIO()
        with redirect_stdout(output):
            sync.print_releases([])
        self.assertIn("No published Planter updates", output.getvalue())
        item = release("v2.0.0", "2026-01-01T00:00:00Z")
        fetch, _ = self.fetch_pages([[item, item], []])
        with patch.object(sync, "RELEASES_PER_PAGE", 2):
            choices = sync.list_releases(REPO, fetch)
        self.assertEqual(len(choices), 1)


if __name__ == "__main__":
    unittest.main()
