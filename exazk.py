#!/usr/bin/env python

import yaml
import time
import sys
import logging

from kazoo.client import KazooClient, KazooState
from kazoo.exceptions import SessionExpiredError

logging.basicConfig()
logger = logging.getLogger()
logger.setLevel('DEBUG')

class BGPTable:
    def __init__(self):
        self.table = []

    def add_route(self, **route):
        if 'prefix' not in route or 'dst' not in route or 'metric' not in route:
            raise Exception('prefix, dst & metric are mandatory in route')
        logger.debug('adding BGP route: %s' % route['prefix'])
        self.table.append(route)

    def del_route(self, **route):
        if 'prefix' not in route or 'dst' not in route or 'metric' not in route:
            raise Exception('prefix, dst & metric are mandatory in route')
        logger.debug('removing BGP route: %s' % route['prefix'])
        self.table.remove(route)

    def get_routes(self):
        return self.table

class BGPSpeaker:
    def __init__(self, table):
        if not isinstance(table, BGPTable):
            raise Exception('BGPTable object expected')

        self.table = table

    def advertise_routes(self):
        for route in self.table.get_routes():
            print('SAYING TO EXABGP: prefix %s dst %s metric %s' %
                    (route['prefix'], route['dst'], route['metric']))
        print('')

class EZKConfFactory:
    def create_from_yaml_file(self, path):
        yml = yaml.safe_load(open(path))

        return EZKConf(**yml)

class EZKConf:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

class EZKRuntime:
    def __init__(self, conf, zk):
        if not isinstance(conf, EZKConf):
            raise Exception('EZKConf object expected')

        if not isinstance(zk, KazooClient):
            raise Exception('KazooClient expected')

        self.conf = conf
        self.zk = zk
        self.bgp_table = BGPTable()

        # flags
        self.refresh = True
        self.recreate = True

    def set_bgp_table(self, table):
        if not isinstance(table, BGPTable):
            raise Exception('BGPTable object expected')

        self.bgp_table = table

    def get_bgp_table(self):
        return self.bgp_table

    def get_conf(self):
        return self.conf

    def get_zk(self):
        return self.zk

    def create_node(self):
        self.recreate = False
        logger.info('re-creating my ephemeral node')

        try:
            self.get_zk().create('%s/%s/%s' % (
                self.get_conf().zk_path_service,
                self.get_conf().srv_name,
                self.get_conf().srv_auth_ip), ephemeral=True)
        except SessionExpiredError as e:
            self.recreate = True

    def refresh_children(self):
        self.refresh = False
        logger.info('refreshing children & routes')

        children = self.get_zk().get_children('%s/%s' % (
                self.get_conf().zk_path_service,
                self.get_conf().srv_name))
        bgp_table = BGPTable()

        for ip in self.get_conf().srv_non_auth_ips:
            if ip not in children:
                bgp_table.add_route(prefix=ip, dst='1.1.1.1', metric=200)

        bgp_table.add_route(prefix=runtime.get_conf().srv_auth_ip,
                dst='1.1.1.1', metric=100)
        self.set_bgp_table(bgp_table)

class EZKState(KazooState):
    INIT = "INIT"

logger.info('ExaZK starting...')
conf = EZKConfFactory().create_from_yaml_file(sys.argv[1])
zk = KazooClient(hosts=','.join(conf.zk_hosts))
runtime = EZKRuntime(conf=conf, zk=zk)

def zk_transition(state):
    logger.info('zk state changed to %s' % state)

    if state == KazooState.SUSPENDED:
        logger.error('zk disconnected, flushing routes...')
        runtime.set_bgp_table(BGPTable())

    if state == KazooState.LOST:
        logger.error('zk lost, have to re-create ephemeral node')
        runtime.recreate = True

    if state == KazooState.CONNECTED:
        runtime.refresh = True

runtime.get_zk().add_listener(zk_transition)
runtime.get_zk().start()
runtime.get_zk().ensure_path('%s/%s' % (
    runtime.get_conf().zk_path_service,
    runtime.get_conf().srv_name))

while runtime.get_zk().exists('%s/%s/%s' % (
    runtime.get_conf().zk_path_service,
    runtime.get_conf().srv_name,
    runtime.get_conf().srv_auth_ip)):
    logger.warn('stale node found, sleeping(1)...')
    time.sleep(1)

@zk.ChildrenWatch('%s/%s' % (
    runtime.get_conf().zk_path_service,
    runtime.get_conf().srv_name))
def zk_watch(children):
    logger.debug('zk children are %s' % children)
    runtime.refresh = True

while True:
    time.sleep(1)

    if runtime.recreate:
        runtime.create_node()

    if runtime.refresh:
        runtime.refresh_children()

    BGPSpeaker(runtime.get_bgp_table()).advertise_routes()


