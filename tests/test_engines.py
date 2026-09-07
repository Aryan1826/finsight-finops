import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from utils.idle_detector import detect_idle_resources
from utils.rightsizing import recommend_rightsizing
from utils.synthetic_generator import generate_synthetic_cur
from utils.tagging_policy import evaluate_tagging_compliance, load_policy


def test_synthetic_ground_truth(tmp_path):
    path = tmp_path / 'cur.csv'
    generate_synthetic_cur(path, seed=42)
    df = pd.read_csv(path)
    assert df['lineItem/ResourceId'].nunique() == 96
    idle = detect_idle_resources(df)
    assert len(idle) == 20
    assert set(idle['finding_type']) == {'Idle EC2', 'Unattached EBS', 'Unused Elastic IP'}


def test_rightsizing_and_tags(tmp_path):
    path = tmp_path / 'cur.csv'
    generate_synthetic_cur(path, seed=42)
    df = pd.read_csv(path)
    rightsizing = recommend_rightsizing(df)
    assert len(rightsizing) > 0
    report, compliance = evaluate_tagging_compliance(df, load_policy('config/tagging_policy.yaml'))
    assert len(report) == 96
    assert 0 < compliance < 100
