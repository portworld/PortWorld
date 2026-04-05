from __future__ import annotations

import unittest
import unittest.mock as mock

from portworld_cli.context import CLIContext
from portworld_cli.ux.progress import ProgressReporter


def _cli_context(*, json_output: bool = False) -> CLIContext:
    return CLIContext(
        project_root_override=None,
        verbose=False,
        json_output=json_output,
        non_interactive=False,
        yes=False,
    )


class ProgressReporterTests(unittest.TestCase):
    def test_reporter_disabled_for_json_output(self) -> None:
        reporter = ProgressReporter(_cli_context(json_output=True))
        self.assertFalse(reporter.enabled)

    @mock.patch("portworld_cli.ux.progress.Console")
    def test_reporter_disabled_for_non_tty(self, console_cls: mock.Mock) -> None:
        console_cls.return_value.is_terminal = False
        console_cls.return_value.is_interactive = False
        reporter = ProgressReporter(_cli_context())
        self.assertFalse(reporter.enabled)

    def test_reporter_disabled_for_non_interactive_mode(self) -> None:
        reporter = ProgressReporter(
            CLIContext(
                project_root_override=None,
                verbose=False,
                json_output=False,
                non_interactive=True,
                yes=False,
            )
        )
        self.assertFalse(reporter.enabled)

    def test_completed_steps_are_retained_in_order(self) -> None:
        with mock.patch.object(ProgressReporter, "_ensure_live"), mock.patch.object(ProgressReporter, "_refresh"):
            reporter = ProgressReporter(_cli_context(), enabled=True)

            with reporter.stage("Loading workspace configuration"):
                pass
            with reporter.stage("Checking cloud credentials"):
                pass

            snapshot = reporter.snapshot()
            self.assertEqual(
                snapshot["history"],
                [
                    ("done", "Loading workspace configuration"),
                    ("done", "Checking cloud credentials"),
                ],
            )
            self.assertIsNone(snapshot["active_label"])
            reporter.close()

    def test_failure_marks_active_step_failed(self) -> None:
        with mock.patch.object(ProgressReporter, "_ensure_live"), mock.patch.object(ProgressReporter, "_refresh"):
            reporter = ProgressReporter(_cli_context(), enabled=True)

            try:
                with reporter.stage("Deploying Cloud Run service"):
                    raise RuntimeError("boom")
            except RuntimeError as exc:
                self.assertRegex(str(exc), "boom")
            else:
                self.fail("expected RuntimeError")

            snapshot = reporter.snapshot()
            self.assertEqual(
                snapshot["history"],
                [("failed", "Deploying Cloud Run service")],
            )
            self.assertIsNone(snapshot["active_label"])
            reporter.close()


if __name__ == "__main__":
    unittest.main()
