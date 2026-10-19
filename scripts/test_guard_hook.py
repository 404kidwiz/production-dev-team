"""Tests for guard_hook.py. Nothing here runs the commands; it only asks the guard what it would decide."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import guard_hook

SCRIPT = [sys.executable, str(Path(guard_hook.__file__))]
ROOT = str(Path(guard_hook.__file__).resolve().parents[1])  # a real folder outside temp, never written to
SECRET = 'sk-live-0123456789abcdef'


def event(command, cwd=ROOT, **extra):
    return {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': {'command': command}, 'cwd': cwd,
            'permission_mode': 'default', **extra}


class GuardCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        os.environ.pop('CLAUDE_PROJECT_DIR', None)
        self.addCleanup(patcher.stop)

    def decision(self, command, **kwargs):
        cwd = kwargs.pop('cwd', ROOT)
        out = guard_hook.decide(event(command, cwd=cwd, **kwargs.pop('extra', {})), **kwargs)
        return out and out['hookSpecificOutput']

    def assertAsks(self, command, needle=None, **kwargs):
        out = self.decision(command, **kwargs)
        self.assertIsNotNone(out, f'should pause: {command!r}')
        self.assertEqual(out['permissionDecision'], 'ask', command)
        if needle:
            self.assertIn(needle, out['permissionDecisionReason'], command)
        return out

    def assertPasses(self, command, **kwargs):
        self.assertIsNone(self.decision(command, **kwargs), f'should pass silently: {command!r}')


class Deletes(GuardCase):
    def test_recursive_removal_of_real_paths_asks(self):
        for command in ('rm -rf data', 'rm -rf ~/', 'rm -fr /', 'sudo rm -r /var/lib/postgres', 'rm -rf "$DIR"', 'rm -rf *',
                        'rm --recursive -- uploads', 'xargs rm -rf', 'find . -name "*.db" -delete',
                        'find data -type f -exec rm {} +', 'dropdb prod'):
            self.assertAsks(command, 'delete' if 'dropdb' not in command else 'drops a database')

    def test_temp_build_output_and_non_recursive_removal_pass(self):
        for command in ('rm -rf node_modules', 'rm -rf dist build .next coverage', 'rm -rf /tmp/pdt-x', 'rm -rf "$TMPDIR/x"',
                        'rm -rf ${TMPDIR}/y', 'rm -rf dist/*', 'rm -rf tmp/cache', 'rm file.txt', 'rm -f a.log',
                        'cd /tmp && rm -rf x', 'find /tmp/x -delete', 'rm -rf build 2>/dev/null', 'ls -la'):
            self.assertPasses(command)

    def test_destructive_sql_through_a_database_client_asks(self):
        for command in ('psql "$DATABASE_URL" -c "DROP TABLE users"', 'sqlite3 app.db "DELETE FROM users WHERE id=1"',
                        'echo "drop database prod;" | mysql', 'psql <<EOF\nTRUNCATE users;\nEOF', 'redis-cli FLUSHALL',
                        'mongosh app --eval "db.users.deleteMany({})"',
                        "python3 -c \"import sqlite3; sqlite3.connect('a.db').execute('DELETE FROM t')\""):
            self.assertAsks(command, 'destructive SQL')

    def test_sql_words_outside_a_database_client_pass(self):
        for command in ('grep -rn "DELETE FROM" src', 'cat migrations/0001_drop_table.sql', 'git log --grep "DROP TABLE"',
                        'psql "$DATABASE_URL" -c "SELECT count(*) FROM users"'):
            self.assertPasses(command)


class Deploys(GuardCase):
    def test_push_deploy_and_publish_ask(self):
        for command in ('git push origin main', 'cd repo && git push', 'git -C app push --force', 'vercel --prod',
                        'vercel deploy', 'npx vercel deploy --prod', 'fly deploy', 'netlify deploy --prod', 'npm publish',
                        'kubectl apply -f k8s/', 'terraform apply -auto-approve', 'gh pr merge 12 --squash', 'docker push me/app',
                        'aws s3 rm s3://bucket/x --recursive', 'gcloud run deploy api', 'supabase functions deploy hello',
                        'helm upgrade api ./chart', 'bash -c "git push"', 'make deploy', 'npm run release'):
            self.assertAsks(command)

    def test_local_and_read_only_commands_pass(self):
        for command in ('git status', 'git push --dry-run', 'git commit -m "push later"', 'vercel dev', 'vercel --version',
                        'kubectl get pods', 'terraform plan', 'gh pr view 12', 'docker build .', 'npm run build',
                        'echo "git push later" > notes.txt', 'cat > notes.md <<EOF\ngit push origin main\nEOF', 'aws s3 ls',
                        "git commit -m \"$(cat <<'EOF'\nFix deploy; git push after review\nEOF\n)\""):
            self.assertPasses(command)


class Migrations(GuardCase):
    def test_migrations_ask(self):
        for command in ('npx prisma migrate deploy', 'prisma db push', 'alembic upgrade head', 'python manage.py migrate',
                        'uv run python manage.py migrate', 'bundle exec rails db:migrate', 'npm run db:migrate', 'pnpm migrate',
                        'npx knex migrate:latest', 'npx drizzle-kit push', 'php artisan migrate --force', 'mix ecto.migrate',
                        'npx wrangler d1 migrations apply DB --remote', 'supabase db reset --linked', 'yarn seed'):
            self.assertAsks(command)

    def test_read_only_migration_commands_pass(self):
        for command in ('prisma generate', 'prisma migrate status', 'alembic history', 'npx knex migrate:make add_users',
                        'npx drizzle-kit generate', 'python manage.py makemigrations'):
            self.assertPasses(command)


class EnvTargets(GuardCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = tmp.name
        Path(self.project, '.env').write_text(f'OPENAI_API_KEY={SECRET}\nDATABASE_URL=postgres://u:p@127.0.0.1:6543/app\n'
                                              'DEBUG=true\nEMPTY_TOKEN=\n')
        Path(self.project, '.env.example').write_text('STRIPE_SECRET_KEY=sk_live_real_looking\n')

    def test_project_scripts_and_tests_ask_and_name_variables_without_values(self):
        for command in ('npm test', 'pnpm vitest run', 'pytest -q', 'python3 -m pytest', 'python3 scripts/check.py', 'node server.js',
                        'go test ./...', 'bun run test', 'npx jest', 'uv run pytest', './scripts/smoke.sh', 'make test'):
            out = self.assertAsks(command, 'OPENAI_API_KEY', cwd=self.project)
            reason = out['permissionDecisionReason']
            self.assertIn('DATABASE_URL', reason)
            self.assertIn('safe_run.py', reason)
            self.assertNotIn(SECRET, reason)
            self.assertNotIn('STRIPE', reason, '.env.example is not loaded by runtimes')
            self.assertNotIn('EMPTY_TOKEN', reason)

    def test_tools_that_do_not_run_project_code_pass(self):
        for command in ('git status', 'npm install', 'npx prettier --check .', 'python3 -m pip install requests', 'ls',
                        'npm ls', 'cat README.md'):
            self.assertPasses(command, cwd=self.project)

    def test_safe_run_wrapped_commands_skip_the_env_check_but_not_the_others(self):
        self.assertPasses(f'python3 {ROOT}/scripts/safe_run.py -- npm test', cwd=self.project)
        self.assertAsks(f'python3 {ROOT}/scripts/safe_run.py -- git push', 'pushes', cwd=self.project)

    def test_placeholder_only_env_passes_and_secret_injectors_ask(self):
        Path(self.project, '.env').write_text('API_KEY=your-key-here\nDATABASE_URL=\n')
        self.assertPasses('npm test', cwd=self.project)
        self.assertAsks('doppler run -- npm test', 'doppler', cwd=self.project)

    def test_project_dir_env_is_found_from_a_subfolder(self):
        sub = Path(self.project, 'web')
        sub.mkdir()
        os.environ['CLAUDE_PROJECT_DIR'] = self.project
        self.assertAsks('npm test', 'OPENAI_API_KEY', cwd=str(sub))


class DecisionShape(GuardCase):
    def test_ask_by_default_deny_with_flag_or_in_no_prompt_modes(self):
        self.assertEqual(self.decision('git push')['permissionDecision'], 'ask')
        self.assertEqual(self.decision('git push', deny=True)['permissionDecision'], 'deny')
        for mode in ('bypassPermissions', 'dontAsk'):
            out = self.decision('git push', extra={'permission_mode': mode})
            self.assertEqual(out['permissionDecision'], 'deny', mode)
            self.assertIn('Do not retry', out['permissionDecisionReason'])
        self.assertEqual(self.decision('git push', extra={'permission_mode': 'acceptEdits'})['permissionDecision'], 'ask')

    def test_output_matches_the_pretooluse_contract(self):
        out = guard_hook.decide(event('git push'))
        self.assertEqual(set(out), {'hookSpecificOutput'})
        self.assertEqual(out['hookSpecificOutput']['hookEventName'], 'PreToolUse')
        self.assertTrue(out['hookSpecificOutput']['permissionDecisionReason'].startswith('production-dev-team guard'))

    def test_off_turns_one_check_off(self):
        self.assertPasses('git push', off={'deploy'})
        self.assertAsks('rm -rf data', off={'deploy'})
        self.assertPasses('rm -rf data', off={'delete'})
        self.assertPasses('alembic upgrade head', off={'migrate'})

    def test_other_tools_and_empty_commands_pass(self):
        self.assertIsNone(guard_hook.decide({'tool_name': 'Write', 'tool_input': {'file_path': 'x'}}))
        self.assertIsNone(guard_hook.decide({'tool_name': 'Bash', 'tool_input': {}}))
        self.assertIsNone(guard_hook.decide({'tool_name': 'Bash', 'tool_input': {'command': '   '}}))

    def test_several_reasons_are_all_named(self):
        reason = self.decision('git push && rm -rf data')['permissionDecisionReason']
        self.assertIn('pushes', reason)
        self.assertIn('deletes', reason)

    def test_unbalanced_quotes_still_get_checked(self):
        self.assertAsks('git push origin "main')


class CommandLine(unittest.TestCase):
    def run_hook(self, payload, *flags):
        env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_PROJECT_DIR'}
        return subprocess.run(SCRIPT + list(flags), input=payload, capture_output=True, text=True, env=env, timeout=20)

    def test_flagged_command_prints_one_json_decision(self):
        result = self.run_hook(json.dumps(event('git push origin main')))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'ask')
        denied = self.run_hook(json.dumps(event('git push origin main')), '--deny')
        self.assertEqual(json.loads(denied.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_safe_command_is_silent(self):
        result = self.run_hook(json.dumps(event('git status')))
        self.assertEqual((result.returncode, result.stdout), (0, ''))

    def test_broken_input_fails_open_with_a_non_blocking_exit(self):
        result = self.run_hook('{not json')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('skipped', result.stderr)

    def test_bad_flags_fail_open_because_exit_2_would_block_every_command(self):
        result = self.run_hook(json.dumps(event('git push')), '--off', 'everything')
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertIn('invalid choice', result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
