# Copyright 2011 Justin Santa Barbara
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

import datetime
import time
import zlib

import mock
from oslo_log import log as logging
from oslo_serialization import base64
from oslo_utils import timeutils

from nova.compute import api as compute_api
from nova.compute import manager as compute_manager
from nova.compute import rpcapi
from nova import context
from nova import exception
from nova import objects
from nova.objects import block_device as block_device_obj
from nova import test
from nova.tests import fixtures as nova_fixtures
from nova.tests.functional.api import client
from nova.tests.functional import integrated_helpers
from nova.tests.unit.api.openstack import fakes
from nova.tests.unit import fake_block_device
from nova.tests.unit import fake_network
import nova.tests.unit.image.fake
from nova.tests.unit import policy_fixture
from nova.virt import fake
from nova import volume


LOG = logging.getLogger(__name__)


class ServersTestBase(integrated_helpers._IntegratedTestBase):
    api_major_version = 'v2'
    _force_delete_parameter = 'forceDelete'
    _image_ref_parameter = 'imageRef'
    _flavor_ref_parameter = 'flavorRef'
    _access_ipv4_parameter = 'accessIPv4'
    _access_ipv6_parameter = 'accessIPv6'
    _return_resv_id_parameter = 'return_reservation_id'
    _min_count_parameter = 'min_count'

    def setUp(self):
        super(ServersTestBase, self).setUp()
        # The network service is called as part of server creates but no
        # networks have been populated in the db, so stub the methods.
        # The networks aren't relevant to what is being tested.
        fake_network.set_stub_network_methods(self)
        self.conductor = self.start_service(
            'conductor', manager='nova.conductor.manager.ConductorManager')

    def _wait_for_state_change(self, server, from_status):
        for i in range(0, 50):
            server = self.api.get_server(server['id'])
            if server['status'] != from_status:
                break
            time.sleep(.1)

        return server

    def _wait_for_deletion(self, server_id):
        # Wait (briefly) for deletion
        for _retries in range(50):
            try:
                found_server = self.api.get_server(server_id)
            except client.OpenStackApiNotFoundException:
                found_server = None
                LOG.debug("Got 404, proceeding")
                break

            LOG.debug("Found_server=%s", found_server)

            # TODO(justinsb): Mock doesn't yet do accurate state changes
            # if found_server['status'] != 'deleting':
            #    break
            time.sleep(.1)

        # Should be gone
        self.assertFalse(found_server)

    def _delete_server(self, server_id):
        # Delete the server
        self.api.delete_server(server_id)
        self._wait_for_deletion(server_id)

    def _get_access_ips_params(self):
        return {self._access_ipv4_parameter: "172.19.0.2",
                self._access_ipv6_parameter: "fe80::2"}

    def _verify_access_ips(self, server):
        self.assertEqual('172.19.0.2',
                         server[self._access_ipv4_parameter])
        self.assertEqual('fe80::2', server[self._access_ipv6_parameter])


class ServersTest(ServersTestBase):

    def test_get_servers(self):
        # Simple check that listing servers works.
        servers = self.api.get_servers()
        for server in servers:
            LOG.debug("server: %s", server)

    def test_create_server_with_error(self):
        # Create a server which will enter error state.

        def throw_error(*args, **kwargs):
            raise exception.BuildAbortException(reason='',
                    instance_uuid='fake')

        self.stub_out('nova.virt.fake.FakeDriver.spawn', throw_error)

        server = self._build_minimal_create_server_request()
        created_server = self.api.post_server({"server": server})
        created_server_id = created_server['id']

        found_server = self.api.get_server(created_server_id)
        self.assertEqual(created_server_id, found_server['id'])

        found_server = self._wait_for_state_change(found_server, 'BUILD')

        self.assertEqual('ERROR', found_server['status'])
        self._delete_server(created_server_id)

    def test_create_and_delete_server(self):
        # Creates and deletes a server.

        # Create server
        # Build the server data gradually, checking errors along the way
        server = {}
        good_server = self._build_minimal_create_server_request()

        post = {'server': server}

        # Without an imageRef, this throws 500.
        # TODO(justinsb): Check whatever the spec says should be thrown here
        self.assertRaises(client.OpenStackApiException,
                          self.api.post_server, post)

        # With an invalid imageRef, this throws 500.
        server[self._image_ref_parameter] = self.get_invalid_image()
        # TODO(justinsb): Check whatever the spec says should be thrown here
        self.assertRaises(client.OpenStackApiException,
                          self.api.post_server, post)

        # Add a valid imageRef
        server[self._image_ref_parameter] = good_server.get(
            self._image_ref_parameter)

        # Without flavorRef, this throws 500
        # TODO(justinsb): Check whatever the spec says should be thrown here
        self.assertRaises(client.OpenStackApiException,
                          self.api.post_server, post)

        server[self._flavor_ref_parameter] = good_server.get(
            self._flavor_ref_parameter)

        # Without a name, this throws 500
        # TODO(justinsb): Check whatever the spec says should be thrown here
        self.assertRaises(client.OpenStackApiException,
                          self.api.post_server, post)

        # Set a valid server name
        server['name'] = good_server['name']

        created_server = self.api.post_server(post)
        LOG.debug("created_server: %s", created_server)
        self.assertTrue(created_server['id'])
        created_server_id = created_server['id']

        # Check it's there
        found_server = self.api.get_server(created_server_id)
        self.assertEqual(created_server_id, found_server['id'])

        # It should also be in the all-servers list
        servers = self.api.get_servers()
        server_ids = [s['id'] for s in servers]
        self.assertIn(created_server_id, server_ids)

        found_server = self._wait_for_state_change(found_server, 'BUILD')
        # It should be available...
        # TODO(justinsb): Mock doesn't yet do this...
        self.assertEqual('ACTIVE', found_server['status'])
        servers = self.api.get_servers(detail=True)
        for server in servers:
            self.assertIn("image", server)
            self.assertIn("flavor", server)

        self._delete_server(created_server_id)

    def _force_reclaim(self):
        # Make sure that compute manager thinks the instance is
        # old enough to be expired
        the_past = timeutils.utcnow() + datetime.timedelta(hours=1)
        timeutils.set_time_override(override_time=the_past)
        self.addCleanup(timeutils.clear_time_override)
        ctxt = context.get_admin_context()
        self.compute._reclaim_queued_deletes(ctxt)

    def test_deferred_delete(self):
        # Creates, deletes and waits for server to be reclaimed.
        self.flags(reclaim_instance_interval=1)

        # Create server
        server = self._build_minimal_create_server_request()

        created_server = self.api.post_server({'server': server})
        LOG.debug("created_server: %s", created_server)
        self.assertTrue(created_server['id'])
        created_server_id = created_server['id']

        # Wait for it to finish being created
        found_server = self._wait_for_state_change(created_server, 'BUILD')

        # It should be available...
        self.assertEqual('ACTIVE', found_server['status'])

        # Cannot restore unless instance is deleted
        self.assertRaises(client.OpenStackApiException,
                          self.api.post_server_action, created_server_id,
                          {'restore': {}})

        # Delete the server
        self.api.delete_server(created_server_id)

        # Wait for queued deletion
        found_server = self._wait_for_state_change(found_server, 'ACTIVE')
        self.assertEqual('SOFT_DELETED', found_server['status'])

        self._force_reclaim()

        # Wait for real deletion
        self._wait_for_deletion(created_server_id)

    def test_deferred_delete_restore(self):
        # Creates, deletes and restores a server.
        self.flags(reclaim_instance_interval=3600)

        # Create server
        server = self._build_minimal_create_server_request()

        created_server = self.api.post_server({'server': server})
        LOG.debug("created_server: %s", created_server)
        self.assertTrue(created_server['id'])
        created_server_id = created_server['id']

        # Wait for it to finish being created
        found_server = self._wait_for_state_change(created_server, 'BUILD')

        # It should be available...
        self.assertEqual('ACTIVE', found_server['status'])

        # Delete the server
        self.api.delete_server(created_server_id)

        # Wait for queued deletion
        found_server = self._wait_for_state_change(found_server, 'ACTIVE')
        self.assertEqual('SOFT_DELETED', found_server['status'])

        # Restore server
        self.api.post_server_action(created_server_id, {'restore': {}})

        # Wait for server to become active again
        found_server = self._wait_for_state_change(found_server, 'DELETED')
        self.assertEqual('ACTIVE', found_server['status'])

    def test_deferred_delete_restore_overquota(self):
        # Test that a restore that would put the user over quota fails
        self.flags(instances=1, group='quota')
        # Creates, deletes and restores a server.
        self.flags(reclaim_instance_interval=3600)

        # Create server
        server = self._build_minimal_create_server_request()

        created_server1 = self.api.post_server({'server': server})
        LOG.debug("created_server: %s", created_server1)
        self.assertTrue(created_server1['id'])
        created_server_id1 = created_server1['id']

        # Wait for it to finish being created
        found_server1 = self._wait_for_state_change(created_server1, 'BUILD')

        # It should be available...
        self.assertEqual('ACTIVE', found_server1['status'])

        # Delete the server
        self.api.delete_server(created_server_id1)

        # Wait for queued deletion
        found_server1 = self._wait_for_state_change(found_server1, 'ACTIVE')
        self.assertEqual('SOFT_DELETED', found_server1['status'])

        # Create a second server
        server = self._build_minimal_create_server_request()

        created_server2 = self.api.post_server({'server': server})
        LOG.debug("created_server: %s", created_server2)
        self.assertTrue(created_server2['id'])

        # Wait for it to finish being created
        found_server2 = self._wait_for_state_change(created_server2, 'BUILD')

        # It should be available...
        self.assertEqual('ACTIVE', found_server2['status'])

        # Try to restore the first server, it should fail
        ex = self.assertRaises(client.OpenStackApiException,
                               self.api.post_server_action,
                               created_server_id1, {'restore': {}})
        self.assertEqual(403, ex.response.status_code)
        self.assertEqual('SOFT_DELETED', found_server1['status'])

    def test_deferred_delete_force(self):
        # Creates, deletes and force deletes a server.
        self.flags(reclaim_instance_interval=3600)

        # Create server
        server = self._build_minimal_create_server_request()

        created_server = self.api.post_server({'server': server})
        LOG.debug("created_server: %s", created_server)
        self.assertTrue(created_server['id'])
        created_server_id = created_server['id']

        # Wait for it to finish being created
        found_server = self._wait_for_state_change(created_server, 'BUILD')

        # It should be available...
        self.assertEqual('ACTIVE', found_server['status'])

        # Delete the server
        self.api.delete_server(created_server_id)

        # Wait for queued deletion
        found_server = self._wait_for_state_change(found_server, 'ACTIVE')
        self.assertEqual('SOFT_DELETED', found_server['status'])

        # Force delete server
        self.api.post_server_action(created_server_id,
                                    {self._force_delete_parameter: {}})

        # Wait for real deletion
        self._wait_for_deletion(created_server_id)

    def test_create_server_with_metadata(self):
        # Creates a server with metadata.

        # Build the server data gradually, checking errors along the way
        server = self._build_minimal_create_server_request()

        metadata = {}
        for i in range(30):
            metadata['key_%s' % i] = 'value_%s' % i

        server['metadata'] = metadata

        post = {'server': server}
        created_server = self.api.post_server(post)
        LOG.debug("created_server: %s", created_server)
        self.assertTrue(created_server['id'])
        created_server_id = created_server['id']

        found_server = self.api.get_server(created_server_id)
        self.assertEqual(created_server_id, found_server['id'])
        self.assertEqual(metadata, found_server.get('metadata'))

        # The server should also be in the all-servers details list
        servers = self.api.get_servers(detail=True)
        server_map = {server['id']: server for server in servers}
        found_server = server_map.get(created_server_id)
        self.assertTrue(found_server)
        # Details do include metadata
        self.assertEqual(metadata, found_server.get('metadata'))

        # The server should also be in the all-servers summary list
        servers = self.api.get_servers(detail=False)
        server_map = {server['id']: server for server in servers}
        found_server = server_map.get(created_server_id)
        self.assertTrue(found_server)
        # Summary should not include metadata
        self.assertFalse(found_server.get('metadata'))

        # Cleanup
        self._delete_server(created_server_id)

    def test_server_metadata_actions_negative_invalid_state(self):
        # Create server with metadata
        server = self._build_minimal_create_server_request()

        metadata = {'key_1': 'value_1'}

        server['metadata'] = metadata

        post = {'server': server}
        created_server = self.api.post_server(post)

        found_server = self._wait_for_state_change(created_server, 'BUILD')
        self.assertEqual('ACTIVE', found_server['status'])
        self.assertEqual(metadata, found_server.get('metadata'))
        server_id = found_server['id']

        # Change status from ACTIVE to SHELVED for negative test
        self.flags(shelved_offload_time = -1)
        self.api.post_server_action(server_id, {'shelve': {}})
        found_server = self._wait_for_state_change(found_server, 'ACTIVE')
        self.assertEqual('SHELVED', found_server['status'])

        metadata = {'key_2': 'value_2'}

        # Update Metadata item in SHELVED (not ACTIVE, etc.)
        ex = self.assertRaises(client.OpenStackApiException,
                               self.api.post_server_metadata,
                               server_id, metadata)
        self.assertEqual(409, ex.response.status_code)
        self.assertEqual('SHELVED', found_server['status'])

        # Delete Metadata item in SHELVED (not ACTIVE, etc.)
        ex = self.assertRaises(client.OpenStackApiException,
                               self.api.delete_server_metadata,
                               server_id, 'key_1')
        self.assertEqual(409, ex.response.status_code)
        self.assertEqual('SHELVED', found_server['status'])

        # Cleanup
        self._delete_server(server_id)

    def test_create_and_rebuild_server(self):
        # Rebuild a server with metadata.

        # create a server with initially has no metadata
        server = self._build_minimal_create_server_request()
        server_post = {'server': server}

        metadata = {}
        for i in range(30):
            metadata['key_%s' % i] = 'value_%s' % i

        server_post['server']['metadata'] = metadata

        created_server = self.api.post_server(server_post)
        LOG.debug("created_server: %s", created_server)
        self.assertTrue(created_server['id'])
        created_server_id = created_server['id']

        created_server = self._wait_for_state_change(created_server, 'BUILD')

        # rebuild the server with metadata and other server attributes
        post = {}
        post['rebuild'] = {
            self._image_ref_parameter: "76fa36fc-c930-4bf3-8c8a-ea2a2420deb6",
            "name": "blah",
            self._access_ipv4_parameter: "172.19.0.2",
            self._access_ipv6_parameter: "fe80::2",
            "metadata": {'some': 'thing'},
        }
        post['rebuild'].update(self._get_access_ips_params())

        self.api.post_server_action(created_server_id, post)
        LOG.debug("rebuilt server: %s", created_server)
        self.assertTrue(created_server['id'])

        found_server = self.api.get_server(created_server_id)
        self.assertEqual(created_server_id, found_server['id'])
        self.assertEqual({'some': 'thing'}, found_server.get('metadata'))
        self.assertEqual('blah', found_server.get('name'))
        self.assertEqual(post['rebuild'][self._image_ref_parameter],
                         found_server.get('image')['id'])
        self._verify_access_ips(found_server)

        # rebuild the server with empty metadata and nothing else
        post = {}
        post['rebuild'] = {
            self._image_ref_parameter: "76fa36fc-c930-4bf3-8c8a-ea2a2420deb6",
            "metadata": {},
        }

        self.api.post_server_action(created_server_id, post)
        LOG.debug("rebuilt server: %s", created_server)
        self.assertTrue(created_server['id'])

        found_server = self.api.get_server(created_server_id)
        self.assertEqual(created_server_id, found_server['id'])
        self.assertEqual({}, found_server.get('metadata'))
        self.assertEqual('blah', found_server.get('name'))
        self.assertEqual(post['rebuild'][self._image_ref_parameter],
                         found_server.get('image')['id'])
        self._verify_access_ips(found_server)

        # Cleanup
        self._delete_server(created_server_id)

    def test_rename_server(self):
        # Test building and renaming a server.

        # Create a server
        server = self._build_minimal_create_server_request()
        created_server = self.api.post_server({'server': server})
        LOG.debug("created_server: %s", created_server)
        server_id = created_server['id']
        self.assertTrue(server_id)

        # Rename the server to 'new-name'
        self.api.put_server(server_id, {'server': {'name': 'new-name'}})

        # Check the name of the server
        created_server = self.api.get_server(server_id)
        self.assertEqual(created_server['name'], 'new-name')

        # Cleanup
        self._delete_server(server_id)

    def test_create_multiple_servers(self):
        # Creates multiple servers and checks for reservation_id.

        # Create 2 servers, setting 'return_reservation_id, which should
        # return a reservation_id
        server = self._build_minimal_create_server_request()
        server[self._min_count_parameter] = 2
        server[self._return_resv_id_parameter] = True
        post = {'server': server}
        response = self.api.post_server(post)
        self.assertIn('reservation_id', response)
        reservation_id = response['reservation_id']
        self.assertNotIn(reservation_id, ['', None])

        # Create 1 more server, which should not return a reservation_id
        server = self._build_minimal_create_server_request()
        post = {'server': server}
        created_server = self.api.post_server(post)
        self.assertTrue(created_server['id'])
        created_server_id = created_server['id']

        # lookup servers created by the first request.
        servers = self.api.get_servers(detail=True,
                search_opts={'reservation_id': reservation_id})
        server_map = {server['id']: server for server in servers}
        found_server = server_map.get(created_server_id)
        # The server from the 2nd request should not be there.
        self.assertIsNone(found_server)
        # Should have found 2 servers.
        self.assertEqual(len(server_map), 2)

        # Cleanup
        self._delete_server(created_server_id)
        for server_id in server_map:
            self._delete_server(server_id)

    def test_create_server_with_injected_files(self):
        # Creates a server with injected_files.
        personality = []

        # Inject a text file
        data = 'Hello, World!'
        personality.append({
            'path': '/helloworld.txt',
            'contents': base64.encode_as_bytes(data),
        })

        # Inject a binary file
        data = zlib.compress(b'Hello, World!')
        personality.append({
            'path': '/helloworld.zip',
            'contents': base64.encode_as_bytes(data),
        })

        # Create server
        server = self._build_minimal_create_server_request()
        server['personality'] = personality

        post = {'server': server}

        created_server = self.api.post_server(post)
        LOG.debug("created_server: %s", created_server)
        self.assertTrue(created_server['id'])
        created_server_id = created_server['id']

        # Check it's there
        found_server = self.api.get_server(created_server_id)
        self.assertEqual(created_server_id, found_server['id'])

        found_server = self._wait_for_state_change(found_server, 'BUILD')
        self.assertEqual('ACTIVE', found_server['status'])

        # Cleanup
        self._delete_server(created_server_id)

    def test_stop_start_servers_negative_invalid_state(self):
        # Create server
        server = self._build_minimal_create_server_request()
        created_server = self.api.post_server({"server": server})
        created_server_id = created_server['id']

        found_server = self._wait_for_state_change(created_server, 'BUILD')
        self.assertEqual('ACTIVE', found_server['status'])

        # Start server in ACTIVE
        # NOTE(mkoshiya): When os-start API runs, the server status
        # must be SHUTOFF.
        # By returning 409, I want to confirm that the ACTIVE server does not
        # cause unexpected behavior.
        post = {'os-start': {}}
        ex = self.assertRaises(client.OpenStackApiException,
                               self.api.post_server_action,
                               created_server_id, post)
        self.assertEqual(409, ex.response.status_code)
        self.assertEqual('ACTIVE', found_server['status'])

        # Stop server
        post = {'os-stop': {}}
        self.api.post_server_action(created_server_id, post)
        found_server = self._wait_for_state_change(found_server, 'ACTIVE')
        self.assertEqual('SHUTOFF', found_server['status'])

        # Stop server in SHUTOFF
        # NOTE(mkoshiya): When os-stop API runs, the server status
        # must be ACTIVE or ERROR.
        # By returning 409, I want to confirm that the SHUTOFF server does not
        # cause unexpected behavior.
        post = {'os-stop': {}}
        ex = self.assertRaises(client.OpenStackApiException,
                               self.api.post_server_action,
                               created_server_id, post)
        self.assertEqual(409, ex.response.status_code)
        self.assertEqual('SHUTOFF', found_server['status'])

        # Cleanup
        self._delete_server(created_server_id)

    def test_revert_resized_server_negative_invalid_state(self):
        # Create server
        server = self._build_minimal_create_server_request()
        created_server = self.api.post_server({"server": server})
        created_server_id = created_server['id']
        found_server = self._wait_for_state_change(created_server, 'BUILD')
        self.assertEqual('ACTIVE', found_server['status'])

        # Revert resized server in ACTIVE
        # NOTE(yatsumi): When revert resized server API runs,
        # the server status must be VERIFY_RESIZE.
        # By returning 409, I want to confirm that the ACTIVE server does not
        # cause unexpected behavior.
        post = {'revertResize': {}}
        ex = self.assertRaises(client.OpenStackApiException,
                               self.api.post_server_action,
                               created_server_id, post)
        self.assertEqual(409, ex.response.status_code)
        self.assertEqual('ACTIVE', found_server['status'])

        # Cleanup
        self._delete_server(created_server_id)

    def test_resize_server_negative_invalid_state(self):
        # Avoid migration
        self.flags(allow_resize_to_same_host=True)

        # Create server
        server = self._build_minimal_create_server_request()
        created_server = self.api.post_server({"server": server})
        created_server_id = created_server['id']
        found_server = self._wait_for_state_change(created_server, 'BUILD')
        self.assertEqual('ACTIVE', found_server['status'])

        # Resize server(flavorRef: 1 -> 2)
        post = {'resize': {"flavorRef": "2", "OS-DCF:diskConfig": "AUTO"}}
        self.api.post_server_action(created_server_id, post)
        found_server = self._wait_for_state_change(found_server, 'RESIZE')
        self.assertEqual('VERIFY_RESIZE', found_server['status'])

        # Resize server in VERIFY_RESIZE(flavorRef: 2 -> 1)
        # NOTE(yatsumi): When resize API runs, the server status
        # must be ACTIVE or SHUTOFF.
        # By returning 409, I want to confirm that the VERIFY_RESIZE server
        # does not cause unexpected behavior.
        post = {'resize': {"flavorRef": "1", "OS-DCF:diskConfig": "AUTO"}}
        ex = self.assertRaises(client.OpenStackApiException,
                               self.api.post_server_action,
                               created_server_id, post)
        self.assertEqual(409, ex.response.status_code)
        self.assertEqual('VERIFY_RESIZE', found_server['status'])

        # Cleanup
        self._delete_server(created_server_id)

    def test_confirm_resized_server_negative_invalid_state(self):
        # Create server
        server = self._build_minimal_create_server_request()
        created_server = self.api.post_server({"server": server})
        created_server_id = created_server['id']
        found_server = self._wait_for_state_change(created_server, 'BUILD')
        self.assertEqual('ACTIVE', found_server['status'])

        # Confirm resized server in ACTIVE
        # NOTE(yatsumi): When confirm resized server API runs,
        # the server status must be VERIFY_RESIZE.
        # By returning 409, I want to confirm that the ACTIVE server does not
        # cause unexpected behavior.
        post = {'confirmResize': {}}
        ex = self.assertRaises(client.OpenStackApiException,
                               self.api.post_server_action,
                               created_server_id, post)
        self.assertEqual(409, ex.response.status_code)
        self.assertEqual('ACTIVE', found_server['status'])

        # Cleanup
        self._delete_server(created_server_id)

    def test_resize_server_overquota(self):
        self.flags(cores=1, group='quota')
        self.flags(ram=512, group='quota')
        # Create server with default flavor, 1 core, 512 ram
        server = self._build_minimal_create_server_request()
        created_server = self.api.post_server({"server": server})
        created_server_id = created_server['id']

        found_server = self._wait_for_state_change(created_server, 'BUILD')
        self.assertEqual('ACTIVE', found_server['status'])

        # Try to resize to flavorid 2, 1 core, 2048 ram
        post = {'resize': {'flavorRef': '2'}}
        ex = self.assertRaises(client.OpenStackApiException,
                               self.api.post_server_action,
                               created_server_id, post)
        self.assertEqual(403, ex.response.status_code)


class ServersTestV21(ServersTest):
    api_major_version = 'v2.1'


class ServersTestV219(ServersTestBase):
    api_major_version = 'v2.1'

    def _create_server(self, set_desc = True, desc = None):
        server = self._build_minimal_create_server_request()
        if set_desc:
            server['description'] = desc
        post = {'server': server}
        response = self.api.api_post('/servers', post).body
        return (server, response['server'])

    def _update_server(self, server_id, set_desc = True, desc = None):
        new_name = integrated_helpers.generate_random_alphanumeric(8)
        server = {'server': {'name': new_name}}
        if set_desc:
            server['server']['description'] = desc
        self.api.api_put('/servers/%s' % server_id, server)

    def _rebuild_server(self, server_id, set_desc = True, desc = None):
        new_name = integrated_helpers.generate_random_alphanumeric(8)
        post = {}
        post['rebuild'] = {
            "name": new_name,
            self._image_ref_parameter: "76fa36fc-c930-4bf3-8c8a-ea2a2420deb6",
            self._access_ipv4_parameter: "172.19.0.2",
            self._access_ipv6_parameter: "fe80::2",
            "metadata": {'some': 'thing'},
        }
        post['rebuild'].update(self._get_access_ips_params())
        if set_desc:
            post['rebuild']['description'] = desc
        self.api.api_post('/servers/%s/action' % server_id, post)

    def _create_server_and_verify(self, set_desc = True, expected_desc = None):
        # Creates a server with a description and verifies it is
        # in the GET responses.
        created_server_id = self._create_server(set_desc,
                                                expected_desc)[1]['id']
        self._verify_server_description(created_server_id, expected_desc)
        self._delete_server(created_server_id)

    def _update_server_and_verify(self, server_id, set_desc = True,
                                  expected_desc = None):
        # Updates a server with a description and verifies it is
        # in the GET responses.
        self._update_server(server_id, set_desc, expected_desc)
        self._verify_server_description(server_id, expected_desc)

    def _rebuild_server_and_verify(self, server_id, set_desc = True,
                                  expected_desc = None):
        # Rebuilds a server with a description and verifies it is
        # in the GET responses.
        self._rebuild_server(server_id, set_desc, expected_desc)
        self._verify_server_description(server_id, expected_desc)

    def _verify_server_description(self, server_id, expected_desc = None,
                                   desc_in_resp = True):
        # Calls GET on the servers and verifies that the description
        # is set as expected in the response, or not set at all.
        response = self.api.api_get('/servers/%s' % server_id)
        found_server = response.body['server']
        self.assertEqual(server_id, found_server['id'])
        if desc_in_resp:
            # Verify the description is set as expected (can be None)
            self.assertEqual(expected_desc, found_server.get('description'))
        else:
            # Verify the description is not included in the response.
            self.assertNotIn('description', found_server)

        servers = self.api.api_get('/servers/detail').body['servers']
        server_map = {server['id']: server for server in servers}
        found_server = server_map.get(server_id)
        self.assertTrue(found_server)
        if desc_in_resp:
            # Verify the description is set as expected (can be None)
            self.assertEqual(expected_desc, found_server.get('description'))
        else:
            # Verify the description is not included in the response.
            self.assertNotIn('description', found_server)

    def _create_assertRaisesRegex(self, desc):
        # Verifies that a 400 error is thrown on create server
        with self.assertRaisesRegex(client.OpenStackApiException,
                                    ".*Unexpected status code.*") as cm:
            self._create_server(True, desc)
            self.assertEqual(400, cm.exception.response.status_code)

    def _update_assertRaisesRegex(self, server_id, desc):
        # Verifies that a 400 error is thrown on update server
        with self.assertRaisesRegex(client.OpenStackApiException,
                                    ".*Unexpected status code.*") as cm:
            self._update_server(server_id, True, desc)
            self.assertEqual(400, cm.exception.response.status_code)

    def _rebuild_assertRaisesRegex(self, server_id, desc):
        # Verifies that a 400 error is thrown on rebuild server
        with self.assertRaisesRegex(client.OpenStackApiException,
                                    ".*Unexpected status code.*") as cm:
            self._rebuild_server(server_id, True, desc)
            self.assertEqual(400, cm.exception.response.status_code)

    def test_create_server_with_description(self):
        self.api.microversion = '2.19'
        # Create and get a server with a description
        self._create_server_and_verify(True, 'test description')
        # Create and get a server with an empty description
        self._create_server_and_verify(True, '')
        # Create and get a server with description set to None
        self._create_server_and_verify()
        # Create and get a server without setting the description
        self._create_server_and_verify(False)

    def test_update_server_with_description(self):
        self.api.microversion = '2.19'
        # Create a server with an initial description
        server_id = self._create_server(True, 'test desc 1')[1]['id']

        # Update and get the server with a description
        self._update_server_and_verify(server_id, True, 'updated desc')
        # Update and get the server name without changing the description
        self._update_server_and_verify(server_id, False, 'updated desc')
        # Update and get the server with an empty description
        self._update_server_and_verify(server_id, True, '')
        # Update and get the server by removing the description (set to None)
        self._update_server_and_verify(server_id)
        # Update and get the server with a 2nd new description
        self._update_server_and_verify(server_id, True, 'updated desc2')

        # Cleanup
        self._delete_server(server_id)

    def test_rebuild_server_with_description(self):
        self.api.microversion = '2.19'

        # Create a server with an initial description
        server = self._create_server(True, 'test desc 1')[1]
        server_id = server['id']
        self._wait_for_state_change(server, 'BUILD')

        # Rebuild and get the server with a description
        self._rebuild_server_and_verify(server_id, True, 'updated desc')
        # Rebuild and get the server name without changing the description
        self._rebuild_server_and_verify(server_id, False, 'updated desc')
        # Rebuild and get the server with an empty description
        self._rebuild_server_and_verify(server_id, True, '')
        # Rebuild and get the server by removing the description (set to None)
        self._rebuild_server_and_verify(server_id)
        # Rebuild and get the server with a 2nd new description
        self._rebuild_server_and_verify(server_id, True, 'updated desc2')

        # Cleanup
        self._delete_server(server_id)

    def test_version_compatibility(self):
        # Create a server with microversion v2.19 and a description.
        self.api.microversion = '2.19'
        server_id = self._create_server(True, 'test desc 1')[1]['id']
        # Verify that the description is not included on V2.18 GETs
        self.api.microversion = '2.18'
        self._verify_server_description(server_id, desc_in_resp = False)
        # Verify that updating the server with description on V2.18
        # results in a 400 error
        self._update_assertRaisesRegex(server_id, 'test update 2.18')
        # Verify that rebuilding the server with description on V2.18
        # results in a 400 error
        self._rebuild_assertRaisesRegex(server_id, 'test rebuild 2.18')

        # Cleanup
        self._delete_server(server_id)

        # Create a server on V2.18 and verify that the description
        # defaults to the name on a V2.19 GET
        server_req, response = self._create_server(False)
        server_id = response['id']
        self.api.microversion = '2.19'
        self._verify_server_description(server_id, server_req['name'])

        # Cleanup
        self._delete_server(server_id)

        # Verify that creating a server with description on V2.18
        # results in a 400 error
        self.api.microversion = '2.18'
        self._create_assertRaisesRegex('test create 2.18')

    def test_description_errors(self):
        self.api.microversion = '2.19'
        # Create servers with invalid descriptions.  These throw 400.
        # Invalid unicode with non-printable control char
        self._create_assertRaisesRegex(u'invalid\0dstring')
        # Description is longer than 255 chars
        self._create_assertRaisesRegex('x' * 256)

        # Update and rebuild servers with invalid descriptions.
        # These throw 400.
        server_id = self._create_server(True, "desc")[1]['id']
        # Invalid unicode with non-printable control char
        self._update_assertRaisesRegex(server_id, u'invalid\u0604string')
        self._rebuild_assertRaisesRegex(server_id, u'invalid\u0604string')
        # Description is longer than 255 chars
        self._update_assertRaisesRegex(server_id, 'x' * 256)
        self._rebuild_assertRaisesRegex(server_id, 'x' * 256)


class ServerTestV220(ServersTestBase):
    api_major_version = 'v2.1'

    def setUp(self):
        super(ServerTestV220, self).setUp()
        self.api.microversion = '2.20'
        fake_network.set_stub_network_methods(self)
        self.ctxt = context.get_admin_context()

    def _create_server(self):
        server = self._build_minimal_create_server_request()
        post = {'server': server}
        response = self.api.api_post('/servers', post).body
        return (server, response['server'])

    def _shelve_server(self):
        server = self._create_server()[1]
        server_id = server['id']
        self._wait_for_state_change(server, 'BUILD')
        self.api.post_server_action(server_id, {'shelve': None})
        return self._wait_for_state_change(server, 'ACTIVE')

    def _get_fake_bdms(self, ctxt):
        return block_device_obj.block_device_make_list(self.ctxt,
                    [fake_block_device.FakeDbBlockDeviceDict(
                    {'device_name': '/dev/vda',
                     'source_type': 'volume',
                     'destination_type': 'volume',
                     'volume_id': '5d721593-f033-4f6d-ab6f-b5b067e61bc4'})])

    def test_attach_detach_vol_to_shelved_server(self):
        self.flags(shelved_offload_time=-1)
        found_server = self._shelve_server()
        self.assertEqual('SHELVED', found_server['status'])
        server_id = found_server['id']

        # Test attach volume
        with test.nested(mock.patch.object(compute_api.API,
                                       '_check_attach_and_reserve_volume'),
                         mock.patch.object(rpcapi.ComputeAPI,
                                       'attach_volume')) as (mock_reserve,
                                                             mock_attach):
            volume_attachment = {"volumeAttachment": {"volumeId":
                                       "5d721593-f033-4f6d-ab6f-b5b067e61bc4"}}
            self.api.api_post(
                            '/servers/%s/os-volume_attachments' % (server_id),
                            volume_attachment)
            self.assertTrue(mock_reserve.called)
            self.assertTrue(mock_attach.called)

        # Test detach volume
        self.stub_out('nova.volume.cinder.API.get', fakes.stub_volume_get)
        with test.nested(mock.patch.object(volume.cinder.API,
                                           'begin_detaching'),
                         mock.patch.object(objects.BlockDeviceMappingList,
                                           'get_by_instance_uuid'),
                         mock.patch.object(rpcapi.ComputeAPI,
                                           'detach_volume')
                         ) as (mock_check, mock_get_bdms, mock_rpc):

            mock_get_bdms.return_value = self._get_fake_bdms(self.ctxt)
            attachment_id = mock_get_bdms.return_value[0]['volume_id']

            self.api.api_delete('/servers/%s/os-volume_attachments/%s' %
                            (server_id, attachment_id))
            self.assertTrue(mock_check.called)
            self.assertTrue(mock_rpc.called)

        self._delete_server(server_id)

    def test_attach_detach_vol_to_shelved_offloaded_server(self):
        self.flags(shelved_offload_time=0)
        found_server = self._shelve_server()
        self.assertEqual('SHELVED_OFFLOADED', found_server['status'])
        server_id = found_server['id']

        # Test attach volume
        with test.nested(mock.patch.object(compute_api.API,
                                       '_check_attach_and_reserve_volume'),
                         mock.patch.object(volume.cinder.API,
                                       'attach')) as (mock_reserve, mock_vol):
            volume_attachment = {"volumeAttachment": {"volumeId":
                                       "5d721593-f033-4f6d-ab6f-b5b067e61bc4"}}
            attach_response = self.api.api_post(
                             '/servers/%s/os-volume_attachments' % (server_id),
                             volume_attachment).body['volumeAttachment']
            self.assertTrue(mock_reserve.called)
            self.assertTrue(mock_vol.called)
            self.assertIsNone(attach_response['device'])

        # Test detach volume
        self.stub_out('nova.volume.cinder.API.get', fakes.stub_volume_get)
        with test.nested(mock.patch.object(volume.cinder.API,
                                           'begin_detaching'),
                         mock.patch.object(objects.BlockDeviceMappingList,
                                           'get_by_instance_uuid'),
                         mock.patch.object(compute_api.API,
                                           '_local_cleanup_bdm_volumes')
                         ) as (mock_check, mock_get_bdms, mock_clean_vols):

            mock_get_bdms.return_value = self._get_fake_bdms(self.ctxt)
            attachment_id = mock_get_bdms.return_value[0]['volume_id']
            self.api.api_delete('/servers/%s/os-volume_attachments/%s' %
                            (server_id, attachment_id))
            self.assertTrue(mock_check.called)
            self.assertTrue(mock_clean_vols.called)

        self._delete_server(server_id)


class ServerMovingTests(test.TestCase, integrated_helpers.InstanceHelperMixin):
    """Tests moving servers while checking the resource allocations and usages

    These tests use two compute hosts. Boot a server on one of them then try to
    move the server to the other. At every step resource allocation of the
    server and the resource usages of the computes are queried from placement
    API and asserted.
    """

    REQUIRES_LOCKING = True
    microversion = 'latest'

    _enabled_filters = ['RetryFilter', 'ComputeFilter']

    def setUp(self):
        # NOTE(danms): The test defaults to using SmallFakeDriver,
        # which only has one vcpu, which can't take the doubled allocation
        # we're now giving it. So, use the bigger MediumFakeDriver here.
        self.flags(compute_driver='fake.MediumFakeDriver')
        self.flags(enabled_filters=self._enabled_filters,
                   group='filter_scheduler')
        super(ServerMovingTests, self).setUp()

        self.useFixture(policy_fixture.RealPolicyFixture())
        self.useFixture(nova_fixtures.NeutronFixture(self))
        self.useFixture(nova_fixtures.AllServicesCurrent())

        placement = self.useFixture(nova_fixtures.PlacementFixture())
        self.placement_api = placement.api
        api_fixture = self.useFixture(nova_fixtures.OSAPIFixture(
            api_version='v2.1'))

        self.admin_api = api_fixture.admin_api
        self.admin_api.microversion = self.microversion

        # NOTE(danms): We need admin_api so we can direct builds to
        # specific compute nodes. We're not doing anything in these tests
        # that should be affected by admin-ness, so this should be okay.
        self.api = self.admin_api

        # the image fake backend needed for image discovery
        nova.tests.unit.image.fake.stub_out_image_service(self)

        self.start_service('conductor')
        self.start_service('scheduler')

        self.addCleanup(nova.tests.unit.image.fake.FakeImageService_reset)

        fake.set_nodes(['host1'])
        self.flags(host='host1')
        self.compute1 = self.start_service('compute', host='host1')

        # NOTE(sbauza): Make sure the FakeDriver returns a different nodename
        # for the second compute node.
        fake.set_nodes(['host2'])
        self.addCleanup(fake.restore_nodes)
        self.flags(host='host2')
        self.compute2 = self.start_service('compute', host='host2')
        fake_network.set_stub_network_methods(self)

        flavors = self.api.get_flavors()
        self.flavor1 = flavors[0]
        self.flavor2 = flavors[1]
        # create flavor3 which has less MEMORY_MB but more DISK_GB than flavor2
        flavor_body = {'flavor':
                           {'name': 'test_flavor3',
                            'ram': int(self.flavor2['ram'] / 2),
                            'vcpus': 1,
                            'disk': self.flavor2['disk'] * 2,
                            'id': 'a22d5517-147c-4147-a0d1-e698df5cd4e3'
                            }}

        self.flavor3 = self.api.post_flavor(flavor_body)

    def _other_hostname(self, host):
        other_host = {'host1': 'host2',
                      'host2': 'host1'}
        return other_host[host]

    def _get_provider_uuid_by_host(self, host):
        # NOTE(gibi): the compute node id is the same as the compute node
        # provider uuid on that compute
        resp = self.admin_api.api_get(
            'os-hypervisors?hypervisor_hostname_pattern=%s' % host).body
        return resp['hypervisors'][0]['id']

    def _get_provider_usages(self, provider_uuid):
        return self.placement_api.get(
            '/resource_providers/%s/usages' % provider_uuid).body['usages']

    def _get_allocations_by_server_uuid(self, server_uuid):
        return self.placement_api.get(
            '/allocations/%s' % server_uuid).body['allocations']

    def _run_periodics(self):
        # NOTE(jaypipes): We always run periodics in the same order: first on
        # compute1, then on compute2. However, we want to test scenarios when
        # the periodics run at different times during mover operations. This is
        # why we have the "reverse" tests which simply switch the source and
        # dest host while keeping the order in which we run the
        # periodics. This effectively allows us to test the matrix of timing
        # scenarios during move operations.
        ctx = context.get_admin_context()
        LOG.info('Running periodic for compute1 (%s)',
            self.compute1.manager.host)
        self.compute1.manager.update_available_resource(ctx)
        LOG.info('Running periodic for compute2 (%s)',
            self.compute2.manager.host)
        self.compute2.manager.update_available_resource(ctx)
        LOG.info('Finished with periodics')

    def test_resize_revert(self):
        self._test_resize_revert(dest_hostname='host1')

    def test_resize_revert_reverse(self):
        self._test_resize_revert(dest_hostname='host2')

    def test_resize_confirm(self):
        self._test_resize_confirm(dest_hostname='host1')

    def test_resize_confirm_reverse(self):
        self._test_resize_confirm(dest_hostname='host2')

    def assertFlavorMatchesAllocation(self, flavor, allocation):
        self.assertEqual(flavor['vcpus'], allocation['VCPU'])
        self.assertEqual(flavor['ram'], allocation['MEMORY_MB'])
        self.assertEqual(flavor['disk'], allocation['DISK_GB'])

    def assertFlavorsMatchAllocation(self, old_flavor, new_flavor, allocation):
        self.assertEqual(old_flavor['vcpus'] + new_flavor['vcpus'],
                         allocation['VCPU'])
        self.assertEqual(old_flavor['ram'] + new_flavor['ram'],
                         allocation['MEMORY_MB'])
        self.assertEqual(old_flavor['disk'] + new_flavor['disk'],
                         allocation['DISK_GB'])

    def _boot_and_check_allocations(self, flavor, source_hostname):
        server_req = self._build_minimal_create_server_request(
            self.api, 'some-server', flavor_id=flavor['id'],
            image_uuid='155d900f-4e14-4e4c-a73d-069cbf4541e6',
            networks=[])
        server_req['availability_zone'] = 'nova:%s' % source_hostname
        LOG.info('booting on %s', source_hostname)
        created_server = self.api.post_server({'server': server_req})
        server = self._wait_for_state_change(
            self.admin_api, created_server, 'ACTIVE')

        # Verify that our source host is what the server ended up on
        self.assertEqual(source_hostname, server['OS-EXT-SRV-ATTR:host'])

        source_rp_uuid = self._get_provider_uuid_by_host(source_hostname)
        dest_rp_uuid = self._get_provider_uuid_by_host(
            self._other_hostname(source_hostname))

        # Before we run periodics, make sure that we have allocations/usages
        # only on the source host
        source_usages = self._get_provider_usages(source_rp_uuid)
        self.assertFlavorMatchesAllocation(flavor, source_usages)

        # Check that the other provider has no usage
        dest_usages = self._get_provider_usages(dest_rp_uuid)
        self.assertEqual({'VCPU': 0,
                          'MEMORY_MB': 0,
                          'DISK_GB': 0}, dest_usages)

        # Check that the server only allocates resource from the host it is
        # booted on
        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(1, len(allocations),
                         'No allocation for the server on the host it'
                         'is booted on')
        allocation = allocations[source_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(flavor, allocation)

        self._run_periodics()

        # After running the periodics but before we start any resize operation,
        # we should have exactly the same allocation/usage information as
        # before running the periodics

        # Check usages on the selected host after boot
        source_usages = self._get_provider_usages(source_rp_uuid)
        self.assertFlavorMatchesAllocation(flavor, source_usages)

        # Check that the other provider has no usage
        dest_usages = self._get_provider_usages(dest_rp_uuid)
        self.assertEqual({'VCPU': 0,
                          'MEMORY_MB': 0,
                          'DISK_GB': 0}, dest_usages)

        # Check that the server only allocates resource from the host it is
        # booted on
        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(1, len(allocations),
                         'No allocation for the server on the host it'
                         'is booted on')
        allocation = allocations[source_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(flavor, allocation)

        return server

    def _resize_and_check_allocations(self, server, old_flavor, new_flavor,
            source_rp_uuid, dest_rp_uuid):
        # Resize the server and check usages in VERIFY_RESIZE state
        self.flags(allow_resize_to_same_host=False)
        resize_req = {
            'resize': {
                'flavorRef': new_flavor['id']
            }
        }
        self.api.post_server_action(server['id'], resize_req)
        self._wait_for_state_change(self.api, server, 'VERIFY_RESIZE')

        # OK, so the resize operation has run, but we have not yet confirmed or
        # reverted the resize operation. Before we run periodics, make sure
        # that we have allocations/usages on BOTH the source and the
        # destination hosts (because during select_destinations(), the
        # scheduler should have created a "doubled-up" allocation referencing
        # both the source and destination hosts
        source_usages = self._get_provider_usages(source_rp_uuid)
        self.assertFlavorMatchesAllocation(old_flavor, source_usages)
        dest_usages = self._get_provider_usages(dest_rp_uuid)
        self.assertFlavorMatchesAllocation(new_flavor, dest_usages)

        # Check that the server allocates resource from both source and dest
        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(2, len(allocations),
                         'Expected scheduler to create doubled-up allocation')
        source_alloc = allocations[source_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(old_flavor, source_alloc)
        dest_alloc = allocations[dest_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(new_flavor, dest_alloc)

        self._run_periodics()

        # the original host expected to have the old resource usage
        source_usages = self._get_provider_usages(source_rp_uuid)
        self.assertFlavorMatchesAllocation(old_flavor, source_usages)

        # the dest host expected to have resource allocation based on
        # the new flavor the server is resized to
        dest_usages = self._get_provider_usages(dest_rp_uuid)
        self.assertFlavorMatchesAllocation(new_flavor, dest_usages)

        # and our server should have allocation on both providers accordingly
        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(2, len(allocations))
        source_allocation = allocations[source_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(old_flavor, source_allocation)

        dest_allocation = allocations[dest_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(new_flavor, dest_allocation)

    def _delete_and_check_allocations(self, server, source_rp_uuid,
                                      dest_rp_uuid):
        # Check usages after delete
        self.api.delete_server(server['id'])
        self._wait_until_deleted(server)

        source_usages = self._get_provider_usages(source_rp_uuid)
        self.assertEqual({'VCPU': 0,
                          'MEMORY_MB': 0,
                          'DISK_GB': 0}, source_usages)

        dest_usages = self._get_provider_usages(dest_rp_uuid)
        self.assertEqual({'VCPU': 0,
                          'MEMORY_MB': 0,
                          'DISK_GB': 0}, dest_usages)

        # and no allocations for the deleted server
        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(0, len(allocations))

    def _test_resize_revert(self, dest_hostname):
        source_hostname = self._other_hostname(dest_hostname)
        source_rp_uuid = self._get_provider_uuid_by_host(source_hostname)
        dest_rp_uuid = self._get_provider_uuid_by_host(dest_hostname)

        server = self._boot_and_check_allocations(self.flavor1,
            source_hostname)

        self._resize_and_check_allocations(server, self.flavor1, self.flavor2,
            source_rp_uuid, dest_rp_uuid)

        # Revert the resize and check the usages
        post = {'revertResize': None}
        self.api.post_server_action(server['id'], post)
        self._wait_for_state_change(self.api, server, 'ACTIVE')

        self._run_periodics()

        # the original host expected to have the old resource allocation
        source_usages = self._get_provider_usages(source_rp_uuid)
        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertFlavorMatchesAllocation(self.flavor1, source_usages)

        dest_usages = self._get_provider_usages(dest_rp_uuid)
        self.assertEqual({'VCPU': 0,
                          'MEMORY_MB': 0,
                          'DISK_GB': 0}, dest_usages,
                          'Target host %s still has usage after the resize '
                          'has been reverted' % dest_hostname)

        # Check that the server only allocates resource from the original host
        self.assertEqual(1, len(allocations))

        source_allocation = allocations[source_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor1, source_allocation)

        self._delete_and_check_allocations(
            server, source_rp_uuid, dest_rp_uuid)

    def _test_resize_confirm(self, dest_hostname):
        source_hostname = self._other_hostname(dest_hostname)
        source_rp_uuid = self._get_provider_uuid_by_host(source_hostname)
        dest_rp_uuid = self._get_provider_uuid_by_host(dest_hostname)

        server = self._boot_and_check_allocations(self.flavor1,
            source_hostname)

        self._resize_and_check_allocations(server, self.flavor1, self.flavor2,
            source_rp_uuid, dest_rp_uuid)

        # Confirm the resize and check the usages
        post = {'confirmResize': None}
        self.api.post_server_action(
            server['id'], post, check_response_status=[204])
        self._wait_for_state_change(self.api, server, 'ACTIVE')

        # After confirming, we should have an allocation only on the
        # destination host
        allocations = self._get_allocations_by_server_uuid(server['id'])

        # and the server allocates only from the target host
        self.assertEqual(1, len(allocations))

        source_usages = self._get_provider_usages(source_rp_uuid)
        dest_usages = self._get_provider_usages(dest_rp_uuid)

        # and the target host allocation should be according to the new flavor
        self.assertFlavorMatchesAllocation(self.flavor2, dest_usages)
        self.assertEqual({'VCPU': 0,
                          'MEMORY_MB': 0,
                          'DISK_GB': 0}, source_usages,
                         'The source host %s still has usages after the '
                         'resize has been confirmed' % source_hostname)

        # and the target host allocation should be according to the new flavor
        self.assertFlavorMatchesAllocation(self.flavor2, dest_usages)

        dest_allocation = allocations[dest_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor2, dest_allocation)

        self._run_periodics()

        # Check we're still accurate after running the periodics

        dest_usages = self._get_provider_usages(dest_rp_uuid)
        source_usages = self._get_provider_usages(source_rp_uuid)

        # and the target host allocation should be according to the new flavor
        self.assertFlavorMatchesAllocation(self.flavor2, dest_usages)
        self.assertEqual({'VCPU': 0,
                          'MEMORY_MB': 0,
                          'DISK_GB': 0}, source_usages,
                          'The source host %s still has usages after the '
                          'resize has been confirmed' % source_hostname)

        allocations = self._get_allocations_by_server_uuid(server['id'])

        # and the server allocates only from the target host
        self.assertEqual(1, len(allocations))

        dest_allocation = allocations[dest_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor2, dest_allocation)

        self._delete_and_check_allocations(
            server, source_rp_uuid, dest_rp_uuid)

    def _resize_to_same_host_and_check_allocations(self, server, old_flavor,
                                                   new_flavor, rp_uuid):
        # Resize the server to the same host and check usages in VERIFY_RESIZE
        # state
        self.flags(allow_resize_to_same_host=True)
        resize_req = {
            'resize': {
                'flavorRef': new_flavor['id']
            }
        }
        self.api.post_server_action(server['id'], resize_req)
        self._wait_for_state_change(self.api, server, 'VERIFY_RESIZE')

        usages = self._get_provider_usages(rp_uuid)
        self.assertFlavorsMatchAllocation(old_flavor, new_flavor, usages)

        allocations = self._get_allocations_by_server_uuid(server['id'])

        self.assertEqual(1, len(allocations))
        allocation = allocations[rp_uuid]['resources']
        self.assertFlavorsMatchAllocation(old_flavor, new_flavor, allocation)

        # We've resized to the same host and have doubled allocations for both
        # the old and new flavor on the same host. Run the periodic on the
        # compute to see if it tramples on what the scheduler did.
        self._run_periodics()

        usages = self._get_provider_usages(rp_uuid)

        self.assertFlavorsMatchAllocation(old_flavor, new_flavor, usages)

        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(1, len(allocations))
        allocation = allocations[rp_uuid]['resources']

        self.assertFlavorsMatchAllocation(old_flavor, new_flavor, allocation)

    def test_resize_revert_same_host(self):
        # make sure that the test only uses a single host
        compute2_service_id = self.admin_api.get_services(
            host=self.compute2.host, binary='nova-compute')[0]['id']
        self.admin_api.put_service(compute2_service_id, {'status': 'disabled'})

        hostname = self.compute1.manager.host
        rp_uuid = self._get_provider_uuid_by_host(hostname)

        server = self._boot_and_check_allocations(self.flavor2, hostname)

        self._resize_to_same_host_and_check_allocations(
            server, self.flavor2, self.flavor3, rp_uuid)

        # Revert the resize and check the usages
        post = {'revertResize': None}
        self.api.post_server_action(server['id'], post)
        self._wait_for_state_change(self.api, server, 'ACTIVE')

        self._run_periodics()

        # after revert only allocations due to the old flavor should remain
        usages = self._get_provider_usages(rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor2, usages)

        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(1, len(allocations))
        allocation = allocations[rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor2, allocation)

        self._delete_and_check_allocations(server, rp_uuid, rp_uuid)

    def test_resize_confirm_same_host(self):
        # make sure that the test only uses a single host
        compute2_service_id = self.admin_api.get_services(
            host=self.compute2.host, binary='nova-compute')[0]['id']
        self.admin_api.put_service(compute2_service_id, {'status': 'disabled'})

        hostname = self.compute1.manager.host
        rp_uuid = self._get_provider_uuid_by_host(hostname)

        server = self._boot_and_check_allocations(self.flavor2, hostname)

        self._resize_to_same_host_and_check_allocations(
            server, self.flavor2, self.flavor3, rp_uuid)

        # Confirm the resize and check the usages
        post = {'confirmResize': None}
        self.api.post_server_action(
            server['id'], post, check_response_status=[204])
        self._wait_for_state_change(self.api, server, 'ACTIVE')

        self._run_periodics()

        # after confirm only allocations due to the new flavor should remain
        usages = self._get_provider_usages(rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor3, usages)

        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(1, len(allocations))
        allocation = allocations[rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor3, allocation)

        self._delete_and_check_allocations(server, rp_uuid, rp_uuid)

    def test_resize_not_enough_resource(self):
        # Try to resize to a flavor that requests more VCPU than what the
        # compute hosts has available and expect the resize to fail

        flavor_body = {'flavor':
                           {'name': 'test_too_big_flavor',
                            'ram': 1024,
                            'vcpus': fake.MediumFakeDriver.vcpus + 1,
                            'disk': 20,
                            }}

        big_flavor = self.api.post_flavor(flavor_body)

        dest_hostname = self.compute2.host
        source_hostname = self._other_hostname(dest_hostname)
        source_rp_uuid = self._get_provider_uuid_by_host(source_hostname)
        dest_rp_uuid = self._get_provider_uuid_by_host(dest_hostname)

        server = self._boot_and_check_allocations(
            self.flavor1, source_hostname)

        self.flags(allow_resize_to_same_host=False)
        resize_req = {
            'resize': {
                'flavorRef': big_flavor['id']
            }
        }

        resp = self.api.post_server_action(
            server['id'], resize_req, check_response_status=[400])
        self.assertEqual(
            resp['badRequest']['message'],
            "No valid host was found. No valid host found for resize")
        server = self.admin_api.get_server(server['id'])
        self.assertEqual(source_hostname, server['OS-EXT-SRV-ATTR:host'])

        # only the source host shall have usages after the failed resize
        source_usages = self._get_provider_usages(source_rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor1, source_usages)

        # Check that the other provider has no usage
        dest_usages = self._get_provider_usages(dest_rp_uuid)
        self.assertEqual({'VCPU': 0,
                          'MEMORY_MB': 0,
                          'DISK_GB': 0}, dest_usages)

        # Check that the server only allocates resource from the host it is
        # booted on
        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(1, len(allocations))
        allocation = allocations[source_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor1, allocation)

        self._delete_and_check_allocations(
            server, source_rp_uuid, dest_rp_uuid)

    def test_evacuate(self):
        source_hostname = self.compute1.host
        dest_hostname = self.compute2.host

        server = self._boot_and_check_allocations(
            self.flavor1, source_hostname)

        source_compute_id = self.admin_api.get_services(
            host=source_hostname, binary='nova-compute')[0]['id']

        self.compute1.stop()
        # force it down to avoid waiting for the service group to time out
        self.admin_api.put_service(
            source_compute_id, {'forced_down': 'true'})

        # evacuate the server
        post = {'evacuate': {}}
        self.api.post_server_action(
            server['id'], post)
        expected_params = {'OS-EXT-SRV-ATTR:host': dest_hostname,
                           'status': 'ACTIVE'}
        server = self._wait_for_server_parameter(self.api, server,
                                                 expected_params)

        # Expect to have allocation and usages on both computes as the
        # source compute is still down
        source_rp_uuid = self._get_provider_uuid_by_host(source_hostname)
        dest_rp_uuid = self._get_provider_uuid_by_host(dest_hostname)

        source_usages = self._get_provider_usages(source_rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor1, source_usages)

        dest_usages = self._get_provider_usages(dest_rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor1, dest_usages)

        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(2, len(allocations))
        source_allocation = allocations[source_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor1, source_allocation)
        dest_allocation = allocations[dest_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor1, dest_allocation)

        # start up the source compute
        self.compute1.start()
        self.admin_api.put_service(
            source_compute_id, {'forced_down': 'false'})

        source_usages = self._get_provider_usages(source_rp_uuid)
        self.assertEqual({'VCPU': 0,
                          'MEMORY_MB': 0,
                          'DISK_GB': 0},
                         source_usages)

        dest_usages = self._get_provider_usages(dest_rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor1, dest_usages)

        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(1, len(allocations))
        dest_allocation = allocations[dest_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor1, dest_allocation)

        self._delete_and_check_allocations(
            server, source_rp_uuid, dest_rp_uuid)

    def _boot_then_shelve_and_check_allocations(self, hostname, rp_uuid):
        # avoid automatic shelve offloading
        self.flags(shelved_offload_time=-1)
        server = self._boot_and_check_allocations(
            self.flavor1, hostname)
        req = {
            'shelve': {}
        }
        self.api.post_server_action(server['id'], req)
        self._wait_for_state_change(self.api, server, 'SHELVED')
        # the host should maintain the existing allocation for this instance
        # while the instance is shelved
        source_usages = self._get_provider_usages(rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor1, source_usages)
        # Check that the server only allocates resource from the host it is
        # booted on
        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(1, len(allocations))
        allocation = allocations[rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor1, allocation)
        return server

    def test_shelve_unshelve(self):
        source_hostname = self.compute1.host
        source_rp_uuid = self._get_provider_uuid_by_host(source_hostname)
        server = self._boot_then_shelve_and_check_allocations(
            source_hostname, source_rp_uuid)

        req = {
            'unshelve': {}
        }
        self.api.post_server_action(server['id'], req)
        self._wait_for_state_change(self.api, server, 'ACTIVE')

        # the host should have resource usage as the instance is ACTIVE
        source_usages = self._get_provider_usages(source_rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor1, source_usages)

        # Check that the server only allocates resource from the host it is
        # booted on
        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(1, len(allocations))
        allocation = allocations[source_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor1, allocation)

        self._delete_and_check_allocations(
            server, source_rp_uuid, source_rp_uuid)

    def _shelve_offload_and_check_allocations(self, server, source_rp_uuid):
        req = {
            'shelveOffload': {}
        }
        self.api.post_server_action(server['id'], req)
        self._wait_for_state_change(self.api, server, 'SHELVED_OFFLOADED')
        source_usages = self._get_provider_usages(source_rp_uuid)
        self.assertEqual({'VCPU': 0,
                          'MEMORY_MB': 0,
                          'DISK_GB': 0},
                         source_usages)

        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(0, len(allocations))

    def test_shelve_offload_unshelve_diff_host(self):
        source_hostname = self.compute1.host
        source_rp_uuid = self._get_provider_uuid_by_host(source_hostname)
        server = self._boot_then_shelve_and_check_allocations(
            source_hostname, source_rp_uuid)

        self._shelve_offload_and_check_allocations(server, source_rp_uuid)

        # unshelve after shelve offload will do scheduling. this test case
        # wants to test the scenario when the scheduler select a different host
        # to ushelve the instance. So we disable the original host.
        source_service_id = self.admin_api.get_services(
            host=source_hostname, binary='nova-compute')[0]['id']
        self.admin_api.put_service(source_service_id, {'status': 'disabled'})

        req = {
            'unshelve': {}
        }
        self.api.post_server_action(server['id'], req)
        server = self._wait_for_state_change(self.api, server, 'ACTIVE')
        # unshelving an offloaded instance will call the scheduler so the
        # instance might end up on a different host
        current_hostname = server['OS-EXT-SRV-ATTR:host']
        self.assertEqual(current_hostname, self._other_hostname(
            source_hostname))

        # the host running the instance should have resource usage
        current_rp_uuid = self._get_provider_uuid_by_host(current_hostname)
        current_usages = self._get_provider_usages(current_rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor1, current_usages)

        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(1, len(allocations))
        allocation = allocations[current_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor1, allocation)

        self._delete_and_check_allocations(
            server, source_rp_uuid, source_rp_uuid)

    def test_shelve_offload_unshelve_same_host(self):
        source_hostname = self.compute1.host
        source_rp_uuid = self._get_provider_uuid_by_host(source_hostname)
        server = self._boot_then_shelve_and_check_allocations(
            source_hostname, source_rp_uuid)

        self._shelve_offload_and_check_allocations(server, source_rp_uuid)

        # unshelve after shelve offload will do scheduling. this test case
        # wants to test the scenario when the scheduler select the same host
        # to ushelve the instance. So we disable the other host.
        source_service_id = self.admin_api.get_services(
            host=self._other_hostname(source_hostname),
            binary='nova-compute')[0]['id']
        self.admin_api.put_service(source_service_id, {'status': 'disabled'})

        req = {
            'unshelve': {}
        }
        self.api.post_server_action(server['id'], req)
        server = self._wait_for_state_change(self.api, server, 'ACTIVE')
        # unshelving an offloaded instance will call the scheduler so the
        # instance might end up on a different host
        current_hostname = server['OS-EXT-SRV-ATTR:host']
        self.assertEqual(current_hostname, source_hostname)

        # the host running the instance should have resource usage
        current_rp_uuid = self._get_provider_uuid_by_host(current_hostname)
        current_usages = self._get_provider_usages(current_rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor1, current_usages)

        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(1, len(allocations))
        allocation = allocations[current_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor1, allocation)

        self._delete_and_check_allocations(
            server, source_rp_uuid, source_rp_uuid)

    def test_live_migrate_force(self):
        source_hostname = self.compute1.host
        dest_hostname = self.compute2.host
        source_rp_uuid = self._get_provider_uuid_by_host(source_hostname)
        dest_rp_uuid = self._get_provider_uuid_by_host(dest_hostname)

        server = self._boot_and_check_allocations(
            self.flavor1, source_hostname)
        post = {
            'os-migrateLive': {
                'host': dest_hostname,
                'block_migration': True,
                'force': True,
            }
        }

        self.api.post_server_action(server['id'], post)
        self._wait_for_server_parameter(self.api, server,
            {'OS-EXT-SRV-ATTR:host': dest_hostname,
             'status': 'ACTIVE'})

        self._run_periodics()

        source_usages = self._get_provider_usages(source_rp_uuid)
        # NOTE(danms): There should be no usage for the source
        self.assertFlavorMatchesAllocation(
            {'ram': 0, 'disk': 0, 'vcpus': 0}, source_usages)

        dest_usages = self._get_provider_usages(dest_rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor1, dest_usages)

        allocations = self._get_allocations_by_server_uuid(server['id'])
        # the server has an allocation on only the dest node
        self.assertEqual(1, len(allocations))
        self.assertNotIn(source_rp_uuid, allocations)
        dest_allocation = allocations[dest_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor1, dest_allocation)

        self._delete_and_check_allocations(
            server, source_rp_uuid, dest_rp_uuid)

    def test_live_migrate(self):
        source_hostname = self.compute1.host
        dest_hostname = self.compute2.host
        source_rp_uuid = self._get_provider_uuid_by_host(source_hostname)
        dest_rp_uuid = self._get_provider_uuid_by_host(dest_hostname)

        server = self._boot_and_check_allocations(
            self.flavor1, source_hostname)
        post = {
            'os-migrateLive': {
                'host': dest_hostname,
                'block_migration': True,
            }
        }

        self.api.post_server_action(server['id'], post)
        self._wait_for_server_parameter(self.api, server,
                                        {'OS-EXT-SRV-ATTR:host': dest_hostname,
                                         'status': 'ACTIVE'})

        self._run_periodics()

        source_usages = self._get_provider_usages(source_rp_uuid)
        # NOTE(danms): There should be no usage for the source
        self.assertFlavorMatchesAllocation(
            {'ram': 0, 'disk': 0, 'vcpus': 0}, source_usages)

        dest_usages = self._get_provider_usages(dest_rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor1, dest_usages)

        allocations = self._get_allocations_by_server_uuid(server['id'])
        self.assertEqual(1, len(allocations))
        self.assertNotIn(source_rp_uuid, allocations)
        dest_allocation = allocations[dest_rp_uuid]['resources']
        self.assertFlavorMatchesAllocation(self.flavor1, dest_allocation)

        self._delete_and_check_allocations(
            server, source_rp_uuid, dest_rp_uuid)

    def test_rescheduling_when_booting_instance(self):
        self.failed_hostname = None
        old_build_resources = (compute_manager.ComputeManager.
            _build_resources)

        def fake_build_resources(sl, *args, **kwargs):
            # We failed on the first scheduling
            if not self.failed_hostname:
                self.failed_hostname = sl.host
                raise Exception()

            return old_build_resources(sl, *args, **kwargs)

        self.stub_out('nova.compute.manager.ComputeManager._build_resources',
                      fake_build_resources)

        server_req = self._build_minimal_create_server_request(
                self.api, 'some-server', flavor_id=self.flavor1['id'],
                image_uuid='155d900f-4e14-4e4c-a73d-069cbf4541e6',
                networks=[])

        created_server = self.api.post_server({'server': server_req})
        server = self._wait_for_state_change(
                self.admin_api, created_server, 'ACTIVE')
        dest_hostname = server['OS-EXT-SRV-ATTR:host']

        LOG.info('failed on %s', self.failed_hostname)
        LOG.info('booting on %s', dest_hostname)

        failed_rp_uuid = self._get_provider_uuid_by_host(self.failed_hostname)
        dest_rp_uuid = self._get_provider_uuid_by_host(dest_hostname)

        failed_usages = self._get_provider_usages(failed_rp_uuid)
        # Expects no allocation records on the failed host.
        self.assertFlavorMatchesAllocation(
           {'vcpus': 0, 'ram': 0, 'disk': 0}, failed_usages)

        # Ensure the allocation records on the destination host.
        dest_usages = self._get_provider_usages(dest_rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor1, dest_usages)

    def test_abort_when_booting_instance(self):
        self.failed_hostname = None
        old_build_resources = (compute_manager.ComputeManager.
            _build_resources)

        def fake_build_resources(sl, *args, **kwargs):
            # We failed on the first scheduling
            if not self.failed_hostname:
                self.failed_hostname = sl.host
                raise exception.BuildAbortException(instance_uuid='fake_uuid',
                    reason='just abort')

            return old_build_resources(sl, *args, **kwargs)

        self.stub_out('nova.compute.manager.ComputeManager._build_resources',
                      fake_build_resources)

        server_req = self._build_minimal_create_server_request(
                self.api, 'some-server', flavor_id=self.flavor1['id'],
                image_uuid='155d900f-4e14-4e4c-a73d-069cbf4541e6',
                networks=[])

        created_server = self.api.post_server({'server': server_req})
        self._wait_for_state_change(self.admin_api, created_server, 'ERROR')

        LOG.info('failed on %s', self.failed_hostname)

        failed_rp_uuid = self._get_provider_uuid_by_host(self.failed_hostname)
        failed_usages = self._get_provider_usages(failed_rp_uuid)
        # Expects no allocation records on the failed host.
        self.assertFlavorMatchesAllocation(
           {'vcpus': 0, 'ram': 0, 'disk': 0}, failed_usages)

    def _wait_for_prep_resize_fail_completion(self, server, expected_action):
        """Polls instance action events for the given instance and action
        until it finds the compute_prep_resize action event with an error
        result.
        """
        completion_event = None
        for attempt in range(10):
            actions = self.api.get_instance_actions(server['id'])
            # Look for the migrate action.
            for action in actions:
                if action['action'] == expected_action:
                    events = (
                        self.api.api_get(
                            '/servers/%s/os-instance-actions/%s' %
                            (server['id'], action['request_id'])
                        ).body['instanceAction']['events'])
                    # Look for the compute_prep_resize being in error state.
                    for event in events:
                        if (event['event'] == 'compute_prep_resize' and
                                event['result'] is not None and
                                event['result'].lower() == 'error'):
                            completion_event = event
                            # Break out of the events loop.
                            break
                    if completion_event:
                        # Break out of the actions loop.
                        break
            # We didn't find the completion event yet, so wait a bit.
            time.sleep(0.5)

        if completion_event is None:
            self.fail('Timed out waiting for compute_prep_resize failure '
                      'event. Current instance actions: %s' % actions)

    def test_rescheduling_when_migrating_instance(self):
        """Tests that allocations are removed from the destination node by
        the compute service when a cold migrate / resize fails and a reschedule
        request is sent back to conductor.
        """
        source_hostname = self.compute1.manager.host
        server = self._boot_and_check_allocations(
            self.flavor1, source_hostname)

        def fake_prep_resize(*args, **kwargs):
            raise test.TestingException('Simulated _prep_resize failure.')

        # Yes this isn't great in a functional test, but it's simple.
        self.stub_out('nova.compute.manager.ComputeManager._prep_resize',
                      fake_prep_resize)

        # Now migrate the server which is going to fail on the destination.
        self.api.post_server_action(server['id'], {'migrate': None})

        self._wait_for_prep_resize_fail_completion(server, 'migrate')

        dest_hostname = self._other_hostname(source_hostname)
        dest_rp_uuid = self._get_provider_uuid_by_host(dest_hostname)

        failed_usages = self._get_provider_usages(dest_rp_uuid)
        # Expects no allocation records on the failed host.
        self.assertFlavorMatchesAllocation(
           {'vcpus': 0, 'ram': 0, 'disk': 0}, failed_usages)

        # Ensure the allocation records still exist on the source host.
        source_rp_uuid = self._get_provider_uuid_by_host(source_hostname)
        source_usages = self._get_provider_usages(source_rp_uuid)
        self.assertFlavorMatchesAllocation(self.flavor1, source_usages)

    def test_resize_to_same_host_prep_resize_fails(self):
        """Tests that when we resize to the same host and resize fails in
        the prep_resize method, we cleanup the allocations before rescheduling.
        """
        # make sure that the test only uses a single host
        compute2_service_id = self.admin_api.get_services(
            host=self.compute2.host, binary='nova-compute')[0]['id']
        self.admin_api.put_service(compute2_service_id, {'status': 'disabled'})

        hostname = self.compute1.manager.host
        rp_uuid = self._get_provider_uuid_by_host(hostname)

        server = self._boot_and_check_allocations(self.flavor1, hostname)

        def fake_prep_resize(*args, **kwargs):
            # Ensure the allocations are doubled now before we fail.
            usages = self._get_provider_usages(rp_uuid)
            self.assertFlavorsMatchAllocation(
                self.flavor1, self.flavor2, usages)
            raise test.TestingException('Simulated _prep_resize failure.')

        # Yes this isn't great in a functional test, but it's simple.
        self.stub_out('nova.compute.manager.ComputeManager._prep_resize',
                      fake_prep_resize)

        self.flags(allow_resize_to_same_host=True)
        resize_req = {
            'resize': {
                'flavorRef': self.flavor2['id']
            }
        }
        self.api.post_server_action(server['id'], resize_req)

        self._wait_for_prep_resize_fail_completion(server, 'resize')

        # Ensure the allocation records still exist on the host.
        source_rp_uuid = self._get_provider_uuid_by_host(hostname)
        source_usages = self._get_provider_usages(source_rp_uuid)
        # The new_flavor should have been subtracted from the doubled
        # allocation which just leaves us with the original flavor.
        self.assertFlavorMatchesAllocation(self.flavor1, source_usages)
