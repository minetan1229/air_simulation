"""Tests for tools/close_resolved_issue.py (Issue #149)."""

import os
import unittest
from unittest.mock import MagicMock, patch

from tools.close_resolved_issue import close_resolved_ci_issues, main


class TestCloseResolvedIssue(unittest.TestCase):
    @patch("tools.close_resolved_issue.github_api_request")
    def test_yes_confirm_false_skips_closing(self, mock_api):
        env = {
            "GITHUB_TOKEN": "mock-token",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_WORKFLOW": "Python Code Verification",
            "GITHUB_REF_NAME": "genetic-project",
        }
        with patch.dict(os.environ, env):
            mock_api.return_value = [
                {
                    "number": 100,
                    "title": "🚨 CI Failure: Python Code Verification on genetic-project",
                }
            ]
            exit_code = close_resolved_ci_issues(dry_run=False, yes_confirm=False)
            self.assertEqual(exit_code, 0)
            # Should have queried issues (GET), but NEVER called POST (comment) or PATCH (close)
            self.assertEqual(mock_api.call_count, 1)
            self.assertEqual(mock_api.call_args[1]["method"], "GET")

    @patch("tools.close_resolved_issue.github_api_request")
    def test_yes_confirm_true_closes_matching_issue(self, mock_api):
        env = {
            "GITHUB_TOKEN": "mock-token",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_WORKFLOW": "Python Code Verification",
            "GITHUB_REF_NAME": "genetic-project",
            "GITHUB_RUN_NUMBER": "42",
            "GITHUB_RUN_ID": "123456",
            "GITHUB_SHA": "abc1234567",
        }
        with patch.dict(os.environ, env):
            mock_api.return_value = [
                {
                    "number": 100,
                    "title": "🚨 CI Failure: Python Code Verification on genetic-project",
                }
            ]
            exit_code = close_resolved_ci_issues(dry_run=False, yes_confirm=True)
            self.assertEqual(exit_code, 0)
            # 1 GET list, 1 POST comment, 1 PATCH close
            self.assertEqual(mock_api.call_count, 3)
            calls = mock_api.call_args_list
            self.assertEqual(calls[0][1]["method"], "GET")
            self.assertEqual(calls[1][1]["method"], "POST")
            self.assertIn("/issues/100/comments", calls[1][0][0])
            self.assertEqual(calls[2][1]["method"], "PATCH")
            self.assertIn("/issues/100", calls[2][0][0])
            self.assertEqual(calls[2][1]["data"]["state"], "closed")

    @patch("tools.close_resolved_issue.close_resolved_ci_issues")
    def test_main_passes_yes_flag(self, mock_close):
        mock_close.return_value = 0
        with patch("sys.argv", ["close_resolved_issue.py", "--yes"]):
            exit_code = main()
            self.assertEqual(exit_code, 0)
            mock_close.assert_called_once_with(dry_run=False, yes_confirm=True)
