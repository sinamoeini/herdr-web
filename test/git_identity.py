#!/usr/bin/env python3
"""Commit identity through the real HTTP route and disposable Git repositories."""

import json
import subprocess

from browser import wait_url
from harness import BINARY, Fixture


def run_git_identity():
    fixture = Fixture('git-identity')
    evidence = {}
    try:
        home = fixture.root / 'home'
        home.mkdir()
        global_config = fixture.root / 'global-config'
        global_config.write_text('')
        environment = {key: value for key, value in fixture.environment.items()
                       if not key.startswith('GIT_') and key != 'EMAIL'}
        environment.update(HOME=str(home), GIT_CONFIG_GLOBAL=str(global_config), GIT_CONFIG_NOSYSTEM='1')
        fixture.environment = {
            **environment,
            'GIT_AUTHOR_NAME': 'Ignored author',
            'GIT_AUTHOR_EMAIL': 'ignored-author@example.invalid',
            'GIT_COMMITTER_NAME': 'Ignored committer',
            'GIT_COMMITTER_EMAIL': 'ignored-committer@example.invalid',
            'EMAIL': 'ignored-fallback@example.invalid',
            'GIT_CONFIG_COUNT': '1',
            'GIT_CONFIG_KEY_0': 'user.name',
            'GIT_CONFIG_VALUE_0': 'Ignored runtime identity',
            'GIT_CONFIG_PARAMETERS': "'user.email=ignored-runtime@example.invalid'",
            'GIT_DIR': str(fixture.root / 'not-a-repository'),
            'GIT_WORK_TREE': str(fixture.root / 'not-a-worktree'),
        }

        def git(repository, *arguments):
            result = subprocess.run(['git', '-C', str(repository), *arguments],
                                    env=environment, capture_output=True, text=True, check=True)
            return result.stdout.strip()

        def config(path, key, value):
            git(fixture.root, 'config', '--file', str(path), key, value)

        def repository(name):
            path = fixture.root / name
            path.mkdir()
            git(path, 'init', '-q')
            return path

        def start_web():
            fixture.web = fixture.process([str(BINARY), '--diagnostics'], 'web.log', stdout=subprocess.PIPE)
            fixture.url = wait_url(fixture.web)

        def author(repository):
            return git(repository, 'log', '-1', '--format=%an%x00%ae%x00%cn%x00%ce').split('\0')

        def commit(repository, label, expected):
            (repository / 'file.txt').write_text(label + '\n')
            staged = fixture.post('/api/git', {'cwd': str(repository), 'args': ['add', '-A']})
            assert staged['code'] == 0, staged
            status, result = fixture.http('/api/git', {'cwd': str(repository), 'args': ['commit', '-m', label]})
            assert status == 200 and result['code'] == 0, (label, status, result)
            actual = author(repository)
            assert actual == expected, (label, actual, expected)
            return result

        global_identity = ["Global ' Fixture = $HOME", 'global@example.invalid']
        config(global_config, 'user.name', global_identity[0])
        config(global_config, 'user.email', global_identity[1])
        repo = repository('repo with spaces')
        git(repo, 'commit', '--allow-empty', '-m', 'Normal Git baseline')
        expected = author(repo)
        assert expected == global_identity * 2
        start_web()
        commit(repo, 'global-only', expected)
        evidence['global_identity_matches_normal_git'] = True
        evidence['inherited_git_identity_and_runtime_injection_ignored'] = True

        local_identity = ['Local Fixture', 'local@example.invalid']
        git(repo, 'config', 'user.name', local_identity[0])
        git(repo, 'config', 'user.email', local_identity[1])
        commit(repo, 'local-override', local_identity * 2)
        evidence['repository_identity_overrides_global'] = True

        git(repo, 'config', '--unset', 'user.name')
        git(repo, 'config', '--unset', 'user.email')
        included = fixture.root / 'included-config'
        included.write_text('')
        conditional_identity = ['Conditional Fixture', 'conditional@example.invalid']
        config(included, 'user.name', conditional_identity[0])
        config(included, 'user.email', conditional_identity[1])
        config(global_config, f'includeIf.gitdir:{repo}/.git.path', str(included))
        assert git(repo, 'config', '--get', 'user.name') == conditional_identity[0]
        commit(repo, 'conditional-include', conditional_identity * 2)
        evidence['conditional_global_include'] = True

        worktree = fixture.root / 'linked worktree'
        git(repo, 'worktree', 'add', '-q', '-b', 'identity-worktree', str(worktree), 'HEAD')
        git(repo, 'config', 'extensions.worktreeConfig', 'true')
        worktree_identity = ['Worktree Fixture', 'worktree@example.invalid']
        git(worktree, 'config', '--worktree', 'user.name', worktree_identity[0])
        git(worktree, 'config', '--worktree', 'user.email', worktree_identity[1])
        commit(worktree, 'worktree-override', worktree_identity * 2)
        evidence['linked_worktree_identity'] = True

        role_identity = ['Author Fixture', 'author@example.invalid', 'Committer Fixture', 'committer@example.invalid']
        for key, value in zip(['author.name', 'author.email', 'committer.name', 'committer.email'], role_identity):
            git(repo, 'config', key, value)
        commit(repo, 'distinct-author-and-committer', role_identity)
        evidence['distinct_configured_author_and_committer'] = True

        missing = repository('missing-identity')
        git(missing, '-c', 'user.name=Baseline', '-c', 'user.email=baseline@example.invalid',
            'commit', '--allow-empty', '-m', 'Baseline')
        (missing / 'file.txt').write_text('Must remain staged\n')
        fixture.post('/api/git', {'cwd': str(missing), 'args': ['add', '-A']})
        before = git(missing, 'rev-parse', 'HEAD')
        index = git(missing, 'write-tree')
        for content in ['', '[user]\n name = Name only\n', '[user]\n name = Name only\n email =\n',
                        '[user]\n name = Name only\n email = fixture@example.invalid\n[author]\n email = "  "\n']:
            global_config.write_text(content)
            status, result = fixture.http('/api/git', {'cwd': str(missing), 'args': ['commit', '-m', 'Must fail']})
            assert status == 422 and 'Configure Git' in result['error'], (content, status, result)
            assert git(missing, 'rev-parse', 'HEAD') == before
            assert git(missing, 'write-tree') == index
        evidence['missing_or_empty_identity_preserves_head_and_index'] = True
        global_config.write_text('[invalid configuration\n')
        status, result = fixture.http('/api/git', {'cwd': str(missing), 'args': ['commit', '-m', 'Must fail']})
        assert status == 422 and 'bad config' in result['stderr'], result
        global_config.write_text('')
        assert git(missing, 'rev-parse', 'HEAD') == before
        evidence['configuration_read_failure_blocks_commit'] = True

        # Exercise normal HOME/XDG discovery as well as an explicit global file.
        fixture.web.terminate()
        fixture.web.wait(timeout=20)
        del environment['GIT_CONFIG_GLOBAL']
        del fixture.environment['GIT_CONFIG_GLOBAL']
        global_config = home / '.gitconfig'
        xdg_config = fixture.root / 'c' / 'git' / 'config'
        xdg_config.parent.mkdir()
        xdg_config.write_text('[user]\n name = XDG Fixture\n email = xdg@example.invalid\n')
        home_identity = ['Home Fixture', 'home@example.invalid']
        config(global_config, 'user.name', home_identity[0])
        config(global_config, 'user.email', home_identity[1])
        start_web()
        discovered = repository('discovered-identity')
        commit(discovered, 'home-overrides-xdg', home_identity * 2)
        global_config.unlink()
        commit(discovered, 'xdg-only', ['XDG Fixture', 'xdg@example.invalid'] * 2)
        evidence['home_and_xdg_global_discovery'] = True

        # These harmless programs leave evidence if restricted Git executes them.
        marker = fixture.root / 'external-program-ran'
        program = fixture.root / 'external-program'
        program.write_text('#!/bin/sh\nprintf executed > "' + str(marker) + '"\nexit 1\n')
        program.chmod(0o700)
        hooks = fixture.root / 'hooks'
        hooks.mkdir()
        (hooks / 'pre-commit').symlink_to(program)
        for key, value in {
            'core.hooksPath': str(hooks), 'core.fsmonitor': str(program),
            'core.pager': str(program), 'pager.config': 'true', 'pager.commit': 'true',
            'commit.gpgSign': 'true', 'gpg.program': str(program),
            'diff.external': str(program), 'filter.fixture.clean': str(program),
            'filter.fixture.required': 'true',
        }.items():
            config(global_config, key, value)
        (discovered / '.gitattributes').write_text('file.txt filter=fixture\n')
        configuration_before = global_config.read_bytes()
        commit(discovered, 'restricted-global-configuration', ['XDG Fixture', 'xdg@example.invalid'] * 2)
        (discovered / 'file.txt').write_text('diff remains internal\n')
        diff = fixture.post('/api/git', {'cwd': str(discovered), 'args': ['diff', '--no-color']})
        assert diff['code'] == 0 and '+diff remains internal' in diff['stdout'], diff
        assert not marker.exists(), 'Git executed a restricted external program'
        assert global_config.read_bytes() == configuration_before
        evidence['hooks_signing_pager_monitor_filter_and_external_diff_remain_disabled'] = True
        (fixture.logs / 'results.json').write_text(json.dumps(evidence, indent=2) + '\n')
        return evidence
    finally:
        fixture.close()


if __name__ == '__main__':
    print(json.dumps(run_git_identity(), indent=2), flush=True)
