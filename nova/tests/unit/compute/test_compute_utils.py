# Copyright 2011 OpenStack Foundation
# All Rights Reserved.
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

"""Tests For miscellaneous util methods used with compute."""

import copy
import string
import uuid

import mock
import six

from nova.compute import flavors
from nova.compute import manager
from nova.compute import power_state
from nova.compute import task_states
from nova.compute import utils as compute_utils
import nova.conf
from nova import context
from nova import exception
from nova.image import glance
from nova.network import model
from nova import objects
from nova.objects import base
from nova.objects import block_device as block_device_obj
from nova.objects import fields
from nova import rpc
from nova import test
from nova.tests.unit import fake_block_device
from nova.tests.unit import fake_crypto
from nova.tests.unit import fake_instance
from nova.tests.unit import fake_network
from nova.tests.unit import fake_notifier
from nova.tests.unit import fake_server_actions
import nova.tests.unit.image.fake
from nova.tests.unit.objects import test_flavor
from nova.tests import uuidsentinel as uuids


CONF = nova.conf.CONF

FAKE_IMAGE_REF = uuids.image_ref


def create_instance(context, user_id='fake', project_id='fake', params=None):
    """Create a test instance."""
    flavor = flavors.get_flavor_by_name('m1.tiny')
    net_info = model.NetworkInfo([])
    info_cache = objects.InstanceInfoCache(network_info=net_info)
    inst = objects.Instance(context=context,
                            image_ref=uuids.fake_image_ref,
                            reservation_id='r-fakeres',
                            user_id=user_id,
                            project_id=project_id,
                            instance_type_id=flavor.id,
                            flavor=flavor,
                            old_flavor=None,
                            new_flavor=None,
                            system_metadata={},
                            ami_launch_index=0,
                            root_gb=0,
                            ephemeral_gb=0,
                            info_cache=info_cache)
    if params:
        inst.update(params)
    inst.create()
    return inst


class ComputeValidateDeviceTestCase(test.NoDBTestCase):
    def setUp(self):
        super(ComputeValidateDeviceTestCase, self).setUp()
        self.context = context.RequestContext('fake', 'fake')
        # check if test name includes "xen"
        if 'xen' in self.id():
            self.flags(compute_driver='xenapi.XenAPIDriver')
            self.instance = objects.Instance(uuid=uuid.uuid4().hex,
                root_device_name=None,
                default_ephemeral_device=None)
        else:
            self.instance = objects.Instance(uuid=uuid.uuid4().hex,
                root_device_name='/dev/vda',
                default_ephemeral_device='/dev/vdb')

        flavor = objects.Flavor(**test_flavor.fake_flavor)
        self.instance.system_metadata = {}
        self.instance.flavor = flavor
        self.instance.default_swap_device = None

        self.data = []

    def _validate_device(self, device=None):
        bdms = base.obj_make_list(self.context,
            objects.BlockDeviceMappingList(), objects.BlockDeviceMapping,
            self.data)
        return compute_utils.get_device_name_for_instance(
            self.instance, bdms, device)

    @staticmethod
    def _fake_bdm(device):
        return fake_block_device.FakeDbBlockDeviceDict({
            'source_type': 'volume',
            'destination_type': 'volume',
            'device_name': device,
            'no_device': None,
            'volume_id': 'fake',
            'snapshot_id': None,
            'guest_format': None
        })

    def test_wrap(self):
        self.data = []
        for letter in string.ascii_lowercase[2:]:
            self.data.append(self._fake_bdm('/dev/vd' + letter))
        device = self._validate_device()
        self.assertEqual(device, '/dev/vdaa')

    def test_wrap_plus_one(self):
        self.data = []
        for letter in string.ascii_lowercase[2:]:
            self.data.append(self._fake_bdm('/dev/vd' + letter))
        self.data.append(self._fake_bdm('/dev/vdaa'))
        device = self._validate_device()
        self.assertEqual(device, '/dev/vdab')

    def test_later(self):
        self.data = [
            self._fake_bdm('/dev/vdc'),
            self._fake_bdm('/dev/vdd'),
            self._fake_bdm('/dev/vde'),
        ]
        device = self._validate_device()
        self.assertEqual(device, '/dev/vdf')

    def test_gap(self):
        self.data = [
            self._fake_bdm('/dev/vdc'),
            self._fake_bdm('/dev/vde'),
        ]
        device = self._validate_device()
        self.assertEqual(device, '/dev/vdd')

    def test_no_bdms(self):
        self.data = []
        device = self._validate_device()
        self.assertEqual(device, '/dev/vdc')

    def test_lxc_names_work(self):
        self.instance['root_device_name'] = '/dev/a'
        self.instance['ephemeral_device_name'] = '/dev/b'
        self.data = []
        device = self._validate_device()
        self.assertEqual(device, '/dev/c')

    def test_name_conversion(self):
        self.data = []
        device = self._validate_device('/dev/c')
        self.assertEqual(device, '/dev/vdc')
        device = self._validate_device('/dev/sdc')
        self.assertEqual(device, '/dev/vdc')
        device = self._validate_device('/dev/xvdc')
        self.assertEqual(device, '/dev/vdc')

    def test_invalid_device_prefix(self):
        self.assertRaises(exception.InvalidDevicePath,
                          self._validate_device, '/baddata/vdc')

    def test_device_in_use(self):
        exc = self.assertRaises(exception.DevicePathInUse,
                          self._validate_device, '/dev/vda')
        self.assertIn('/dev/vda', six.text_type(exc))

    def test_swap(self):
        self.instance['default_swap_device'] = "/dev/vdc"
        device = self._validate_device()
        self.assertEqual(device, '/dev/vdd')

    def test_swap_no_ephemeral(self):
        self.instance.default_ephemeral_device = None
        self.instance.default_swap_device = "/dev/vdb"
        device = self._validate_device()
        self.assertEqual(device, '/dev/vdc')

    def test_ephemeral_xenapi(self):
        self.instance.flavor.ephemeral_gb = 10
        self.instance.flavor.swap = 0
        device = self._validate_device()
        self.assertEqual(device, '/dev/xvdc')

    def test_swap_xenapi(self):
        self.instance.flavor.ephemeral_gb = 0
        self.instance.flavor.swap = 10
        device = self._validate_device()
        self.assertEqual(device, '/dev/xvdb')

    def test_swap_and_ephemeral_xenapi(self):
        self.instance.flavor.ephemeral_gb = 10
        self.instance.flavor.swap = 10
        device = self._validate_device()
        self.assertEqual(device, '/dev/xvdd')

    def test_swap_and_one_attachment_xenapi(self):
        self.instance.flavor.ephemeral_gb = 0
        self.instance.flavor.swap = 10
        device = self._validate_device()
        self.assertEqual(device, '/dev/xvdb')
        self.data.append(self._fake_bdm(device))
        device = self._validate_device()
        self.assertEqual(device, '/dev/xvdd')

    def test_no_dev_root_device_name_get_next_name(self):
        self.instance['root_device_name'] = 'vda'
        device = self._validate_device()
        self.assertEqual('/dev/vdc', device)


class DefaultDeviceNamesForInstanceTestCase(test.NoDBTestCase):

    def setUp(self):
        super(DefaultDeviceNamesForInstanceTestCase, self).setUp()
        self.context = context.RequestContext('fake', 'fake')
        self.ephemerals = block_device_obj.block_device_make_list(
                self.context,
                [fake_block_device.FakeDbBlockDeviceDict(
                 {'id': 1,
                  'instance_uuid': uuids.block_device_instance,
                  'device_name': '/dev/vdb',
                  'source_type': 'blank',
                  'destination_type': 'local',
                  'delete_on_termination': True,
                  'guest_format': None,
                  'boot_index': -1})])

        self.swap = block_device_obj.block_device_make_list(
                self.context,
                [fake_block_device.FakeDbBlockDeviceDict(
                 {'id': 2,
                  'instance_uuid': uuids.block_device_instance,
                  'device_name': '/dev/vdc',
                  'source_type': 'blank',
                  'destination_type': 'local',
                  'delete_on_termination': True,
                  'guest_format': 'swap',
                  'boot_index': -1})])

        self.block_device_mapping = block_device_obj.block_device_make_list(
                self.context,
                [fake_block_device.FakeDbBlockDeviceDict(
                 {'id': 3,
                  'instance_uuid': uuids.block_device_instance,
                  'device_name': '/dev/vda',
                  'source_type': 'volume',
                  'destination_type': 'volume',
                  'volume_id': 'fake-volume-id-1',
                  'boot_index': 0}),
                 fake_block_device.FakeDbBlockDeviceDict(
                 {'id': 4,
                  'instance_uuid': uuids.block_device_instance,
                  'device_name': '/dev/vdd',
                  'source_type': 'snapshot',
                  'destination_type': 'volume',
                  'snapshot_id': 'fake-snapshot-id-1',
                  'boot_index': -1}),
                 fake_block_device.FakeDbBlockDeviceDict(
                 {'id': 5,
                  'instance_uuid': uuids.block_device_instance,
                  'device_name': '/dev/vde',
                  'source_type': 'blank',
                  'destination_type': 'volume',
                  'boot_index': -1})])
        self.instance = {'uuid': uuids.instance,
                         'ephemeral_gb': 2}
        self.is_libvirt = False
        self.root_device_name = '/dev/vda'
        self.update_called = False

        self.patchers = []
        self.patchers.append(
                mock.patch.object(objects.BlockDeviceMapping, 'save'))
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        super(DefaultDeviceNamesForInstanceTestCase, self).tearDown()
        for patcher in self.patchers:
            patcher.stop()

    def _test_default_device_names(self, *block_device_lists):
        compute_utils.default_device_names_for_instance(self.instance,
                                                        self.root_device_name,
                                                        *block_device_lists)

    def test_only_block_device_mapping(self):
        # Test no-op
        original_bdm = copy.deepcopy(self.block_device_mapping)
        self._test_default_device_names([], [], self.block_device_mapping)
        for original, new in zip(original_bdm, self.block_device_mapping):
            self.assertEqual(original.device_name, new.device_name)

        # Assert it defaults the missing one as expected
        self.block_device_mapping[1]['device_name'] = None
        self.block_device_mapping[2]['device_name'] = None
        self._test_default_device_names([], [], self.block_device_mapping)
        self.assertEqual('/dev/vdb',
                         self.block_device_mapping[1]['device_name'])
        self.assertEqual('/dev/vdc',
                         self.block_device_mapping[2]['device_name'])

    def test_with_ephemerals(self):
        # Test ephemeral gets assigned
        self.ephemerals[0]['device_name'] = None
        self._test_default_device_names(self.ephemerals, [],
                                        self.block_device_mapping)
        self.assertEqual(self.ephemerals[0]['device_name'], '/dev/vdb')

        self.block_device_mapping[1]['device_name'] = None
        self.block_device_mapping[2]['device_name'] = None
        self._test_default_device_names(self.ephemerals, [],
                                        self.block_device_mapping)
        self.assertEqual('/dev/vdc',
                         self.block_device_mapping[1]['device_name'])
        self.assertEqual('/dev/vdd',
                         self.block_device_mapping[2]['device_name'])

    def test_with_swap(self):
        # Test swap only
        self.swap[0]['device_name'] = None
        self._test_default_device_names([], self.swap, [])
        self.assertEqual(self.swap[0]['device_name'], '/dev/vdb')

        # Test swap and block_device_mapping
        self.swap[0]['device_name'] = None
        self.block_device_mapping[1]['device_name'] = None
        self.block_device_mapping[2]['device_name'] = None
        self._test_default_device_names([], self.swap,
                                        self.block_device_mapping)
        self.assertEqual(self.swap[0]['device_name'], '/dev/vdb')
        self.assertEqual('/dev/vdc',
                         self.block_device_mapping[1]['device_name'])
        self.assertEqual('/dev/vdd',
                         self.block_device_mapping[2]['device_name'])

    def test_all_together(self):
        # Test swap missing
        self.swap[0]['device_name'] = None
        self._test_default_device_names(self.ephemerals,
                                        self.swap, self.block_device_mapping)
        self.assertEqual(self.swap[0]['device_name'], '/dev/vdc')

        # Test swap and eph missing
        self.swap[0]['device_name'] = None
        self.ephemerals[0]['device_name'] = None
        self._test_default_device_names(self.ephemerals,
                                        self.swap, self.block_device_mapping)
        self.assertEqual(self.ephemerals[0]['device_name'], '/dev/vdb')
        self.assertEqual(self.swap[0]['device_name'], '/dev/vdc')

        # Test all missing
        self.swap[0]['device_name'] = None
        self.ephemerals[0]['device_name'] = None
        self.block_device_mapping[1]['device_name'] = None
        self.block_device_mapping[2]['device_name'] = None
        self._test_default_device_names(self.ephemerals,
                                        self.swap, self.block_device_mapping)
        self.assertEqual(self.ephemerals[0]['device_name'], '/dev/vdb')
        self.assertEqual(self.swap[0]['device_name'], '/dev/vdc')
        self.assertEqual('/dev/vdd',
                         self.block_device_mapping[1]['device_name'])
        self.assertEqual('/dev/vde',
                         self.block_device_mapping[2]['device_name'])


class UsageInfoTestCase(test.TestCase):

    def setUp(self):
        self.public_key = fake_crypto.get_ssh_public_key()
        self.fingerprint = '1e:2c:9b:56:79:4b:45:77:f9:ca:7a:98:2c:b0:d5:3c'

        def fake_get_nw_info(cls, ctxt, instance):
            self.assertTrue(ctxt.is_admin)
            return fake_network.fake_get_instance_nw_info(self, 1, 1)

        super(UsageInfoTestCase, self).setUp()
        self.stub_out('nova.network.api.get_instance_nw_info',
                      fake_get_nw_info)

        fake_notifier.stub_notifier(self)
        self.addCleanup(fake_notifier.reset)

        self.flags(compute_driver='fake.FakeDriver',
                   network_manager='nova.network.manager.FlatManager')
        self.compute = manager.ComputeManager()
        self.user_id = 'fake'
        self.project_id = 'fake'
        self.context = context.RequestContext(self.user_id, self.project_id)

        def fake_show(meh, context, id, **kwargs):
            return {'id': 1, 'properties': {'kernel_id': 1, 'ramdisk_id': 1}}

        self.flags(group='glance', api_servers=['http://localhost:9292'])
        self.stub_out('nova.tests.unit.image.fake._FakeImageService.show',
                      fake_show)
        fake_network.set_stub_network_methods(self)
        fake_server_actions.stub_out_action_events(self)

    def test_notify_usage_exists(self):
        # Ensure 'exists' notification generates appropriate usage data.
        instance = create_instance(self.context)
        # Set some system metadata
        sys_metadata = {'image_md_key1': 'val1',
                        'image_md_key2': 'val2',
                        'other_data': 'meow'}
        instance.system_metadata.update(sys_metadata)
        instance.save()
        compute_utils.notify_usage_exists(
            rpc.get_notifier('compute'), self.context, instance)
        self.assertEqual(len(fake_notifier.NOTIFICATIONS), 1)
        msg = fake_notifier.NOTIFICATIONS[0]
        self.assertEqual(msg.priority, 'INFO')
        self.assertEqual(msg.event_type, 'compute.instance.exists')
        payload = msg.payload
        self.assertEqual(payload['tenant_id'], self.project_id)
        self.assertEqual(payload['user_id'], self.user_id)
        self.assertEqual(payload['instance_id'], instance['uuid'])
        self.assertEqual(payload['instance_type'], 'm1.tiny')
        type_id = flavors.get_flavor_by_name('m1.tiny')['id']
        self.assertEqual(str(payload['instance_type_id']), str(type_id))
        flavor_id = flavors.get_flavor_by_name('m1.tiny')['flavorid']
        self.assertEqual(str(payload['instance_flavor_id']), str(flavor_id))
        for attr in ('display_name', 'created_at', 'launched_at',
                     'state', 'state_description',
                     'bandwidth', 'audit_period_beginning',
                     'audit_period_ending', 'image_meta'):
            self.assertIn(attr, payload,
                          "Key %s not in payload" % attr)
        self.assertEqual(payload['image_meta'],
                {'md_key1': 'val1', 'md_key2': 'val2'})
        image_ref_url = "%s/images/%s" % (glance.generate_glance_url(),
                                          uuids.fake_image_ref)
        self.assertEqual(payload['image_ref_url'], image_ref_url)
        self.compute.terminate_instance(self.context, instance, [], [])

    def test_notify_usage_exists_deleted_instance(self):
        # Ensure 'exists' notification generates appropriate usage data.
        instance = create_instance(self.context)
        # Set some system metadata
        sys_metadata = {'image_md_key1': 'val1',
                        'image_md_key2': 'val2',
                        'other_data': 'meow'}
        instance.system_metadata.update(sys_metadata)
        instance.save()
        self.compute.terminate_instance(self.context, instance, [], [])
        compute_utils.notify_usage_exists(
            rpc.get_notifier('compute'), self.context, instance)
        msg = fake_notifier.NOTIFICATIONS[-1]
        self.assertEqual(msg.priority, 'INFO')
        self.assertEqual(msg.event_type, 'compute.instance.exists')
        payload = msg.payload
        self.assertEqual(payload['tenant_id'], self.project_id)
        self.assertEqual(payload['user_id'], self.user_id)
        self.assertEqual(payload['instance_id'], instance['uuid'])
        self.assertEqual(payload['instance_type'], 'm1.tiny')
        type_id = flavors.get_flavor_by_name('m1.tiny')['id']
        self.assertEqual(str(payload['instance_type_id']), str(type_id))
        flavor_id = flavors.get_flavor_by_name('m1.tiny')['flavorid']
        self.assertEqual(str(payload['instance_flavor_id']), str(flavor_id))
        for attr in ('display_name', 'created_at', 'launched_at',
                     'state', 'state_description',
                     'bandwidth', 'audit_period_beginning',
                     'audit_period_ending', 'image_meta'):
            self.assertIn(attr, payload, "Key %s not in payload" % attr)
        self.assertEqual(payload['image_meta'],
                {'md_key1': 'val1', 'md_key2': 'val2'})
        image_ref_url = "%s/images/%s" % (glance.generate_glance_url(),
                                          uuids.fake_image_ref)
        self.assertEqual(payload['image_ref_url'], image_ref_url)

    def test_notify_about_instance_action(self):
        instance = create_instance(self.context)

        compute_utils.notify_about_instance_action(
            self.context,
            instance,
            host='fake-compute',
            action='delete',
            phase='start')

        self.assertEqual(len(fake_notifier.VERSIONED_NOTIFICATIONS), 1)
        notification = fake_notifier.VERSIONED_NOTIFICATIONS[0]

        self.assertEqual(notification['priority'], 'INFO')
        self.assertEqual(notification['event_type'], 'instance.delete.start')
        self.assertEqual(notification['publisher_id'],
                         'nova-compute:fake-compute')

        payload = notification['payload']['nova_object.data']
        self.assertEqual(payload['tenant_id'], self.project_id)
        self.assertEqual(payload['user_id'], self.user_id)
        self.assertEqual(payload['uuid'], instance['uuid'])

        flavorid = flavors.get_flavor_by_name('m1.tiny')['flavorid']
        flavor = payload['flavor']['nova_object.data']
        self.assertEqual(str(flavor['flavorid']), flavorid)

        for attr in ('display_name', 'created_at', 'launched_at',
                     'state', 'task_state', 'display_description', 'locked',
                     'auto_disk_config', 'key_name'):
            self.assertIn(attr, payload, "Key %s not in payload" % attr)

        self.assertEqual(payload['image_uuid'], uuids.fake_image_ref)

    def test_notify_about_instance_create(self):
        keypair = objects.KeyPair(name='my-key', user_id='fake', type='ssh',
                                  public_key=self.public_key,
                                  fingerprint=self.fingerprint)
        keypairs = objects.KeyPairList(objects=[keypair])
        instance = create_instance(self.context, params={'keypairs': keypairs})

        compute_utils.notify_about_instance_create(
            self.context,
            instance,
            host='fake-compute',
            phase='start')

        self.assertEqual(1, len(fake_notifier.VERSIONED_NOTIFICATIONS))
        notification = fake_notifier.VERSIONED_NOTIFICATIONS[0]

        self.assertEqual('INFO', notification['priority'])
        self.assertEqual('instance.create.start', notification['event_type'])
        self.assertEqual('nova-compute:fake-compute',
                         notification['publisher_id'])

        payload = notification['payload']['nova_object.data']
        self.assertEqual('fake', payload['tenant_id'])
        self.assertEqual('fake', payload['user_id'])
        self.assertEqual(instance['uuid'], payload['uuid'])

        flavorid = flavors.get_flavor_by_name('m1.tiny')['flavorid']
        flavor = payload['flavor']['nova_object.data']
        self.assertEqual(flavorid, str(flavor['flavorid']))

        keypairs_payload = payload['keypairs']
        self.assertEqual(1, len(keypairs_payload))
        keypair_data = keypairs_payload[0]['nova_object.data']
        self.assertEqual(keypair_data,
                         {'name': 'my-key',
                          'user_id': 'fake',
                          'type': 'ssh',
                          'public_key': self.public_key,
                          'fingerprint': self.fingerprint})

        for attr in ('display_name', 'created_at', 'launched_at',
                     'state', 'task_state', 'display_description', 'locked',
                     'auto_disk_config'):
            self.assertIn(attr, payload, "Key %s not in payload" % attr)

        self.assertEqual(uuids.fake_image_ref, payload['image_uuid'])

    def test_notify_about_instance_create_without_keypair(self):
        instance = create_instance(self.context)

        compute_utils.notify_about_instance_create(
            self.context,
            instance,
            host='fake-compute',
            phase='start')

        self.assertEqual(1, len(fake_notifier.VERSIONED_NOTIFICATIONS))
        notification = fake_notifier.VERSIONED_NOTIFICATIONS[0]

        self.assertEqual('INFO', notification['priority'])
        self.assertEqual('instance.create.start', notification['event_type'])
        self.assertEqual('nova-compute:fake-compute',
                         notification['publisher_id'])

        payload = notification['payload']['nova_object.data']
        self.assertEqual('fake', payload['tenant_id'])
        self.assertEqual('fake', payload['user_id'])
        self.assertEqual(instance['uuid'], payload['uuid'])

        flavorid = flavors.get_flavor_by_name('m1.tiny')['flavorid']
        flavor = payload['flavor']['nova_object.data']
        self.assertEqual(flavorid, str(flavor['flavorid']))

        self.assertEqual(0, len(payload['keypairs']))
        for attr in ('display_name', 'created_at', 'launched_at',
                     'state', 'task_state', 'display_description', 'locked',
                     'auto_disk_config'):
            self.assertIn(attr, payload, "Key %s not in payload" % attr)

        self.assertEqual(uuids.fake_image_ref, payload['image_uuid'])

    def test_notify_about_instance_create_with_tags(self):
        instance = create_instance(self.context)

        # TODO(Kevin Zheng): clean this up to pass tags as params to
        # create_instance() once instance.create() handles tags.
        instance.tags = objects.TagList(
            objects=[objects.Tag(self.context, tag='tag1')])

        compute_utils.notify_about_instance_create(
            self.context,
            instance,
            host='fake-compute',
            phase='start')

        self.assertEqual(1, len(fake_notifier.VERSIONED_NOTIFICATIONS))
        notification = fake_notifier.VERSIONED_NOTIFICATIONS[0]

        self.assertEqual('INFO', notification['priority'])
        self.assertEqual('instance.create.start', notification['event_type'])
        self.assertEqual('nova-compute:fake-compute',
                         notification['publisher_id'])

        payload = notification['payload']['nova_object.data']
        self.assertEqual('fake', payload['tenant_id'])
        self.assertEqual('fake', payload['user_id'])
        self.assertEqual(instance.uuid, payload['uuid'])

        flavorid = flavors.get_flavor_by_name('m1.tiny')['flavorid']
        flavor = payload['flavor']['nova_object.data']
        self.assertEqual(flavorid, str(flavor['flavorid']))

        self.assertEqual(0, len(payload['keypairs']))
        for attr in ('display_name', 'created_at', 'launched_at',
                     'state', 'task_state', 'display_description', 'locked',
                     'auto_disk_config', 'tags'):
            self.assertIn(attr, payload, "Key %s not in payload" % attr)

        self.assertEqual(1, len(payload['tags']))
        self.assertEqual('tag1', payload['tags'][0])
        self.assertEqual(uuids.fake_image_ref, payload['image_uuid'])

    def test_notify_about_volume_swap(self):
        instance = create_instance(self.context)

        compute_utils.notify_about_volume_swap(
            self.context, instance, 'fake-compute',
            fields.NotificationAction.VOLUME_SWAP,
            fields.NotificationPhase.START,
            uuids.old_volume_id, uuids.new_volume_id)

        self.assertEqual(len(fake_notifier.VERSIONED_NOTIFICATIONS), 1)
        notification = fake_notifier.VERSIONED_NOTIFICATIONS[0]

        self.assertEqual('INFO', notification['priority'])
        self.assertEqual('instance.%s.%s' %
                         (fields.NotificationAction.VOLUME_SWAP,
                          fields.NotificationPhase.START),
                         notification['event_type'])
        self.assertEqual('nova-compute:fake-compute',
                         notification['publisher_id'])

        payload = notification['payload']['nova_object.data']
        self.assertEqual(self.project_id, payload['tenant_id'])
        self.assertEqual(self.user_id, payload['user_id'])
        self.assertEqual(instance['uuid'], payload['uuid'])

        flavorid = flavors.get_flavor_by_name('m1.tiny')['flavorid']
        flavor = payload['flavor']['nova_object.data']
        self.assertEqual(flavorid, str(flavor['flavorid']))

        for attr in ('display_name', 'created_at', 'launched_at',
                     'state', 'task_state'):
            self.assertIn(attr, payload)

        self.assertEqual(uuids.fake_image_ref, payload['image_uuid'])

        self.assertEqual(uuids.old_volume_id, payload['old_volume_id'])
        self.assertEqual(uuids.new_volume_id, payload['new_volume_id'])

    def test_notify_about_volume_swap_with_error(self):
        instance = create_instance(self.context)

        try:
            # To get exception trace, raise and catch an exception
            raise test.TestingException('Volume swap error.')
        except Exception as ex:
            compute_utils.notify_about_volume_swap(
                self.context, instance, 'fake-compute',
                fields.NotificationAction.VOLUME_SWAP,
                fields.NotificationPhase.ERROR,
                uuids.old_volume_id, uuids.new_volume_id, ex)

        self.assertEqual(len(fake_notifier.VERSIONED_NOTIFICATIONS), 1)
        notification = fake_notifier.VERSIONED_NOTIFICATIONS[0]

        self.assertEqual('ERROR', notification['priority'])
        self.assertEqual('instance.%s.%s' %
                         (fields.NotificationAction.VOLUME_SWAP,
                          fields.NotificationPhase.ERROR),
                         notification['event_type'])
        self.assertEqual('nova-compute:fake-compute',
                         notification['publisher_id'])

        payload = notification['payload']['nova_object.data']
        self.assertEqual(self.project_id, payload['tenant_id'])
        self.assertEqual(self.user_id, payload['user_id'])
        self.assertEqual(instance['uuid'], payload['uuid'])

        flavorid = flavors.get_flavor_by_name('m1.tiny')['flavorid']
        flavor = payload['flavor']['nova_object.data']
        self.assertEqual(flavorid, str(flavor['flavorid']))

        for attr in ('display_name', 'created_at', 'launched_at',
                     'state', 'task_state'):
            self.assertIn(attr, payload)

        self.assertEqual(uuids.fake_image_ref, payload['image_uuid'])

        self.assertEqual(uuids.old_volume_id, payload['old_volume_id'])
        self.assertEqual(uuids.new_volume_id, payload['new_volume_id'])

        # Check ExceptionPayload
        exception_payload = payload['fault']['nova_object.data']
        self.assertEqual('TestingException', exception_payload['exception'])
        self.assertEqual('Volume swap error.',
                         exception_payload['exception_message'])
        self.assertEqual('test_notify_about_volume_swap_with_error',
                         exception_payload['function_name'])
        self.assertEqual('nova.tests.unit.compute.test_compute_utils',
                         exception_payload['module_name'])

    def test_notify_usage_exists_instance_not_found(self):
        # Ensure 'exists' notification generates appropriate usage data.
        instance = create_instance(self.context)
        self.compute.terminate_instance(self.context, instance, [], [])
        compute_utils.notify_usage_exists(
            rpc.get_notifier('compute'), self.context, instance)
        msg = fake_notifier.NOTIFICATIONS[-1]
        self.assertEqual(msg.priority, 'INFO')
        self.assertEqual(msg.event_type, 'compute.instance.exists')
        payload = msg.payload
        self.assertEqual(payload['tenant_id'], self.project_id)
        self.assertEqual(payload['user_id'], self.user_id)
        self.assertEqual(payload['instance_id'], instance['uuid'])
        self.assertEqual(payload['instance_type'], 'm1.tiny')
        type_id = flavors.get_flavor_by_name('m1.tiny')['id']
        self.assertEqual(str(payload['instance_type_id']), str(type_id))
        flavor_id = flavors.get_flavor_by_name('m1.tiny')['flavorid']
        self.assertEqual(str(payload['instance_flavor_id']), str(flavor_id))
        for attr in ('display_name', 'created_at', 'launched_at',
                     'state', 'state_description',
                     'bandwidth', 'audit_period_beginning',
                     'audit_period_ending', 'image_meta'):
            self.assertIn(attr, payload, "Key %s not in payload" % attr)
        self.assertEqual(payload['image_meta'], {})
        image_ref_url = "%s/images/%s" % (glance.generate_glance_url(),
                                          uuids.fake_image_ref)
        self.assertEqual(payload['image_ref_url'], image_ref_url)

    def test_notify_about_instance_usage(self):
        instance = create_instance(self.context)
        # Set some system metadata
        sys_metadata = {'image_md_key1': 'val1',
                        'image_md_key2': 'val2',
                        'other_data': 'meow'}
        instance.system_metadata.update(sys_metadata)
        instance.save()
        extra_usage_info = {'image_name': 'fake_name'}
        compute_utils.notify_about_instance_usage(
            rpc.get_notifier('compute'),
            self.context, instance, 'create.start',
            extra_usage_info=extra_usage_info)
        self.assertEqual(len(fake_notifier.NOTIFICATIONS), 1)
        msg = fake_notifier.NOTIFICATIONS[0]
        self.assertEqual(msg.priority, 'INFO')
        self.assertEqual(msg.event_type, 'compute.instance.create.start')
        payload = msg.payload
        self.assertEqual(payload['tenant_id'], self.project_id)
        self.assertEqual(payload['user_id'], self.user_id)
        self.assertEqual(payload['instance_id'], instance['uuid'])
        self.assertEqual(payload['instance_type'], 'm1.tiny')
        type_id = flavors.get_flavor_by_name('m1.tiny')['id']
        self.assertEqual(str(payload['instance_type_id']), str(type_id))
        flavor_id = flavors.get_flavor_by_name('m1.tiny')['flavorid']
        self.assertEqual(str(payload['instance_flavor_id']), str(flavor_id))
        for attr in ('display_name', 'created_at', 'launched_at',
                     'state', 'state_description', 'image_meta'):
            self.assertIn(attr, payload, "Key %s not in payload" % attr)
        self.assertEqual(payload['image_meta'],
                {'md_key1': 'val1', 'md_key2': 'val2'})
        self.assertEqual(payload['image_name'], 'fake_name')
        image_ref_url = "%s/images/%s" % (glance.generate_glance_url(),
                                          uuids.fake_image_ref)
        self.assertEqual(payload['image_ref_url'], image_ref_url)
        self.compute.terminate_instance(self.context, instance, [], [])

    def test_notify_about_aggregate_update_with_id(self):
        # Set aggregate payload
        aggregate_payload = {'aggregate_id': 1}
        compute_utils.notify_about_aggregate_update(self.context,
                                                    "create.end",
                                                    aggregate_payload)
        self.assertEqual(len(fake_notifier.NOTIFICATIONS), 1)
        msg = fake_notifier.NOTIFICATIONS[0]
        self.assertEqual(msg.priority, 'INFO')
        self.assertEqual(msg.event_type, 'aggregate.create.end')
        payload = msg.payload
        self.assertEqual(payload['aggregate_id'], 1)

    def test_notify_about_aggregate_update_with_name(self):
        # Set aggregate payload
        aggregate_payload = {'name': 'fakegroup'}
        compute_utils.notify_about_aggregate_update(self.context,
                                                    "create.start",
                                                    aggregate_payload)
        self.assertEqual(len(fake_notifier.NOTIFICATIONS), 1)
        msg = fake_notifier.NOTIFICATIONS[0]
        self.assertEqual(msg.priority, 'INFO')
        self.assertEqual(msg.event_type, 'aggregate.create.start')
        payload = msg.payload
        self.assertEqual(payload['name'], 'fakegroup')

    def test_notify_about_aggregate_update_without_name_id(self):
        # Set empty aggregate payload
        aggregate_payload = {}
        compute_utils.notify_about_aggregate_update(self.context,
                                                    "create.start",
                                                    aggregate_payload)
        self.assertEqual(len(fake_notifier.NOTIFICATIONS), 0)


class ComputeUtilsGetValFromSysMetadata(test.NoDBTestCase):

    def test_get_value_from_system_metadata(self):
        instance = fake_instance.fake_instance_obj('fake-context')
        system_meta = {'int_val': 1,
                       'int_string': '2',
                       'not_int': 'Nope'}
        instance.system_metadata = system_meta

        result = compute_utils.get_value_from_system_metadata(
                   instance, 'int_val', int, 0)
        self.assertEqual(1, result)

        result = compute_utils.get_value_from_system_metadata(
                   instance, 'int_string', int, 0)
        self.assertEqual(2, result)

        result = compute_utils.get_value_from_system_metadata(
                   instance, 'not_int', int, 0)
        self.assertEqual(0, result)


class ComputeUtilsGetRebootTypes(test.NoDBTestCase):
    def setUp(self):
        super(ComputeUtilsGetRebootTypes, self).setUp()
        self.context = context.RequestContext('fake', 'fake')

    def test_get_reboot_type_started_soft(self):
        reboot_type = compute_utils.get_reboot_type(task_states.REBOOT_STARTED,
                                                    power_state.RUNNING)
        self.assertEqual(reboot_type, 'SOFT')

    def test_get_reboot_type_pending_soft(self):
        reboot_type = compute_utils.get_reboot_type(task_states.REBOOT_PENDING,
                                                    power_state.RUNNING)
        self.assertEqual(reboot_type, 'SOFT')

    def test_get_reboot_type_hard(self):
        reboot_type = compute_utils.get_reboot_type('foo', power_state.RUNNING)
        self.assertEqual(reboot_type, 'HARD')

    def test_get_reboot_not_running_hard(self):
        reboot_type = compute_utils.get_reboot_type('foo', 'bar')
        self.assertEqual(reboot_type, 'HARD')


class ComputeUtilsTestCase(test.NoDBTestCase):

    def setUp(self):
        super(ComputeUtilsTestCase, self).setUp()
        self.compute = 'compute'
        self.user_id = 'fake'
        self.project_id = 'fake'
        self.context = context.RequestContext(self.user_id,
                                              self.project_id)

    @mock.patch.object(objects.InstanceActionEvent, 'event_start')
    @mock.patch.object(objects.InstanceActionEvent,
                       'event_finish_with_failure')
    def test_wrap_instance_event(self, mock_finish, mock_start):
        inst = {"uuid": uuids.instance}

        @compute_utils.wrap_instance_event(prefix='compute')
        def fake_event(self, context, instance):
            pass

        fake_event(self.compute, self.context, instance=inst)

        self.assertTrue(mock_start.called)
        self.assertTrue(mock_finish.called)

    @mock.patch.object(objects.InstanceActionEvent, 'event_start')
    @mock.patch.object(objects.InstanceActionEvent,
                       'event_finish_with_failure')
    def test_wrap_instance_event_return(self, mock_finish, mock_start):
        inst = {"uuid": uuids.instance}

        @compute_utils.wrap_instance_event(prefix='compute')
        def fake_event(self, context, instance):
            return True

        retval = fake_event(self.compute, self.context, instance=inst)

        self.assertTrue(retval)
        self.assertTrue(mock_start.called)
        self.assertTrue(mock_finish.called)

    @mock.patch.object(objects.InstanceActionEvent, 'event_start')
    @mock.patch.object(objects.InstanceActionEvent,
                       'event_finish_with_failure')
    def test_wrap_instance_event_log_exception(self, mock_finish, mock_start):
        inst = {"uuid": uuids.instance}

        @compute_utils.wrap_instance_event(prefix='compute')
        def fake_event(self2, context, instance):
            raise exception.NovaException()

        self.assertRaises(exception.NovaException, fake_event,
                          self.compute, self.context, instance=inst)

        self.assertTrue(mock_start.called)
        self.assertTrue(mock_finish.called)
        args, kwargs = mock_finish.call_args
        self.assertIsInstance(kwargs['exc_val'], exception.NovaException)

    @mock.patch('netifaces.interfaces')
    def test_get_machine_ips_value_error(self, mock_interfaces):
        # Tests that the utility method does not explode if netifaces raises
        # a ValueError.
        iface = mock.sentinel
        mock_interfaces.return_value = [iface]
        with mock.patch('netifaces.ifaddresses',
                        side_effect=ValueError) as mock_ifaddresses:
            addresses = compute_utils.get_machine_ips()
            self.assertEqual([], addresses)
        mock_ifaddresses.assert_called_once_with(iface)

    @mock.patch('nova.compute.utils.notify_about_instance_usage')
    @mock.patch('nova.objects.Instance.destroy')
    def test_notify_about_instance_delete(self, mock_instance_destroy,
                                          mock_notify_usage):
        instance = fake_instance.fake_instance_obj(
            self.context, expected_attrs=('system_metadata',))
        with compute_utils.notify_about_instance_delete(
            mock.sentinel.notifier, self.context, instance):
            instance.destroy()
        expected_notify_calls = [
            mock.call(mock.sentinel.notifier, self.context, instance,
                      'delete.start'),
            mock.call(mock.sentinel.notifier, self.context, instance,
                      'delete.end', system_metadata=instance.system_metadata)
        ]
        mock_notify_usage.assert_has_calls(expected_notify_calls)


class ComputeUtilsQuotaTestCase(test.TestCase):
    def setUp(self):
        super(ComputeUtilsQuotaTestCase, self).setUp()
        self.context = context.RequestContext('fake', 'fake')

    def test_upsize_quota_delta(self):
        old_flavor = flavors.get_flavor_by_name('m1.tiny')
        new_flavor = flavors.get_flavor_by_name('m1.medium')

        expected_deltas = {
            'cores': new_flavor['vcpus'] - old_flavor['vcpus'],
            'ram': new_flavor['memory_mb'] - old_flavor['memory_mb']
        }

        deltas = compute_utils.upsize_quota_delta(self.context, new_flavor,
                                                  old_flavor)
        self.assertEqual(expected_deltas, deltas)

    def test_downsize_quota_delta(self):
        inst = create_instance(self.context, params=None)
        inst.old_flavor = flavors.get_flavor_by_name('m1.medium')
        inst.new_flavor = flavors.get_flavor_by_name('m1.tiny')

        expected_deltas = {
            'cores': (inst.new_flavor['vcpus'] -
                      inst.old_flavor['vcpus']),
            'ram': (inst.new_flavor['memory_mb'] -
                    inst.old_flavor['memory_mb'])
        }

        deltas = compute_utils.downsize_quota_delta(self.context, inst)
        self.assertEqual(expected_deltas, deltas)

    def test_reverse_quota_delta(self):
        inst = create_instance(self.context, params=None)
        inst.old_flavor = flavors.get_flavor_by_name('m1.tiny')
        inst.new_flavor = flavors.get_flavor_by_name('m1.medium')

        expected_deltas = {
            'cores': -1 * (inst.new_flavor['vcpus'] -
                           inst.old_flavor['vcpus']),
            'ram': -1 * (inst.new_flavor['memory_mb'] -
                         inst.old_flavor['memory_mb'])
        }

        deltas = compute_utils.reverse_upsize_quota_delta(self.context, inst)
        self.assertEqual(expected_deltas, deltas)

    @mock.patch.object(objects.Quotas, 'reserve')
    @mock.patch.object(objects.quotas, 'ids_from_instance')
    def test_reserve_quota_delta(self, mock_ids_from_instance,
                                 mock_reserve):
        quotas = objects.Quotas(context=context)
        inst = create_instance(self.context, params=None)
        inst.old_flavor = flavors.get_flavor_by_name('m1.tiny')
        inst.new_flavor = flavors.get_flavor_by_name('m1.medium')

        mock_ids_from_instance.return_value = (inst.project_id, inst.user_id)
        mock_reserve.return_value = quotas

        deltas = compute_utils.upsize_quota_delta(self.context,
                                                  inst.new_flavor,
                                                  inst.old_flavor)
        compute_utils.reserve_quota_delta(self.context, deltas, inst)
        mock_reserve.assert_called_once_with(project_id=inst.project_id,
                                             user_id=inst.user_id, **deltas)

    @mock.patch('nova.objects.Quotas.count_as_dict')
    def test_check_instance_quota_exceeds_with_multiple_resources(self,
                                                                  mock_count):
        quotas = {'cores': 1, 'instances': 1, 'ram': 512}
        overs = ['cores', 'instances', 'ram']
        over_quota_args = dict(quotas=quotas,
                               usages={'instances': 1, 'cores': 1, 'ram': 512},
                               overs=overs)
        e = exception.OverQuota(**over_quota_args)
        fake_flavor = objects.Flavor(vcpus=1, memory_mb=512)
        instance_num = 1
        proj_count = {'instances': 1, 'cores': 1, 'ram': 512}
        user_count = proj_count.copy()
        mock_count.return_value = {'project': proj_count, 'user': user_count}
        with mock.patch.object(objects.Quotas, 'limit_check_project_and_user',
                               side_effect=e):
            try:
                compute_utils.check_num_instances_quota(self.context,
                                                        fake_flavor,
                                                        instance_num,
                                                        instance_num)
            except exception.TooManyInstances as e:
                self.assertEqual('cores, instances, ram', e.kwargs['overs'])
                self.assertEqual('1, 1, 512', e.kwargs['req'])
                self.assertEqual('1, 1, 512', e.kwargs['used'])
                self.assertEqual('1, 1, 512', e.kwargs['allowed'])
            else:
                self.fail("Exception not raised")


class IsVolumeBackedInstanceTestCase(test.TestCase):
    def setUp(self):
        super(IsVolumeBackedInstanceTestCase, self).setUp()
        self.user_id = 'fake'
        self.project_id = 'fake'
        self.context = context.RequestContext(self.user_id,
                                            self.project_id)

    def test_is_volume_backed_instance_no_bdm_no_image(self):
        ctxt = self.context

        instance = create_instance(ctxt, params={'image_ref': ''})
        self.assertTrue(
            compute_utils.is_volume_backed_instance(ctxt, instance, None))

    def test_is_volume_backed_instance_empty_bdm_with_image(self):
        ctxt = self.context
        instance = create_instance(ctxt, params={
            'root_device_name': 'vda',
            'image_ref': FAKE_IMAGE_REF
        })
        self.assertFalse(
            compute_utils.is_volume_backed_instance(
                ctxt, instance,
                block_device_obj.block_device_make_list(ctxt, [])))

    def test_is_volume_backed_instance_bdm_volume_no_image(self):
        ctxt = self.context
        instance = create_instance(ctxt, params={
            'root_device_name': 'vda',
            'image_ref': ''
        })
        bdms = block_device_obj.block_device_make_list(ctxt,
                            [fake_block_device.FakeDbBlockDeviceDict(
                                {'source_type': 'volume',
                                 'device_name': '/dev/vda',
                                 'volume_id': uuids.volume_id,
                                 'instance_uuid':
                                     'f8000000-0000-0000-0000-000000000000',
                                 'boot_index': 0,
                                 'destination_type': 'volume'})])
        self.assertTrue(
            compute_utils.is_volume_backed_instance(ctxt, instance, bdms))

    def test_is_volume_backed_instance_bdm_local_no_image(self):
        # if the root device is local the instance is not volume backed, even
        # if no image_ref is set.
        ctxt = self.context
        instance = create_instance(ctxt, params={
            'root_device_name': 'vda',
            'image_ref': ''
        })
        bdms = block_device_obj.block_device_make_list(ctxt,
               [fake_block_device.FakeDbBlockDeviceDict(
                {'source_type': 'volume',
                 'device_name': '/dev/vda',
                 'volume_id': uuids.volume_id,
                 'destination_type': 'local',
                 'instance_uuid': 'f8000000-0000-0000-0000-000000000000',
                 'boot_index': 0,
                 'snapshot_id': None}),
                fake_block_device.FakeDbBlockDeviceDict(
                {'source_type': 'volume',
                 'device_name': '/dev/vdb',
                 'instance_uuid': 'f8000000-0000-0000-0000-000000000000',
                 'boot_index': 1,
                 'destination_type': 'volume',
                 'volume_id': 'c2ec2156-d75e-11e2-985b-5254009297d6',
                 'snapshot_id': None})])
        self.assertFalse(
            compute_utils.is_volume_backed_instance(ctxt, instance, bdms))

    def test_is_volume_backed_instance_bdm_volume_with_image(self):
        ctxt = self.context
        instance = create_instance(ctxt, params={
            'root_device_name': 'vda',
            'image_ref': FAKE_IMAGE_REF
        })
        bdms = block_device_obj.block_device_make_list(ctxt,
                            [fake_block_device.FakeDbBlockDeviceDict(
                                {'source_type': 'volume',
                                 'device_name': '/dev/vda',
                                 'volume_id': uuids.volume_id,
                                 'boot_index': 0,
                                 'destination_type': 'volume'})])
        self.assertTrue(
            compute_utils.is_volume_backed_instance(ctxt, instance, bdms))

    def test_is_volume_backed_instance_bdm_snapshot(self):
        ctxt = self.context
        instance = create_instance(ctxt, params={
            'root_device_name': 'vda'
        })
        bdms = block_device_obj.block_device_make_list(ctxt,
               [fake_block_device.FakeDbBlockDeviceDict(
                {'source_type': 'volume',
                 'device_name': '/dev/vda',
                 'snapshot_id': 'de8836ac-d75e-11e2-8271-5254009297d6',
                 'instance_uuid': 'f8000000-0000-0000-0000-000000000000',
                 'destination_type': 'volume',
                 'boot_index': 0,
                 'volume_id': None})])
        self.assertTrue(
            compute_utils.is_volume_backed_instance(ctxt, instance, bdms))

    @mock.patch.object(objects.BlockDeviceMappingList, 'get_by_instance_uuid')
    def test_is_volume_backed_instance_empty_bdm_by_uuid(self, mock_bdms):
        ctxt = self.context
        instance = create_instance(ctxt)
        mock_bdms.return_value = block_device_obj.block_device_make_list(
            ctxt, [])
        self.assertFalse(
            compute_utils.is_volume_backed_instance(ctxt, instance, None))
        mock_bdms.assert_called_with(ctxt, instance.uuid)
