from __future__ import annotations

import unittest
from unittest import mock

from portworld_cli.aws.stages.network_edge import wait_for_cloudfront_deployed


class AWSNetworkEdgeStageTests(unittest.TestCase):
    @mock.patch("portworld_cli.aws.stages.network_edge.run_aws_json")
    def test_wait_for_cloudfront_deployed_returns_on_deployed_status(self, run_json: mock.Mock) -> None:
        run_json.return_value = mock.Mock(
            ok=True,
            value={"Distribution": {"Status": "DEPLOYED"}},
            message=None,
        )
        stages: list[dict[str, object]] = []
        wait_for_cloudfront_deployed(distribution_id="dist-1", stage_records=stages)
        self.assertTrue(any(stage.get("stage") == "cloudfront_wait_deployed" for stage in stages))


if __name__ == "__main__":
    unittest.main()
