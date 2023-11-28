import time
import os
import json
import re
import requests
from collections import defaultdict

from Common.biolink_utils import BiolinkUtils
from Common.loader_interface import SourceDataLoader
from Common.kgxmodel import kgxnode, kgxedge
from Common.node_types import PRIMARY_KNOWLEDGE_SOURCE, PUBLICATIONS
from Common.utils import GetData, snakify
from Common.normalization import call_name_resolution
from Common.prefixes import PUBMED


LLM_SUBJECT_NAME = 'entity_1'
LLM_SUBJECT_TYPE = 'entity_1_type'
LLM_OBJECT_NAME = 'entity_2'
LLM_OBJECT_TYPE = 'entity_2_type'
LLM_RELATIONSHIP = 'relationship'
LLM_MAIN_FINDING = 'main_finding'


NODE_TYPE_MAPPINGS = {
    "Activity": "Activity",
    "AnatomicalStructure": "AnatomicalEntity",
    "Antibody": "ChemicalEntity",
    "Behavior": "Behavior",
    "BiologicalStructure": "AnatomicalEntity",
    "BiologicalPathway": "Pathway",
    "CellType": "Cell",
    "Chemical": "ChemicalEntity",
    "Chemicals": "ChemicalEntity",
    "Condition": "PhenotypicFeature",
    "Device": "Device",
    "Disease": "Disease",
    "DiseaseSymptom": "DiseaseOrPhenotypicFeature",
    "Drug": "Drug",
    "DrugClass": "Drug",
    "Gene": "Gene",
    "LifestyleFactor": "Behavior",
    "Organ": "AnatomicalEntity",
    "OrganSystem": "AnatomicalEntity",
    "OrganismHuman": "Cohort",
    "OrganismHumanEthnicGroup": "PopulationOfIndividualOrganisms",
    "OrganismPart": "AnatomicalEntity",
    "Organization": "Agent",
    "Phenotype": "PhenotypicFeature",
    "Procedure": "Procedure",
    "Protein": "Protein",
    "Proteins": "Protein",
    "StatisticalMethod": "Activity",
    "Symptom": "PhenotypicFeature",
    "Technique": "Procedure",
    "Therapy": "Procedure",
    "Treatment": "Procedure"
}


##############
# Class: LitCoin source loader
#
# Desc: Class that loads/parses the LitCoin data.
##############
class LitCoinLoader(SourceDataLoader):

    source_id: str = 'LitCoin'
    provenance_id: str = 'infores:robokop'  # TODO - change this to a LitCoin infores when it exists
    parsing_version: str = '1.2'

    def __init__(self, test_mode: bool = False, source_data_dir: str = None):
        """
        :param test_mode - sets the run into test mode
        :param source_data_dir - the specific storage directory to save files in
        """
        super().__init__(test_mode=test_mode, source_data_dir=source_data_dir)

        self.data_url = 'https://stars.renci.org/var/data_services/litcoin/'
        self.data_file = 'HEAL_2.7.23_gpt4.json'
        self.data_files = [self.data_file]
        # dicts of name to id lookups organized by node type (node_name_to_id_lookup[node_type] = dict of names -> id)
        self.node_name_to_id_lookup = defaultdict(dict)
        self.name_res_stats = []
        self.bl_utils = BiolinkUtils()

    def get_latest_source_version(self) -> str:
        latest_version = 'v1.0'
        return latest_version

    def get_data(self) -> bool:
        source_data_url = f'{self.data_url}{self.data_file}'
        data_puller = GetData()
        data_puller.pull_via_http(source_data_url, self.data_path)
        return True

    def parse_data(self) -> dict:
        """
        Parses the data file for graph nodes/edges

        :return: ret_val: load_metadata
        """

        # could use cached results for faster dev runs with something like this
        # with open(os.path.join(self.data_path, "litcoin_name_res_results.json"), "w") as name_res_results_file:
        #    self.node_name_to_id_lookup = json.load(name_res_results_file)

        records = 0
        skipped_records = 0
        litcoin_file_path: str = os.path.join(self.data_path, self.data_file)
        with open(litcoin_file_path) as litcoin_file:
            litcoin_json = json.load(litcoin_file)
            for litcoin_object in litcoin_json:
                pubmed_id = f'{PUBMED}:{litcoin_object["abstract_id"]}'
                llm_output = litcoin_object['output']
                for litcoin_edge in self.parse_llm_output(llm_output):
                    records += 1
                    # self.logger.info(f'processing edge {records}')
                    subject_resolution_results = self.process_llm_node(litcoin_edge[LLM_SUBJECT_NAME],
                                                                       litcoin_edge[LLM_SUBJECT_TYPE])
                    if not subject_resolution_results:
                        skipped_records += 1
                        continue
                    object_resolution_results = self.process_llm_node(litcoin_edge[LLM_OBJECT_NAME],
                                                                      litcoin_edge[LLM_OBJECT_TYPE])
                    if not object_resolution_results:
                        skipped_records += 1
                        continue
                    self.output_file_writer.write_node(node_id=subject_resolution_results['curie'],
                                                       node_name=subject_resolution_results['name'])
                    self.output_file_writer.write_node(node_id=object_resolution_results['curie'],
                                                       node_name=object_resolution_results['name'])

                    predicate = 'biolink:' + snakify(litcoin_edge[LLM_RELATIONSHIP])
                    edge_properties = {
                        PUBLICATIONS: [pubmed_id],
                        LLM_MAIN_FINDING: litcoin_edge[LLM_MAIN_FINDING]
                    }
                    self.output_file_writer.write_edge(subject_id=subject_resolution_results['curie'],
                                                       object_id=object_resolution_results['curie'],
                                                       predicate=predicate,
                                                       edge_properties=edge_properties)

        # write out name res results alongside the output
        with open(os.path.join(self.data_path, "..",
                               f"parsed_{self.parsing_version}",
                               "litcoin_name_res_results.json"), "w") as name_res_results_file:

            # include the biolink type used to call name res
            for node_type, node_name_to_results_dict in self.node_name_to_id_lookup.items():
                node_type_used_for_name_res = NODE_TYPE_MAPPINGS.get(self.convert_node_type_to_biolink_format(node_type),
                                                                     None)
                for results in node_name_to_results_dict.values():
                    if results:
                        results['queried_type'] = node_type_used_for_name_res
            json.dump(self.node_name_to_id_lookup,
                      name_res_results_file,
                      indent=4,
                      sort_keys=True)

        # write name res lookup times
        with open(os.path.join(self.data_path, "..",
                               f"parsed_{self.parsing_version}",
                               "name_res_timing_litcoin.tsv"), "w") as name_res_timing_file:
            name_res_timing_file.writelines(self.name_res_stats)

        parsing_metadata = {
            'records': records,
            'skipped_records': skipped_records
        }
        return parsing_metadata

    def process_llm_node(self, node_name: str, node_type: str):

        # check if we did name resolution for this name and type already and return it if so
        if node_name in self.node_name_to_id_lookup[node_type]:
            return self.node_name_to_id_lookup[node_type][node_name]

        # otherwise call the name res service and try to find a match
        # the following node_type string formatting conversion is kind of unnecessary now,
        # it was intended to produce valid biolink types given the node_type from the llm,
        # but that doesn't really work well enough to use, now we use the NODE_TYPE_MAPPINGS mappings,
        # but the keys currently use the post-conversion format so this stays for now
        biolink_node_type = self.convert_node_type_to_biolink_format(node_type)
        preferred_biolink_node_type = NODE_TYPE_MAPPINGS.get(biolink_node_type, None)
        self.logger.debug(f'calling name res for {node_name} - {preferred_biolink_node_type}')
        start_time = time.time()
        name_resolution_results = self.name_resolution_function(node_name, preferred_biolink_node_type)
        elapsed_time = time.time() - start_time
        standardized_name_res_result = self.standardize_name_resolution_results(name_resolution_results)
        self.name_res_stats.append(f"{node_name}\t{preferred_biolink_node_type}\t{elapsed_time}\n")
        self.node_name_to_id_lookup[node_type][node_name] = standardized_name_res_result
        return standardized_name_res_result

    @staticmethod
    def convert_node_type_to_biolink_format(node_type):
        biolink_node_type = re.sub("[()/]", "", node_type)  # remove parentheses and forward slash
        biolink_node_type = "".join([node_type_segment[0].upper() + node_type_segment[1:].lower()
                             for node_type_segment in biolink_node_type.split()])  # force Pascal case
        return f'{biolink_node_type}'

    @staticmethod
    def parse_llm_output(llm_output):
        # this is from Miles at CoVar, it parses the current format of output from the llm
        required_fields = [LLM_SUBJECT_NAME,
                           LLM_SUBJECT_TYPE,
                           LLM_OBJECT_NAME,
                           LLM_OBJECT_TYPE,
                           LLM_RELATIONSHIP,
                           LLM_MAIN_FINDING]
        matches = re.findall(r'\{([^\}]*)\}', llm_output)
        valid_responses = []
        for match in matches:
            cur_response = '{' + match + '}'
            try:
                cur_response_dict = json.loads(cur_response)
            except json.decoder.JSONDecodeError:
                continue
            for field in required_fields:
                if field not in cur_response_dict:
                    break
            else:  # only add the fields which have all the fields
                valid_responses.append(cur_response_dict)
        return valid_responses

    def name_resolution_function(self, node_name, preferred_biolink_node_type):
        return call_name_resolution(node_name, preferred_biolink_node_type)

    def standardize_name_resolution_results(self, name_res_json):
        if not name_res_json:
            return None
        return {
            "curie": name_res_json['curie'],
            "name": name_res_json['label'],
            "types": list(self.bl_utils.find_biolink_leaves(set(name_res_json['types']))),
            "score": None
        }

class LitCoinSapBERTLoader(LitCoinLoader):
    source_id: str = 'LitCoinSapBERT'
    parsing_version: str = '1.2'

    def name_resolution_function(self, node_name, preferred_biolink_node_type):
        sapbert_url = 'https://babel-sapbert.apps.renci.org/annotate/'
        sapbert_payload = {
          "text": node_name,
          "model_name": "sapbert",
          "count": 1000,
          "args": {"bl_type": preferred_biolink_node_type}
        }
        sapbert_response = requests.post(sapbert_url, json=sapbert_payload)
        if sapbert_response.status_code == 200:
            sapbert_json = sapbert_response.json()
            # return the first result if there is one
            if sapbert_json:
                return sapbert_json[0]
        else:
            self.logger.error(f'Non-200 Sapbert result {sapbert_response.status_code} for request {sapbert_payload}')
        # if no results return None
        return None

    def standardize_name_resolution_results(self, name_res_json):
        if not name_res_json:
            return None
        return {
            "curie": name_res_json['curie'],
            "name": name_res_json['name'],
            "types": [name_res_json['category']],
            "score": name_res_json['score']
        }

