import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_app_imports_and_definitions():
    """Verify that app.py imports cleanly and has no syntax or import errors."""
    mock_res = MagicMock()
    mock_res.account_id = "123456789012"
    mock_res.region = "ap-south-1"
    mock_res.arn = "arn:aws:iam::123456789012:role/FinSightRole"
    mock_res.total_spend_usd = 0.0
    mock_res.data = MagicMock()
    mock_res.data.empty = True

    with patch("utils.finops_pipeline.run_live_finops_analysis", return_value=mock_res):
        import app
        assert hasattr(app, "cached_live_analysis")
        assert hasattr(app, "load_synthetic_dataset")
        assert hasattr(app, "read_uploaded_file")

