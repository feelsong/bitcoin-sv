#!/usr/bin/env python3
# Copyright (c) 2014-2016 The Bitcoin Core developers
# Copyright (c) 2017 The Bitcoin developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Run regression test suite.

This module calls down into individual test cases via subprocess. It will
forward all unrecognized arguments onto the individual test scripts.

Functional tests are disabled on Windows by default. Use --force to run them anyway.

For a description of arguments recognized by test scripts, see
`test/functional/test_framework/test_framework.py:BitcoinTestFramework.main`.

"""

import argparse
import configparser
import os
import time
import shutil
import signal
import sys
import subprocess
import tempfile
import re

BOLD = ("", "")
RED = ("", "")
GREEN = ("", "")
if os.name == 'posix':
    # primitive formatting on supported
    # terminal via ANSI escape sequences:
    BOLD = ('\033[0m', '\033[1m')
    RED = ("\033[0m", "\033[31m")
    GREEN = ("\033[0m", "\033[32m")

TEST_EXIT_PASSED = 0
TEST_EXIT_SKIPPED = 77

BASE_SCRIPTS = [
    # Longest test should go first, to favor running tests in parallel
    'wallet-hd.py',
    'walletbackup.py',
    # vv Tests less than 5m vv
    'p2p-fullblocktest.py',
    'fundrawtransaction.py',
    'p2p-compactblocks.py',
    # vv Tests less than 2m vv
    'wallet.py',
    'wallet-accounts.py',
    'wallet-dump.py',
    'listtransactions.py',
    # vv Tests less than 60s vv
    'sendheaders.py',
    'zapwallettxes.py',
    'importmulti.py',
    'mempool_limit.py',
    'merkle_blocks.py',
    'receivedby.py',
    'abandonconflict.py',
    'bip68-112-113-p2p.py',
    'rawtransactions.py',
    'reindex.py',
    # vv Tests less than 30s vv
    'zmq_test.py',
    'mempool_resurrect_test.py',
    'txn_doublespend.py --mineblock',
    'txn_clone.py',
    'getchaintips.py',
    'rest.py',
    'mempool_spendcoinbase.py',
    'mempool_reorg.py',
    'mempool_persist.py',
    'httpbasics.py',
    'multi_rpc.py',
    'proxy_test.py',
    'signrawtransactions.py',
    'disconnect_ban.py',
    'decodescript.py',
    'blockchain.py',
    'disablewallet.py',
    'net.py',
    'keypool.py',
    'p2p-mempool.py',
    'prioritise_transaction.py',
    'high_priority_transaction.py',
    'invalidblockrequest.py',
    'invalidtxrequest.py',
    'p2p-versionbits-warning.py',
    'preciousblock.py',
    'importprunedfunds.py',
    'signmessages.py',
    'nulldummy.py',
    'import-rescan.py',
    'rpcnamedargs.py',
    'listsinceblock.py',
    'p2p-leaktests.py',
    'abc-cmdline.py',
    'abc-p2p-fullblocktest.py',
    'abc-rpc.py',
    'mempool-accept-txn.py',
]

EXTENDED_SCRIPTS = [
    # Longest test should go first, to favor running tests in parallel
    'pruning.py',
    # vv Tests less than 20m vv
    'smartfees.py',
    # vv Tests less than 5m vv
    'maxuploadtarget.py',
    'mempool_packages.py',
    # vv Tests less than 2m vv
    'bip68-sequence.py',
    'getblocktemplate_longpoll.py',
    'p2p-timeouts.py',
    # vv Tests less than 60s vv
    'bip9-softforks.py',
    'p2p-feefilter.py',
    'rpcbind_test.py',
    # vv Tests less than 30s vv
    'assumevalid.py',
    'bip65-cltv.py',
    'bip65-cltv-p2p.py',
    'bipdersig-p2p.py',
    'bipdersig.py',
    'getblocktemplate_proposals.py',
    'txn_doublespend.py',
    'txn_clone.py --mineblock',
    'forknotify.py',
    'invalidateblock.py',
    'maxblocksinflight.py',
    'p2p-acceptblock.py',
]

# Place EXTENDED_SCRIPTS first since it has the 3 longest running tests
ALL_SCRIPTS = EXTENDED_SCRIPTS + BASE_SCRIPTS


def main():
    # Parse arguments and pass through unrecognised args
    parser = argparse.ArgumentParser(add_help=False,
                                     usage='%(prog)s [test_runner.py options] [script options] [scripts]',
                                     description=__doc__,
                                     epilog='''
    Help text and arguments for individual test script:''',
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--coverage', action='store_true',
                        help='generate a basic coverage report for the RPC interface')
    parser.add_argument(
        '--exclude', '-x', help='specify a comma-seperated-list of scripts to exclude. Do not include the .py extension in the name.')
    parser.add_argument('--extended', action='store_true',
                        help='run the extended test suite in addition to the basic tests')
    parser.add_argument('--force', '-f', action='store_true',
                        help='run tests even on platforms where they are disabled by default (e.g. windows).')
    parser.add_argument('--help', '-h', '-?',
                        action='store_true', help='print help text and exit')
    parser.add_argument('--jobs', '-j', type=int, default=4,
                        help='how many test scripts to run in parallel. Default=4.')
    args, unknown_args = parser.parse_known_args()

    # Create a set to store arguments and create the passon string
    tests = set(arg for arg in unknown_args if arg[:2] != "--")
    passon_args = [arg for arg in unknown_args if arg[:2] == "--"]

    # Read config generated by configure.
    config = configparser.ConfigParser()
    configfile = os.path.abspath(os.path.dirname(__file__)) + "/../config.ini"
    config.read_file(open(configfile))

    passon_args.append("--configfile=%s" % configfile)

    enable_wallet = config["components"].getboolean("ENABLE_WALLET")
    enable_utils = config["components"].getboolean("ENABLE_UTILS")
    enable_bitcoind = config["components"].getboolean("ENABLE_BITCOIND")

    if config["environment"]["EXEEXT"] == ".exe" and not args.force:
        # https://github.com/bitcoin/bitcoin/commit/d52802551752140cf41f0d9a225a43e84404d3e9
        # https://github.com/bitcoin/bitcoin/pull/5677#issuecomment-136646964
        print(
            "Tests currently disabled on Windows by default. Use --force option to enable")
        sys.exit(0)

    if not (enable_wallet and enable_utils and enable_bitcoind):
        print(
            "No functional tests to run. Wallet, utils, and bitcoind must all be enabled")
        print(
            "Rerun `configure` with -enable-wallet, -with-utils and -with-daemon and rerun make")
        sys.exit(0)

    # Build list of tests
    if tests:
        # Individual tests have been specified. Run specified tests that exist
        # in the ALL_SCRIPTS list. Accept the name with or without .py
        # extension.
        test_list = [t for t in ALL_SCRIPTS if
                     (t in tests or re.sub(".py$", "", t) in tests)]
    else:
        # No individual tests have been specified.
        # Run all base tests, and optionally run extended tests.
        test_list = BASE_SCRIPTS
        if args.extended:
            test_list += EXTENDED_SCRIPTS
            # TODO: BASE_SCRIPTS and EXTENDED_SCRIPTS are sorted by runtime
            # (for parallel running efficiency). This combined list will is no
            # longer sorted.

    # Remove the test cases that the user has explicitly asked to exclude.
    if args.exclude:
        for exclude_test in args.exclude.split(','):
            if exclude_test + ".py" in test_list:
                test_list.remove(exclude_test + ".py")

    if not test_list:
        print("No valid test scripts specified. Check that your test is in one "
              "of the test lists in test_runner.py, or run test_runner.py with no arguments to run all tests")
        sys.exit(0)

    if args.help:
        # Print help for test_runner.py, then print help of the first script
        # and exit.
        parser.print_help()
        subprocess.check_call(
            (config["environment"]["SRCDIR"] + '/test/functional/' + test_list[0]).split() + ['-h'])
        sys.exit(0)

    run_tests(
        test_list, config["environment"][
            "SRCDIR"], config["environment"]["BUILDDIR"],
        config["environment"]["EXEEXT"], args.jobs, args.coverage, passon_args)


def run_tests(test_list, src_dir, build_dir, exeext, jobs=1, enable_coverage=False, args=[]):
    # Set env vars
    if "BITCOIND" not in os.environ:
        os.environ["BITCOIND"] = build_dir + '/src/bitcoind' + exeext

    tests_dir = src_dir + '/test/functional/'
    flags = ["--srcdir={}".format(src_dir)] + args
    flags.append("--cachedir=%s/test/cache" % build_dir)

    if enable_coverage:
        coverage = RPCCoverage()
        flags.append(coverage.flag)
        print("Initializing coverage directory at {dir}\n".format(
            dir=coverage.dir))
    else:
        coverage = None

    if len(test_list) > 1 and jobs > 1:
        # Populate cache
        subprocess.check_output([tests_dir + 'create_cache.py'] + flags)

    # Run Tests
    all_passed = True
    time_sum = 0
    time0 = time.time()

    job_queue = TestHandler(jobs, tests_dir, test_list, flags)

    max_len_name = len(max(test_list, key=len))
    results = BOLD[1] + "%s | %s | %s\n\n" % (
        "TEST".ljust(max_len_name), "STATUS ", "DURATION") + BOLD[0]
    for _ in range(len(test_list)):
        (name, stdout, stderr, status, duration) = job_queue.get_next()
        all_passed = all_passed and status != "Failed"
        time_sum += duration

        print('\n' + BOLD[1] + name + BOLD[0] + ":")
        print('' if status == "Passed" else stdout + '\n', end='')
        print('' if stderr == '' else 'stderr:\n' + stderr + '\n', end='')
        print("Pass: {bold}{result}{unbold}, Duration: {duration}s\n".format(
            bold=BOLD[1], result=status, unbold=BOLD[0], duration=duration))
        result = "{name} | {passed} | {duration}s\n".format(name=name.ljust(
            max_len_name), passed=str(status).ljust(6), duration=duration)
        if status == "Passed":
            results += GREEN[1] + result + GREEN[0]
        else:
            results += RED[1] + result + RED[0]

    results += BOLD[1] + "\n{name} | {passed} | {duration}s (accumulated)".format(
        name="ALL".ljust(max_len_name), passed=str(all_passed).ljust(6), duration=time_sum) + BOLD[0]
    print(results)
    print("\nRuntime: {} s".format(int(time.time() - time0)))

    if coverage:
        coverage.report_rpc_coverage()

        print("Cleaning up coverage data")
        coverage.cleanup()

    sys.exit(not all_passed)


class TestHandler:

    """
    Trigger the testscrips passed in via the list.
    """

    def __init__(self, num_tests_parallel, tests_dir, test_list=None, flags=None):
        assert(num_tests_parallel >= 1)
        self.num_jobs = num_tests_parallel
        self.tests_dir = tests_dir
        self.test_list = test_list
        self.flags = flags
        self.num_running = 0
        # In case there is a graveyard of zombie bitcoinds, we can apply a
        # pseudorandom offset to hopefully jump over them.
        # (625 is PORT_RANGE/MAX_NODES)
        self.portseed_offset = int(time.time() * 1000) % 625
        self.jobs = []

    def get_next(self):
        while self.num_running < self.num_jobs and self.test_list:
            # Add tests
            self.num_running += 1
            t = self.test_list.pop(0)
            port_seed = ["--portseed={}".format(
                len(self.test_list) + self.portseed_offset)]
            log_stdout = tempfile.SpooledTemporaryFile(max_size=2**16)
            log_stderr = tempfile.SpooledTemporaryFile(max_size=2**16)
            self.jobs.append((t,
                              time.time(),
                              subprocess.Popen(
                                  (self.tests_dir + t).split() +
                                  self.flags + port_seed,
                                  universal_newlines=True,
                                  stdout=log_stdout,
                                  stderr=log_stderr),
                              log_stdout,
                              log_stderr))
        if not self.jobs:
            raise IndexError('pop from empty list')
        while True:
            # Return first proc that finishes
            time.sleep(.5)
            for j in self.jobs:
                (name, time0, proc, log_out, log_err) = j
                if os.getenv('TRAVIS') == 'true' and int(time.time() - time0) > 20 * 60:
                    # In travis, timeout individual tests after 20 minutes (to stop tests hanging and not
                    # providing useful output.
                    proc.send_signal(signal.SIGINT)
                if proc.poll() is not None:
                    log_out.seek(0), log_err.seek(0)
                    [stdout, stderr] = [l.read().decode('utf-8')
                                        for l in (log_out, log_err)]
                    log_out.close(), log_err.close()
                    if proc.returncode == TEST_EXIT_PASSED and stderr == "":
                        status = "Passed"
                    elif proc.returncode == TEST_EXIT_SKIPPED:
                        status = "Skipped"
                    else:
                        status = "Failed"
                    self.num_running -= 1
                    self.jobs.remove(j)
                    return name, stdout, stderr, status, int(
                        time.time() - time0)
            print('.', end='', flush=True)


class RPCCoverage(object):

    """
    Coverage reporting utilities for test_runner.

    Coverage calculation works by having each test script subprocess write
    coverage files into a particular directory. These files contain the RPC
    commands invoked during testing, as well as a complete listing of RPC
    commands per `bitcoin-cli help` (`rpc_interface.txt`).

    After all tests complete, the commands run are combined and diff'd against
    the complete list to calculate uncovered RPC commands.

    See also: test/functional/test_framework/coverage.py

    """

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="coverage")
        self.flag = '--coveragedir={}'.format(self.dir)

    def report_rpc_coverage(self):
        """
        Print out RPC commands that were unexercised by tests.

        """
        uncovered = self._get_uncovered_rpc_commands()

        if uncovered:
            print("Uncovered RPC commands:")
            print("".join(("  - {}\n".format(i)) for i in sorted(uncovered)))
        else:
            print("All RPC commands covered.")

    def cleanup(self):
        return shutil.rmtree(self.dir)

    def _get_uncovered_rpc_commands(self):
        """
        Return a set of currently untested RPC commands.

        """
        # This is shared from `test/functional/test-framework/coverage.py`
        reference_filename = 'rpc_interface.txt'
        coverage_file_prefix = 'coverage.'

        coverage_ref_filename = os.path.join(self.dir, reference_filename)
        coverage_filenames = set()
        all_cmds = set()
        covered_cmds = set()

        if not os.path.isfile(coverage_ref_filename):
            raise RuntimeError("No coverage reference found")

        with open(coverage_ref_filename, 'r') as f:
            all_cmds.update([i.strip() for i in f.readlines()])

        for root, dirs, files in os.walk(self.dir):
            for filename in files:
                if filename.startswith(coverage_file_prefix):
                    coverage_filenames.add(os.path.join(root, filename))

        for filename in coverage_filenames:
            with open(filename, 'r') as f:
                covered_cmds.update([i.strip() for i in f.readlines()])

        return all_cmds - covered_cmds


if __name__ == '__main__':
    main()