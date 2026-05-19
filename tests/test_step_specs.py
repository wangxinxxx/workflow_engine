from workflow_engine.requirement_flow.step_specs import load_step_specs


def test_step_specs_have_unique_ids():
    specs = load_step_specs()
    assert specs
    ids = [spec.id for spec in specs]
    assert len(ids) == len(set(ids))
