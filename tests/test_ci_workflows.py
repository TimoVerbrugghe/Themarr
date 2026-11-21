"""Regression tests for CI workflow behavior."""
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/dependabot-auto-merge.yml'
SUCCESSFUL_JOBS = (
    'Unit tests\tsuccess\n'
    'Container build and runtime tests\tsuccess\n'
    'Review dependency changes\tsuccess\n'
)


@pytest.mark.parametrize(
    ('pr_number', 'current_head', 'jobs', 'api_status', 'expected_calls', 'expected_status'),
    [
        ('100', 'tested-sha', SUCCESSFUL_JOBS, 0, ['api', 'view', 'merge'], 0),
        ('100', 'new-sha', SUCCESSFUL_JOBS, 0, ['api', 'view'], 0),
        ('', 'tested-sha', SUCCESSFUL_JOBS, 0, [], 0),
        ('100', 'tested-sha', 'Unit tests\tsuccess\n', 0, ['api'], 1),
        ('100', 'tested-sha', SUCCESSFUL_JOBS.replace('Unit tests\tsuccess', 'Unit tests\tfailure'), 0, ['api'], 1),
        ('100', 'tested-sha', SUCCESSFUL_JOBS.replace('runtime tests\tsuccess', 'runtime tests\tfailure'), 0, ['api'], 1),
        ('100', 'tested-sha', SUCCESSFUL_JOBS.replace('changes\tsuccess', 'changes\tfailure'), 0, ['api'], 1),
        ('100', 'tested-sha', SUCCESSFUL_JOBS, 1, ['api'], 1),
    ],
)
def test_dependabot_merge_requires_all_checks_on_tested_head(
    tmp_path, pr_number, current_head, jobs, api_status, expected_calls, expected_status,
):
    workflow = WORKFLOW.read_text()
    match = re.search(
        r'(?ms)^      - name: Merge tested Dependabot update\n'
        r'        run: \|\n(.*?)^        env:',
        workflow,
    )
    assert match is not None
    script = textwrap.dedent(match.group(1))

    gh = tmp_path / 'gh'
    gh.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = api ]; then\n'
        '  printf "%s\\n" "$*" >> "$GH_CALLS"\n'
        '  printf "%s" "$JOB_RESULTS"\n'
        '  exit "$API_STATUS"\n'
        'fi\n'
        'case " $* " in\n'
        '  *" --repo $GITHUB_REPOSITORY "*) ;;\n'
        '  *) echo "Missing repository" >&2; exit 1 ;;\n'
        'esac\n'
        'printf "%s\\n" "$*" >> "$GH_CALLS"\n'
        'if [ "$2" = view ]; then printf "%s\\n" "$CURRENT_HEAD"; fi\n'
    )
    gh.chmod(0o755)
    calls = tmp_path / 'calls'
    env = os.environ | {
        'PATH': f'{tmp_path}{os.pathsep}{os.environ["PATH"]}',
        'GITHUB_REPOSITORY': 'owner/repo',
        'PR_NUMBER': pr_number,
        'HEAD_SHA': 'tested-sha',
        'RUN_ID': '123',
        'CURRENT_HEAD': current_head,
        'JOB_RESULTS': jobs,
        'API_STATUS': str(api_status),
        'GH_CALLS': str(calls),
    }
    result = subprocess.run(
        ['bash', '-e', '-c', script], cwd=tmp_path, env=env,
        capture_output=True, text=True,
    )

    assert result.returncode == expected_status, result.stderr
    actual_calls = calls.read_text().splitlines() if calls.exists() else []
    expected_commands = {
        'api': 'api repos/owner/repo/actions/runs/123/jobs --paginate --jq .jobs[] | [.name, .conclusion] | @tsv',
        'view': 'pr view 100 --repo owner/repo --json headRefOid --jq .headRefOid',
        'merge': 'pr merge 100 --repo owner/repo --squash --delete-branch --match-head-commit tested-sha',
    }
    assert actual_calls == [expected_commands[call] for call in expected_calls]
