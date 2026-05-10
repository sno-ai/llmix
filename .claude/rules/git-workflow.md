---
paths: ["**/*"]
---

# Git Workflow Rules

| # | Rule |
|---|------|
| 1 | SHALL commit with descriptive messages: `type(scope): description` |
| 2 | SHALL run the narrowest relevant validation before committing changed files |
| 3 | SHALL NOT use `git worktree remove --force` without explicit approval |
| 4 | SHALL NOT run `git push --force` to main/master without explicit approval |
| 5 | SHALL NOT commit `.env`, `.env.local`, `.npmrc`, or files containing API keys/tokens |

## Safe Worktree Removal

```bash
# Unlink worktree, preserve files:
rm /path/to/worktree/.git && git worktree prune
```

Two worktrees cannot check out the same branch simultaneously.
