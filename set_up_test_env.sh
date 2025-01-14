#!/usr/bin/env bash

#These environment variables are required by Data Services. See the README for more information.

#ORION_STORAGE - a directory for storing data sources
mkdir -p "$PWD/../ORION_storage"
export ORION_STORAGE="$PWD/../ORION_storage/"

#ORION_GRAPHS - a directory for storing knowledge graphs
mkdir -p "$PWD/../ORION_graphs"
export ORION_GRAPHS="$PWD/../ORION_graphs/"

#ORION_LOGS - a directory for storing logs
mkdir -p "$PWD/../ORION_logs"
export ORION_LOGS="$PWD/../ORION_logs/"

#Use EITHER of the following, ORION_GRAPH_SPEC or ORION_GRAPH_SPEC_URL

#ORION_GRAPH_SPEC - the name of a Graph Spec file located in the graph_specs directory of ORION
export ORION_GRAPH_SPEC=example-graph-spec.yaml

#ORION_GRAPH_SPEC_URL - a URL pointing to a Graph Spec file
#export ORION_GRAPH_SPEC_URL=https://raw.githubusercontent.com/RENCI-AUTOMAT/ORION/helm_deploy/graph_specs/yeast-graph-spec.yml

export PYTHONPATH="$PYTHONPATH:$PWD"


#The following environment variables are optional
# export EDGE_NORMALIZATION_ENDPOINT=https://bl-lookup-sri.renci.org/
# export NODE_NORMALIZATION_ENDPOINT=https://nodenormalization-sri.renci.org/
# export NAME_RESOLVER_ENDPOINT=https://name-resolution-sri.renci.org/
# export ORION_OUTPUT_URL=https://localhost/  # this is currently only used to generate metadata
# export BL_VERSION=4.2.1

# if you are building your own docker image and issues occur, setting the correct platform may help
# export DOCKER_PLATFORM=linux/arm64
