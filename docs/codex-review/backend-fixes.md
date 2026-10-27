# Backend lifecycle and fact-check fixes

- Fact checks now reject incomplete structured claims and every non-supported verdict. Common numerical football statistics require a supported corresponding stat claim, an explicitly named grounded player or team stat, or the exact supplied coaching evidence. Score pairs must match the current or a grounded historical score, with formation, unit ranges, and momentum scale notation handled separately. Structured count claims must identify the subject named in the sentence. Reports expose their verification scope explicitly. The blanket allowance for 0–11 has been removed.
- Writer shutdown wakes and joins the writer loop, allowing its detached in-flight batch to complete before a final drain. Failed store batches still follow the existing retry/drop policy; callers must stop producers before closing.
- Session shutdown cancels and joins active signal handlers and restored pipeline jobs before a manager can reset the shared pipeline. Archived or finished sessions do not requeue restored agent jobs.
- Personalised feeds order tied elapsed times by period, evidence-event sequence, then insertion order, without exposing new response fields.
- Replay pacing tracks scaled elapsed time at control changes. Pause interrupts an active wait and freezes progress; speed changes retain the progress already made.

## Verification

`PYTHONPATH=src <backend-venv>/bin/python -m pytest tests/test_backend_lifecycle_regressions.py tests/test_agents.py tests/test_runtime.py tests/test_redesign_p1.py tests/test_store.py tests/test_studio.py tests/test_coach.py -q`: 87 passed, 2 skipped. After the final defensive adjustments, the renderer, feed-ordering, and lifecycle regression tests passed: 11 passed. The parent will run the full suite.

The fact checker is a deterministic guard, not a general semantic verifier: its numeric count recognition covers a defined English vocabulary and it cannot establish all natural-language claims, subject associations, translations, or temporal qualifiers. Structured claims remain necessary for broader verification. No live model or external database test was run.

## Independent second review

Reviewed the core diff and stable synthetic season importer independently. The original normal-shutdown/pause/task leak causes are addressed, but three follow-ups were sent to their implementers:

- **P1, seed integrity:** readback compares expected rows but accepts extra event/frame/signal records and does not validate completion-marker counts. A MemoryStore probe reverified an archive after an extra goal was inserted; reopening changed its score from 2-2 to 3-2. Require exact imported document sets and the complete marker/count contract.
- **P1, count-check regression:** truthful producer/personalizer statements such as a named player's grounded `0 goals` or Northbridge's grounded `0 shots` are blocked because live contexts use `players[]`/`teams{}` and rendering schemas have no claims. Require accurate subject/stat binding while retaining wrong-value/subject rejection.
- **P2, Highlights chronology:** half-time and second-half kickoff share elapsed 2761.4 in seed 7; stable `-elapsed` sorting keeps the older half-time first. Add period/clock/feed-sequence tie ordering and a boundary regression; the frontend Timeline is already correct.

Exact code pointers, reproductions, ownership and acceptance are recorded under “Independent post-fix review” in [backend-review-and-plan.md](backend-review-and-plan.md). These findings refer to the snapshot before the follow-up patches; recheck their regression tests after those land.

Independent targeted run: `tests/test_backend_lifecycle_regressions.py` — **9 passed in 1.76 seconds**. No second full-suite run or database mutation was performed by the reviewer.
