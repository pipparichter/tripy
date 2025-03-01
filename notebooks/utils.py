import pandas as pd 
import numpy as np 
import re
from src import GTDB_DTYPES
import os 
import glob
from src import *  
from src.files import GBFFFile, FASTAFile
from tqdm import tqdm 
import matplotlib.pyplot as plt 
import warnings
from sklearn.linear_model import LogisticRegression, LinearRegression

plt.rcParams['font.family'] = 'Arial'

is_n_truncated = lambda df : ((df.start > df.top_hit_start) & (df.strand == 1)) | ((df.stop < df.top_hit_stop) & (df.strand == -1)) 
is_c_truncated = lambda df : ((df.stop < df.top_hit_stop) & (df.strand == 1)) | ((df.start > df.top_hit_start) & (df.strand == -1)) 
is_n_extended = lambda df : ((df.start < df.top_hit_start) & (df.strand == 1)) | ((df.stop > df.top_hit_stop) & (df.strand == -1)) 
is_c_extended = lambda df : ((df.stop > df.top_hit_stop) & (df.strand == 1)) | ((df.start < df.top_hit_start) & (df.strand == -1)) 

is_hypothetical = lambda df : df.top_hit_product == 'hypothetical protein'
is_ab_initio = lambda df : df.top_hit_evidence_type == 'ab initio prediction'
is_suspect = lambda df : is_hypothetical(df) & is_ab_initio(df) # This will be False for intergenic sequences. 


# Need to make sure to check that n_hits > 0 for the spurious check, or there is overlap with intergenic.
is_intergenic = lambda df : df.n_hits == 0 # Uncertain label. 
is_suspect_match = lambda df : is_suspect(df) & df.top_hit_valid # Uncertain label. 
is_suspect_conflict = lambda df : is_suspect(df) & ~df.top_hit_valid & (df.n_hits > 0) # Uncertain label. 
is_spurious = lambda df : ~is_suspect(df) & ~df.top_hit_valid & (df.n_hits > 0) # Certain negative label. 
is_real = lambda df : ~is_suspect(df) & df.top_hit_valid # Certain positive label. 

has_interpro_hit = lambda row : not (pd.isnull(row.interpro_analysis))


def remove_partial(df:pd.DataFrame):
    assert (df.partial.isnull().sum() == 0), 'remove_partial: Some of the proteins do not have a partial indicator.'
    assert (df.partial.dtype == 'object') and (df.ref_partial.dtype == 'object'), 'remove_partial: It seems as though the partial indicators were not loaded in as strings.'
    mask = ((df.partial != '00') & pd.isnull(df.ref_partial))
    mask = mask | ((df.partial != '00') & (df.ref_partial != '00')) 
    print(f'remove_partial: Removing {int(mask.sum())} sequences marked as partial by both Prodigal and the reference.')
    return df[~mask].copy()


def get_lengths(df:pd.DataFrame, top_hit:bool=True):
    start_col, stop_col = ('top_hit_' if top_hit else '') +'start', ('top_hit_' if top_hit else '') + 'stop'
    lengths = (df[stop_col] - (df[start_col] - 1)) # The start position is inclusive but one-indexes, and the stop position is non-inclusive

    if np.any((lengths % 3) != 0):
        warnings.warn('get_lengths: Not all gene lengths are divisible by three.')
    if pd.isnull(lengths).sum() > 0:
        warnings.warn('get_lengths: Some of the returned lengths are NaNs, which probably means there are sequences that do not have NCBI reference hits.')

    return lengths // 3


def denoise(df:pd.DataFrame, x_col:str=None, y_cols:list=None, bins:int=50):
    bin_labels, bin_edges = pd.cut(df[x_col], bins=bins, retbins=True, labels=False)
    df['bin_label'] = bin_labels 
    df_ = dict()
    df_[x_col] = df.groupby('bin_label', sort=True)[x_col].mean()
    for y_col in y_cols:
        df_[y_col] = df.groupby('bin_label', sort=True)[y_col].mean()
        df_[f'{y_col}_err'] = df.groupby('bin_label').apply(lambda df : df[y_col].std() / np.sqrt(len(df)), include_groups=False)
    df_ = pd.DataFrame(df_, index=df.bin_label.sort_values().unique())
    return df_


def correlation(x, y):
    linreg = LinearRegression().fit(x.reshape(-1, 1), y) 
    r2 = linreg.score(x.reshape(-1, 1), y)
    return np.round(r2, 3), linreg


def partial_correlation(x, y, z):
    # Standardize the input arrays. 
    x, y, z = (x - x.mean()) / x.std(), (y - y.mean()) / y.std(), (z - z.mean()) / z.std()

    _, linreg_zy = correlation(z, y)
    _, linreg_zx = correlation(z, x)
    # Do not need to standardize the residuale (not sure if I completely understand why)
    x_residuals = x - linreg_zx.predict(z.reshape(-1, 1))
    y_residuals = y - linreg_zy.predict(z.reshape(-1, 1))

    r2, linreg_xy = correlation(x_residuals, y_residuals)
    return r2, linreg_xy, (x_residuals, y_residuals)


def load_ncbi_genome_metadata(genome_metadata_path='../data/ncbi_genome_metadata.tsv', taxonomy_metadata_path:str='../data/ncbi_taxonomy_metadata.tsv'):
    taxonomy_metadata_df = pd.read_csv(taxonomy_metadata_path, delimiter='\t', low_memory=False)
    genome_metadata_df = pd.read_csv(genome_metadata_path, delimiter='\t', low_memory=False)

    taxonomy_metadata_df = taxonomy_metadata_df.drop_duplicates('Taxid')
    genome_metadata_df = genome_metadata_df.drop_duplicates('Assembly Accession')

    genome_metadata_df = genome_metadata_df.merge(taxonomy_metadata_df, right_on='Taxid', left_on='Organism Taxonomic ID', how='left')
    genome_metadata_df = genome_metadata_df.drop(columns=['Organism Taxonomic ID']) # This column is redundant now. 
    genome_metadata_df = genome_metadata_df.rename(columns={col:'_'.join(col.lower().split()) for col in genome_metadata_df.columns})

    col_names = dict()
    col_names['annotation_count_gene_protein-coding'] = 'n_gene_protein_coding'
    col_names['annotation_count_gene_non-coding'] = 'n_gene_non_coding'
    col_names['annotation_count_gene_pseudogene'] = 'n_pseudogene'
    col_names['assembly_stats_gc_percent'] = 'gc_percent'
    col_names['assembly_stats_total_sequence_length'] = 'total_sequence_length'
    col_names['assembly_stats_number_of_contigs'] = 'n_contigs'
    col_names['taxid'] = 'taxonomy_id'
    levels = ['phylum', 'superkingdom', 'kingdom', 'class', 'order', 'genus', 'species']
    col_names.update({f'{level}_taxid':f'{level}_taxonomy_id' for level in levels})
    col_names.update({f'{level}_name':f'{level}' for level in levels})
    col_names['phylum_taxid'] = 'phylum_taxonomy_id'
    col_names['class_taxid'] = 'class_taxonomy_id'
    col_names['order_taxid'] = 'order_taxonomy_id'
    col_names['genus_taxid'] = 'genus_taxonomy_id'
    col_names['species_taxid'] = 'species_taxonomy_id'
    col_names['kingdom_taxid'] = 'kingdom_taxonomy_id'
    col_names['assembly_accession'] = 'genome_id'

    genome_metadata_df = genome_metadata_df.rename(columns=col_names)
    genome_metadata_df = fillna(genome_metadata_df, rules={str:'none'}, check=False)
    return genome_metadata_df.set_index('genome_id')


def load_pred_out(path:str, model_name:str=''):

    df = pd.read_csv(path, index_col=0)

    cols = [col for col in df.columns if ((model_name in col) or (col == 'label'))]
    df = df[cols].copy()
    df = df.rename(columns={col:col.replace(f'{model_name}', 'model') for col in cols})

    confusion_matrix = np.where((df.model_label == 1) & (df.label == 0), 'false positive', '')
    confusion_matrix = np.where((df.model_label  == 1) & (df.label == 1), 'true positive', confusion_matrix)
    confusion_matrix = np.where((df.model_label == 0) & (df.label == 1), 'false negative', confusion_matrix)
    confusion_matrix = np.where((df.model_label  == 0) & (df.label == 0), 'true negative', confusion_matrix)
    df['confusion_matrix'] = confusion_matrix
    df['model_name'] = model_name

    return df


def load_ref(genome_ids:list):

    summary_paths = [f'../data/ref/{genome_id}_summary.csv' for genome_id in genome_ids]
    query_paths = [f'../data/proteins/prodigal/{genome_id}_protein.faa' for genome_id in genome_ids] 

    summary_df = pd.concat([pd.read_csv(path, index_col=0, dtype={'top_hit_partial':str}) for path in summary_paths])
    query_df = pd.concat([FASTAFile(path).to_df(prodigal_output=True).assign(genome_id=get_genome_id(path)) for path in query_paths])

    ref_df = query_df.merge(summary_df, left_index=True, right_index=True, validate='one_to_one')
    ref_df['suspect_match'] = is_suspect_match(ref_df)
    ref_df['suspect_conflict'] = is_suspect_conflict(ref_df)
    ref_df['intergenic'] = is_intergenic(ref_df)
    ref_df['real'] = is_real(ref_df)
    ref_df['spurious'] = is_spurious(ref_df)

    categories = ['real', 'spurious', 'intergenic', 'suspect_match', 'suspect_conflict']
    assert (ref_df[categories].sum(axis=1) != 1).sum() == 0, 'load_ref: Each ref output entry should have exactly one assigned category.'

    return ref_df