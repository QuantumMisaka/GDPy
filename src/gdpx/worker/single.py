#!/usr/bin/env python3
# -*- coding: utf-8 -*

import io
import gzip
import pathlib
import time
import uuid
import shutil
import tarfile
import yaml

from typing import List

from tinydb import Query, TinyDB

from ase import Atoms
from ase.io import read, write

from .worker import AbstractWorker
from ..utils.command import CustomTimer


class SingleWorker(AbstractWorker):

    #: TODO: Current working directory name...
    _wdir_name: str = None

    def __init__(self, potter, driver, scheduler, directory="./", *args, **kwargs) -> None:
        """"""
        super().__init__(directory)

        self.potter = potter
        self.driver = driver
        self.scheduler = scheduler

        return
    
    @property
    def wdir_name(self):
        """"""

        return self._wdir_name
    
    @wdir_name.setter
    def wdir_name(self, name: str):
        """"""
        self._wdir_name = name

        return 
    
    def run(self, builder, *args, **kwargs):
        """This worker accepts only a single structure."""
        super().run(*args, **kwargs)

        if isinstance(builder, list): # assume List[Atoms]
            frames = builder
        else: # assume it is a builder
            frames = builder.run()
        nframes = len(frames)
        assert len(frames) == 1, f"{self.__class__.__name__} accepts only a single structure."

        uid = str(uuid.uuid1())
        assert self.wdir_name is not None, "Computation folder is not set."
        wdir = self.directory / self.wdir_name
        job_name = uid + "-" + "single"

        scheduler = self.scheduler
        if scheduler.name == "local":
            with CustomTimer(name="run-driver", func=self._print):
                self.driver.directory = wdir
                self._print(
                    f"{time.asctime( time.localtime(time.time()) )} {wdir.name} {self.driver.directory.name} is running..."
                )
                self.driver.reset()
                self.driver.run(frames[0], read_exists=True, extra_info=None)
        else:
            worker_params = {}
            worker_params["use_single"] = True
            worker_params["driver"] = self.driver.as_dict()
            worker_params["potential"] = self.potter.as_dict()

            with open(wdir/f"worker-{uid}.yaml", "w") as fopen:
                yaml.dump(worker_params, fopen)

            # - save structures
            dataset_path = str((wdir/f"_gdp_inp.xyz").resolve())
            write(dataset_path, frames[0])

            # - save scheduler file
            jobscript_fname = f"run-{uid}.script"
            self.scheduler.job_name = job_name
            self.scheduler.script = wdir/jobscript_fname

            self.scheduler.user_commands = "gdp -p {} compute {}\n".format(
                (wdir/f"worker-{uid}.yaml").name, dataset_path
            )

            # - TODO: check whether params for scheduler is changed
            self.scheduler.write()
            if self._submit:
                self._print(f"{wdir.name} JOBID: {self.scheduler.submit()}")
            else:
                self._print(f"{wdir.name} waits to submit.")

        # - save this batch job to the database
        with TinyDB(
            self.directory/f"_{self.scheduler.name}_jobs.json", indent=2
        ) as database:
            _ = database.insert(
                dict(
                    uid = uid,
                    #md5 = identifier,
                    gdir=job_name, 
                    #group_number=ig, 
                    wdir_names=[str(wdir)], 
                    queued=True
                )
            )

        return

    def inspect(self, resubmit=False, *args, **kwargs):
        """"""
        self._initialise(*args, **kwargs)
        self._debug(f"~~~{self.__class__.__name__}+inspect")

        running_jobs = self._get_running_jobs() # Always return one job
        self._debug(f"running_jobs: {running_jobs}")

        with TinyDB(
            self.directory/f"_{self.scheduler.name}_jobs.json", indent=2
        ) as database:
            for job_name in running_jobs:
                doc_data = database.get(Query().gdir == job_name)
                uid = doc_data["uid"]
                wdir_name = pathlib.Path(doc_data["wdir_names"][0]).name

                self.scheduler.job_name = job_name
                self.scheduler.script = self.directory/f"run-{uid}.script"

                if self.scheduler.is_finished():
                    # -- check if the job finished properly
                    # assert wdir_name = self.wdir_name
                    self.driver.directory = self.directory/wdir_name
                    if self.driver.read_convergence():
                        database.update({"finished": True}, doc_ids=[doc_data.doc_id])
                    else:
                        if resubmit:
                            jobid = self.scheduler.submit()
                            self._print(f"{job_name} is re-submitted with JOBID {jobid}.")
                else:
                    self._print(f"{job_name} is running...")

        return

    def retrieve(
        self, include_retrieved: bool=False, use_archive: bool=False,
        *args, **kwargs
    ):
        """Retrieve training results.

        Args:
            use_archive: Whether archive finished computation folders.

        """
        self.inspect(*args, **kwargs)
        self._debug(f"~~~{self.__class__.__name__}+retrieve")

        unretrieved_wdirs_ = []
        if not include_retrieved:
            unretrieved_jobs = self._get_unretrieved_jobs()
        else:
            unretrieved_jobs = self._get_finished_jobs()

        with TinyDB(
            self.directory/f"_{self.scheduler.name}_jobs.json", indent=2
        ) as database:
            for job_name in unretrieved_jobs:
                doc_data = database.get(Query().gdir == job_name)
                unretrieved_wdirs_.extend(
                    (self.directory/w).resolve() for w in doc_data["wdir_names"]
                )
            unretrieved_wdirs = [p for p in unretrieved_wdirs_ if p.name == self.wdir_name]

            results = []
            self._debug(f"unretrieved_wdirs: {unretrieved_wdirs}")
            if unretrieved_wdirs:
                unretrieved_wdirs = [pathlib.Path(x) for x in unretrieved_wdirs]
                # NOTE: SingleWorker.directory directly points to the computation folder
                #       and its parent directory may have several directories.
                # TODO: Change the above behaviour?
                archive_path = (self.directory/"cand.tgz").absolute()
                is_archived = False
                if not archive_path.exists():
                    results = self._read_results(unretrieved_wdirs, )
                else:
                    target_name = self.wdir_name
                    with tarfile.open(archive_path, "r:gz") as tar:
                        for tarinfo in tar:
                            #self._debug(tarinfo.name)
                            if tarinfo.name == target_name:
                                self._debug(f"Found archived data {tarinfo.name}.")
                                is_archived = True
                                break
                        else:
                            ...
                    if is_archived:
                        results = self._read_results(unretrieved_wdirs, archive_path)
                    else:
                        results = self._read_results(unretrieved_wdirs)
                # - archive results if it has not been done
                if use_archive and not is_archived:
                    self._print("archive computation folders...")
                    if not archive_path.exists():
                        #with tarfile.open(archive_path, "w:gz") as tar:
                        #    for w in unretrieved_wdirs:
                        #        tar.add(w, arcname=w.name)
                        archive_data = io.BytesIO()
                        # -- append
                        with tarfile.open(fileobj=archive_data, mode="w") as tar:
                            for w in unretrieved_wdirs:
                                self._debug(f"add {w.name} to archive.")
                                tar.add(w, arcname=w.name)
                        archive_data.seek(0)
                    else:
                        # -- load
                        archive_data = io.BytesIO()
                        with gzip.open(archive_path, "rb") as gzf:
                            archive_data.write(gzf.read())
                        archive_data.seek(0)
                        # -- append
                        with tarfile.open(fileobj=archive_data, mode="a") as tar:
                            for w in unretrieved_wdirs:
                                self._debug(f"add {w.name} to archive.")
                                tar.add(w, arcname=w.name)
                        archive_data.seek(0)
                    # -- save archive
                    with gzip.open(archive_path, "wb") as gzf:
                        gzf.write(archive_data.read())
                    for w in unretrieved_wdirs:
                        shutil.rmtree(w)
                else:
                    ...

            for job_name in unretrieved_jobs:
                doc_data = database.get(Query().gdir == job_name)
                database.update({"retrieved": True}, doc_ids=[doc_data.doc_id])

        return results
    
    def _read_results(
            self, unretrieved_wdirs: List[pathlib.Path], archive_path: pathlib.Path=None,
            *args, **kwargs
        ):
        """"""
        results = []
        for p in unretrieved_wdirs: # NOTE: SingleWorker always have one unretrieved directory...
            self.driver.directory = self.directory/self.wdir_name
            results.append(self.driver.read_trajectory(archive_path=archive_path))

        return results
    
    def as_dict(self) -> dict:
        """"""
        params = super().as_dict()
        params["use_single"] = True

        return params
    

if __name__ == "__main__":
    ...