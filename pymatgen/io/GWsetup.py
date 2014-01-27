#!/usr/bin/env python

"""
Script to test writing GW Input for VASP.
Reads the POSCAR_name in the the current folder and outputs GW input to
subfolders name
"""

from __future__ import division

__author__ = "Michiel van Setten"
__copyright__ = " "
__version__ = "0.9"
__maintainer__ = "Michiel van Setten"
__email__ = "mjvansetten@gmail.com"
__date__ = "Oct 23, 2013"

import os
import stat
import json
import os.path
import pymatgen as pmg

from pymatgen.core.structure import Structure
from pymatgen.io.vaspio.vasp_input import Kpoints, Potcar, Poscar
from pymatgen.io.vaspio_set import DictVaspInputSet
from pymatgen.matproj.rest import MPRester
from pymatgen.io.abinitio.abiobjects import asabistructure
from pymatgen.io.abinitio.calculations import g0w0_with_ppmodel_extended
from pymatgen.io.abinitio.flows import AbinitFlow
from pymatgen.io.abinitio.tasks import TaskManager
from pymatgen.symmetry.finder import SymmetryFinder
from pymatgen.serializers.json_coders import MSONable
from pymatgen.io.abinitio.pseudos import PseudoTable

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))

"""
MPGWVaspInputSet.joson contains the standards for GW calculations. This set contains all
parameters for the first sc dft calculation. The modifications for the subsequent
sub calculations are made below.
For many settings the number of cores on which the calculations will be run is needed, this
number is assumed to be on the environment variable NPARGWCALC.
"""


class MPGWscDFTPrepVaspInputSet(DictVaspInputSet):
    """
    Should go to Pymatgen vaspinputsets
    Implementation of VaspInputSet overriding MaterialsProjectVaspInputSet
    for static calculations preparing for a GW calculation.
    """
    TESTS = {}

    def __init__(self, structure, functional='PBE', sym_prec=0.01, **kwargs):
        """
        Supports the same kwargs as :class:`JSONVaspInputSet`.
        """
        with open(os.path.join(MODULE_DIR, "MPGWVaspInputSet.json")) as f:
            DictVaspInputSet.__init__(
                self, "MP Static Self consistent run for GW", json.load(f), **kwargs)
        self.structure = structure
        self.tests = self.__class__.get_defaults_tests()
        self.functional = functional
        self.sym_prec = sym_prec
    #  todo update the fromdict and todict ot include the new atributes

    @classmethod
    def get_defaults_tests(cls):
        return cls.TESTS.copy()

    def get_npar(self, structure):
        """
        get 'optimally' useful number of parallelism
        """
        npar = int(self.get_bands(structure) ** 2 * structure.volume / 600)
        npar = min(max(npar, 1), 52)
        return npar

    def set_test(self, _test_):
        """
        Method to switch a specific test on
        """
        all_tests = MPGWscDFTPrepVaspInputSet.get_defaults_tests()
        all_tests.update(MPGWDFTDiagVaspInputSet.get_defaults_tests())
        all_tests.update(MPGWG0W0VaspInputSet.get_defaults_tests())
        test_type = all_tests[_test_.keys()[0]]['method']
        npar = self.get_npar(self.structure)
        if test_type == 'incar_settings':
            self.incar_settings.update(_test_)
        if test_type == 'set_nomega':
            nomega = npar * int(_test_['NOMEGA'] / npar)
            self.incar_settings.update({"NOMEGA": int(nomega)})
        if test_type == 'set_nbands':
            nbands = _test_['NBANDS'] * self.get_bands(self.structure)
            nbands = npar * int(nbands / npar + 1)
            self.incar_settings.update({"NBANDS": int(nbands)})
        if test_type == 'kpoint_grid':
            pass

    def get_potcar(self, structure):
        """
        Method for getting LDA potcars
        """
        if self.sort_structure:
            structure = structure.get_sorted_structure()
        return Potcar(self.get_potcar_symbols(structure), functional=self.functional)

    def get_kpoints(self, structure):
        """
        Writes out a KPOINTS file using the automated gamma grid method.
        VASP crashes GW calculations on none gamma centered meshes.
        """
        if self.sort_structure:
            structure = structure.get_sorted_structure()
        dens = int(self.kpoints_settings['grid_density'])
        return Kpoints.automatic_gamma_density(structure, dens)

    def set_dens(self, spec):
        """
        sets the grid_density to the value specified in spec
        """
        self.kpoints_settings['grid_density'] = spec['kp_grid_dens']

    def get_electrons(self, structure):
        """
        Method for retrieving the number of valence electrons
        """
        valence_list = {}
        potcar = self.get_potcar(structure)
        for pot_single in potcar:
            valence_list.update({pot_single.element: pot_single.nelectrons})
        electrons = sum([valence_list[element.symbol] for element in structure.species])
        return int(electrons)

    def get_bands(self, structure):
        """
        Method for retrieving the standard number of bands
        """
        bands = self.get_electrons(structure) / 2 + len(structure)
        return int(bands)

    def set_test_calc(self):
        """
        absolute minimal setting for testing
        """
        self.incar_settings.update({"PREC": "low", "ENCUT": 250})
        self.kpoints_settings['grid_density'] = 1

    def set_prec_high(self):
        self.incar_settings.update({"PREC": "Accurate", "ENCUT": 400})


class MPGWDFTDiagVaspInputSet(MPGWscDFTPrepVaspInputSet):
    """
    Should go to Pymatgen vaspinputsets
    Implementation of VaspInputSet overriding MaterialsProjectVaspInputSet
    for static non self-consistend exact diagonalization step preparing for
    a GW calculation.
    """
    TESTS = {'NBANDS': {'test_range': (10, 20, 30), 'method': 'set_nbands', 'control': "gap"}}

    def __init__(self, structure, functional='PBE', sym_prec=0.01, **kwargs):
        """
        Supports the same kwargs as :class:`JSONVaspInputSet`.
        """
        with open(os.path.join(MODULE_DIR, "MPGWVaspInputSet.json")) as f:
            DictVaspInputSet.__init__(
                self, "MP Static exact diagonalization", json.load(f), **kwargs)
        self.structure = structure
        self.tests = self.__class__.get_defaults_tests()
        self.functional = functional
        self.sym_prec = sym_prec
        npar = self.get_npar(self.structure)
        #single step exact diagonalization, output WAVEDER
        self.incar_settings.update({"ALGO": "Exact", "NELM": 1, "LOPTICS": "TRUE"})
        # for large systems exact diagonalization consumes too much memory
        self.set_gw_bands(15)
        self.incar_settings.update({"NPAR": npar})

    def get_kpoints(self, structure, regular=True):
        """
        Writes out a KPOINTS file using the automated gamma grid method.
        VASP crashes GW calculations on none gamma centered meshes.
        """
        if regular:
            if self.sort_structure:
                structure = structure.get_sorted_structure()
            dens = int(self.kpoints_settings['grid_density'])
            return Kpoints.automatic_gamma_density(structure, dens)
        else:
            num_kpoints = self.kpoints_settings["kpoints_density"] * structure.lattice.reciprocal_lattice.volume
            kpoints = Kpoints.automatic_density(structure, num_kpoints * structure.num_sites)
            mesh = kpoints.kpts[0]
            ir_kpts = SymmetryFinder(structure, symprec=0.0).get_ir_reciprocal_mesh(mesh)
            kpts = []
            weights = []
            for k in ir_kpts:
                kpts.append(k[0])
                weights.append(int(k[1]))
            # add the extrema
            kpts.append(structure.cbm)
            weights.append(int(0))
            kpts.append(structure.vbm)
            weights.append(int(0))
            return Kpoints(comment="uniform grid with extrema", style="Reciprocal", num_kpts=len(ir_kpts), kpts=kpts,
                           kpts_weights=weights)

    def set_gw_bands(self, factor=15):
        """
        method to set the number of bands for GW
        """
        gw_bands = self.get_bands(self.structure)
        gw_bands = self.get_npar(self.structure) * int((factor * gw_bands) / self.get_npar(self.structure) + 1)
        self.incar_settings.update({"NBANDS": gw_bands})
        if gw_bands > 800:
            self.incar_settings.update({"ALGO": 'fast'})

    def set_prec_high(self):
        super(MPGWDFTDiagVaspInputSet, self).set_prec_high()
        self.set_gw_bands(30)


class MPGWG0W0VaspInputSet(MPGWDFTDiagVaspInputSet):
    """
    Should go to Pymatgen vaspinputsets
    Implementation of VaspInputSet overriding MaterialsProjectVaspInputSet
    for static G0W0 calculation
    """
    TESTS = {'ENCUTGW': {'test_range': (200, 300, 400), 'method': 'incar_settings', 'control': "gap"},
             'NOMEGA': {'test_range': (80, 100, 120), 'method': 'set_nomega', 'control': "gap"}}

    @classmethod
    def get_defaults_tests(cls):
        return cls.TESTS.copy()

    def __init__(self, structure, functional='PBE', sym_prec=0.01, **kwargs):
        """
        Supports the same kwargs as :class:`JSONVaspInputSet`.
        """
        with open(os.path.join(MODULE_DIR, "MPGWVaspInputSet.json")) as f:
            DictVaspInputSet.__init__(
                self, "MP Static G0W0", json.load(f), **kwargs)
        self.structure = structure
        self.tests = self.__class__.get_defaults_tests()
        self.functional = functional
        self.sym_prec = sym_prec
        npar = self.get_npar(structure)
        # G0W0 calculation with reduced cutoff for the response function
        self.incar_settings.update({"ALGO": "GW0", "ENCUTGW": 250, "LWAVE": "FALSE", "NELM": 1})
        self.nomega_max = 25 * self.get_kpoints(structure).kpts[0][0]
        nomega = npar * int(self.nomega_max / npar)
        self.set_gw_bands(15)
        self.incar_settings.update({"NPAR": npar})
        self.incar_settings.update({"NOMEGA": nomega})
        self.incar_settings.update({"LWANNIER90_RUN": ".TRUE."})
        self.incar_settings.update({"LWRITE_MMN_AMN": ".TRUE."})
        self.tests = self.__class__.get_defaults_tests()

    def spectral_off(self):
        """
        Method to switch the use of the spectral decomposition of the response function of
        this may be used to reduce memory demands if the calculation crashes due to memory shortage
        """
        self.incar_settings.update({"LSPECTRAL": ".False."})

    def gw0_on(self, niter=4, gwbandsfac=4, qpsc=False):
        """
        Method to switch to gw0 calculation with standard 4 iterations
        """
        # set the number of iterations of GW0
        self.incar_settings.update({"NELM": niter})
        # set the number of bands to update in the iteration of G
        npar = self.get_npar(self.structure)
        nbandsgw = self.get_bands(self.structure)*gwbandsfac
        nbandsgw = npar * int(nbandsgw / npar)
        self.incar_settings.update({"NBANDSGW": nbandsgw})
        # if set also updat the orbitals 'quasi particle self-consistency'
        if qpsc:
            self.incar_settings.update({"ALGO": "scGW0"})
        # todo update tests ....

    def set_prec_high(self):
        super(MPGWG0W0VaspInputSet, self).set_prec_high()
        self.incar_settings.update({"ENCUTGW": 400, "NOMEGA": int(self.incar_settings["NOMEGA"]*1.5)})
        self.incar_settings.update({"PRECFOCK": "accurate"})


class Wannier90InputSet():
    """
    Should go to Pymatgen vaspinputsets
    class containing the imput parameters for the wannier90.win file
    """
    def __init__(self):
        self.file_name = "wannier90.win"
        self.settings = {"bands_plot": "true", "num_wann": 2, "num_bands": 2}
        self.parameters = {"n_include_bands": 1}

    def make_kpoint_path(self, structure, f):
        f.write("\nbegin kpoint_path\n")
        line = str(structure.vbm_l) + " " + str(structure.vbm[0]) + " " + str(structure.vbm[1]) + " " + str(structure.vbm[2])
        line = line + " " + str(structure.cbm_l) + " " + str(structure.cbm[0]) + " " + str(structure.cbm[1]) + " " + str(structure.cbm[2])
        f.write(line)
        f.write("\nend kpoint_path\n\n")
        pass

    def make_exclude_bands(self, structure, f):
        nocc = MPGWscDFTPrepVaspInputSet(structure).get_electrons(structure) / 2
        n1 = str(int(1))
        n2 = str(int(nocc - self.parameters["n_include_bands"]))
        n3 = str(int(nocc + 1 + self.parameters["n_include_bands"]))
        n4 = str(int(MPGWG0W0VaspInputSet(structure).incar_settings["NBANDS"]))
        line = "exclude_bands : " + n1 + "-" + n2 + ", " + n3 + "-" + n4 + "\n"
        f.write(line)
        pass

    def write_file(self, structure, path):
        f = open(os.path.join(path, self.file_name), mode='w')
        f.write("bands_plot = ")
        f.write(self.settings["bands_plot"])
        f.write("\n")
        self.make_kpoint_path(structure, f)
        f.write("num_wann  = ")
        f.write(str(self.settings["num_wann"]))
        f.write("\n")
        f.write("num_bands = ")
        f.write(str(self.settings["num_bands"]))
        f.write("\n")
        self.make_exclude_bands(structure, f)
        f.close()


    #begin projections
    #V:dxy;dxz;dyz
    #end projections


class SingleVaspGWWork():
    """
    Should go to uclworks
    Create VASP input for a single standard G0W0 and GW0 calculations
    """
    def __init__(self, structure, job, spec, option=None):
        """

        """
        self.structure = structure
        self.job = job
        self.spec = spec
        self.option = option

    def create_input(self):
        """
        create vasp input
        """
        if self.option is not None:
            option_prep_name = str(self.option[0])
            option_name = str(self.option[1])
            for char in ["'", ":", " ", ",", "{", "}"]:
                option_prep_name = option_prep_name.replace(char, "")
                option_name = option_name.replace(char, "")
            if len(option_prep_name) > 0:
                option_prep_name = "." + option_prep_name
            if len(option_name) > 0:
                option_name = "." + option_name
        else:
            option_name = option_prep_name = ''
        path = self.structure.composition.reduced_formula+option_prep_name
        if self.job == 'prep':
            inpset = MPGWscDFTPrepVaspInputSet(self.structure, functional=self.spec['functional'])
            inpset.set_dens(self.spec)
            if self.spec['test']:
                if self.option[0].keys()[0] in MPGWscDFTPrepVaspInputSet(self.structure).tests.keys():
                    inpset.set_test(self.option[0])
            if self.spec["prec"] == "h":
                inpset.set_prec_high()
            inpset.write_input(self.structure, path)
            inpset = MPGWDFTDiagVaspInputSet(self.structure, functional=self.spec['functional'])
            if self.spec["prec"] == "h":
                inpset.set_prec_high()
            if self.spec['test']:
                inpset.set_test(self.option[0])
            inpset.get_incar(self.structure).write_file(os.path.join(path, 'INCAR.DIAG'))
        if self.job == 'G0W0':
            inpset = MPGWG0W0VaspInputSet(self.structure, functional=self.spec['functional'])
            inpset.set_dens(self.spec)
            if self.spec['test']:
                inpset.set_test(self.option[0])
                inpset.set_test(self.option[1])
            if self.spec["prec"] == "h":
                inpset.set_prec_high()
            inpset.write_input(self.structure, os.path.join(path, 'G0W0'+option_name))
            w_inpset = Wannier90InputSet()
            w_inpset.write_file(self.structure, os.path.join(path, 'G0W0'+option_name))
        if self.job == 'GW0':
            inpset = MPGWG0W0VaspInputSet(self.structure, functional=self.spec['functional'])
            inpset.set_dens(self.spec)
            inpset.gw0_on()
            if self.spec['test']:
                inpset.set_test(self.option[0])
                inpset.set_test(self.option[1])
            if self.spec["prec"] == "h":
                inpset.set_prec_high()
            inpset.write_input(self.structure, os.path.join(path, 'GW0'+option_name))
            w_inpset = Wannier90InputSet()
            w_inpset.write_file(self.structure, os.path.join(path, 'G0W0'+option_name))
        if self.job == 'scGW0':
            inpset = MPGWG0W0VaspInputSet(self.structure, functional=self.spec['functional'])
            inpset.gw0_on(qpsc=True)
            inpset.set_dens(self.spec)
            if self.spec['test']:
                inpset.set_test(self.option[0])
                inpset.set_test(self.option[1])
            if self.spec["prec"] == "h":
                inpset.set_prec_high()
            inpset.write_input(self.structure, os.path.join(path, 'scGW0'+option_name))
            w_inpset = Wannier90InputSet()
            w_inpset.write_file(self.structure, os.path.join(path, 'G0W0'+option_name))

    def create_job_script(self, add_to_collection=True):
        """
        Create job script for ceci.
        """
        npar = MPGWscDFTPrepVaspInputSet(self.structure, functional=self.spec['functional']).get_npar(self.structure)
        if self.option is not None:
            option_prep_name = str(self.option[0])
            option_name = str(self.option[1])
            for char in ["'", ":", " ", ",", "{", "}"]:
                option_prep_name = option_prep_name.replace(char, "")
                option_name = option_name.replace(char, "")
            if len(option_prep_name) > 0:
                option_prep_name = "." + option_prep_name
            if len(option_name) > 0:
                option_name = "." + option_name
        else:
            option_prep_name = option_name = ''
        # npar = int(os.environ['NPARGWCALC'])
        header = ("#!/bin/bash \n"
                  "## standard header for Ceci clusters ## \n"
                  "#SBATCH --mail-user=michiel.vansetten@uclouvain.be \n"
                  "#SBATCH --mail-type=ALL\n"
                  "#SBATCH --time=2-24:0:0 \n"
                  "#SBATCH --cpus-per-task=1 \n"
                  "#SBATCH --mem-per-cpu=4000 \n")
        if self.job == 'prep':
            path = self.structure.composition.reduced_formula + option_prep_name
            # create this job
            job_file = open(name=path+'/job', mode='w')
            job_file.write(header)
            job_file.write('#SBATCH --job-name='+self.structure.composition.reduced_formula+self.job+'\n')
            job_file.write('#SBATCH --ntasks='+str(npar)+'\n')
            job_file.write('module load vasp \n')
            job_file.write('mpirun vasp \n')
            job_file.write('cp OUTCAR OUTCAR.sc \n')
            job_file.write('cp INCAR.DIAG INCAR \n')
            job_file.write('mpirun vasp \n')
            job_file.write('cp OUTCAR OUTCAR.diag \n')
            job_file.close()
            os.chmod(path+'/job', stat.S_IRWXU)
            if add_to_collection:
                job_file = open("job_collection", mode='a')
                job_file.write('cd ' + path + ' \n')
                job_file.write('sbatch job \n')
                job_file.write('cd .. \n')
                job_file.close()
                os.chmod("job_collection", stat.S_IRWXU)
        if self.job in ['G0W0', 'GW0', 'scGW0']:
            path = self.structure.composition.reduced_formula + option_prep_name + '/' + self.job + option_name
            # create this job
            job_file = open(name=path+'/job', mode='w')
            job_file.write(header)
            job_file.write('#SBATCH --job-name='+self.structure.composition.reduced_formula+self.job+'\n')
            job_file.write('#SBATCH --ntasks='+str(npar)+'\n')
            job_file.write('module load vasp/5.2_par_wannier90 \n')
            job_file.write('cp ../CHGCAR ../WAVECAR ../WAVEDER . \n')
            job_file.write('mpirun vasp \n')
            job_file.write('rm W* \n')
            #job_file.write('workon pymatgen-GW; get_gap > gap; deactivate')
            #job_file.write('echo '+path+'`get_gap` >> ../../gaps.dat')
            job_file.close()
            os.chmod(path+'/job', stat.S_IRWXU)
            path = self.structure.composition.reduced_formula + option_prep_name
            # 'append submission of this job script to that of prep for this structure'
            if add_to_collection:
                job_file = open(name=path+'/job', mode='a')
                job_file.write('cd ' + self.job + option_name + ' \n')
                job_file.write('sbatch job \n')
                job_file.write('cd .. \n')
                job_file.close()


class SingleAbinitGWWorkFlow():
    """
    interface the
    """
    TESTS = {'ecuteps': {'test_range': (8, 12, 16), 'method': 'direct', 'control': "gap", 'level': "sigma"},
             'nscf_nbands': {'test_range': (10, 20, 30), 'method': 'direct', 'control': "gap", 'level': "nscf"}}

    def __init__(self, structure, spec):
        self.structure = structure
        self.spec = spec
        self.work_dir = self.structure.composition.reduced_formula
        abi_pseudo = '.GGA_PBE-JTH-paw.xml'
        abi_pseudo_dir = os.path.join(os.environ['ABINIT_PS'], 'GGA_PBE-JTH-paw')
        pseudos = []
        for element in self.structure.composition.element_composition:
            pseudo = os.path.join(abi_pseudo_dir, str(element) + abi_pseudo)
            print pseudo, element
            pseudos.append(pseudo)
        self.pseudo_table = PseudoTable(pseudos)

    @classmethod
    def get_defaults_tests(cls):
        return cls.TESTS.copy()

    def get_electrons(self, structure):
        """
        Method for retrieving the number of valence electrons
        """
        electrons = 0

        for element in structure.species:
            entries = self.pseudo_table.pseudos_with_symbol(element.symbol)
            assert len(entries) == 1
            pseudo = entries[0]
            electrons += pseudo.Z_val
        return electrons
        # todo still copy from vasp

    def get_bands(self, structure):
        """
        Method for retrieving the standard number of bands
        """
        bands = self.get_electrons(structure) / 2 + len(structure)
        return int(bands)

    def create(self):
        """
        create single abinit G0W0 flow
        """
        manager = 'slurm' if 'ceci' in self.spec['mode'] else 'shell'
        print manager

        abi_structure = asabistructure(self.structure)
        manager = TaskManager.from_user_config()
        # Initialize the flow.
        # FIXME
        # Don't know why protocol=-1 does not work here.
        flow = AbinitFlow(self.work_dir, manager, pickle_protocol=0)

        # kpoint grid defined over density 40 > ~ 3 3 3
        scf_kppa = self.spec.data['kp_grid_dens']
        gamma = True
        # alternatively:
        #nscf_ngkpt = [4,4,4]
        #nscf_shiftk = [0.0, 0.0, 0.0]

        # 100
        nscf_nband = 100
        #scr_nband = 50 takes nscf_nbands if not specified
        #sigma_nband = 50 takes scr_nbands if not specified

        # 6
        ecuteps = 8
        # 8
        ecutsigx = 8
        # 8
        ecut = 12

        extra_abivars = dict(
            ecut=ecut,
            istwfk="*1",
            timopt=-1,
            pawecutdg=ecut*2,
            paral_kgb=0,
            nbdbuf=8
        )

        work = g0w0_with_ppmodel_extended(abi_structure, self.pseudo_table, scf_kppa, nscf_nband, ecuteps, ecutsigx,
                                          accuracy="normal", spin_mode="unpolarized", smearing=None, ppmodel="godby",
                                          charge=0.0, inclvkb=2, sigma_nband=None, scr_nband=None, gamma=gamma,
                                          **extra_abivars)

        flow.register_work(work)
        return flow.allocate()

    def create_job_file(self):
        job_file = open("job_collection", mode='a')
        job_file.write('nohup abirun.py ' + self.work_dir + ' scheduler > ' + self.work_dir + '.log & \n')
        job_file.close()


class GWSpecs(MSONable):
    """
    main program
    Class for GW specifications
    """
    def __init__(self):
        self.data = {'mode': 'ceci', 'jobs': ['prep', 'G0W0'], 'test': False, 'source': 'mp-vasp', 'code': 'VASP',
                     'functional': 'PBE', 'kp_grid_dens': 500, 'prec': 'm'}
        self.warnings = []
        self.errors = []

    def __getitem__(self, item):
        return self.data[item]

    def to_dict(self):
        return self.data

    def from_dict(self, data):
        self.data = data
        self.test()

    def reset_job_collection(self):
        if 'ceci' in self.data['mode']:
            if os.path.isfile('job_collection'):
                os.remove('job_collection')
            if 'ABINIT' in self.data['code']:
                job_file = open('job_collection', mode='w')
                job_file.write('module load abinit \n')
                job_file.close()

    def update_interactive(self):
        """
        method to make changes to the GW input setting interactively
        """
        key = 'tmp'
        while len(key) != 0:
            print self.data
            key = raw_input('enter key to change: ')
            if key in self.data.keys():
                value = raw_input('enter new value: ')
                if key == 'jobs':
                    if len(value) == 0:
                        print 'removed', self.data['jobs'].pop(-1)
                    else:
                        self.data['jobs'].append(value)
                elif key == 'test':
                    if value.lower() in ['true', 't']:
                        self.data['test'] = True
                    elif value.lower() in ['false', 'f']:
                        self.data['test'] = False
                    else:
                        print 'undefined value, test should be True or False'
                elif key in 'kp_grid_dens':
                    self.data[key] = int(value)
                else:
                    self.data[key] = value
            elif key in ['help', 'h']:
                print "source:       poscar, mp-vasp, any other will be interpreted as a filename to read mp-id's from"
                print "              poscar will read files starting with POSCAR_ in the working folder"
                print 'mode:         input, ceci, fw'
                print 'functional:   PBE, LDA'
                print 'jobs:         prep, G0W0, GW0, scGW0'
                print 'code:         VASP, ABINIT'
                print 'kp_grid_dens: usually 500 - 1000'
                print 'prec:         l, m, h NOT IMPLEMENTED YET'
            elif len(key) == 0:
                print 'setup finished'
            else:
                print 'undefined key'
        self.data['functional'] = self.data['functional'].upper()

    def get_code(self):
        return self['code']

    def test(self):
        if self.data['mode'].lower() not in ['input', 'ceci', 'fw']:
            self.errors.append('unspecified mode')
        if self.data['code'] == 'VASP':
            if self.data['functional'] not in ['PBE', 'LDA']:
                self.errors.append(str(self.data['functional'] + 'not defined for VASP yet'))
        elif self.data['code'] == 'ABINIT':
            if self.data['test'] and self.data['code'] == 'ABINIT':
                self.warnings.append('no tests defined for ABINIT calculations')
            if self.data['functional'] not in ['PBE']:
                self.errors.append(str(self.data['functional'] + 'not defined for ABINIT yet'))
        else:
            self.errors.append('unknown code')
        if self.data["source"] not in ['poscar', 'mp-vasp']:
            if not os.path.isfile(self.data['source']):
                self.errors.append('no structures defined')
        if len(self.errors) > 0:
            print str(len(self.errors)) + ' error(s) found:'
            print self.errors
            exit()
        if len(self.warnings) > 0:
            print str(len(self.warnings)) + ' warning(s) found:'
            print self.warnings
        self.reset_job_collection()

    def excecute_flow(self, structure):
        """
        excecute spec prepare input/jobfiles or submit to fw for a given structure
        for vasp the different jobs are created into a flow
        for abinit a flow is created using abinitio
        """
        if self.get_code() == 'VASP':
            if self.data['test']:
                tests_prep = MPGWscDFTPrepVaspInputSet(structure).tests
                tests_prep.update(MPGWDFTDiagVaspInputSet(structure).tests)
                for test_prep in tests_prep:
                    print 'setting up test for: ' + test_prep
                    for value_prep in tests_prep[test_prep]['test_range']:
                        print "**" + str(value_prep) + "**"
                        option = [{test_prep: value_prep}, {}]
                        self.create_job(structure, 'prep', option)
                        for job in self.data['jobs'][1:]:
                            if job == 'G0W0':
                                tests = MPGWG0W0VaspInputSet(structure).tests
                            if job in ['GW0', 'scGW0']:
                                input_set = MPGWG0W0VaspInputSet(structure)
                                input_set.gw0_on()
                                tests = input_set.tests
                            for test in tests:
                                print '    setting up test for: ' + test
                                for value in tests[test]['test_range']:
                                    print "    **" + str(value) + "**"
                                    option = [{test_prep: value_prep}, {test: value}]
                                    self.create_job(structure, job, option)
            else:
                for job in self['jobs']:
                    self.create_job(structure, job)
        elif self.get_code() == 'ABINIT':
            if self.data['test']:
                pass
            else:
                work_flow = SingleAbinitGWWorkFlow(structure, self)
                flow = work_flow.create()
                flow.build_and_pickle_dump()
                work_flow.create_job_file()
        else:
            print 'unspecified code, actually this should have been catched earlier .. '
            exit()

    def create_job(self, structure, job, option=None):
        work = SingleVaspGWWork(structure, job, self, option)
        if 'input' in self.data['mode'] or 'ceci' in self.data['mode']:
            work.create_input()
            if 'ceci' in self.data['mode']:
                work.create_job_script()
        if 'fw' in self.data['mode']:
            structure_dict = structure.to_dict
            band_structure_dict = {'vbm_l': structure.vbm_l, 'cbm_l': structure.cbm_l, 'vbm_a': structure.vbm[0],
                                   'vbm_b': structure.vbm[1], 'vbm_c': structure.vbm[2], 'cbm_a': structure.cbm[0],
                                   'cbm_b': structure.cbm[1], 'cbm_c': structure.cbm[2]}
            fire_works_spec = {'structure': structure_dict, 'band_structure': band_structure_dict, 'job': job,
                               'spec': self.to_dict()}
            for test in fire_works_spec.keys():
                print test
                if isinstance(fire_works_spec[test], dict):
                    print fire_works_spec[test].keys()


def folder_name(option):
    """
    method to return the sub folder name
    """
    option_prep_name = str(option[0])
    option_name = str(option[1])
    for char in ["'", ":", " ", ",", "{", "}"]:
        option_prep_name = option_prep_name.replace(char, "")
        option_name = option_name.replace(char, "")
    if len(option_prep_name) > 0:
        option_prep_name = "." + option_prep_name
    if len(option_name) > 0:
        option_name = "." + option_name
    return [option_prep_name, option_name]


def main(spec):
    """
    reading the structures specified in spec, add special points, and excecute the specs
    """

    mp_key = os.environ['MP_KEY']

    mp_list_vasp = ['mp-149', 'mp-2534', 'mp-8062', 'mp-2469', 'mp-1550', 'mp-830', 'mp-510626', 'mp-10695', 'mp-66',
                    'mp-1639', 'mp-1265', 'mp-1138', 'mp-23155', 'mp-111']

    if spec['source'] == 'mp-vasp':
        items_list = mp_list_vasp
    elif spec['source'] == 'poscar':
        files = os.listdir('.')
        items_list = files
    else:
        items_list = [line.strip() for line in open(spec['source'])]

    for item in items_list:
        if item.startswith('POSCAR_'):
            structure = pmg.read_structure(item)
            comment = Poscar.from_file(item).comment
            print comment
            if comment.startswith("gap"):
                structure.vbm_l = comment.split(" ")[1]
                structure.vbm = (comment.split(" ")[2], comment.split(" ")[3], comment.split(" ")[4])
                structure.cbm_l = comment.split(" ")[5]
                structure.cbm = (comment.split(" ")[6], comment.split(" ")[7], comment.split(" ")[8])
            else:
                print "no bandstructure information available, adding GG as 'gap'"
                structure.vbm_l = "G"
                structure.cbm_l = "G"
                structure.cbm = (0.0, 0.0, 0.0)
                structure.vbm = (0.0, 0.0, 0.0)
        elif item.startswith('mp-'):
            print item
            with MPRester(mp_key) as mp_database:
                structure = mp_database.get_structure_by_material_id(item, final=True)
                bandstructure = mp_database.get_bandstructure_by_material_id(item)
                structure.vbm_l = bandstructure.kpoints[bandstructure.get_vbm()['kpoint_index'][0]].label
                structure.cbm_l = bandstructure.kpoints[bandstructure.get_cbm()['kpoint_index'][0]].label
                structure.cbm = tuple(bandstructure.kpoints[bandstructure.get_cbm()['kpoint_index'][0]].frac_coords)
                structure.vbm = tuple(bandstructure.kpoints[bandstructure.get_vbm()['kpoint_index'][0]].frac_coords)
        else:
            next(item)
        print structure.composition.reduced_formula
        spec.excecute_flow(structure)
    if 'ceci' in spec['mode']:
        os.chmod("job_collection", stat.S_IRWXU)


if __name__ == "__main__":
    spec_in = GWSpecs()
    spec_in.update_interactive()
    spec_in.test()
    main(spec=spec_in)