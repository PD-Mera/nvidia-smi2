#!/usr/bin/env python3

#######
# USAGE
#
# [nvidia-smi | ] nvidia-htop.py [-l [length]]
#   print GPU utilization with usernames and CPU stats for each GPU-utilizing process
#
#   -l|--command-length [length]     Print longer part of the commandline. If `length'
#                                    is provided, use it as the commandline length,
#                                    otherwise print first 100 characters.
#   -c|--color                       Colorize the output (green - free GPU, yellow -
#                                    moderately used GPU, red - fully used GPU)
######

import sys
import os
import re
import subprocess
import select
import argparse
from termcolor import colored

MEMORY_FREE_RATIO = 0.05
MEMORY_MODERATE_RATIO = 0.9
GPU_FREE_RATIO = 0.05
GPU_MODERATE_RATIO = 0.75

def colorize(_lines):
    for j in range(len(_lines)):
        line = _lines[j]
        m = re.match(r"\| (?:N/A|..%)\s+[0-9]{2,3}C.*\s([0-9]+)MiB\s+/\s+([0-9]+)MiB.*\s([0-9]+)%", line)
        if m is not None:
            used_mem = int(m.group(1))
            total_mem = int(m.group(2))
            gpu_util = int(m.group(3)) / 100.0
            mem_util = used_mem / float(total_mem)

            is_moderate = False
            is_high = gpu_util >= GPU_MODERATE_RATIO or mem_util >= MEMORY_MODERATE_RATIO
            if not is_high:
                is_moderate = gpu_util >= GPU_FREE_RATIO or mem_util >= MEMORY_FREE_RATIO

            c = 'red' if is_high else ('yellow' if is_moderate else 'green')
            _lines[j] = colored(_lines[j], c)
            _lines[j-1] = colored(_lines[j-1], c)

    return _lines

def get_nvidia_smi_stdout():
    fake_stdin_path = os.getenv("FAKE_STDIN_PATH", None)
    stdin_lines = []
    if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
        stdin_lines = sys.stdin.readlines()

    if fake_stdin_path is not None:
        with open(fake_stdin_path, 'rt') as f:
            lines = f.readlines()
    elif stdin_lines:
        lines = stdin_lines
    else:
        ps_call = subprocess.run('nvidia-smi', stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if ps_call.returncode != 0:
            print('nvidia-smi exited with error code {}:'.format(ps_call.returncode))
            print(ps_call.stdout.decode() + ps_call.stderr.decode())
            sys.exit()
        lines_proc = ps_call.stdout.decode().split("\n")
        lines = [line + '\n' for line in lines_proc[:-1]]
        lines += lines_proc[-1]
    return lines

def get_process_user_detail(i, is_new_format, lines):
    # Parse the PIDs from the lower part
    ps_detail = {
            "gpu_num": [], "pid": [], "gpu_mem": [], "user": [], "cpu": [], "mem": [], "time": [], "command": []
        }

    gpu_num_idx = 1
    pid_idx = 2 if not is_new_format else 4
    gpu_mem_idx = -3
    while not lines[i].startswith("+--"):
        if "Not Supported" in lines[i]:
            i += 1
            continue
        line = lines[i]
        line = re.split(r'\s+', line)
        ps_detail["gpu_num"].append(line[gpu_num_idx])
        ps_detail["pid"].append(line[pid_idx])
        ps_detail["gpu_mem"].append(line[gpu_mem_idx])
        ps_detail["user"].append("")
        ps_detail["cpu"].append("")
        ps_detail["mem"].append("")
        ps_detail["time"].append("")
        ps_detail["command"].append("")
        i += 1


    # Query the PIDs using ps
    ps_format = "pid,user,%cpu,%mem,etime,command"
    ps_call = subprocess.run(["ps", "-o", ps_format, "-p", ",".join(ps_detail["pid"])], stdout=subprocess.PIPE)


    processes = ps_call.stdout.decode().split("\n")
    # Parse ps output
    for line in processes:
        if line.strip().startswith("PID") or len(line) == 0:
            continue
        parts = re.split(r'\s+', line.strip(), 5)
        # idx = pid.index(parts[0])
        for idx in filter(lambda p: ps_detail["pid"][p] == parts[0], range(len(ps_detail["pid"]))):
            ps_detail["user"][idx] = parts[1]
            ps_detail["cpu"][idx] = parts[2]
            ps_detail["mem"][idx] = parts[3]
            ps_detail["time"][idx] = parts[4] if "-" not in parts[4] else parts[4].split("-")[0] + " days"
            ps_detail["command"][idx] = parts[5]

    # Get user detail
    user_detail = {}
    for idx in range(len(ps_detail["pid"])):
        user = ps_detail["user"][idx]
        if user not in user_detail:
            user_detail[user] = {"total_gpu_mem": 0, "total_cpu": 0, "total_mem": 0}
        user_detail[user]["total_gpu_mem"] += int(ps_detail["gpu_mem"][idx].replace('MiB', ''))
        user_detail[user]["total_cpu"] += round(float(ps_detail["cpu"][idx]), 1)
        user_detail[user]["total_mem"] += float(ps_detail["mem"][idx])

    return ps_detail, user_detail

def get_line_to_print(lines):
    lines_to_print = []
    is_new_format = False
    # Copy the utilization upper part verbatim
    for i in range(len(lines)):
        if not lines[i].startswith("| Processes:"):
            lines_to_print.append(lines[i].rstrip())
        else:
            while not lines[i].startswith("|===="):
                m = re.search(r'GPU\s*GI\s*CI', lines[i])
                if m is not None:
                    is_new_format = True
                i += 1
            i += 1
            break
    ps_start_idx = i
    return lines_to_print, ps_start_idx, is_new_format
