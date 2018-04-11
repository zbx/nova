# Copyright (c) 2012 OpenStack Foundation
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

import copy

import fixtures
import mock
from oslo_serialization import jsonutils
import testtools

from nova.api.openstack.compute import services
from nova.cells import utils as cells_utils
from nova import compute
from nova.compute import api as compute_api
from nova import context
from nova import exception
from nova import objects
from nova import test
from nova.tests import fixtures as nova_fixtures
from nova.tests.unit.api.openstack import fakes
from nova.tests.unit import fake_notifier
from nova.tests.unit.objects import test_objects
from nova.tests.unit.objects import test_service
from nova.tests import uuidsentinel as uuids


class ComputeHostAPITestCase(test.TestCase):
    def setUp(self):
        super(ComputeHostAPITestCase, self).setUp()
        self.host_api = compute.HostAPI()
        self.aggregate_api = compute_api.AggregateAPI()
        self.ctxt = context.get_admin_context()
        fake_notifier.stub_notifier(self)
        self.addCleanup(fake_notifier.reset)
        self.req = fakes.HTTPRequest.blank('')
        self.controller = services.ServiceController()
        self.useFixture(nova_fixtures.SingleCellSimple())

    def _compare_obj(self, obj, db_obj):
        test_objects.compare_obj(self, obj, db_obj,
                                 allow_missing=test_service.OPTIONAL)

    def _compare_objs(self, obj_list, db_obj_list):
        self.assertEqual(len(obj_list), len(db_obj_list),
                         "The length of two object lists are different.")
        for index, obj in enumerate(obj_list):
            self._compare_obj(obj, db_obj_list[index])

    def test_set_host_enabled(self):
        fake_notifier.NOTIFICATIONS = []

        @mock.patch.object(self.host_api.rpcapi, 'set_host_enabled',
                           return_value='fake-result')
        @mock.patch.object(self.host_api, '_assert_host_exists',
                           return_value='fake_host')
        def _do_test(mock_assert_host_exists, mock_set_host_enabled):
            result = self.host_api.set_host_enabled(self.ctxt, 'fake_host',
                                                    'fake_enabled')
            self.assertEqual('fake-result', result)
            self.assertEqual(2, len(fake_notifier.NOTIFICATIONS))
            msg = fake_notifier.NOTIFICATIONS[0]
            self.assertEqual('HostAPI.set_enabled.start', msg.event_type)
            self.assertEqual('api.fake_host', msg.publisher_id)
            self.assertEqual('INFO', msg.priority)
            self.assertEqual('fake_enabled', msg.payload['enabled'])
            self.assertEqual('fake_host', msg.payload['host_name'])
            msg = fake_notifier.NOTIFICATIONS[1]
            self.assertEqual('HostAPI.set_enabled.end', msg.event_type)
            self.assertEqual('api.fake_host', msg.publisher_id)
            self.assertEqual('INFO', msg.priority)
            self.assertEqual('fake_enabled', msg.payload['enabled'])
            self.assertEqual('fake_host', msg.payload['host_name'])

        _do_test()

    def test_host_name_from_assert_hosts_exists(self):
        @mock.patch.object(self.host_api.rpcapi, 'set_host_enabled',
                           return_value='fake-result')
        @mock.patch.object(self.host_api, '_assert_host_exists',
                           return_value='fake_host')
        def _do_test(mock_assert_host_exists, mock_set_host_enabled):
            result = self.host_api.set_host_enabled(self.ctxt, 'fake_host',
                                                    'fake_enabled')
            self.assertEqual('fake-result', result)

        _do_test()

    def test_get_host_uptime(self):
        @mock.patch.object(self.host_api.rpcapi, 'get_host_uptime',
                           return_value='fake-result')
        @mock.patch.object(self.host_api, '_assert_host_exists',
                           return_value='fake_host')
        def _do_test(mock_assert_host_exists, mock_get_host_uptime):
            result = self.host_api.get_host_uptime(self.ctxt, 'fake_host')
            self.assertEqual('fake-result', result)

        _do_test()

    def test_get_host_uptime_service_down(self):
        @mock.patch.object(self.host_api.db, 'service_get_by_compute_host',
                           return_value=dict(test_service.fake_service, id=1))
        @mock.patch.object(self.host_api.servicegroup_api, 'service_is_up',
                           return_value=False)
        def _do_test(mock_service_is_up, mock_service_get_by_compute_host):
            self.assertRaises(exception.ComputeServiceUnavailable,
                              self.host_api.get_host_uptime, self.ctxt,
                              'fake_host')

        _do_test()

    def test_host_power_action(self):
        fake_notifier.NOTIFICATIONS = []

        @mock.patch.object(self.host_api.rpcapi, 'host_power_action',
                           return_value='fake-result')
        @mock.patch.object(self.host_api, '_assert_host_exists',
                           return_value='fake_host')
        def _do_test(mock_assert_host_exists, mock_host_power_action):
            result = self.host_api.host_power_action(self.ctxt, 'fake_host',
                                                     'fake_action')
            self.assertEqual('fake-result', result)
            self.assertEqual(2, len(fake_notifier.NOTIFICATIONS))
            msg = fake_notifier.NOTIFICATIONS[0]
            self.assertEqual('HostAPI.power_action.start', msg.event_type)
            self.assertEqual('api.fake_host', msg.publisher_id)
            self.assertEqual('INFO', msg.priority)
            self.assertEqual('fake_action', msg.payload['action'])
            self.assertEqual('fake_host', msg.payload['host_name'])
            msg = fake_notifier.NOTIFICATIONS[1]
            self.assertEqual('HostAPI.power_action.end', msg.event_type)
            self.assertEqual('api.fake_host', msg.publisher_id)
            self.assertEqual('INFO', msg.priority)
            self.assertEqual('fake_action', msg.payload['action'])
            self.assertEqual('fake_host', msg.payload['host_name'])

        _do_test()

    def test_set_host_maintenance(self):
        fake_notifier.NOTIFICATIONS = []

        @mock.patch.object(self.host_api.rpcapi, 'host_maintenance_mode',
                           return_value='fake-result')
        @mock.patch.object(self.host_api, '_assert_host_exists',
                           return_value='fake_host')
        def _do_test(mock_assert_host_exists, mock_host_maintenance_mode):
            result = self.host_api.set_host_maintenance(self.ctxt, 'fake_host',
                                                        'fake_mode')
            self.assertEqual('fake-result', result)
            self.assertEqual(2, len(fake_notifier.NOTIFICATIONS))
            msg = fake_notifier.NOTIFICATIONS[0]
            self.assertEqual('HostAPI.set_maintenance.start', msg.event_type)
            self.assertEqual('api.fake_host', msg.publisher_id)
            self.assertEqual('INFO', msg.priority)
            self.assertEqual('fake_host', msg.payload['host_name'])
            self.assertEqual('fake_mode', msg.payload['mode'])
            msg = fake_notifier.NOTIFICATIONS[1]
            self.assertEqual('HostAPI.set_maintenance.end', msg.event_type)
            self.assertEqual('api.fake_host', msg.publisher_id)
            self.assertEqual('INFO', msg.priority)
            self.assertEqual('fake_host', msg.payload['host_name'])
            self.assertEqual('fake_mode', msg.payload['mode'])

        _do_test()

    def test_service_get_all_cells(self):
        cells = objects.CellMappingList.get_all(self.ctxt)
        for cell in cells:
            with context.target_cell(self.ctxt, cell) as cctxt:
                objects.Service(context=cctxt,
                                binary='nova-compute',
                                host='host-%s' % cell.uuid).create()
        services = self.host_api.service_get_all(self.ctxt, all_cells=True)
        self.assertEqual(sorted(['host-%s' % cell.uuid for cell in cells]),
                         sorted([svc.host for svc in services]))

    def test_service_get_all_no_zones(self):
        services = [dict(test_service.fake_service,
                         id=1, topic='compute', host='host1'),
                    dict(test_service.fake_service,
                         topic='compute', host='host2')]

        @mock.patch.object(self.host_api.db, 'service_get_all')
        def _do_test(mock_service_get_all):
            mock_service_get_all.return_value = services
            # Test no filters
            result = self.host_api.service_get_all(self.ctxt)
            mock_service_get_all.assert_called_once_with(self.ctxt,
                                                         disabled=None)
            self._compare_objs(result, services)

            # Test no filters #2
            mock_service_get_all.reset_mock()
            result = self.host_api.service_get_all(self.ctxt, filters={})
            mock_service_get_all.assert_called_once_with(self.ctxt,
                                                         disabled=None)
            self._compare_objs(result, services)

            # Test w/ filter
            mock_service_get_all.reset_mock()
            result = self.host_api.service_get_all(self.ctxt,
                                                   filters=dict(host='host2'))
            mock_service_get_all.assert_called_once_with(self.ctxt,
                                                         disabled=None)
            self._compare_objs(result, [services[1]])

        _do_test()

    def test_service_get_all(self):
        services = [dict(test_service.fake_service,
                         topic='compute', host='host1'),
                    dict(test_service.fake_service,
                         topic='compute', host='host2')]
        exp_services = []
        for service in services:
            exp_service = {}
            exp_service.update(availability_zone='nova', **service)
            exp_services.append(exp_service)

        @mock.patch.object(self.host_api.db, 'service_get_all')
        def _do_test(mock_service_get_all):
            mock_service_get_all.return_value = services

            # Test no filters
            result = self.host_api.service_get_all(self.ctxt, set_zones=True)
            mock_service_get_all.assert_called_once_with(self.ctxt,
                                                         disabled=None)
            self._compare_objs(result, exp_services)

            # Test no filters #2
            mock_service_get_all.reset_mock()
            result = self.host_api.service_get_all(self.ctxt, filters={},
                                                   set_zones=True)
            mock_service_get_all.assert_called_once_with(self.ctxt,
                                                         disabled=None)
            self._compare_objs(result, exp_services)

            # Test w/ filter
            mock_service_get_all.reset_mock()
            result = self.host_api.service_get_all(self.ctxt,
                                                   filters=dict(host='host2'),
                                                   set_zones=True)
            mock_service_get_all.assert_called_once_with(self.ctxt,
                                                         disabled=None)
            self._compare_objs(result, [exp_services[1]])

            # Test w/ zone filter but no set_zones arg.
            mock_service_get_all.reset_mock()
            filters = {'availability_zone': 'nova'}
            result = self.host_api.service_get_all(self.ctxt,
                                                   filters=filters)
            mock_service_get_all.assert_called_once_with(self.ctxt,
                                                         disabled=None)
            self._compare_objs(result, exp_services)

        _do_test()

    def test_service_get_by_compute_host(self):
        @mock.patch.object(self.host_api.db, 'service_get_by_compute_host',
                           return_value=test_service.fake_service)
        def _do_test(mock_service_get_by_compute_host):
            result = self.host_api.service_get_by_compute_host(self.ctxt,
                                                               'fake-host')
            self.assertEqual(test_service.fake_service['id'], result.id)

        _do_test()

    def test_service_update(self):
        host_name = 'fake-host'
        binary = 'nova-compute'
        params_to_update = dict(disabled=True)
        service_id = 42
        expected_result = dict(test_service.fake_service, id=service_id)

        @mock.patch.object(self.host_api.db, 'service_get_by_host_and_binary')
        @mock.patch.object(self.host_api.db, 'service_update')
        def _do_test(mock_service_update, mock_service_get_by_host_and_binary):
            mock_service_get_by_host_and_binary.return_value = expected_result
            mock_service_update.return_value = expected_result

            result = self.host_api.service_update(
                self.ctxt, host_name, binary, params_to_update)
            self._compare_obj(result, expected_result)

        _do_test()

    @mock.patch.object(objects.InstanceList, 'get_by_host',
                       return_value = ['fake-responses'])
    def test_instance_get_all_by_host(self, mock_get):
        result = self.host_api.instance_get_all_by_host(self.ctxt,
                                                        'fake-host')
        self.assertEqual(['fake-responses'], result)

    def test_task_log_get_all(self):
        @mock.patch.object(self.host_api.db, 'task_log_get_all',
                           return_value='fake-response')
        def _do_test(mock_task_log_get_all):
            result = self.host_api.task_log_get_all(self.ctxt, 'fake-name',
                                                    'fake-begin', 'fake-end',
                                                    host='fake-host',
                                                    state='fake-state')
            self.assertEqual('fake-response', result)

        _do_test()

    @mock.patch.object(objects.CellMappingList, 'get_all',
                       return_value=objects.CellMappingList(objects=[
                           objects.CellMapping(
                               uuid=uuids.cell1_uuid,
                               transport_url='mq://fake1',
                               database_connection='db://fake1'),
                           objects.CellMapping(
                               uuid=uuids.cell2_uuid,
                               transport_url='mq://fake2',
                               database_connection='db://fake2'),
                           objects.CellMapping(
                               uuid=uuids.cell3_uuid,
                               transport_url='mq://fake3',
                               database_connection='db://fake3')]))
    @mock.patch.object(objects.Service, 'get_by_uuid',
                       side_effect=[
                           exception.ServiceNotFound(
                               service_id=uuids.service_uuid),
                           objects.Service(uuid=uuids.service_uuid)])
    def test_service_get_by_id_using_uuid(self, service_get_by_uuid,
                                          cell_mappings_get_all):
        """Tests that we can lookup a service in the HostAPI using a uuid.
        There are two calls to objects.Service.get_by_uuid and the first
        raises ServiceNotFound so that we ensure we keep looping over the
        cells. We'll find the service in the second cell and break the loop
        so that we don't needlessly check in the third cell.
        """

        def _fake_set_target_cell(ctxt, cell_mapping):
            if cell_mapping:
                # These aren't really what would be set for values but let's
                # keep this simple so we can assert something is set when a
                # mapping is provided.
                ctxt.db_connection = cell_mapping.database_connection
                ctxt.mq_connection = cell_mapping.transport_url

        # We have to override the SingleCellSimple fixture.
        self.useFixture(fixtures.MonkeyPatch(
            'nova.context.set_target_cell', _fake_set_target_cell))
        ctxt = context.get_admin_context()
        self.assertIsNone(ctxt.db_connection)
        self.host_api.service_get_by_id(ctxt, uuids.service_uuid)
        # We should have broken the loop over the cells and set the target cell
        # on the context.
        service_get_by_uuid.assert_has_calls(
            [mock.call(ctxt, uuids.service_uuid)] * 2)
        self.assertEqual('db://fake2', ctxt.db_connection)

    @mock.patch('nova.context.set_target_cell')
    @mock.patch('nova.compute.api.load_cells')
    @mock.patch('nova.objects.Service.get_by_id')
    def test_service_delete(self, get_by_id, load_cells, set_target):
        compute_api.CELLS = [
            objects.CellMapping(),
            objects.CellMapping(),
            objects.CellMapping(),
        ]

        service = mock.MagicMock()
        get_by_id.side_effect = [exception.ServiceNotFound(service_id=1),
                                 service,
                                 exception.ServiceNotFound(service_id=1)]
        self.host_api.service_delete(self.ctxt, 1)
        get_by_id.assert_has_calls([mock.call(self.ctxt, 1),
                                    mock.call(self.ctxt, 1),
                                    mock.call(self.ctxt, 1)])
        service.destroy.assert_called_once_with()
        set_target.assert_called_once_with(self.ctxt, compute_api.CELLS[1])

    @mock.patch('nova.context.set_target_cell')
    @mock.patch('nova.compute.api.load_cells')
    @mock.patch('nova.objects.Service.get_by_id')
    def test_service_delete_ambiguous(self, get_by_id, load_cells, set_target):
        compute_api.CELLS = [
            objects.CellMapping(),
            objects.CellMapping(),
            objects.CellMapping(),
        ]

        service1 = mock.MagicMock()
        service2 = mock.MagicMock()
        get_by_id.side_effect = [exception.ServiceNotFound(service_id=1),
                                 service1,
                                 service2]
        self.assertRaises(exception.ServiceNotUnique,
                          self.host_api.service_delete, self.ctxt, 1)
        self.assertFalse(service1.destroy.called)
        self.assertFalse(service2.destroy.called)
        self.assertFalse(set_target.called)

    def test_service_delete_compute_in_aggregate(self):
        compute = self.host_api.db.service_create(self.ctxt,
            {'host': 'fake-compute-host',
             'binary': 'nova-compute',
             'topic': 'compute',
             'report_count': 0})
        aggregate = self.aggregate_api.create_aggregate(self.ctxt,
                                                   'aggregate',
                                                   None)
        self.aggregate_api.add_host_to_aggregate(self.ctxt,
                                                 aggregate.id,
                                                 'fake-compute-host')
        self.controller.delete(self.req, compute.id)
        result = self.aggregate_api.get_aggregate(self.ctxt,
                                                  aggregate.id).hosts
        self.assertEqual([], result)

    @mock.patch('nova.db.compute_node_statistics')
    def test_compute_node_statistics(self, mock_cns):
        # Note this should only be called twice
        mock_cns.side_effect = [
            {'stat1': 1, 'stat2': 4.0},
            {'stat1': 5, 'stat2': 1.2},
        ]
        compute_api.CELLS = [objects.CellMapping(uuid=uuids.cell1),
                             objects.CellMapping(
                                 uuid=objects.CellMapping.CELL0_UUID),
                             objects.CellMapping(uuid=uuids.cell2)]
        stats = self.host_api.compute_node_statistics(self.ctxt)
        self.assertEqual({'stat1': 6, 'stat2': 5.2}, stats)

    @mock.patch.object(objects.CellMappingList, 'get_all',
                       return_value=objects.CellMappingList(objects=[
                           objects.CellMapping(
                               uuid=objects.CellMapping.CELL0_UUID,
                               transport_url='mq://cell0',
                               database_connection='db://cell0'),
                           objects.CellMapping(
                               uuid=uuids.cell1_uuid,
                               transport_url='mq://fake1',
                               database_connection='db://fake1'),
                           objects.CellMapping(
                               uuid=uuids.cell2_uuid,
                               transport_url='mq://fake2',
                               database_connection='db://fake2')]))
    @mock.patch.object(objects.ComputeNode, 'get_by_uuid',
                       side_effect=[exception.ComputeHostNotFound(
                                        host=uuids.cn_uuid),
                                    objects.ComputeNode(uuid=uuids.cn_uuid)])
    def test_compute_node_get_using_uuid(self, compute_get_by_uuid,
                                         cell_mappings_get_all):
        """Tests that we can lookup a compute node in the HostAPI using a uuid.
        """
        self.host_api.compute_node_get(self.ctxt, uuids.cn_uuid)
        # cell0 should have been skipped, and the compute node wasn't found
        # in cell1 so we checked cell2 and found it
        self.assertEqual(2, compute_get_by_uuid.call_count)
        compute_get_by_uuid.assert_has_calls(
            [mock.call(self.ctxt, uuids.cn_uuid)] * 2)

    @mock.patch.object(objects.CellMappingList, 'get_all',
                       return_value=objects.CellMappingList(objects=[
                           objects.CellMapping(
                               uuid=objects.CellMapping.CELL0_UUID,
                               transport_url='mq://cell0',
                               database_connection='db://cell0'),
                           objects.CellMapping(
                               uuid=uuids.cell1_uuid,
                               transport_url='mq://fake1',
                               database_connection='db://fake1'),
                           objects.CellMapping(
                               uuid=uuids.cell2_uuid,
                               transport_url='mq://fake2',
                               database_connection='db://fake2')]))
    @mock.patch.object(objects.ComputeNode, 'get_by_uuid',
                       side_effect=exception.ComputeHostNotFound(
                           host=uuids.cn_uuid))
    def test_compute_node_get_not_found(self, compute_get_by_uuid,
                                        cell_mappings_get_all):
        """Tests that we can lookup a compute node in the HostAPI using a uuid
        and will fail with ComputeHostNotFound if we didn't find it in any
        cell.
        """
        self.assertRaises(exception.ComputeHostNotFound,
                          self.host_api.compute_node_get,
                          self.ctxt, uuids.cn_uuid)
        # cell0 should have been skipped, and the compute node wasn't found
        # in cell1 or cell2.
        self.assertEqual(2, compute_get_by_uuid.call_count)
        compute_get_by_uuid.assert_has_calls(
            [mock.call(self.ctxt, uuids.cn_uuid)] * 2)


class ComputeHostAPICellsTestCase(ComputeHostAPITestCase):
    def setUp(self):
        self.flags(enable=True, group='cells')
        self.flags(cell_type='api', group='cells')
        super(ComputeHostAPICellsTestCase, self).setUp()

    @testtools.skip('cellsv1 does not use this')
    def test_service_get_all_cells(self):
        pass

    @testtools.skip('cellsv1 does not use this')
    def test_service_delete_ambiguous(self):
        pass

    def test_service_get_all_no_zones(self):
        services = [
            cells_utils.ServiceProxy(
                objects.Service(id=1, topic='compute', host='host1'),
                'cell1'),
            cells_utils.ServiceProxy(
                objects.Service(id=2, topic='compute', host='host2'),
                'cell1')]

        fake_filters = {'host': 'host1'}

        @mock.patch.object(self.host_api.cells_rpcapi, 'service_get_all')
        def _do_test(mock_service_get_all):
            mock_service_get_all.return_value = services
            result = self.host_api.service_get_all(self.ctxt,
                                                   filters=fake_filters)
            self.assertEqual(services, result)

        _do_test()

    def _test_service_get_all(self, fake_filters, **kwargs):
        service_attrs = dict(test_service.fake_service)
        del service_attrs['version']
        services = [
            cells_utils.ServiceProxy(
                objects.Service(**dict(service_attrs, id=1,
                                topic='compute', host='host1')),
                'cell1'),
            cells_utils.ServiceProxy(
                objects.Service(**dict(service_attrs, id=2,
                                topic='compute', host='host2')),
                'cell1')]
        exp_services = []
        for service in services:
            exp_service = copy.copy(service)
            exp_service.update({'availability_zone': 'nova'})
            exp_services.append(exp_service)

        @mock.patch.object(self.host_api.cells_rpcapi, 'service_get_all')
        def _do_test(mock_service_get_all):
            mock_service_get_all.return_value = services
            result = self.host_api.service_get_all(self.ctxt,
                                                   filters=fake_filters,
                                                   **kwargs)
            mock_service_get_all.assert_called_once_with(self.ctxt,
                                                         filters=fake_filters)
            self.assertEqual(jsonutils.to_primitive(exp_services),
                             jsonutils.to_primitive(result))

        _do_test()

    def test_service_get_all(self):
        fake_filters = {'availability_zone': 'nova'}
        self._test_service_get_all(fake_filters)

    def test_service_get_all_set_zones(self):
        fake_filters = {'key1': 'val1'}
        self._test_service_get_all(fake_filters, set_zones=True)

    def test_service_get_by_compute_host(self):
        obj = objects.Service(id=1, host='fake')
        fake_service = cells_utils.ServiceProxy(obj, 'cell1')

        @mock.patch.object(self.host_api.cells_rpcapi,
                           'service_get_by_compute_host')
        def _do_test(mock_service_get_by_compute_host):
            mock_service_get_by_compute_host.return_value = fake_service
            result = self.host_api.service_get_by_compute_host(self.ctxt,
                                                               'fake-host')
            self.assertEqual(fake_service, result)

        _do_test()

    def test_service_update(self):
        host_name = 'fake-host'
        binary = 'nova-compute'
        params_to_update = dict(disabled=True)

        obj = objects.Service(id=42, host='fake')
        fake_service = cells_utils.ServiceProxy(obj, 'cell1')

        @mock.patch.object(self.host_api.cells_rpcapi, 'service_update')
        def _do_test(mock_service_update):
            mock_service_update.return_value = fake_service

            result = self.host_api.service_update(
                self.ctxt, host_name, binary, params_to_update)
            self.assertEqual(fake_service, result)

        _do_test()

    def test_service_delete(self):
        cell_service_id = cells_utils.cell_with_item('cell1', 1)
        with mock.patch.object(self.host_api.cells_rpcapi,
                               'service_delete') as service_delete:
            self.host_api.service_delete(self.ctxt, cell_service_id)
            service_delete.assert_called_once_with(
                self.ctxt, cell_service_id)

    @testtools.skip('cells do not support host aggregates')
    def test_service_delete_compute_in_aggregate(self):
        # this test is not valid for cell
        pass

    @mock.patch.object(objects.InstanceList, 'get_by_host')
    def test_instance_get_all_by_host(self, mock_get):
        instances = [dict(id=1, cell_name='cell1', host='host1'),
                     dict(id=2, cell_name='cell2', host='host1'),
                     dict(id=3, cell_name='cell1', host='host2')]

        mock_get.return_value = instances
        expected_result = [instances[0], instances[2]]
        cell_and_host = cells_utils.cell_with_item('cell1', 'fake-host')
        result = self.host_api.instance_get_all_by_host(self.ctxt,
                cell_and_host)
        self.assertEqual(expected_result, result)

    def test_task_log_get_all(self):
        @mock.patch.object(self.host_api.cells_rpcapi, 'task_log_get_all',
                           return_value='fake-response')
        def _do_test(mock_task_log_get_all):
            result = self.host_api.task_log_get_all(self.ctxt, 'fake-name',
                                                    'fake-begin', 'fake-end',
                                                    host='fake-host',
                                                    state='fake-state')
            self.assertEqual('fake-response', result)

        _do_test()

    def test_get_host_uptime_service_down(self):
        # The corresponding Compute test case depends on the
        # _assert_host_exists which is a no-op in the cells api
        pass

    def test_get_host_uptime(self):
        @mock.patch.object(self.host_api.cells_rpcapi, 'get_host_uptime',
                           return_value='fake-response')
        def _do_test(mock_get_host_uptime):
            result = self.host_api.get_host_uptime(self.ctxt, 'fake-host')
            self.assertEqual('fake-response', result)

        _do_test()

    def test_compute_node_statistics(self):
        # Not implementing cross-cellsv2 for cellsv1
        pass

    def test_compute_node_get_using_uuid(self):
        cell_compute_uuid = cells_utils.cell_with_item('cell1', uuids.cn_uuid)
        with mock.patch.object(self.host_api.cells_rpcapi,
                               'compute_node_get') as compute_node_get:
            self.host_api.compute_node_get(self.ctxt, cell_compute_uuid)
        compute_node_get.assert_called_once_with(self.ctxt, cell_compute_uuid)

    def test_compute_node_get_not_found(self):
        cell_compute_uuid = cells_utils.cell_with_item('cell1', uuids.cn_uuid)
        with mock.patch.object(self.host_api.cells_rpcapi, 'compute_node_get',
                               side_effect=exception.CellRoutingInconsistency(
                                   reason='because_cells_v1')):
            self.assertRaises(exception.ComputeHostNotFound,
                              self.host_api.compute_node_get,
                              self.ctxt, cell_compute_uuid)
