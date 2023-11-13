import os
import tarfile

from io import TextIOWrapper
from Common.utils import GetData
from Common.loader_interface import SourceDataLoader
from Common.node_types import DESCRIPTION
from parsers.UberGraph.src.ubergraph import UberGraphTools


class UGLoader(SourceDataLoader):

    source_id = 'Ubergraph'
    provenance_id = 'infores:automat-ubergraph-nonredundant'
    description = "Ubergraph is an open-source graph database containing integrated ontologies, including GO, CHEBI, HPO, and Uberon’s anatomical ontology."
    source_data_url = "https://github.com/INCATools/ubergraph#downloads"
    license = "https://raw.githubusercontent.com/INCATools/ubergraph/master/LICENSE.txt"
    attribution = "https://github.com/INCATools/ubergraph"
    parsing_version: str = '1.4'

    def __init__(self, test_mode: bool = False, source_data_dir: str = None):
        """
        :param test_mode - sets the run into test mode
        :param source_data_dir - the specific storage directory to save files in
        """
        super().__init__(test_mode=test_mode, source_data_dir=source_data_dir)

        # this is the name of the archive file the source files will come from
        self.base_url = 'https://ubergraph.apps.renci.org'
        self.data_file = 'nonredundant-graph-table.tgz'
        self.data_url: str = f'{self.base_url}/downloads/current/'
        self.only_subclass_edges = False
        self.subclass_predicate = 'rdfs:subClassOf'

    def get_latest_source_version(self):
        """
        gets the version of the data

        this is a terrible way to grab the latest version but it works until we get Jim to make it easier
        :return:
        """

        latest_source_version = UberGraphTools.get_latest_source_version(ubergraph_url=self.base_url)
        return latest_source_version

    def get_data(self):
        archive_url = f'{self.data_url}{self.data_file}'
        gd = GetData(self.logger.level)
        gd.pull_via_http(archive_url,
                         self.data_path)
        return True

    def parse_data(self):
        """
        Parses the data file for graph nodes/edges
        """

        # init the record counters
        record_counter: int = 0
        skipped_record_counter: int = 0

        ubergraph_archive_path = os.path.join(self.data_path, self.data_file)
        ubergraph_graph_path = self.data_file.split('.tgz')[0]
        ubergraph_tools = UberGraphTools(ubergraph_url=self.base_url,
                                         ubergraph_archive_path=ubergraph_archive_path,
                                         graph_base_path=ubergraph_graph_path,
                                         logger=self.logger)

        with tarfile.open(ubergraph_archive_path, 'r') as tar_files:

            self.logger.info(f'Parsing Ubergraph..')
            with tar_files.extractfile(f'{ubergraph_graph_path}/edges.tsv') as edges_file:
                for line in TextIOWrapper(edges_file):
                    record_counter += 1
                    subject_id, predicate_id, object_id = tuple(line.rstrip().split('\t'))
                    subject_curie = ubergraph_tools.get_curie_for_node_id(subject_id)
                    if not subject_curie:
                        skipped_record_counter += 1
                        continue
                    object_curie = ubergraph_tools.get_curie_for_node_id(object_id)
                    if not object_curie:
                        skipped_record_counter += 1
                        continue
                    predicate_curie = ubergraph_tools.get_curie_for_edge_id(predicate_id)
                    if (not predicate_curie) or \
                            (self.only_subclass_edges and predicate_curie != self.subclass_predicate):
                        skipped_record_counter += 1
                        continue

                    subject_description = ubergraph_tools.node_descriptions.get(subject_curie, None)
                    subject_properties = {DESCRIPTION: subject_description} if subject_description else {}
                    self.output_file_writer.write_node(node_id=subject_curie, node_properties=subject_properties)

                    object_description = ubergraph_tools.node_descriptions.get(object_curie, None)
                    object_properties = {DESCRIPTION: object_description} if object_description else {}
                    self.output_file_writer.write_node(node_id=object_curie, node_properties=object_properties)

                    self.output_file_writer.write_edge(subject_id=subject_curie,
                                                       object_id=object_curie,
                                                       predicate=predicate_curie,
                                                       primary_knowledge_source=self.provenance_id)
                    if self.test_mode and record_counter == 10_000:
                        break

        # load up the metadata
        load_metadata: dict = {
            'num_source_lines': record_counter,
            'unusable_source_lines': skipped_record_counter
        }

        # return the split file names so they can be removed if desired
        return load_metadata


class UGRedundantLoader(UGLoader):

    source_id: str = 'UbergraphRedundant'
    provenance_id: str = 'infores:automat-ubergraph'
    description = "The redundant version of Ubergraph contains the complete inference closure for all subclass and existential relations, including transitive, reflexive subclass relations."
    source_data_url = "https://github.com/INCATools/ubergraph"
    license = "https://raw.githubusercontent.com/INCATools/ubergraph/master/LICENSE.txt"
    attribution = "https://github.com/INCATools/ubergraph"
    parsing_version: str = '1.0'

    def __init__(self, test_mode: bool = False, source_data_dir: str = None):
        """
        :param test_mode - sets the run into test mode
        :param source_data_dir - the specific storage directory to save files in
        """
        super().__init__(test_mode=test_mode, source_data_dir=source_data_dir)

        # this is the name of the archive file the source files will come from
        self.base_url = 'https://ubergraph.apps.renci.org'
        self.data_file = 'redundant-graph-table.tgz'
        self.data_url: str = f'{self.base_url}/downloads/current/'
        self.only_subclass_edges = False

    def get_latest_source_version(self):
        """
        gets the version of the data

        this is a terrible way to grab the latest version but it works until we get Jim to make it easier
        :return:
        """

        latest_source_version = UberGraphTools.get_latest_source_version(ubergraph_url=self.base_url)
        return latest_source_version

    def get_data(self):
        archive_url = f'{self.data_url}{self.data_file}'
        gd = GetData(self.logger.level)
        gd.pull_via_http(archive_url,
                         self.data_path)
        return True

    def parse_data(self):
        """
        Parses the data file for graph nodes/edges
        """
        DISEASE_PREFIXES = {   
            'OMIM:',
            'DOID:',
            'UMLS:',
            'NCIT:',
            'EFO:',
            'MONDO:',   
            'OMIM.PS:',
            'orphanet:',
            'MESH:',
            'MEDDRA:',
            'SNOMEDCT:',
            'medgen:',
            'ICD10:',
            'ICD9:',
            'KEGG.DISEASE:',
            'HP:',
            'MP:',
            'PHARMGKB.DISEASE:',
        }

        BIOPROCESS_PREFIXES = {
            'GO:',
            'REACT:',
            'metacyc.reaction:',
            'KEGG.MODULE:',
            'KEGG:',
        }

        GROSSANATOMICALSTRUCTURE_PREFIXES = { 
            'UBERON:',
            'UMLS:',
            'MESH:',
            'NCIT:',
            'PO:',
            'FAO:',
        }

        CELL_PREFIXES = {
            'CL:',
            'PO:',
            'UMLS:',
            'NCIT:',
            'MESH:',
            'UBERON:',
            'SNOMEDCT:',
            'MESH:',
        }

        PATHWAY_PREFIXES = {  
            'GO:',
            'REACT:',
            'KEGG:',
            'SMPDB:',
            'MSigDB:',
            'PHARMGKB.PATHWAYS:',
            'WIKIPATHWAYS:',
            'FB:',
            'PANTHER.PATHWAY:',
            'KEGG.PATHWAY:',
            'ncats.bioplanet:',
        }
        NON_DISEASE_PREFIXES = BIOPROCESS_PREFIXES.union(GROSSANATOMICALSTRUCTURE_PREFIXES).union(CELL_PREFIXES).union(PATHWAY_PREFIXES)
        ALL_PREFIXES = BIOPROCESS_PREFIXES.union(GROSSANATOMICALSTRUCTURE_PREFIXES).union(CELL_PREFIXES).union(PATHWAY_PREFIXES).union(DISEASE_PREFIXES)

        # DESIRED_PREDICATES = {
        #     'biolink:related_to',
        #     'biolink:causes',
        #     'biolink:has_participant',
        #     'biolink:disrupts',
        #     'biolink:affects',
        #     'biolink:associated_with',
        # }

        # init the record counters
        record_counter: int = 0
        parsed_record_counter: int = 0
        skipped_record_counter: int = 0

        ubergraph_archive_path = os.path.join(self.data_path, self.data_file)
        ubergraph_graph_path = self.data_file.split('.tgz')[0]
        ubergraph_tools = UberGraphTools(ubergraph_url=self.base_url,
                                         ubergraph_archive_path=ubergraph_archive_path,
                                         graph_base_path=ubergraph_graph_path,
                                         logger=self.logger)

        with tarfile.open(ubergraph_archive_path, 'r') as tar_files:

            self.logger.info(f'Parsing UbergraphRedundant..')
            with tar_files.extractfile(f'{ubergraph_graph_path}/edges.tsv') as edges_file:
                for line in TextIOWrapper(edges_file):
                    record_counter += 1
                    subject_id, predicate_id, object_id = tuple(line.rstrip().split('\t'))
                    subject_curie = ubergraph_tools.get_curie_for_node_id(subject_id)
                    if not subject_curie:
                        skipped_record_counter += 1
                        continue
                    elif not any(prefix in subject_curie for prefix in ALL_PREFIXES):
                        skipped_record_counter += 1
                        continue
                    object_curie = ubergraph_tools.get_curie_for_node_id(object_id)
                    if not object_curie:
                        skipped_record_counter += 1
                        continue
                    elif not any(prefix in object_curie for prefix in ALL_PREFIXES):
                        skipped_record_counter += 1
                        continue
                    if not (any(prefix in subject_curie for prefix in DISEASE_PREFIXES) & \
                        any(prefix in object_curie for prefix in NON_DISEASE_PREFIXES)) or \
                        (any(prefix in subject_curie for prefix in NON_DISEASE_PREFIXES) & \
                        any(prefix in object_curie for prefix in DISEASE_PREFIXES)):
                        skipped_record_counter += 1
                        continue
                    predicate_curie = ubergraph_tools.get_curie_for_edge_id(predicate_id)
                    if (not predicate_curie) or \
                            (self.only_subclass_edges and predicate_curie != self.subclass_predicate):
                        skipped_record_counter += 1
                        continue
                    parsed_record_counter +=1
                    if parsed_record_counter % 100000 == 0:
                        self.logger.info(f'Parsed {parsed_record_counter} edges so far..')
                    subject_description = ubergraph_tools.node_descriptions.get(subject_curie, None)
                    subject_properties = {'description': subject_description} if subject_description else {}
                    self.output_file_writer.write_node(node_id=subject_curie, node_properties=subject_properties)

                    object_description = ubergraph_tools.node_descriptions.get(object_curie, None)
                    object_properties = {'description': object_description} if object_description else {}
                    self.output_file_writer.write_node(node_id=object_curie, node_properties=object_properties)

                    self.output_file_writer.write_edge(subject_id=subject_curie,
                                                       object_id=object_curie,
                                                       predicate=predicate_curie,
                                                       primary_knowledge_source=self.provenance_id)
                    if self.test_mode and record_counter == 10_000:
                        break

        # load up the metadata
        load_metadata: dict = {
            'num_source_lines': record_counter,
            'unusable_source_lines': skipped_record_counter
        }

        # return the split file names so they can be removed if desired
        return load_metadata


class OHLoader(UGLoader):

    source_id: str = 'OntologicalHierarchy'
    provenance_id: str = 'infores:sri-ontology'
    description = "Subclass relationships from the redundant version of Ubergraph."
    source_data_url = "https://github.com/INCATools/ubergraph"
    license = "https://raw.githubusercontent.com/INCATools/ubergraph/master/LICENSE.txt"
    attribution = "https://github.com/INCATools/ubergraph"
    parsing_version: str = '1.4'

    def __init__(self, test_mode: bool = False, source_data_dir: str = None):
        """
        :param test_mode - sets the run into test mode
        :param source_data_dir - the specific storage directory to save files in
        """
        super().__init__(test_mode=test_mode, source_data_dir=source_data_dir)

        # this is the name of the archive file the source files will come from
        self.base_url = 'https://ubergraph.apps.renci.org'
        self.data_file = 'redundant-graph-table.tgz'
        self.data_url: str = f'{self.base_url}/downloads/current/'
        self.only_subclass_edges = True



