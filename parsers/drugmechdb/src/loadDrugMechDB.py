import json
import requests as rq
import os

from Common.utils import GetData
from Common.loader_interface import SourceDataLoader
from Common.kgxmodel import kgxnode, kgxedge

def load_json(json_data):
    with open(json_data, encoding="utf-8") as file:
        data = json.load(file)
    file.close()
    return data

predicate_mappings = {
    "biolink:decreases_abundance_of":["biolink:affects","biolink:causes","decreased","abundance"],
    "biolink:increases_abundance_of":["biolink:affects","biolink:causes","increased","abundance"],
    "biolink:decreases_activity_of":["biolink:affects","biolink:causes","decreased","activity"],
    "biolink:increases_activity_of":["biolink:affects","biolink:causes","increased","activity"],
    "biolink:decreases_expression_of":["biolink:affects","biolink:causes","decreased","expression"],
    "biolink:increases_expression_of":["biolink:affects","biolink:causes","increased","expression"],
    "biolink:decreases_synthesis_of":["biolink:affects","biolink:causes","decreased","synthesis"],
    "biolink:increases_synthesis_of":["biolink:affects","biolink:causes","increased","synthesis"],
    "biolink:decreases_uptake_of":["biolink:affects","biolink:causes","decreased","uptake"],
    "biolink:increases_uptake_of":["biolink:affects","biolink:causes","increased","uptake"],
    "biolink:decreases_degradation_of":["biolink:affects","biolink:causes","decreased","degradation"],
    "biolink:increases_degradation_of":["biolink:affects","biolink:causes","increased","degradation"],
    "biolink:decreases_stability_of":["biolink:affects","biolink:causes","decreased","stability"],
    "biolink:increases_stability_of":["biolink:affects","biolink:causes","increased","stability"],
    "biolink:decreases_transport_of":["biolink:affects","biolink:causes","decreased","transport"],
    "biolink:increases_transport_of":["biolink:affects","biolink:causes","increased","transport"],
    "biolink:negatively_regulates":["biolink:regulates","None","downregulated","None"],
    "biolink:positively_regulates":["biolink:regulates","None","upregulated","None"],
    "biolink:directly_interacts_with":["biolink:directly_physically_interacts_with","None","None","None"],
    "biolink:molecularly_interacts_with":["biolink:directly_physically_interacts_with","None","None","None"],
}
    
##############
# Class: Load in direct Gene/Protein-[biolink:target_for]->Disease relationships from DrugMechDB
# By: Jon-Michael Beasley
# Date: 09/06/2023
##############
class DrugMechDBLoader(SourceDataLoader):

    source_id: str = 'DrugMechDB'
    provenance_id: str = 'infores:drugmechdb'
    description = "A database of paths that represent the mechanism of action from a drug to a disease in an indication."
    source_data_url = "https://github.com/SuLab/DrugMechDB/raw/main/indication_paths.json"
    license = "SuLab/DrugMechDB is licensed under the Creative Commons Zero v1.0 Universal license"
    attribution = 'https://sulab.github.io/DrugMechDB/'
    parsing_version = '1.1'

    def __init__(self, test_mode: bool = False, source_data_dir: str = None):
        """
        constructor
        :param test_mode - sets the run into test mode
        """
        # call the super
        super().__init__(test_mode=test_mode, source_data_dir=source_data_dir)
        #self.drugmechdb_version = '202307'  # TODO temporarily hard coded
        self.drugmechdb_version = self.get_latest_source_version()
        self.drugmechdb_data_url = f"https://github.com/SuLab/DrugMechDB/raw/main/"
        self.drugmechdb_file_name = f"indication_paths.json"
        self.data_files = [self.drugmechdb_file_name]

    def get_latest_source_version(self) -> str:
        """
        gets the latest version of the data
        :return:
        """
        ### The method below gets the database version from the html, but this may be subject to change. ###
        drugmechdb_download_page_response = rq.get('https://github.com/SuLab/DrugMechDB')
        version_index = drugmechdb_download_page_response.text.index('<span class="css-truncate css-truncate-target text-bold mr-2" style="max-width: none;') + 87
        bindingdb_version = drugmechdb_download_page_response.text[version_index:version_index + 5]
        print(bindingdb_version)

        return f"{bindingdb_version}"
    
    def get_data(self) -> int:
        """
        Gets the DrugMechDB data.
        """
        data_puller = GetData()
        i=0
        for source in self.data_files:
            source_url = f"{self.drugmechdb_data_url}{source}"
            data_puller.pull_via_http(source_url, self.data_path)
            i+=1
        return True

    def process_node_to_kgx(self,node_id):
        #self.logger.info(f'processing node: {node_identity}')
        node_id = node_id.replace("InterPro:","interpro:").replace("UniProt:","UniProtKB:").replace("taxonomy:","NCBITaxon:").replace("reactome:","REACT:").replace("DB:","DRUGBANK:").replace("Pfam:","PFAM:").replace("\ufeff","")
        node_to_write = kgxnode(node_id)
        self.output_file_writer.write_kgx_node(node_to_write)
        return node_id

    def process_edge_to_kgx(self, subject_id: str, predicate: str, object_id: str, qualified_predicate: None, object_direction_qualifier: None, object_aspect_qualifier: None):
        if predicate:
            edgeprops={}
            if qualified_predicate:
                edgeprops.update({"qualified_predicate":qualified_predicate})
            if object_direction_qualifier:
                edgeprops.update({"object_direction_qualifier":object_direction_qualifier})
            if object_aspect_qualifier:
                edgeprops.update({"object_aspect_qualifier":object_aspect_qualifier})
            output_edge = kgxedge(
                subject_id=subject_id,
                object_id=object_id,
                predicate=predicate,
                edgeprops=edgeprops,
                primary_knowledge_source=self.provenance_id
            )
            self.output_file_writer.write_kgx_edge(output_edge)
        else:
            self.logger.warning(f'A predicate could not be mapped for relationship type {predicate}')
        return 

    def parse_data(self) -> dict:
        """
        Parses the data file for graph nodes/edges

        :return: ret_val: load_metadata
        """
        data = load_json(os.path.join(self.data_path,self.drugmechdb_file_name))
        for entry in data:
            #dmdb_id = entry["graph"]["_id"]
            #drug_name = entry["graph"]["drug"]
            drug_mesh = entry["graph"]["drug_mesh"]
            #drug_drugbank = entry["graph"]["drugbank"]
            #disease_name = entry["graph"]["disease"]
            disease_mesh = entry["graph"]["disease_mesh"]
            links  = entry["links"] 
            
            for i in range(len(links)):
                triple = links[i]
                source = triple["source"]
                source_id = self.process_node_to_kgx(source)
                predicate = "biolink:" + triple["key"].replace(" ","_")
                target = triple["target"]
                target_id = self.process_node_to_kgx(target)
                if predicate in predicate_mappings:
                    qualified_predicate = predicate_mappings[predicate][1] if predicate_mappings[predicate][1] != "None" else None
                    object_direction_qualifier = predicate_mappings[predicate][2] if predicate_mappings[predicate][2] != "None" else None
                    object_aspect_qualifier = predicate_mappings[predicate][3] if predicate_mappings[predicate][3] != "None" else None
                    predicate = predicate_mappings[predicate][0]
                    self.process_edge_to_kgx(subject_id=source_id, predicate=predicate, object_id=target_id, qualified_predicate=qualified_predicate, object_direction_qualifier=object_direction_qualifier, object_aspect_qualifier=object_aspect_qualifier)
                else:
                    self.process_edge_to_kgx(subject_id=source_id, predicate=predicate, object_id=target_id, qualified_predicate=None, object_direction_qualifier=None, object_aspect_qualifier=None)
                if source == drug_mesh:
                    nodes = entry["nodes"]
                    for node in nodes:
                        if (node["id"] == target) and (node["label"] == "Protein"):
                            #drug_target_name = node["name"]
                            drug_target_uniprot = node["id"]
                            drug_target_uniprot_id = self.process_node_to_kgx(drug_target_uniprot)
                            disease_mesh_id = self.process_node_to_kgx(disease_mesh)
                            self.process_edge_to_kgx(subject_id=drug_target_uniprot_id, predicate="biolink:target_for", object_id=disease_mesh_id, qualified_predicate=None, object_direction_qualifier=None, object_aspect_qualifier=None)
                            
                        # The section below checks the "Drug" + 1 node for drug metabolites, which may be the active molecule.
                        # Then, if the next node in the path is a protein, assign that as the target.
                        elif node["id"] == target and node["label"] in ["Drug","ChemicalSubstance"]:
                            if entry["links"][i+1]["source"] == node["id"]:
                                new_target = entry["links"][i+1]["target"]
                                for node in nodes:
                                    if (node["id"] == new_target) and (node["label"] == "Protein"):
                                        #drug_target_name = node["name"]
                                        drug_target_uniprot = node["id"]
                                        drug_target_uniprot_id = self.process_node_to_kgx(drug_target_uniprot)
                                        disease_mesh_id = self.process_node_to_kgx(disease_mesh)
                                        self.process_edge_to_kgx(subject_id=drug_target_uniprot_id, predicate="biolink:target_for", object_id=disease_mesh_id, qualified_predicate=None, object_direction_qualifier=None, object_aspect_qualifier=None)

                        else:
                            continue

        return {}