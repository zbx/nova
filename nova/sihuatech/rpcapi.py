# Copyright 2013 Red Hat, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
Client side of the compute RPC API.
"""

from oslo.config import cfg
from oslo import messaging

from nova import exception
from nova.i18n import _
from nova import objects
from nova.objects import base as objects_base
from nova.openstack.common import jsonutils
from nova import rpc
from nova import utils

rpcapi_opts = [
    cfg.StrOpt('compute_topic',
               default='compute',
               help='The topic compute nodes listen on'),
]

CONF = cfg.CONF
CONF.register_opts(rpcapi_opts)

rpcapi_cap_opt = cfg.StrOpt('compute',
        help='Set a version cap for messages sent to compute services. If you '
             'plan to do a live upgrade from havana to icehouse, you should '
             'set this option to "icehouse-compat" before beginning the live '
             'upgrade procedure.')
CONF.register_opt(rpcapi_cap_opt, 'upgrade_levels')


def _compute_host(host, instance):
    '''Get the destination host for a message.

    :param host: explicit host to send the message to.
    :param instance: If an explicit host was not specified, use
                     instance['host']

    :returns: A host
    '''
    if host:
        return host
    if not instance:
        raise exception.NovaException(_('No compute host specified'))
    if not instance['host']:
        raise exception.NovaException(_('Unable to find host for '
                                        'Instance %s') % instance['uuid'])
    return instance['host']


class ComputeAPI(object):
    '''Client side of the compute rpc API.'''
    VERSION_ALIASES = {
        'icehouse': '3.23',
        'juno': '3.35',
    }
    def __init__(self):
            super(ComputeAPI, self).__init__()
            target = messaging.Target(topic=CONF.compute_topic, version='3.0')
            version_cap = self.VERSION_ALIASES.get(CONF.upgrade_levels.compute,
                                                   CONF.upgrade_levels.compute)
            serializer = objects_base.NovaObjectSerializer()
            self.client = self.get_client(target, version_cap, serializer)
    def get_client(self, target, version_cap, serializer):
        return rpc.get_client(target,
                              version_cap=version_cap,
                              serializer=serializer)

    def snapshot_create(self, ctxt, instance, snapshot_name,snapshot_desc):
        version = '3.0'
        cctxt = self.client.prepare(server=_compute_host(None, instance),
                version=version)
        cctxt.call(ctxt, 'snapshot_create',
                   instance=instance,
                   snapshot_name=snapshot_name,
                   snapshot_desc=snapshot_desc)

    def snapshot_delete(self, ctxt, instance, snapshot_name):
        version = '3.0'
        cctxt = self.client.prepare(server=_compute_host(None, instance),
                version=version)
        return cctxt.call(ctxt, 'snapshot_delete',
                   instance=instance,
                   snapshot_name=snapshot_name)

    def snapshot_list(self, ctxt, instance):
        version = '3.0'
        cctxt = self.client.prepare(server=_compute_host(None, instance),
                version=version)
        return cctxt.call(ctxt, 'snapshot_list',
                   instance=instance)

    def snapshot_revert(self, ctxt, instance, snapshot_name):
        version = '3.0'
        cctxt = self.client.prepare(server=_compute_host(None, instance),
                version=version)
        return cctxt.call(ctxt, 'snapshot_revert',
                   instance=instance,
                   snapshot_name=snapshot_name)

    def snapshot_current(self, ctxt, instance):
        version = '3.0'
        cctxt = self.client.prepare(server=_compute_host(None, instance),
                version=version)
        return cctxt.call(ctxt, 'snapshot_current',
                   instance=instance)

    def guest_set_user_password(self, ctxt, instance,user_name, user_password):
        version = '3.0'
        cctxt = self.client.prepare(server=_compute_host(None, instance),
                version=version)
        return cctxt.call(ctxt, 'guest_set_user_password',
                   instance=instance,
                   user_name=user_name,
                   user_password=user_password)

    def live_migrate_delete_snapshot_meta(self, ctxt, instance):
        version = '3.0'
        cctxt = self.client.prepare(server=_compute_host(None, instance),
                version=version)
        return cctxt.call(ctxt, 'live_migrate_delete_snapshot_meta',
                   instance=instance)

    def live_migrate_redefine_snapshot_meta(self, ctxt, instance):
        version = '3.0'
        cctxt = self.client.prepare(server=_compute_host(None, instance),
                version=version)
        return cctxt.call(ctxt, 'live_migrate_redefine_snapshot_meta',
                   instance=instance)

    def guest_live_update(self, ctxt, instance, vcpus,memory_size):
        version = '3.0'
        cctxt = self.client.prepare(server=_compute_host(None, instance),
                version=version)
        cctxt.call(ctxt, 'guest_live_update',
                   instance=instance,
                   vcpus=vcpus,
                   memory_size=memory_size)