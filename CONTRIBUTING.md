# Contributing to club_cli

This project is **stable**. To keep it that way, all changes must be tracked through GitHub Issues. There are no exceptions — even small fixes get an issue.

## The golden rule

> **No code change without an issue. No merge without a PR linked to that issue.**

This applies to bugs, features, refactors, docs, dependency bumps, test tooling — everything.

---

## Workflow

```
1. Open issue
   ↓
2. Triage: assign labels, mark status:ready
   ↓
3. Create branch from main
   ↓
4. Implement + test locally (just test, both stacks)
   ↓
5. Open PR linked to the issue ("Fixes #N")
   ↓
6. Review → merge to main
   ↓
7. Tag the release on main (RELEASE_VERSION_<major>_<minor>_<patch>)
```

## Conventions

The CLI wraps the [club_server](https://github.com/cloudonlanapps/club_server)
API, and it wraps every feature the server offers, optional parameters
included. `client/tests/test_endpoint_contract.py` enforces this: it reads the
server's `openapi.json` and fails when an operation, a query parameter or a
request-body field has no command that sends it. What is deliberately not sent
is listed in `UNSENT_BY_DESIGN`, with its reason.

When a change follows a club_server change, name the server issue or PR
(`club_server#N`) in the issue and in the PR.

How to *run* the CLI and the tests is in `README.md` (sections *Seed* and
*Test*), not here.

---

## 1. Open an issue

Open an issue at <https://github.com/cloudonlanapps/club_cli/issues/new>.
The repo has no issue templates yet, so say in the title and body which kind it
is:

| Kind | When to use |
|---|---|
| **Bug** | A command is broken or behaves incorrectly |
| **Feature** | A new command or option, often following a server change |
| **Task** | Refactor, docs, help text, tooling, dependency bump, tests |

If your work doesn't fit any kind, the closest match is **Task**.

## Version

`VERSION` at this repo's root is the CLI's only version number.
`client/pyproject.toml` reads it through hatch, and
`client/tests/test_version.py` checks that the installed package reports it.
Do not add a second one.

It moves with club_server's:

| change | `VERSION` |
|---|---|
| a release | **major** |
| a quick fix after a release | **patch** |

A release is tagged `RELEASE_VERSION_<major>_<minor>_<patch>` in club_server,
club_client, club_sdk and club_cli, which take the same major at each release.

## 2. Triage

When an issue is opened, give it `status:triage`. Triage means:

1. Verify it is reproducible / well-scoped
2. Replace `status:triage` with `status:ready` once it's actionable
3. Optionally add a kind label and assign it to a person

An issue parked for an unknown future gets `status:deferred` in place of its
other status label. It stays **open** (deferring is not closing), with a
comment saying why; it moves back to `status:triage` or `status:ready` when
picked up again.

Labels in use:

| Group | Labels |
|---|---|
| Status | `status:triage`, `status:ready`, `status:in-progress`, `status:blocked`, `status:needs-info`, `status:deferred` |
| Kind (optional) | `bug`, `enhancement`, `documentation` and the other GitHub defaults |

Every open issue carries exactly one `status:*` label. Move it to
`status:in-progress` when the branch is created, and remove it when the issue
closes. Priority is not a label here; it is tracked outside GitHub.

## 3. Branch naming

Branches must include the issue number:

```
<type>/<issue-number>-<short-slug>
```

`<type>` is `feature`, `fix`, `docs` or `task`. Examples:

```
fix/25-genders-only
feature/13-genders-list
docs/17-rrule-help-utc
task/23-streamline-version
```

Always branch **from `main`**.

## 4. Commits

- Write commits in the imperative mood: *"fix login redirect"*, not *"fixed login"*
- Reference the issue in the body when context helps: `Refs #42`
- Keep commits focused; squash trivial fixups before opening the PR

## 5. Pull requests

- **Title:** clear, < 70 characters
- **Body:** must include `Fixes #N` (or `Closes #N` / `Refs #N`) so GitHub auto-links and auto-closes
- **Target branch:** `main`
- **One issue per PR** when possible. Bundling is OK only if issues are tightly related.
- **Tests:** `just test` must pass on both stacks (every optional module on, and every module off) before merge. Say in the PR body which tests confirm the change.
- At least one human review for non-trivial changes
- **Server coverage:** a PR that wraps a new server endpoint or option adds the command and its tests in the same PR. Something left unsent on purpose goes into `UNSENT_BY_DESIGN` with its reason.

## 6. Merging

- Merge to `main` is human-gated — no auto-merge
- **Default: squash merge.** `gh pr merge <num> --squash`.
- **Use `--rebase` only for an intentional, individually-meaningful commit series.** Default to squash unless you specifically curated the commits and want each preserved on `main`.
- **Never `--merge`.** A merge commit can drag unrelated ancestry into the target branch; squash lands only the PR's own diff.
- **Do not delete the feature branch on merge.** Branches stay on the remote so the PR's commits remain reachable for archaeology.
- Never push directly to `main`

## 7. Closing issues

- An issue is closed automatically when a PR with `Fixes #N` is merged
- If an issue is invalid or won't be done, close it with a comment explaining why and apply `wontfix` if appropriate

---

## Working with Claude Code

This repo uses [Claude Code](https://claude.com/claude-code) locally as part of the development workflow. Claude is invoked from a developer's terminal and uses the `gh` CLI to interact with issues and pull requests.

A typical Claude-assisted session looks like:

```
You: "implement issue #42"
Claude:
  → gh issue view 42
  → creates branch fix/42-...
  → reads relevant code, edits, runs tests
  → commits
  → confirms with you
  → pushes branch
  → gh pr create --title ... --body "Fixes #42"
```

Conventions Claude must follow on this repo:

1. **Always work from an issue.** If asked to make a change without an issue, Claude should propose creating one first.
2. **Always branch from `main`.**
3. **Never push, open PRs, or merge without explicit user confirmation.**
4. **Always link PRs to their issue** with `Fixes #N` in the PR body.
5. **Never tag a release.** That's a human-only operation.
