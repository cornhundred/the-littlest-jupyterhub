"""
Parse YAML config file & update JupyterHub config.

Config should never append or mutate, only set. Functions here could
be called many times per lifetime of a jupyterhub.

Traitlets that modify the startup of JupyterHub should not be here.
FIXME: A strong feeling that JSON Schema should be involved somehow.
"""

import os
import sys

from .config import CONFIG_FILE, STATE_DIR
from .yaml import yaml

# Default configuration for tljh
# User provided config is merged into this
default = {
    'auth': {
        'type': 'firstuseauthenticator.FirstUseAuthenticator',
        'FirstUseAuthenticator': {
            'create_users': False
        }
    },
    'users': {
        'allowed': [],
        'banned': [],
        'admin': [],
    },
    'limits': {
        'memory': None,
        'cpu': None,
    },
    'http': {
        'port': 80,
    },
    'https': {
        'enabled': False,
        'port': 443,
        'tls': {
            'cert': '',
            'key': '',
        },
        'letsencrypt': {
            'email': '',
            'domains': [],
        },
    },
    'traefik_api': {
        'ip': "127.0.0.1",
        'port': 8099,
        'username': 'api_admin',
        'password': '',
    },
    'user_environment': {
        'default_app': 'classic',
    },
    'services': {
        'cull': {
            'enabled': True,
            'timeout': 600,
            'every': 60,
            'concurrency': 5,
            'users': False,
            'max_age': 0
        }
    }
}

def load_config(config_file=CONFIG_FILE):
    """Load the current config as a dictionary

    merges overrides from config.yaml with default config
    """
    if os.path.exists(config_file):
        with open(config_file) as f:
            config_overrides = yaml.load(f)
    else:
        config_overrides = {}

    secrets = load_secrets()
    config = _merge_dictionaries(dict(default), secrets)
    config = _merge_dictionaries(config, config_overrides)
    return config


def apply_config(config_overrides, c):
    """
    Merge config_overrides with config defaults & apply to JupyterHub config c
    """
    tljh_config = _merge_dictionaries(dict(default), config_overrides)

    update_auth(c, tljh_config)
    update_userlists(c, tljh_config)
    update_limits(c, tljh_config)
    update_user_environment(c, tljh_config)
    update_user_account_config(c, tljh_config)
    update_traefik_api(c, tljh_config)
    update_services(c, tljh_config)


def set_if_not_none(parent, key, value):
    """
    Set attribute 'key' on parent if value is not None
    """
    if value is not None:
        setattr(parent, key, value)


def load_traefik_api_credentials():
    """Load traefik api secret from a file"""
    proxy_secret_path = os.path.join(STATE_DIR, 'traefik-api.secret')
    if not os.path.exists(proxy_secret_path):
        return {}
    with open(proxy_secret_path,'r') as f:
        password = f.read()
    return {
        'traefik_api': {
            'password': password,
        }
    }


def load_secrets():
    """Load any secret values stored on disk

    Returns dict to be merged into config during load
    """
    config = {}
    config = _merge_dictionaries(config, load_traefik_api_credentials())
    return config


def update_auth(c, config):
    """
    Set auth related configuration from YAML config file

    Use auth.type to determine authenticator to use. All parameters
    in the config under auth.{auth.type} will be passed straight to the
    authenticators themselves.
    """
    auth = config.get('auth')

    # FIXME: Make sure this is something importable.
    # FIXME: SECURITY: Class must inherit from Authenticator, to prevent us being
    # used to set arbitrary properties on arbitrary types of objects!
    authenticator_class = auth['type']
    # When specifying fully qualified name, use classname as key for config
    authenticator_configname = authenticator_class.split('.')[-1]
    c.JupyterHub.authenticator_class = authenticator_class
    # Use just class name when setting config. If authenticator is dummyauthenticator.DummyAuthenticator,
    # its config will be set under c.DummyAuthenticator
    authenticator_parent = getattr(c, authenticator_class.split('.')[-1])

    for k, v in auth.get(authenticator_configname, {}).items():
        set_if_not_none(authenticator_parent, k, v)


def update_userlists(c, config):
    """
    Set user whitelists & admin lists
    """
    users = config['users']

    c.Authenticator.whitelist = set(users['allowed'])
    c.Authenticator.blacklist = set(users['banned'])
    c.Authenticator.admin_users = set(users['admin'])


def update_limits(c, config):
    """
    Set user server limits
    """
    limits = config['limits']

    c.Spawner.mem_limit = limits['memory']
    c.Spawner.cpu_limit = limits['cpu']


def update_user_environment(c, config):
    """
    Set user environment configuration
    """
    user_env = config['user_environment']

    # Set default application users are launched into
    if user_env['default_app'] == 'jupyterlab':
        c.Spawner.default_url = '/lab'
    elif user_env['default_app'] == 'nteract':
        c.Spawner.default_url = '/nteract'


def update_user_account_config(c, config):
    c.SystemdSpawner.username_template = 'jupyter-{USERNAME}'


def update_traefik_api(c, config):
    """
    Set traefik api endpoint credentials
    """
    c.TraefikTomlProxy.traefik_api_username = config['traefik_api']['username']
    c.TraefikTomlProxy.traefik_api_password = config['traefik_api']['password']


def set_cull_idle_service(config):
    """
    Set Idle Culler service
    """
    cull_cmd = [
       sys.executable, '-m', 'tljh.cull_idle_servers'
    ]
    cull_config = config['services']['cull']
    print()

    cull_cmd += ['--timeout=%d' % cull_config['timeout']]
    cull_cmd += ['--cull-every=%d' % cull_config['every']]
    cull_cmd += ['--concurrency=%d' % cull_config['concurrency']]
    cull_cmd += ['--max-age=%d' % cull_config['max_age']]
    if cull_config['users']:
        cull_cmd += ['--cull-users']

    cull_service = {
        'name': 'cull-idle',
        'admin': True,
        'command': cull_cmd,
    }

    return cull_service


def update_services(c, config):
    c.JupyterHub.services = []
    if config['services']['cull']['enabled']:
        c.JupyterHub.services.append(set_cull_idle_service(config))


def _merge_dictionaries(a, b, path=None, update=True):
    """
    Merge two dictionaries recursively.

    From https://stackoverflow.com/a/7205107
    """
    if path is None:
        path = []
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                _merge_dictionaries(a[key], b[key], path + [str(key)])
            elif a[key] == b[key]:
                pass  # same leaf value
            elif update:
                a[key] = b[key]
            else:
                raise Exception('Conflict at %s' % '.'.join(path + [str(key)]))
        else:
            a[key] = b[key]
    return a
