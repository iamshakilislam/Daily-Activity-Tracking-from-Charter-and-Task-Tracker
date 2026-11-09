import unittest
from unittest.mock import Mock, patch
import requests
import resource_daily_activity_and_task_report as report


def project(identifier='one'):
    return {
        '_id': identifier, 'project_drawer_name': identifier,
        'project_status': 'project-in-progress', 'projectCharterLink': 'charter',
        'client_alias': 'client', 'pdl': [{'email': 'lead@example.com'}],
    }


def response(payload):
    result = Mock()
    result.json.return_value = payload
    return result


class MigrationTests(unittest.TestCase):
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
            self.assertEqual(frame.iloc[0]['Ticket Status'], 'project in progress')
            self.assertEqual(frame.iloc[0]['Task Tracker Link'], '')
            self.assertEqual(report.refresh_latest_member_status_sheet.call_args.kwargs['project_status_by_name']['completed'], 'project completed')
            self.assertEqual(report.write_audit_log.call_args.args[0], [])


if __name__ == '__main__':
    unittest.main()
