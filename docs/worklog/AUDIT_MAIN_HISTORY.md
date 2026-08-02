# Audit: `main`'s git history (2026-08-02)

The working-tree hygiene test and the two layout audits all look at the **tip**.
History is a separate surface: a file deleted from the tip is still fetched by
every clone and still served by the GitHub API. This audit walks what is
reachable from `main` — 430 commits, 1521 distinct paths ever added, 119 of them
since deleted.

**The headline is a negative result, and it is the one that matters:**

    private key blocks       0
    GitHub / PAT tokens      0
    AWS key ids              0
    secret-style assignments 0
                             ...across all 1342 reachable text blobs

**No credential of any kind has ever been committed.** Nor has any game asset —
zero `.nif`, `.dds`, `.bsa`, `.esp`, `.esm`, `.esl`, `.tri` or `.hkx` in the
whole history, so no third-party mod content was ever published.

What follows is mild by comparison. None of it is urgent; all of it is
permanent without a history rewrite.

---

## 1. Three never-track working notes are still in history — MEDIUM

The policy list in `tests/test_public_repo_hygiene.py` exists because these
files name specific mods. All three were committed, then removed from the tip in
`9ad2a97` ("Keep working notes off main") — but removal from the tip does not
remove them from history.

| file | size at deletion | still reachable |
|---|---|---|
| `CLIPPING_LOG.md` | 671 lines, 25 per-piece sections, 6 lines citing a mesh path | 38 object refs |
| `CONVERTER_AUDIT_2026-07-04.md` | 78 lines | 5 |
| `CONVERTER_AUDIT_PLAN_2026-07-04.md` | 110 lines | 5 |

~643 KB across 16 blobs. None of them contains a local filesystem path — the
policy concern here is mod naming, and `CLIPPING_LOG.md` is the substantive one.

The intent was already right in `9ad2a97`; only the mechanism was incomplete.
**The hygiene test cannot catch this class** — it asks "is this file tracked?",
and the answer at the tip is correctly "no".

## 2. Four files carried a local path in older revisions — LOW

    scripts/armor_clip_diag.py        clean at the tip
    scripts/analysis/underbust_census.py  clean at the tip (fixed 9671641)
    scripts/diag_jiggle_batch.py      deleted; history only
    scripts/fix_overlay_mod.py        deleted; history only

(`tests/test_public_repo_hygiene.py` also matches, by design: it holds the
control patterns that prove the rule still fires.)

Both surviving files are clean now. The two deleted diagnostics exist only in
history. A directory layout, not a credential.

## 3. Eleven commit messages contain the deploy path — LOW

The developer's absolute deploy path appears in 11 commit messages, all of the
"rebuild and deploy" kind. Three further matches are test-fixture placeholders
(`C:/Users/v/.ssh/id_rsa`, `C:\Users\...\id_rsa`) and are not real paths.

Commit messages cannot be edited without rewriting history, and unlike file
content they are not covered by any test. Worth a habit change: name the
destination generically in future deploy commits.

## 4. Two personal email addresses are in the commit metadata — MEDIUM (privacy)

    417 commits   DayOnly <DayOnly@users.noreply.github.com>
     11 commits   a personal gmail address    (under two different display names)
      2 commits   a personal icloud address

Author email is public on every commit of a public repo and is exposed through
the API, so those two addresses are harvestable. 97% of commits already use the
GitHub noreply form; 13 do not.

Setting `git config user.email` to the noreply address fixes it going forward.
Fixing the existing 13 requires a rewrite.

## 5. History weight is `dist/`, overwhelmingly — MEDIUM

    dist          934,540,105 bytes   88.6%
    src           108,609,757         10.3%
    tests           3,514,756          0.3%
    everything else                    0.8%
    -------------------------------------------
    total       1,054,205,050 bytes uncompressed  ->  291 MB packed

`src/` being second is the same effect at smaller scale: `nif_convert.py` is
~900 KB and every commit that touches it stores a fresh blob.

This confirms the layout audit from the same day: the repository's size is the
tracked exe, and no amount of tidying elsewhere moves it.

---

## What can actually be done

Nothing here is a credential, so none of it is an emergency.

**Everything in §1–§4 is permanent unless history is rewritten.**
`git filter-repo` can drop the three working-note files and rewrite the author
emails in one pass, but it rewrites every commit SHA after the earliest change.
Consequences to weigh before choosing it:

* every clone and fork must be re-cloned; existing SHAs in issues, links and
  release notes stop resolving;
* it needs a force-push to a public default branch, which is exactly the
  operation branch protection is meant to prevent;
* GitHub retains unreferenced objects until its own GC, and **forks keep the
  old objects regardless** — a rewrite reduces exposure, it does not undo it.

Given the content is working notes and email addresses rather than secrets, the
defensible options are, in order:

1. **Do nothing, change habits.** Set `user.email` to the noreply form; keep
   deploy paths out of commit messages. Cost: zero. The historical content stays.
2. **Rewrite for the emails only**, if the addresses are the actual concern —
   it is the smallest rewrite and the one with a privacy rationale.
3. **Full rewrite** dropping the three working notes as well. Only worth the
   disruption if the mod naming in `CLIPPING_LOG.md` genuinely matters.

A rewrite is not something to do on someone's behalf; §1–§4 are recorded so the
choice can be made deliberately rather than by default.

## Worth adding regardless

The hygiene test guards the tip. Nothing guards a *new* commit that adds a
policy file — it would be caught only on the next run, after the commit exists.
A `pre-commit` hook running the hygiene test would move the guard one step
earlier, to before the object is written. Cheap, and it makes §1 unrepeatable.

---

## Postscript: this document was itself a leak

The first revision of this file quoted both personal email addresses, one real
display name, and the absolute deploy path — as *findings*, in plain text, on a
public branch. A rewrite that strips those from commit metadata while the audit
describing them republishes them as file content achieves nothing.

Caught by re-running the content scan against the REWRITTEN history rather than
trusting that the rewrite had done its job: the scan reported `personal email:
1 path` and named this file.

**An audit that quotes the identifier it is reporting becomes a copy of the
leak.** Report the shape and the count; never the value. The same rule already
applies to mod names elsewhere in this repo — it applies to addresses and paths
too, and the strings are now scrubbed from history by `--replace-text` rather
than only from the tip.
