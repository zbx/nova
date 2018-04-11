# Copyright 2014 Red Hat, Inc.
# All Rights Reserved.
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

from nova import objects
from nova.virt.ironic import ironic_states


def get_test_validation(**kw):
    return type('interfaces', (object,),
               {'power': kw.get('power', {'result': True}),
                'deploy': kw.get('deploy', {'result': True}),
                'console': kw.get('console', True),
                'rescue': kw.get('rescue', True),
                'storage': kw.get('storage', {'result': True})})()


def get_test_node(**kw):
    return type('node', (object,),
               {'uuid': kw.get('uuid', 'eeeeeeee-dddd-cccc-bbbb-aaaaaaaaaaaa'),
                'chassis_uuid': kw.get('chassis_uuid'),
                'power_state': kw.get('power_state',
                                      ironic_states.NOSTATE),
                'target_power_state': kw.get('target_power_state',
                                             ironic_states.NOSTATE),
                'provision_state': kw.get('provision_state',
                                          ironic_states.NOSTATE),
                'target_provision_state': kw.get('target_provision_state',
                                                 ironic_states.NOSTATE),
                'last_error': kw.get('last_error'),
                'instance_uuid': kw.get('instance_uuid'),
                'instance_info': kw.get('instance_info'),
                'driver': kw.get('driver', 'fake'),
                'driver_info': kw.get('driver_info', {}),
                'properties': kw.get('properties', {}),
                'reservation': kw.get('reservation'),
                'maintenance': kw.get('maintenance', False),
                'network_interface': kw.get('network_interface'),
                'resource_class': kw.get('resource_class'),
                'extra': kw.get('extra', {}),
                'updated_at': kw.get('created_at'),
                'created_at': kw.get('updated_at')})()


def get_test_port(**kw):
    return type('port', (object,),
               {'uuid': kw.get('uuid', 'gggggggg-uuuu-qqqq-ffff-llllllllllll'),
                'node_uuid': kw.get('node_uuid', get_test_node().uuid),
                'address': kw.get('address', 'FF:FF:FF:FF:FF:FF'),
                'extra': kw.get('extra', {}),
                'internal_info': kw.get('internal_info', {}),
                'portgroup_uuid': kw.get('portgroup_uuid'),
                'created_at': kw.get('created_at'),
                'updated_at': kw.get('updated_at')})()


def get_test_portgroup(**kw):
    return type('portgroup', (object,),
               {'uuid': kw.get('uuid', 'deaffeed-1234-5678-9012-fedcbafedcba'),
                'node_uuid': kw.get('node_uuid', get_test_node().uuid),
                'address': kw.get('address', 'EE:EE:EE:EE:EE:EE'),
                'extra': kw.get('extra', {}),
                'internal_info': kw.get('internal_info', {}),
                'properties': kw.get('properties', {}),
                'mode': kw.get('mode', 'active-backup'),
                'name': kw.get('name'),
                'standalone_ports_supported': kw.get(
                    'standalone_ports_supported', True),
                'created_at': kw.get('created_at'),
                'updated_at': kw.get('updated_at')})()


def get_test_vif(**kw):
    return {
        'profile': kw.get('profile', {}),
        'ovs_interfaceid': kw.get('ovs_interfaceid'),
        'preserve_on_delete': kw.get('preserve_on_delete', False),
        'network': kw.get('network', {}),
        'devname': kw.get('devname', 'tapaaaaaaaa-00'),
        'vnic_type': kw.get('vnic_type', 'baremetal'),
        'qbh_params': kw.get('qbh_params'),
        'meta': kw.get('meta', {}),
        'details': kw.get('details', {}),
        'address': kw.get('address', 'FF:FF:FF:FF:FF:FF'),
        'active': kw.get('active', True),
        'type': kw.get('type', 'ironic'),
        'id': kw.get('id', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'),
        'qbg_params': kw.get('qbg_params')}


def get_test_volume_connector(**kw):
    return type('volume_connector', (object,),
               {'uuid': kw.get('uuid', 'hhhhhhhh-qqqq-uuuu-mmmm-bbbbbbbbbbbb'),
                'node_uuid': kw.get('node_uuid', get_test_node().uuid),
                'type': kw.get('type', 'iqn'),
                'connector_id': kw.get('connector_id', 'iqn.test'),
                'extra': kw.get('extra', {}),
                'created_at': kw.get('created_at'),
                'updated_at': kw.get('updated_at')})()


def get_test_volume_target(**kw):
    return type('volume_target', (object,),
                {'uuid': kw.get('uuid', 'aaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'),
                 'node_uuid': kw.get('node_uuid', get_test_node().uuid),
                 'volume_type': kw.get('volume_type', 'iscsi'),
                 'properties': kw.get('properties', {}),
                 'boot_index': kw.get('boot_index', 0),
                 'volume_id': kw.get('volume_id',
                                     'fffffff-gggg-hhhh-iiii-jjjjjjjjjjjj'),
                 'extra': kw.get('extra', {}),
                 'created_at': kw.get('created_at'),
                 'updated_at': kw.get('updated_at')})()


def get_test_flavor(**kw):
    default_extra_specs = {'baremetal:deploy_kernel_id':
                                       'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                           'baremetal:deploy_ramdisk_id':
                                       'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'}
    flavor = {'name': kw.get('name', 'fake.flavor'),
              'extra_specs': kw.get('extra_specs', default_extra_specs),
              'swap': kw.get('swap', 0),
              'root_gb': 1,
              'memory_mb': 1,
              'vcpus': 1,
              'ephemeral_gb': kw.get('ephemeral_gb', 0)}
    return objects.Flavor(**flavor)


def get_test_image_meta(**kw):
    return objects.ImageMeta.from_dict(
        {'id': kw.get('id', 'cccccccc-cccc-cccc-cccc-cccccccccccc')})


class FakeVolumeTargetClient(object):

    def create(self, node_uuid, driver_volume_type, target_properties,
               boot_index):
        pass

    def delete(self, volume_target_id):
        pass


class FakePortClient(object):

    def get(self, port_uuid):
        pass

    def update(self, port_uuid, patch):
        pass


class FakePortgroupClient(object):

    def list(self, node=None, detail=False):
        pass


class FakeNodeClient(object):

    def list(self, detail=False):
        return []

    def get(self, node_uuid, fields=None):
        pass

    def get_by_instance_uuid(self, instance_uuid, fields=None):
        pass

    def list_ports(self, node_uuid, detail=False):
        pass

    def set_power_state(self, node_uuid, target, soft=False, timeout=None):
        pass

    def set_provision_state(self, node_uuid, target):
        pass

    def update(self, node_uuid, patch):
        pass

    def validate(self, node_uuid):
        pass

    def vif_attach(self, node_uuid, port_id):
        pass

    def vif_detach(self, node_uuid, port_id):
        pass

    def inject_nmi(self, node_uuid):
        pass

    def list_volume_targets(self, node_uuid, detail=False):
        pass


class FakeClient(object):

    node = FakeNodeClient()
    port = FakePortClient()
    portgroup = FakePortgroupClient()
    volume_target = FakeVolumeTargetClient()
