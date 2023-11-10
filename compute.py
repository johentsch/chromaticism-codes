import ast

import pandas as pd
import numpy as np

from pitchtypes import SpelledPitchClass

from utils.util import DTYPES, flatten, flatten_to_list, safe_literal_eval
from utils.htypes import Numeral, Key
from utils.metrics import tone_to_diatonic_set_distance, cumulative_distance_to_diatonic_set, \
    min_distance_from_S_to_L


def load_dcml_harmonies_tsv(harmony_tsv_path: str, meatadata_tsv_path: str) -> pd.DataFrame:
    """
    An intermediate step for loading and process the filtered dcml harmonies tsv before computing chromaticity
    :param harmony_tsv_path:
    :param meatadata_tsv_path:
    :return:
    """
    df = pd.read_csv(harmony_tsv_path, sep="\t", dtype=DTYPES, index_col=0)

    def find_lk_spc(row):
        """get the local key tonic (in roman numeral) in spelled pitch class"""
        return Numeral.from_string(s=row["localkey"], k=Key.from_string(s=row["globalkey"])).key_if_tonicized().tonic

    def lk2c_dist(row):
        """get the fifths distance from the local key tonic to C"""
        return row["localkey_spc"].interval_to(SpelledPitchClass("C")).fifths()

    def correct_tpc_ref_center(row):
        return [x + row["lk2C"] for x in row["tones_in_span_in_C"]]

    def tones_not_in_ct(row):
        return [item for item in row['tones_in_span'] if item not in row['ct']]

    def determine_mode(row):
        return "minor" if row["localkey"].islower() else "major"

    df["lk_mode"] = df.apply(determine_mode, axis=1)

    df["localkey_spc"] = df.apply(find_lk_spc, axis=1)

    df["lk2C"] = df.apply(lk2c_dist, axis=1)

    # flatten and get unique tpc in the list in tones_in_span_in_C col
    df["tones_in_span_in_C"] = df["tones_in_span_in_C"].apply(lambda s: list(ast.literal_eval(s))).apply(
        lambda lst: list(flatten(lst))).apply(lambda l: list(set(l)))

    # correct the tpc to reference to local key tonic
    df["tones_in_span"] = df.apply(correct_tpc_ref_center, axis=1)

    df["added_tones"] = df["added_tones"].apply(lambda s: safe_literal_eval(s)).apply(flatten_to_list)
    df["chord_tones"] = df["chord_tones"].apply(lambda s: list(ast.literal_eval(s)))

    df["ct"] = df.apply(lambda row: [x for x in row["chord_tones"] + row["added_tones"] if x != row["root"]], axis=1)
    df["nct"] = df.apply(tones_not_in_ct, axis=1)

    # add metadata
    metadata_df = pd.read_csv(meatadata_tsv_path, sep="\t", usecols=["corpus", "piece", "composed_end"])
    metadata_df["piece"] = metadata_df["piece"].str.normalize(form='NFC')

    print(f'adding metadata to the df ...')
    # Create a dict mapping from (corpus, piece) to year from metadata_df
    mapping = dict(zip(zip(metadata_df['corpus'], metadata_df['piece']), metadata_df['composed_end']))

    # normalize the strings
    df['piece'] = df['piece'].str.normalize(form='NFC')

    df["piece_year"] = df.apply(lambda row: mapping[(str(row['corpus']), str(row['piece']))], axis=1)
    df = df.assign(corpus_year=df.groupby("corpus")["piece_year"].transform(np.mean)).sort_values(
        ['corpus_year', 'piece_year']).reset_index(drop=True)

    return df


def compute_chord_chromaticity(df: pd.DataFrame) -> pd.DataFrame:
    # the distance of the root to the closet members of the diatonic set generated by the localkey tonic.
    df["r_chromaticity"] = df.apply(lambda row: tone_to_diatonic_set_distance(tone=int(row["root"]),
                                                                              tonic=None,
                                                                              diatonic_mode=row["lk_mode"]), axis=1)

    # the cumulative distance of the chord tones to the local key scale set
    df["ct_chromaticity"] = df.apply(
        lambda row: cumulative_distance_to_diatonic_set(tonic=None, ts=row["ct"], diatonic_mode=row["lk_mode"]), axis=1)

    # the cumulative distance of the non-chord tones to the local key scale set
    df["nct_chromaticity"] = df.apply(
        lambda row: cumulative_distance_to_diatonic_set(tonic=None, ts=row["nct"], diatonic_mode=row["lk_mode"]),
        axis=1)

    # the cumulative distance of the concurrent pitch class set to the local key scale set
    df["pcs_chromaticity"] = df.apply(lambda row: cumulative_distance_to_diatonic_set(tonic=None,
                                                                                      ts=row["tones_in_span"],
                                                                                      diatonic_mode=row["lk_mode"]),
                                      axis=1)
    # diatonicity of chord tones:
    df["ct_diatonicity"] = df.apply(
        lambda row: min_distance_from_S_to_L(S=row["ct"]), axis=1)

    # diatonicity of non-chord tones:
    df["nct_diatonicity"] = df.apply(
        lambda row: min_distance_from_S_to_L(S=row["nct"]), axis=1)

    return df


def compute_piece_chromaticity(df: pd.DataFrame, compute_full: bool = False) -> pd.DataFrame:
    def calculate_max_min_pc(x):
        if len(x) > 0:
            return max(x), min(x)
        else:  # hacking the zero-length all_tones
            return 0, 0

    df["max_ct"] = df["ct"].apply(lambda x: max(x))
    df["min_ct"] = df["ct"].apply(lambda x: min(x))

    df["max_nct"], df["min_nct"] = zip(*df["nct"].apply(calculate_max_min_pc))

    if compute_full:
        result_df = df.groupby(['corpus', 'piece'], as_index=False).agg(
            corpus_year=("corpus_year", "first"),
            piece_year=("piece_year", "first"),
            globalkey=("globalkey", "first"),
            localkey=("localkey", "first"),

            max_root=("root", "max"),
            min_root=("root", "min"),

            max_ct=("max_ct", "max"),
            min_ct=("min_ct", "min"),

            max_nct=("max_nct", "max"),
            min_nct=("min_nct", "min"),

            mean_r_chromaticity=("r_chromaticity", "mean"),
            max_r_chromaticity=("r_chromaticity", "max"),
            min_r_chromaticity=("r_chromaticity", "min"),

            mean_ct_chromaticity=("ct_chromaticity", lambda x: x.unique().mean()),
            max_ct_chromaticity=("ct_chromaticity", "max"),
            min_ct_chromaticity=("ct_chromaticity", "min"),

            mean_nct_chromaticity=("nct_chromaticity", lambda x: x.unique().mean()),
            max_nct_chromaticity=("nct_chromaticity", "max"),
            min_nct_chromaticity=("nct_chromaticity", "min"),

            mean_pcs_chromaticity=("pcs_chromaticity", lambda x: x.unique().mean()),
            max_pcs_chromaticity=("pcs_chromaticity", "max"),
            min_pcs_chromaticity=("pcs_chromaticity", "min"),

            mean_ct_diatonicity=("ct_diatonicity", lambda x: x.unique().mean()),
            max_ct_diatonicity=("ct_diatonicity", "max"),
            min_ct_diatonicity=("ct_diatonicity", "min"),

            mean_nct_diatonicity=("nct_diatonicity", lambda x: x.unique().mean()),
            max_nct_diatonicity=("nct_diatonicity", "max"),
            min_nct_diatonicity=("nct_diatonicity", "min")

        )

    else:
        result_df = df.groupby(['corpus', 'piece'], as_index=False).agg(
            corpus_year=("corpus_year", "first"),
            piece_year=("piece_year", "first"),
            globalkey=("globalkey", "first"),
            localkey=("localkey", "first"),

            max_root=("root", "max"),
            min_root=("root", "min"),

            max_ct=("max_ct", "max"),
            min_ct=("min_ct", "min"),

            max_nct=("max_nct", "max"),
            min_nct=("min_nct", "min"),

            RC=("r_chromaticity", "mean"),

            CTC=("ct_chromaticity", lambda x: x.unique().mean()),

            NCTC=("nct_chromaticity", lambda x: x.unique().mean()),

            CTD=("ct_diatonicity", lambda x: x.unique().mean()),

            NCTD=("nct_diatonicity", lambda x: x.unique().mean())
        )

    result_df = result_df.sort_values(by=["corpus_year", "piece_year"], ignore_index=True)
    result_df["r_fifths_range"] = (result_df["max_root"] - result_df["min_root"]).abs()
    result_df["ct_fifths_range"] = (result_df["max_ct"] - result_df["min_ct"]).abs()
    result_df["nct_fifths_range"] = (result_df["max_nct"] - result_df["min_nct"]).abs()

    result_df["corpus_id"] = pd.factorize(result_df["corpus"])[0] + 1
    result_df["piece_id"] = list(range(1, len(result_df) + 1))

    return result_df


def beethoven_chromaticity(piece_indices_result: str = "data/piece_indices.tsv"):
    corpora = ["ABC", "beethoven_piano_sonatas"]
    df = pd.read_csv(piece_indices_result, sep="\t")

    beethoven_df = df[df['corpus'].isin(corpora)]
    return beethoven_df


def save_df(df: pd.DataFrame, directory: str, fname: str):
    path = f'{directory}/{fname}.tsv'
    df.to_csv(path, sep="\t")


if __name__ == "__main__":
    # load preprocess data
    h = load_dcml_harmonies_tsv(harmony_tsv_path="data/dcml_harmonies.tsv",
                                meatadata_tsv_path="data/all_subcorpora/all_subcorpora.metadata.tsv")

    print(f'computing chord chromaticity indices ...')
    chord_chromaticity_df = compute_chord_chromaticity(h)
    save_df(df=chord_chromaticity_df, directory="data/", fname="chord_indices")

    print(f'computing piece-level chromaticity ...')
    piece_chromaticity_df = compute_piece_chromaticity(chord_chromaticity_df)
    save_df(df=piece_chromaticity_df, directory="data/", fname="piece_indices")

    beethoven = beethoven_chromaticity()
    save_df(df=beethoven, directory="data/", fname="beethoven_chromaticity")
