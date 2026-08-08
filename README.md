# General Readme
# sutLab

## Hannover Synthetic Population

This repository contains the documentation and the code used to create the
synthetic populations and corresponding travel for city projects in SUT Lab. It also provides stages that can be used to convert it and run a MATSim/eqasim agent-based simulations. Furthermore, it provides a visualization of the data at the end of the pipeline.

The pipeline uses the `synpp` Python package for stage chaining avaialble at [here](https://github.com/eqasim-org/synpp).

The pipeline generates:

- Synthetic households and persons with sociodemographic attributes cities already setup
- Daily activity schedules and trips
- Outputs in both **CSV** and **GeoPackage (GPKG)** formats for direct use in **MATSim simulations** and related spatial analyses.

# Installation

Before using the pipeline one needs to have the Python environment set up. This can be done either by setting up the `conda` environment or a `python` environment.

For setting up the conda environment (not continuously tested):

Two bash scripts which set up everything that is needed to run the pipeline on Linux machines, as well as a requirements.txt file, can be found in `environment`:

- `setup.sh [path]` downloads Miniconda3, creates a Python virtual environment, installs OpenJDK and Maven. A path needs to be passed, which defines the directory in which the environment will be setup. Make sure you call this script with `bash`!
- `activate.sh [path]` activates the environment when the script is *source*'d. The path to the environment needs to be supplied.

An `environment.yml [path]` specifies the python environment variables needed and is read by the `setup.sh [path]`. If the name of the python environment is changed in the environment file, this must be done in the `activate.sh [path]` which activates the python environment.

Example:
- `bash environment/setup.sh myenv`
- `source environment/activate.sh myenv`

To clean, simply delete the environment directory (here `myenv`).

In case you are using a Mac machine there are minoconda paths within the `environment/setup.sh` file that you can use.

For settign up the python environment:
- Install `Python 3.10.13`
- Install packages in `euler_requirements.txt`
- How to do this in detail on our Euler server can be found [here](https://gitlab.ethz.ch/csfm/csfm-documentation/-/wikis/MATSim/Eqasim-on-Euler).

# Run

Once you have set up your environment, all dependencies should have been installed, including synpp. At this point, all you need to do is adjust the config file (**DO NOT MODIFY** `config.yml`) to run the stages you required, and then:

`python3 -m synpp config.yml`



# Data preparation and usage

To run the pipeline successfully, you need to gather and prepare several input datasets, such as:

- Administrative boundaries of Hannover
- MiD 2017 German national travel survey
- Buildings
- Driving license statistics
- OpenStreetMap

For step-by-step instructions on gathering and structuring the necessary data, please refer to the [Data preparation](./docs/population.md).

To run the analysis, add the stage 'analysis.hannover.ivt_style.analysis'. This will produce plots for comparison between HTS and synthesis data.
