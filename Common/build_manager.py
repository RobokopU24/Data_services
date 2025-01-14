import os
import yaml
import argparse
import datetime
import requests

from pathlib import Path
from xxhash import xxh64_hexdigest
from collections import defaultdict
from Common.biolink_utils import BiolinkInformationResources, INFORES_STATUS_INVALID, INFORES_STATUS_DEPRECATED
from Common.utils import LoggingUtil, quick_jsonl_file_iterator, GetDataPullError
from Common.data_sources import get_available_data_sources
from Common.exceptions import DataVersionError, GraphSpecError
from Common.load_manager import SourceDataManager
from Common.kgx_file_merger import KGXFileMerger
from Common.neo4j_tools import create_neo4j_dump
from Common.kgxmodel import GraphSpec, SubGraphSource, DataSource
from Common.normalization import NORMALIZATION_CODE_VERSION, NormalizationScheme
from Common.metadata import Metadata, GraphMetadata, SourceMetadata
from Common.supplementation import SequenceVariantSupplementation
from Common.biolink_constants import PRIMARY_KNOWLEDGE_SOURCE, AGGREGATOR_KNOWLEDGE_SOURCES, PREDICATE, PUBLICATIONS
from Common.meta_kg import MetaKnowledgeGraphBuilder, META_KG_FILENAME, TEST_DATA_FILENAME
from Common.redundant_kg import generate_redundant_kg
from Common.collapse_qualifiers import generate_collapsed_qualifiers_kg

NODES_FILENAME = 'nodes.jsonl'
EDGES_FILENAME = 'edges.jsonl'
REDUNDANT_EDGES_FILENAME = 'redundant_edges.jsonl'
COLLAPSED_QUALIFIERS_FILENAME = 'collapsed_qualifier_edges.jsonl'


class GraphBuilder:

    def __init__(self,
                 graph_specs_dir=None):

        self.logger = LoggingUtil.init_logging("ORION.Common.GraphBuilder",
                                               line_format='medium',
                                               log_file_path=os.environ['ORION_LOGS'])

        self.graphs_dir = self.get_graphs_dir()  # path to the graphs output directory
        self.source_data_manager = SourceDataManager()  # access to the data sources and their metadata
        self.graph_specs = {}   # graph_id -> GraphSpec all potential graphs that could be built, including sub-graphs
        self.load_graph_specs(graph_specs_dir=graph_specs_dir)
        self.build_results = {}

    def build_graph(self, graph_spec: GraphSpec):

        graph_id = graph_spec.graph_id
        self.logger.info(f'Building graph {graph_id}...')

        graph_version = self.determine_graph_version(graph_spec)
        graph_metadata = self.get_graph_metadata(graph_id, graph_version)
        graph_output_dir = self.get_graph_dir_path(graph_id, graph_version)

        # check for previous builds of this same graph
        build_status = graph_metadata.get_build_status()
        if build_status == Metadata.IN_PROGRESS:
            self.logger.info(f'Graph {graph_id} version {graph_version} has status: in progress. '
                             f'This means either the graph is already in the process of being built, '
                             f'or an error occurred previously that could not be handled. '
                             f'You may need to clean up and/or remove the failed build.')
            return False

        if build_status == Metadata.BROKEN or build_status == Metadata.FAILED:
            self.logger.info(f'Graph {graph_id} version {graph_version} previously failed to build. Skipping..')
            return False

        if build_status == Metadata.STABLE:
            self.logger.info(f'Graph {graph_id} version {graph_version} was already built.')
            return True
        else:
            # if we get here we need to build the graph
            self.logger.info(f'Building graph {graph_id} version {graph_version}, checking dependencies...')
            if not self.build_dependencies(graph_spec):
                self.logger.warning(f'Aborting graph {graph_spec.graph_id} version {graph_version}, building '
                                    f'dependencies failed.')
                return False

            self.logger.info(f'Building graph {graph_id} version {graph_version}. '
                             f'Dependencies ready, merging sources...')
            graph_metadata.set_build_status(Metadata.IN_PROGRESS)
            graph_metadata.set_graph_version(graph_version)
            graph_metadata.set_graph_name(graph_spec.graph_name)
            graph_metadata.set_graph_description(graph_spec.graph_description)
            graph_metadata.set_graph_url(graph_spec.graph_url)
            graph_metadata.set_graph_spec(graph_spec.get_metadata_representation())

            # merge the sources and write the finalized graph kgx files
            source_merger = KGXFileMerger(output_directory=graph_output_dir)
            merge_metadata = source_merger.merge(graph_spec,
                                                 nodes_output_filename=NODES_FILENAME,
                                                 edges_output_filename=EDGES_FILENAME)

            current_time = datetime.datetime.now().strftime('%m-%d-%y %H:%M:%S')
            if "merge_error" in merge_metadata:
                graph_metadata.set_build_error(merge_metadata["merge_error"], current_time)
                graph_metadata.set_build_status(Metadata.FAILED)
                self.logger.error(f'Merge error occured while building graph {graph_id}: '
                                  f'{merge_metadata["merge_error"]}')
                return False

            graph_metadata.set_build_info(merge_metadata, current_time)
            graph_metadata.set_build_status(Metadata.STABLE)
            self.logger.info(f'Building graph {graph_id} complete!')
            self.build_results[graph_id] = {'version': graph_version, 'success': True}

        if not graph_metadata.has_qc():
            self.logger.info(f'Running QC for graph {graph_id}...')
            qc_results = self.run_qc(graph_id, graph_version, graph_directory=graph_output_dir)
            graph_metadata.set_qc_results(qc_results)
            if qc_results['pass']:
                self.logger.info(f'QC passed for graph {graph_id}.')
            else:
                self.logger.warning(f'QC failed for graph {graph_id}.')
                self.build_results[graph_id] = {'version': graph_version, 'success': False}

        needs_meta_kg = not self.has_meta_kg(graph_directory=graph_output_dir)
        needs_test_data = not self.has_test_data(graph_directory=graph_output_dir)
        if needs_meta_kg or needs_test_data:
            self.logger.info(f'Generating MetaKG and test data for {graph_id}...')
            self.generate_meta_kg_and_test_data(graph_directory=graph_output_dir,
                                                generate_meta_kg=needs_meta_kg,
                                                generate_test_data=needs_test_data)

        output_formats = graph_spec.graph_output_format.lower().split('+') if graph_spec.graph_output_format else []
        nodes_filepath = os.path.join(graph_output_dir, NODES_FILENAME)
        edges_filepath = os.path.join(graph_output_dir, EDGES_FILENAME)
        
        if 'redundant_jsonl' in output_formats:
            self.logger.info(f'Generating redundant edge KG for {graph_id}...')
            redundant_filepath = edges_filepath.replace(EDGES_FILENAME, REDUNDANT_EDGES_FILENAME)
            generate_redundant_kg(edges_filepath, redundant_filepath)

        if 'redundant_neo4j' in output_formats:
            self.logger.info(f'Generating redundant edge KG for {graph_id}...')
            redundant_filepath = edges_filepath.replace(EDGES_FILENAME, REDUNDANT_EDGES_FILENAME)
            generate_redundant_kg(edges_filepath, redundant_filepath)
            self.logger.info(f'Starting Neo4j dump pipeline for redundant {graph_id}...')
            dump_success = create_neo4j_dump(nodes_filepath=nodes_filepath,
                                             edges_filepath=redundant_filepath,
                                             output_directory=graph_output_dir,
                                             graph_id=graph_id,
                                             graph_version=graph_version,
                                             logger=self.logger)

            if dump_success:
                graph_output_url = self.get_graph_output_url(graph_id, graph_version)
                graph_metadata.set_dump_url(f'{graph_output_url}graph_{graph_version}_redundant.db.dump')
            
        if 'collapsed_qualifiers_jsonl' in output_formats:
            self.logger.info(f'Generating collapsed qualifier predicates KG for {graph_id}...')
            collapsed_qualifiers_filepath = edges_filepath.replace(EDGES_FILENAME, COLLAPSED_QUALIFIERS_FILENAME)
            generate_collapsed_qualifiers_kg(edges_filepath, collapsed_qualifiers_filepath)

        if 'collapsed_qualifiers_neo4j' in output_formats:
            self.logger.info(f'Generating collapsed qualifier predicates KG for {graph_id}...')
            collapsed_qualifiers_filepath = edges_filepath.replace(EDGES_FILENAME, COLLAPSED_QUALIFIERS_FILENAME)
            generate_collapsed_qualifiers_kg(edges_filepath, collapsed_qualifiers_filepath)
            self.logger.info(f'Starting Neo4j dump pipeline for {graph_id} with collapsed qualifiers...')
            dump_success = create_neo4j_dump(nodes_filepath=nodes_filepath,
                                             edges_filepath=collapsed_qualifiers_filepath,
                                             output_directory=graph_output_dir,
                                             graph_id=graph_id,
                                             graph_version=graph_version,
                                             logger=self.logger)

            if dump_success:
                graph_output_url = self.get_graph_output_url(graph_id, graph_version)
                graph_metadata.set_dump_url(f'{graph_output_url}graph_{graph_version}_collapsed_qualifiers.db.dump')

        if 'neo4j' in output_formats:
            self.logger.info(f'Starting Neo4j dump pipeline for {graph_id}...')
            dump_success = create_neo4j_dump(nodes_filepath=nodes_filepath,
                                             edges_filepath=edges_filepath,
                                             output_directory=graph_output_dir,
                                             graph_id=graph_id,
                                             graph_version=graph_version,
                                             logger=self.logger)

            if dump_success:
                graph_output_url = self.get_graph_output_url(graph_id, graph_version)
                graph_metadata.set_dump_url(f'{graph_output_url}graph_{graph_version}.db.dump')
    
        return True

    # determine a graph version utilizing versions of data sources, or just return the graph version specified
    def determine_graph_version(self, graph_spec: GraphSpec):
        # if the version was set or previously determined just back out
        if graph_spec.graph_version:
            return graph_spec.graph_version
        try:
            # go out and find the latest version for any data source that doesn't have a version specified
            for source in graph_spec.sources:
                if not source.source_version:
                    source.source_version = self.source_data_manager.get_latest_source_version(source.id)
                self.logger.info(f'Source version - {source.id}: {source.version}')

            # for sub-graphs, if a graph version isn't specified,
            # use the graph spec for that subgraph to determine a graph version
            for subgraph in graph_spec.subgraphs:
                if not subgraph.graph_version:
                    subgraph_graph_spec = self.graph_specs.get(subgraph.id, None)
                    if subgraph_graph_spec:
                        subgraph.graph_version = self.determine_graph_version(subgraph_graph_spec)
                        self.logger.info(f'found subgraph version, {graph_spec.graph_id}: {subgraph.graph_version}')
                    else:
                        raise GraphSpecError(f'Subgraph {subgraph.id} requested for graph {graph_spec.graph_id} '
                                             f'but the version was not specified and could not be determined without '
                                             f'a graph spec for {subgraph.id}.')
        except (GetDataPullError, DataVersionError) as e:
            raise GraphSpecError(error_message=e.error_message)

        # make a string that is a composite of versions and their merge strategy for each source
        composite_version_string = ""
        if graph_spec.sources:
            composite_version_string += '_'.join([graph_source.version + '_' + graph_source.merge_strategy
                                                  if graph_source.merge_strategy else graph_source.version
                                                  for graph_source in graph_spec.sources])
        if graph_spec.subgraphs:
            if composite_version_string:
                composite_version_string += '_'
            composite_version_string += '_'.join([sub_graph_source.version + '_' + sub_graph_source.merge_strategy
                                                  if sub_graph_source.merge_strategy else sub_graph_source.version
                                                  for sub_graph_source in graph_spec.subgraphs])
        graph_version = xxh64_hexdigest(composite_version_string)
        graph_spec.graph_version = graph_version
        self.logger.info(f'Version determined for graph {graph_spec.graph_id}: {graph_version} ({composite_version_string})')
        return graph_version

    
    def build_dependencies(self, graph_spec: GraphSpec):
        graph_id = graph_spec.graph_id
        for subgraph_source in graph_spec.subgraphs:
            subgraph_id = subgraph_source.id
            subgraph_version = subgraph_source.version
            if not self.check_for_existing_graph_dir(subgraph_id, subgraph_version):
                # If the subgraph doesn't already exist, we need to make sure it matches the current version of the
                # subgraph as generated by the current graph spec, otherwise we won't be able to build it.
                subgraph_graph_spec = self.graph_specs.get(subgraph_id, None)
                if not subgraph_graph_spec:
                    self.logger.warning(f'Subgraph {subgraph_id} version {subgraph_version} was requested for graph '
                                        f'{graph_id} but it was not found and could not be built without a Graph Spec.')
                    return False

                if subgraph_version != subgraph_graph_spec.graph_version:
                    self.logger.error(f'Subgraph {subgraph_id} version {subgraph_version} was specified, but that '
                                      f'version of the graph could not be found. It can not be built now because the '
                                      f'current version is {subgraph_graph_spec.graph_version}. Either specify a '
                                      f'version that is already built, or remove the subgraph version specification to '
                                      f'automatically include the latest one.')
                    return False

                # here the graph specs and versions all look right, but we still need to build the subgraph
                self.logger.warning(f'Graph {graph_id}, subgraph dependency {subgraph_id} is not ready. Building now..')
                subgraph_build_success = self.build_graph(subgraph_graph_spec)
                if not subgraph_build_success:
                    return False

            # confirm the subgraph build worked and update the DataSource object in preparation for merging
            subgraph_metadata = self.get_graph_metadata(subgraph_id, subgraph_version)
            subgraph_source.graph_metadata = subgraph_metadata
            if subgraph_metadata.get_build_status() == Metadata.STABLE:
                subgraph_dir = self.get_graph_dir_path(subgraph_id, subgraph_version)
                subgraph_nodes_path = self.get_graph_nodes_file_path(subgraph_dir)
                subgraph_edges_path = self.get_graph_edges_file_path(subgraph_dir)
                subgraph_source.file_paths = [subgraph_nodes_path, subgraph_edges_path]
            else:
                self.logger.warning(f'Attempting to build graph {graph_id} failed, dependency subgraph {subgraph_id} '
                                    f'version {subgraph_version} was not built successfully.')
                return False

        for data_source in graph_spec.sources:
            source_id = data_source.id
            source_metadata: SourceMetadata = self.source_data_manager.get_source_metadata(source_id,
                                                                                           data_source.source_version)
            release_version = data_source.generate_version()
            release_metadata = source_metadata.get_release_info(release_version)
            if release_metadata is None:
                self.logger.info(
                    f'Attempting to build graph {graph_id}, '
                    f'dependency {source_id} is not ready. Building now...')
                pipeline_sucess = self.source_data_manager.run_pipeline(source_id,
                                                                        source_version=data_source.source_version,
                                                                        parsing_version=data_source.parsing_version,
                                                                        normalization_scheme=data_source.normalization_scheme,
                                                                        supplementation_version=data_source.supplementation_version)
                if not pipeline_sucess:
                    self.logger.info(f'While attempting to build {graph_spec.graph_id}, '
                                     f'data source pipeline failed for dependency {source_id}...')
                    return False
                release_metadata = source_metadata.get_release_info(release_version)

            data_source.release_info = release_metadata
            data_source.file_paths = self.source_data_manager.get_final_file_paths(source_id,
                                                                                   data_source.source_version,
                                                                                   data_source.parsing_version,
                                                                                   data_source.normalization_scheme.get_composite_normalization_version(),
                                                                                   data_source.supplementation_version)
        return True

    def has_meta_kg(self, graph_directory: str):
        if os.path.exists(os.path.join(graph_directory, META_KG_FILENAME)):
            return True
        else:
            return False

    def has_test_data(self, graph_directory: str):
        if os.path.exists(os.path.join(graph_directory, TEST_DATA_FILENAME)):
            return True
        else:
            return False

    def generate_meta_kg_and_test_data(self,
                                       graph_directory: str,
                                       generate_meta_kg: bool = True,
                                       generate_test_data: bool = True):
        graph_nodes_file_path = os.path.join(graph_directory, NODES_FILENAME)
        graph_edges_file_path = os.path.join(graph_directory, EDGES_FILENAME)
        mkgb = MetaKnowledgeGraphBuilder(nodes_file_path=graph_nodes_file_path,
                                         edges_file_path=graph_edges_file_path,
                                         logger=self.logger)
        if generate_meta_kg:
            meta_kg_file_path = os.path.join(graph_directory, META_KG_FILENAME)
            mkgb.write_meta_kg_to_file(meta_kg_file_path)
        if generate_test_data:
            test_data_file_path = os.path.join(graph_directory, TEST_DATA_FILENAME)
            mkgb.write_test_data_to_file(test_data_file_path)

    def run_qc(self,
               graph_id: str,
               graph_version: str,
               graph_directory: str):

        # Edges QC
        # Iterate through the edges and find all knowledge sources, edge properties, and predicates
        primary_knowledge_sources = set()
        aggregator_knowledge_sources = set()
        edge_properties = set()
        predicate_counts = defaultdict(int)
        predicate_counts_by_ks = defaultdict(lambda: defaultdict(int))
        edges_with_publications = defaultdict(int)
        graph_edges_file_path = os.path.join(graph_directory, EDGES_FILENAME)
        for edge_json in quick_jsonl_file_iterator(graph_edges_file_path):
            primary_knowledge_sources.add(edge_json[PRIMARY_KNOWLEDGE_SOURCE])
            if AGGREGATOR_KNOWLEDGE_SOURCES in edge_json:
                for ks in edge_json[AGGREGATOR_KNOWLEDGE_SOURCES]:
                    aggregator_knowledge_sources.add(ks)
            for key in edge_json.keys():
                edge_properties.add(key)
            predicate_counts[edge_json[PREDICATE]] += 1
            predicate_counts_by_ks[edge_json[PRIMARY_KNOWLEDGE_SOURCE]][edge_json[PREDICATE]] += 1
            if PUBLICATIONS in edge_json and edge_json[PUBLICATIONS]:
                edges_with_publications[edge_json[PREDICATE]] += 1

        # validate the knowledge sources with the biolink model
        bl_inforesources = BiolinkInformationResources()
        deprecated_infores_ids = []
        invalid_infores_ids = []
        all_knowledge_sources = primary_knowledge_sources | aggregator_knowledge_sources
        for knowledge_source in all_knowledge_sources:
            infores_status = bl_inforesources.get_infores_status(knowledge_source)
            if infores_status == INFORES_STATUS_DEPRECATED:
                deprecated_infores_ids.append(knowledge_source)
                self.logger.warning(f'QC for graph {graph_id} version {graph_version} found a deprecated infores '
                                    f'identifier: {knowledge_source}')
            elif infores_status == INFORES_STATUS_INVALID:
                invalid_infores_ids.append(knowledge_source)
                self.logger.warning(f'QC for graph {graph_id} version {graph_version} found an invalid infores '
                                    f'identifier: {knowledge_source}')

        # nodes QC
        node_curie_prefixes = defaultdict(int)
        graph_nodes_file_path = os.path.join(graph_directory, NODES_FILENAME)
        for node in quick_jsonl_file_iterator(graph_nodes_file_path):
            node_curie_prefixes[node['id'].split(':')[0]] += 1

        qc_metadata = {
            'pass': True,
            'primary_knowledge_sources': list(primary_knowledge_sources),
            'aggregator_knowledge_sources': list(aggregator_knowledge_sources),
            'predicate_totals': {k: v for k, v in predicate_counts.items()},
            'predicates_by_knowledge_source': {ks: {predicate: count for predicate, count in ks_to_p.items()}
                                               for ks, ks_to_p in predicate_counts_by_ks.items()},
            'node_curie_prefixes': {k: v for k, v in node_curie_prefixes.items()},
            'edges_with_publications': {k: v for k, v in edges_with_publications.items()},
            'edge_properties': list(edge_properties),
            'warnings': {}
        }
        if deprecated_infores_ids:
            qc_metadata['warnings']['deprecated_knowledge_sources'] = deprecated_infores_ids
        if invalid_infores_ids:
            qc_metadata['warnings']['invalid_knowledge_sources'] = invalid_infores_ids
        return qc_metadata

    def load_graph_specs(self, graph_specs_dir=None):
        graph_spec_file = os.environ.get('ORION_GRAPH_SPEC', None)
        graph_spec_url = os.environ.get('ORION_GRAPH_SPEC_URL', None)

        if graph_spec_file and graph_spec_url:
            raise GraphSpecError(f'Configuration Error - the environment variables ORION_GRAPH_SPEC and '
                                 f'ORION_GRAPH_SPEC_URL were set. Please choose one or the other. See the README for '
                                 f'details.')

        if graph_spec_file:
            if not graph_specs_dir:
                graph_specs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'graph_specs')
            graph_spec_path = os.path.join(graph_specs_dir, graph_spec_file)
            if os.path.exists(graph_spec_path):
                self.logger.info(f'Loading graph spec: {graph_spec_file}')
                with open(graph_spec_path) as graph_spec_file:
                    graph_spec_yaml = yaml.safe_load(graph_spec_file)
                    self.parse_graph_spec(graph_spec_yaml)
                    return
            else:
                raise GraphSpecError(f'Configuration Error - Graph Spec could not be found: {graph_spec_file}')

        if graph_spec_url:
            graph_spec_request = requests.get(graph_spec_url)
            graph_spec_request.raise_for_status()
            graph_spec_yaml = yaml.safe_load(graph_spec_request.text)
            self.parse_graph_spec(graph_spec_yaml)
            return

        raise GraphSpecError(f'Configuration Error - No Graph Spec was configured. Set the environment variable '
                             f'ORION_GRAPH_SPEC to the name of a graph spec included in this package, or '
                             f'ORION_GRAPH_SPEC_URL to a URL of a valid Graph Spec yaml file. '
                             f'See the README for more info.')

    def parse_graph_spec(self, graph_spec_yaml):
        graph_id = None
        try:
            for graph_yaml in graph_spec_yaml['graphs']:
                graph_id = graph_yaml['graph_id']
                graph_name = graph_yaml.get('graph_name', '')
                graph_description = graph_yaml.get('graph_description', '')
                graph_url = graph_yaml.get('graph_url', '')

                # parse the list of data sources
                data_sources = [self.parse_data_source_spec(data_source)
                                for data_source in graph_yaml.get('sources', [])]

                # parse the list of subgraphs
                subgraph_sources = [self.parse_subgraph_spec(subgraph)
                                    for subgraph in graph_yaml.get('subgraphs', [])]

                if not data_sources and not subgraph_sources:
                    raise GraphSpecError('Error: No sources were provided for graph: {graph_id}.')

                # see if there are any normalization scheme parameters specified at the graph level
                graph_wide_node_norm_version = graph_yaml.get('node_normalization_version', None)
                graph_wide_edge_norm_version = graph_yaml.get('edge_normalization_version', None)
                graph_wide_conflation = graph_yaml.get('conflation', None)
                graph_wide_strict_norm = graph_yaml.get('strict_normalization', None)
                if graph_wide_node_norm_version == 'latest':
                    graph_wide_node_norm_version = self.source_data_manager.get_latest_node_normalization_version()
                if graph_wide_edge_norm_version == 'latest':
                    graph_wide_edge_norm_version = self.source_data_manager.get_latest_edge_normalization_version()

                # apply them to all the data sources, this will overwrite anything defined at the source level
                for data_source in data_sources:
                    if graph_wide_node_norm_version is not None:
                        data_source.normalization_scheme.node_normalization_version = graph_wide_node_norm_version
                    if graph_wide_edge_norm_version is not None:
                        data_source.normalization_scheme.edge_normalization_version = graph_wide_edge_norm_version
                    if graph_wide_conflation is not None:
                        data_source.normalization_scheme.conflation = graph_wide_conflation
                    if graph_wide_strict_norm is not None:
                        data_source.normalization_scheme.strict = graph_wide_strict_norm

                graph_output_format = graph_yaml.get('output_format', '')
                graph_spec = GraphSpec(graph_id=graph_id,
                                       graph_name=graph_name,
                                       graph_description=graph_description,
                                       graph_url=graph_url,
                                       graph_version=None,  # this will get populated when a build is triggered
                                       graph_output_format=graph_output_format,
                                       subgraphs=subgraph_sources,
                                       sources=data_sources)
                self.graph_specs[graph_id] = graph_spec
        except KeyError as e:
            error_message = f'Graph Spec missing required field: {e}'
            if graph_id is not None:
                error_message += f"(in graph {graph_id})"
            raise GraphSpecError(error_message)

    def parse_subgraph_spec(self, subgraph_yml):
        subgraph_id = subgraph_yml['graph_id']
        subgraph_version = subgraph_yml.get('graph_version', None)
        merge_strategy = subgraph_yml.get('merge_strategy', None)
        if merge_strategy == 'default':
            merge_strategy = None
        subgraph_source = SubGraphSource(id=subgraph_id,
                                         graph_version=subgraph_version,
                                         merge_strategy=merge_strategy)
        return subgraph_source

    def parse_data_source_spec(self, source_yml):
        # get the source id and make sure it's valid
        source_id = source_yml['source_id']
        if source_id not in get_available_data_sources():
            error_message = f'Data source {source_id} is not a valid data source id.'
            self.logger.error(error_message + " " +
                              f'Valid sources are: {", ".join(get_available_data_sources())}')
            raise GraphSpecError(error_message)

        # read version and normalization specifications from the graph spec
        source_version = source_yml.get('source_version', None)
        parsing_version = source_yml.get('parsing_version', None)
        merge_strategy = source_yml.get('merge_strategy', None)
        node_normalization_version = source_yml.get('node_normalization_version', None)
        edge_normalization_version = source_yml.get('edge_normalization_version', None)
        strict_normalization = source_yml.get('strict_normalization', True)
        conflation = source_yml.get('conflation', False)

        # supplementation and normalization code version cannot be specified, set them to the current version
        supplementation_version = SequenceVariantSupplementation.SUPPLEMENTATION_VERSION
        normalization_code_version = NORMALIZATION_CODE_VERSION

        # if normalization versions are not specified, set them to the current latest
        # source_version is intentionally not handled here because we want to do it lazily and avoid if not needed
        if not parsing_version or parsing_version == 'latest':
            parsing_version = self.source_data_manager.get_latest_parsing_version(source_id)
        if not node_normalization_version or node_normalization_version == 'latest':
            node_normalization_version = self.source_data_manager.get_latest_node_normalization_version()
        if not edge_normalization_version or edge_normalization_version == 'latest':
            edge_normalization_version = self.source_data_manager.get_latest_edge_normalization_version()

        # do some validation
        if type(strict_normalization) != bool:
            raise GraphSpecError(f'Invalid type (strict_normalization: {strict_normalization}), must be true or false.')
        if type(conflation) != bool:
            raise GraphSpecError(f'Invalid type (conflation: {conflation}), must be true or false.')
        if merge_strategy == 'default':
            merge_strategy = None

        normalization_scheme = NormalizationScheme(node_normalization_version=node_normalization_version,
                                                   edge_normalization_version=edge_normalization_version,
                                                   normalization_code_version=normalization_code_version,
                                                   strict=strict_normalization,
                                                   conflation=conflation)
        data_source = DataSource(id=source_id,
                                 source_version=source_version,
                                 merge_strategy=merge_strategy,
                                 normalization_scheme=normalization_scheme,
                                 parsing_version=parsing_version,
                                 supplementation_version=supplementation_version)
        return data_source

    def get_graph_dir_path(self, graph_id: str, graph_version: str):
        return os.path.join(self.graphs_dir, graph_id, graph_version)

    def get_graph_output_url(self, graph_id: str, graph_version: str):
        graph_output_url = os.environ.get('ORION_OUTPUT_URL', "https://localhost/")
        if graph_output_url[-1] != '/':
            graph_output_url += '/'
        return f'{graph_output_url}{graph_id}/{graph_version}/'

    @staticmethod
    def get_graph_nodes_file_path(graph_output_dir: str):
        return os.path.join(graph_output_dir, NODES_FILENAME)

    @staticmethod
    def get_graph_edges_file_path(graph_output_dir: str):
        return os.path.join(graph_output_dir, EDGES_FILENAME)

    def check_for_existing_graph_dir(self, graph_id: str, graph_version: str):
        graph_output_dir = self.get_graph_dir_path(graph_id, graph_version)
        if not os.path.isdir(graph_output_dir):
            return False
        return True

    def get_graph_metadata(self, graph_id: str, graph_version: str):
        # make sure the output directory exists (where we check for existing GraphMetadata)
        graph_output_dir = self.get_graph_dir_path(graph_id, graph_version)

        # load existing or create new metadata file
        return GraphMetadata(graph_id, graph_output_dir)

    @staticmethod
    def get_graphs_dir():
        # confirm the directory specified by the environment variable ORION_GRAPHS is valid
        graphs_dir = os.environ.get('ORION_GRAPHS', None)
        if graphs_dir and Path(graphs_dir).is_dir():
            return os.environ['ORION_GRAPHS']

        # if invalid or not specified back out
        raise IOError('ORION graphs directory not configured properly. '
                      'Specify a valid directory with environment variable ORION_GRAPHS.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Merge data sources into complete graphs.")
    parser.add_argument('graph_id',
                        help='ID of the graph to build. Must match an ID from the configured Graph Spec.')
    args = parser.parse_args()
    graph_id_arg = args.graph_id

    graph_builder = GraphBuilder()
    if graph_id_arg == "all":
        for graph_spec in graph_builder.graph_specs.values():
            graph_builder.build_graph(graph_spec)
    else:
        graph_spec = graph_builder.graph_specs.get(graph_id_arg, None)
        if graph_spec:
            graph_builder.build_graph(graph_spec)
        else:
            print(f'Invalid graph spec requested: {graph_id_arg}')
    for results_graph_id, results in graph_builder.build_results.items():
        if results['success']:
            print(f'{results_graph_id}\t{results["version"]}')
