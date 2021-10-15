import os
import datetime

import numpy as np
from typing import Mapping, Optional, Sequence, Any

from openfold.features import templates, parsers, mmcif_parsing
from openfold.features.np import jackhmmer, hhblits, hhsearch
from openfold.features.np.utils import to_date
from openfold.np import residue_constants


FeatureDict = Mapping[str, np.ndarray]

def make_sequence_features(
    sequence: str, 
    description: str, 
    num_res: int
) -> FeatureDict:
    """Construct a feature dict of sequence features."""
    features = {}
    features['aatype'] = residue_constants.sequence_to_onehot(
        sequence=sequence,
        mapping=residue_constants.restype_order_with_x,
        map_unknown_to_x=True
    )
    features['between_segment_residues'] = np.zeros((num_res,), dtype=np.int32)
    features['domain_name'] = np.array(
        [description.encode('utf-8')], dtype=np.object_
    )
    features['residue_index'] = np.array(range(num_res), dtype=np.int32)
    features['seq_length'] = np.array([num_res] * num_res, dtype=np.int32)
    features['sequence'] = np.array(
        [sequence.encode('utf-8')], dtype=np.object_
    )
    return features


def make_mmcif_features(
    mmcif_object: mmcif_parsing.MmcifObject,
    chain_id: str
) -> FeatureDict:
    input_sequence = mmcif_object.chain_to_seqres[chain_id]
    description = '_'.join([mmcif_object.file_id, chain_id])
    num_res = len(input_sequence)
    
    mmcif_feats = {}

    mmcif_feats.update(make_sequence_features(
        sequence=input_sequence,
        description=description,
        num_res=num_res,
    ))
    
    all_atom_positions, all_atom_mask = mmcif_parsing.get_atom_coords(
        mmcif_object=mmcif_object, chain_id=chain_id
    )
    mmcif_feats["all_atom_positions"] = all_atom_positions
    mmcif_feats["all_atom_mask"] = all_atom_mask
   
    mmcif_feats["resolution"] = np.array(
        [mmcif_object.header["resolution"]], dtype=np.float32
    )

    mmcif_feats["release_date"] = np.array(
        [mmcif_object.header["release_date"].encode('utf-8')], dtype=np.object_
    )

    return mmcif_feats


def make_msa_features(
        msas: Sequence[Sequence[str]],
        deletion_matrices: Sequence[parsers.DeletionMatrix]) -> FeatureDict:
    """Constructs a feature dict of MSA features."""
    if not msas:
        raise ValueError('At least one MSA must be provided.')

    int_msa = []
    deletion_matrix = []
    seen_sequences = set()
    for msa_index, msa in enumerate(msas):
        if not msa:
            raise ValueError(f'MSA {msa_index} must contain at least one sequence.')
        for sequence_index, sequence in enumerate(msa):
            if sequence in seen_sequences:
                continue
            seen_sequences.add(sequence)
            int_msa.append(
                [residue_constants.HHBLITS_AA_TO_ID[res] for res in sequence]
            )
            deletion_matrix.append(deletion_matrices[msa_index][sequence_index])

    num_res = len(msas[0][0])
    num_alignments = len(int_msa)
    features = {}
    features['deletion_matrix_int'] = np.array(deletion_matrix, dtype=np.int32)
    features['msa'] = np.array(int_msa, dtype=np.int32)
    features['num_alignments'] = np.array(
        [num_alignments] * num_res, dtype=np.int32
    )
    return features


class AlignmentRunner:
    """ Runs alignment tools and saves the results """
    def __init__(self,
         jackhmmer_binary_path: str,
         hhblits_binary_path: str,
         hhsearch_binary_path: str,
         uniref90_database_path: str,
         mgnify_database_path: str,
         bfd_database_path: Optional[str],
         uniclust30_database_path: Optional[str],
         small_bfd_database_path: Optional[str],
         pdb70_database_path: str,
         use_small_bfd: bool,
         no_cpus: int,
         uniref_max_hits: int = 10000,
         mgnify_max_hits: int = 5000,
    ):
        self._use_small_bfd = use_small_bfd
        self.jackhmmer_uniref90_runner = jackhmmer.Jackhmmer(
            binary_path=jackhmmer_binary_path,
            database_path=uniref90_database_path,
            n_cpu=no_cpus,
        )

        if use_small_bfd:
            self.jackhmmer_small_bfd_runner = jackhmmer.Jackhmmer(
                binary_path=jackhmmer_binary_path,
                database_path=small_bfd_database_path,
                n_cpu=no_cpus,
            )
        else:
            self.hhblits_bfd_uniclust_runner = hhblits.HHBlits(
                binary_path=hhblits_binary_path,
                databases=[bfd_database_path, uniclust30_database_path],
                n_cpu=no_cpus,
            )

        self.jackhmmer_mgnify_runner = jackhmmer.Jackhmmer(
            binary_path=jackhmmer_binary_path,
            database_path=mgnify_database_path,
            n_cpu=no_cpus,
        )

        self.hhsearch_pdb70_runner = hhsearch.HHSearch(
            binary_path=hhsearch_binary_path,
            databases=[pdb70_database_path]
        )
        self.uniref_max_hits = uniref_max_hits
        self.mgnify_max_hits = mgnify_max_hits

    def run(self,
        fasta_path: str,
        output_dir: str,
    ):
        """Runs alignment tools on a sequence"""
        jackhmmer_uniref90_result = self.jackhmmer_uniref90_runner.query(fasta_path)[0]
        uniref90_msa_as_a3m = parsers.convert_stockholm_to_a3m(
            jackhmmer_uniref90_result['sto'], max_sequences=self.uniref_max_hits
        )
        uniref90_out_path = os.path.join(output_dir, 'uniref90_hits.a3m')
        with open(uniref90_out_path, 'w') as f:
            f.write(uniref90_msa_as_a3m)

        jackhmmer_mgnify_result = self.jackhmmer_mgnify_runner.query(fasta_path)[0]
        mgnify_msa_as_a3m = parsers.convert_stockholm_to_a3m(
            jackhmmer_mgnify_result['sto'], max_sequences=self.mgnify_max_hits
        )
        mgnify_out_path = os.path.join(output_dir, 'mgnify_hits.a3m')
        with open(mgnify_out_path, 'w') as f:
            f.write(mgnify_msa_as_a3m)

        hhsearch_result = self.hhsearch_pdb70_runner.query(uniref90_msa_as_a3m)
        pdb70_out_path = os.path.join(output_dir, 'pdb70_hits.hhr')
        with open(pdb70_out_path, 'w') as f:
            f.write(hhsearch_result)

        if self._use_small_bfd:
            jackhmmer_small_bfd_result = self.jackhmmer_small_bfd_runner.query(fasta_path)[0]
            bfd_out_path = os.path.join(output_dir, 'small_bfd_hits.sto')
            with open(bfd_out_path, 'w') as f:
                f.write(jackhmmer_small_bfd_result['sto'])
        else:
            hhblits_bfd_uniclust_result = self.hhblits_bfd_uniclust_runner.query(fasta_path)
            if(output_dir is not None):
                bfd_out_path = os.path.join(output_dir, 'bfd_uniclust_hits.a3m')
                with open(bfd_out_path, 'w') as f:
                    f.write(hhblits_bfd_uniclust_result['a3m'])


class DataPipeline:
    """Assembles input features."""
    def __init__(self,
         template_featurizer: templates.TemplateHitFeaturizer,
         use_small_bfd: bool,
    ):
        self.template_featurizer = template_featurizer
        self.use_small_bfd = use_small_bfd

    def _parse_alignment_output(self,
        alignment_dir: str,
    ) -> Mapping[str, Any]:
        uniref90_out_path = os.path.join(alignment_dir, 'uniref90_hits.a3m')
        with open(uniref90_out_path, 'r') as f:
            uniref90_msa, uniref90_deletion_matrix = parsers.parse_a3m(
                f.read() 
            )

        mgnify_out_path = os.path.join(alignment_dir, 'mgnify_hits.a3m')
        with open(mgnify_out_path, 'r') as f:
            mgnify_msa, mgnify_deletion_matrix = parsers.parse_a3m(
                f.read()
            )

        pdb70_out_path = os.path.join(alignment_dir, 'pdb70_hits.hhr')
        with open(pdb70_out_path, 'r') as f:
            hhsearch_hits = parsers.parse_hhr(
                f.read()
            )

        if(self.use_small_bfd):
            bfd_out_path = os.path.join(alignment_dir, 'small_bfd_hits.sto')
            with open(bfd_out_path, 'r') as f:
                bfd_msa, bfd_deletion_matrix, _ = parsers.parse_stockholm(
                    f.read()
                )
        else:
            bfd_out_path = os.path.join(alignment_dir, 'bfd_uniclust_hits.a3m')
            with open(bfd_out_path, 'r') as f:
                bfd_msa, bfd_deletion_matrix = parsers.parse_a3m(
                    f.read()    
                )

        return {
            'uniref90_msa': uniref90_msa,
            'uniref90_deletion_matrix': uniref90_deletion_matrix,
            'mgnify_msa': mgnify_msa,
            'mgnify_deletion_matrix': mgnify_deletion_matrix,
            'hhsearch_hits': hhsearch_hits,
            'bfd_msa': bfd_msa,
            'bfd_deletion_matrix': bfd_deletion_matrix,
        }

    def process_fasta(self, 
        fasta_path: str,
        alignment_dir: str,
    ) -> FeatureDict:
        """Assembles features for a single sequence in a FASTA file"""
        with open(fasta_path) as f:
          fasta_str = f.read()
        input_seqs, input_descs = parsers.parse_fasta(fasta_str)
        if len(input_seqs) != 1:
          raise ValueError(
              f'More than one input sequence found in {fasta_path}.')
        input_sequence = input_seqs[0]
        input_description = input_descs[0]
        num_res = len(input_sequence)

        alignments = self._parse_alignment_output(alignment_dir)

        templates_result = self.template_featurizer.get_templates(
            query_sequence=input_sequence,
            query_pdb_code=None,
            query_release_date=None,
            hits=alignments['hhsearch_hits']
        )

        sequence_features = make_sequence_features(
            sequence=input_sequence,
            description=input_description,
            num_res=num_res
        )

        msa_features = make_msa_features(
            msas=(
                alignments['uniref90_msa'], 
                alignments['bfd_msa'], 
                alignments['mgnify_msa']
            ),
            deletion_matrices=(
                alignments['uniref90_deletion_matrix'],
                alignments['bfd_deletion_matrix'],
                alignments['mgnify_deletion_matrix']
            )
        )
        return {**sequence_features, **msa_features, **templates_result.features}

    def process_mmcif(self,
        mmcif: mmcif_parsing.MmcifObject, # parsing is expensive, so no path
        alignment_dir: str,
        chain_id: Optional[str] = None,
    ) -> FeatureDict:
        """
            Assembles features for a specific chain in an mmCIF object.

            If chain_id is None, it is assumed that there is only one chain
            in the object. Otherwise, a ValueError is thrown.
        """
        if(chain_id is None):
            chains = mmcif.structure.get_chains()
            chain = next(chains, None)
            if(chain is None):
                raise ValueError(
                    'No chains in mmCIF file'
                )
            chain_id = chain.id

        mmcif_feats = make_mmcif_features(mmcif, chain_id)

        alignments = self._parse_alignment_output(alignment_dir)

        input_sequence = mmcif.chain_to_seqres[chain_id]
        templates_result = self.template_featurizer.get_templates(
            query_sequence=input_sequence,
            query_pdb_code=None,
            query_release_date=to_date(mmcif.header["release_date"]),
            hits=alignments['hhsearch_hits']
        )

        msa_features = make_msa_features(
            msas=(
                alignments['uniref90_msa'], 
                alignments['bfd_msa'], 
                alignments['mgnify_msa']
            ),
            deletion_matrices = (
                alignments['uniref90_deletion_matrix'],
                alignments['bfd_deletion_matrix'],
                alignments['mgnify_deletion_matrix']
            )
        )

        return {**mmcif_feats, **templates_result.features, **msa_features}