#!/usr/bin/env python3
"""
Tests for ctxaudit. Stdlib only.

    python3 test_ctxaudit.py
"""

import argparse
import datetime
import os
import shutil
import tempfile
import unittest

import ctxaudit as ca


def default_args(**over):
    a = argparse.Namespace(
        max_lines=200, stale_days=90, top=5, min_dup_words=12,
        json=False, no_color=True, today=None,
    )
    for k, v in over.items():
        setattr(a, k, v)
    return a


TODAY = datetime.date(2026, 10, 31)


class TreeCase(unittest.TestCase):
    """Builds a temporary CLAUDE.md tree on disk."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ctxaudit-test-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, relpath, text):
        full = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        return full

    def audit(self, **over):
        return ca.build_report(self.root, default_args(**over), TODAY)


class TestDiscovery(TreeCase):
    def test_finds_nested_context_files(self):
        self.write("CLAUDE.md", "# Root\n")
        self.write("a/CLAUDE.md", "# A\n")
        self.write("a/b/CLAUDE.md", "# B\n")
        d = self.audit()
        self.assertEqual(len(d["files"]), 3)

    def test_skips_vendor_dirs(self):
        self.write("CLAUDE.md", "# Root\n")
        self.write("node_modules/pkg/CLAUDE.md", "# noise\n")
        self.write(".git/CLAUDE.md", "# noise\n")
        d = self.audit()
        self.assertEqual(len(d["files"]), 1)

    def test_picks_up_claude_local(self):
        self.write("CLAUDE.md", "# Root\n")
        self.write("CLAUDE.local.md", "# Local\n")
        self.assertEqual(len(self.audit()["files"]), 2)


class TestLoadSimulation(TreeCase):
    def test_deep_dir_inherits_all_ancestors(self):
        # 400 chars ~= 100 tokens each, so the arithmetic is checkable.
        self.write("CLAUDE.md", "x" * 400)
        self.write("a/CLAUDE.md", "y" * 400)
        self.write("a/b/CLAUDE.md", "z" * 400)
        d = self.audit()
        deep = [s for s in d["loads"] if s["reldir"] == os.path.join("a", "b")][0]

        self.assertEqual(deep["file_count"], 3)
        self.assertEqual(deep["own_tokens"], 100)
        self.assertEqual(deep["inherited_tokens"], 200)
        self.assertEqual(deep["total_tokens"], 300)

    def test_root_inherits_nothing(self):
        self.write("CLAUDE.md", "x" * 400)
        self.write("a/CLAUDE.md", "y" * 400)
        d = self.audit()
        root = [s for s in d["loads"] if s["reldir"] == "."][0]
        self.assertEqual(root["inherited_tokens"], 0)
        self.assertEqual(root["total_tokens"], 100)

    def test_siblings_do_not_leak_into_each_other(self):
        self.write("CLAUDE.md", "r" * 400)
        self.write("a/CLAUDE.md", "a" * 400)
        self.write("b/CLAUDE.md", "b" * 4000)
        d = self.audit()
        a = [s for s in d["loads"] if s["reldir"] == "a"][0]
        # Sibling b is 1000 tokens; it must not appear in a's chain.
        self.assertEqual(a["total_tokens"], 200)
        self.assertNotIn("b", [os.path.dirname(f["relpath"]) for f in a["files"]])

    def test_heaviest_chain_ranked_first(self):
        self.write("CLAUDE.md", "r" * 400)
        self.write("light/CLAUDE.md", "l" * 400)
        self.write("heavy/CLAUDE.md", "h" * 8000)
        d = self.audit()
        self.assertEqual(d["loads"][0]["reldir"], "heavy")


BLOCK = (
    "This is a shared instruction block that is long enough to clear the "
    "minimum word threshold used by the duplication detector."
)


class TestDuplication(TreeCase):
    def test_identical_block_across_files_is_one_cluster(self):
        for name in ("a", "b", "c"):
            self.write("%s/CLAUDE.md" % name, "# %s\n\n%s\n" % (name, BLOCK))
        dups = self.audit()["duplication"]

        self.assertEqual(len(dups), 1, "three copies must collapse to one finding")
        cluster = dups[0]
        self.assertEqual(cluster["copies"], 3)
        self.assertEqual(cluster["files"], 3)
        self.assertTrue(cluster["exact"])

    def test_reclaimable_excludes_the_copy_you_keep(self):
        for name in ("a", "b", "c", "d"):
            self.write("%s/CLAUDE.md" % name, BLOCK + "\n")
        cluster = self.audit()["duplication"][0]
        per_copy = cluster["block_tokens"]
        # Keep one of four; reclaim the other three.
        self.assertEqual(cluster["reclaimable_tokens"], per_copy * 3)

    def test_near_duplicate_detected(self):
        self.write("a/CLAUDE.md", BLOCK + "\n")
        self.write("b/CLAUDE.md", BLOCK.replace("shared", "common") + "\n")
        dups = self.audit()["duplication"]
        self.assertEqual(len(dups), 1)
        self.assertFalse(dups[0]["exact"])

    def test_unrelated_text_not_flagged(self):
        self.write("a/CLAUDE.md", "Always run the linter before committing any change at all.\n")
        self.write("b/CLAUDE.md", "Deployment happens on Tuesdays via the release pipeline only.\n")
        self.assertEqual(self.audit()["duplication"], [])

    def test_repetition_within_one_file_ignored(self):
        self.write("a/CLAUDE.md", BLOCK + "\n\n" + BLOCK + "\n")
        # Same-file repetition costs nothing extra across load chains.
        self.assertEqual(self.audit()["duplication"], [])

    def test_short_blocks_below_threshold_ignored(self):
        self.write("a/CLAUDE.md", "Be concise.\n")
        self.write("b/CLAUDE.md", "Be concise.\n")
        self.assertEqual(self.audit()["duplication"], [])

    def test_code_fences_excluded(self):
        fenced = "```\n%s\n```\n" % BLOCK
        self.write("a/CLAUDE.md", fenced)
        self.write("b/CLAUDE.md", fenced)
        self.assertEqual(self.audit()["duplication"], [])


class TestFreshness(TreeCase):
    def test_last_updated_field_parsed(self):
        self.write("CLAUDE.md", "# T\n\n**Last Updated:** 2026-10-01\n")
        f = self.audit()["files"][0]
        self.assertEqual(f["last_updated"]["date"], datetime.date(2026, 10, 1))
        self.assertEqual(f["last_updated"]["source"], "field")

    def test_fresh_file_not_flagged(self):
        self.write("CLAUDE.md", "Last Updated: 2026-10-30\n")
        self.assertEqual(
            [s for s in self.audit()["stale"] if s["age_days"] is not None], [])

    def test_old_file_flagged_with_age(self):
        self.write("CLAUDE.md", "Last Updated: 2026-01-01\n")
        stale = self.audit()["stale"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["age_days"], 303)

    def test_missing_date_reported_separately(self):
        self.write("CLAUDE.md", "# No dates here at all\n")
        stale = self.audit()["stale"]
        self.assertEqual(len(stale), 1)
        self.assertIsNone(stale[0]["age_days"])

    def test_malformed_date_does_not_crash(self):
        self.write("CLAUDE.md", "Last Updated: 2026-13-45\n")
        self.assertEqual(len(self.audit()["files"]), 1)

    def test_newest_date_wins(self):
        self.write("CLAUDE.md", "Last Updated: 2026-01-01\nLast Reviewed: 2026-10-20\n")
        self.assertEqual(
            self.audit()["files"][0]["last_updated"]["date"],
            datetime.date(2026, 10, 20))


class TestBloatAndScore(TreeCase):
    def test_over_budget_flagged(self):
        self.write("CLAUDE.md", "line\n" * 250)
        bloat = self.audit()["bloat"]
        self.assertEqual(len(bloat), 1)
        self.assertEqual(bloat[0]["lines"], 250)
        self.assertEqual(bloat[0]["over_by"], 50)

    def test_under_budget_not_flagged(self):
        self.write("CLAUDE.md", "line\n" * 150)
        self.assertEqual(self.audit()["bloat"], [])

    def test_custom_budget_respected(self):
        self.write("CLAUDE.md", "line\n" * 120)
        self.assertEqual(len(self.audit(max_lines=100)["bloat"]), 1)

    def test_clean_tree_scores_a(self):
        self.write("CLAUDE.md", "# Clean\n\nLast Updated: 2026-10-30\n\nBe terse.\n")
        s = self.audit()["score"]
        self.assertEqual(s["score"], 100)
        self.assertEqual(s["grade"], "A")

    def test_messy_tree_scores_worse_than_clean(self):
        # Asserts degradation and that every problem is attributed, rather
        # than a magic cliff -- the exact weights are tunable, the ordering
        # and the attribution are what must hold.
        for name in ("a", "b", "c"):
            self.write("%s/CLAUDE.md" % name,
                       "Last Updated: 2025-01-01\n" + BLOCK + "\n" + "pad\n" * 250)
        s = self.audit()["score"]
        self.assertLess(s["score"], 70)
        self.assertNotIn(s["grade"], ("A", "B"))

        reasons = " ".join(r for r, _ in s["penalties"])
        self.assertIn("over", reasons)        # bloat attributed
        self.assertIn("stale", reasons)       # staleness attributed
        self.assertIn("duplication", reasons)  # duplication attributed

    def test_penalties_are_monotonic(self):
        self.write("clean/CLAUDE.md", "# Clean\n\nLast Updated: 2026-10-30\n")
        clean = self.audit()["score"]["score"]
        self.write("messy/CLAUDE.md", "Last Updated: 2024-01-01\n" + "pad\n" * 400)
        self.assertLess(self.audit()["score"]["score"], clean)

    def test_score_never_negative(self):
        for i in range(12):
            self.write("d%d/CLAUDE.md" % i,
                       "Last Updated: 2020-01-01\n" + BLOCK + "\n" + "pad\n" * 900)
        self.assertGreaterEqual(self.audit()["score"]["score"], 0)

    def test_recommendations_always_present(self):
        self.write("CLAUDE.md", "# Fine\n\nLast Updated: 2026-10-30\n")
        self.assertTrue(self.audit()["recommendations"])


class TestCollisions(TreeCase):
    def test_shared_heading_flagged(self):
        self.write("a/CLAUDE.md", "## Testing\n\nUse pytest.\n")
        self.write("b/CLAUDE.md", "## Testing\n\nUse unittest.\n")
        col = self.audit()["collisions"]
        self.assertEqual([c["heading"] for c in col], ["testing"])
        self.assertEqual(col[0]["count"], 2)

    def test_unique_headings_not_flagged(self):
        self.write("a/CLAUDE.md", "## Build\n")
        self.write("b/CLAUDE.md", "## Deploy\n")
        self.assertEqual(self.audit()["collisions"], [])


class TestUnits(unittest.TestCase):
    def test_token_estimate(self):
        self.assertEqual(ca.est_tokens("x" * 400), 100)
        self.assertEqual(ca.est_tokens(""), 0)

    def test_human_formatting(self):
        self.assertEqual(ca.human(999), "999")
        self.assertEqual(ca.human(1500), "1.5k")
        self.assertEqual(ca.human(44000), "44k")

    def test_normalise_strips_markdown(self):
        self.assertEqual(ca.normalise("**Bold** and `code`!"), "bold and code")

    def test_blocks_split_on_blank_lines(self):
        blocks = ca.extract_blocks("para one here\n\npara two here\n")
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["line"], 1)
        self.assertEqual(blocks[1]["line"], 3)

    def test_unionfind_merges_transitively(self):
        uf = ca.UnionFind(4)
        uf.union(0, 1)
        uf.union(1, 2)
        self.assertEqual(uf.find(0), uf.find(2))
        self.assertNotEqual(uf.find(0), uf.find(3))

    def test_truncate_keeps_tail(self):
        self.assertTrue(ca.truncate("a/very/long/path/file.md", 10).endswith("file.md"))

    def test_excerpt_flattens_and_caps(self):
        e = ca.excerpt("line one\nline two" + " padding" * 50)
        self.assertNotIn("\n", e)
        self.assertLessEqual(len(e), 100)


class TestCLI(TreeCase):
    def test_json_is_serialisable(self):
        import json
        self.write("CLAUDE.md", "Last Updated: 2026-10-30\n" + BLOCK + "\n")
        self.write("a/CLAUDE.md", BLOCK + "\n")
        payload = json.dumps(ca.to_json(self.audit()))
        self.assertIn("duplication", json.loads(payload))

    def test_missing_path_exits_2(self):
        self.assertEqual(ca.main(["/does/not/exist/anywhere"]), 2)

    def test_version_exits_clean(self):
        self.assertEqual(ca.main(["--version"]), 0)

    def test_runs_on_real_tree(self):
        self.write("CLAUDE.md", "# Root\n\nLast Updated: 2026-10-30\n")
        self.assertEqual(ca.main([self.root, "--no-color", "--today", "2026-10-31"]), 0)

    def _messy(self):
        for name in ("a", "b", "c"):
            self.write("%s/CLAUDE.md" % name,
                       "Last Updated: 2024-01-01\n" + BLOCK + "\n" + "pad\n" * 300)

    def test_fail_under_exits_1_below_threshold(self):
        self._messy()
        self.assertEqual(
            ca.main([self.root, "--no-color", "--today", "2026-10-31",
                     "--fail-under", "70"]), 1)

    def test_fail_under_exits_0_above_threshold(self):
        self.write("CLAUDE.md", "# Clean\n\nLast Updated: 2026-10-30\n")
        self.assertEqual(
            ca.main([self.root, "--no-color", "--today", "2026-10-31",
                     "--fail-under", "70"]), 0)

    def test_no_fail_under_never_errors(self):
        self._messy()
        self.assertEqual(
            ca.main([self.root, "--no-color", "--today", "2026-10-31"]), 0)

    def test_fail_under_applies_in_json_mode(self):
        self._messy()
        self.assertEqual(
            ca.main([self.root, "--json", "--today", "2026-10-31",
                     "--fail-under", "70"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
