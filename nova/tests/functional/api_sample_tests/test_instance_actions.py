# Copyright 2012 Nebula, Inc.
# Copyright 2013 IBM Corp.
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

import copy

import six

from nova.tests.functional.api_sample_tests import api_sample_base
from nova.tests.unit import fake_instance
from nova.tests.unit import fake_server_actions
from nova.tests.unit import utils as test_utils


class ServerActionsSampleJsonTest(api_sample_base.ApiSampleTestBaseV21):
    microversion = None
    ADMIN_API = True
    sample_dir = 'os-instance-actions'

    def setUp(self):
        super(ServerActionsSampleJsonTest, self).setUp()
        self.api.microversion = self.microversion
        self.actions = fake_server_actions.FAKE_ACTIONS
        self.events = fake_server_actions.FAKE_EVENTS
        self.instance = test_utils.get_test_instance(obj=True)

        def _fake_get(stub_self, context, instance_uuid, expected_attrs=None):
            return fake_instance.fake_instance_obj(
                None, **{'uuid': instance_uuid})

        def fake_instance_action_get_by_request_id(context, uuid, request_id):
            return copy.deepcopy(self.actions[uuid][request_id])

        def fake_server_actions_get(context, uuid):
            return [copy.deepcopy(value) for value in
                    six.itervalues(self.actions[uuid])]

        def fake_instance_action_events_get(context, action_id):
            return copy.deepcopy(self.events[action_id])

        def fake_instance_get_by_uuid(context, instance_id):
            return self.instance

        self.stub_out('nova.db.action_get_by_request_id',
                      fake_instance_action_get_by_request_id)
        self.stub_out('nova.db.actions_get', fake_server_actions_get)
        self.stub_out('nova.db.action_events_get',
                      fake_instance_action_events_get)
        self.stub_out('nova.db.instance_get_by_uuid',
                      fake_instance_get_by_uuid)
        self.stub_out('nova.compute.api.API.get', _fake_get)

    def test_instance_action_get(self):
        fake_uuid = fake_server_actions.FAKE_UUID
        fake_request_id = fake_server_actions.FAKE_REQUEST_ID1
        fake_action = self.actions[fake_uuid][fake_request_id]

        response = self._do_get('servers/%s/os-instance-actions/%s' %
                                (fake_uuid, fake_request_id))
        subs = {}
        subs['action'] = '(reboot)|(resize)'
        subs['instance_uuid'] = str(fake_uuid)
        subs['integer_id'] = '[0-9]+'
        subs['request_id'] = str(fake_action['request_id'])
        subs['start_time'] = str(fake_action['start_time'])
        subs['result'] = '(Success)|(Error)'
        subs['event'] = '(schedule)|(compute_create)'
        # Non-admins can see event details except for the "traceback" field
        # starting in the 2.51 microversion.
        if self.ADMIN_API:
            name = 'instance-action-get-resp'
        else:
            name = 'instance-action-get-non-admin-resp'
        self._verify_response(name, subs, response, 200)

    def test_instance_actions_list(self):
        fake_uuid = fake_server_actions.FAKE_UUID
        response = self._do_get('servers/%s/os-instance-actions' % (fake_uuid))
        subs = {}
        subs['action'] = '(reboot)|(resize)'
        subs['integer_id'] = '[0-9]+'
        subs['request_id'] = ('req-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}'
                              '-[0-9a-f]{4}-[0-9a-f]{12}')
        self._verify_response('instance-actions-list-resp', subs,
                              response, 200)


class ServerActionsV221SampleJsonTest(ServerActionsSampleJsonTest):
    microversion = '2.21'
    scenarios = [('v2_21', {'api_major_version': 'v2.1'})]


class ServerActionsV251AdminSampleJsonTest(ServerActionsSampleJsonTest):
    """Tests the 2.51 microversion for the os-instance-actions API.

    The 2.51 microversion allows non-admins to see instance action event
    details *except* for the traceback field.

    The tests in this class are run as an admin user so all fields will be
    displayed.
    """
    microversion = '2.51'
    scenarios = [('v2_51', {'api_major_version': 'v2.1'})]


class ServerActionsV251NonAdminSampleJsonTest(ServerActionsSampleJsonTest):
    """Tests the 2.51 microversion for the os-instance-actions API.

    The 2.51 microversion allows non-admins to see instance action event
    details *except* for the traceback field.

    The tests in this class are run as a non-admin user so all fields except
    for the ``traceback`` field will be displayed.
    """
    ADMIN_API = False
    microversion = '2.51'
    scenarios = [('v2_51', {'api_major_version': 'v2.1'})]
