from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from portworld_cli.deploy_state import DeployState, read_deploy_state, write_deploy_state
from portworld_cli.targets import MANAGED_TARGETS, ManagedTargetStatePaths


class DeployStateTargetSerializationTests(unittest.TestCase):
    def test_roundtrip_per_managed_target_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_paths = ManagedTargetStatePaths(Path(temp_dir) / ".portworld" / "state")
            for index, target in enumerate(MANAGED_TARGETS, start=1):
                with self.subTest(target=target):
                    state = DeployState(
                        project_id=f"project-{index}",
                        region="us-central1",
                        service_name=f"svc-{target}",
                        runtime_source="source",
                        image_source_mode="build",
                        artifact_repository=None,
                        artifact_repository_base=None,
                        cloud_sql_instance=None,
                        database_name=None,
                        bucket_name=None,
                        image=f"img-{index}",
                        published_release_tag=None,
                        published_image_ref=None,
                        service_url=f"https://{target}.example.com",
                        service_account_email=None,
                        last_deployed_at_ms=1_700_000_000_000 + index,
                    )
                    path = state_paths.file_for_target(target)
                    write_deploy_state(path, state)
                    loaded = read_deploy_state(path)
                    self.assertEqual(loaded.to_payload(), state.to_payload())


if __name__ == "__main__":
    unittest.main()
