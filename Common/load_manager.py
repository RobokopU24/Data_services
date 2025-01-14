import os
import argparse
import datetime
import time
from collections import defaultdict

from Common.data_sources import SourceDataLoaderClassFactory, RESOURCE_HOGS, get_available_data_sources
from Common.exceptions import DataVersionError
from Common.utils import LoggingUtil, GetDataPullError
from Common.kgx_file_normalizer import KGXFileNormalizer
from Common.normalization import NormalizationScheme, NodeNormalizer, EdgeNormalizer, NormalizationFailedError
from Common.metadata import SourceMetadata
from Common.loader_interface import SourceDataBrokenError, SourceDataFailedError
from Common.supplementation import SequenceVariantSupplementation, SupplementationFailedError


SOURCE_DATA_LOADER_CLASSES = SourceDataLoaderClassFactory()


class SourceDataManager:

    def __init__(self,
                 test_mode: bool = False,
                 fresh_start_mode: bool = False):

        self.logger = LoggingUtil.init_logging("ORION.Common.SourceDataManager",
                                               line_format='medium',
                                               log_file_path=os.environ['ORION_LOGS'])

        self.test_mode = test_mode
        if test_mode:
            self.logger.info(f'SourceDataManager running in test mode... test data sets will be used when possible.')

        self.fresh_start_mode = fresh_start_mode
        if fresh_start_mode:
            self.logger.info(f'SourceDataManager running in fresh start mode... previous state and files ignored.')

        # locate and verify the main storage directory
        self.storage_dir = self.init_storage_dir()

        # dict of source_id -> latest source version (to prevent double lookups)
        self.latest_source_version_lookup = {}
        self.latest_parsing_version_lookup = {}
        # placeholders for lazy loading
        self.latest_node_normalization_version = None
        self.latest_edge_normalization_version = None
        self.latest_supplementation_version = None

        # nested dict of source_id -> source_version -> SourceMetadata
        self.source_metadata = defaultdict(dict)

    def run_pipeline(self,
                     source_id: str,
                     source_version: str='latest',
                     parsing_version: str='latest',
                     normalization_scheme: NormalizationScheme = None,
                     supplementation_version: str='latest'):

        self.logger.info(f"Running pipeline on {source_id}...")
        self.init_source_output_dir(source_id)

        if source_version == 'latest':
            source_version = self.get_latest_source_version(source_id)
        if not self.run_fetch_stage(source_id, source_version):
            self.logger.warning(f"Pipeline for {source_id} aborted during fetch stage.")
            return False

        if parsing_version == 'latest':
            parsing_version = self.get_latest_parsing_version(source_id)
        if not self.run_parsing_stage(source_id, source_version, parsing_version=parsing_version):
            self.logger.warning(f"Pipeline for {source_id} aborted during parsing stage.")
            return False

        if not normalization_scheme:
            self.logger.debug(f"No normalization scheme provided, using defaults/latest...")
            normalization_scheme = NormalizationScheme()
        if normalization_scheme.node_normalization_version == 'latest':
            normalization_scheme.node_normalization_version = self.get_latest_node_normalization_version()
        if normalization_scheme.edge_normalization_version == 'latest':
            normalization_scheme.edge_normalization_version = self.get_latest_edge_normalization_version()

        if not self.run_normalization_stage(source_id,
                                            source_version,
                                            parsing_version=parsing_version,
                                            normalization_scheme=normalization_scheme):
            self.logger.warning(f"Pipeline for {source_id} aborted during normalization stage.")
            return False

        if supplementation_version == 'latest':
            supplementation_version = SequenceVariantSupplementation.SUPPLEMENTATION_VERSION
        if not self.run_supplementation_stage(source_id,
                                              source_version,
                                              parsing_version=parsing_version,
                                              supplementation_version=supplementation_version,
                                              normalization_scheme=normalization_scheme):
            self.logger.warning(f"Pipeline for {source_id} supplementation stage not successful.")
            return False

        release_version = self.run_qc_and_metadata_stage(source_id,
                                                         source_version,
                                                         parsing_version=parsing_version,
                                                         normalization_scheme=normalization_scheme,
                                                         supplementation_version=supplementation_version)
        if release_version is None:
            self.logger.warning(f"Pipeline for {source_id} failed quality control...")
            return False
        else:
            return release_version

    def run_fetch_stage(self, source_id: str, source_version: str):
        if not source_version:
            self.logger.error(f"Error running pipeline for {source_id} - could not determine latest version.")
            return False

        fetch_status = self.get_source_metadata(source_id, source_version).get_fetch_status()
        if fetch_status == SourceMetadata.STABLE:
            return True
        elif fetch_status == SourceMetadata.IN_PROGRESS:
            self.logger.info(f"Fetch stage for {source_id} is already in progress.")
            return False
        elif fetch_status == SourceMetadata.BROKEN or fetch_status == SourceMetadata.FAILED:
            # TODO consider retry logic here
            self.logger.info(f"Fetch stage for {source_id} previously: {fetch_status}")
            return False
        else:
            self.logger.info(f"Fetching source data for {source_id} (version: {source_version})...")
            return self.fetch_source(source_id, source_version=source_version)

    def get_latest_source_version(self, source_id: str, retries: int = 0):
        if source_id in self.latest_source_version_lookup:
            return self.latest_source_version_lookup[source_id]

        loader = SOURCE_DATA_LOADER_CLASSES[source_id](test_mode=self.test_mode)
        self.logger.info(f"Retrieving latest source version for {source_id}...")
        try:
            latest_source_version = loader.get_latest_source_version()
            self.logger.info(f"Found latest source version for {source_id}: {latest_source_version}")
            self.latest_source_version_lookup[source_id] = latest_source_version
            return latest_source_version
        except GetDataPullError as failed_error:
            error_message = f"Error while checking for latest source version for {source_id}: " \
                            f"{failed_error.error_message}"
            self.logger.error(error_message)
            if retries < 2:
                time.sleep(3)
                return self.get_latest_source_version(source_id, retries=retries+1)
            else:
                raise DataVersionError(error_message=error_message)
        except Exception as e:
            error_message = f"Error while checking for latest source version for {source_id}: {repr(e)}-{str(e)}"
            self.logger.error(error_message)
            raise DataVersionError(error_message=error_message)

    def fetch_source(self, source_id: str, source_version: str='latest', retries: int=0):

        self.logger.debug(f'Fetching source {source_id}...')
        source_version_path = self.get_source_version_path(source_id, source_version)
        os.makedirs(source_version_path, exist_ok=True)
        source_metadata = self.get_source_metadata(source_id, source_version)
        source_metadata.set_fetch_status(SourceMetadata.IN_PROGRESS)
        try:
            loader = SOURCE_DATA_LOADER_CLASSES[source_id](test_mode=self.test_mode,
                                                           source_data_dir=source_version_path)
            if loader.needs_data_download():
                if source_version != self.get_latest_source_version(source_id):
                    unsupported_error_message = f"Fetching source data {source_id} (version: {source_version}) failed - " \
                                                f"fetching old source versions not supported."
                    self.logger.error(unsupported_error_message)
                    source_metadata.set_fetch_error(unsupported_error_message)
                    source_metadata.set_fetch_status(SourceMetadata.FAILED)
                    return False

                self.logger.info(f'Retrieving source data for {source_id} (version: {source_version})..')
                loader.get_data()
            else:
                self.logger.info(f'Source data was already retrieved for {source_id}..')
            source_metadata.set_fetch_status(SourceMetadata.STABLE)
            return True

        except GetDataPullError as failed_error:
            self.logger.info(
                f"Error while fetching source data for {source_id} (version: {source_version}): "
                f"{failed_error.error_message}")
            if retries < 2:
                self.logger.error(f"Retrying fetching for {source_id}.. (retry {retries + 1})")
                self.fetch_source(source_id=source_id, source_version=source_version, retries=retries+1)
            else:
                source_metadata.set_fetch_error(failed_error.error_message)
                source_metadata.set_fetch_status(SourceMetadata.FAILED)
                return False
        except Exception as e:
            self.logger.info(
                f"Error while fetching source data for {source_id} (version: {source_version}): "
                f"{repr(e)}-{str(e)}")
            source_metadata.set_fetch_error(f"{repr(e)}-{str(e)}")
            source_metadata.set_fetch_status(SourceMetadata.FAILED)
            return False

    def run_parsing_stage(self, source_id: str, source_version: str, parsing_version: str):
        source_metadata = self.get_source_metadata(source_id, source_version)
        parsing_status = source_metadata.get_parsing_status(parsing_version)
        if parsing_status == SourceMetadata.STABLE:
            return True
        elif parsing_status == SourceMetadata.IN_PROGRESS:
            self.logger.info(f"Parsing stage for {source_id} is already in progress.")
            return False
        elif parsing_status == SourceMetadata.BROKEN:
            self.logger.info(f"Parsing stage for {source_id} previously: {parsing_status}")
            return False
        else:
            # if parsing_status == SourceMetadata.FAILED:
            # TODO consider retry logic here - should we only try a few times? ask user?
            self.logger.info(f"Parsing source {source_id} (source_version: {source_version}, "
                             f"parsing_version: {parsing_version})...")
            return self.parse_source(source_id, source_version, parsing_version)

    def parse_source(self, source_id: str, source_version: str, parsing_version: str):

        # we currently don't support any parser version but the latest, just bail
        if parsing_version != self.get_latest_parsing_version(source_id):
            self.logger.error(f'Parser version {parsing_version} unavailable for {source_id}.')
            return False

        self.logger.info(f'Parsing source {source_id}...')
        current_time = datetime.datetime.now().strftime('%m-%d-%y %H:%M:%S')
        source_metadata = self.get_source_metadata(source_id, source_version)
        source_metadata.update_parsing_metadata(parsing_version,
                                                parsing_source_version=source_version,
                                                parsing_status=SourceMetadata.IN_PROGRESS)
        try:
            # init the parsing output directory
            source_data_dir = self.get_source_version_path(source_id, source_version)
            versioned_parsing_dir = self.get_versioned_parsing_directory(source_id, source_version, parsing_version)
            os.makedirs(versioned_parsing_dir, exist_ok=True)
            # create an instance of the appropriate loader using the source_data_loader_classes lookup map
            source_data_loader = SOURCE_DATA_LOADER_CLASSES[source_id](test_mode=self.test_mode,
                                                                       source_data_dir=source_data_dir)

            # call the loader - retrieve/parse data and write to a kgx file
            nodes_output_file_path = self.get_source_node_file_path(source_id, source_version, parsing_version)
            edges_output_file_path = self.get_source_edge_file_path(source_id, source_version, parsing_version)
            parsing_info = source_data_loader.load(nodes_output_file_path, edges_output_file_path)

            # update the associated metadata
            has_sequence_variants = source_data_loader.has_sequence_variants
            source_metadata.update_parsing_metadata(parsing_version,
                                                    parsing_source_version=source_version,
                                                    parsing_status=SourceMetadata.STABLE,
                                                    parsing_info=parsing_info,
                                                    parsing_time=current_time,
                                                    has_sequence_variants=has_sequence_variants)
            return True

        except SourceDataBrokenError as broken_error:
            self.logger.error(f"SourceDataBrokenError while parsing {source_id}: {broken_error.error_message}")
            source_metadata.update_parsing_metadata(parsing_version,
                                                    parsing_status=SourceMetadata.BROKEN,
                                                    parsing_error=broken_error.error_message,
                                                    parsing_time=current_time)
            return False
        except SourceDataFailedError as failed_error:
            self.logger.error(f"SourceDataFailedError while parsing {source_id}: {failed_error.error_message}")
            source_metadata.update_parsing_metadata(parsing_version,
                                                    parsing_status=SourceMetadata.FAILED,
                                                    parsing_error=failed_error.error_message,
                                                    parsing_time=current_time)
            return False
        except Exception as e:
            source_metadata.update_parsing_metadata(parsing_version,
                                                    parsing_status=SourceMetadata.FAILED,
                                                    parsing_error=f'{repr(e)}-{str(e)}',
                                                    parsing_time=current_time)
            raise e

    def get_latest_parsing_version(self, source_id: str):
        if source_id in self.latest_parsing_version_lookup:
            return self.latest_parsing_version_lookup[source_id]

        parsing_version = SOURCE_DATA_LOADER_CLASSES[source_id].parsing_version
        self.latest_parsing_version_lookup[source_id] = parsing_version
        return parsing_version

    def run_normalization_stage(self,
                                source_id: str,
                                source_version: str,
                                parsing_version: str,
                                normalization_scheme: NormalizationScheme):

        composite_normalization_version = normalization_scheme.get_composite_normalization_version()
        source_metadata = self.get_source_metadata(source_id, source_version)
        normalization_status = source_metadata.get_normalization_status(parsing_version,
                                                                        composite_normalization_version)
        if normalization_status == SourceMetadata.STABLE:
            return True
        elif normalization_status == SourceMetadata.IN_PROGRESS:
            self.logger.info(f"Normalization stage for {source_id} is already in progress.")
            return False
        elif normalization_status == SourceMetadata.BROKEN or normalization_status == SourceMetadata.FAILED:
            self.logger.info(f"Normalization stage for {source_id} previously: {normalization_status}")
            # TODO consider retry logic here
            return False
        else:
            return self.normalize_source(source_id,
                                         source_version,
                                         parsing_version,
                                         normalization_scheme=normalization_scheme)

    def normalize_source(self,
                         source_id: str,
                         source_version: str,
                         parsing_version: str,
                         normalization_scheme: NormalizationScheme):
        self.logger.info(f"Normalizing {source_id}...")
        composite_normalization_version = normalization_scheme.get_composite_normalization_version()
        versioned_normalization_dir = self.get_versioned_normalization_directory(source_id,
                                                                                 source_version,
                                                                                 parsing_version,
                                                                                 composite_normalization_version)
        os.makedirs(versioned_normalization_dir, exist_ok=True)
        source_metadata = self.get_source_metadata(source_id, source_version)
        source_metadata.update_normalization_metadata(parsing_version,
                                                      composite_normalization_version,
                                                      normalization_scheme=normalization_scheme,
                                                      normalization_status=SourceMetadata.IN_PROGRESS)
        try:
            current_time = datetime.datetime.now().strftime('%m-%d-%y %H:%M:%S')
            nodes_source_file_path = self.get_source_node_file_path(source_id, source_version, parsing_version)
            nodes_norm_file_path = self.get_normalized_node_file_path(source_id, source_version, parsing_version, composite_normalization_version)
            node_norm_map_file_path = self.get_node_norm_map_file_path(source_id, source_version, parsing_version, composite_normalization_version)
            node_norm_failures_file_path = self.get_node_norm_failures_file_path(source_id, source_version, parsing_version, composite_normalization_version)
            edges_source_file_path = self.get_source_edge_file_path(source_id, source_version, parsing_version)
            edges_norm_file_path = self.get_normalized_edge_file_path(source_id, source_version, parsing_version, composite_normalization_version)
            edge_norm_predicate_map_file_path = self.get_edge_norm_predicate_map_file_path(source_id, source_version, parsing_version, composite_normalization_version)
            has_sequence_variants = source_metadata.has_sequence_variants(parsing_version)
            default_provenance = SOURCE_DATA_LOADER_CLASSES[source_id].provenance_id
            preserve_unconnected_nodes = SOURCE_DATA_LOADER_CLASSES[source_id].preserve_unconnected_nodes
            process_in_memory = False if source_id in RESOURCE_HOGS else True
            file_normalizer = KGXFileNormalizer(nodes_source_file_path,
                                                nodes_norm_file_path,
                                                node_norm_map_file_path,
                                                node_norm_failures_file_path,
                                                edges_source_file_path,
                                                edges_norm_file_path,
                                                edge_norm_predicate_map_file_path,
                                                has_sequence_variants=has_sequence_variants,
                                                normalization_scheme=normalization_scheme,
                                                default_provenance=default_provenance,
                                                process_in_memory=process_in_memory,
                                                preserve_unconnected_nodes=preserve_unconnected_nodes)
            normalization_info = file_normalizer.normalize_kgx_files()

            # update the associated metadata
            source_metadata.update_normalization_metadata(parsing_version,
                                                          composite_normalization_version,
                                                          normalization_time=current_time,
                                                          normalization_status=SourceMetadata.STABLE,
                                                          normalization_info=normalization_info)
            return True
        except NormalizationFailedError as failed_error:
            error_message = f"{source_id} NormalizationFailedError: {failed_error.error_message}"
            if failed_error.actual_error:
                error_message += f" - {failed_error.actual_error}"
            self.logger.error(error_message)
            source_metadata.update_normalization_metadata(parsing_version,
                                                          composite_normalization_version,
                                                          normalization_status=SourceMetadata.FAILED,
                                                          normalization_error=error_message,
                                                          normalization_time=current_time)
            return False
        except Exception as e:
            self.logger.error(f"Error while normalizing {source_id}: {repr(e)}")
            source_metadata.update_normalization_metadata(parsing_version,
                                                          composite_normalization_version,
                                                          normalization_status=SourceMetadata.FAILED,
                                                          normalization_error=repr(e),
                                                          normalization_time=current_time)
            return False

    def get_latest_node_normalization_version(self):
        if self.latest_node_normalization_version is not None:
            return self.latest_node_normalization_version
        node_normalizer = NodeNormalizer()
        node_norm_version = node_normalizer.get_current_node_norm_version()
        self.latest_node_normalization_version = node_norm_version
        return node_norm_version

    def get_latest_edge_normalization_version(self):
        if self.latest_edge_normalization_version is not None:
            return self.latest_edge_normalization_version
        edge_normalizer = EdgeNormalizer()
        edge_norm_version = edge_normalizer.get_current_edge_norm_version()
        self.latest_edge_normalization_version = edge_norm_version
        return edge_norm_version

    def run_supplementation_stage(self,
                                  source_id: str,
                                  source_version: str,
                                  parsing_version: str,
                                  supplementation_version: str,
                                  normalization_scheme: NormalizationScheme):

        if supplementation_version != SequenceVariantSupplementation.SUPPLEMENTATION_VERSION:
            self.logger.warning(f"Supplementation version {supplementation_version} is not supported.")
            return False

        composite_normalization_version = normalization_scheme.get_composite_normalization_version()
        source_metadata = self.get_source_metadata(source_id, source_version)
        supplementation_status = source_metadata.get_supplementation_status(parsing_version,
                                                                            composite_normalization_version,
                                                                            supplementation_version)
        if supplementation_status == SourceMetadata.STABLE:
            return True
        elif supplementation_status == SourceMetadata.FAILED or supplementation_status == SourceMetadata.BROKEN:
            self.logger.info(f"Supplementation stage for {source_id} previously failed or was broken.")
            # TODO consider retry logic here
            return False
        elif supplementation_status == SourceMetadata.IN_PROGRESS:
            self.logger.info(f"Supplementation stage for {source_id} is already in progress.")
            return False
        else:
            return self.supplement_source(source_id,
                                          source_version,
                                          parsing_version,
                                          supplementation_version,
                                          normalization_scheme=normalization_scheme)

    def supplement_source(self,
                          source_id: str,
                          source_version: str,
                          parsing_version: str,
                          supplementation_version: str,
                          normalization_scheme: NormalizationScheme):
        self.logger.info(f"Supplementing source {source_id}...")
        composite_normalization_version = normalization_scheme.get_composite_normalization_version()
        current_time = datetime.datetime.now().strftime('%m-%d-%y %H:%M:%S')
        versioned_supplementation_dir = self.get_versioned_supplementation_directory(source_id,
                                                                                     source_version,
                                                                                     parsing_version,
                                                                                     composite_normalization_version,
                                                                                     supplementation_version)
        try:

            source_metadata = self.get_source_metadata(source_id, source_version)
            source_metadata.update_supplementation_metadata(parsing_version,
                                                            composite_normalization_version,
                                                            supplementation_version,
                                                            supplementation_status=SourceMetadata.IN_PROGRESS)
            if source_metadata.has_sequence_variants(parsing_version):

                os.makedirs(versioned_supplementation_dir, exist_ok=True)

                nodes_file_path = self.get_normalized_node_file_path(source_id, source_version, parsing_version, composite_normalization_version)
                supplemental_node_file_path = self.get_supplemental_node_file_path(source_id, source_version, parsing_version, composite_normalization_version, supplementation_version)
                normalized_supp_node_file_path = self.get_normalized_supp_node_file_path(source_id, source_version, parsing_version, composite_normalization_version, supplementation_version)
                supp_node_norm_map_file_path = self.get_supp_node_norm_map_file_path(source_id, source_version, parsing_version, composite_normalization_version, supplementation_version)
                supp_node_norm_failures_file_path = self.get_supp_node_norm_failures_file_path(source_id, source_version, parsing_version, composite_normalization_version, supplementation_version)
                supplemental_edge_file_path = self.get_supplemental_edge_file_path(source_id, source_version, parsing_version, composite_normalization_version, supplementation_version)
                normalized_supp_edge_file_path = self.get_normalized_supplemental_edge_file_path(source_id, source_version, parsing_version, composite_normalization_version, supplementation_version)
                supp_edge_norm_predicate_map_file_path = self.get_supp_edge_norm_predicate_map_file_path(source_id, source_version, parsing_version, composite_normalization_version, supplementation_version)
                sv_supp = SequenceVariantSupplementation()
                supplementation_info = sv_supp.find_supplemental_data(nodes_file_path=nodes_file_path,
                                                                      supp_nodes_file_path=supplemental_node_file_path,
                                                                      supp_nodes_norm_file_path=normalized_supp_node_file_path,
                                                                      supp_node_norm_map_file_path=supp_node_norm_map_file_path,
                                                                      supp_node_norm_failures_file_path=supp_node_norm_failures_file_path,
                                                                      supp_edges_file_path=supplemental_edge_file_path,
                                                                      normalized_supp_edge_file_path=normalized_supp_edge_file_path,
                                                                      supp_edge_norm_predicate_map_file_path=supp_edge_norm_predicate_map_file_path,
                                                                      normalization_scheme=normalization_scheme)
            else:
                supplementation_info = None
            source_metadata.update_supplementation_metadata(parsing_version,
                                                            composite_normalization_version,
                                                            supplementation_version,
                                                            supplementation_status=SourceMetadata.STABLE,
                                                            supplementation_time=current_time,
                                                            supplementation_info=supplementation_info)
            return True
        except SupplementationFailedError as failed_error:
            error_message = f"{source_id} SupplementationFailedError: " \
                            f"{failed_error.error_message} - {failed_error.actual_error}"
            self.logger.error(error_message)
            source_metadata.update_supplementation_metadata(parsing_version,
                                                            composite_normalization_version,
                                                            supplementation_version,
                                                            supplementation_status=SourceMetadata.FAILED,
                                                            supplementation_error=error_message,
                                                            supplementation_time=current_time)
            return False

        except Exception as e:
            self.logger.error(f"{source_id} Error while supplementing: {repr(e)}")
            source_metadata.update_supplementation_metadata(parsing_version,
                                                            composite_normalization_version,
                                                            supplementation_version,
                                                            supplementation_status=SourceMetadata.FAILED,
                                                            supplementation_error=repr(e),
                                                            supplementation_time=current_time)
            return False

    def run_qc_and_metadata_stage(self,
                                  source_id: str,
                                  source_version: str,
                                  parsing_version: str,
                                  supplementation_version: str,
                                  normalization_scheme: NormalizationScheme):
        # source data QC should go here

        self.logger.info(f'Generating release for {source_id}')
        source_metadata = self.get_source_metadata(source_id, source_version)
        loader = SOURCE_DATA_LOADER_CLASSES[source_id](test_mode=self.test_mode)
        source_meta_information = loader.get_source_meta_information()
        normalization_version = normalization_scheme.get_composite_normalization_version()
        release_version = source_metadata.generate_release_metadata(parsing_version=parsing_version,
                                                                    supplementation_version=supplementation_version,
                                                                    normalization_version=normalization_version,
                                                                    source_meta_information=source_meta_information)
        return release_version

    def get_source_metadata(self, source_id: str, source_version):
        if source_id not in self.source_metadata or source_version not in self.source_metadata[source_id]:
            source_metadata = SourceMetadata(source_id,
                                             source_version,
                                             self.get_source_version_path(source_id, source_version))
            self.source_metadata[source_id][source_version] = source_metadata
        else:
            source_metadata = self.source_metadata[source_id][source_version]
        return source_metadata

    def get_versioned_parsing_directory(self, source_id: str, source_version: str, parsing_version: str):
        versioned_parsing_directory = f'parsed_{parsing_version}/'
        return os.path.join(self.get_source_version_path(source_id, source_version),
                            versioned_parsing_directory)

    def get_source_node_file_path(self, source_id: str, source_version: str, parsing_version: str):
        file_name = f'source_nodes.jsonl'
        return os.path.join(self.get_versioned_parsing_directory(source_id, source_version, parsing_version), file_name)

    def get_source_edge_file_path(self, source_id: str, source_version: str, parsing_version: str):
        file_name = f'source_edges.jsonl'
        return os.path.join(self.get_versioned_parsing_directory(source_id, source_version, parsing_version), file_name)

    def get_versioned_normalization_directory(self, source_id: str, source_version: str, parsing_version: str, normalization_version: str):
        versioned_norm_dir = f'normalized_{normalization_version}/'
        return os.path.join(self.get_versioned_parsing_directory(source_id, source_version, parsing_version), versioned_norm_dir)

    def get_normalized_node_file_path(self, source_id: str, source_version: str, parsing_version: str, normalization_version: str):
        return os.path.join(self.get_versioned_normalization_directory(source_id,
                                                                       source_version,
                                                                       parsing_version,
                                                                       normalization_version), f'normalized_nodes.jsonl')

    def get_node_norm_map_file_path(self, source_id: str, source_version: str, parsing_version: str, normalization_version: str):
        return os.path.join(self.get_versioned_normalization_directory(source_id,
                                                                       source_version,
                                                                       parsing_version,
                                                                       normalization_version), f'norm_node_map.json')

    def get_node_norm_failures_file_path(self, source_id: str, source_version: str, parsing_version: str, normalization_version: str):
        return os.path.join(self.get_versioned_normalization_directory(source_id,
                                                                       source_version,
                                                                       parsing_version,
                                                                       normalization_version), f'norm_node_failures.log')

    def get_normalized_edge_file_path(self, source_id: str, source_version: str, parsing_version: str, normalization_version: str):
        return os.path.join(self.get_versioned_normalization_directory(source_id,
                                                                       source_version,
                                                                       parsing_version,
                                                                       normalization_version), f'normalized_edges.jsonl')

    def get_edge_norm_predicate_map_file_path(self, source_id: str, source_version: str, parsing_version: str, normalization_version: str):
        return os.path.join(self.get_versioned_normalization_directory(source_id,
                                                                       source_version,
                                                                       parsing_version,
                                                                       normalization_version), f'norm_predicate_map.json')

    def get_versioned_supplementation_directory(self,
                                                source_id: str,
                                                source_version: str,
                                                parsing_version: str,
                                                normalization_version: str,
                                                supplementation_version: str):
        versioned_dir = f'supplemental_{supplementation_version}/'
        return os.path.join(self.get_versioned_normalization_directory(source_id,
                                                                       source_version,
                                                                       parsing_version,
                                                                       normalization_version), versioned_dir)

    def get_supplemental_node_file_path(self, source_id: str, source_version: str, parsing_version: str,
                                        normalization_version: str, supplementation_version: str):
        return os.path.join(self.get_versioned_supplementation_directory(source_id,
                                                                         source_version,
                                                                         parsing_version,
                                                                         normalization_version,
                                                                         supplementation_version), f'supp_nodes.jsonl')

    def get_normalized_supp_node_file_path(self, source_id: str, source_version: str, parsing_version: str,
                                           normalization_version: str, supplementation_version: str):
        return os.path.join(self.get_versioned_supplementation_directory(source_id,
                                                                         source_version,
                                                                         parsing_version,
                                                                         normalization_version,
                                                                         supplementation_version), f'supp_norm_nodes.jsonl')

    def get_supp_node_norm_map_file_path(self, source_id: str, source_version: str, parsing_version: str,
                                         normalization_version: str, supplementation_version: str):
        return os.path.join(self.get_versioned_supplementation_directory(source_id,
                                                                         source_version,
                                                                         parsing_version,
                                                                         normalization_version,
                                                                         supplementation_version), f'supp_norm_node_map.json')

    def get_supp_node_norm_failures_file_path(self, source_id: str, source_version: str, parsing_version: str,
                                              normalization_version: str, supplementation_version: str):
        return os.path.join(self.get_versioned_supplementation_directory(source_id,
                                                                         source_version,
                                                                         parsing_version,
                                                                         normalization_version,
                                                                         supplementation_version), f'supp_norm_nodes_failures.log')

    def get_supplemental_edge_file_path(self, source_id: str, source_version: str, parsing_version: str,
                                        normalization_version: str, supplementation_version: str):
        return os.path.join(self.get_versioned_supplementation_directory(source_id,
                                                                         source_version,
                                                                         parsing_version,
                                                                         normalization_version,
                                                                         supplementation_version), f'supp_edges.jsonl')

    def get_normalized_supplemental_edge_file_path(self, source_id: str, source_version: str, parsing_version: str,
                                                   normalization_version: str, supplementation_version: str):
        return os.path.join(self.get_versioned_supplementation_directory(source_id,
                                                                         source_version,
                                                                         parsing_version,
                                                                         normalization_version,
                                                                         supplementation_version), f'supp_norm_edges.jsonl')

    def get_supp_edge_norm_predicate_map_file_path(self, source_id: str, source_version: str, parsing_version: str,
                                                   normalization_version: str, supplementation_version: str):
        return os.path.join(self.get_versioned_supplementation_directory(source_id,
                                                                         source_version,
                                                                         parsing_version,
                                                                         normalization_version,
                                                                         supplementation_version), f'supp_norm_predicate_map.json')

    def get_final_file_paths(self, source_id: str, source_version: str, parsing_version: str,
                             normalization_version: str, supplementation_version: str):
        file_paths = list()
        file_paths.append(
            self.get_normalized_node_file_path(source_id, source_version, parsing_version, normalization_version))
        file_paths.append(
            self.get_normalized_edge_file_path(source_id, source_version, parsing_version, normalization_version))
        if self.get_source_metadata(source_id, source_version).has_supplemental_data(parsing_version,
                                                                                     normalization_version,
                                                                                     supplementation_version):
            file_paths.append(self.get_normalized_supp_node_file_path(source_id,
                                                                      source_version,
                                                                      parsing_version,
                                                                      normalization_version,
                                                                      supplementation_version))
            file_paths.append(
                self.get_normalized_supplemental_edge_file_path(source_id,
                                                                source_version,
                                                                parsing_version,
                                                                normalization_version,
                                                                supplementation_version))
        return file_paths

    def get_source_version_path(self, source_id: str, source_version: str):
        return os.path.join(self.storage_dir, source_id, source_version)

    def init_storage_dir(self):
        # use the storage directory specified by the environment variable ORION_STORAGE
        # check to make sure it's set and valid, otherwise fail
        if "ORION_STORAGE" not in os.environ:
            raise Exception(f'You must use the environment variable ORION_STORAGE '
                            f'to specify a storage directory.')
        if os.path.isdir(os.environ["ORION_STORAGE"]):
            return os.environ["ORION_STORAGE"]
        else:
            raise IOError(f'Storage directory not valid: {os.environ["ORION_STORAGE"]}')

    def init_source_output_dir(self, source_id: str):
        source_dir_path = os.path.join(self.storage_dir, source_id)
        os.makedirs(source_dir_path, exist_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Transform data sources into KGX files.")
    parser.add_argument('data_source',
                        nargs="+",
                        help=f'Select one or more data sources to process from the following: '
                             f'{", ".join(get_available_data_sources())}')
    parser.add_argument('-t', '--test_mode',
                        action='store_true',
                        help='Test mode will process a small sample version of the data.')
    parser.add_argument('-f', '--fresh_start_mode',
                        action='store_true',
                        help='Fresh start mode will ignore previous states and overwrite previous data.')
    parser.add_argument('-l', '--lenient_normalization',
                        action='store_true',
                        help='Lenient normalization mode will allow nodes that do not normalize to persist '
                             'in the finalized kgx files.')
    args = parser.parse_args()

    if 'ORION_TEST_MODE' in os.environ:
        test_mode_from_env = os.environ['ORION_TEST_MODE']
    else:
        test_mode_from_env = False

    loader_test_mode = args.test_mode or test_mode_from_env
    loader_strict_normalization = (not args.lenient_normalization)
    load_manager = SourceDataManager(test_mode=loader_test_mode,
                                     fresh_start_mode=args.fresh_start_mode)
    for data_source in args.data_source:
        if data_source not in get_available_data_sources():
            print(f'Data source {data_source} is not valid. '
                  f'These are the available data sources: {", ".join(get_available_data_sources())}')
        else:
            cmd_line_normalization_scheme = NormalizationScheme(strict=loader_strict_normalization)
            release_vers = load_manager.run_pipeline(data_source, normalization_scheme=cmd_line_normalization_scheme)
            if release_vers:
                print(f'Finished running data pipeline for {data_source} (release version {release_vers}).')
