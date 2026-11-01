# Bounded local Git API

`POST /api/git` accepts JSON `{ "cwd": "/absolute/repository", "args": [...] }`, matching Origin, and JSON Content-Type. Source of truth for exact forms is `server/src/git.rs::arguments`; no arbitrary tail is allowed after an approved command name.

Supported forms needed by the official Files/Changes/worktree panels include:

- `status`: exact porcelain v1/v2 forms, optional branch, NUL/no-renames/untracked options as enumerated in Rust.
- `rev-parse`: repository/common paths, worktree inspection, verify/abbrev-ref, or bounded `HEAD:PATH` lookup.
- `show-ref`: heads/tags or exact verify/quiet reference.
- `for-each-ref`: the short-name and short-name/committer-date formats for local heads.
- `rev-list`: count a reference or the specific local unpushed-commit inspection form.
- `cat-file`: type/size inspection, or `.gitmodules` presence.
- `config`: only exact branch remote/merge and submodule-path queries, including `.gitmodules` blob queries.
- `diff`: only `HEAD`, `-z`, `--no-renames`, `--cached`, `--stat`, `--numstat`, `--name-only`, `--name-status`, `--no-color`, `--no-ext-diff`, `--no-textconv`, `--unified=3`, and bounded paths after `--`, without colon pathspec magic or repeated flags.
- `worktree list --porcelain`, explicitly enumerated `worktree add` forms with absolute destination and safe references, and `worktree remove` forms including the warned forced removal path.
- `add -A`, `add --all`, and `commit -m MESSAGE`. The official Commit control explicitly stages all changes before committing.

Only `-c core.fsmonitor=false` and `-c diff.autoRefreshIndex=false` leading configuration entries are accepted. `--no-optional-locks` is recognized; an absolute `--git-dir` is restricted to config/revision/object inspection. Arbitrary aliases/configuration, output files, network commands, external diffs, and text conversion cannot be requested. References have a conservative bounded alphabet and cannot begin with `-`.

Every child receives explicit argv/cwd and fixed `--no-pager`, `core.fsmonitor=false`, empty `diff.external`, `core.pager=cat`, disabled hooks/commit signing, and `GIT_TERMINAL_PROMPT=0`. Inherited `GIT_*` values are removed; system/global Git config is disabled. Diffs additionally force `--no-ext-diff --no-textconv`. **Repository-local configuration still affects Git: this is not a security sandbox.**

Maximum four jobs; each has a 15-second deadline and 1 MiB each of stdout/stderr. Cancellation/shutdown/error paths kill and reap the exact owned child and release its permit. Nonzero exit returns a failed HTTP status with `{error,code,stdout,stderr}` so callers can distinguish operation failure from an empty successful result. Git's normal “not a repository/no match” inspection exits can therefore appear as 422 in browser developer tools.

Real disposable-repository tests passed status/diff/staging/commit/worktree operations. `test/git_failures.py` also passes real timeout, stdout/stderr overflow, four-job concurrency, requester cancellation, and shutdown child-reaping checks. These service tests do not prove every worktree-dialog interaction; see `docs/ACCEPTANCE.md` for scope. Never test commits/removal against a user's working repository.
