"""
This module contains objects for postprocessing polaron calculations
using the results stored in the VARPEQ.nc file.

For a theoretical introduction see ...
"""
from __future__ import annotations

import dataclasses
import numpy as np
import pandas as pd
import abipy.core.abinit_units as abu

from collections import defaultdict
from monty.string import marquee #, list_strings
from monty.functools import lazy_property
from monty.termcolor import cprint
from abipy.core.func1d import Function1D
from abipy.core.structure import Structure
from abipy.core.kpoints import kpoints_indices, map_grid2ibz, kmesh_from_mpdivs
from abipy.core.mixins import AbinitNcFile, Has_Structure, Has_ElectronBands, NotebookWriter
from abipy.tools.typing import PathLike
from abipy.tools.plotting import (add_fig_kwargs, get_ax_fig_plt, get_axarray_fig_plt, set_axlims, set_visible,
    rotate_ticklabels, ax_append_title, set_ax_xylabels, linestyles, Marker, set_grid_legend)
#from abipy.tools import duck
from abipy.electrons.ebands import ElectronBands, RobotWithEbands
from abipy.dfpt.phonons import PhononBands
from abipy.dfpt.ddb import DdbFile
from abipy.tools.typing import Figure
from abipy.tools.numtools import BzRegularGridInterpolator, gaussian
from abipy.iotools import bxsf_write
from abipy.abio.robots import Robot
from abipy.eph.common import BaseEphReader


#TODO Finalize the implementation. Look at Pedro's implementation for GFR
from abipy.electrons.effmass_analyzer import EffMassAnalyzer

class FrohlichAnalyzer:

    def __init__(self, gsr_kpath, ddb, verbose = 0, **anaddb_kwargs):
        """
        """
        ebands_kpath = ElectronBands.as_ebands(gsr_kpath)
        self.ddb = DdbFile.as_ddb(ddb)
        self.verbose = verbose

        r = self.ddb.anaget_epsinf_and_becs(verbose=verbose, return_input=True, **anaddb_kwargs)
        self.epsinf, self.becs = r.epsinf, r.becs

        self.diel_gen, diel_inp = self.ddb.anaget_dielectric_tensor_generator(verbose=verbose, return_input=True, **anaddb_kwargs)
        self.eps0_tensor = self.diel_gen.tensor_at_frequency(0.0)

        # Spherical average of eps_inf and eps_0 (real part only)
        einf_savg, e0_savg = self.epsinf.trace() / 3, self.eps0_tensor.real.trace() / 3

        self.kappa = 1 / (1/einf_savg - 1/e0_savg)

        self.emana = EffMassAnalyzer(ebands_kpath)

    def __str__(self) -> str:
        return self.to_string()

    def to_string(self, verbose: int = 0) -> str:
        """String representation with verbosity level verbose"""
        lines = []
        app = lines.append

        app("epsilon_infinity in Cartesian coordinates:")
        app(str(self.epsinf))
        app("BECS:")
        app(str(self.becs))
        app("eps0 tensor:")
        app(str(self.eps0_tensor))
        app(f"kappa = {self.kappa}")

        return "\n".join(lines)

    def analyze_band_edges(self):
        self.emana.select_cbm()
        self.emana.summarize()
        self.emana.plot_emass()
        self.emana.select_vbm()
        self.emana.summarize()
        self.emana.plot_emass()
        #self.emana.select_band_edges()


@dataclasses.dataclass(kw_only=True)
class Entry:
    name: str
    latex: str
    info: str


_ALL_ENTRIES = [
    Entry(name="E_pol", latex=r'$E_{pol}$', info=""),
    Entry(name="E_el", latex=r'$E_{el}$', info=""),
    Entry(name="E_ph", latex=r'$E_{ph}$', info=""),
    Entry(name="elph", latex=r'$E_{elph}$', info=""),
    Entry(name="epsilon", latex=r"$\varepsilon$", info=""),
]

# Convert to dictionary: name --> Entry
_ALL_ENTRIES = {e.name: e for e in _ALL_ENTRIES}

# TODO:
# Handle multiple states.

class VarpeqFile(AbinitNcFile, Has_Structure, Has_ElectronBands, NotebookWriter):
    """
    This file stores the results of a VARPEQ calculations: SCF cycle, A_nk, B_qnu
    and provides methods to analyze and plot results.

    Usage example:

    .. code-block:: python

        from abipy.eph.varpeq import VarpeqFile
        with VarpeqFile("out_VARPEQ.nc") as varpeq:
            print(varpeq)
            varpeq.plot_scf_cycle()

    .. rubric:: Inheritance Diagram
    .. inheritance-diagram:: VarpeqFile
    """

    @classmethod
    def from_file(cls, filepath: PathLike) -> VarpeqFile:
        """Initialize the object from a netcdf file."""
        return cls(filepath)

    def __init__(self, filepath: PathLike):
        super().__init__(filepath)
        self.r = VarpeqReader(filepath)

    @lazy_property
    def ebands(self) -> ElectronBands:
        """|ElectronBands| object."""
        return self.r.read_ebands()

    @property
    def structure(self) -> Structure:
        """|Structure| object."""
        return self.ebands.structure

    def close(self) -> None:
        """Close the file."""
        self.r.close()

    @lazy_property
    def polaron_spin(self) -> list[Polaron]:
        """List of polaron objects, one for each spin (if any)."""
        return [Polaron.from_varpeq(self, spin) for spin in range(self.r.nsppol)]

    @lazy_property
    def params(self) -> dict:
        """dict with the convergence parameters, e.g. ``nbsum``."""
        #od = OrderedDict([
        #    ("nbsum", self.nbsum),
        #    ("nqbz", self.r.nqbz),
        #    ("nqibz", self.r.nqibz),
        #])
        ## Add EPH parameters.
        #od.update(self.r.common_eph_params)

        od = {}
        return od

    def __str__(self) -> str:
        return self.to_string()

    def to_string(self, verbose=0) -> str:
        """String representation with verbosiy level ``verbose``."""
        lines = []; app = lines.append

        app(marquee("File Info", mark="="))
        app(self.filestat(as_string=True))
        app("")
        app(self.structure.to_string(verbose=verbose, title="Structure"))
        app("")
        app(self.ebands.to_string(with_structure=False, verbose=verbose, title="Electronic Bands"))

        app("")
        app("VARPEQ parameters:")
        app(f"varpeq_pkind: {self.r.varpeq_pkind}")
        #app(f"gstore_completed: {bool(self.r.completed)}")
        #app(f"gstore_cplex: {self.r.cplex}")
        #app(f"gstore_kzone: {self.r.kzone}")
        #app(f"gstore_kfilter: {self.r.kfilter}")
        #app(f"gstore_gmode: {self.r.gmode}")
        #app(f"gstore_qzone: {self.r.qzone}")
        #app(f"gstore_with_vk: {self.r.with_vk}")
        #app(f"gstore_kptopt: {self.r.kptopt}")
        #app(f"gstore_qptopt: {self.r.qptopt}")

        return "\n".join(lines)

    def get_last_iteration_dict_ev(self, spin: int) -> dict:
        """
        Return dictionary mapping the latex label to the value of the last iteration
        for the given spin. All energies are in eV.
        """
        nstep2cv_spin = self.r.read_value('nstep2cv')
        iter_rec_spin = self.r.read_value('iter_rec')
        nstep2cv = nstep2cv_spin[spin]
        last_iteration = iter_rec_spin[spin, nstep2cv-1, :] * abu.Ha_eV

        return dict(zip(_ALL_ENTRIES.keys(), last_iteration))

    def get_title_spin(self, spin: int) -> str:
        """
        Return string with title for matplotlib plots.
        """
        pre = "" if self.ebands.nsppol == 1 else f"spin={self.spin}"
        if not with_gaps:
            return f"{pre}{self.r.varpeq_pkind}"
        else:
            gaps_string = self.varpeq.ebands.get_gaps_string()
            return f"{pre}{self.r.varpeq_pkind}, {gaps_string}"

    @add_fig_kwargs
    def plot_scf_cycle(self, ax_mat=None, fontsize=8, **kwargs) -> Figure:
        """
        Plot the VARPEQ SCF cycle.

        Args:
            ax_max: |matplotlib-Axes| or None if a new figure should be created.
            fontsize: fontsize for legends and titles
        """
        nsppol = self.r.nsppol
        nstep2cv_spin = self.r.read_value('nstep2cv')
        iter_rec_spin = self.r.read_value('iter_rec')

        #title = self.get_title() ??
        # Build grid of plots.
        nrows, ncols = nsppol, 2
        ax_mat, fig, plt = get_axarray_fig_plt(ax_mat, nrows=nrows, ncols=ncols,
                                               sharex=False, sharey=False, squeeze=False)

        for spin in range(nsppol):
            nstep2cv = nstep2cv_spin[spin]
            iterations = iter_rec_spin[spin, :nstep2cv, :] * abu.Ha_eV
            xs = np.arange(1, nstep2cv + 1)

            for iax, ax in enumerate(ax_mat[spin]):
                for ilab, (name, entry) in enumerate(_ALL_ENTRIES.items()):
                    ys = iterations[:,ilab]
                    if iax == 0:
                        # Plot energies in linear scale.
                        ax.plot(xs, ys, label=entry.latex)
                    else:
                        # Plot deltas in logscale.
                        ax.plot(xs, np.abs(ys - ys[-1]), label=entry.latex)
                        ax.set_yscale("log")

                ax.set_xlim(1, nstep2cv)
                set_grid_legend(ax, fontsize, xlabel="Iteration", title=self.get_title_spin(spin),
                                ylabel="Energy (eV)" if iax == 0 else r"$|\Delta|$ Energy (eV)",
                )

        return fig

    def yield_figs(self, **kwargs):  # pragma: no cover
        """
        This function *generates* a predefined list of matplotlib figures with minimal input from the user.
        """
        yield self.plot_scf_cycle(show=False)

    def write_notebook(self, nbpath=None) -> str:
        """
        Write a jupyter_ notebook to ``nbpath``. If nbpath is None, a temporay file in the current
        working directory is created. Return path to the notebook.
        """
        nbformat, nbv, nb = self.get_nbformat_nbv_nb(title=None)

        nb.cells.extend([
            nbv.new_code_cell("varpeq = abilab.abiopen('%s')" % self.filepath),
            nbv.new_code_cell("print(varpeq)"),
        ])

        return self._write_nb_nbpath(nb, nbpath)


@dataclasses.dataclass(kw_only=True)
class Polaron:
    """
    This object stores the polaron coefficients A_kn, B_qnu for a given spin.
    Provides methods to plot |A_nk|^2 or |B_qnu|^2 together with the band structures (fatbands-like plots).
    """

    spin: int          # Spin index.
    nb: int            # Number of bands in A_kn,
    nk: int            # Number of k-points in A_kn, (including filtering if any)
    nq: int            # Number of q-points in B_qnu (including filtering if any)
    bstart: int        # First band starts at bstart
    bstop: int         # Last band (python convention)

    kpoints: np.ndarray   # Reduced coordinates of the k-points
    qpoints: np.ndarray   # Reduced coordinates of the q-points
    a_kn: np.ndarray
    b_qnu: np.ndarray
    varpeq: VarpeqFile

    @classmethod
    def from_varpeq(cls, varpeq: VarpeqFile, spin: int) -> Polaron:
        """
        Build an istance from a VarpeqFile and the spin index.
        """
        r = varpeq.r
        nk, nq, nb = r.nk_spin[spin], r.nq_spin[spin], r.nb_spin[spin]
        bstart, bstop = r.brange_spin[spin]
        kpoints = r.read_value("kpts_spin")[spin, :nk]
        qpoints = r.read_value("qpts_spin")[spin, :nq]
        a_kn = r.read_value("a_spin", cmode="c")[spin, :nk, :nb]
        b_qnu = r.read_value("b_spin", cmode="c")[spin, :nq]
        data = locals()

        return cls(**{k: data[k] for k in [field.name for field in dataclasses.fields(Polaron)]})

    @property
    def structure(self) -> Structure:
        """Crystalline structure."""
        return self.varpeq.structure

    @property
    def ebands(self) -> ElectronBands:
        """Electron bands."""
        return self.varpeq.ebands

    def __str__(self) -> str:
        return self.to_string()

    def to_string(self, verbose=0) -> str:
        """
        String representation with verbosiy level verbose.
        """
        lines = []; app = lines.append

        app(marquee(f"Ank for spin: {self.spin}", mark="="))
        app(f"nb: {self.nb}")
        app(f"nk: {self.nk}")
        app(f"nq: {self.nq}")
        app(f"bstart: {self.bstart}")
        app(f"bstop: {self.bstop}")
        ksampling = self.ebands.kpoints.ksampling
        ngkpt, shifts = ksampling.mpdivs, ksampling.shifts
        app(f"ksampling: {str(ksampling)}")
        ngqpt = self.varpeq.r.ngqpt
        app(f"q-mesh: {ngqpt}")
        #if verbose:
        norm = np.sum(np.abs(self.a_kn) ** 2) / self.nk
        app("1/N_k sum_{nk} |A_nk|^2: %f" % norm)

        return "\n".join(lines)

    def get_title(self, with_gaps: bool=True) -> str:
        """
        Return string with title for matplotlib plots.
        """
        varpeq = self.varpeq
        pre = "" if varpeq.ebands.nsppol == 1 else f"spin={self.spin}"
        if not with_gaps:
            return f"{pre}{varpeq.r.varpeq_pkind}"
        else:
            gaps_string = self.varpeq.ebands.get_gaps_string()
            return f"{pre}{varpeq.r.varpeq_pkind}, {gaps_string}"

    @lazy_property
    def ngkpt_and_shifts(self):
        """
        Return k-mesh divisions and shifts.
        """
        ksampling = self.ebands.kpoints.ksampling
        ngkpt, shifts = ksampling.mpdivs, ksampling.shifts

        if ngkpt is None:
            raise ValueError("Non diagonal k-meshes are not supported!")

        if len(shifts) > 1:
            raise ValueError("Multiple k-shifts are not supported!")

        return ngkpt, shifts

    def insert_a_inbox(self, fill_value=None):
        """
        """
        # Need to know the size of the k-mesh.
        ngkpt, shifts = self.ngkpt_and_shifts
        k_indices = kpoints_indices(self.kpoints, ngkpt)
        nx, ny, nz = ngkpt

        shape = (self.nb, nx, ny, nz)
        if fill_value is None:
            a_data = np.empty(shape, dtype=complex)
        else:
            a_data = np.full(shape, fill_value, dtype=complex)

        for ib in range(self.nb):
            for a_cplx, k_inds in zip(self.a_kn[:,ib], k_indices):
                ix, iy, iz = k_inds
                a_data[ib, ix, iy, iz] = a_cplx

        return a_data, ngkpt, shifts

    def insert_b_inbox(self, fill_value=None):
        """
        """
        # Need to know the shape of the q-mesh (always Gamma-centered)
        ngqpt, shifts = self.varpeq.r.ngqpt, [0, 0, 0]
        q_indices = kpoints_indices(self.qpoints, ngqpt)

        natom3 = 3 * len(self.structure)
        nx, ny, nz = ngqpt

        shape = (natom3, nx, ny, nz)
        if fill_value is None:
            b_data = np.empty(shape, dtype=complex)
        else:
            b_data = np.full(shape, fill_value, dtype=complex)

        for nu in range(natom3):
            for b_cplx, q_inds in zip(self.b_qnu[:,nu], q_indices):
                ix, iy, iz = q_inds
                b_data[nu, ix, iy, iz] = b_cplx

        return b_data, ngqpt, shifts

    def get_a2_interpolator(self) -> BzRegularGridInterpolator:
        """
        Build and return an interpolator for |A_nk|^2
        """
        a_data, ngkpt, shifts = self.insert_a_inbox()

        return BzRegularGridInterpolator(self.structure, shifts, np.abs(a_data) ** 2, method="linear")

    def get_b2_interpolator(self) -> BzRegularGridInterpolator:
        """
        Build and return an interpolator for |B_qnu|^2.
        """
        b_data, ngqpt, shifts = self.insert_b_inbox()

        return BzRegularGridInterpolator(self.structure, shifts, np.abs(b_data) ** 2, method="linear")

    def write_a2_bxsf(self, filepath: PathLike) -> None:
        """
        Export |A_nk|^2 in BXSF format suitable for visualization with xcrysden (use ``xcrysden --bxsf FILE``).
        Require gamma-centered k-mesh.

        Args:
            filepath: BXSF filename.
        """
        # NB: kmesh must be gamma-centered, multiple shifts are not supported.
        # Init values with 0. This is relevant only if kfiltering is being used.
        a_data, ngkpt, shifts = self.insert_a_inbox(fill_value=0.0)
        nkbz = np.product(ngkpt)
        a2_data = np.abs(a_data) ** 2
        fermie = a2_data.mean()

        bxsf_write(filepath, self.structure, 1, num_states, ngkpt, a2_data, fermie, unit="Ha")

    def write_b2_bxsf(self, filepath: PathLike) -> None:
        """
        Export |B_qnu|^2 in BXSF format suitable for visualization with xcrysden (use ``xcrysden --bxsf FILE``).

        Args:
            filepath: BXSF filename.
        """
        # NB: qmesh must be gamma-centered, multiple shifts are not supported.
        # Init values with 0. This is relevant only if kfiltering is being used.
        b_data, ngqpt, shifts = self.insert_b_inbox(fill_value=0.0)
        nqbz = np.product(nqkpt)
        b2_data = np.abs(b_data) ** 2
        fermie = b2_data.mean()

        bxsf_write(filepath, self.structure, 1, num_states, ngqpt, b2_data, fermie, unit="Ha")

    #@add_fig_kwargs
    #def plot_bz_sampling(self, what="kpoints", fold=False,
    #                     ax=None, pmg_path=True, with_labels=True, **kwargs) -> Figure:
    #    """
    #    Plots a 3D representation of the Brillouin zone with the sampling.

    #    Args:
    #        what: "kpoints" or "qpoints"
    #        fold: whether the points should be folded inside the first Brillouin Zone.
    #            Defaults to False.
    #    """
    #    bz_points = dict(kpoints=self.kpoints, qpoints=self.qpoints)[what]
    #    kws = dict(ax=ax, pmg_path=pmg_path, with_labels=with_labels, fold=fold, kpoints=bz_points)

    #    return self.structure.plot_bz(show=False, **kws)

    @add_fig_kwargs
    def plot_ank_with_ebands(self, ebands_kpath, ebands_kmesh=None,
                             lpratio: int=5, method="gaussian", step: float=0.05, width: float=0.1,
                             nksmall: int=20, normalize: bool=False, with_title=True,
                             ax_list=None, ylims=None, scale=10, fontsize=12, **kwargs) -> Figure:
        """
        Plot electron bands with markers whose size is proportional to |A_nk|^2.

        Args:
            ebands_kpath: ElectronBands or Abipy file providing an electronic band structure along a k-path.
            ebands_kmesh: ElectronBands or Abipy file providing an electronic band structure with k in the IBZ.
            nksmall:
            normalize: Rescale the two DOS to plot them on the same scale.
            lpratio: Ratio between the number of star functions and the number of ab-initio k-points.
                The default should be OK in many systems, larger values may be required for accurate derivatives.
            method: Integration scheme for DOS
            step: Energy step (eV) of the linear mesh for DOS computation.
            width: Standard deviation (eV) of the gaussian for DOS computation.
            with_title: True to add title with chemical formula and gaps.
            ax_list: List of |matplotlib-Axes| or None if a new figure should be created.
            ylims: Set the data limits for the y-axis. Accept tuple e.g. ``(left, right)``
                   or scalar e.g. ``left``. If left (right) is None, default values are used.
            scale: Scaling factor for |A_nk|^2.
            fontsize: fontsize for legends and titles
        """
        ebands_kpath = ElectronBands.as_ebands(ebands_kpath)

        # Interpolate A_nk
        a2_interp = self.get_a2_interpolator()

        # DEBUG SECTION
        #ref_kn = np.abs(self.a_kn) ** 2
        #for ik, kpoint in enumerate(self.kpoints):
        #    interp = a2_interp.eval_kpoint(kpoint)
        #    print("MAX (A2 ref - A2 interp) at qpoint", kpoint)
        #    print((np.abs(ref_kn[ik] - interp)).max())

        ymin, ymax = +np.inf, -np.inf
        x, y, s = [], [], []
        for ik, kpoint in enumerate(ebands_kpath.kpoints):
            enes_n = ebands_kpath.eigens[self.spin, ik, self.bstart:self.bstop]
            a2_n = a2_interp.eval_kpoint(kpoint)
            for e, a2 in zip(enes_n, a2_n):
                x.append(ik); y.append(e); s.append(scale * a2)
                ymin = min(ymin, e)
                ymax = max(ymax, e)

        color = "gold"
        points = Marker(x, y, s, color=color, edgecolors='gray', alpha=0.8, label=r'$|A_{n\mathbf{k}}|^2$')

        nrows, ncols = 1, 2
        gridspec_kw = {'width_ratios': [2, 1]}
        ax_list, fig, plt = get_axarray_fig_plt(ax_list, nrows=nrows, ncols=ncols,
                                                sharex=False, sharey=True, squeeze=False, gridspec_kw=gridspec_kw)
        ax_list = ax_list.ravel()

        ax = ax_list[0]
        ebands_kpath.plot(ax=ax, points=points, show=False)
        ax.legend(loc="best", shadow=True, fontsize=fontsize)

        vertices_names = [(k.frac_coords, k.name) for k in ebands_kpath.kpoints]

        if ebands_kmesh is None:
            print(f"Computing ebands_kmesh with star-function interpolation and {nksmall=}")
            this_ngkpt = self.structure.calc_ngkpt(nksmall)
            r = self.ebands.interpolate(lpratio=lpratio, vertices_names=vertices_names, kmesh=this_ngkpt)
            ebands_kmesh = r.ebands_kmesh

        # Get electronic DOS from ebands_kmesh.
        edos_kws = dict(method=method, step=step, width=width)
        edos = ebands_kmesh.get_edos(**edos_kws)
        mesh = edos.spin_dos[self.spin].mesh
        ank_dos = np.zeros(len(mesh))
        e0 = self.ebands.fermie

        ##################
        # Compute A_nk DOS
        ##################
        # FIXME
        # NB: This is just to sketch the ideas. I don't think the present version
        # is correct as only the k --> -k symmetry can be used.

        for ik_ibz, kpoint in enumerate(ebands_kmesh.kpoints):
            weight = kpoint.weight
            enes_n = ebands_kmesh.eigens[self.spin, ik_ibz, self.bstart:self.bstop]
            a2_n = a2_interp.eval_kpoint(kpoint)
            for e, a2 in zip(enes_n, a2_n):
                ank_dos += weight * a2 * gaussian(mesh, width, center=e-e0)

        # TODO New version using the BZ. Requires new VARPEQ.nc file with all symmetries
        # The A_nk do not necessarily have the symmetry of the lattice so we have to loop over the full BZ.
        # Get mapping BZ --> IBZ needed to obtain the KS eigenvalues e_nk from the IBZ for the DOS
        """
        bz2ibz, bz_kpoints = ebands_kmesh.get_bz2ibz_bz_points()

        for ik_ibz, kpoint in zip(bz2ibz, bz_kpoints):
            enes_n = ebands_kmesh.eigens[self.spin, ik_ibz, self.bstart:self.bstop]
            a2_n = a2_interp.eval_kpoint(kpoint)
            for e, a2 in zip(enes_n, a2_n):
                ank_dos += a2 * gaussian(mesh, width, center=e-e0)
        ank_dos /= np.product(ngkpt)
        """

        ank_dos = Function1D(mesh, ank_dos)
        print("A2(E) integrates to:", ank_dos.integral_value, " Ideally, it should be 1.")

        edos_opts = {"color": "black",} if self.spin == 0 else {"color": "red"}

        ax = ax_list[1]
        edos.plot_ax(ax, e0, spin=self.spin, normalize=normalize, exchange_xy=True, label="eDOS(E)", **edos_opts)
        ank_dos.plot_ax(ax, exchange_xy=True, normalize=normalize, label=r"$A^2$(E)", color=color)
        set_grid_legend(ax, fontsize, xlabel="arb. unit")

        if ylims is None:
            # Automatic ylims.
            ymin -= 0.1 * abs(ymin)
            ymin -= e0
            ymax += 0.1 * abs(ymax)
            ymax -= e0
            ylims = [ymin, ymax]

        for ax in ax_list:
            set_axlims(ax, ylims, "y")

        if with_title:
            fig.suptitle(self.get_title(with_gaps=True))

        return fig

    @add_fig_kwargs
    def plot_bqnu_with_ddb(self, ddb, with_phdos=True, anaddb_kwargs=None, **kwargs) -> Figure:
        """
        High-level interface to plot phonon energies with markers whose size is proportional to |B_qnu|^2.
        Similar to plot_bqnu_with_phbands but this function receives in input a DdbFile or a
        path to a ddb file and automates the computation of the phonon bands by invoking anaddb.

        Args:
            ddb: DdbFile or path to file.
            with_phdos: True if phonon DOS should be computed and plotter.
            anaddb_kwargs: Optional arguments passed to anaddb.
        """
        ddb = DdbFile.as_ddb(ddb)
        anaddb_kwargs = {} if anaddb_kwargs is None else anaddb_kwargs

        with ddb.anaget_phbst_and_phdos_files(**anaddb_kwargs) as g:
            phbst_file, phdos_file = g[0], g[1]
            phbands_qpath = phbst_file.phbands
            return self.plot_bqnu_with_phbands(phbands_qpath,
                                               phdos_file=phdos_file if with_phdos else None,
                                               ddb=ddb,
                                               **kwargs)

    @add_fig_kwargs
    def plot_bqnu_with_phbands(self, phbands_qpath, phdos_file=None,
                               ddb=None, width=0.001, normalize: bool=True,
                               verbose=0, anaddb_kwargs=None, with_title=True,
                               ax_list=None, scale=10, fontsize=12, **kwargs) -> Figure:
        """
        Plot phonon energies with markers whose size is proportional to |B_qnu|^2.

        Args:
            phbands_qpath: PhononBands or Abipy file providing a phonon band structure.
            phdos_file:
            ddb: DdbFile or path to file.
            width: Standard deviation (eV) of the gaussian.
            normalize: Rescale the two DOS to plot them on the same scale.
            verbose:
            anaddb_kwargs: Optional arguments passed to anaddb.
            with_title: True to add title with chemical formula and gaps.
            ax_list: List of |matplotlib-Axes| or None if a new figure should be created.
            scale: Scaling factor for |B_qnu|^2.
            fontsize: fontsize for legends and titles.
        """
        with_phdos = phdos_file is not None  and ddb is not None
        nrows, ncols, gridspec_kw = 1, 1, None
        if with_phdos:
            ncols, gridspec_kw = 2, {'width_ratios': [2, 1]}

        ax_list, fig, plt = get_axarray_fig_plt(ax_list, nrows=nrows, ncols=ncols,
                                                sharex=False, sharey=True, squeeze=False, gridspec_kw=gridspec_kw)
        ax_list = ax_list.ravel()

        phbands_qpath = PhononBands.as_phbands(phbands_qpath)
        b2_interp = self.get_b2_interpolator()

        x, y, s = [], [], []
        for iq, qpoint in enumerate(phbands_qpath.qpoints):
            omegas_nu = phbands_qpath.phfreqs[iq,:]
            b2_nu = b2_interp.eval_kpoint(qpoint)
            for w, b2 in zip(omegas_nu, b2_nu):
                x.append(iq); y.append(w); s.append(scale * b2)

        ax = ax_list[0]
        color = "gold"
        points = Marker(x, y, s, color=color, edgecolors='gray', alpha=0.8, label=r'$|B_{\nu\mathbf{q}}|^2$')

        phbands_qpath.plot(ax=ax, points=points, show=False)
        ax.legend(loc="best", shadow=True, fontsize=fontsize)

        if not with_phdos:
            if with_title:
                fig.suptitle(self.get_title(with_gaps=True))
            return fig

        ##################
        # Compute B_qnu DOS
        ##################

        # Add phdos and |B_qn| dos. Mesh is given in eV, values are in states/eV.
        phdos = phdos_file.phdos
        ngqpt = np.diagonal(phdos_file.qptrlatt)
        mesh = phdos.mesh
        bqnu_dos = np.zeros(len(mesh))

        # Call anaddb (again) to get phonons on the nqpt mesh.
        anaddb_kwargs = {} if anaddb_kwargs is None else anaddb_kwargs
        phbands_qmesh = ddb.anaget_phmodes_at_qpoints(ngqpt=ngqpt, verbose=verbose, **anaddb_kwargs)

        # FIXME
        # NB: This is just to sketch the ideas. I don't think the present version
        # is correct as only the k --> -k symmetry can be used.
        for iq, qpoint in enumerate(phbands_qmesh.qpoints):
            weight = qpoint.weight
            freqs_nu = phbands_qmesh.phfreqs[iq]
            b2_nu = b2_interp.eval_kpoint(qpoint)
            for w, b2 in zip(freqs_nu, b2_nu):
                bqnu_dos += weight * b2 * gaussian(mesh, width, center=w)

        # TODO New version using the BZ. Requires new VARPEQ.nc file with all symmetries
        # The B_qnu do not necessarily have the symmetry of the lattice so we have to loop over the full BZ.
        # Get mapping BZ --> IBZ needed to obtain the KS eigenvalues e_nk from the IBZ for the DOS
        """
        shifts = [0, 0, 0]
        bz_qpoints = kmesh_from_mpdivs(ngqpt, shifts)
        bz2ibz = map_grid2ibz(self.structure, phbands_qmesh.qpoints.frac_coords, ngqpt, has_timrev)
        for iq_ibz, qpoint in zip(bz2ibz, bz_qpoints):
            freqs_nu = phbands_qmesh.phfreqs[iq_ibz]
            b2_nu = b2_interp.eval_kpoint(qpoint)
            for w, b2 in zip(freqs_nu, b2_nu):
                bqnu_dos += weight * b2 * gaussian(mesh, width, center=w)
        bqnu_dos /= np.product(ngqpt)
        """

        bqnu_dos = Function1D(mesh, bqnu_dos)

        ax = ax_list[1]
        phdos.plot_ax(ax, exchange_xy=True, normalize=normalize, label="phDOS(E)", color="black")
        bqnu_dos.plot_ax(ax, exchange_xy=True, normalize=normalize, label=r"$B^2$(E)", color=color)
        set_grid_legend(ax, fontsize, xlabel="arb. unit")

        if with_title:
            fig.suptitle(self.get_title(with_gaps=True))

        return fig


class VarpeqReader(BaseEphReader):
    """
    Reads data from file and constructs objects.

    .. rubric:: Inheritance Diagram
    .. inheritance-diagram:: VarpeqReader
    """

    def __init__(self, filepath: PathLike):
        super().__init__(filepath)
        """
        Netcdf Variables

        char input_string(input_len) ;
        int eph_task ;
        int varpeq_nstep ;
        int nkbz ;
        int nqbz ;
        double tolgrs ;
        int nstep2cv(nsppol) ;
        double iter_rec(nsppol, nstep, six) ;
        int nk_spin(nsppol) ;
        int nq_spin(nsppol) ;
        int nb_spin(nsppol) ;
        double kpts_spin(nsppol, max_nk, three) ;
        double qpts_spin(nsppol, max_nq, three) ;
        double a_spin(nsppol, max_nk, max_nb, two) ;
        double b_spin(nsppol, max_nq, natom3, two) ;
        """

        # Read important dimensions.
        self.nsppol = self.read_dimvalue("nsppol")
        self.nk_spin = self.read_value("nk_spin")
        self.nq_spin = self.read_value("nq_spin")
        #self.nb_spin = self.read_value("nb_spin")
        #self.nkbz = self.read_dimvalue("gstore_nkbz")
        #self.nkibz = self.read_dimvalue("gstore_nkibz")
        #self.nqbz = self.read_dimvalue("gstore_nqbz")
        #self.nqibz = self.read_dimvalue("gstore_nqibz")
        self.varpeq_pkind = self.read_string("varpeq_pkind")
        self.ngqpt = self.read_value("gstore_ngqpt")

        # Read important variables.
        #self.completed = self.read_value("gstore_completed")
        #self.done_spin_qbz = self.read_value("gstore_done_qbz_spin")
        #self.qptopt = self.read_value("gstore_qptopt")
        #self.kptopt = self.read_value("kptopt")
        #self.kzone = self.read_string("gstore_kzone")
        #self.qzone = self.read_string("gstore_qzone")
        #self.kfilter = self.read_string("gstore_kfilter")
        #self.gmode = self.read_string("gstore_gmode")

        # Note conversion Fortran --> C for the isym index.
        self.brange_spin = self.read_value("brange_spin")
        self.brange_spin[:,0] -= 1
        self.nb_spin = self.brange_spin[:,1] - self.brange_spin[:,0]
        #self.erange_spin = self.read_value("gstore_erange_spin")
        # Total number of k/q points for each spin after filtering (if any)
        #self.glob_spin_nq = self.read_value("gstore_glob_nq_spin")
        #self.glob_nk_spin = self.read_value("gstore_glob_nk_spin")

        # K-points and q-points in the IBZ
        #self.kibz = self.read_value("reduced_coordinates_of_kpoints")
        #self.qibz = self.read_value("gstore_qibz")

        # K-points and q-points in the BZ
        #self.kbz = self.read_value("gstore_kbz")
        #self.qbz = self.read_value("gstore_qbz")

        # Mapping BZ --> IBZ. Note conversion Fortran --> C for the isym index.
        # nctkarr_t("gstore_kbz2ibz", "i", "six, gstore_nkbz"), &
        # nctkarr_t("gstore_qbz2ibz", "i", "six, gstore_nqbz"), &
        #self.kbz2ibz = self.read_value("gstore_kbz2ibz")
        #self.kbz2ibz[:,0] -= 1

        #self.qbz2ibz = self.read_value("gstore_qbz2ibz")
        #self.qbz2ibz[:,0] -= 1

        # Mapping q/k points in gqk --> BZ. Note conversion Fortran --> C for indexing.
        # nctkarr_t("gstore_qglob2bz", "i", "gstore_max_nq, number_of_spins"), &
        # nctkarr_t("gstore_kglob2bz", "i", "gstore_max_nk, number_of_spins") &
        #self.qglob2bz = self.read_value("gstore_qglob2bz")
        #self.qglob2bz -= 1

        #self.kglob2bz = self.read_value("gstore_kglob2bz")
        #self.kglob2bz -= 1


class VarpeqRobot(Robot, RobotWithEbands):
    """
    This robot analyzes the results contained in multiple VARPEQ.nc files.

    Usage example:

    .. code-block:: python

        robot = VarpeqRobot.from_files([
            "out1_VARPEQ.nc",
            "out2_VARPEQ.nc",
            ])
        robot.plot_scf_cycle()

    .. rubric:: Inheritance Diagram
    .. inheritance-diagram:: VarpeqRobot
    """

    EXT = "VARPEQ"

    def get_kdata_spin(self, spin: int) -> dict:
        """
        Build and return dictionary with the different terms of the polaron energy
        Each entry in the dict is ordered ...
        """
        if warn_msg := self.has_different_structures():
            cprint(warn_msg, color="yellow")

        # First of all sort the files in reverse order using the total number of k-points in the mesh.
        def sort_func(abifile):
            ksampling = abifile.ebands.kpoints.ksampling
            ngkpt, shifts = ksampling.mpdivs, ksampling.shifts
            return np.prod(ngkpt)

        labels, abifiles, nktot_list = self.sortby(sort_func, reverse=True, unpack=True)

        # Now loop over the sorted files and extract the results of the final iteration.
        data = defaultdict(list)
        for i, (label, abifile, nktot) in enumerate(zip(labels, abifiles, nktot_list)):

            for k, v in abifile.get_last_iteration_dict_ev(spin).items():
                data[k].append(v)

            ksampling = abifile.ebands.kpoints.ksampling
            ngkpt, shifts = ksampling.mpdivs, ksampling.shifts
            nk_tot = np.prod(ngkpt)
            data["ngkpt"].append(ngkpt)
            data["nk_tot"].append(nk_tot)
            vol_ang = abifile.structure.lattice.volume * (abu.Ang_Bohr ** 3)
            x = 1.0 / (nk_tot * abifile.structure.lattice.volume ** (1/3))
            data["xs_inv_bohr"].append(x)

        #  Convert to numpy arrays. NB: energies are already in eV.
        return {k: np.array(v) for k, v in data.items()}

    def get_makov_payne_df_spin(self, spin: int) -> pd.DataFrame:
        """
        Build and return dataframe with extrapolated quantities.
        obtained using the firs npts points.
        """
        kdata = self.get_kdata_spin(spin)

        xs = kdata["xs_inv_bohr"]
        d = defaultdict(list)
        for ix, ylabel in enumerate(_ALL_ENTRIES):
            ys = kdata[ylabel]
            # Fit data using the first nn points.
            for nn in range(1, len(xs)):
                p = np.poly1d(np.polyfit(xs[:nn+1], ys[:nn+1], deg=1))
                d[ylabel].append(p(0))

        df = pd.DataFrame(d, index=list(i + 1 for i in range(1, len(xs))))
        df.index.name = 'npts'

        return df

    @add_fig_kwargs
    def plot_scf_cycle(self, **kwargs) -> Figure:
        """
        Plot the VARPEQ SCF cycle for all the files stored in the Robot.
        """
        nsppol = self.getattr_alleq("nsppol")

        # Build grid of plots.
        nrows, ncols = nsppol * len(self), 2
        ax_mat, fig, plt = get_axarray_fig_plt(None, nrows=nrows, ncols=ncols,
                                               sharex=True, sharey=True, squeeze=False)

        for ifile, abifile in enumerate(self.abifiles):
            row_start = nsppol * ifile
            row_stop = row_start + nsppol
            abifile.plot_scf_cycle(ax_mat=ax_mat[row_start:row_stop], show=False)

        return fig

    @add_fig_kwargs
    def plot_kconv(self, colormap="jet", fontsize=12, **kwargs) -> Figure:
        """
        Plot the convergence of the data wrt to the k-point sampling.

        Args:
            colormap: Color map. Have a look at the colormaps here and decide which one you like:
            fontsize: fontsize for legends and titles
        """
        nsppol = self.getattr_alleq("nsppol")

        # Build grid of plots.
        nrows, ncols = len(_ALL_ENTRIES), nsppol
        ax_mat, fig, plt = get_axarray_fig_plt(None, nrows=nrows, ncols=ncols,
                                               sharex=True, sharey=False, squeeze=False)
        cmap = plt.get_cmap(colormap)
        rows = []
        for spin in range(nsppol):
            kdata = self.get_kdata_spin(spin)
            xs = kdata["xs_inv_bohr"]
            xvals = np.linspace(0.0, 1.1 * xs.max(), 100)

            df = self.get_makov_payne_df_spin(spin)
            print(df)

            for ix, ylabel in enumerate(_ALL_ENTRIES):
                ax = ax_mat[ix, spin]
                ys = kdata[ylabel]

                # Plot ab-initio points.
                ax.scatter(xs, ys, color="red", marker="o")

                # Plot fit using the first nn points.
                for nn in range(1, len(xs)):
                    color = cmap((nn - 1) / len(xs))
                    p = np.poly1d(np.polyfit(xs[:nn+1], ys[:nn+1], deg=1))
                    ax.plot(xvals, p(xvals), color=color, ls="--")

                xlabel = "Inverse supercell size (Bohr$^-1$)" if ix == len(_ALL_ENTRIES) - 1 else None
                set_grid_legend(ax, fontsize, xlabel=xlabel, ylabel=f"{ylabel} (eV)", legend=False)
                #ax.tick_params(axis='x', color='black', labelsize='20', pad=5, length=5, width=2)

        #for ax in ax_mat.ravel():
        #    set_axlims(ax, (xs[0]-1e-3, xs[-1]), "x")

        return fig

    def yield_figs(self, **kwargs):  # pragma: no cover
        """
        This function *generates* a predefined list of matplotlib figures with minimal input from the user.
        Used in abiview.py to get a quick look at the results.
        """
        yield self.plot_scf_cycle(show=False)

    def write_notebook(self, nbpath=None) -> str:
        """
        Write a jupyter_ notebook to ``nbpath``. If nbpath is None, a temporary file in the current
        working directory is created. Return path to the notebook.
        """
        nbformat, nbv, nb = self.get_nbformat_nbv_nb(title=None)

        args = [(l, f.filepath) for l, f in self.items()]
        nb.cells.extend([
            #nbv.new_markdown_cell("# This is a markdown cell"),
            nbv.new_code_cell("robot = abilab.VarpeqRobot(*%s)\nrobot.trim_paths()\nrobot" % str(args)),
            #nbv.new_code_cell("ebands_plotter = robot.get_ebands_plotter()"),
        ])

        return self._write_nb_nbpath(nb, nbpath)


#def plot_data(kleninv, energy, p, str_label):
#    xrange = np.linspace(0, 1/6, 100)
#    plt.plot(kleninv, energy, 's-', label=str_label)
#    plt.plot(xrange, p(xrange), 'k--')
#    plt.xlim(0, 0.6)
#    plt.xlabel('Inverse k-grid')
#
#def interp_data(kleninv, energy, n):
#    fit = np.polyfit(kleninv[-n:], energy[-n:], 1)
#    p = np.poly1d(fit)
#    return p
#
#def transform_data(klen, enpol, eps):
#    kleninv = 1/klen
#    enpol = (enpol - cbm)*ha_ev
#    eps = -(eps - cbm)*ha_ev
#    return kleninv, enpol, eps
#
#def analyze(filename, mode, label):
#    klen, enpol, eps = get_data(filename)
#    kleninv, enpol, eps = transform_data(klen, enpol, eps)
#    p_enpol = interp_data(kleninv, enpol, 3)
#    p_eps = interp_data(kleninv, eps, 3)
#
#    if mode == 'enpol':
#        plot_data(kleninv, enpol, p_enpol, label)
#    elif mode == 'eps':
#        plot_data(kleninv, eps, p_eps, label)
#
#analyze('energy.dat', 'enpol', 'no sym')
#analyze('energy_ksym.dat', 'enpol', r'$g(Sk,q) = g(k,S^{-1}q)$')
#plt.ylabel('Hole polaron formation energy (eV)')

