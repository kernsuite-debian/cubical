# CubiCal: a radio interferometric calibration suite
# (c) 2017 Rhodes University & Jonathan S. Kenyon
# http://github.com/ratt-ru/CubiCal
# This code is distributed under the terms of GPLv2, see LICENSE.md for details
from cubical.machines.interval_gain_machine import PerIntervalGains
import numpy as np
import cubical.kernels.cyphase_only as cyphase
from cubical.flagging import FL

class PhaseDiagGains(PerIntervalGains):
    """
    This class implements the diagonal phase-only gain machine.
    """
    def __init__(self, label, data_arr, ndir, nmod, chunk_ts, chunk_fs, chunk_label, options):
        """
        Initialises a diagonal phase-only gain machine.
        
        Args:
            label (str):
                Label identifying the Jones term.
            data_arr (np.ndarray): 
                Shape (n_mod, n_tim, n_fre, n_ant, n_ant, n_cor, n_cor) array containing observed 
                visibilities. 
            ndir (int):
                Number of directions.
            nmod (nmod):
                Number of models.
            chunk_ts (np.ndarray):
                Times for the data being processed.
            chunk_fs (np.ndarray):
                Frequencies for the data being processsed.
            options (dict): 
                Dictionary of options. 
        """
        
        PerIntervalGains.__init__(self, label, data_arr, ndir, nmod,
                                  chunk_ts, chunk_fs, chunk_label, options)

        self.phases = np.zeros(self.gain_shape, dtype=self.ftype)

        self.gains = np.empty_like(self.phases, dtype=self.dtype)
        self.gains[:] = np.eye(self.n_cor) 
        self.old_gains = self.gains.copy()


    def compute_js(self, obser_arr, model_arr):
        """
        This function computes the J\ :sup:`H`\R term of the GN/LM method. 

        Args:
            obser_arr (np.ndarray): 
                Shape (n_mod, n_tim, n_fre, n_ant, n_ant, n_cor, n_cor) array containing the 
                observed visibilities.
            model_arr (np.ndrray): 
                Shape (n_dir, n_mod, n_tim, n_fre, n_ant, n_ant, n_cor, n_cor) array containing the 
                model visibilities.

        Returns:
            np.ndarray:
                J\ :sup:`H`\R
        """

        n_dir, n_timint, n_freint, n_ant, n_cor, n_cor = self.gains.shape

        gh = self.gains.transpose(0,1,2,3,5,4).conj()

        jh = np.zeros_like(model_arr)

        cyphase.cycompute_jh(model_arr, self.gains, jh, self.t_int, self.f_int)

        jhr_shape = [n_dir, n_timint, n_freint, n_ant, n_cor, n_cor]

        jhr = np.zeros(jhr_shape, dtype=obser_arr.dtype)

        # TODO: This breaks with the new compute residual code for n_dir > 1. Will need a fix.
        if n_dir > 1:
            resid_arr = np.empty_like(obser_arr)
            r = self.compute_residual(obser_arr, model_arr, resid_arr)
        else:
            r = obser_arr

        cyphase.cycompute_jhr(gh, jh, r, jhr, self.t_int, self.f_int)

        return jhr.imag, self.jhjinv, 0


    @property
    def dof_per_antenna(self):
        """This property returns the number of real degrees of freedom per antenna, per solution interval"""
        return 2

    def implement_update(self, jhr, jhjinv):

        # variance of gain is diagonal of jhjinv
        self.posterior_gain_error = np.sqrt(jhjinv.real)

        update = np.zeros_like(jhr)

        cyphase.cycompute_update(jhr, jhjinv, update)

        if self.iters%2 == 0:
            self.phases += 0.5*update
        else:
            self.phases += update

        self.restrict_solution()

        self.gains = np.exp(1j*self.phases)
        self.gains[...,(0,1),(1,0)] = 0 

    def compute_residual(self, obser_arr, model_arr, resid_arr):
        """
        This function computes the residual. This is the difference between the
        observed data, and the model data with the gains applied to it.

        Args:
            obser_arr (np.ndarray): 
                Shape (n_mod, n_tim, n_fre, n_ant, n_ant, n_cor, n_cor) array containing the 
                observed visibilities.
            model_arr (np.ndrray): 
                Shape (n_dir, n_mod, n_tim, n_fre, n_ant, n_ant, n_cor, n_cor) array containing the 
                model visibilities.
            resid_arr (np.ndarray): 
                Shape (n_mod, n_tim, n_fre, n_ant, n_ant, n_cor, n_cor) array into which the 
                computed residuals should be placed.

        Returns:
            np.ndarray: 
                Array containing the result of computing D - GMG\ :sup:`H`.
        """
        
        gains_h = self.gains.transpose(0,1,2,3,5,4).conj()

        resid_arr[:] = obser_arr

        cyphase.cycompute_residual(model_arr, self.gains, gains_h, resid_arr, self.t_int, self.f_int)

        return resid_arr

    def apply_inv_gains(self, obser_arr, corr_vis=None):
        """
        Applies the inverse of the gain estimates to the observed data matrix.

        Args:
            obser_arr (np.ndarray): 
                Shape (n_mod, n_tim, n_fre, n_ant, n_ant, n_cor, n_cor) array containing the 
                observed visibilities.
            corr_vis (np.ndarray or None, optional): 
                if specified, shape (n_mod, n_tim, n_fre, n_ant, n_ant, n_cor, n_cor) array 
                into which the corrected visibilities should be placed.

        Returns:
            np.ndarray: 
                Array containing the result of G\ :sup:`-1`\DG\ :sup:`-H`.
        """

        g_inv = self.gains.conj()

        gh_inv = g_inv.conj()

        if corr_vis is None:                
            corr_vis = np.empty_like(obser_arr)

        cyphase.cycompute_corrected(obser_arr, g_inv, gh_inv, corr_vis, self.t_int, self.f_int)

        return corr_vis, 0   # no flags raised here, since phase-only always invertible

    def restrict_solution(self):
        """
        Restricts the solution by invoking the inherited restrict_soultion method and applying
        any machine specific restrictions.
        """

        PerIntervalGains.restrict_solution(self)

        if self.ref_ant is not None:
            self.phases -= self.phases[:,:,:,self.ref_ant,:,:][:,:,:,np.newaxis,:,:]
        for idir in self.fix_directions:
            self.phases[idir, ...] = 0


    def precompute_attributes(self, model_arr, flags_arr, noise):
        """
        Precompute (J\ :sup:`H`\J)\ :sup:`-1`, which does not vary with iteration.

        Args:
            model_arr (np.ndarray):
                Shape (n_dir, n_mod, n_tim, n_fre, n_ant, n_ant, n_cor, n_cor) array containing 
                model visibilities.
        """
        PerIntervalGains.precompute_attributes(self, model_arr, flags_arr, noise)

        self.jhjinv = np.zeros_like(self.gains)

        cyphase.cycompute_jhj(model_arr, self.jhjinv, self.t_int, self.f_int)

        cyphase.cycompute_jhjinv(self.jhjinv, self.gflags, self.eps, FL.ILLCOND)

        self.jhjinv = self.jhjinv.real

