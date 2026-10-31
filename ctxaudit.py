#!/usr/bin/env python3
"""
ctxaudit - audit the real cost of your CLAUDE.md tree.

Claude Code loads CLAUDE.md from your working directory AND every parent
directory, every single session. In a repo with nested context files that
cost is invisible: you never see the number, you just feel the symptoms --
instructions getting ignored, stale rules resurfacing, context running out
early.

This tool makes the number visible, then tells you what to cut.

Zero dependencies. Python 3.8+. Reads only; never writes to your files.

Usage:
    python3 ctxaudit.py [PATH] [options]

Options:
    --json              Machine-readable output.
    --max-lines N       Per-file line budget (default 200, per Anthropic guidance).
    --stale-days N      Flag "Last Updated" older than N days (default 90).
    --top N             Show N worst entries per section (default 5).
    --min-dup-words N   Ignore duplicate blocks shorter than N words (default 12).
    --no-color          Disable ANSI colour.
    --today YYYY-MM-DD  Override today's date (for reproducible reports).
    -h, --help          This message.
"""

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import sys
from collections import defaultdict

__version__ = "1.0.0"

# Files Claude Code treats as directory-scoped instruction files.
CONTEXT_FILENAMES = ("CLAUDE.md", "CLAUDE.local.md")

# Directories never worth walking into.
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".next", ".cache", "vendor",
    ".stversions", ".stfolder", "site-packages", ".tox", ".ruff_cache",
}

# Rough chars-per-token for English prose + markdown. An estimate, not a
# tokeniser -- good to within ~10% for this kind of text, and the relative
# numbers (which file is worst) are what the report is actually for.
CHARS_PER_TOKEN = 4.0

DATE_FIELD_RE = re.compile(
    r"^\s*(?:[*_>#\s-]*)?\**\s*(?:last\s+updated|updated|last\s+reviewed|date)\s*\**\s*[:\-]\s*\**\s*"
    r"(\d{4})-(\d{2})-(\d{2})",
    re.IGNORECASE | re.MULTILINE,
)
ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$", re.MULTILINE)
CODE_FENCE_RE = re.compile(r"^```")

# Markdown noise stripped before comparing blocks for duplication.
NORMALISE_STRIP_RE = re.compile(r"[^a-z0-9\s]+")
WHITESPACE_RE = re.compile(r"\s+")


class Colour:
    def __init__(self, enabled):
        self.enabled = enabled

    def _w(self, code, text):
        if not self.enabled:
            return text
        return "\033[%sm%s\033[0m" % (code, text)

    def bold(self, t):
        return self._w("1", t)

    def dim(self, t):
        return self._w("2", t)

    def red(self, t):
        return self._w("31", t)

    def yellow(self, t):
        return self._w("33", t)

    def green(self, t):
        return self._w("32", t)

    def cyan(self, t):
        return self._w("36", t)


def est_tokens(text):
    """Estimated token count for a string."""
    return int(round(len(text) / CHARS_PER_TOKEN))


def human(n):
    """Compact number formatting: 1234 -> 1.2k."""
    if n >= 10000:
        return "%.0fk" % (n / 1000.0)
    if n >= 1000:
        return "%.1fk" % (n / 1000.0)
    return str(n)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def discover(root):
    """Walk root, returning context files as a list of dicts."""
    found = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in place so os.walk does not descend.
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".stversions")
        )
        for name in CONTEXT_FILENAMES:
            if name in filenames:
                full = os.path.join(dirpath, name)
                rec = read_context_file(full, root)
                if rec is not None:
                    found.append(rec)
    found.sort(key=lambda r: r["relpath"])
    return found


def read_context_file(path, root):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except (IOError, OSError) as exc:
        sys.stderr.write("warning: could not read %s (%s)\n" % (path, exc))
        return None

    lines = text.splitlines()
    return {
        "path": path,
        "relpath": os.path.relpath(path, root) or os.path.basename(path),
        "dir": os.path.dirname(path),
        "text": text,
        "lines": len(lines),
        "chars": len(text),
        "tokens": est_tokens(text),
        "headings": [m.group(2).strip() for m in HEADING_RE.finditer(text)],
        "last_updated": parse_last_updated(text),
        "blocks": extract_blocks(text),
    }


def parse_last_updated(text):
    """Explicit 'Last Updated:' field if present, else newest ISO date found."""
    best = None
    for m in DATE_FIELD_RE.finditer(text):
        d = safe_date(m.group(1), m.group(2), m.group(3))
        if d and (best is None or d > best):
            best = d
    if best:
        return {"date": best, "source": "field"}

    best = None
    for m in ISO_DATE_RE.finditer(text):
        d = safe_date(m.group(1), m.group(2), m.group(3))
        if d and (best is None or d > best):
            best = d
    if best:
        return {"date": best, "source": "mention"}
    return None


def safe_date(y, mo, d):
    try:
        return datetime.date(int(y), int(mo), int(d))
    except ValueError:
        return None


def extract_blocks(text):
    """
    Split into comparable blocks (paragraphs / list runs), skipping fenced
    code. Each block keeps its starting line number for citable output.
    """
    blocks = []
    current = []
    start_line = 1
    in_fence = False

    for idx, raw in enumerate(text.splitlines(), start=1):
        if CODE_FENCE_RE.match(raw.strip()):
            in_fence = not in_fence
            if current:
                blocks.append(_mkblock(current, start_line))
                current = []
            continue
        if in_fence:
            continue

        if raw.strip():
            if not current:
                start_line = idx
            current.append(raw)
        else:
            if current:
                blocks.append(_mkblock(current, start_line))
                current = []

    if current:
        blocks.append(_mkblock(current, start_line))
    return [b for b in blocks if b["words"]]


def _mkblock(lines, start_line):
    raw = "\n".join(lines)
    norm = normalise(raw)
    words = norm.split()
    return {
        "raw": raw,
        "norm": norm,
        "words": words,
        "line": start_line,
        "tokens": est_tokens(raw),
    }


def normalise(text):
    """Lowercase, strip markdown punctuation, collapse whitespace."""
    t = text.lower()
    t = NORMALISE_STRIP_RE.sub(" ", t)
    return WHITESPACE_RE.sub(" ", t).strip()


# --------------------------------------------------------------------------
# Analysis: load simulation
# --------------------------------------------------------------------------

def simulate_loads(files, root):
    """
    For each directory containing a context file -- a realistic place to
    launch Claude -- compute everything that gets loaded from there.

    Claude Code walks from cwd up through every parent, so a deep project
    inherits every ancestor's instructions whether they are relevant or not.
    """
    by_dir = defaultdict(list)
    for f in files:
        by_dir[f["dir"]].append(f)

    root = os.path.abspath(root)
    sims = []
    for d in sorted(by_dir):
        chain = []
        cur = d
        while True:
            for f in by_dir.get(cur, []):
                chain.append(f)
            if os.path.abspath(cur) == root:
                break
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

        # Nearest-first reads better in a report than filesystem order.
        chain.sort(key=lambda f: len(f["dir"]), reverse=True)
        own = [f for f in chain if f["dir"] == d]
        inherited = [f for f in chain if f["dir"] != d]
        total = sum(f["tokens"] for f in chain)
        sims.append({
            "dir": d,
            "reldir": os.path.relpath(d, root) or ".",
            "files": chain,
            "own_tokens": sum(f["tokens"] for f in own),
            "inherited_tokens": sum(f["tokens"] for f in inherited),
            "total_tokens": total,
            "file_count": len(chain),
        })
    sims.sort(key=lambda s: s["total_tokens"], reverse=True)
    return sims


# --------------------------------------------------------------------------
# Analysis: duplication
# --------------------------------------------------------------------------

SHINGLE = 5
MIN_SHARED_SHINGLES = 2

# Similarity is scored with difflib on normalised text, not on shingle
# overlap. Shingle Jaccard is far too harsh on short blocks -- editing one
# word in a 22-word paragraph knocks overlap down to ~0.57, which would miss
# the single most common real case: a block copy-pasted and lightly tweaked.
# Shingles are still used to shortlist candidate pairs cheaply.
NEAR_DUP_THRESHOLD = 0.85


def similarity(a, b):
    """
    Ratio in [0,1] over normalised text. quick_ratio is a cheap upper bound,
    so bailing on it first avoids the expensive comparison for most pairs.
    """
    sm = difflib.SequenceMatcher(None, a, b)
    if sm.quick_ratio() < NEAR_DUP_THRESHOLD:
        return 0.0
    return sm.ratio()


def shingles(words, n=SHINGLE):
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def find_duplication(files, min_words):
    """
    Cross-file duplicate and near-duplicate blocks, grouped into clusters.

    Reporting raw pairs is useless: one boilerplate block shared by 10 files
    is 45 pairs describing a single problem, and summing their tokens
    massively overstates the waste. So candidate pairs are unioned into
    clusters, and each cluster is reported once -- "this block appears in N
    files, reclaim X tokens by keeping one copy."

    An inverted shingle index shortlists candidates, so this stays usable on
    large repos instead of comparing every block against every other.
    """
    blocks = []
    for f in files:
        for b in f["blocks"]:
            if len(b["words"]) < min_words:
                continue
            blocks.append({
                "file": f,
                "block": b,
                "norm": b["norm"],
                "sh": shingles(b["words"]),
                "fp": hashlib.sha1(b["norm"].encode("utf-8")).hexdigest(),
            })

    if not blocks:
        return []

    index = defaultdict(list)
    for i, item in enumerate(blocks):
        for s in item["sh"]:
            index[s].append(i)

    pair_hits = defaultdict(int)
    for ids in index.values():
        # A shingle in a huge number of blocks is structural noise, and the
        # pair count from it would dominate everything else.
        if len(ids) > 40:
            continue
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                pair_hits[(ids[a], ids[b])] += 1

    uf = UnionFind(len(blocks))
    linked = set()
    for (i, j), hits in pair_hits.items():
        if hits < MIN_SHARED_SHINGLES:
            continue
        A, B = blocks[i], blocks[j]
        if A["fp"] == B["fp"] or similarity(A["norm"], B["norm"]) >= NEAR_DUP_THRESHOLD:
            uf.union(i, j)
            linked.add(i)
            linked.add(j)

    groups = defaultdict(list)
    for i in linked:
        groups[uf.find(i)].append(i)

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        paths = {blocks[i]["file"]["relpath"] for i in members}
        # Repetition inside a single file is a style issue, not context tax.
        if len(paths) < 2:
            continue

        toks = sorted((blocks[i]["block"]["tokens"] for i in members), reverse=True)
        # Keep one copy, delete the rest.
        reclaimable = sum(toks[1:])
        exact = len({blocks[i]["fp"] for i in members}) == 1
        locations = sorted(
            ({"relpath": blocks[i]["file"]["relpath"],
              "line": blocks[i]["block"]["line"]} for i in members),
            key=lambda x: (x["relpath"], x["line"]),
        )
        biggest = max(members, key=lambda i: blocks[i]["block"]["tokens"])
        clusters.append({
            "copies": len(members),
            "files": len(paths),
            "exact": exact,
            "reclaimable_tokens": reclaimable,
            "block_tokens": toks[0],
            "locations": locations,
            "excerpt": excerpt(blocks[biggest]["block"]["raw"]),
        })

    clusters.sort(key=lambda c: (-c["reclaimable_tokens"], -c["copies"]))
    return clusters


def excerpt(raw, limit=100):
    flat = WHITESPACE_RE.sub(" ", raw.strip())
    if len(flat) <= limit:
        return flat
    return flat[:limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# Analysis: conflicting headings
# --------------------------------------------------------------------------

def find_heading_collisions(files):
    """
    The same section title in several files is where contradictory rules
    hide: two 'Testing' sections in one load chain, saying different things,
    and no signal about which wins.
    """
    index = defaultdict(list)
    for f in files:
        for h in f["headings"]:
            key = normalise(h)
            if len(key) < 3:
                continue
            index[key].append(f["relpath"])

    out = []
    for key, paths in index.items():
        uniq = sorted(set(paths))
        if len(uniq) >= 2:
            out.append({"heading": key, "files": uniq, "count": len(uniq)})
    out.sort(key=lambda x: -x["count"])
    return out


# --------------------------------------------------------------------------
# Analysis: staleness and bloat
# --------------------------------------------------------------------------

def find_stale(files, today, stale_days):
    out = []
    for f in files:
        lu = f["last_updated"]
        if not lu:
            out.append({
                "relpath": f["relpath"], "age_days": None,
                "date": None, "source": "none",
            })
            continue
        age = (today - lu["date"]).days
        if age >= stale_days:
            out.append({
                "relpath": f["relpath"], "age_days": age,
                "date": lu["date"].isoformat(), "source": lu["source"],
            })
    out.sort(key=lambda x: (x["age_days"] is not None, x["age_days"] or 0), reverse=True)
    return out


def find_bloat(files, max_lines):
    out = [
        {
            "relpath": f["relpath"], "lines": f["lines"],
            "tokens": f["tokens"], "over_by": f["lines"] - max_lines,
        }
        for f in files if f["lines"] > max_lines
    ]
    out.sort(key=lambda x: -x["lines"])
    return out


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def score(files, sims, dups, bloat, stale, max_lines):
    """
    0-100, higher is better. Deliberately simple and explainable -- a score
    nobody can interpret is worse than no score.
    """
    if not files:
        return {"score": 100, "grade": "A", "penalties": []}

    penalties = []
    worst = sims[0]["total_tokens"] if sims else 0

    # Worst-case load. 2k tokens of always-on instruction is a reasonable
    # ceiling; past that you are taxing every single session.
    if worst > 2000:
        p = min(30, int((worst - 2000) / 400.0))
        penalties.append(("Heaviest load chain is %s tokens" % human(worst), p))

    wasted = sum(d["reclaimable_tokens"] for d in dups)
    if wasted:
        p = min(25, int(wasted / 60.0) + 3)
        penalties.append(("%s tokens reclaimable from duplication" % human(wasted), p))

    if bloat:
        # Count alone under-reports: 3 oversized files out of 3 is a far worse
        # tree than 3 out of 50, so weight by the share of files affected too.
        frac = len(bloat) / float(len(files))
        p = min(20, int(4 * len(bloat) + 8 * frac))
        penalties.append((
            "%d of %d file(s) over %d lines" % (len(bloat), len(files), max_lines), p))

    undated = [s for s in stale if s["age_days"] is None]
    dated = [s for s in stale if s["age_days"] is not None]
    if dated:
        p = min(15, 3 * len(dated))
        penalties.append(("%d file(s) stale" % len(dated), p))
    if undated:
        p = min(10, 2 * len(undated))
        penalties.append(("%d file(s) with no date marker" % len(undated), p))

    total = max(0, 100 - sum(p for _, p in penalties))
    if total >= 90:
        grade = "A"
    elif total >= 75:
        grade = "B"
    elif total >= 60:
        grade = "C"
    elif total >= 40:
        grade = "D"
    else:
        grade = "F"
    return {"score": total, "grade": grade, "penalties": penalties}


def recommendations(sims, dups, bloat, stale, max_lines, root_files):
    """Concrete, ordered next actions. No generic advice."""
    recs = []

    if dups:
        wasted = sum(d["reclaimable_tokens"] for d in dups)
        top = dups[0]
        recs.append(
            "Reclaim ~%s tokens by de-duplicating %d repeated block(s). Worst "
            "offender appears in %d files (%s) -- hoist it into the nearest "
            "shared parent CLAUDE.md and delete the %d copies."
            % (human(wasted), len(dups), top["files"],
               ", ".join(l["relpath"] for l in top["locations"][:2]) +
               (" ..." if len(top["locations"]) > 2 else ""),
               top["copies"] - 1)
        )

    if bloat:
        w = bloat[0]
        recs.append(
            "Split %s (%d lines, %d over budget). Move task-specific detail into "
            "the subdirectory it applies to, so it only loads when relevant."
            % (w["relpath"], w["lines"], w["over_by"])
        )

    if sims and sims[0]["inherited_tokens"] > sims[0]["own_tokens"] and sims[0]["inherited_tokens"] > 600:
        s = sims[0]
        recs.append(
            "Working in %s inherits %s tokens from parents vs %s of its own. "
            "Push shared rules down, or trim the ancestors -- you cannot opt out "
            "of parent loading."
            % (s["reldir"], human(s["inherited_tokens"]), human(s["own_tokens"]))
        )

    if root_files:
        rf = max(root_files, key=lambda f: f["tokens"])
        if rf["tokens"] > 800:
            recs.append(
                "%s is loaded by every session in the tree (%s tokens). This is "
                "the highest-leverage file to cut -- every token here is paid "
                "everywhere." % (rf["relpath"], human(rf["tokens"]))
            )

    dated = [s for s in stale if s["age_days"] is not None]
    if dated:
        recs.append(
            "Review %d stale file(s) -- oldest is %s at %d days. Stale "
            "instructions are worse than missing ones: Claude follows them."
            % (len(dated), dated[0]["relpath"], dated[0]["age_days"])
        )

    undated = [s for s in stale if s["age_days"] is None]
    if undated:
        recs.append(
            "Add a 'Last Updated: YYYY-MM-DD' line to %d file(s) with no date "
            "marker, so staleness becomes visible next audit." % len(undated)
        )

    if not recs:
        recs.append("No structural problems found. Re-run after your next batch of context edits.")
    return recs


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def report(data, c, top):
    files = data["files"]
    out = []
    w = out.append

    w("")
    w(c.bold("  ctxaudit %s" % __version__) + c.dim("  -  %s" % data["root"]))
    w("")

    if not files:
        w("  No CLAUDE.md files found under this path.")
        w("")
        return "\n".join(out)

    sc = data["score"]
    grade_colour = c.green if sc["grade"] in ("A", "B") else (
        c.yellow if sc["grade"] == "C" else c.red)
    w("  %s   %s" % (
        grade_colour(c.bold("Context health: %d/100 (%s)" % (sc["score"], sc["grade"]))),
        c.dim("%d files, %s tokens total" % (len(files), human(data["total_tokens"]))),
    ))
    if sc["penalties"]:
        for label, p in sc["penalties"]:
            w("    %s %s" % (c.red("-%-3d" % p), c.dim(label)))
    w("")

    # --- Load simulation: the headline number.
    w(c.bold("  WHAT LOADS, AND WHERE") + c.dim("  (every session pays this)"))
    w("")
    w("  %-42s %9s %9s %7s" % ("working directory", "own", "inherited", "total"))
    w("  " + c.dim("-" * 70))
    for s in data["loads"][:top]:
        flag = ""
        if s["total_tokens"] > 2000:
            flag = c.red(" !")
        elif s["total_tokens"] > 1200:
            flag = c.yellow(" ~")
        w("  %-42s %9s %9s %7s%s" % (
            truncate(s["reldir"], 42),
            human(s["own_tokens"]),
            human(s["inherited_tokens"]),
            c.bold(human(s["total_tokens"])),
            flag,
        ))
    if len(data["loads"]) > top:
        w("  " + c.dim("... and %d more launch points" % (len(data["loads"]) - top)))
    w("")

    # --- Duplication.
    dups = data["duplication"]
    if dups:
        wasted = sum(d["reclaimable_tokens"] for d in dups)
        w(c.bold("  DUPLICATED INSTRUCTIONS") +
          c.dim("  (%d repeated block(s), ~%s tokens reclaimable)"
                % (len(dups), human(wasted))))
        w("")
        for d in dups[:top]:
            kind = "identical" if d["exact"] else "near-identical"
            w("  %s %s" % (
                c.yellow("*"),
                c.bold("%s block in %d files" % (kind, d["files"])),
            ) + c.dim("  reclaim ~%s tokens" % human(d["reclaimable_tokens"])))
            for loc in d["locations"][:4]:
                w("    %s:%d" % (loc["relpath"], loc["line"]))
            if len(d["locations"]) > 4:
                w("    " + c.dim("... +%d more copies" % (len(d["locations"]) - 4)))
            w("    " + c.dim("\"%s\"" % d["excerpt"]))
            w("")
        if len(dups) > top:
            w("  " + c.dim("... and %d more repeated blocks" % (len(dups) - top)))
            w("")

    # --- Oversized files.
    bloat = data["bloat"]
    if bloat:
        w(c.bold("  OVER LINE BUDGET") +
          c.dim("  (>%d lines; longer files are followed less reliably)" % data["max_lines"]))
        w("")
        for b in bloat[:top]:
            w("  %s %-46s %s" % (
                c.red("*"), truncate(b["relpath"], 46),
                c.dim("%d lines, %s tokens" % (b["lines"], human(b["tokens"]))),
            ))
        w("")

    # --- Heading collisions.
    col = data["collisions"]
    if col:
        w(c.bold("  REPEATED SECTION TITLES") +
          c.dim("  (check these agree with each other)"))
        w("")
        for x in col[:top]:
            w("  %s %-30s %s" % (
                c.yellow("*"), truncate(x["heading"], 30),
                c.dim("in %d files: %s" % (x["count"], ", ".join(x["files"][:3]))),
            ))
        w("")

    # --- Staleness.
    stale = data["stale"]
    dated = [s for s in stale if s["age_days"] is not None]
    undated = [s for s in stale if s["age_days"] is None]
    if dated or undated:
        w(c.bold("  FRESHNESS"))
        w("")
        for s in dated[:top]:
            w("  %s %-46s %s" % (
                c.yellow("*"), truncate(s["relpath"], 46),
                c.dim("%d days old (%s)" % (s["age_days"], s["date"])),
            ))
        if undated:
            w("  %s %s" % (c.dim("*"), c.dim(
                "%d file(s) carry no date marker: %s" % (
                    len(undated), ", ".join(s["relpath"] for s in undated[:3])))))
        w("")

    # --- Actions.
    w(c.bold("  DO THIS NEXT"))
    w("")
    for i, r in enumerate(data["recommendations"], 1):
        w("  %s %s" % (c.cyan("%d." % i), wrap(r, 74, "     ")))
    w("")
    w(c.dim("  Token counts are estimates (~%.0f chars/token). Nothing was modified."
            % CHARS_PER_TOKEN))
    w("")
    return "\n".join(out)


def truncate(s, n):
    return s if len(s) <= n else "…" + s[-(n - 1):]


def wrap(text, width, indent):
    words = text.split()
    lines, cur = [], ""
    for word in words:
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    if cur:
        lines.append(cur)
    return ("\n" + indent).join(lines)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def build_report(root, args, today):
    files = discover(root)
    sims = simulate_loads(files, root)
    dups = find_duplication(files, args.min_dup_words)
    bloat = find_bloat(files, args.max_lines)
    stale = find_stale(files, today, args.stale_days)
    collisions = find_heading_collisions(files)
    root_files = [f for f in files if os.path.abspath(f["dir"]) == os.path.abspath(root)]
    sc = score(files, sims, dups, bloat, stale, args.max_lines)

    return {
        "version": __version__,
        "root": os.path.abspath(root),
        "generated": today.isoformat(),
        "max_lines": args.max_lines,
        "files": files,
        "total_tokens": sum(f["tokens"] for f in files),
        "loads": sims,
        "duplication": dups,
        "bloat": bloat,
        "stale": stale,
        "collisions": collisions,
        "score": sc,
        "recommendations": recommendations(
            sims, dups, bloat, stale, args.max_lines, root_files),
    }


def to_json(data):
    """Strip unserialisable internals (file text, block objects)."""
    return {
        "version": data["version"],
        "root": data["root"],
        "generated": data["generated"],
        "score": data["score"]["score"],
        "grade": data["score"]["grade"],
        "penalties": [{"reason": r, "points": p} for r, p in data["score"]["penalties"]],
        "total_tokens": data["total_tokens"],
        "files": [
            {
                "path": f["relpath"], "lines": f["lines"],
                "tokens": f["tokens"],
                "last_updated": f["last_updated"]["date"].isoformat()
                if f["last_updated"] else None,
            }
            for f in data["files"]
        ],
        "loads": [
            {
                "dir": s["reldir"], "own_tokens": s["own_tokens"],
                "inherited_tokens": s["inherited_tokens"],
                "total_tokens": s["total_tokens"],
                "files": [f["relpath"] for f in s["files"]],
            }
            for s in data["loads"]
        ],
        "duplication": data["duplication"],
        "bloat": data["bloat"],
        "stale": data["stale"],
        "collisions": data["collisions"],
        "recommendations": data["recommendations"],
    }


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="ctxaudit", add_help=False,
        description="Audit the real cost of your CLAUDE.md tree.")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--max-lines", type=int, default=200)
    p.add_argument("--stale-days", type=int, default=90)
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--min-dup-words", type=int, default=12)
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--today", default=None)
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("--version", action="store_true")
    args = p.parse_args(argv)

    if args.help:
        sys.stdout.write(__doc__)
        return 0
    if args.version:
        sys.stdout.write("ctxaudit %s\n" % __version__)
        return 0

    if not os.path.isdir(args.path):
        sys.stderr.write("error: not a directory: %s\n" % args.path)
        return 2

    if args.today:
        try:
            y, mo, d = args.today.split("-")
            today = datetime.date(int(y), int(mo), int(d))
        except (ValueError, TypeError):
            sys.stderr.write("error: --today must be YYYY-MM-DD\n")
            return 2
    else:
        today = datetime.date.today()

    data = build_report(args.path, args, today)

    if args.json:
        sys.stdout.write(json.dumps(to_json(data), indent=2) + "\n")
        return 0

    use_colour = (not args.no_color) and sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
    sys.stdout.write(report(data, Colour(use_colour), args.top) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
