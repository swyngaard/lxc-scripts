#!/usr/bin/env python3

"""
Create and start a new LXC container running a PostgreSQL database

usage: postgresql.py [-h] [-v] [prefix]
"""
import lxc, os, sys, fileinput, socket, string, random, json, atexit, argparse, urllib.request

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
        return None
    
    return None

def append_hba_config(db_name, db_user, container_address):
    """
    Append IP mask to config file.
    
    This will allow users on the same subnet to connect to the database.
    """
    try:
        filename = "/etc/postgresql/9.4/main/pg_hba.conf"
        with open(filename, 'r'):
            pass
        with open(filename, 'a') as pg_conf:
            pg_conf.write("host\t\t{}\t\t{}\t\t{}0/24\t\tmd5\n".format(db_name, db_user, container_address.rstrip("1234567890")))
    except (OSError, IOError):
        return 1
    return 0

def update_main_config(container_name, host_name):
    """
    Modify line in config file to contain hostnames of container and host.
    
    This will allow for connections from the container and the host.
    """
    try:
        filename = "/etc/postgresql/9.4/main/postgresql.conf"
        with open(filename, 'r'):
            pass
        found = False
        for line in fileinput.input(files=(filename,), inplace=True):
            if line.startswith("#listen_addresses"):
                line = "listen_addresses = '{},{}'\n".format(container_name, host_name)
                found = True
            sys.stdout.write(line)
        
        if not found:
            return 1
    except (OSError, IOError):
        return 1
    return 0

def main(prefix="test", verbose=False):
    db_user = prefix + "_user"
    db_name = prefix + "_db"
    db_password = generate_password()
    host_name = socket.gethostname()
    
    # Log output to stdout if specified as command line flag
    logger = lambda text: print(text) if verbose else None
    
    # Print error message to stderr and abort with error state (WARNING the list is a hack!)
    error_exit = lambda text: [print(text, file=sys.stderr), sys.exit(1)]
    
    # Get latest debian stable release name
    debian_release = get_stable_codename()
    if not debian_release:
        debian_release = "jessie"
    
    container_name = "{}_postgresql_{}".format(prefix, debian_release)
    
    container = lxc.Container(container_name)
    
    if container.defined:
        error_exit("Container {} already exists!".format(container_name))
    
    logger("Creating filesystem...")
    if not container.create("download", lxc.LXC_CREATE_QUIET, {"dist": "debian", "release": debian_release, "arch": "amd64"}):
        error_exit("Failed to create the container filesystem!")
    
    # Stop and destroy container on error (WARNING the list is a hack!)
    cleanup_container = lambda: [container.stop(), container.destroy()]
    atexit.register(cleanup_container)
    
    logger("Starting container...")
    if not container.start():
        error_exit("Failed to start the container!")
    
    logger("Getting IP address...")
    # Wait for connectivity
    container_address = container.get_ips(timeout=120)[0]
    if not container_address:
        error_exit("Failed to connect to the container")
    
    # Disregard any output from commands issued
    dev_null_file = open(os.devnull, 'w')
    dev_null = dev_null_file.fileno()
    atexit.register(lambda: dev_null_file.close())
    
    logger("Updating apt...")
    if container.attach_wait(lxc.attach_run_command, ["apt-get", "update"], stdout=dev_null, env_policy=lxc.LXC_ATTACH_CLEAR_ENV):
        error_exit("Error updating apt!")
    
    logger("Installing packages...")
    if container.attach_wait(lxc.attach_run_command, ["apt-get", "install", "-y", "postgresql", "postgresql-client"], stdout=dev_null, stderr=dev_null, env_policy=lxc.LXC_ATTACH_CLEAR_ENV, extra_env_vars=["TERM=xterm"]):
        error_exit("Error installing packages!")
    
    logger("Configuring PostgreSQL...")
    # Append line to pg_hba.conf configuration file
    if container.attach_wait(lambda: append_hba_config(db_name, db_user, container_address)):
        error_exit("Error configuring pg_hba.conf!")
    
    # Modify a line in postgresql.conf configuration file
    if container.attach_wait(lambda: update_main_config(container_name, host_name)):
        error_exit("Error configuring postgresql.conf!")
    
    logger("Restarting PostgreSQL daemon...")
    if container.attach_wait(lxc.attach_run_command, ["systemctl", "restart", "postgresql"], stdout=dev_null, env_policy=lxc.LXC_ATTACH_CLEAR_ENV):
        error_exit("Error restarting PostgresSQL daemon!")
    
    logger("Creating database user...")
    # Create user with password
    if container.attach_wait(lxc.attach_run_command, ["su", "-", "postgres", "-c", "psql -c \"CREATE USER {} WITH PASSWORD '{}';\"".format(db_user, db_password)], stdout=dev_null, env_policy=lxc.LXC_ATTACH_CLEAR_ENV):
        error_exit("Error creating database user!")
    
    logger("Creating database...")
    if container.attach_wait(lxc.attach_run_command, ["su", "-", "postgres", "-c", "psql -c \"CREATE DATABASE {} OWNER {};\"".format(db_name, db_user)], stdout=dev_null, env_policy=lxc.LXC_ATTACH_CLEAR_ENV):
        error_exit("Error creating database!")
    
    logger("Success!")
    
    output = {"container_name"    : container_name, 
              "container_address" : container_address,
              "database_name"     : db_name, 
              "database_user"     : db_user, 
              "database_password" : db_password }
    
    # Output details as JSON
    print(json.dumps(output, sort_keys=True, indent=4))
    
    # Don't cleanup container on success
    atexit.unregister(cleanup_container)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create and start a new LXC container running a PostgreSQL database")
    parser.add_argument("-v", "--verbose", action="store_true", help="display additional information to stdout")
    parser.add_argument("prefix", nargs='?', default="test", help="Specify a prefix for the container name. The resulting container name will be prefix_postgresql_jessie. The default prefix is test.")
    args = vars(parser.parse_args())
    main(prefix=args["prefix"], verbose=args["verbose"])
