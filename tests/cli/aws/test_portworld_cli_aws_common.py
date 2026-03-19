from __future__ import annotations

import unittest

from portworld_cli.aws.common import s3_bucket_name_tls_warning, validate_s3_bucket_name


class AWSCommonValidationTests(unittest.TestCase):
    def test_reserved_prefix_and_suffix_are_rejected(self) -> None:
        self.assertIn("reserved prefix", validate_s3_bucket_name("xn--bucket") or "")
        self.assertIn("reserved suffix", validate_s3_bucket_name("bucket--x-s3") or "")

    def test_bucket_name_with_period_has_tls_warning(self) -> None:
        warning = s3_bucket_name_tls_warning("bucket.with.dots")
        self.assertIsNotNone(warning)
        self.assertIn("HTTPS", warning or "")


if __name__ == "__main__":
    unittest.main()
