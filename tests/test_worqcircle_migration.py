import unittest
from unittest.mock import Mock, patch
import requests
import resource_daily_activity_and_task_report as report


def project(identifier='one'):
    return {
        '_id': identifier, 'project_drawer_name': identifier,
        'project_status': 'in-Progress', 'projectCharterLink': 'charter',
        'client_alias': 'client', 'pdl': [{'email': 'lead@example.com'}],
    }


def response(payload):
    result = Mock(status_code=200, headers={})
    result.json.return_value = payload
    return result


class MigrationTests(unittest.TestCase):
    def setUp(self):
        sleep = patch.object(report.time, 'sleep')
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def test_timeout_retries_same_page_with_longer_timeout(self):
        with patch.object(report.requests, 'get', side_effect=[
            requests.exceptions.ReadTimeout(),
            response({'projectDrawers': [project()], 'count': 1}),
        ]) as get:
            self.assertEqual(len(report.fetch_worqcircle_projects()), 1)
            self.assertEqual(get.call_args_list[0].kwargs['params'], get.call_args_list[1].kwargs['params'])
            self.assertEqual(get.call_args.kwargs['timeout'], (10, 60))
            self.sleep.assert_called_once_with(2)

    def test_rate_limit_respects_retry_after(self):
        limited = Mock(status_code=429, headers={'Retry-After': '25'})
        with patch.object(report.requests, 'get', side_effect=[limited, response({'projectDrawers': [], 'count': 0})]):
            self.assertEqual(report.fetch_worqcircle_projects(), [])
            self.sleep.assert_called_once_with(25)
            limited.close.assert_called_once()

    def test_server_failure_retries_but_forbidden_does_not(self):
        server_error = Mock(status_code=503, headers={})
        with patch.object(report.requests, 'get', side_effect=[server_error, response({'projectDrawers': [], 'count': 0})]) as get:
            report.fetch_worqcircle_projects()
            self.assertEqual(get.call_count, 2)
        forbidden = Mock(status_code=403, headers={})
        forbidden.raise_for_status.side_effect = requests.HTTPError('Forbidden')
        with patch.object(report.requests, 'get', return_value=forbidden) as get:
            with self.assertRaises(requests.HTTPError):
                report.fetch_worqcircle_projects()
            get.assert_called_once()

    def test_retry_exhaustion_stops_before_google_access(self):
        with patch.object(report, 'load_runtime_config'), patch.object(report, 'authenticate_google') as auth, patch.object(report.requests, 'get', side_effect=requests.exceptions.ReadTimeout()) as get:
            with self.assertRaises(requests.exceptions.ReadTimeout):
                report.main()
            self.assertEqual(get.call_count, 3)
            self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [2, 4])
            auth.assert_not_called()

    def test_retry_after_http_date(self):
        from datetime import datetime, timezone
        limited = Mock(headers={'Retry-After': 'Tue, 17 Nov 2026 06:01:00 GMT'})
        with patch.object(report, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 11, 17, 6, 0, 0, tzinfo=timezone.utc)
            self.assertEqual(report.worqcircle_retry_delay(limited, 1), 60)

    def test_long_server_cooldown_aborts_instead_of_retrying_early(self):
        limited = Mock(status_code=429, headers={'Retry-After': '600'})
        with patch.object(report.requests, 'get', return_value=limited) as get:
            with self.assertRaises(RuntimeError):
                report.fetch_worqcircle_projects()
            get.assert_called_once()
            self.sleep.assert_not_called()

    def test_2222_projects_need_23_requests(self):
        projects = [project(str(index)) for index in range(2222)]
        def serve_page(*args, **kwargs):
            params = kwargs['params']
            self.assertEqual(params['limit'], 100)
            return response({'projectDrawers': projects[params['skip']:params['skip'] + params['limit']], 'count': len(projects)})
        with patch.object(report.requests, 'get', side_effect=serve_page) as get:
            self.assertEqual(len(report.fetch_worqcircle_projects()), 2222)
            self.assertEqual(get.call_count, 23)
            self.assertEqual(get.call_args.kwargs['params']['skip'], 2200)
        self.assertEqual(len(self.sleep.call_args_list), 22)
        self.assertAlmostEqual(sum(call.args[0] for call in self.sleep.call_args_list), 5.5)

    def test_server_page_cap_does_not_skip_projects(self):
        projects = [project(str(index)) for index in range(61)]
        def serve_page(*args, **kwargs):
            skip = kwargs['params']['skip']
            return response({'projectDrawers': projects[skip:skip + 25], 'count': len(projects)})
        with patch.object(report.requests, 'get', side_effect=serve_page) as get:
            self.assertEqual(len(report.fetch_worqcircle_projects()), 61)
            self.assertEqual([call.kwargs['params']['skip'] for call in get.call_args_list], [0, 25, 50])

    def test_actual_worqcircle_target_statuses(self):
        for status in ('project-received', 'in-Progress', 'pending-schedule-approval'):
            with self.subTest(status=status):
                self.assertIn(report.normalize_project_status(status), report.TARGET_STATUS_NAME_SET)
                self.assertEqual(report.resolve_latest_member_engagement({
                    'Ticket Status': status, 'Charter Team Status': 'Active',
                    'Engagement (today)': 'Active',
                }), 'Active')
        for status in ('not-Started', 'demo-project-received', 'project-completed'):
            self.assertNotIn(report.normalize_project_status(status), report.TARGET_STATUS_NAME_SET)
        self.assertEqual(report.resolve_latest_member_engagement({
            'Ticket Status': 'project-completed', 'Charter Team Status': 'Active',
            'Engagement (today)': 'Active',
        }), 'Worked')

    def test_pagination_uses_total_not_short_page(self):
        with patch.object(report.requests, 'get', side_effect=[
            response({'projectDrawers': [project('one')], 'filteredTotalCount': 2}),
            response({'projectDrawers': [project('two')], 'filteredTotalCount': 2}),
        ]) as get:
            self.assertEqual(len(report.fetch_worqcircle_projects()), 2)
            self.assertEqual(get.call_args_list[1].kwargs['params']['skip'], 1)

    def test_incomplete_or_repeated_pages_fail(self):
        for second in ([], [project()]):
            with self.subTest(second=second), patch.object(report.requests, 'get', side_effect=[
                response({'projectDrawers': [project()], 'count': 2}),
                response({'projectDrawers': second, 'count': 2}),
            ]):
                with self.assertRaises(ValueError):
                    report.fetch_worqcircle_projects()

    def test_http_failure_propagates(self):
        failed = Mock()
        failed.raise_for_status.side_effect = requests.HTTPError('unauthorized')
        with patch.object(report.requests, 'get', return_value=failed):
            with self.assertRaises(requests.HTTPError):
                report.fetch_worqcircle_projects()

    def test_charter_snapshot_has_unknown_tracker_metrics(self):
        frame = report.build_charter_activity_report({'QAI_DK1001': 'Active'}, {'QAI_DK1001': 'Member'})
        rows = report.build_daily_project_progress_rows(frame, {})
        self.assertEqual(rows.iloc[0]['Full Name'], 'Member')
        self.assertEqual(rows.iloc[0]['Engagement (today)'], 'Active')
        for column in report.CLEAR_IF_NOT_RUNNING_HEADERS:
            self.assertEqual(rows.iloc[0][column], '')

    def test_main_never_reads_tracker_and_keeps_all_statuses_for_latest(self):
        completed = {**project('completed'), 'project_status': 'project-completed'}
        ignored = {**project('ignored'), 'pdl': [{'name': 'Ignored Lead', 'email': next(iter(report.IGNORED_PDL_EMAILS))}]}
        with patch.multiple(report,
            load_runtime_config=Mock(), authenticate_google=Mock(),
            fetch_worqcircle_projects=Mock(return_value=[project(), completed, ignored]),
            PROJECT_NAME_FILTER='',
            get_qai_id_and_status_pair=Mock(return_value=({'QAI_DK1001': 'Active'}, {'QAI_DK1001': 'Member'}, [])),
            get_charter_project_dates=Mock(return_value=('start', 'end')),
            get_tracker_data=Mock(side_effect=AssertionError('Tracker must not be read')),
            load_resource_type_lookup=Mock(side_effect=AssertionError('Unused lookup must not run')),
            append_daily_project_progress=Mock(), refresh_latest_member_status_sheet=Mock(),
            write_audit_log=Mock(),
        ):
            report.main()
            report.append_daily_project_progress.assert_called_once()
            frame = report.append_daily_project_progress.call_args.args[0]
            self.assertEqual(frame.iloc[0]['Ticket Status'], 'in-Progress')
            self.assertEqual(frame.iloc[0]['Task Tracker Link'], '')
            self.assertEqual(report.refresh_latest_member_status_sheet.call_args.kwargs['project_status_by_name']['completed'], 'project-completed')
            self.assertEqual(report.write_audit_log.call_args.args[0], [])


if __name__ == '__main__':
    unittest.main()
