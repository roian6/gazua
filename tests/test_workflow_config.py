from pathlib import Path


def test_result_workflow_has_bounded_job_runtime():
    workflow = Path(".github/workflows/action-result.yml").read_text(encoding="utf-8")

    assert "timeout-minutes: 15" in workflow
