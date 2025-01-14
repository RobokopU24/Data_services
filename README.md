
# ORION
### Operational Routine for the Ingest and Output of Networks

This package takes data sets from various sources and converts them into Knowledge Graphs.

Each data source will go through the following pipeline before it can be included in a graph:

1. Fetch (retrieve an original data source) 
2. Parse (convert the data source into KGX files) 
3. Normalize (use normalization services to convert identifiers and ontology terms to preferred synonyms) 
4. Supplement (add supplementary knowledge specific to that source)

To build a graph use a Graph Spec yaml file to specify the sources you want.

ORION will automatically run each data source specified through the necessary pipeline. Then it will merge the specified sources into a Knowledge Graph.

### Installing and Configuring ORION

Create a parent directory:
```
mkdir ~/ORION_root
```

Clone the code repository:
```
cd ~/ORION_root
git clone https://github.com/RobokopU24/ORION.git
```

Next create directories where data sources, graphs, and logs will be stored. 

**ORION_STORAGE** - for storing data sources

**ORION_GRAPHS** - for storing knowledge graphs

**ORION_LOGS** - for storing logs

You can do this manually, or use the script indicated below to set up a default workspace.

Option 1: Use this script to create the directories and set the environment variables:
```
cd ~/ORION_root/ORION/
source ./set_up_test_env.sh
```

Option 2: Create three directories and set environment variables specifying paths to the locations of those directories.
```
mkdir ~/ORION_root/storage/
export ORION_STORAGE=~/ORION_root/storage/ 

mkdir ~/ORION_root/graphs/
export ORION_GRAPHS=~/ORION_root/graphs/

mkdir ~/ORION_root/logs/
export ORION_LOGS=~/ORION_root/logs/
```

Next create or select a Graph Spec yaml file, where the content of knowledge graphs to be built is specified.

Set either of the following environment variables, but not both:

Option 1: ORION_GRAPH_SPEC - the name of a Graph Spec file located in the graph_specs directory of ORION
```
export ORION_GRAPH_SPEC=example-graph-spec.yaml
```
Option 2: ORION_GRAPH_SPEC_URL - a URL pointing to a Graph Spec yaml file
```
export ORION_GRAPH_SPEC_URL=https://stars.renci.org/var/data_services/graph_specs/default-graph-spec.yaml
```

To build a custom graph, alter a Graph Spec file, which is composed of a list of graphs.

For each graph, specify:

**graph_id** - a unique identifier string for the graph, with no spaces

**sources** - a list of sources identifiers for data sources to include in the graph

See the full list of data sources and their identifiers in the [data sources file](https://github.com/RobokopU24/ORION/blob/master/Common/data_sources.py).

Here is a simple example. 
```
graphs:
  - graph_id: Example_Graph
    graph_name: Example Graph
    graph_description: A free text description of what is in the graph.
    output_format: neo4j
    sources:
      - source_id: CTD
      - source_id: HGNC
```

There are variety of ways to further customize a knowledge graph. The following are parameters you can set for a particular data source. Mostly, these parameters are used to indicate that you'd like to use a previously built version of a data source or a specific normalization of a source. If you specify versions that are not the latest, and haven't previously built a data source or graph with those versions, it probably won't work.

**source_version** - the version of the data source, as determined by ORION

**parsing_version** - the version of the parsing code in ORION for this source

**merge_strategy** - used to specify alternative merge strategies

The following are parameters you can set for the entire graph, or for an individual data source:

**node_normalization_version** - the version of the node normalizer API (see: https://nodenormalization-sri.renci.org/openapi.json)

**edge_normalization_version** - the version of biolink model used to normalize predicates and validate the KG

**strict_normalization** - True or False specifying whether to discard nodes, node types, and edges connected to those nodes when they fail to normalize

**conflation** - True or False flag specifying whether to conflate genes with proteins and chemicals with drugs

For example, we could customize the previous example:
```
graphs:
  - graph_id: Example_Graph
    graph_name: Example Graph
    graph_description: A free text description of what is in the graph.
    output_format: neo4j
    sources:
      - source_id: CTD
      - source_id: HGNC
```

See the graph_specs directory for more examples.

### Running ORION

Install Docker to create and run the necessary containers. 

By default, using docker-compose up will build every graph in your Graph Spec. It runs the command: python /ORION/Common/build_manager.py all
```
docker-compose up
```
If you want to build an individual graph, you can override the default command with a graph_id from the Graph Spec:
```
docker-compose run --rm orion python /ORION/Common/build_manager.py Example_Graph
```
To run the ORION pipeline for a single data source, you can use the load manager:
```
docker-compose run --rm orion python /ORION/Common/load_manager.py CTD
```
To see available arguments and a list of supported data sources:
```
docker-compose run --rm orion python /ORION/Common/load_manager.py -h
```

### For Developers

To add a new data source to ORION, create a new parser. Each parser extends the SourceDataLoader interface in Common/loader_interface.py.

To implement the interface you will need to write a class that fulfills the following.

Set the class level variables for the source ID and provenance: 
```
source_id: str = 'ExampleSourceID'
provenance_id: str = 'infores:example_source'
```

In initialization, call the parent init function first and pass the initialization arguments.
Then set the file names for the data file or files.
```
super().__init__(test_mode=test_mode, source_data_dir=source_data_dir)

self.data_file = 'example_file.gz'
OR
self.example_file_1 = 'example_file_1.csv'
self.example_file_2 = 'example_file_2.csv'
self.data_files = [self.example_file_1, self.example_file_2]
```

Note that self.data_path is set by the parent class and by default refers to a specific directory for the current version of that source in the storage directory.

Implement get_latest_source_version(). This function should return a string representing the latest available version of the source data.

Implement get_data(). This function should retrieve any source data files. The files should be stored with the file names specified by self.data_file or self.data_files. They should be saved in the directory specified by self.data_path.

Implement parse_data(). This function should parse the data files and populate lists of node and edge objects: self.final_node_list (kgxnode), self.final_edge_list (kgxedge).

Finally, add your source to the list of sources in Common/data_sources.py. The source ID string here should match the one specified in the new parser. Also add your source to the SOURCE_DATA_LOADER_CLASS_IMPORTS dictionary, mapping it to the new parser class.

Now you can use that source ID in a graph spec to include your new source in a graph, or as the source id using load_manager.py.

#### Testing and Troubleshooting

After you alter the codebase, or if you are experiencing issues or errors you may want to run tests:
```
docker-compose run --rm orion pytest /ORION
```