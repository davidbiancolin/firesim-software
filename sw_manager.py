#!/usr/bin/env python3
import sys
import argparse
import subprocess as sp
import os
import shutil
import pprint
import doit
import re
import logging
import time
import random
import string
from util.config import *
from util.util import *
import pathlib as pth
import tempfile

if 'RISCV' not in os.environ:
    sys.exit("Please source firesim/sourceme-manager-f1.sh first\n")

def main():
    parser = argparse.ArgumentParser(
        description="Build and run (in spike or qemu) boot code and disk images for firesim")
    parser.add_argument('-c', '--config',
                        help='Configuration file to use (defaults to br-disk.json)',
                        nargs='?', default=os.path.join(root_dir, 'workloads', 'br-disk.json'), dest='config_file')
    parser.add_argument('--workdir', help='Use a custom workload directory', default=os.path.join(root_dir, 'workloads'))
    parser.add_argument('-v', '--verbose',
                        help='Print all output of subcommands to stdout as well as the logs', action='store_true')
    subparsers = parser.add_subparsers(title='Commands', dest='command')

    # Build command
    build_parser = subparsers.add_parser(
        'build', help='Build an image from the given configuration.')
    build_parser.set_defaults(func=handleBuild)
    build_parser.add_argument('-j', '--job', nargs='?', default='all',
            help="Build only the specified JOB (defaults to 'all')")
    build_parser.add_argument('-i', '--initramfs', action='store_true', help="Build an image with initramfs instead of a disk")

    # Launch command
    launch_parser = subparsers.add_parser(
        'launch', help='Launch an image on a software simulator (defaults to qemu)')
    launch_parser.set_defaults(func=handleLaunch)
    launch_parser.add_argument('-s', '--spike', action='store_true',
            help="Use the spike isa simulator instead of qemu")
    launch_parser.add_argument('-j', '--job', nargs='?', default='all',
            help="Launch the specified job. Defaults to running the base image.")
    launch_parser.add_argument('-i', '--initramfs', action='store_true', help="Launch the initramfs version of this workload")

    args = parser.parse_args()
    setRunName(args.config_file, args.command)

    args.workdir = os.path.abspath(args.workdir)
    # args.config_file = os.path.join(args.workdir, args.config_file)
    args.config_file = os.path.abspath(args.config_file)

    initLogging(args.verbose)
    log = logging.getLogger()

    # Load all the configs from the workload directory
    cfgs = ConfigManager([args.workdir])
    targetCfg = cfgs[args.config_file]
    
    # Jobs are named with their base config internally 
    if args.command == 'build' or args.command == 'launch':
        if args.initramfs:
            targetCfg['initramfs'] = True
            if 'jobs' in targetCfg:
                for j in targetCfg['jobs'].values():
                    j['initramfs'] = True

        if args.job != 'all':
            if 'jobs' in targetCfg: 
                args.job = targetCfg['name'] + '-' + args.job
            else:
                print("Job " + args.job + " requested, but no jobs specified in config file\n")
                parser.print_help()

    args.func(args, cfgs)

class doitLoader(doit.cmd_base.TaskLoader):
    workloads = []

    def load_tasks(self, cmd, opt_values, pos_args):
        task_list = [doit.task.dict_to_task(w) for w in self.workloads]
        config = {'verbosity': 2}
        return task_list, config

# Checks if the linux kernel used by this config needs to be rebuilt
# Note: this is intended to be used by the doit 'uptodate' feature
# XXX Note: this doesn't actually work because linux unconditionally runs some
#           rules (it always reports false).
def checkLinuxUpToDate(config):
    retcode = sp.call(['make', '-q', 'ARCH=riscv', 'vmlinux'], cwd=config['linux-src'])
    if retcode == 0:
        return True
    else:
        return False

def addDep(loader, config):

    # Add a rule for the binary
    file_deps = []
    task_deps = []
    if 'linux-config' in config:
        file_deps.append(config['linux-config'])

    loader.workloads.append({
            'name' : config['bin'],
            'actions' : [(makeBin, [config])],
            'targets' : [config['bin']],
            'file_dep': file_deps,
            'task_dep' : task_deps,
            'uptodate' : [(checkLinuxUpToDate, [config])]
            })

    # Add a rule for the initramfs version if requested
    # Note that we need both the regular bin and initramfs bin if the base
    # workload needs an init script
    if 'initramfs' in config:
        file_deps = [config['img']]
        task_deps = [config['img']]
        if 'linux-config' in config:
            file_deps.append(config['linux-config'])

        loader.workloads.append({
                'name' : config['bin'] + '-initramfs',
                'actions' : [(makeBin, [config], {'initramfs' : True})],
                'targets' : [config['bin'] + '-initramfs'],
                'file_dep': file_deps,
                'task_dep' : task_deps
                })

    # Add a rule for the image (if any)
    file_deps = []
    task_deps = []
    if 'img' in config:
        if 'base-img' in config:
            task_deps = [config['base-img']]
            file_deps = [config['base-img']]
        if 'files' in config:
            for fSpec in config['files']:
                # Add directories recursively
                if os.path.isdir(fSpec.src):
                    for root, dirs, files in os.walk(fSpec.src):
                        for f in files:
                            fdep = os.path.join(root, f)
                            # Ignore symlinks
                            if not os.path.islink(fdep):
                                file_deps.append(fdep)
                else:
                    # Ignore symlinks
                    if not os.path.islink(fSpec.src):
                        file_deps.append(fSpec.src)			
        if 'guest-init' in config:
            file_deps.append(config['guest-init'])
            task_deps.append(config['bin'])
        if 'runSpec' in config and config['runSpec'].path != None:
            file_deps.append(config['runSpec'].path)
        if 'cfg-file' in config:
            file_deps.append(config['cfg-file'])
        
        loader.workloads.append({
            'name' : config['img'],
            'actions' : [(makeImage, [config])],
            'targets' : [config['img']],
            'file_dep' : file_deps,
            'task_dep' : task_deps
            })

# Generate a task-graph loader for the doit "Run" command
# Note: this doesn't depend on the config or runtime args at all. In theory, it
# could be cached, but I'm not going to bother unless it becomes a performance
# issue.
def buildDepGraph(cfgs):
    loader = doitLoader()

    # Define the base-distro tasks
    for d in distros:
        dCfg = cfgs[d]
        if 'img' in dCfg:
            loader.workloads.append({
                    'name' : dCfg['img'],
                    'actions' : [(dCfg['builder'].buildBaseImage, [])],
                    'targets' : [dCfg['img']],
                    'uptodate': [(dCfg['builder'].upToDate, [])]
                })

    # Non-distro configs 
    for cfgPath in (set(cfgs.keys()) - set(distros)):
        config = cfgs[cfgPath]
        addDep(loader, config)

        if 'jobs' in config.keys():
            for jCfg in config['jobs'].values():
                addDep(loader, jCfg)

    return loader

def handleHostInit(config):
    log = logging.getLogger()
    if 'host-init' in config:
       log.info("Applying host-init: " + config['host-init'])
       if not os.path.exists(config['host-init']):
           raise ValueError("host-init script " + config['host-init'] + " not found.")

       run([config['host-init']], cwd=config['workdir'])
 
def handleBuild(args, cfgs):
    loader = buildDepGraph(cfgs)
    config = cfgs[args.config_file]

    handleHostInit(config)
    binList = [config['bin']]
    imgList = []
    if 'img' in config:
        imgList.append(config['img'])

    if 'initramfs' in config:
        binList.append(config['bin'] + '-initramfs')

    if 'jobs' in config.keys():
        if args.job == 'all':
            for jCfg in config['jobs'].values():
                handleHostInit(jCfg)
                binList.append(jCfg['bin'])
                if 'initramfs' in jCfg:
                    binList.append(jCfg['bin'] + '-initramfs')
                if 'img' in jCfg:
                    imgList.append(jCfg['img'])
        else:
            jCfg = config['jobs'][args.job]
            handleHostInit(jCfg)
            binList.append(jCfg['bin'])
            if 'initramfs' in jCfg:
                binList.append(jCfg['bin'] + '-initramfs')
            if 'img' in jCfg:
                imgList.append(jCfg['img'])

    # The order isn't critical here, we should have defined the dependencies correctly in loader 
    ret = doit.doit_cmd.DoitMain(loader).run(binList + imgList)
    if ret != 0:
        raise RuntimeError("Error while building workload")

# Returns a command string to luanch the given config in spike. Must be called with shell=True.
def getSpikeCmd(config, initramfs=False):
    if initramfs:
        return 'spike -p4 -m4096 ' + config['bin'] + '-initramfs'
    elif 'img' not in config:
        return 'spike -p4 -m4096 ' + config['bin']
    else:
        raise ValueError("Spike does not support disk-based configurations")

# Returns a command string to luanch the given config in qemu. Must be called with shell=True.
def getQemuCmd(config, initramfs=False):
    log = logging.getLogger()

    if initramfs:
        exe = config['bin'] + '-initramfs'
    else:
        exe = config['bin']

    cmd = ['qemu-system-riscv64',
           '-nographic',
           '-smp', '4',
           '-machine', 'virt',
           '-m', '4G',
           '-kernel', exe,
           '-object', 'rng-random,filename=/dev/urandom,id=rng0',
           '-device', 'virtio-rng-device,rng=rng0',
           '-device', 'virtio-net-device,netdev=usernet',
           '-netdev', 'user,id=usernet,hostfwd=tcp::10000-:22']

    if 'img' in config and not initramfs:
        cmd = cmd + ['-device', 'virtio-blk-device,drive=hd0',
                     '-drive', 'file=' + config['img'] + ',format=raw,id=hd0']
        cmd = cmd + ['-append', '"ro root=/dev/vda"']

    return " ".join(cmd)

def handleLaunch(args, cfgs):
    log = logging.getLogger()
    baseConfig = cfgs[args.config_file]
    if 'jobs' in baseConfig.keys() and args.job != 'all':
        # Run the specified job
        config = cfgs[args.config_file]['jobs'][args.job]
    else:
        # Run the base image
        config = cfgs[args.config_file]
 
    baseResDir = os.path.join(res_dir, getRunName())
    runResDir = os.path.join(baseResDir, config['name'])
    uartLog = os.path.join(runResDir, "uartlog")
    os.makedirs(runResDir)

    if args.spike:
        if 'img' in config and 'initramfs' not in config:
            sys.exit("Spike currently does not support disk-based " +
                    "configurations. Please use an initramfs based image.")
        cmd = getSpikeCmd(config, args.initramfs)
    else:
        cmd = getQemuCmd(config, args.initramfs)

    sp.check_call(cmd + " | tee " + uartLog, shell=True)

    if 'outputs' in config:
        outputSpec = [ FileSpec(src=f, dst=runResDir + "/") for f in config['outputs']] 
        copyImgFiles(config['img'], outputSpec, direction='out')

    if 'post_run_hook' in config:
        log.info("Running post_run_hook script: " + config['post_run_hook'])
        run(config['post_run_hook'] + " " + baseResDir, cwd=config['workdir'], shell=True)

    log.info("Run output available in: " + os.path.dirname(runResDir))

# Now build linux/bbl
def makeBin(config, initramfs=False):
    log = logging.getLogger()

    # We assume that if you're not building linux, then the image is pre-built (e.g. during host-init)
    if 'linux-config' in config:
        linuxCfg = os.path.join(config['linux-src'], '.config')
        shutil.copy(config['linux-config'], linuxCfg)

        if initramfs:
            with tempfile.NamedTemporaryFile(suffix='.cpio') as tmpCpio:
                toCpio(config, config['img'], tmpCpio.name)
                convertInitramfsConfig(linuxCfg, tmpCpio.name)
                run(['make', 'ARCH=riscv', 'olddefconfig'], cwd=config['linux-src'])
                run(['make', 'ARCH=riscv', 'vmlinux', jlevel], cwd=config['linux-src'])
        else: 
            run(['make', 'ARCH=riscv', 'vmlinux', jlevel], cwd=config['linux-src'])

        # BBL doesn't seem to detect changes in its configuration and won't rebuild if the payload path changes
        if os.path.exists('riscv-pk/build'):
            shutil.rmtree('riscv-pk/build')
        os.mkdir('riscv-pk/build')

        run(['../configure', '--host=riscv64-unknown-elf',
            '--with-payload=' + os.path.join(config['linux-src'], 'vmlinux')], cwd='riscv-pk/build')
        run(['make', jlevel], cwd='riscv-pk/build')

        if initramfs:
            shutil.copy('riscv-pk/build/bbl', config['bin'] + '-initramfs')
        else:
            shutil.copy('riscv-pk/build/bbl', config['bin'])

def makeImage(config):
    log = logging.getLogger()

    if 'base-img' in config:
        shutil.copy(config['base-img'], config['img'])

  
    if 'files' in config:
        log.info("Applying file list: " + str(config['files']))
        copyImgFiles(config['img'], config['files'], 'in')

    if 'guest-init' in config:
        log.info("Applying init script: " + config['guest-init'])
        if not os.path.exists(config['guest-init']):
            raise ValueError("Init script " + config['guest-init'] + " not found.")

        # Apply and run the init script
        init_overlay = config['builder'].generateBootScriptOverlay(config['guest-init'])
        applyOverlay(config['img'], init_overlay)
        print("Launching: " + config['bin'])
        sp.check_call(getQemuCmd(config), shell=True)

        # Clear the init script
        run_overlay = config['builder'].generateBootScriptOverlay(None)
        applyOverlay(config['img'], run_overlay)

    if 'runSpec' in config:
        spec = config['runSpec']
        if spec.command != None:
            log.info("Applying run command: " + spec.command)
            scriptPath = genRunScript(spec.command)
        else:
            log.info("Applying run script: " + spec.path)
            scriptPath = spec.path

        if not os.path.exists(scriptPath):
            raise ValueError("Run script " + scriptPath + " not found.")

        run_overlay = config['builder'].generateBootScriptOverlay(scriptPath)
        applyOverlay(config['img'], run_overlay)

# Apply the overlay directory "overlay" to the filesystem image "img"
# Note that all paths must be absolute
def applyOverlay(img, overlay):
    log = logging.getLogger()
    copyImgFiles(img, [FileSpec(src=os.path.join(overlay, "*"), dst='/')], 'in')
    
# Copies a list of type FileSpec ('files') to/from the destination image (img)
#   img - path to image file to use
#   files - list of FileSpecs to use
#   direction - "in" or "out" for copying files into or out of the image (respectively)
def copyImgFiles(img, files, direction):
    log = logging.getLogger()

    if not os.path.exists(mnt):
        run(['mkdir', mnt])

    # The guestmount options (and rsync without chown) are to avoid dependence
    # on sudo, but they require libguestfs-tools to be installed. There are
    # other sudo dependencies in fedora.py though.
    # run(['guestmount', '-a', img, '-m', '/dev/sda', mnt])
    # run(['fuse-ext2', '-o', 'rw+', img, mnt])
    run(['sudo', 'mount', '-o', 'loop', img, mnt])
    try:
        for f in files:
            # Overlays may not be owned by root, but the filesystem must be.
            # Rsync lets us chown while copying.
            # Note: shell=True because f.src is allowed to contain globs
            # Note: os.path.join can't handle overlay-style concats (e.g. join('foo/bar', '/baz') == '/baz')
            # run('cp -a ' + f.src + " " + os.path.normpath(mnt + f.dst), shell=True)
            if direction == 'in':
                run('sudo rsync -a --chown=root:root ' + f.src + " " + os.path.normpath(mnt + f.dst), shell=True)
            elif direction == 'out':
                uid = os.getuid()
                run('sudo rsync -a --chown=' + str(uid) + ':' + str(uid) + ' ' + os.path.normpath(mnt + f.src) + " " + f.dst, shell=True)
            else:
                raise ValueError("direction option must be either 'in' or 'out'")
    finally:
        # run(['guestunmount', mnt])
        # run(['fusermount', '-u', mnt])
        run(['sudo', 'umount', mnt])

def testInclude():
    print("HEYO!")
    return 42

if __name__ == "__main__":
    main()