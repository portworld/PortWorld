from __future__ import annotations

import unittest

from portworld_cli.azure.stages.container_app_runtime import (
    extract_fqdn_and_external,
    revision_is_ready,
)


class AzureContainerAppRuntimeStageTests(unittest.TestCase):
    def test_revision_is_ready_when_latest_matches_ready(self) -> None:
        payload = {
            "properties": {
                "provisioningState": "Succeeded",
                "latestRevisionName": "app--abc",
                "latestReadyRevisionName": "app--abc",
                "runningStatus": "Running",
            }
        }
        self.assertTrue(revision_is_ready(payload))

    def test_extract_fqdn_and_external_requires_external_true(self) -> None:
        payload = {
            "properties": {
                "configuration": {
                    "ingress": {
                        "external": True,
                        "fqdn": "app.westeurope.azurecontainerapps.io",
                    }
                }
            }
        }
        self.assertEqual(extract_fqdn_and_external(payload), "app.westeurope.azurecontainerapps.io")


if __name__ == "__main__":
    unittest.main()
