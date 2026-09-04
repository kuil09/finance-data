# Luna Chat Coder entry point

When repository development is requested from a chat surface with a disposable or sandboxed code-execution environment, read `.agents/skills/luna-chat-coder/SKILL.md` before working on the repository task.

Loading the skill is a readiness step, not a reason to use GitHub Actions. Normal engineering work should stay in the chat sandbox work container when it is available and sufficient.

The repository itself defines its runtimes, services, dependencies, architecture, build system, and verification requirements. Luna Chat Coder supplies continuity, exact transport, and bounded fallback capability; it does not introduce a development methodology or substitute technologies merely because they are easier to run.

Treat exact GitHub commit and PR state as durable source truth, preserve unrelated work, and do not make access to the user's computer a dependency of the workflow.

When this repository is used as a template, keep this entry point and add the project's own engineering instructions alongside it.

# Repository workflow

This repository uses an issue-first autonomous development workflow. GitHub is the durable record of user intent, implementation context, review state, and completed work.

## 1. Convert actionable requests into issues first

Do not begin repository changes directly from an actionable user instruction.

Before implementation:

1. Search for an existing issue that already represents the request.
2. If no suitable issue exists, create one.
3. Record the user's intent, expected outcome, relevant constraints, and initial scope in the issue.

A chat message is an input to the workflow. The GitHub issue becomes the durable task record.

Trivial conversational questions that require no repository change do not need issues.

## 2. Use the issue as working memory

Keep material task context in the issue while work proceeds. Do not leave important reasoning or discoveries only in the transient chat history.

Update the issue when work reveals information that can affect later implementation, review, debugging, or continuation, including:

- relevant repository or external-system discoveries;
- architectural or implementation decisions;
- assumptions that were validated or rejected;
- scope changes;
- blockers and their resolution;
- source-data or API behavior that matters to the implementation;
- important alternatives considered;
- verification results and known limitations.

Do not turn the issue into a raw execution log. Record durable context and decisions, not every command or intermediate action.

## 3. Work on a dedicated branch

Do not implement repository changes directly on `main`.

Create a task-specific branch from the current target branch. Keep unrelated changes out of the branch and preserve concurrent work.

Prefer branch names that make the linked task recognizable, for example:

```text
feat/12-eia-adapter
fix/31-null-period-handling
docs/7-dataset-contract
```

## 4. Open a pull request linked to the issue

When the implementation is coherent enough to review, open a pull request against the intended target branch.

The pull request should:

- link the issue using `Closes #<issue>` when completing it;
- summarize what changed;
- state how the change was verified;
- call out meaningful limitations, migrations, or follow-up work;
- contain only changes relevant to the issue.

The PR is the durable review record; the issue remains the durable task and context record.

## 5. Review and verify autonomously

The agent is responsible for reviewing its own change before merge.

Run the repository's available tests, linters, type checks, schema checks, builds, or other relevant verification. Inspect the final diff for unintended changes and verify that the result still matches the issue rather than merely the latest implementation path.

If verification uncovers material context, record it in the issue or PR before continuing.

Do not ask the user for routine approval of implementation details, branch creation, PR creation, or merge when the user's requested intent is already clear. Ask only when a genuinely unresolved product or safety decision cannot be determined from repository context, existing issues, or the user's request.

## 6. Merge autonomously when complete

When all of the following are true, merge the pull request without waiting for an additional user instruction:

- the issue requirements are satisfied;
- relevant verification has passed or any accepted limitation is explicitly documented;
- the final diff has been reviewed;
- required repository checks and branch rules permit the merge;
- no unresolved review feedback or merge conflict remains.

Use the repository's preferred merge method when one is defined. Otherwise prefer squash merge for a focused task branch unless preserving individual commits has clear value.

After merge, ensure the linked issue is closed, either automatically through the PR linkage or explicitly when necessary.

## 7. Continue existing work instead of duplicating it

Before creating new issues or PRs, search for existing open work that matches the request. If a relevant issue or PR already exists, continue from that durable state rather than creating parallel duplicates.

When resuming work from another chat or agent, recover context from the issue, PR, branch, and exact GitHub state first. Do not reconstruct authoritative task state from conversation memory when GitHub contains the durable record.

## 8. Preserve the boundary between intent and implementation

Issues should describe the problem, intent, constraints, and durable discoveries. They should not be rewritten merely to mirror whichever implementation was chosen.

Implementation may change while the issue remains the stable statement of what needs to be accomplished.

The default lifecycle for repository-changing work is therefore:

```text
User request
    ↓
Find or create GitHub issue
    ↓
Record intent and constraints
    ↓
Create task branch
    ↓
Implement and verify
    ↓
Record durable discoveries/context in issue
    ↓
Open linked pull request
    ↓
Self-review and repository checks
    ↓
Autonomous merge
    ↓
Close issue
```
