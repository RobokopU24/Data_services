import enum
import os
import requests
import json

from Common.biolink_constants import *
from Common.extractor import Extractor
from Common.utils import GetData
from Common.loader_interface import SourceDataLoader
from Common.utils import GetDataPullError


# the data header columns the nodes files are:
class NODESDATACOLS(enum.IntEnum):
    ID = 0
    NAME = 1
    CATEGORY = 2


# the data header columns for the edges file are:
class EDGESDATACOLS(enum.IntEnum):
    ID = 0
    SUBJECT = 1
    PREDICATE = 2
    OBJECT = 3
    SUBJECT_NAME = 4
    OBJECT_NAME = 5
    CATEGORY = 6
    KNOWLEDGE_LEVEL = 7
    AGENT_TYPE = 8
    NCTID = 9
    PHASE = 10
    PRIMARY_PURPOSE = 11
    INTERVENTION_MODEL = 12
    TIME_PERSPECTIVE = 13
    OVERALL_STATUS = 14
    START_DATE = 15
    ENROLLMENT = 16
    ENROLLMENT_TYPE = 17
    AGE_RANGE = 18
    CHILD = 19
    ADULT = 20
    OLDER_ADULT = 21
    UNII = 22


class CTKPLoader(SourceDataLoader):
    source_id: str = "ClinicalTrialsKP"
    provenance_id: str = "infores:biothings-multiomics-clinicaltrials"
    description = "The Clinical Trials KP, created and maintained by the Multiomics Provider, provides information on Clinical Trials, ultimately derived from researcher submissions to clinicaltrials.gov, via the Aggregate Analysis of Clinical Trials (AACT) database). Information on select trials includes the NCT Identifier of the trial, interventions used, diseases/conditions relevant to the trial, adverse events, etc."
    source_data_url = "https://aact.ctti-clinicaltrials.org/"
    license = "https://github.com/ctti-clinicaltrials/aact/blob/dev/LICENSE"
    attribution = ""
    parsing_version = "1.0"

    def __init__(self, test_mode: bool = False, source_data_dir: str = None):
        """
        :param test_mode - sets the run into test mode
        :param source_data_dir - the specific storage directory to save files in
        """
        super().__init__(test_mode=test_mode, source_data_dir=source_data_dir)

        # Get latest source version from the manifest and use it to name node and edge files.
        self.latest_version = self.get_manifest()['version']
        self.node_file_name = f'clinical_trials_kg_nodes_v{self.latest_version}.tsv'
        self.edge_file_name = f'clinical_trials_kg_edges_v{self.latest_version}.tsv'
        self.data_url = "https://db.systemsbiology.net/gestalt/KG/"

        self.data_files = [
            self.node_file_name,
            self.edge_file_name
        ]

        self.aact_infores = "infores:aact"
        self.ctgov_infores = "infores:clinicaltrials"
        self.treats_predicate = "biolink:treats"
        self.source_record_url = "https://db.systemsbiology.net/gestalt/cgi-pub/KGinfo.pl?id="

    def get_latest_source_version(self) -> str:
        latest_version = self.get_manifest()['version']
        return latest_version

    @staticmethod
    def get_manifest():
        manifest_response = requests.get('https://raw.githubusercontent.com/multiomicsKP/clinical_trials_kp/refs/heads/main/manifest.json')
        if manifest_response.status_code == 200:
            manifest = manifest_response.json()
            return manifest
        else:
            manifest_response.raise_for_status()

    def get_data(self) -> int:
        """
        manifest = self.get_manifest()
        source_data_urls = manifest['dumper']['data_url']
        nodes_url = None
        edges_url = None
        for data_url in source_data_urls:
            if 'nodes' in data_url:
                nodes_url = data_url
            elif 'edges' in data_url:
                edges_url = data_url
        if not nodes_url and edges_url:
            raise GetDataPullError(f'Could not determine nodes and edges files in CTKP manifest data urls: {source_data_urls}')
        data_puller = GetData()
        data_puller.pull_via_http(nodes_url, self.data_path, saved_file_name=self.node_file_name)
        data_puller.pull_via_http(edges_url, self.data_path, saved_file_name=self.edge_file_name)
        """
        data_puller = GetData()
        for source in self.data_files:
            source_url = f"{self.data_url}{source}"
            data_puller.pull_via_http(source_url, self.data_path)
        return True

    def parse_data(self) -> dict:
        """
        Parses the data file for graph nodes/edges and writes them to the KGX files.

        :return: ret_val: record counts
        """

        extractor = Extractor(file_writer=self.output_file_writer)

        # get the nodes
        # it's not really necessary because normalization will overwrite the only information here (name and category)
        nodes_file: str = os.path.join(self.data_path, self.node_file_name)
        with open(nodes_file, 'r') as fp:
            extractor.csv_extract(fp,
                                  lambda line: line[NODESDATACOLS.ID.value],  # subject id
                                  lambda line: None,  # object id
                                  lambda line: None,  # predicate
                                  lambda line: {NAME: line[NODESDATACOLS.NAME.value],
                                                NODE_TYPES: line[NODESDATACOLS.CATEGORY.value]},  # subject props
                                  lambda line: {},  # object props
                                  lambda line: {},  # edgeprops
                                  comment_character=None,
                                  delim='\t',
                                  has_header_row=True)

        edges_file: str = os.path.join(self.data_path, self.edge_file_name)
        with open(edges_file, 'r') as fp:
            extractor.csv_extract(fp,
                                  lambda line: line[EDGESDATACOLS.SUBJECT.value],  # subject id
                                  lambda line: line[EDGESDATACOLS.OBJECT.value],  # object id
                                  lambda line: line[EDGESDATACOLS.PREDICATE.value],  # predicate
                                  lambda line: {},  # subject props
                                  lambda line: {},  # object props
                                  lambda line: self.get_edge_properties(line),  # edgeprops
                                  comment_character=None,
                                  delim='\t',
                                  has_header_row=True)

        return extractor.load_metadata

    def get_edge_properties(self, line):

        supporting_studies = []
        pred = str(line[EDGESDATACOLS.PREDICATE.value])
        nctids = str(line[EDGESDATACOLS.NCTID.value]).split(',')
        phases = str(line[EDGESDATACOLS.PHASE.value]).split(',')
        status = str(line[EDGESDATACOLS.OVERALL_STATUS.value]).split(',')
        enroll = str(line[EDGESDATACOLS.ENROLLMENT.value]).split(',')
        en_typ = str(line[EDGESDATACOLS.ENROLLMENT_TYPE.value]).split(',')
        max_phase = 0
        elevate_to_prediction = False
        for nctid, phase, stat, enrollment, enrollment_type in zip(nctids, phases, status, enroll, en_typ):
            if float(phase) > max_phase:
                max_phase = float(phase)
            try:
                enrollment = int(enrollment)
            except ValueError:
                enrollment = -1

            supporting_study_attributes = {
                "id": nctid,
                "tested_intervention": "unsure" if pred == "biolink:mentioned_in_trials_for" else "yes",
                "phase": phase,
                "status": stat,
                "study_size": enrollment
            }
            # convert to TRAPI format
            supporting_studies.append(
                {"attribute_type_id": HAS_SUPPORTING_STUDY_RESULT,
                 "value": nctid,
                 "attributes": [{"attribute_type_id": key,
                                "value": value} for key, value in supporting_study_attributes.items()]})

        # if pred == "biolink:in_clinical_trials_for" and max_phase >= 4:
        #        elevate_to_prediction = True

        if pred == self.treats_predicate:
            primary_knowledge_source = self.provenance_id
            aggregator_knowledge_sources = [self.aact_infores]
            supporting_data_source = self.ctgov_infores
        else:
            primary_knowledge_source = self.ctgov_infores
            aggregator_knowledge_sources = [self.aact_infores, self.provenance_id]
            supporting_data_source = None

        edge_attributes = {
            EDGE_ID: line[EDGESDATACOLS.ID.value],
            PRIMARY_KNOWLEDGE_SOURCE: primary_knowledge_source,
            AGGREGATOR_KNOWLEDGE_SOURCES: aggregator_knowledge_sources,
            KNOWLEDGE_LEVEL: line[EDGESDATACOLS.KNOWLEDGE_LEVEL.value],
            AGENT_TYPE: line[EDGESDATACOLS.AGENT_TYPE.value],
            MAX_RESEARCH_PHASE: str(float(max_phase)),
            "elevate_to_prediction": elevate_to_prediction,  # this isn't in biolink so not using a constant for now
            # note source_record_urls should be paired with specific knowledge sources but currently
            # there's no implementation for that, just pass it as a normal attribute for now
            "source_record_urls": [self.source_record_url + line[EDGESDATACOLS.ID.value]]
        }
        if supporting_data_source:
            edge_attributes[SUPPORTING_DATA_SOURCE] = supporting_data_source
        # to handle nested attributes, use the "attributes" property which supports TRAPI attributes as json strings
        if supporting_studies:
            edge_attributes["attributes"] = [json.dumps(study) for study in supporting_studies]
        return edge_attributes
