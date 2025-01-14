
import os
import json
from xxhash import xxh64_hexdigest

from Common.normalization import NormalizationScheme


class Metadata:

    NOT_STARTED = 'not_started'
    STABLE = 'stable'
    IN_PROGRESS = 'in_progress'
    BROKEN = 'broken'
    FAILED = 'failed'

    def __init__(self, metadata_file_path: str):
        self.metadata = None
        self.metadata_file_path = metadata_file_path
        self.load_current_metadata()

    def load_current_metadata(self):
        if os.path.isfile(self.metadata_file_path):
            with open(self.metadata_file_path) as meta_json_file:
                self.metadata = json.load(meta_json_file)
        else:
            self.init_metadata()

    def init_metadata(self):
        raise NotImplementedError()

    def save_metadata(self):
        if not os.path.isdir(os.path.dirname(self.metadata_file_path)):
            os.makedirs(os.path.dirname(self.metadata_file_path))
        with open(self.metadata_file_path, 'w') as meta_json_file:
            json.dump(self.metadata, meta_json_file, indent=4)


class GraphMetadata(Metadata):

    def __init__(self, graph_id: str, graph_storage_dir: str):
        self.graph_id = graph_id
        metadata_file_path = os.path.join(graph_storage_dir, f'{graph_id}.meta.json')
        super().__init__(metadata_file_path=metadata_file_path)

    def init_metadata(self):
        self.metadata = dict()
        self.metadata['graph_id'] = self.graph_id
        self.metadata['graph_name'] = self.graph_id
        self.metadata['graph_description'] = ""
        self.metadata['graph_url'] = ""
        self.metadata['graph_version'] = None
        self.metadata['sources'] = []
        self.metadata['subgraphs'] = []
        self.reset_state_metadata()

    def reset_state_metadata(self):
        self.metadata['build_status'] = self.NOT_STARTED
        self.metadata['build_time'] = None
        self.metadata['build_error'] = None

    def set_graph_version(self, graph_version: str):
        self.metadata['graph_version'] = graph_version
        self.save_metadata()

    def set_graph_name(self, graph_name: str):
        if graph_name:
            self.metadata['graph_name'] = graph_name
            self.save_metadata()

    def set_graph_description(self, graph_description: str):
        if graph_description:
            self.metadata['graph_description'] = graph_description
            self.save_metadata()

    def set_graph_url(self, graph_url: str):
        if graph_url:
            self.metadata['graph_url'] = graph_url
            self.save_metadata()

    def set_graph_spec(self, graph_spec: dict):
        self.metadata['sources'] = graph_spec['sources']
        self.metadata['subgraphs'] = graph_spec['subgraphs']
        self.save_metadata()

    def set_dump_url(self, dump_url: str):
        self.metadata['neo4j_dump'] = dump_url
        self.save_metadata()

    def has_qc(self):
        if 'qc_results' in self.metadata and self.metadata['qc_results']:
            return True
        else:
            return False

    def set_qc_results(self, qc_results: dict):
        self.metadata['qc_results'] = qc_results
        self.save_metadata()

    def set_build_status(self, status: str):
        self.metadata['build_status'] = status
        self.save_metadata()

    def set_build_info(self, build_info: dict, build_time: str):
        for source_id, source_info in build_info['sources'].items():
            for graph_spec_source in self.metadata['sources']:
                if source_info['release_version'] == graph_spec_source['release_version']:
                    graph_spec_source.update(source_info)
        self.metadata.update({
            key: value for key, value in build_info.items() if key != 'sources'
        })
        self.metadata['build_time'] = build_time
        self.save_metadata()

    def set_build_error(self, build_error: str, error_time: str = None):
        self.metadata['build_error'] = build_error
        if error_time:
            self.metadata['build_error_time'] = error_time
        self.save_metadata()

    def get_build_status(self):
        return self.metadata['build_status']

    def get_graph_version(self):
        return self.metadata['graph_version']

    def get_source_ids(self):
        return [source['source_id'] for source in self.metadata['sources']]


class SourceMetadata(Metadata):

    def __init__(self, source_id: str, source_version: str, source_storage_dir: str):
        self.source_id = source_id
        self.source_version = source_version
        metadata_file_path = os.path.join(source_storage_dir, f'{source_id}.meta.json')
        super().__init__(metadata_file_path=metadata_file_path)

    def init_metadata(self):
        self.metadata = dict()
        self.metadata['source_id'] = self.source_id
        self.metadata['source_version'] = self.source_version
        self.metadata['fetch_status'] = self.NOT_STARTED
        self.metadata['parsings'] = dict()

    def get_source_version(self):
        self.load_current_metadata()
        return self.metadata['source_version']

    def set_fetch_status(self, fetch_status: str):
        self.metadata['fetch_status'] = fetch_status
        self.save_metadata()

    def get_fetch_status(self):
        return self.metadata['fetch_status']

    def set_fetch_error(self, fetch_error: str):
        self.metadata['fetch_error'] = fetch_error
        self.save_metadata()

    def get_initial_parsing_metadata(self):
        return {'parsing_status': self.NOT_STARTED,
                'parsing_source_version': None,
                'parsing_info': None,
                'parsing_time': None,
                'has_sequence_variants': None,
                'normalizations': dict()}

    def update_parsing_metadata(self,
                                parsing_version: str,
                                parsing_status: str = None,
                                parsing_source_version: str = None,
                                parsing_info: dict = None,
                                parsing_time: str = None,
                                parsing_error: str = None,
                                has_sequence_variants: bool = None):
        if parsing_version not in self.metadata['parsings']:
            self.metadata['parsings'][parsing_version] = self.get_initial_parsing_metadata()
        if parsing_status:
            self.metadata['parsings'][parsing_version]['parsing_status'] = parsing_status
        if parsing_source_version:
            self.metadata['parsings'][parsing_version]['parsing_source_version'] = parsing_source_version
        if parsing_info:
            self.metadata['parsings'][parsing_version]['parsing_info'] = parsing_info
        if parsing_error:
            self.metadata['parsings'][parsing_version]['parsing_error'] = parsing_error
        if parsing_time:
            self.metadata['parsings'][parsing_version]['parsing_time'] = parsing_time
        if has_sequence_variants is not None:
            self.metadata['parsings'][parsing_version]['has_sequence_variants'] = has_sequence_variants
        self.save_metadata()

    def get_parsing_status(self, parsing_version: str):
        self.load_current_metadata()
        if parsing_version in self.metadata['parsings']:
            return self.metadata['parsings'][parsing_version]['parsing_status']
        else:
            return self.NOT_STARTED

    def reset_parsing(self, parsing_version: str):
        if parsing_version in self.metadata['parsings']:
            self.metadata['parsings'][parsing_version] = self.get_initial_parsing_metadata()

    def get_parsing_error(self, parsing_version: str):
        self.load_current_metadata()
        if parsing_version in self.metadata['parsings']:
            if 'parsing_error' in self.metadata['parsings'][parsing_version]:
                return self.metadata['parsings'][parsing_version]['parsing_error']
        return None

    def has_sequence_variants(self, parsing_version: str):
        if parsing_version in self.metadata['parsings']:
            return self.metadata['parsings'][parsing_version]['has_sequence_variants']
        return False

    def update_normalization_metadata(self,
                                      parsing_version: str,
                                      normalization_version: str,
                                      normalization_scheme: NormalizationScheme = None,
                                      normalization_status: str = None,
                                      normalization_info: dict = None,
                                      normalization_time: str = None,
                                      normalization_error: str = None):
        if normalization_version not in self.metadata['parsings'][parsing_version]['normalizations']:
            normalization_dict = {
                'normalization_status': self.NOT_STARTED,
                'normalization_metadata': None,
                'normalization_time': None,
                'supplementations': dict()
            }
            self.metadata['parsings'][parsing_version]['normalizations'][normalization_version] = normalization_dict
        else:
            normalization_dict = self.metadata['parsings'][parsing_version]['normalizations'][normalization_version]
        if normalization_status:
            normalization_dict['normalization_status'] = normalization_status
        if normalization_info:
            normalization_dict['normalization_info'] = normalization_info
        if normalization_scheme:
            normalization_dict['node_normalization_version'] = normalization_scheme.node_normalization_version
            normalization_dict['edge_normalization_version'] = normalization_scheme.edge_normalization_version
            normalization_dict['strict_normalization'] = normalization_scheme.strict
            normalization_dict['conflation'] = normalization_scheme.conflation
        if normalization_time:
            normalization_dict['normalization_time'] = normalization_time
        if normalization_error:
            normalization_dict['normalization_error'] = normalization_error
        self.save_metadata()

    def get_normalization_status(self, parsing_version: str, normalization_version: str):
        self.load_current_metadata()
        if normalization_version in self.metadata['parsings'][parsing_version]['normalizations']:
            return self.metadata['parsings'][parsing_version]['normalizations'][normalization_version]['normalization_status']
        else:
            return self.NOT_STARTED

    def update_supplementation_metadata(self,
                                        parsing_version: str,
                                        normalization_version: str,
                                        supplementation_version: str,
                                        supplementation_status: str = None,
                                        supplementation_info: dict = None,
                                        supplementation_time: str = None,
                                        supplementation_error: str = None):
        if supplementation_version not in self.metadata['parsings'][parsing_version]['normalizations'][normalization_version]['supplementations']:
            supplementation_dict = {
                'supplementation_status': self.NOT_STARTED,
                'supplementation_time': None,
            }
            self.metadata['parsings'][parsing_version]['normalizations'][normalization_version]['supplementations'][supplementation_version] = supplementation_dict
        else:
            supplementation_dict = self.metadata['parsings'][parsing_version]['normalizations'][normalization_version]['supplementations'][supplementation_version]
        if supplementation_status:
            supplementation_dict['supplementation_status'] = supplementation_status
        if supplementation_info:
            supplementation_dict['supplementation_info'] = supplementation_info
        if supplementation_time:
            supplementation_dict['supplementation_time'] = supplementation_time
        if supplementation_error:
            supplementation_dict['supplementation_error'] = supplementation_error
        self.save_metadata()

    def get_supplementation_status(self, parsing_version: str, normalization_version: str, supplementation_version: str):
        self.load_current_metadata()
        if supplementation_version in self.metadata['parsings'][parsing_version]['normalizations'][normalization_version]['supplementations']:
            return self.metadata['parsings'][parsing_version]['normalizations'][normalization_version]['supplementations'][supplementation_version]['supplementation_status']
        else:
            return self.NOT_STARTED

    def has_supplemental_data(self, parsing_version: str, normalization_version: str, supplementation_version: str):
        try:
            supplementations = self.metadata['parsings'][parsing_version]['normalizations'][normalization_version]['supplementations']
            supplemental_edge_count = supplementations[supplementation_version]['supplementation_info']['supplementation_normalization_info']['final_normalized_edges']
            if supplemental_edge_count > 0:
                return True
            else:
                return False
        except KeyError:
            return False

    def generate_release_metadata(self,
                                  parsing_version: str,
                                  normalization_version: str,
                                  supplementation_version: str,
                                  source_meta_information: dict):
        if "releases" not in self.metadata:
            self.metadata["releases"] = {}
        release_version = get_source_release_version(self.source_id,
                                                     self.source_version,
                                                     parsing_version,
                                                     normalization_version,
                                                     supplementation_version)
        if release_version not in self.metadata["releases"]:
            self.metadata["releases"][release_version] = {
                "source_version": self.source_version,
                "parsing_version": parsing_version,
                "normalization_version": normalization_version,
                "supplementation_version": supplementation_version
            }
        self.metadata["releases"][release_version].update(source_meta_information)
        self.save_metadata()
        return release_version

    def get_release_info(self, release_version: str):
        if 'releases' in self.metadata and release_version in self.metadata['releases']:
            return self.metadata['releases'][release_version]
        return None


def get_source_release_version(source_id,
                               source_version,
                               parsing_version,
                               normalization_version,
                               supplementation_version):
    release_string = "_".join([source_id,
                              source_version,
                              parsing_version,
                              normalization_version,
                              supplementation_version])
    return xxh64_hexdigest(release_string)
