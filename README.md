# ctxaudit

**See what your `CLAUDE.md` files actually cost you.**

Claude Code loads `CLAUDE.md` from your working directory *and every parent
directory* — every session, before you type anything. In a repo with nested
context files, that bill is invisible. You never see the number. You just feel
the symptoms:

- Claude ignores instructions that are definitely in the file
- Rules you deleted months ago keep resurfacing
- Context runs out earlier than it should
- Two `CLAUDE.md` files disagree and you can't tell which one wins

`ctxaudit` makes the number visible, then tells you what to cut.

```
$ python3 ctxaudit.py .

  Context health: 30/100 (F)   21 files, 44k tokens total
    -6   Heaviest load chain is 4.8k tokens
    -25  1.7k tokens reclaimable from duplication
    -20  5 of 21 file(s) over 200 lines
    -15  18 file(s) stale

  WHAT LOADS, AND WHERE  (every session pays this)

  working directory                                own inherited   total
  ----------------------------------------------------------------------
  02_Health/Fitness                               4.0k       791    4.8k !
  02_Health/Nutrition                             3.8k       791    4.6k !
  03_Finance/Finances                             3.7k       879    4.5k !

  DUPLICATED INSTRUCTIONS  (15 repeated blocks, ~1.7k tokens reclaimable)

  * near-identical block in 8 files  reclaim ~366 tokens
    01_Prompt_Engineering/CLAUDE.md:84
    02_Health/Fitness/CLAUDE.md:85
    ... +6 more copies
    "### Version Management **Bump version when:** Changes to CRAFT-C…"

  DO THIS NEXT

  1. Reclaim ~1.7k tokens by de-duplicating 15 repeated block(s). Worst
     offender appears in 8 files — hoist it into the nearest shared parent
     CLAUDE.md and delete the 7 copies.
```

That's real output from a real 23-file setup, not a mockup.

---

## Install

There is no install. One file, zero dependencies, Python 3.8+.

```bash
curl -O https://raw.githubusercontent.com/YOUR_USER/ctxaudit/main/ctxaudit.py
python3 ctxaudit.py /path/to/your/repo
```

It **only reads**. It never modifies, moves, or deletes anything.

---

## What it checks

### 1. Load simulation — the headline number

For every directory that could be a working directory, `ctxaudit` walks the
parent chain exactly the way Claude Code does and totals the cost. The
`inherited` column is the one that stings: context you're paying for in that
directory but didn't write there, and **cannot opt out of**.

This is usually the finding that changes how people structure their repo.

### 2. Duplicated instructions

Finds blocks repeated across files — exact and near-identical (lightly edited
copy-paste, the most common real case).

Results are reported as **clusters, not pairs**. One boilerplate block shared
by 8 files is a single finding — not 28 pair-wise findings — and the
reclaimable figure counts keeping one copy and deleting the rest. Pair-wise
reporting overstates waste by multiples; this doesn't.

### 3. Over line budget

Anthropic's guidance is to keep `CLAUDE.md` under ~200 lines, because longer
files are followed less reliably. Flags offenders worst-first. Tune with
`--max-lines`.

### 4. Repeated section titles

Two `Testing` sections in one load chain, saying different things, with no
signal about precedence. This is where contradictions hide.

### 5. Freshness

Parses `Last Updated:` / `Last Reviewed:` fields (falling back to the newest
date mentioned) and flags anything past `--stale-days`. Files with no date
marker are listed separately — you can't detect drift you never timestamped.

Stale instructions are worse than missing ones, because Claude follows them.

---

## Usage

```bash
python3 ctxaudit.py [PATH] [options]
```

| Option | Default | Does |
|---|---|---|
| `--json` | off | Machine-readable output, for CI |
| `--max-lines N` | 200 | Per-file line budget |
| `--stale-days N` | 90 | Age at which a file is flagged stale |
| `--top N` | 5 | Entries shown per section |
| `--min-dup-words N` | 12 | Ignore duplicate blocks shorter than this |
| `--no-color` | off | Disable ANSI colour |
| `--today YYYY-MM-DD` | real today | Pin the date for reproducible reports |

### In CI

`--json` gives you a `score` to gate on:

```bash
python3 ctxaudit.py . --json | python3 -c "
import json,sys
d = json.load(sys.stdin)
print('context health:', d['score'], d['grade'])
sys.exit(1 if d['score'] < 70 else 0)"
```

---

## How the numbers work

**Token counts are estimates**, at ~4 characters per token. That's good to
roughly ±10% for English prose and markdown, and the *relative* numbers —
which file is worst, what's worth cutting — are what the report is for. If you
need exact counts, feed the paths to a real tokeniser.

**The health score is a heuristic**, deliberately simple and fully itemised:
every deduction is printed with its reason. Treat it as a smoke alarm, not a
measurement. A tree can score 100 and still contain bad instructions —
`ctxaudit` audits *structure*, not whether your rules are any good.

**What it does not do:** it doesn't read `~/.claude/CLAUDE.md` (your global
file, outside any repo), doesn't resolve `@imports`, and doesn't judge
instruction quality.

---

## Tests

```bash
python3 test_ctxaudit.py
```

41 tests, stdlib only. Covers inheritance arithmetic, cluster collapsing,
reclaimable-token math, date parsing (including malformed dates), code-fence
exclusion, and CLI exit codes.

---

## Licence

MIT.
