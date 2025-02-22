#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import copy
import json
import logging
import pathlib
import traceback

import yaml

import omegaconf
from omegaconf import OmegaConf

from .. import config
from .utils import create_variable, create_operation, traverse_postorder

cache_nodes = {}

def instantiate_variable(vx_name, vx_params):
    """"""
    print(f"----- {vx_name} -----")
    params = {}
    for k, v in vx_params.items():
        print(k, v)
        ...

    return

def resolve_variables(variables: dict):
    """"""
    for vx_name, vx_params in variables.items():
        vx = instantiate_variable(vx_name, vx_params)
        ...

    return

def instantiate_operation(op_name, op_params):
    """"""
    #print(f"----- {op_name} -----")
    params = {}
    for k, v in op_params.items():
        params[k] = v # resolve one by one...
    op = create_operation(op_name, op_params)
    #print(op)
    #print(op.input_nodes)

    return op

def resolve_operations(config: dict):
    """"""
    operations = {}
    for op_name, op_params in config.items():
        op = instantiate_operation(op_name, op_params)
        operations[op_name] = op
        cache_nodes[op_name] = op

    return operations

def run_session(config_filepath, feed_command=None, directory="./"):
    """Configure session with omegaconfig."""
    directory = pathlib.Path(directory)

    # - add resolvers
    def create_vx_instance(vx_name, _root_):
        """"""
        if vx_name not in cache_nodes:
            vx_params = OmegaConf.to_object(_root_.variables.get(vx_name))
            vx = create_variable(vx_name, vx_params)
            cache_nodes[vx_name] = vx
            return vx
        else:
            return cache_nodes[vx_name]

    OmegaConf.register_new_resolver(
        "vx", create_vx_instance, use_cache=False
    )

    def create_op_instance(op_name, _root_):
        """"""
        if op_name not in cache_nodes:
            op_params = OmegaConf.to_object(_root_.operations.get(op_name))
            op = create_operation(op_name, op_params)
            cache_nodes[op_name] = op
            return op
        else:
            return cache_nodes[op_name]

    OmegaConf.register_new_resolver(
        "op", create_op_instance, use_cache=False
    )

    # --
    def read_json(input_file):
        with open(input_file, "r") as fopen:
            input_dict = json.load(fopen)

        return input_dict

    OmegaConf.register_new_resolver(
        "json", read_json
    )

    def read_yaml(input_file):
        with open(input_file, "r") as fopen:
            input_dict = yaml.safe_load(fopen)

        return input_dict

    OmegaConf.register_new_resolver(
        "yaml", read_yaml
    )

    # - load configuration and resolve it
    conf = OmegaConf.load(config_filepath)

    # - add placeholders and their directories
    if "placeholders" not in conf:
        conf.placeholders = {}
    if feed_command is not None:
        pairs = [x.split("=") for x in feed_command]
        for k, v in pairs:
            if v.isdigit():
                v = int(v)
            conf.placeholders[k] = v
    config._debug(f"YAML: {OmegaConf.to_yaml(conf)}")

    # - check operations and their directories
    for op_name, op_params in conf.operations.items():
        op_params["directory"] = str(directory/op_name)
    
    # - set variable directory
    for k, v_dict in conf.variables.items():
        v_dict["directory"] = str(directory/"variables"/k)
    #print("YAML: ", OmegaConf.to_yaml(conf))

    # - resolve sessions
    #container = OmegaConf.to_object(conf.sessions)
    #for k, v in container.items():
    #    print(k, v)

    try:
        operations = resolve_operations(conf["operations"])
    except omegaconf.errors.InterpolationResolutionError as err:
        config._debug (traceback.format_exc())
        err_key = (str(err).strip().split("\n")[1]).strip().split(":")[1]
        config._print(f"FAILED TO PARSE `{err_key}` KEY.")
        exit()

    container = {}
    for k, v in conf["sessions"].items():
        container[k] = operations[v]

    # - run session
    names = conf.placeholders.get("names", None)
    if names is not None:
        session_names = [x.strip() for x in names.strip().split(",")]
    else:
        session_names =[None]*len(container)
    
    # - some imported packages change `logging.basicConfig` 
    #   and accidently add a StreamHandler to logging.root
    #   so remove it...
    for h in logging.root.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            logging.root.removeHandler(h)
    
    # - get session general configs
    sconfigs = conf.get("configs", {})

    exec_mode = sconfigs.get("mode", "basic")
    if exec_mode == "basic": # sequential
        from .basic import Session
        # -- sequential
        for i, (k, v) in enumerate(container.items()):
            n = session_names[i]
            if n is None:
                n = k
            entry_operation = v
            session = Session(directory=directory/n)
            session.run(entry_operation, feed_dict={})
    elif exec_mode == "active":
        from .active import ActiveSession
        assert len(container) == 1, "ActiveSession only accepts one operation."
        for i, (k, v) in enumerate(container.items()):
            n = session_names[i]
            if n is None:
                n = k
            entry_operation = v
            session = ActiveSession(
                steps=sconfigs.get("steps", 2), directory=directory/n
            )
            session.run(entry_operation, feed_dict={})
    elif exec_mode == "cyc":
        from .active import CyclicSession
        # -- iterative
        session = CyclicSession(directory="./")
        session.run(
            container["init"], container["iter"], container.get("post"),
            repeat=conf.get("repeat", 1)
        )
    elif exec_mode == "otf":
        config._print("Use OTF Session...")
        from .active import OTFSession
        for i, (k, v) in enumerate(container.items()):
            n = session_names[i]
            if n is None:
                n = k
            entry_operation = v
            session = OTFSession(directory=directory/n)
            session.run(entry_operation, feed_dict={})
    else:
        raise RuntimeError(f"Unknown session type {exec_mode}.")

    return


if __name__ == "__main__":
    ...
