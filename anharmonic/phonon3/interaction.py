import numpy as np
from anharmonic.other.phonon import get_dynamical_matrix, set_phonon_c, set_phonon_py
from phonopy.harmonic.dynamical_matrix import get_smallest_vectors
from phonopy.units import VaspToTHz, Hbar, EV, Angstrom, THz, AMU, THzToEv
from anharmonic.phonon3.real_to_reciprocal import RealToReciprocal
from anharmonic.phonon3.reciprocal_to_normal import ReciprocalToNormal
from anharmonic.phonon3.triplets import get_triplets_at_q, get_nosym_triplets_at_q, get_bz_grid_address

class Interaction:
    def __init__(self,
                 supercell,
                 primitive,
                 mesh,
                 symmetry,
                 fc3=None,
                 band_indices=None,
                 use_Peierls_model=False,
                 frequency_factor_to_THz=VaspToTHz,
                 is_nosym=False,
                 symmetrize_fc3_q=False,
                 cutoff_frequency=None,
                 lapack_zheev_uplo='L'):
        self._fc3 = fc3 
        self._supercell = supercell
        self._primitive = primitive
        self._mesh = np.array(mesh, dtype='intc')
        self._symmetry = symmetry
        num_band = primitive.get_number_of_atoms() * 3
        if band_indices is None:
            self._band_indices = np.arange(num_band, dtype='intc')
        else:
            self._band_indices = np.array(band_indices, dtype='intc')
        self._use_Peierls_model = use_Peierls_model
        self._frequency_factor_to_THz = frequency_factor_to_THz

        if cutoff_frequency is None:
            self._cutoff_frequency = 0
        else:
            self._cutoff_frequency = cutoff_frequency
        self._is_nosym = is_nosym
        self._symmetrize_fc3_q = symmetrize_fc3_q
        self._lapack_zheev_uplo = lapack_zheev_uplo

        self._symprec = symmetry.get_symmetry_tolerance()

        self._triplets_at_q = None
        self._weights_at_q = None
        self._triplets_map_at_q = None
        self._ir_map_at_q = None
        self._grid_address = None
        self._bz_map = None
        self._interaction_strength = None

        self._phonon_done = None
        self._frequencies = None
        self._eigenvectors = None
        self._dm = None
        self._nac_q_direction = None
        
        self._allocate_phonon()
        
    def run(self, lang='C'):
        num_band = self._primitive.get_number_of_atoms() * 3
        num_triplets = len(self._triplets_at_q)
        self._interaction_strength = np.zeros(
            (num_triplets, len(self._band_indices), num_band, num_band),
            dtype='double')

        if lang == 'C':
            self._run_c()
        else:
            self._run_py()

        if self._use_Peierls_model:
            self._set_Peierls_model_interaction()

    def get_interaction_strength(self):
        return self._interaction_strength

    def get_mesh_numbers(self):
        return self._mesh
    
    def get_phonons(self):
        return self._frequencies, self._eigenvectors, self._phonon_done

    def get_dynamical_matrix(self):
        return self._dm

    def get_primitive(self):
        return self._primitive

    def get_triplets_at_q(self):
        if self._triplets_map_at_q is None:
            return self._triplets_at_q, self._weights_at_q
        else:
            return (self._triplets_at_q,
                    self._weights_at_q,
                    self._triplets_map_at_q,
                    self._ir_map_at_q)

    def get_grid_address(self):
        return self._grid_address

    def get_bz_map(self):
        return self._bz_map
    
    def get_band_indices(self):
        return self._band_indices

    def get_frequency_factor_to_THz(self):
        return self._frequency_factor_to_THz

    def get_lapack_zheev_uplo(self):
        return self._lapack_zheev_uplo

    def is_nosym(self):
        return self._is_nosym

    def get_cutoff_frequency(self):
        return self._cutoff_frequency
        
    def set_grid_point(self, grid_point, stores_triplets_map=False):
        reciprocal_lattice = np.linalg.inv(self._primitive.get_cell())
        if self._is_nosym:
            (triplets_at_q,
             weights_at_q,
             grid_address,
             bz_map,
             triplets_map_at_q,
             ir_map_at_q) = get_nosym_triplets_at_q(
                 grid_point,
                 self._mesh,
                 reciprocal_lattice,
                 stores_triplets_map=stores_triplets_map)
        else:
            (triplets_at_q,
             weights_at_q,
             grid_address,
             bz_map,
             triplets_map_at_q,
             ir_map_at_q)= get_triplets_at_q(
                 grid_point,
                 self._mesh,
                 self._symmetry.get_pointgroup_operations(),
                 reciprocal_lattice,
                 stores_triplets_map=stores_triplets_map)

        for triplet in triplets_at_q:
            sum_q = (grid_address[triplet]).sum(axis=0)
            if (sum_q % self._mesh != 0).any():
                print "============= Warning =================="
                print triplet
                print grid_address[triplet]
                print sum_q
                print "============= Warning =================="

        self._triplets_at_q = triplets_at_q
        self._weights_at_q = weights_at_q
        self._triplets_map_at_q = triplets_map_at_q
        self._grid_address = grid_address
        self._bz_map = bz_map
        self._ir_map_at_q = ir_map_at_q

    def set_dynamical_matrix(self,
                             fc2,
                             supercell,
                             primitive,
                             nac_params=None,
                             frequency_scale_factor=None,
                             decimals=None):
        self._dm = get_dynamical_matrix(
            fc2,
            supercell,
            primitive,
            nac_params=nac_params,
            frequency_scale_factor=frequency_scale_factor,
            decimals=decimals,
            symprec=self._symprec)

    def set_nac_q_direction(self, nac_q_direction=None):
        if nac_q_direction is not None:
            self._nac_q_direction = np.array(nac_q_direction, dtype='double')

    def set_phonon(self, grid_points):
        # for i, grid_triplet in enumerate(self._triplets_at_q):
        #     for gp in grid_triplet:
        #         self._set_phonon_py(gp)
        self._set_phonon_c(grid_points)

    def get_mean_square_strength(self):
        unit_conversion = (
            (Hbar * EV) ** 3 / 36 / 8
            * EV ** 2 / Angstrom ** 6
            / (2 * np.pi * THz) ** 3
            / AMU ** 3 / np.prod(self._mesh)) / (THzToEv * EV) ** 2
        v = self._interaction_strength
        w = self._weights_at_q
        v_sum = v.sum(axis=2).sum(axis=2)
        return np.dot(w, v_sum) * unit_conversion
            
    def _run_c(self):
        import anharmonic._phono3py as phono3c
        
        self.set_phonon(self._triplets_at_q.ravel())
        num_band = self._primitive.get_number_of_atoms() * 3
        svecs, multiplicity = get_smallest_vectors(self._supercell,
                                                   self._primitive,
                                                   self._symprec)
        masses = np.array(self._primitive.get_masses(), dtype='double')
        p2s = self._primitive.get_primitive_to_supercell_map()
        s2p = self._primitive.get_supercell_to_primitive_map()

        phono3c.interaction(self._interaction_strength,
                            self._frequencies,
                            self._eigenvectors,
                            self._triplets_at_q,
                            self._grid_address,
                            self._mesh,
                            self._fc3,
                            svecs,
                            multiplicity,
                            masses,
                            p2s,
                            s2p,
                            self._band_indices,
                            self._symmetrize_fc3_q,
                            self._cutoff_frequency)

    def _set_phonon_c(self, grid_points):
        set_phonon_c(self._dm,
                     self._frequencies,
                     self._eigenvectors,
                     self._phonon_done,
                     grid_points,
                     self._grid_address,
                     self._mesh,
                     self._frequency_factor_to_THz,
                     self._nac_q_direction,
                     self._lapack_zheev_uplo)
        
    def _run_py(self):
        r2r = RealToReciprocal(self._fc3,
                               self._supercell,
                               self._primitive,
                               self._mesh,
                               symprec=self._symprec)
        r2n = ReciprocalToNormal(self._primitive,
                                 self._frequencies,
                                 self._eigenvectors,
                                 self._band_indices,
                                 cutoff_frequency=self._cutoff_frequency)

        for i, grid_triplet in enumerate(self._triplets_at_q):
            print "%d / %d" % (i + 1, len(self._triplets_at_q))
            r2r.run(self._grid_address[grid_triplet])
            fc3_reciprocal = r2r.get_fc3_reciprocal()
            for gp in grid_triplet:
                self._set_phonon_py(gp)
            r2n.run(fc3_reciprocal, grid_triplet)
            self._interaction_strength[i] = np.abs(
                r2n.get_reciprocal_to_normal()) ** 2

    def _set_phonon_py(self, grid_point):
        set_phonon_py(grid_point,
                      self._phonon_done,
                      self._frequencies,
                      self._eigenvectors,
                      self._grid_address,
                      self._mesh,
                      self._dm,
                      self._frequency_factor_to_THz,                  
                      self._lapack_zheev_uplo)

    def _allocate_phonon(self):
        primitive_lattice = np.linalg.inv(self._primitive.get_cell())
        self._grid_address, self._bz_map = get_bz_grid_address(
            self._mesh, primitive_lattice, with_boundary=True)
        num_band = self._primitive.get_number_of_atoms() * 3
        num_grid = len(self._grid_address)
        self._phonon_done = np.zeros(num_grid, dtype='byte')
        self._frequencies = np.zeros((num_grid, num_band), dtype='double')
        self._eigenvectors = np.zeros((num_grid, num_band, num_band),
                                      dtype='complex128')
        
    def _set_Peierls_model_interaction(self):
        """This gives averaged ph-ph interaction strength for each band. """
        
        v = self._interaction_strength.copy()
        divisor = np.prod(self._mesh)
        v_sum = np.dot(self._weights_at_q, v.sum(axis=2).sum(axis=2))
        v_ave = v_sum / divisor / np.prod(v.shape[2:])
        for i in range(v.shape[1]):
            self._interaction_strength[:, i, :, :] = v_ave[i]
        
