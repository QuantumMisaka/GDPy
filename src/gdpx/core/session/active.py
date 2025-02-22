#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import copy
import pathlib
import time

from typing import NoReturn, Union, List, Callable

from .. import config
from ..placeholder import Placeholder
from ..variable import Variable
from .basic import Session
from .utils import traverse_postorder


class ActiveSession():

    #: Standard print function.
    _print: Callable = config._print

    #: Standard debug function.
    _debug: Callable = config._debug

    def __init__(self, steps: int=2, directory="./") -> None:
        """"""
        self.steps = steps
        self.directory = pathlib.Path(directory)

        return
    
    def run(self, operation, feed_dict: dict={}, *args, **kwargs) -> None:
        """"""
        # - update nodes' attrs based on the previous iteration
        #nodes_postorder = traverse_postorder(operation)
        #for node in nodes_postorder:
        #    if hasattr(node, "enable_active"):
        #        node.enable_active()

        # -
        for curr_step in range(self.steps):
            curr_wdir = self.directory/f"iter.{str(curr_step).zfill(4)}"
            # -- run operation
            nodes_postorder = traverse_postorder(operation)
            finished = self._irun(
                wdir=curr_wdir, nodes_postorder=nodes_postorder, feed_dict=feed_dict, 
            )
            if not finished:
                self._print("wait current iteration to finish...")
                break
            else:
                # NOTE: If previous step finished, the nodes may not have outputs
                #       as we skip them...
                if not (curr_wdir/"FINISHED").exists():
                    with open(curr_wdir/"FINISHED", "w") as fopen:
                        fopen.write(
                            f"FINISHED AT {time.asctime( time.localtime(time.time()) )}."
                        )
                    # --- report
                    self._print("[{:^24s}]".format("CONVERGENCE"))
                    converged_list = []
                    for node in nodes_postorder:
                        if hasattr(node, "report_convergence"):
                            converged = node.report_convergence()
                            converged_list.append(converged)
                    if all(converged_list):
                        self._print(f"Active Session converged at step {curr_step}.")
                        break
                    else:
                        self._print(f"Active Session UNconverged at step {curr_step}.")
                else:
                    self._print("[{:^24s}] FINISHED".format(f"STEP.{str(curr_step).zfill(4)}"))
        else:
            ... # ALL iterations finished...

        return
    
    def _irun(self, wdir, nodes_postorder, feed_dict: dict={}) -> bool:
        """"""
        if (wdir/"FINISHED").exists():
            return True
        
        # - clear previous nodes' outputs
        #   somtimes two steps run consecutively and some nodes in the second step
        #   breaks and make its following nodes use outputs from the last step,
        #   which is a hidden error
        for node in nodes_postorder:
            node.reset()

        # - find forward order
        self._print(
            "[{:^24s}] NUM_NODES: {} AT MAIN: {}".format(
                "START", len(nodes_postorder), str(wdir)
            )
        )

        # - run nodes
        finished = True
        for i, node in enumerate(nodes_postorder):
            # -- change version ...
            if hasattr(node, "version"):
                node.version = wdir.name

            # NOTE: reset directory since it maybe changed
            prev_name = node.directory.name.split(".")[-1] # remove previous orders
            if not prev_name:
                prev_name = node.__class__.__name__
            #prev_name = node.__class__.__name__
            node.directory = wdir/f"{str(i).zfill(4)}.{prev_name}"
            if node.__class__.__name__.endswith("Variable"):
                node_type = "VX"
            else:
                node_type = "OP"
            self._print(
                "[{:^24s}] NAME: {} AT {}".format(
                    node_type, node.__class__.__name__.upper(), node.directory.name
                )
            )

            # -- forward
            if isinstance(node, Placeholder):
                node.output = feed_dict[node]
            elif isinstance(node, Variable):
                node.output = node.value
            else: # Operation
                self._debug(f"node: {node}")
                if node.is_ready_to_forward():
                    node.inputs = [input_node.output for input_node in node.input_nodes]
                    node.output = node.forward(*node.inputs)
                else:
                    finished = False
                    self._print("wait previous nodes to finish...")
                    continue

        return finished

class OTFSession():

    #: Standard print function.
    _print: Callable = config._print

    #: Standard debug function.
    _debug: Callable = config._debug

    def __init__(self, directory="./") -> None:
        """"""
        self.directory = pathlib.Path(directory)

        return
    
    def run(self, operation, feed_dict: dict={}) -> None:
        """"""
        for curr_step in range(3):
            # -- update operation
            if curr_step > 0:
                curr_step = curr_step
                self._update_potter(operation)
            # -- run operation
            finished = self._irun(
                operation=operation, feed_dict=feed_dict, 
                wdir=self.directory/f"iter.{str(curr_step).zfill(4)}"
            )
            if not finished:
                self._print("wait current iteration to finish...")
                break
        else:
            ... # ALL iterations finished...

        return
    
    def _update_potter(self, operation):
        """"""

        return
    
    def _irun(self, operation, feed_dict: dict={}, wdir=None):
        """"""
        # - find forward order
        nodes_postorder = traverse_postorder(operation)
        self._print(
            "[{:^24s}] NUM_NODES: {} AT MAIN: {}".format(
                "START", len(nodes_postorder), str(wdir)
            )
        )

        # - run nodes
        finished = True
        for i, node in enumerate(nodes_postorder):
            # NOTE: reset directory since it maybe changed
            #prev_name = node.directory.name
            #if not prev_name:
            #    prev_name = node.__class__.__name__
            prev_name = node.__class__.__name__
            node.directory = wdir/f"{str(i).zfill(4)}.{prev_name}"
            if node.__class__.__name__.endswith("Variable"):
                node_type = "VX"
            else:
                node_type = "OP"
            self._print(
                "[{:^24s}] NAME: {} AT {}".format(
                    node_type, node.__class__.__name__.upper(), node.directory.name
                )
            )

            if isinstance(node, Placeholder):
                node.output = feed_dict[node]
            elif isinstance(node, Variable):
                node.output = node.value
            else: # Operation
                self._debug(f"node: {node}")
                if node.is_ready_to_forward():
                    node.inputs = [input_node.output for input_node in node.input_nodes]
                    node.output = node.forward(*node.inputs)
                else:
                    finished = False
                    self._print("wait previous nodes to finish...")
                    continue

        return finished


class CyclicSession:

    """Create a cyclic session.

    This supports a session that contains preprocess, iteration, and postprocess.

    """

    #: Standard print function.
    _print: Callable = config._print

    #: Standard debug function.
    _debug: Callable = config._debug

    def __init__(self, directory="./") -> None:
        """"""
        self.directory = pathlib.Path(directory)

        return
    
    def iteration(self, operations):
        """Iterative part that converges at certain criteria.

        This noramlly includes steps: sample, select, label, and train. Some inputs of
        operations should be update during the iterations, for instance, the models 
        in the potential.

        """

        return
    
    def run(self, init_node, iter_node=None, post_node=None, repeat=1, *args, **kwargs) -> None:
        """"""
        # - init
        self._print(("="*28+"{:^24s}"+"="*28+"\n").format("INIT"))
        session = Session(self.directory/"init")
        _ = session.run(init_node, feed_dict={})
        self._print("status: ", init_node.status)

        init_state = init_node.status
        if init_state != "finished":
            self._print("wait init session to finish...")
            return

        # - iter
        curr_potter_node = init_node # a node that forwards a potter manager
        for i in range(repeat):
            self._print(("="*28+"{:^24s}"+"="*28+"\n").format(f"ITER.{str(i).zfill(4)}"))
            session = Session(self.directory/f"iter.{str(i).zfill(4)}")
            # -- update some parameters
            curr_node = copy.deepcopy(iter_node)
            nodes_postorder = traverse_postorder(curr_node)
            # --- trainer
            # --- potter
            for x in nodes_postorder:
                if x.__class__.__name__ == "drive":
                    x.input_nodes[1]._update_workers(curr_potter_node)
            # -- run
            _ = session.run(curr_node, feed_dict={})
            curr_state = curr_node.status
            if curr_state != "finished":
                self._print(f"wait iter.{str(i).zfill(4)} session to finish...")
                break
            else:
                curr_potter_node = curr_node
        else:
            self._print(f"iter finished...")

        # - post

        return

    def _irun(self, operation, feed_dict: dict={}) -> NoReturn:
        """"""
        # - find forward order
        nodes_postorder = traverse_postorder(operation)
        self._print(
            "[{:^24s}] NUM_NODES: {} AT MAIN: {}".format(
                "START", len(nodes_postorder), str(self.directory)
            )
        )

        # - run nodes
        for i, node in enumerate(nodes_postorder):
            # NOTE: reset directory since it maybe changed
            prev_name = node.directory.name
            if not prev_name:
                prev_name = node.__class__.__name__
            node.directory = self.directory/f"{str(i).zfill(4)}.{prev_name}"
            if node.__class__.__name__.endswith("Variable"):
                node_type = "VX"
            else:
                node_type = "OP"
            self._print(
                "[{:^24s}] {:<36s} AT {}".format(
                    node_type, node.__class__.__name__.upper(), node.directory.name
                )
            )

            if isinstance(node, Placeholder):
                node.output = feed_dict[node]
            elif isinstance(node, Variable):
                node.output = node.value
            else: # Operation
                if node.is_ready_to_forward():
                    node.inputs = [input_node.output for input_node in node.input_nodes]
                    node.output = node.forward(*node.inputs)
                else:
                    print("wait previous nodes to finish...")
                    continue

        return

if __name__ == "__main__":
    ...
