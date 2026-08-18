# Everything this workspace set out to do is MERGED to main (8 squash comm

_2026-08-18 20:00 · persistent_

Everything this workspace set out to do is MERGED to main (8 squash commits: #259 #260 #267 #263 #264 #265 #266 #268), 2542 tests green on main, all todos closed, only #254 left open. Two mechanical lessons from merging a stacked chain with SQUASH: (1) squashing rewrites the parent's commits, so every child must be rebased with 'git rebase --onto origin/main <old-parent-tip>' before it will merge — a plain rebase re-applies them and conflicts; (2) NEVER delete a merged branch while an open PR still targets it — GitHub CLOSES that PR and it cannot be reopened once the base ref is gone (#262 died that way and had to be refiled as #267). Correct order per link: retarget the child to main FIRST, then merge, then delete the parent branch. Better still: retarget the whole chain to main up front.
