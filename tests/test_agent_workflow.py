"""Standard-library tests: no application imports or external service writes."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import agent_workflow as w


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'codex/test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Workflow Test')
        (self.repo / 'fixture.txt').write_text('fixture')
        self.git('add', '.')
        self.git('commit', '-m', 'fixture')
        self.flow = w.Workflow(self.base / 'workflow')
        self.create()

    def git(self, *args):
        return w.git(self.repo, *args)

    def create(self, task='task-1'):
        return self.flow.apply('create', task, 1, 'absent', task+'-create',
            requirement='Approved requirement. c1: fixture check passes.', prompt='Implement c1 only.',
            criteria=[{'id':'c1','description':'Fixture check passes'}], required_checks=['fixture-check'],
            worktree=str(self.repo), pr_url='https://github.com/test/repo/pull/3', pr_number=3)

    def claim(self, role='implementation', number=1):
        return self.flow.apply('claim-'+role, 'task-1', number,
            'awaiting_implementation' if role == 'implementation' else 'awaiting_review',
            f'{role}-claim-{number}', owner=role)['ownership_token']

    def implementation(self, number=1):
        return {'task_id':'task-1','round':number,'pr_url':'https://github.com/test/repo/pull/3',
            'pr_number':3,'branch':'codex/test','worktree_path':str(self.repo),
            'commit_sha':self.git('rev-parse','HEAD'),'implemented_requirements':['c1'],
            'validation_results':[{'name':'fixture-check','status':'pass','verified':True,'evidence':'fixture inspected'}],
            'unverified_checks':[],'blockers':[]}

    def review(self, number=1):
        return {'task_id':'task-1','round':number,'reviewed_commit':self.git('rev-parse','HEAD'),
            'criteria':[{'id':'c1','status':'pass','verified':True,'evidence':'independent fixture inspection'}],
            'findings':[],'unverified_checks':[]}

    def publish(self, token, number=1, report=None):
        return self.flow.apply('publish-implementation','task-1',number,'implementing',f'publish-{number}',
            token=token,report=report or self.implementation(number))

    def ready_review(self):
        self.publish(self.claim())
        return self.claim('review')

    def test_complete_and_repeated_commands(self):
        token=self.claim()
        self.assertEqual(token,self.claim())
        report=self.implementation()
        self.publish(token,report=report)
        self.assertTrue(self.publish(token,report=report)['idempotent'])
        review_token=self.claim('review')
        args=dict(token=review_token,report=self.review())
        result=self.flow.apply('accept','task-1',1,'reviewing','accept-1',**args)
        self.assertEqual(result['state'],'complete')
        self.assertTrue(self.flow.apply('accept','task-1',1,'reviewing','accept-1',**args)['idempotent'])
        self.assertTrue((self.flow.path('task-1')/'SUCCESS.json').exists())

    def test_simultaneous_claims_in_separate_processes(self):
        script=Path(w.__file__).resolve()
        def run(index):
            return subprocess.run([sys.executable,str(script),'--root',str(self.flow.root),
                'claim-implementation','--task-id','task-1','--round','1','--expected-state',
                'awaiting_implementation','--request-id',f'competing-{index}','--owner',f'worker-{index}'],capture_output=True)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results=list(pool.map(run,[1,2]))
        self.assertEqual(sorted(r.returncode for r in results),[0,2])
        self.assertEqual(self.flow.status('task-1')['state'],'implementing')

    def test_invalid_state_round_token_and_identity(self):
        token=self.claim()
        for task,number,state,ownership in [('task-1',2,'implementing',token),
                ('task-1',1,'awaiting_review',token),('task-1',1,'implementing','wrong'),
                ('other',1,'implementing',token)]:
            with self.assertRaises(w.WorkflowError):
                self.flow.apply('publish-implementation',task,number,state,'invalid-'+task+str(number)+state+ownership[:4],
                    token=ownership,report=self.implementation())

    def test_duplicate_claim_and_request_collision(self):
        self.claim()
        with self.assertRaises(w.WorkflowError):
            self.flow.apply('claim-implementation','task-1',1,'implementing','duplicate',owner='other')
        with self.assertRaises(w.WorkflowError):
            self.flow.apply('claim-implementation','task-1',1,'awaiting_implementation','implementation-claim-1',owner='other')

    def test_interrupted_file_write_has_no_actionable_partial(self):
        target=self.base/'published.json'
        with patch.object(w.os,'replace',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                w.atomic(target,b'{"complete":true}')
        self.assertFalse(target.exists())
        token=self.claim()
        with patch.object(self.flow,'commit',side_effect=OSError('state interruption')):
            with self.assertRaises(OSError):
                self.publish(token)
        self.assertEqual(self.flow.status('task-1')['state'],'implementing')
        self.assertTrue((self.flow.path('task-1')/'implementation-reports/001.json').exists())
        self.assertEqual(self.publish(token)['state'],'awaiting_review')

    def test_changed_commit_prevents_acceptance(self):
        token=self.ready_review()
        report=self.review()
        self.git('commit','--allow-empty','-m','changed')
        with self.assertRaises(w.WorkflowError):
            self.flow.apply('accept','task-1',1,'reviewing','accept',token=token,report=report)
        self.assertFalse((self.flow.path('task-1')/'SUCCESS.json').exists())

    def test_missing_verification_prevents_success(self):
        token=self.claim()
        implementation=self.implementation()
        implementation['validation_results'][0]['verified']=False
        self.publish(token,report=implementation)
        token=self.claim('review')
        with self.assertRaises(w.WorkflowError):
            self.flow.apply('accept','task-1',1,'reviewing','accept',token=token,report=self.review())

    def test_incomplete_review_prevents_success(self):
        token=self.ready_review()
        report=self.review(); report['criteria']=[]
        with self.assertRaises(w.WorkflowError):
            self.flow.apply('accept','task-1',1,'reviewing','accept',token=token,report=report)

    def test_five_round_limit_preserves_history(self):
        for number in range(1,6):
            self.publish(self.claim(number=number),number)
            token=self.claim('review',number)
            result=self.flow.apply('request-round','task-1',number,'reviewing',f'correction-{number}',
                token=token,report=self.review(number),prompt='Fix c1 only',criterion_ids=['c1'])
        self.assertEqual(result['state'],'blocked')
        state=self.flow.status('task-1')
        self.assertEqual(state['round'],5)
        self.assertTrue((self.flow.path('task-1')/'correction-prompts/005.md').exists())
        with self.assertRaises(w.WorkflowError):
            self.flow.apply('resume','task-1',5,'blocked','resume',owner_note='Owner intervened')

    def test_new_criterion_in_correction_rejected(self):
        token=self.ready_review()
        with self.assertRaises(w.WorkflowError):
            self.flow.apply('request-round','task-1',1,'reviewing','correction',token=token,
                report=self.review(),prompt='Invent new scope',criterion_ids=['new'])

    def test_block_resume_and_stale_claim_no_reclaim(self):
        token=self.claim()
        self.assertTrue(self.flow.status('task-1',stale_seconds=0)['stale_claim'])
        self.assertEqual(self.flow.status('task-1')['claim']['token'],token)
        self.flow.apply('block','task-1',1,'implementing','block',token=token,reason='External dependency missing')
        with self.assertRaises(w.WorkflowError):
            self.flow.apply('resume','task-1',1,'blocked','bad-resume',owner_note='')
        self.flow.apply('resume','task-1',1,'blocked','resume',owner_note='Owner authorized after recovery')
        self.assertEqual(self.flow.status('task-1')['state'],'awaiting_implementation')
        self.assertTrue((self.flow.path('task-1')/'BLOCKED.json').exists())

    def test_one_active_and_immutable_requirement(self):
        with self.assertRaises(w.WorkflowError):
            self.create('task-2')
        (self.flow.path('task-1')/'requirement.md').write_text('tampered')
        with self.assertRaises(w.WorkflowError):
            self.flow.status('task-1')

    def test_uncommitted_application_and_unrelated_changes_rejected(self):
        token=self.claim()
        (self.repo/'app').mkdir(); (self.repo/'app'/'new.py').write_text('unfinished')
        with self.assertRaises(w.WorkflowError):
            self.publish(token)

    def test_process_crash_mid_publication_leaves_only_inert_temp(self):
        target=self.base/'crash.json'
        code = ('import os; from pathlib import Path; from scripts import agent_workflow as w; '
                'w.os.replace=lambda source,target: os._exit(19); '
                f'w.atomic(Path({str(target)!r}), b"complete bytes")')
        result=subprocess.run([sys.executable,'-c',code],cwd=Path(w.__file__).resolve().parents[1])
        self.assertEqual(result.returncode,19)
        self.assertFalse(target.exists())
        self.assertEqual(len(list(self.base.glob('.crash.json.*.tmp'))),1)
        w.atomic(target,b'complete bytes')
        self.assertEqual(target.read_bytes(),b'complete bytes')

    def test_preexisting_workspace_edits_must_remain_unchanged(self):
        self.flow=w.Workflow(self.base/'separate-workflow')
        (self.repo/'fixture.txt').write_text('unrelated edit')
        self.create()
        token=self.claim()
        (self.repo/'fixture.txt').write_text('overwritten edit')
        with self.assertRaises(w.WorkflowError):
            self.publish(token)

    def test_missing_required_check_and_wrong_review_sha(self):
        token=self.claim()
        implementation=self.implementation(); implementation['validation_results']=[]
        self.publish(token,report=implementation)
        token=self.claim('review')
        with self.assertRaises(w.WorkflowError):
            self.flow.apply('accept','task-1',1,'reviewing','accept',token=token,report=self.review())
        report=self.review(); report['reviewed_commit']='0'*40
        with self.assertRaises(w.WorkflowError):
            self.flow.apply('accept','task-1',1,'reviewing','wrong-sha',token=token,report=report)


if __name__ == '__main__':
    unittest.main()
