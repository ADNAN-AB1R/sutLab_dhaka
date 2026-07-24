import numpy as np

"""
This stage exports synthetic population from IPU.
"""


def configure(context):
    context.stage("seville.ipu.attributed")
    context.stage("seville.data.hts.entd.filtered")
    context.config("analysis_path")


def execute(context):
    df_ipu = context.stage("seville.ipu.attributed")   
    df_hts_households, df_hts_persons, _ = context.stage("seville.data.hts.entd.filtered")        
    df_ipu.to_csv(f"{context.config('analysis_path')}/synthetic_population.csv", sep = ";", index = None, lineterminator = "\n")
    df_hts_persons.to_csv(f"{context.config('analysis_path')}/hts_population.csv", sep = ";", index = None, lineterminator = "\n")
    df_hts_households.to_csv(f"{context.config('analysis_path')}/hts_households.csv", sep = ";", index = None, lineterminator = "\n")
