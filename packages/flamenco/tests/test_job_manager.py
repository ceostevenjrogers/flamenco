# -*- encoding: utf-8 -*-
from __future__ import absolute_import

import mock

from pillar.tests import common_test_data as ctd
from abstract_flamenco_test import AbstractFlamencoTest


class JobManagerTest(AbstractFlamencoTest):
    def test_create_job(self):
        from pillar.api.utils.authentication import force_cli_user

        manager, _, _ = self.create_manager_service_account()

        with self.app.test_request_context():
            force_cli_user()
            self.jmngr.api_create_job(
                'test job',
                u'Wörk wørk w°rk.',
                'sleep',
                {
                    'frames': '12-18, 20-22',
                    'chunk_size': 5,
                    'time_in_seconds': 3,
                },
                self.proj_id,
                ctd.EXAMPLE_PROJECT_OWNER_ID,
                manager['_id'],
            )

        # Test the jobs
        with self.app.test_request_context():
            jobs_coll = self.flamenco.db('jobs')

            jobs = list(jobs_coll.find())
            self.assertEqual(1, len(jobs))
            job = jobs[0]

            self.assertEqual(u'Wörk wørk w°rk.', job['description'])
            self.assertEqual(u'sleep', job['job_type'])

        # Test the tasks
        with self.app.test_request_context():
            tasks_coll = self.flamenco.db('tasks')

            tasks = list(tasks_coll.find())
            self.assertEqual(2, len(tasks))
            task = tasks[0]

            self.assertEqual(u'sleep-12-16', task['name'])
            self.assertEqual({
                u'name': u'echo',
                u'settings': {
                    u'message': u'Preparing to sleep',
                }
            }, task['commands'][0])

            self.assertEqual({
                u'name': u'sleep',
                u'settings': {
                    u'time_in_seconds': 3,
                }
            }, task['commands'][1])


class JobStatusChangeTest(AbstractFlamencoTest):
    def setUp(self, **kwargs):
        super(JobStatusChangeTest, self).setUp(**kwargs)

        # Create a job with 4 tasks
        from pillar.api.utils.authentication import force_cli_user

        manager, _, token = self.create_manager_service_account()
        self.mngr_token = token['token']

        with self.app.test_request_context():
            force_cli_user()
            job = self.jmngr.api_create_job(
                'test job',
                u'Wörk wørk w°rk.',
                'blender-render',
                {
                    'filepath': u'/my/blend.file',
                    'frames': u'12-18, 20-23',
                    'chunk_size': 2,
                    'time_in_seconds': 3,
                },
                self.proj_id,
                ctd.EXAMPLE_PROJECT_OWNER_ID,
                manager['_id'],
            )
            self.job_id = job['_id']

            # Fetch the task IDs and set the task statuses to a fixed list.
            tasks_coll = self.flamenco.db('tasks')
            tasks = tasks_coll.find({'job': self.job_id}, projection={'_id': 1})
            self.task_ids = [task['_id'] for task in tasks]

        self.assertEqual(6, len(self.task_ids))
        self.set_task_status(0, 'queued')
        self.set_task_status(1, 'claimed-by-manager')
        self.set_task_status(2, 'completed')
        self.set_task_status(3, 'active')
        self.set_task_status(4, 'canceled')
        self.set_task_status(5, 'failed')

    def set_job_status(self, new_status):
        """Nice, official, ripple-to-task-status approach"""

        with self.app.test_request_context():
            self.jmngr.set_job_status(self.job_id, new_status)

    def force_job_status(self, new_status):
        """Directly to MongoDB approach"""

        with self.app.test_request_context():
            jobs_coll = self.flamenco.db('jobs')
            result = jobs_coll.update_one({'_id': self.job_id},
                                          {'$set': {'status': new_status}})
        self.assertEqual(1, result.matched_count)

    def set_task_status(self, task_idx, new_status):
        """Sets the task status directly in MongoDB.

        This should only be used to set up a certain scenario.
        """
        from flamenco import current_flamenco

        with self.app.test_request_context():
            current_flamenco.update_status('tasks', self.task_ids[task_idx], new_status)

    def assert_job_status(self, expected_status):
        with self.app.test_request_context():
            jobs_coll = self.flamenco.db('jobs')
            job = jobs_coll.find_one({'_id': self.job_id},
                                     projection={'status': 1})
        self.assertEqual(job['status'], unicode(expected_status))

    def assert_task_status(self, task_idx, expected_status):
        with self.app.test_request_context():
            tasks_coll = self.flamenco.db('tasks')
            task = tasks_coll.find_one({'_id': self.task_ids[task_idx]},
                                       projection={'status': 1})

        self.assertIsNotNone(task)
        self.assertEqual(task['status'], unicode(expected_status),
                         "Task %i:\n   has status: '%s'\n but expected: '%s'" % (
                             task_idx, task['status'], expected_status))

    def test_status_from_queued_to_active(self):
        # This shouldn't change any of the tasks.
        self.force_job_status('queued')
        self.set_job_status('active')

        self.assert_task_status(0, 'queued')  # was: queued
        self.assert_task_status(1, 'claimed-by-manager')  # was: claimed-by-manager
        self.assert_task_status(2, 'completed')  # was: completed
        self.assert_task_status(3, 'active')  # was: active
        self.assert_task_status(4, 'canceled')  # was: canceled
        self.assert_task_status(5, 'failed')  # was: failed

    def test_status_from_active_to_canceled(self):
        # This should cancel all tasks that could possibly still run.
        self.force_job_status('active')
        self.set_job_status('canceled')

        self.assert_task_status(0, 'canceled')  # was: queued
        self.assert_task_status(1, 'canceled')  # was: claimed-by-manager
        self.assert_task_status(2, 'completed')  # was: completed
        self.assert_task_status(3, 'canceled')  # was: active
        self.assert_task_status(4, 'canceled')  # was: canceled
        self.assert_task_status(5, 'failed')  # was: failed

    def test_status_from_canceled_to_queued(self):
        # This should re-queue all non-completed tasks.
        self.force_job_status('canceled')
        self.set_job_status('queued')

        self.assert_task_status(0, 'queued')  # was: queued
        self.assert_task_status(1, 'queued')  # was: claimed-by-manager
        self.assert_task_status(2, 'completed')  # was: completed
        self.assert_task_status(3, 'queued')  # was: active
        self.assert_task_status(4, 'queued')  # was: canceled
        self.assert_task_status(5, 'queued')  # was: failed

    def test_status_from_completed_to_queued(self):
        # This should re-queue all tasks.
        self.force_job_status('completed')
        self.set_job_status('queued')

        self.assert_task_status(0, 'queued')  # was: queued
        self.assert_task_status(1, 'queued')  # was: claimed-by-manager
        self.assert_task_status(2, 'queued')  # was: completed
        self.assert_task_status(3, 'queued')  # was: active
        self.assert_task_status(4, 'queued')  # was: canceled
        self.assert_task_status(5, 'queued')  # was: failed

    def test_status_from_active_to_failed(self):
        # This should be the same as going to 'canceled', except that the underlying reason
        # to go to this state is different (active action by user vs. result of massive task
        # failure).
        self.force_job_status('active')
        self.set_job_status('failed')

        self.assert_task_status(0, 'canceled')  # was: queued
        self.assert_task_status(1, 'canceled')  # was: claimed-by-manager
        self.assert_task_status(2, 'completed')  # was: completed
        self.assert_task_status(3, 'canceled')  # was: active
        self.assert_task_status(4, 'canceled')  # was: canceled
        self.assert_task_status(5, 'failed')  # was: failed

    def test_status_from_active_to_completed(self):
        # Shouldn't do anything, as going to completed is a result of all tasks being completed.

        self.assert_task_status(0, 'queued')  # was: queued
        self.assert_task_status(1, 'claimed-by-manager')  # was: claimed-by-manager
        self.assert_task_status(2, 'completed')  # was: completed
        self.assert_task_status(3, 'active')  # was: active
        self.assert_task_status(4, 'canceled')  # was: canceled
        self.assert_task_status(5, 'failed')  # was: failed

    @mock.patch('flamenco.jobs.JobManager.handle_job_status_change')
    def test_put_job(self, handle_job_status_change):
        """Test that flamenco.jobs.JobManager.handle_job_status_change is called when we PUT."""

        from pillar.api.utils import remove_private_keys

        self.create_user(24 * 'a', roles=('admin',))
        self.create_valid_auth_token(24 * 'a', token='admin-token')

        json_job = self.get('/api/flamenco/jobs/%s' % self.job_id,
                            auth_token='admin-token').json()

        json_job['status'] = 'canceled'

        self.put('/api/flamenco/jobs/%s' % self.job_id,
                 json=remove_private_keys(json_job),
                 headers={'If-Match': json_job['_etag']},
                 auth_token='admin-token')

        handle_job_status_change.assert_called_once_with(self.job_id, 'queued', 'canceled')
