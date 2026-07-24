"""
This stage exports synthetic population from IPU (Dhaka variant of
analysis_dhaka/seville/ivt_style/save_output.py).
"""


def configure(context):
    context.stage("dhaka.ipu.attributed")
    context.stage("dhaka.data.hts.entd.filtered")
    context.config("analysis_path")


def execute(context):
    df_ipu = context.stage("dhaka.ipu.attributed")
    df_hts_households, df_hts_persons, _ = context.stage("dhaka.data.hts.entd.filtered")
    df_ipu.to_csv(f"{context.config('analysis_path')}/synthetic_population.csv", sep=";", index=None, lineterminator="\n")
    df_hts_persons.to_csv(f"{context.config('analysis_path')}/hts_population.csv", sep=";", index=None, lineterminator="\n")
    df_hts_households.to_csv(f"{context.config('analysis_path')}/hts_households.csv", sep=";", index=None, lineterminator="\n")
