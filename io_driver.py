#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2020 Seagate Technology LLC and/or its Affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# For any questions about this software or licensing,
# please email opensource@seagate.com or cortx-questions@seagate.com.
#
#
"""
IOdriver for scheduling and managing all tests.
Consists of Schedular which parses yaml based inputs and schedules, monitor jobs accordingly.
IO driver will also be responsible for performing health checks and
support bundle collection on regular intervals.
"""
import argparse
import logging
import os
import random
import sched
import sys
import time
from datetime import datetime
from multiprocessing import Process, Manager

import psutil
import yaml

from libs.io import yaml_parser
from libs.io.tools.s3bench import S3bench

IO_DRIVER_CFG = "config/io/io_driver_config.yaml"

with open(IO_DRIVER_CFG) as cfg:
    conf = yaml.safe_load(cfg)

log = logging.getLogger()
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh = logging.FileHandler(conf['driver_log'])
ch = logging.StreamHandler()

log.setLevel(logging.DEBUG)
fh.setLevel(logging.DEBUG)
ch.setLevel(logging.INFO)

ch.setFormatter(formatter)
fh.setFormatter(formatter)

log.addHandler(ch)
log.addHandler(fh)

sched_obj = sched.scheduler(time.time, time.sleep)
manager = Manager()
process_states = manager.dict()
event_list = list()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, help="seed",
                        default=random.SystemRandom().randint(1, 9999999))
    parser.add_argument("secret_key", type=str, help="Secret Key")
    parser.add_argument("access_key", type=str, help="Access Key")
    parser.add_argument("--endpoint", type=str,
                        help="Endpoint for S3 operations",
                        default="https://s3.seagate.com")
    return parser.parse_args()


def launch_process(process, process_type, test_id):
    """
    This method is intended to Start the process and add PID to dictionary.
    process_states : Dictionary of process as key and pid as value
    :param process: Object of Process
    :param process_type: Type of tool invoked by process
    :param test_id: Jira Test id
    """
    process.start()
    pid = process.pid
    log.info("Started Process PID: %s TEST_ID: %s TYPE: %s", pid, test_id,
             process_type)
    new_proc_data = manager.dict()
    new_proc_data['state'] = 'started'
    new_proc_data['type'] = process_type
    new_proc_data['start_time'] = datetime.now()
    process_states[pid] = new_proc_data


def update_process_termination(return_status):
    """
    Update the required structures with return status from process.
    param return_status: Return status from the executed process
    """
    pid = os.getpid()
    log.info("Process terminated : %s Response: %s", pid, return_status)
    process_states[pid]['state'] = 'done'
    process_states[pid]['ret_status'] = return_status[0]
    process_states[pid]['response'] = return_status[1]
    log.info("Proc state post termination : %s %s", pid,
             process_states[pid]['state'])


# pylint: disable=too-many-arguments
def run_s3bench(access, secret, endpoint, test_id, clients, samples, size_low,
                size_high, seed,
                duration):
    """
    Execute S3bench tool and update error code if any, to process_state on termination.
    """
    log.info("Start S3bench run ")
    s3bench = S3bench(access, secret, endpoint, test_id, clients, samples,
                      size_low, size_high,
                      seed, duration)
    ret = s3bench.run_check()
    update_process_termination(return_status=ret)
    log.info("Completed S3bench run ")


def run_warp():
    """
    Execute warp tool and update error code if any, to process_state on termination.
    """
    log.info("Start warp run ")
    time.sleep(50)
    ret = (False, True)  # TODO: add warp tool support
    update_process_termination(return_status=ret)
    log.info("Completed warp run ")


def collect_sb():
    """
    Collect support bundle and update error code if any, to process_state on termination.
    """
    log.info("Collect Support bundle")
    time.sleep(10)
    ret = (True, True)  # TODO: add support bundle collection call
    update_process_termination(return_status=ret)
    log.info("Support bundle collection done!!")


def perform_health_check():
    """
    Collect support bundle and update error code if any, to process_state on termination.
    """
    log.info("Performing health check")
    time.sleep(10)
    ret = (True, True)  # TODO: add health check call
    update_process_termination(return_status=ret)
    log.info("Health check done!!")


def periodic_sb():
    """
    Perform Periodic support bundle collection
    """
    sb_interval = conf['sb_interval_mins'] * 60
    event_list.append(sched_obj.enter(sb_interval, 1, periodic_sb))
    process = Process(target=collect_sb)
    launch_process(process, 'support_bundle', None)


def periodic_hc():
    """
    Perform periodic health check
    """
    hc_interval = conf['hc_interval_mins'] * 60
    event_list.append(sched_obj.enter(hc_interval, 1, periodic_hc))
    process = Process(target=perform_health_check)
    launch_process(process, 'health_check', None)


def ps_kill(proc_pid):
    """
    Kill process with proc_pid and its child process
    :param proc_pid: Pid of process to be killed
    """
    log.info("Killing %s", proc_pid)
    process = psutil.Process(proc_pid)
    for proc in process.children(recursive=True):
        proc.kill()
    process.kill()


def monitor_proc():
    """
    Monitor all started processes
    """
    log.info("Monitoring Processes..")
    while True:
        time.sleep(30)
        log.info(".")
        error_proc = None
        error_proc_data = None
        terminate_run = False
        is_process_running = False

        for key, value in process_states.items():
            if value['state'] == 'done':
                if not value['ret_status']:
                    terminate_run = True
                    error_proc = key
                    error_proc_data = value
            if value['state'] == 'started':
                is_process_running = True

        # Terminate if error observed in any process
        if terminate_run:
            log.error("Error observed in process %s %s", error_proc,
                      error_proc_data)
            log.error("Terminating schedular..")
            for pid in process_states.keys():
                ps_kill(pid)
            for event in event_list:
                try:
                    log.info("Cancelling event %s", event)
                    sched_obj.cancel(event)
                except ValueError:
                    log.info("Event not present %s", event)
            sys.exit(0)

        # Terminate if no process scheduled or running.
        if sched_obj.empty() and not is_process_running:
            log.info("No jobs scheduled in schedular,exiting..!!")
            sys.exit(0)


def main(options):
    """
    Main function
    """
    access = options.access_key
    secret = options.secret_key
    endpoint = options.endpoint
    seed = options.seed
    log.info("Seed Used : %s", seed)

    test_input = yaml_parser.test_parser(
        'config/io/s3bench_random_io_object_test.yaml')
    for key, value in test_input.items():
        process_type = value['tool'].lower()
        if process_type == 's3bench':
            process = Process(target=run_s3bench,
                              args=(access, secret, endpoint, value['TEST_ID'],
                                    value['sessions_per_node'], 2000,
                                    value['start_range'], value['end_range'],
                                    seed, '60s'))
        elif process_type == 'warp':
            process = Process(target=run_warp)
        else:
            log.error("Error! Tool type not defined: %s", process_type)
            sys.exit(1)
        event_list.append(
            sched_obj.enter(value['start_time'].seconds, 1, launch_process,
                            (process, process_type, key)))

    process = Process(target=monitor_proc)
    process.start()

    # if conf['capture_support_bundle']:
    #     log.info("Scheduling Support bundle collection")
    #     event_list.append(sched_obj.enter(conf['sb_interval_mins'] * 60, 1, periodic_sb))

    # if conf['perform_health_check']:
    #     log.info("Scheduling health check")
    #     event_list.append(sched_obj.enter(conf['hc_interval_mins'] * 60, 1, periodic_hc))

    log.info("Starting scheduler")
    sched_obj.run()
    process.join()


if __name__ == '__main__':
    opts = parse_args()
    main(opts)
