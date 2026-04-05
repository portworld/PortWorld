from __future__ import annotations

import unittest

from portworld_cli.context import CLIContext
from portworld_cli.output import CommandResult, DiagnosticCheck
from portworld_cli.services.doctor.service import _finalize_doctor_result


def _cli_context(*, json_output: bool = False) -> CLIContext:
    return CLIContext(
        project_root_override=None,
        verbose=False,
        json_output=json_output,
        non_interactive=False,
        yes=False,
    )


class DoctorOutputTests(unittest.TestCase):
    def test_ready_message_is_used_when_all_checks_pass(self) -> None:
        result = CommandResult(
            ok=True,
            command="portworld doctor",
            message="verbose summary",
            data={"target": "gcp-cloud-run"},
            checks=(
                DiagnosticCheck(id="workspace_root_detected", status="pass", message="ok"),
                DiagnosticCheck(id="required_apis_ready", status="pass", message="ok"),
            ),
            exit_code=0,
        )

        final = _finalize_doctor_result(_cli_context(), result)

        self.assertEqual(final.message, "ready: target gcp-cloud-run")
        self.assertEqual(final.checks, ())
        self.assertIn("all_checks", final.data)
        self.assertEqual(len(final.data["all_checks"]), 2)

    def test_only_warning_and_fail_checks_are_rendered(self) -> None:
        result = CommandResult(
            ok=True,
            command="portworld doctor",
            message="verbose summary",
            data={"target": "gcp-cloud-run"},
            checks=(
                DiagnosticCheck(id="workspace_root_detected", status="pass", message="ok"),
                DiagnosticCheck(id="managed_storage_backend_shape", status="warn", message="warn"),
                DiagnosticCheck(id="required_apis_ready", status="fail", message="fail"),
            ),
            exit_code=0,
        )

        final = _finalize_doctor_result(_cli_context(), result)

        self.assertIsNone(final.message)
        self.assertEqual(
            [(check.id, check.status) for check in final.checks],
            [
                ("managed_storage_backend_shape", "warn"),
                ("required_apis_ready", "fail"),
            ],
        )

    def test_json_output_keeps_original_message_and_checks(self) -> None:
        result = CommandResult(
            ok=True,
            command="portworld doctor",
            message="verbose summary",
            data={"target": "gcp-cloud-run"},
            checks=(
                DiagnosticCheck(id="workspace_root_detected", status="pass", message="ok"),
                DiagnosticCheck(id="managed_storage_backend_shape", status="warn", message="warn"),
            ),
            exit_code=0,
        )

        final = _finalize_doctor_result(_cli_context(json_output=True), result)

        self.assertEqual(final.message, "verbose summary")
        self.assertEqual(len(final.checks), 2)
        self.assertNotIn("all_checks", final.data)


if __name__ == "__main__":
    unittest.main()
