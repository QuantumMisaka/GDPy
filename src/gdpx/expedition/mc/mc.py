#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import copy
import logging
import os
import re
import shutil
import pathlib
import pickle
import tarfile
from typing import NoReturn, List

import numpy as np

from ase import Atoms
from ase.io import read, write

from .. import registers
from .. import Variable
from .. import SingleWorker
from ..expedition import AbstractExpedition
from .operators import select_operator, parse_operators, save_operator, load_operator

"""This module tries to offer a base class for all MonteCarlo-like methods.
"""

class MonteCarloVariable(Variable):

    def __init__(self, builder, directory="./", *args, **kwargs) -> None:
        """"""
        # - builder
        if isinstance(builder, dict):
            builder_params = copy.deepcopy(builder)
            builder_method = builder_params.pop("method")
            builder = registers.create(
                "builder", builder_method, convert_name=False, **builder_params
            )
        else: # variable
            builder = builder.value

        # - engine
        engine = self._create_engine(builder, *args, **kwargs)
        engine.directory = directory
        super().__init__(initial_value=engine, directory=directory)

        return
    
    def _create_engine(self, builder, *args, **kwargs) -> None:
        """"""
        engine = MonteCarlo(builder, *args, **kwargs)

        return engine


class MonteCarlo(AbstractExpedition):

    restart = False

    #: Prefix of the working directory.
    WDIR_PREFIX: str = "cand"

    #: Name of the MC trajectory.
    TRAJ_NAME: str = "mc.xyz"

    #: Name of the file stores MC information (operations).
    INFO_NAME: str = "opstat.txt"

    def __init__(
        self, builder: dict, operators: List[dict], convergence: dict,
        random_seed=None, dump_period: int=1, ckpt_period: int=100, restart: bool=False, 
        directory="./", *args, **kwargs
    ) -> None:
        """Parameters for Monte Carlo.

        Args:
            overwrite: Whether overwrite calculation directory.
        
        """
        self.directory = directory
        self.dump_period = dump_period
        self.ckpt_period = ckpt_period
        self.restart = restart

        # - set random seed that 
        if random_seed is None:
            random_seed = np.random.randint(0, 10000)
        self.random_seed = random_seed
        self.rng = np.random.default_rng(seed=random_seed)

        # - check system type
        if isinstance(builder, dict):
            builder_params = copy.deepcopy(builder)
            builder_method = builder_params.pop("method")
            builder = registers.create(
                "builder", builder_method, convert_name=False, **builder_params
            )
        else:
            builder = builder
        self.builder = builder

        frames = builder.run()
        assert len(frames) == 1, f"{self.__class__.__name__} only accepts one structure."
        self.atoms = frames[0]

        # - create worker
        self.worker = None

        # - parse operators
        self.operators, self.op_probs = parse_operators(operators)

        # - parse convergence
        self.convergence = convergence
        if self.convergence.get("steps", None) is None:
            self.convergence["steps"] = 1

        return

    def _init_structure(self):
        """Initialise the input structure.

        Set proper tags and minimise the structure. Prepare `self.atoms`, 
        `self.energy_stored`, and `self.curr_step`.
        
        """
        step_wdir = self.directory / f"{self.WDIR_PREFIX}0"
        if not step_wdir.exists():
            # - prepare atoms
            self._print("===== MonteCarlo Structure =====\n")
            tags = self.atoms.arrays.get("tags", None)
            if tags is None:
                # default is setting tags by elements
                symbols = self.atoms.get_chemical_symbols()
                type_list = sorted(list(set(symbols)))
                new_tags = [type_list.index(s)*10000+i for i, s in enumerate(symbols)]
                self.atoms.set_tags(new_tags)
                self._print("set default tags by chemical symbols...")
            else:
                self._print("set attached tags from the structure...")

        # - run init
        self._print("===== MonteCarlo Initial Minimisation =====\n")
        # NOTE: atoms lost tags in optimisation
        #       TODO: move this part to driver?
        curr_tags = self.atoms.get_tags()

        self.atoms.info["confid"] = 0
        self.atoms.info["step"] = -1 # NOTE: remove step info

        # TODO: whether init driver?
        self.worker.wdir_name = step_wdir.name
        _ = self.worker.run([self.atoms])
        self.worker.inspect(resubmit=True)
        if self.worker.get_number_of_running_jobs() == 0:
            curr_frames = self.worker.retrieve()[0]
            # - update atoms
            curr_atoms = curr_frames[-1]
            self.energy_stored = curr_atoms.get_potential_energy()
            self._print(f"ene: {self.energy_stored}")
            self.atoms = curr_atoms
            self.atoms.set_tags(curr_tags)
            write(self.directory/self.TRAJ_NAME, self.atoms)

            # - 
            self.curr_step = 0

            # - log operator status
            with open(self.directory/self.INFO_NAME, "w") as fopen:
                fopen.write(
                    "{:<24s}  {:<24s}  {:<12s}  {:<12s}  {:<24s}  {:<24s}  \n".format(
                        "#Operator", "Info", "natoms", "Success", "prev_ene", "curr_ene"
                    )
                )
            step_converged = True
        else:
            step_converged = False

        return step_converged
    
    def run(self, *args, **kwargs):
        """Run MonteCarlo simulation."""
        # - some imported packages change `logging.basicConfig` 
        #   and accidently add a StreamHandler to logging.root
        #   so remove it...
        for h in logging.root.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                logging.root.removeHandler(h)
            
        # - check if it has a valid worker..
        assert self.worker is not None, "MC has not set its worker properly."
        assert isinstance(self.worker, SingleWorker), f"{self.__class__.__name__} only supports SingleWorker (set use_single=True)."
        self.worker.directory = self.directory

        # - prepare logger and output some basic info...
        if not self.directory.exists():
            self.directory.mkdir(parents=True)
        
        self._print("===== MonteCarlo Operators (Modifiers) =====\n")
        for op in self.operators:
            for x in str(op).split("\n"):
                self._print(x)
        self._print(f"normalised probabilities {self.op_probs}\n")

        # -- add print function to operators
        for op in self.operators:
            op._print = self._print

        # NOTE: check if operators' regions are consistent
        #       though it works, unexpected results may occur
        # TODO: need rewrite eq function as compare array is difficult
        #noperators = len(self.operators)
        #for i in range(1,noperators):
        #    if self.operators[i].region != self.operators[i-1].region:
        #        raise RuntimeError(f"Inconsistent region found in op {i-1} and op {i}")

        converged = self.read_convergence()
        if not converged:
            # - init structure
            # -- 
            step_converged = False
            if not self._veri_checkpoint():
                step_converged = self._init_structure()
            else:
                step_converged = True
                self._load_checkpoint()

            if not step_converged:
                self._print("Wait structure to initialise.")
                return
            else:
                self.curr_step += 1

            # - run mc steps
            step_converged = False
            for i in range(self.curr_step, self.convergence["steps"]+1):
                self._print(f"RANDOM_SEED:  {self.random_seed}")
                self._print(f"RANDOM_STATE: {self.rng.bit_generator.state}")
                step_converged = self._irun(i)
                if not step_converged:
                    self._print("Wait MC step to finish.")
                    break
                else:
                    # -- save checkpoint
                    self._save_checkpoint(step=i)
                    # -- clean up
                    if ((self.directory/f"{self.WDIR_PREFIX}{i}").exists()) and (i%self.dump_period != 0):
                        shutil.rmtree(self.directory/f"{self.WDIR_PREFIX}{i}")
            else:
                self._print("MC is converged...")
        else:
            self._print("Monte Carlo is converged.")

        return
    
    def _irun(self, i):
        """Run a single MC step."""
        self._print(f"===== MC Step {i} =====")
        step_wdir = self.directory/f"{self.WDIR_PREFIX}{i}"
        self.worker.wdir_name = step_wdir.name

        # - operate atoms
        curr_op = select_operator(self.operators, self.op_probs, self.rng)
        self._print(f"operator {curr_op.__class__.__name__}")
        curr_atoms = curr_op.run(self.atoms, self.rng)
        if curr_atoms:
            # --- add info
            curr_atoms.info["confid"] = int(f"{i}")
            curr_atoms.info["step"] = -1 # NOTE: remove step info from driver
        else:
            self._print("FAILED to run operation...")
        
        # - run postprocess
        if curr_atoms is not None:
            # - TODO: save some info not stored by driver
            curr_tags = curr_atoms.get_tags()

            # - run postprocess (spc, min or md)
            _ = self.worker.run([curr_atoms], read_exists=True)
            self.worker.inspect(resubmit=True)
            if self.worker.get_number_of_running_jobs() == 0:
                curr_atoms = self.worker.retrieve()[0][-1]
                curr_atoms.set_tags(curr_tags)

                self.energy_operated = curr_atoms.get_potential_energy()
                self._print(f"post ene: {self.energy_operated}")

                # -- metropolis
                success = curr_op.metropolis(
                    self.energy_stored, self.energy_operated, self.rng
                )

                self._save_step_info(curr_op, success)

                # -- update atoms
                if success:
                    self.energy_stored = self.energy_operated
                    self.atoms = curr_atoms
                    self._print("success...")
                else:
                    self._print("failure...")
                write(self.directory/self.TRAJ_NAME, self.atoms, append=True)

                step_converged = True
            else:
                step_converged = False
        else:
            # save the previous structure as the current operation gives no structure.
            step_converged = True
            write(self.directory/self.TRAJ_NAME, self.atoms, append=True)

        return step_converged
    
    def _veri_checkpoint(self) -> bool:
        """Verify checkpoints."""
        ckpt_wdirs = list(self.directory.glob("checkpoint.*"))
        nwdirs = len(ckpt_wdirs)

        verified = True
        if nwdirs > 0:
            # TODO: check the directory is not empty
            ...
        else:
            verified = False

        return verified
    
    def _save_checkpoint(self, step):
        """Save the current Monte Carlo state."""
        if self.ckpt_period > 0 and (step%self.ckpt_period == 0):
            self._print("SAVE CHECKPOINT...")
            ckpt_wdir = self.directory / f"checkpoint.{step}"
            ckpt_wdir.mkdir(parents=True)
            # - save the structure
            write(ckpt_wdir/"structure.xyz", self.atoms)
            # - save operator state
            for i, op in enumerate(self.operators):
                save_operator(op, ckpt_wdir/f"op-{i}.ckpt")
            # - save the random state
            with open(ckpt_wdir/"rng.ckpt", "wb") as fopen:
                pickle.dump(self.rng.bit_generator.state, fopen)
        else:
            ...

        return
    
    def _load_checkpoint(self):
        """Load the current Monet Carlo state."""
        # - Find the latest checkpoint
        ckpt_wdir = sorted(
            self.directory.glob("checkpoint.*"), key=lambda x: int(str(x.name).split(".")[-1])
        )[-1]
        step = int(ckpt_wdir.name.split(".")[-1])
        self._print(f"LOAD CHECKPOINT STEP {step}.")

        # -- load operators
        op_files = sorted(
            ckpt_wdir.glob("op-*.ckpt"), key=lambda x: int(str(x.name).split(".")[0][3:])
        )

        saved_operators = []
        for i, op_file in enumerate(op_files):
            saved_operator = load_operator(op_file)
            saved_operators.append(saved_operator)
        self.operators = saved_operators

        # -- add print function to operators
        for op in self.operators:
            op._print = self._print

        self._print("===== Saved MonteCarlo Operators (Modifiers) =====\n")
        for op in self.operators:
            for x in str(op).split("\n"):
                self._print(x)
        self._print(f"normalised probabilities {self.op_probs}\n")

        # -- load random state
        with open(ckpt_wdir/"rng.ckpt", "rb") as fopen:
            rng_state = pickle.load(fopen)
        self.rng.bit_generator.state = rng_state

        # -- load structure
        self.curr_step = step
        self.atoms = read(ckpt_wdir/"structure.xyz")
        self.energy_stored = self.atoms.get_potential_energy()

        return 
    
    def _save_step_info(self, curr_op, success: bool):
        """"""
        extra_info = getattr(curr_op, "_extra_info", "-")
        with open(self.directory/self.INFO_NAME, "a") as fopen:
            fopen.write(
                "{:<24s}  {:<24s}  {:<12d}  {:<12s}  {:<24.4f}  {:<24.4f}  \n".format(
                    curr_op.__class__.__name__, extra_info,
                    len(self.atoms), str(success), 
                    self.energy_stored, self.energy_operated
                )
            )

        return
    
    def read_convergence(self):
        """Check the convergence of MC.

        Currently, the only criteria is whether the simulation reaches the maximum 
        steps.

        """
        converged = False
        if (self.directory/self.TRAJ_NAME).exists():
            mctraj = read(self.directory/self.TRAJ_NAME, ":")
            nframes = len(mctraj)
            #self.curr_step = nframes
            if nframes > self.convergence["steps"]:
                converged = True
        else:
            ...

        return converged
    
    def get_workers(self):
        """Get all workers used by this expedition."""
        # NOTE: Check if computation folders have been archived
        archive_path = self.directory/"cand.tgz"
        if not archive_path.exists():
            wdirs = list(self.directory.glob(f"{self.WDIR_PREFIX}*"))
        else:
            wdirs = []
            with tarfile.open(archive_path, "r:gz") as tar:
                for tarinfo in tar:
                    if tarinfo.isdir() and tarinfo.name.startswith(self.WDIR_PREFIX):
                        wdirs.append(self.directory/tarinfo.name)
                    else:
                        ...
            ...
        wdirs = sorted(wdirs, key=lambda x: int(x.name[len(self.WDIR_PREFIX):]))

        if hasattr(self.worker.potter, "remove_loaded_models"):
            self.worker.potter.remove_loaded_models()

        workers = []
        for curr_wdir in wdirs:
            curr_worker = copy.deepcopy(self.worker)
            curr_worker.directory = curr_wdir.parent
            curr_worker.wdir_name = curr_wdir.name
            workers.append(curr_worker)

        return workers

    def as_dict(self) -> dict:
        """"""
        engine_params = {}
        engine_params["method"] = "monte_carlo"
        engine_params["builder"] = self.builder.as_dict()
        engine_params["worker"] = self.worker.as_dict()
        engine_params["operators"] = []
        for op in self.operators:
            engine_params["operators"].append(op.as_dict())
        engine_params["dump_period"] = self.dump_period
        engine_params["ckpt_period"] = self.ckpt_period
        engine_params["convergence"] = self.convergence
        engine_params["random_seed"] = self.random_seed

        engine_params = copy.deepcopy(engine_params)

        return engine_params



if __name__ == "__main__":
    ...
