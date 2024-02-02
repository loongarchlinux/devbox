#!/usr/bin/env python
# -*- encoding:utf-8 -*-
# FileName: update-repos.py
# SPDX-License-Identifier: GPL-2.0-or-later

__author__   = "yetist"
__copyright__= "Copyright (c) 2023 Xiaotian Wu <yetist@gmail.com>"
__license__  = "GPL-2.0-or-later"

import os
import sys
import getopt
import subprocess
import tempfile
import shlex
import json
import time
import gitlab
import shutil
import fnmatch

class Options:
    verbose = False
    sync = True
    push = False

class Result:
    pass

class bcolors:
    CEND    = '\33[0m'
    CRED    = '\33[31m'
    CBOLD   = '\33[1m'
    CGREEN  = '\33[32m'
    CBLUE   = '\33[34m'

def print_info(msg):
    print(bcolors.CGREEN + msg + bcolors.CEND)

def print_cmdline(msg):
    print(bcolors.CBLUE + '>>> [' + msg + '] <<<' + bcolors.CEND)

def print_error(msg):
    print(bcolors.CRED + '!!! ERROR: ' + msg + bcolors.CEND)

def run_cmd(command, times=1, quiet=False):
    if not quiet:
        print_cmdline(command)
    factor = 2

    result = Result()
    result.exit_code = -1
    while times > 0 and result.exit_code != 0:
        output = []
        p = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE)
        for line in iter(p.stdout.readline, b''):
            output.append(line.decode('utf-8'))
            if not quiet:
                print(line.decode('utf-8'), end='', flush=True)
        p.stdout.close()
        p.wait()
        times = times - 1
        result.exit_code = p.returncode
        result.stdout = ''.join(output)
        if p.returncode != 0:
            print_error('run command [%s].' % command)
            factor = factor * 2
            time.sleep(factor * 10)
    result.command = command

    return result

def load_json(path):
    with open(path, 'r') as openfile:
        json_object = json.load(openfile)
    return json_object

def write_json(path, obj):
    json_object = json.dumps(obj, indent=4)

    with open(path, "w") as outfile:
        outfile.write(json_object)

class Repo:
    def __init__(self, repo='core', today=None, arch='x86_64', host='https://geo.mirror.pkgbuild.com'):
        self.host = host
        self.repo = repo
        self.arch = arch
        self.cwd = os.getcwd()
        self.work = tempfile.mkdtemp()
        self.conf = self._write_conf()
        self.cache = os.path.expanduser('~/.cache/archlinux.packages')
        self.packages = {}
        if today == None:
            self.today = time.strftime('%Y%m%d')
        else:
            self.today = today

        if not os.path.isdir(self.cache):
            os.makedirs(self.cache)

    def _write_conf(self):
        conf=f"""
architecture = "{self.arch}"
database_compression = "gz"

[syncdb_settings]
desc_version = 1
files_version = 1

[management_repo]
directory = "package"

[[repositories]]
architecture = "{self.arch}"
build_requirements_exist = false
name = "{self.repo}"
debug  = "{self.repo}-debug"
staging = "{self.repo}-staging"
staging_debug = "{self.repo}-staging-debug"
testing = "{self.repo}-testing"
testing_debug = "{self.repo}-testing-debug"
management_repo = {{directory = "{self.work}/management"}}
"""
        conf_path = f'{self.work}/default.conf'
        f = open(conf_path, 'w', encoding="utf-8")
        f.write(conf)
        f.close()
        return conf_path

    def _download (self):
        for repo in (self.repo, self.repo+'-testing', self.repo+'-staging'):
            url = f'{self.host}/{repo}/os/{self.arch}/{repo}.db.tar.gz'

            repo_path = f'{self.work}/{repo}.db.tar.gz'
            run_cmd(f'wget -q -O {repo_path} {url}')

            if repo.endswith('-staging'):
                run_cmd(f'repod-file -c {self.conf} repo importdb {repo_path} -S {self.repo}')
            elif repo.endswith('-testing'):
                run_cmd(f'repod-file -c {self.conf} repo importdb {repo_path} -T {self.repo}')
            else:
                run_cmd(f'repod-file -c {self.conf} repo importdb {repo_path} {self.repo}')

    def load(self):
        if len(self.packages) > 0:
            return self.packages

        json_file = f'{self.cwd}/{self.repo}-{self.today}.json'
        print(json_file)
        if os.path.isfile(json_file):
            self.packages = load_json(json_file)
        else:
            self._download()
            for repo in (self.repo, self.repo+'-testing', self.repo+'-staging'):
                lst = []
                parent = f'{self.work}/management/{self.arch}/{repo}'
                files = os.listdir(parent)
                files.sort()
                for i in files:
                    data = load_json(f'{parent}/{i}')

                    name = data['base']
                    version = data['version']
                    lst.append({'name': name, 'version': version})
                self.packages[repo] = lst
            write_json(json_file, self.packages)
        return self.packages

    def clone(self):
        self.load()
        old_dir = os.getcwd()
        for repo in self.packages.keys():
            repo_dir = os.path.join(self.cache, repo)
            lines = ["{name} {version}\n".format(**pkg) for pkg in self.packages[repo]]
            if len(lines) > 0:
                if not os.path.isdir(repo_dir):
                    os.makedirs(repo_dir)

                with open(f'{repo_dir}/.version', 'w') as f:
                    f.writelines(lines)

                for pkg in self.packages[repo]:
                    name = pkg['name']
                    version = pkg['version']
                    os.chdir(repo_dir)
                    run_cmd(f'pkgctl repo clone --protocol https --switch {version} {name}', times=5)
        os.chdir(self.cwd)

    def check(self, sync=True):
        self.load()
        error = False
        for repo in self.packages.keys():
            repo_dir = os.path.join(self.cache, repo)
            for pkg in self.packages[repo]:
                name = pkg['name']
                version = pkg['version']
                repo_path = os.path.join(repo_dir, name)

                try:
                    os.chdir(repo_path)
                except FileNotFoundError:
                    error = True
                    print_error(f'{repo}/{name} is not exists!')
                    if sync:
                        os.chdir(repo_dir)
                        result = run_cmd(f'pkgctl repo clone --protocol https --switch {version} {name}', times=3)
                    continue
                out_bytes = subprocess.check_output(['git', 'describe', '--tags', 'HEAD'])
                tag = out_bytes.decode('utf-8').strip()
                if version.replace(':', '-') != tag:
                    error = True
                    print_error(f'{repo}/{name}-{version}: tag is: {tag}')
                    if sync:
                        os.chdir(repo_path)
                        run_cmd(f'git fetch origin')
                        os.chdir(repo_dir)
                        result = run_cmd(f'pkgctl repo clone --protocol https --switch {version} {name}', times=3)

            db_repos = [pkg['name'] for pkg in self.packages[repo]]
            git_repos = os.listdir(repo_dir)
            git_repos.sort()
            for i in git_repos:
                if i == '.version':
                    continue
                if i not in db_repos:
                    error = True
                    git_repo = os.path.join(repo_dir, i)
                    print(f'{i} is not in {repo}, removing {git_repo}')
                    shutil.rmtree(git_repo)
        os.chdir(self.cwd)
        if not error:
            return True
        else:
            return self.check(True)

def arch_add_loong64(root_dir):
    pkgbuilds = []
    for dirpath, dirs, files in os.walk(root_dir):
        for filename in fnmatch.filter(files, 'PKGBUILD'):
            pkgbuilds.append(os.path.join(dirpath, filename))

    for pkg in pkgbuilds:
        d = open(pkg).readlines()
        out=[]
        for i in d:
            if i.startswith('arch'):
                if not i.find('loong64') > 0:
                    if i.find("'") > 0:
                        i = i.replace("x86_", "loong64' 'x86_")
                    elif i.find("\"") > 0:
                        i = i.replace("x86_", "loong64\" \"x86_")
                    else:
                        i = i.replace("x86_", "loong64 x86_")
            out.append(i)
        fd = open(pkg, "w+")
        fd.write("".join(out))
        fd.close()

def gitlab_dump_repos(path, debug=False, verbose=True):
    root, ext = os.path.splitext(path)
    repos = []

    gl = gitlab.Gitlab('https://gitlab.archlinux.org', api_version=4)
    if debug:
        gl.enable_debug()

    group = gl.groups.get(11323)
    for project in group.projects.list(get_all=True, order_by='name', sort='asc'):
        if verbose:
            print(r'dump {project.name} info...')
        repo = {}
        repo['name'] = project.name
        repo['path'] = project.path
        repo['id'] = project.id
        repo['url'] = project.http_url_to_repo
        repos.append(repo)
    write_json(r'{root}.json', repos)

def gitlab_dump_tags(path, debug=False, verbose=True):
    root, ext = os.path.splitext(path)
    repos_with_tags = []

    gl = gitlab.Gitlab('https://gitlab.archlinux.org', api_version=4)
    if debug:
        gl.enable_debug()

    repos = load_json(path)
    for repo in repos:
        if verbose:
            print(r'dump {name} tags ...'.format(**repo))
        repo['tags'] = []
        proj = gl.projects.get(repo['id'])
        for tag in proj.tags.list(get_all=True, order_by='name'):
            repo['tags'].append(tag.name)
        repos_with_tags.append(repo)
    write_json(r'{root}-tags.json', repos_with_tags)

def main2(opts):

    core = Repo('core')
    extra = Repo('extra')
    if opts.sync:
        core_check = core.check()
        extra_check = extra.check()

    if not opts.push:
        return

    if core_check and extra_check:
        date = core.today
        cache_dir = core.cache
        cwd_dir = core.cwd
        repos_dir = os.path.join(cwd_dir, 'repos')
        core_dir = os.path.join(cwd_dir, 'core')
        extra_dir = os.path.join(cwd_dir, 'extra')

        commit_log = os.path.join(cwd_dir, 'commit.txt')
        with open(commit_log, "w") as outfile:
            outfile.write(f'import repos from Archlinux\n\ndate: {date}\n')

        if os.path.isdir(core_dir) and os.path.isdir(extra_dir) and os.path.isdir(repos_dir):
            os.chdir(f'{repos_dir}')
            run_cmd(f'git switch -f main')
            run_cmd(f'rm -rf ./*')
            run_cmd(f'rsync -a --delete --exclude=.git --exclude=.SRCINFO {cache_dir}/ .')
            arch_add_loong64(repos_dir)
            run_cmd(f'git add *')
            run_cmd(f'git commit -a -F {commit_log}')
            run_cmd(f'git push origin main')

            os.chdir(f'{core_dir}')
            run_cmd(f'git switch -f arch')
            run_cmd(f'rm -rf ./*')
            run_cmd(f'rsync -a --delete --exclude=.git --exclude=.version {repos_dir}/core/ .')
            run_cmd(f'git add *')
            run_cmd(f'git commit -a -F {commit_log}')
            run_cmd(f'git tag -a -m x86.{date} x86.{date}')
            run_cmd(f'git push origin arch x86.{date}')

            os.chdir(f'{extra_dir}')
            run_cmd(f'git switch -f arch')
            run_cmd(f'rm -rf ./*')
            run_cmd(f'rsync -a --delete --exclude=.git --exclude=.version {repos_dir}/extra/ .')
            run_cmd(f'git add *')
            run_cmd(f'git commit -a -F {commit_log}')
            run_cmd(f'git tag -a -m x86.{date} x86.{date}')
            run_cmd(f'git push origin arch x86.{date}')
        else:
            print('"core", "extra", or "repos" does not exists.')
    elif not core_check:
        print('"core" check failed.')
    elif not extra_check:
        print('"extra" check failed.')

    #gitlab_dump_repos('gitlab.json')
    #gitlab_dump_tags('gitlab.json')


def usage():
    print(f'{sys.argv[0]} <OPTIONS>')
    print('')
    print('OPTIONS:')
    print('-h --help       Show this help message')
    print('-s --sync       Sync local git repos from archlinux gitlab')
    print('-p --push       Push archloong repos to remote github repo')
    print('-v --verbose    Verbose output')

def main():
    options = Options()
    try:
        opts, args = getopt.getopt(sys.argv[1:], "hspv", ["help", "sync", "push", "verbose"])
    except getopt.GetoptError as err:
        print(err)
        usage()
        sys.exit(2)
    for o, a in opts:
        if o in ("-v", "--verbose"):
            options.verbose = True
        elif o in ("-h", "--help"):
            usage()
            sys.exit()
        elif o in ("-s", "--sync"):
            options.sync = True
        elif o in ("-p", "--push"):
            options.push = True
        else:
            assert False, "unhandled option"

    main2(options)

if __name__ == "__main__":
    main()
