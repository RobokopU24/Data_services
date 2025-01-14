import os
import jsonlines
from itertools import chain
from Common.utils import LoggingUtil, quick_jsonl_file_iterator, quick_json_dumps, quick_json_loads
from Common.kgxmodel import GraphSpec, SubGraphSource, DataSource
from Common.biolink_constants import SUBJECT_ID, OBJECT_ID
from Common.merging import GraphMerger, DiskGraphMerger, MemoryGraphMerger
from Common.load_manager import RESOURCE_HOGS

# import line_profiler
# import atexit
# profile = line_profiler.LineProfiler()
# atexit.register(profile.print_stats)


class KGXFileMerger:

    def __init__(self,
                 output_directory: str):
        self.output_directory = output_directory
        self.logger = LoggingUtil.init_logging("ORION.Common.KGXFileMerger",
                                               line_format='medium',
                                               log_file_path=os.environ['ORION_LOGS'])

    def merge(self,
              graph_spec: GraphSpec,
              nodes_output_filename: str,
              edges_output_filename: str):

        if not (graph_spec.sources or graph_spec.subgraphs):
            merge_error_msg = f'Merge attempted but {graph_spec.graph_id} had no sources to merge.'
            self.logger.error(merge_error_msg)
            return {'merge_error': merge_error_msg}

        nodes_output_file_path = os.path.join(self.output_directory, nodes_output_filename)
        edges_output_file_path = os.path.join(self.output_directory, edges_output_filename)
        if os.path.exists(nodes_output_file_path) or os.path.exists(edges_output_file_path):
            merge_error_msg = f'Merge attempted for {graph_spec.graph_id} but merged files already existed!'
            self.logger.error(merge_error_msg)
            return {'merge_error': merge_error_msg}

        # group the sources based on their merge strategy, we'll process the primary sources first
        primary_sources = []
        secondary_sources = []
        for graph_source in chain(graph_spec.sources, graph_spec.subgraphs):
            if not graph_source.merge_strategy:
                primary_sources.append(graph_source)
            elif graph_source.merge_strategy == 'connected_edge_subset':
                secondary_sources.append(graph_source)
            else:
                return {'merge_error': f'Unsupported merge strategy specified: {graph_source.merge_strategy}'}

        # TODO we should be able to process a single primary source more efficiently (ie copy and paste it)
        # if len(primary_sources) == 1:
        #    self.process_single_source(primary_sources[0], nodes_output_file_path, edges_output_file_path)
        # else:

        merge_metadata = {
            'sources': {},
            'final_node_count': 0,
            'final_edge_count': 0
        }

        self.merge_primary_sources(primary_sources,
                                   nodes_output_file_path,
                                   edges_output_file_path,
                                   merge_metadata)

        self.merge_secondary_sources(secondary_sources,
                                     nodes_output_file_path,
                                     edges_output_file_path,
                                     merge_metadata)

        if len(merge_metadata['sources']) != len(graph_spec.sources) + len(graph_spec.subgraphs):
            all_source_ids = [graph_source.id for graph_source in chain(graph_spec.sources, graph_spec.subgraphs)]
            missing_data_sets = [source_id for source_id in all_source_ids if
                                 source_id not in merge_metadata['sources'].keys()]
            error_message = f"Error merging graph {graph_spec.graph_id}! could not merge: {missing_data_sets}"
            self.logger.error(error_message)
            merge_metadata["merge_error"] = error_message
        return merge_metadata

    def merge_primary_sources(self,
                              graph_sources: list,
                              nodes_out_file: str,
                              edges_out_file: str,
                              merge_metadata: dict):
        needs_on_disk_merge = False
        for graph_source in graph_sources:
            if isinstance(graph_source, SubGraphSource):
                for source_id in graph_source.graph_metadata.get_source_ids():
                    if source_id in RESOURCE_HOGS:
                        needs_on_disk_merge = True
                        break
            elif graph_source.id in RESOURCE_HOGS:
                needs_on_disk_merge = True
                break

        if needs_on_disk_merge:
            graph_merger = DiskGraphMerger(temp_directory=self.output_directory)
        else:
            graph_merger = MemoryGraphMerger()
        for i, graph_source in enumerate(graph_sources, start=1):
            self.logger.info(f"Processing {graph_source.id}. (primary source {i}/{len(graph_sources)})")
            merge_metadata["sources"][graph_source.id] = {'release_version': graph_source.version}

            for file_path in graph_source.file_paths:
                source_filename = file_path.rsplit('/')[-1]
                merge_metadata["sources"][graph_source.id][source_filename] = {}
                if "nodes" in file_path:
                    with jsonlines.open(file_path) as nodes:
                        nodes_count = graph_merger.merge_nodes(nodes)
                        merge_metadata["sources"][graph_source.id][source_filename]["nodes"] = nodes_count

                elif "edges" in file_path:
                    with jsonlines.open(file_path) as edges:
                        edges_count = graph_merger.merge_edges(edges)
                        merge_metadata["sources"][graph_source.id][source_filename]["edges"] = edges_count

                else:
                    raise ValueError(f"Did not recognize file {file_path} for merging "
                                     f"from data source {graph_source.id}.")

        nodes_written, edges_written = self.__write_back_to_file(graph_merger,
                                                                 nodes_out_file,
                                                                 edges_out_file)
        merge_metadata['merged_nodes'] = graph_merger.merged_node_counter
        merge_metadata['merged_edges'] = graph_merger.merged_edge_counter
        merge_metadata['final_node_count'] += nodes_written
        merge_metadata['final_edge_count'] += edges_written
        return True

    # nodes_output_file_path and edges_output_file_path could/should be existing kgx files that will be appended to
    def merge_secondary_sources(self,
                                graph_sources: list,
                                nodes_output_file_path: str,
                                edges_output_file_path: str,
                                merge_metadata: dict):
        for i, graph_source in enumerate(graph_sources, start=1):
            self.logger.info(f"Processing {graph_source.id}. (secondary source {i}/{len(graph_sources)})")
            if graph_source.merge_strategy == 'connected_edge_subset':
                self.logger.info(f"Merging {graph_source.id} using connected_edge_subset merge strategy.")

                merge_metadata["sources"][graph_source.id] = {'release_version': graph_source.version}

                file_path_iterator = iter(graph_source.file_paths)
                for file_path in file_path_iterator:
                    nodes_file_to_merge = file_path
                    edges_file_to_merge = next(file_path_iterator)
                    if not (('nodes' in nodes_file_to_merge) and
                            ('edges' in edges_file_to_merge)):
                        raise IOError(
                            f'File paths were not in node, edge ordered pairs: {nodes_file_to_merge},{edges_file_to_merge}')
                    new_node_count, new_edge_count = self.merge_connected_edges(nodes_output_file_path,
                                                                                edges_output_file_path,
                                                                                nodes_file_to_merge,
                                                                                edges_file_to_merge)
                    nodes_source_filename = nodes_file_to_merge.rsplit('/')[-1]
                    edges_source_filename = edges_file_to_merge.rsplit('/')[-1]
                    merge_metadata["sources"][graph_source.id][nodes_source_filename] = {
                        "nodes": new_node_count
                    }
                    merge_metadata["sources"][graph_source.id][edges_source_filename] = {
                        "edges": new_edge_count
                    }
                    merge_metadata['final_node_count'] += new_node_count
                    merge_metadata['final_edge_count'] += new_edge_count

    def __write_back_to_file(self,
                             graph_merger: GraphMerger,
                             nodes_out_file: str,
                             edges_out_file: str):

        self.logger.debug(f'Writing merged nodes to file...')
        nodes_written = 0
        with open(nodes_out_file, 'w') as nodes_out:
            for node_line in graph_merger.get_merged_nodes_jsonl():
                nodes_out.write(node_line)
                nodes_written += 1

        self.logger.debug(f'Writing merged edges to file...')
        edges_written = 0
        with open(edges_out_file, 'w') as edges_out:
            for edge_line in graph_merger.get_merged_edges_jsonl():
                edges_out.write(edge_line)
                edges_written += 1

        return nodes_written, edges_written

    """
    This is on hold / TBD - we should be able to process individual sources more efficiently
    def process_single_source(self, graph_source: SourceDataSpec, nodes_out_file: str, edges_out_file: str):
        self.logger.info(f"Processing single primary source {graph_source.id}.")
        files_processed = 0
        files_to_process = graph_source.file_paths
        if len(files_to_process) <= 2:
            # TODO just copy them over, never needed to merge
            # maybe split nodes and edges file paths in SourceDataSpec to make that more flexible
        else:
            # merge all the files from just one source - probably dont need to check duplicates
    """

    """
    Given two sets of kgx files, A and B, append to A the edges from B that connect to nodes in A, 
    and any nodes from B that they connect to.
    """
    def merge_connected_edges(self, node_file_a, edge_file_a, node_file_b, edge_file_b):

        # first grab the node ids from file A
        node_ids = set([node['id'] for node in quick_jsonl_file_iterator(node_file_a)])
        connected_node_ids_not_in_a = set()

        # append to edges file A the edges from B that connect to nodes from A,
        # meanwhile determine the set of node ids from those edges that aren't in A already
        edges_added = 0
        with open(edge_file_a, 'a') as merged_edges_file, \
                open(edge_file_b, 'r', encoding='utf-8') as edges_from_b:
            for edge_line in edges_from_b:
                subject_connected = False
                object_connected = False
                edge = quick_json_loads(edge_line)
                if edge[SUBJECT_ID] in node_ids:
                    subject_connected = True
                if edge[OBJECT_ID] in node_ids:
                    object_connected = True
                if subject_connected or object_connected:
                    edges_added += 1
                    merged_edges_file.write(edge_line)
                    if not subject_connected:
                        connected_node_ids_not_in_a.add(edge[SUBJECT_ID])
                    elif not object_connected:
                        connected_node_ids_not_in_a.add(edge[OBJECT_ID])
        self.logger.debug(f'Added {edges_added} new connected edges')

        nodes_added = len(connected_node_ids_not_in_a)
        self.logger.debug(f'Found {nodes_added} new nodes from connected edges')

        with open(node_file_a, 'a') as merged_nodes_file:
            for node_line in open(node_file_b, 'r', encoding='utf-8'):
                node = quick_json_loads(node_line)
                if node['id'] in connected_node_ids_not_in_a:
                    merged_nodes_file.write(node_line)

        return nodes_added, edges_added

