from __future__ import annotations

import unittest

from portworld_cli.aws.stages.database import build_postgres_url, select_subnets_for_rds


class AWSDatabaseStageTests(unittest.TestCase):
    def test_select_subnets_prefers_distinct_azs(self) -> None:
        payload = {
            "Subnets": [
                {"SubnetId": "subnet-a1", "AvailabilityZone": "us-east-1a"},
                {"SubnetId": "subnet-a2", "AvailabilityZone": "us-east-1a"},
                {"SubnetId": "subnet-b1", "AvailabilityZone": "us-east-1b"},
            ]
        }
        selected = select_subnets_for_rds(payload)
        self.assertEqual(selected, ("subnet-a1", "subnet-b1"))

    def test_build_postgres_url_escapes_credentials(self) -> None:
        value = build_postgres_url(username="user@x", password="p/1", host="db.local", port=5432, db_name="app")
        self.assertIn("user%40x", value)
        self.assertIn("p/1", value)


if __name__ == "__main__":
    unittest.main()
