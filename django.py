#!/usr/bin/env python3

"""
Create and start a new LXC container running a barebones Django site

usage: django.py [-h] [-v] [prefix]
"""
import lxc, os, sys, string, random, json, atexit, argparse, urllib.request, subprocess

nginx_conf = """
# the upstream component nginx needs to connect to
upstream django {{
    server unix://{0}.sock; # for a file socket
}}

# configuration of the server
server {{
    # the port your site will be served on
    listen      80;
    # the domain name it will serve for
    server_name {1}; # substitute your machine's IP address or FQDN
    charset     utf-8;

    # max upload size
    client_max_body_size 75M;   # adjust to taste

    location = /favicon.ico {{ access_log off; log_not_found off; }}

    # Django media
    location /media  {{
        alias {2}media;  # your Django project's media files - amend as required
    }}

    location /static {{
        alias {2}static; # your Django project's static files - amend as required
    }}

    # Finally, send all non-media requests to the Django server.
    location / {{
        uwsgi_pass  django;
        include     {2}uwsgi_params; # the uwsgi_params file you installed
    }}
}}

"""

uwsgi_conf = """
[uwsgi]
project         = {0}
base            = {1}

# Django-related settings
# the base directory (full path)
chdir           = %(base)/%(project)
# Django's wsgi file
module          = %(project).wsgi
# the virtualenv (full path)
#home            = %(base)/%(project)

# process-related settings
# master
master          = true
# maximum number of worker processes
processes       = 5
# the socket (use the full path to be safe
socket          = %(base)/%(project)/%(project).sock
# ... with appropriate permissions - may be needed
chmod-socket    = 666
# clear environment on exit
vacuum          = true
daemonize       = /var/log/uwsgi-emperor.log
"""

uwsgi_service = """
[Unit]
Description=uWSGI Emperor
After=syslog.target

[Service]
ExecStart=/usr/local/bin/uwsgi --emperor /etc/uwsgi/vassals
Restart=always
KillSignal=SIGQUIT
Type=notify
StandardError=syslog
NotifyAccess=all

[Install]
WantedBy=multi-user.target
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

    with subprocess.Popen(first_cmd, stdout=subprocess.PIPE) as pipe_cmd:
        container_run_command(container, description, second_cmd, stdin_fd=pipe_cmd.stdout, verbose=verbose, debug=debug)

def main(prefix="test", verbose=False):
    user_name = prefix + "_user"
    user_info = prefix[0].upper() + prefix[1:].lower() + " User"
    user_password = generate_password()
    user_home = "/home/" + user_name
    user_dir = user_home + '/'

    project_name = prefix + "_project"
    project_path = user_dir + project_name
    project_dir = project_path + '/'

    debian_packages = ["python3", "python3-pip", "python3-psycopg2", "nginx", "adduser", "openssh-server"]
    python_packages = ["uWSGI==2.0.13.1", "Django==1.10", "openpyxl==2.4.1"]

    # Log output to stdout if specified as command line flag (WARNING the list is a hack!)
    logger = lambda text: [print(text + "..."), text][1] if verbose else text

    # Print error message to stderr and abort with error state (WARNING the list is a hack!)
    error_exit = lambda text: [print("Error: {}!".format(text), file=sys.stderr), sys.exit(1)]

    # Get latest debian stable release name
    debian_release = get_stable_codename()
    if not debian_release:
        debian_release = "jessie"

    container_name = "{}_django_{}".format(prefix, debian_release)

    container = lxc.Container(container_name)

    if container.defined:
        error_exit("Container {} already exists!".format(container_name))

    description = logger("Creating filesystem")
    if not container.create("download", lxc.LXC_CREATE_QUIET, {"dist": "debian", "release": debian_release, "arch": "amd64"}):
        error_exit(description)

    # Stop and destroy container on error (WARNING the list is a hack!)
    cleanup_container = lambda: [container.stop(), container.destroy()]
    atexit.register(cleanup_container)

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

    description = logger("Getting IP address")
    # Wait for connectivity
    container_address = container.get_ips(timeout=120)[0]
    if not container_address:
        error_exit(description)

    run_command = lambda desc, cmd, debug=False: container_run_command(container, desc, cmd, verbose=verbose, debug=debug)
    pipe_command = lambda desc, cmd0, cmd1, debug=False: container_pipe_command(container, desc, cmd0, cmd1, verbose=verbose, debug=debug)

    run_command("Updating apt", ["apt-get", "update"])

    run_command("Installing debian packages", ["apt-get", "install", "-y"] + debian_packages)

    run_command("Installing python packages", ["pip3", "install"] + python_packages)

    run_command("Adding user", ["adduser", "--disabled-password", "--gecos", user_info, user_name])

    pipe_command("Setting user password", ["echo", "{}:{}".format(user_name, user_password)], ["chpasswd"])

    run_command("Creating Django project", ["su", "-", user_name, "-c", "django-admin.py startproject {}".format(project_name)])

    run_command("Appending configuration to settings.py", ["su", "-", user_name, "-c", 'echo "STATIC_ROOT = os.path.join(BASE_DIR, \'static\') + os.sep" >> {}'.format(project_dir+project_name+"/settings.py")])

    run_command("Updating static files configuration", ["su", "-", user_name, "-c", "cd {} && python3 manage.py collectstatic --noinput".format(project_name)])

    run_command("Creating media directory", ["su", "-", user_name, "-c", "mkdir {}".format(project_dir+"media")])

    run_command("Creating nginx configuration file", ["su", "-", user_name, "-c", 'echo "{}" > {}'.format(nginx_conf.format(project_dir+project_name, container_address, project_dir), project_dir+project_name+"_nginx.conf")])

    run_command("Copying nginx uwsgi parameter file", ["su", "-", user_name, "-c", "cp /etc/nginx/uwsgi_params {}".format(project_dir)])

    run_command("Removing default site", ["rm", "-f", "/etc/nginx/sites-enabled/default"])

    run_command("Setting site status to active", ["ln", "-s", project_dir+project_name+"_nginx.conf", "/etc/nginx/sites-enabled/"])

    run_command("Restarting nginx", ["systemctl", "restart", "nginx"])

    run_command("Creating uwsgi configuration file", ["su", "-", user_name, "-c", 'echo "{}" > {}'.format(uwsgi_conf.format(project_name, user_home), project_dir+project_name+"_uwsgi.ini")])

    run_command("Creating uwsgi configuration directory", ["mkdir", "-p", "/etc/uwsgi/vassals"])

    run_command("Linking uwsgi configuration", ["ln", "-s", project_dir+project_name+"_uwsgi.ini", "/etc/uwsgi/vassals/"])

    run_command("Creating uwsgi service", ["bash", "-c", 'echo "{}" > {};'.format(uwsgi_service, "/lib/systemd/system/uwsgi.service")])

    run_command("Activating uwsgi service", ["systemctl", "enable", "uwsgi"])

    run_command("Starting uwsgi service", ["systemctl", "start", "uwsgi"])

    logger("Success!")

    output = {"container_name"    : container_name,
              "container_address" : container_address,
              "user_name"         : user_name,
              "user_password"     : user_password,
              "project_path"      : project_path}

    # Output details as JSON
    print(json.dumps(output, sort_keys=True, indent=4))

    # Don't cleanup container on success
    atexit.unregister(cleanup_container)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create and start a new LXC container running a Django app")
    parser.add_argument("-v", "--verbose", action="store_true", help="display additional information to stdout")
    parser.add_argument("prefix", nargs='?', default="test", help="Specify a prefix for the container name. The resulting container name will be prefix_django_jessie. The default prefix is test.")
    args = vars(parser.parse_args())
    main(prefix=args["prefix"], verbose=args["verbose"])
