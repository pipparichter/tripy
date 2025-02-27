from src.files.gbff import GBFFFile
import pandas as pd 
import numpy as np
from tqdm import tqdm 
from src import get_genome_id, fillna


class ReferenceGenome():

    def __init__(self, path:str):

        self.genome_id = get_genome_id(genome_id)
        df = GBFFFile(path).to_df()
        df['genome_id'] = self.genome_id
        self.df = df

    def __str__(self):
        return self.genome_id
    
    def __len__(self):
        return len(self.df)

    def _get_hits(self, query):

        ref_df = self.df[self.df.contig_id == query.contig_id] # Get the contig corresponding of the query region. 
        hits_df = ref_df[~(ref_df.start > query.stop) & ~(ref_df.stop < query.start)].copy() # Everything which passes this filter overlaps with the query region. 
        hits_df.columns = ['subject_' + col for col in hits_df.columns]
        
        if len(hits_df) == 0:
            return None

        hits_info_df = list()
        for subject in hits_df.itertuples():
            hit = dict()
            hit['query_id'] = query.Index
            hit['subject_id'] = str(subject.Index) # This will be an integer ID, convert to a string.
            hit['query_strand'] = query.strand 
            hit['same_strand'] = (subject.strand == query.strand)
            # Computed overlap is relative to the query sequence, so can't exceed the length of the query. 
            hit['overlap_start'] = max(subject.start, query.start)   
            hit['overlap_stop'] = min(subject.stop, query.stop)
            hit['overlap_length'] = hit['overlap_stop'] - hit['overlap_start']
            hit['start_aligned'] = (subject.start == query.start)
            hit['stop_aligned'] = (subject.stop == query.stop)
            hit['match'] = (hit['start_aligned'] and hit['stop_aligned']) and hit['same_strand']
            hit['in_frame'] = ((query.stop - subject.stop) % 3 == 0) and ((query.stop - subject.stop) % 3 == 0)
            hit['valid'] = (hit['same_strand'] and hit['in_frame']) and (subject.feature == 'CDS')
            hits_info_df.append(hit)
        
        hits_df = pd.concat([hits_df, hits_info_df], ignore_index=True) # This will reset the index, which is no longer important. 
        return hits_df

    def search(self, query_df:pd.DataFrame, verbose:bool=True, summarize:bool=True):
        results_df = list()
        for query in tqdm(list(query_df.itertuples()), desc='ReferenceGenome.search', disable=(not verbose)):
            hits_df = self._get_hit(query) # Get the hit with the biggest overlap, with a preference for "valid" hits.
            if hits_df_ is not None:
                results_df.append(hits_df)

        results_df = pd.concat(search_df).reset_index()
        summary_df = ReferenceGenome.summarize(query_df, results_df) if summarize else NOne
        return results_df, summary_df

    @staticmethod
    def summarize(query_df:pd.DataFrame, results_df:pd.DataFrame):

        summary_df = []
        for query_id, df in results_df.groupby('query_id'):
            row = dict()
            row['query_id'] = query_id
            row['n_valid_hits'] = df.valid.sum()
            row['n_hits'] = len(df)
            row['n_hits_same_strand'] = df.same_strand.sum()
            row['n_hits_opposite_strand'] = len(df) - row['n_hits_same_strand']
            row['n_hits_in_frame'] = df.in_frame.sum()
            # Sort values on a boolean will put False (0) first, and True (1) last if ascending is True. 
            top_hit = search_df.sort_values(by=['valid', 'overlap_length'], ascending=False).iloc[0]
            row['top_hit_overlap'] = top_hit['overlap']
            row['top_hit_locus_tag'] = top_hit['subject_locus_tag']
            row['top_hit_feature'] = top_hit['subject_feature']
            row['top_hit_id'] = top_hit['subject_id']
            row['top_hit_product'] = top_hit['subject_product']
            row['top_hit_valid'] = top_hit['valid']
            summary_df.append(row)

        summary_df = pd.DataFrame(summary_df).set_index('query_id')
        # Add the query IDs which had no search results to the summary DataFrame. 
        index = pd.Index(name='query_id', data=[id_ for id_ in query_df.index if (id_ not in summary_df.index)])
        summary_df = pd.concat([summary_df, pd.DataFrame(index=index, columns=summary_df.columns)])
        summary_df = summary_df.set_index('query_id')
        summary_df = summary_df.loc[query_df.index] # Make sure the DataFrames are in the same order for convenience.

        return fillna(summary_df, rules={bool:False, str:'none', int:0}, check=True)



    # def _add_start_stop_codons(self):
    #     self.df = self.add_start_stop_codons(self.df)

    # def _add_lengths(self):
    #     # Can't just use seq.apply(len) because forcing the sequences to be strings causes null sequences (e.g., in the case of non-CDS features) to be 'nan'.'''
    #     # This also gets lengths for pseudogenes. 
    #     lengths = list()
    #     for row in self.df.itertuples():
    #         if (row.feature == 'CDS'):
    #             lengths.append((row.stop - row.start) // 3) # The start and stop indices are in terms of nucleotides. 
    #         else:
    #             lengths.append(None)
    #     self.df['length'] = lengths 


    # def get_nt_seq(self, start:int=None, stop:int=None, strand:int=None, contig_id:int=None, error:str='ignore'):
    #     nt_seq = self.contigs[contig_id] 
    #     # Pretty sure the stop position is non-inclusive, so need to shift it over.
    #     nt_seq = nt_seq[start - 1:stop] 
    #     nt_seq = str(Seq(nt_seq).reverse_complement()) if (strand == -1) else nt_seq # If on the opposite strand, get the reverse complement. 

    #     if( len(nt_seq) % 3 == 0) and (error == 'raise'):
    #         raise Exception(f'GBFFFile.get_nt_seq: Expected the length of the nucleotide sequence to be divisible by three, but sequence is of length {len(nt_seq)}.')

    #     return nt_seq

    # def get_stop_codon(self, start:int=None, stop:int=None, strand:int=None, contig_id:int=None, **kwargs) -> str:
    #     return self.get_nt_seq(start=start, stop=stop, strand=strand, contig_id=contig_id)[-3:]
    
    # def get_start_codon(self, start:int=None, stop:int=None, strand:int=None, contig_id:int=None, **kwargs) -> str:
    #     return self.get_nt_seq(start=start, stop=stop, strand=strand, contig_id=contig_id)[:3]

    # def add_start_stop_codons(self, df:pd.DataFrame) -> pd.DataFrame:
    #     '''Add start and stop codons to the input DataFrame. Assumes the DataFrame contains, at least, columns for the 
    #     nucleotide start and stop positions, the strand, and the contig ID.'''
    #     start_codons, stop_codons = ['ATG', 'GTG', 'TTG'], ['TAA', 'TAG', 'TGA']

    #     df['stop_codon'] = [self.get_stop_codon(**row) for row in df.to_dict(orient='records')]
    #     df['start_codon'] = [self.get_start_codon(**row) for row in df.to_dict(orient='records')]

    #     # df.stop_codon = df.stop_codon.apply(lambda c : 'none' if (c not in stop_codons) else c)
    #     # df.start_codon = df.start_codon.apply(lambda c : 'none' if (c not in start_codons) else c)

    #     return df

