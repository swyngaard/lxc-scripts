#!/usr/bin/env python3

"""
Create a new LXC container with the PyDev IDE installed

usage: pydev.py [-h] [-v] [prefix]
"""
import lxc, os, sys, string, random, json, atexit, argparse, urllib.request, subprocess, stat

start_pydev="""#!/bin/sh
CONTAINER={}
CMD_LINE="eclipse/eclipse $*"

STARTED=false

if ! lxc-wait -n $CONTAINER -s RUNNING -t 0; then
    lxc-start -n $CONTAINER -d
    lxc-wait -n $CONTAINER -s RUNNING
    STARTED=true
fi

lxc-attach --clear-env -n $CONTAINER -- sudo -u {} -i env DISPLAY=$DISPLAY $CMD_LINE

if [ "$STARTED" = "true" ]; then
    lxc-stop -n $CONTAINER -t 10
fi
"""

def generate_password(length=8, characters=string.ascii_uppercase+string.ascii_lowercase+string.digits):
    """
    Simple password generator
    """
    return ''.join(random.SystemRandom().choice(characters) for _ in range(length))

def get_stable_codename():
    """
    Return the codename of the latest debian stable release or None on failure
    """
    try:
        url = "http://ftp.debian.org/debian/dists/stable/Release"
        with urllib.request.urlopen(url) as response:
            lines = response.readlines()
            release = str(lines[4].split()[1])
            release = release.strip("b'")
            return release.rstrip("\n'")
    except OSError:
        pass

def container_run_command(container, description, command_list, stdin_fd=0, stdout_fd=1, stderr_fd=2, verbose=False, debug=False):

    if container.defined and container.running:
        if verbose:
            print(description + "...")

        with open(os.devnull, 'w') as dev_null_file:
            dev_null = dev_null_file.fileno()
            if not debug:
                stdout_fd = dev_null
                stderr_fd = dev_null
            if container.attach_wait(lxc.attach_run_command, command_list, stdin=stdin_fd, stdout=stdout_fd, stderr=stderr_fd, env_policy=lxc.LXC_ATTACH_CLEAR_ENV, extra_env_vars=["TERM=xterm"]):
                print("Error: {}!".format(description), file=sys.stderr)
                sys.exit(1)
    else:
        print("Error: Container does not exist or is not running!", file=sys.stderr)
        sys.exit(1)


def container_pipe_command(container, description, first_cmd, second_cmd, verbose=False, debug=False):
    stderr_file = None
    if not debug:
        stderr_file = subprocess.DEVNULL
    with subprocess.Popen(first_cmd, stdout=subprocess.PIPE, stderr=stderr_file) as pipe_cmd:
        container_run_command(container, description, second_cmd, stdin_fd=pipe_cmd.stdout, verbose=verbose, debug=debug)

def write_file(file_path, text):
    try:
        with open(file_path, 'w') as output:
            output.write(text)
    except(OSError, IOError):
        return False
    return True

def chmod_file(file_path, permissions):
    try:
        os.chmod(file_path, permissions)
    except(OSError, IOError):
        return False
    return True

def main(prefix="test", verbose=False):
    user_name = prefix + "_user"
    user_info = prefix[0].upper() + prefix[1:].lower() + " User"
    user_password = generate_password()
    user_home = "/home/" + user_name
    user_dir = user_home + '/'

    debian_packages = ["python3", "python3-pip", "python3-psycopg2", "adduser", "sudo", "curl", "git"]
    gui_packages = ["libgtk2.0-0", "libxtst6"]
    eclipse_repos = ["http://pydev.org/updates", "http://download.eclipse.org/releases/neon", "http://eclipse.kacprzak.org/updates"]
    eclipse_packages = ["org.python.pydev.feature.feature.group", "org.eclipse.egit.feature.group", "org.eclipse.tm.terminal.feature.feature.group", "org.kacprzak.eclipse.django.feature.feature.group"]
    python_packages = ["Django==1.10"]

    #TODO find the latest release of java and eclipse
    java_url = "https://edelivery.oracle.com/otn-pub/java/jdk/8u102-b14/jdk-8u102-linux-x64.tar.gz"
    eclipse_url = "http://download.eclipse.org/eclipse/downloads/drops4/R-4.6-201606061100/eclipse-platform-4.6-linux-gtk-x86_64.tar.gz"

    # Log output to stdout if specified as command line flag (WARNING the list is a hack!)
    logger = lambda text: [print(text + "..."), text][1] if verbose else text

    # Print error message to stderr and abort with error state (WARNING the list is a hack!)
    error_exit = lambda text: [print("Error: {}!".format(text), file=sys.stderr), sys.exit(1)]

    # Get latest debian stable release name
    debian_release = get_stable_codename()
    if not debian_release:
        debian_release = "jessie"

    container_name = "{}_pydev_{}".format(prefix, debian_release)

    host_script = os.path.join(os.path.expanduser("~"), ".local", "share", "lxc", container_name, "start-pydev")

    container = lxc.Container(container_name)

    if container.defined:
        error_exit("Container {} already exists!".format(container_name))

    description = logger("Creating filesystem")
    if not container.create("download", lxc.LXC_CREATE_QUIET, {"dist": "debian", "release": debian_release, "arch": "amd64"}):
        error_exit(description)

    # Stop and destroy container on error (WARNING the list is a hack!)
    cleanup_container = lambda: [container.stop(), container.destroy()]
    atexit.register(cleanup_container)

    description = logger("Appending mount entry to config")
    if not container.append_config_item("lxc.mount.entry", "/tmp/.X11-unix tmp/.X11-unix none bind,optional,create=dir"):
        error_exit(description)

    description = logger("Clearing UID and GID mappings")
    if not container.clear_config_item("lxc.id_map"):
        error_exit(description)

    description = logger("Appending new UID and GID mappings")
    if not (container.append_config_item("lxc.id_map", "u 0 100000 1000") and
            container.append_config_item("lxc.id_map", "g 0 100000 1000") and
            container.append_config_item("lxc.id_map", "u 1000 1000 1") and
            container.append_config_item("lxc.id_map", "g 1000 1000 1") and
            container.append_config_item("lxc.id_map", "u 1001 101001 64535") and
            container.append_config_item("lxc.id_map", "g 1001 101001 64535")):
        error_exit(description)

    description = logger("Saving configuration")
    if not container.save_config():
        error_exit(description)

    description = logger("Starting container")
    if not container.start():
        error_exit(description)

    # Wait for connectivity
    description = logger("Getting IP address")
    container_address = container.get_ips(timeout=120)[0]
    if not container_address:
        error_exit(description)

    run_command = lambda desc, cmd, debug=False: container_run_command(container, desc, cmd, verbose=verbose, debug=debug)
    pipe_command = lambda desc, cmd0, cmd1, debug=False: container_pipe_command(container, desc, cmd0, cmd1, verbose=verbose, debug=debug)

    run_command("Unmouting X11 directory", ["umount", "/tmp/.X11-unix"])

    run_command("Updating apt", ["apt-get", "update"])

    run_command("Installing debian packages", ["apt-get", "install", "-y"] + debian_packages)

    run_command("Installing GUI packages", ["apt-get", "install", "--no-install-recommends", "-y"] + gui_packages)

    run_command("Installing python packages", ["pip3", "install"] + python_packages)

    run_command("Adding user", ["adduser", "--disabled-password", "--gecos", user_info, user_name])

    pipe_command("Setting user password", ["echo", "{}:{}".format(user_name, user_password)], ["chpasswd"])

    # This command is needed to squash the sudo warning when executing the startup script
    run_command("Appending container name to /etc/hosts", ["bash", "-c", 'echo "127.0.1.1       {}" >> /etc/hosts'.format(container_name)])

    pipe_command("Downloading and extracting Java JDK", ["bash", "-c", 'curl -L -H "Cookie: oraclelicense=accept-securebackup-cookie" -k "{}"'.format(java_url)], ["su", "-", user_name, "-c", "mkdir {0} && tar xz -C {0} --strip-components 1".format("jdk")])

    pipe_command("Downloading and extracting Eclipse IDE", ["bash", "-c", 'curl -L -k "{}"'.format(eclipse_url)], ["su", "-", user_name, "-c", "tar xz"])

    # Escape the newline character so that it isn't interpreted by python but only by sed
    run_command("Updating Eclipse configuration", ["su", "-", user_name, "-c", 'sed -i "/-vmargs/i-data\\n{0}/workspace\\n-vm\\n{0}/jdk/bin/java" eclipse/eclipse.ini'.format(user_home)])

    run_command("Installing PyDev", ["su", "-", user_name, "-c", "eclipse/eclipse -application org.eclipse.equinox.p2.director -noSplash -repository {} -installIU {}".format(','.join(eclipse_repos), ','.join(eclipse_packages))])

    description = logger("Writing startup script")
    if not write_file(host_script, start_pydev.format(container_name, user_name)):
        error_exit(description)

    description = logger("Making script executable")
    if not chmod_file(host_script, stat.S_IRWXU | stat.S_IRGRP | stat.S_IROTH):
        error_exit(description)

    description = logger("Stopping container")
    if not container.stop():
        error_exit(description)

    logger("Success!")

    output = {"container_name"    : container_name,
              "container_address" : container_address,
              "user_name"         : user_name,
              "user_password"     : user_password,
              "startup_script"    : host_script}

    # Output details as JSON
    print(json.dumps(output, sort_keys=True, indent=4))

    # Don't cleanup container on success
    atexit.unregister(cleanup_container)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a new LXC container with the PyDev IDE installed")
    parser.add_argument("-v", "--verbose", action="store_true", help="display additional information to stdout")
    parser.add_argument("prefix", nargs='?', default="test", help="Specify a prefix for the container name. The resulting container name will be prefix_pydev_jessie. The default prefix is test.")
    args = vars(parser.parse_args())
    main(prefix=args["prefix"], verbose=args["verbose"])
