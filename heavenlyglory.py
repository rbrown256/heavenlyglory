#!/usr/bin/env python3
import socket
import os
import asyncio
import subprocess
import ipaddress
import argparse
import sys
from subprocess import check_output
from itertools import chain
from urllib.parse import urlparse


def expandRange(cidr: str) -> list:
    """ Attempts to expand given CIDR range """
    if cidr[0].isalpha():
        return [cidr]
    return [str(ip) for ip in ipaddress.IPv4Network(cidr)]


def stripScheme(target: str) -> str:
    """ Strips scheme from a given URL """
    parsed = urlparse(target)
    return parsed.hostname if parsed.hostname is not None else parsed.path


def hostnameToIP(target: str) -> str:
    """ Attempts to reduce a given URL to an IP address """
    try:
        result = socket.gethostbyname(target)
    except socket.error:
        return target
    return result


def parseTargets(fileName: str) -> set:
    """
    Parses a file of new line seperated IP addresses/CIDR ranges/URLs
    Returns a set of results
    """
    targets = set()
    with open(fileName, 'r') as f:
        targets = [expandRange(hostnameToIP(stripScheme(x.strip())))
                   for x in f.read().split()]
        targets = chain.from_iterable(targets)
    return set(targets)


def parseMasscan(target: str, result: str) -> list:
    """
    Parses the results of Masscan output
    Reduces the discovered ports to a list
    """
    final = [target]
    ports = set()
    result = str(result).split('\\r')
    for r in result:
        if "Discovered" in r:
            for dis in r.split('\\n'):
                dis = dis.split()
                try:
                    ports.add(dis[3].split('/')[0])
                except:
                    pass
    final.append(ports)
    return final


async def performMasscan(target: str, flags: list) -> list:
    """
    Invokes masscan with the given target and arguments
    Returns a list containing a the target in the first element
    And a list of open ports in the second
    """
    if not target:
        return [target, []]
    cmd = " ".join(["sudo", "masscan"] + flags.split() +
                   [target])
    print(f"[+] {cmd}")

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    result, _ = await proc.communicate()

    return parseMasscan(target, result)


def parseNmap(target: str, result: str) -> list:
    """
    Parses the results of an nmap scan
    In to a list formatted for CSV output, in the form:
    Hostname, port number, service type, service description
    """
    final = []
    result = str(result).split('\\n')
    for r in result:
        if "Ports:" in r:
            r = r.split('\\t')[1]  # ports
            r = r.split(',')
            firstRun = True
            for port in r:
                port = port.lstrip("Ports: ").split('/')
                first = ".."
                if firstRun:
                    first = target
                    firstRun = False
                if "Site" in port[0]:  # edge case for ldap servers
                    final[-1] += f"/ {port[0]}"
                else:
                    final.append(",".join([first, port[0], port[4], port[6]]))
    return final


async def performNmap(target: str, ports: list, flags: str) -> list:
    """
    Invokes nmap with a given list of ports, target and flags
    Returns a list of results in a format suitable for writing to CSV
    """
    cmd = " ".join(["nmap"] + flags.split() +
                   ["-p", ",".join(ports), "-oG", "-", target])
    print(f"[+] {cmd}")

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    result, _ = await proc.communicate()

    return parseNmap(target, result)


async def main(targets: str, nmapFlags: str, masscanFlags: str,
               outFile: str, maxTasks: int):
    """
    Runs a series of masscans against a given target(s) (synchronous)
    And then passes the open ports to nmap
    For detailed service detection (asynchronous);
    Results are written to the given OutFile in CSV format
    """
    ips = []
    if os.path.isfile(targets):
        print(f"[-] Parsing scope file '{targets}'")
        ips = parseTargets(targets)
    else:
        ips = expandRange(hostnameToIP(stripScheme(targets)))

    check_output(["sudo", "-v"])  # cache creds

    args = ((ip, masscanFlags) for ip in ips)

    # Do masscan synchronously to get consistent results
    print("[-] Performing Masscan(s)...")
    results = []
    for x in args:
        results.append(await performMasscan(*x))

    print("[-] Performing Nmap(s)...")
    tasks, completed = set(), set()
    for target, ports in results:
        if not ports:
            continue
        if len(tasks) >= maxTasks:
            completed, tasks = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED
            )
        tasks.add(asyncio.create_task(performNmap(target, ports, nmapFlags)))

    completed, tasks = await asyncio.wait(tasks)

    print(f"[-] Writing {len(completed)} Result(s)")
    with open(outFile, 'w') as f:
        f.write("Host,Port,Service,Version\n")
        for task in completed:
            for line in task.result():
                f.write(f"{line}\n")

    print(f"[+] Results written to {outFile}")
    sys.stdout.flush()
    sys.stderr.flush()


if __name__ == "__main__":
    print("*** It's like a finger pointing away to the moon.")
    print("*** Don't concentrate on the finger")
    print("*** Or you will miss all that heavenly glory.\n")

    parser = argparse.ArgumentParser(
        "Performs masscan and passes the output to Nmap version scan")
    parser.add_argument(
        "-t", "--target",
        required=True,
        help="Single target or file of newline seperated target(s) to scan")
    parser.add_argument(
        "-n", "--nmap-flags",
        default="-Pn -sV -T5 --min-rate 1500",
        help="Flags for Nmap")
    parser.add_argument(
        "-m", "--masscan-flags",
        default="-p1-65535 --rate=20000 --wait=1",
        help="Flags for masscan")
    parser.add_argument(
        "-o", "--out-file",
        default="heaven.csv",
        help="Final result output")
    parser.add_argument(
        "-p", "--task-pool-size",
        type=int, default=10,
        help="Set the maximum number of concurrent scans")

    args = parser.parse_args()

    asyncio.run(main(args.target, args.nmap_flags,
                     args.masscan_flags,
                     args.out_file,
                     args.task_pool_size))
