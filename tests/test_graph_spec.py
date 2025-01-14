import os
import pytest
import requests.exceptions

from Common.build_manager import GraphBuilder, GraphSpecError


def clear_graph_spec_config():
    os.environ['ORION_GRAPH_SPEC'] = ''
    os.environ['ORION_GRAPH_SPEC_URL'] = ''


def reset_graph_spec_config():
    os.environ['ORION_GRAPH_SPEC'] = 'testing-graph-spec.yaml'
    os.environ['ORION_GRAPH_SPEC_URL'] = ''


def get_testing_graph_spec_dir():
    # this is ORION/tests/graph_specs not ORION/graph_specs
    testing_specs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'graph_specs')
    print(testing_specs_dir)
    return testing_specs_dir


def test_empty_graph_spec_config():
    clear_graph_spec_config()
    with pytest.raises(GraphSpecError):
        graph_builder = GraphBuilder(graph_specs_dir=get_testing_graph_spec_dir())


def test_invalid_graph_spec_config():
    clear_graph_spec_config()
    os.environ['ORION_GRAPH_SPEC'] = 'invalid-spec.yaml'
    with pytest.raises(GraphSpecError):
        graph_builder = GraphBuilder(graph_specs_dir=get_testing_graph_spec_dir())


def test_invalid_graph_spec_url_config():
    clear_graph_spec_config()
    os.environ['ORION_GRAPH_SPEC_URL'] = 'http://localhost/invalid_graph_spec_url'
    with pytest.raises(requests.exceptions.ConnectionError):
        graph_builder = GraphBuilder()


def test_valid_graph_spec_config():
    reset_graph_spec_config()
    os.environ['ORION_GRAPH_SPEC'] = 'testing-graph-spec.yaml'
    graph_builder = GraphBuilder(graph_specs_dir=get_testing_graph_spec_dir())
    assert len(graph_builder.graph_specs)

    testing_graph_spec = graph_builder.graph_specs.get('Testing_Graph', None)
    assert testing_graph_spec is not None

    assert len(testing_graph_spec.sources) == 2

    for source in testing_graph_spec.sources:
        assert source.version is None


def test_graph_spec_lazy_versions():
    reset_graph_spec_config()
    os.environ['ORION_GRAPH_SPEC'] = 'testing-graph-spec.yaml'
    graph_builder = GraphBuilder(graph_specs_dir=get_testing_graph_spec_dir())
    testing_graph_spec = graph_builder.graph_specs.get('Testing_Graph', None)
    for source in testing_graph_spec.sources:
        assert source.version is None
    for source in testing_graph_spec.sources:
        source.source_version = source.id + "_1"
    for source in testing_graph_spec.sources:
        assert source.version is not None



