#!/usr/bin/env python3
# -*- coding: utf-8 -*

import abc
import copy
from typing import Union, List, NoReturn

import numpy as np

from ase.calculators.calculator import Calculator, all_properties, all_changes

from ..core.register import registers
from ..computation import register_drivers

"""The abstract base class of any potential manager.

"""

class DummyCalculator(Calculator):

    name = "dummy"

    def __init__(self, restart=None, label="dummy", atoms=None, directory=".", **kwargs):
        super().__init__(restart, label=label, atoms=atoms, directory=directory, **kwargs)

        return

    def calculate(self, atoms=None, properties=all_properties, system_changes=all_changes):
        """"""
        raise NotImplementedError("DummyCalculator is unable to calculate.")


class AbstractPotentialManager(abc.ABC):
    """
    Create various potential instances
    """

    name = "potential"
    version = "m00" # calculator name

    implemented_backends = []
    valid_combinations = []

    _calc = None

    def __init__(self):
        """
        """

        return
    
    @property
    def calc(self):
        return self._calc
    
    @calc.setter
    def calc(self, calc_):
        self._calc = calc_
        return 
    
    @abc.abstractmethod
    def register_calculator(self, calc_params, *agrs, **kwargs):
        """Register the host calculator.
        """
        self.calc_backend = calc_params.pop("backend", self.name)
        if self.calc_backend not in self.implemented_backends:
            raise RuntimeError(f"Unknown backend {self.calc_backend} for potential {self.name}")

        self.calc_params = copy.deepcopy(calc_params)

        return

    def create_driver(
        self, 
        dyn_params: dict = {},
        *args, **kwargs
    ):
        """Create a driver for dynamics.

        Default the dynamics backend will be the same as calc. However, 
        ase-based dynamics can be used for all calculators.

        """
        # - check whether there is a calc
        if not hasattr(self, "calc"):
            raise AttributeError("Cannot create driver since a calculator has been properly registered.")
            
        # parse backends
        self.dyn_params = dyn_params
        dynamics = dyn_params.get("backend", self.calc_backend)
        if dynamics == "external":
            dynamics = self.calc_backend

        if (self.calc_backend, dynamics) not in self.valid_combinations:
            raise RuntimeError(f"Invalid dynamics backend {dynamics} based on {self.calc_backend} calculator")
        
        # - merge params for compat
        merged_params = {}
        if "task" in dyn_params:
            merged_params.update(task=dyn_params.get("task", "min"))

        if "init" in dyn_params or "run" in dyn_params:
            merged_params.update(**dyn_params.get("init", {}))
            merged_params.update(**dyn_params.get("run", {}))
        else:
            merged_params.update(**dyn_params)

        merged_params.update(ignore_convergence=dyn_params.get("ignore_convergence", False))

        # -- other params
        ignore_convergence = merged_params.pop("ignore_convergence", False)

        # - create dynamics
        driver_cls = register_drivers[dynamics]
        #assert driver_cls is not None, f"Cannot find a driver named {dynamics}."

        driver = driver_cls(
            self.calc, merged_params, directory=self.calc.directory, 
            ignore_convergence=ignore_convergence
        )
        driver.pot_params = self.as_dict()
        
        return driver

    def create_reactor(self, rxn_params: dict = {}, *args, **kwargs):
        """Create a reactor for reaction.

        Default the reaction backend will be the same as calc. However, 
        ase-based dynamics can be used for all calculators.

        """
        # - check whether there is a calc
        if not hasattr(self, "calc"):
            raise AttributeError("Cant create reactor before a calculator has been properly registered.")
            
        # parse backends
        self.rxn_params = rxn_params
        reaction = rxn_params.get("backend", self.calc_backend)
        if reaction == "external":
            reaction = self.calc_backend

        if (self.calc_backend, reaction) not in self.valid_combinations:
            raise RuntimeError(
                f"Invalid reaction backend {reaction} based on {self.calc_backend} calculator. Valid combinations are {self.valid_combinations}"
            )
        
        # - merge params for compat
        merged_params = {}
        if "task" in rxn_params:
            merged_params.update(task=rxn_params.get("task", "min"))
        if "init" in rxn_params or "run" in rxn_params:
            merged_params.update(**rxn_params.get("init", {}))
            merged_params.update(**rxn_params.get("run", {}))
        else:
            merged_params.update(**rxn_params)

        # - other params
        ignore_convergence = merged_params.pop("ignore_convergence", False)

        # - construct driver params
        inp_params = dict(
            calc = self.calc,
            params = merged_params,
            ignore_convergence = ignore_convergence
        )

        driver = registers.create("reactor", reaction, convert_name=False, **inp_params)
        driver.pot_params = self.as_dict()
        
        return driver

    def as_dict(self):
        """"""
        params = {}
        params["name"] = self.name

        pot_params = {"backend": self.calc_backend}
        pot_params.update(copy.deepcopy(self.calc_params))
        params["params"] = pot_params

        return params


if __name__ == "__main__":
    ...
