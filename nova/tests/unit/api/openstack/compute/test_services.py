# Copyright 2012 IBM Corp.
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
import datetime

import iso8601
import mock
from oslo_utils import fixture as utils_fixture
import six
import webob.exc

from nova.api.openstack import api_version_request as api_version
from nova.api.openstack.compute import services as services_v21
from nova.api.openstack import wsgi as os_wsgi
from nova import availability_zones
from nova.cells import utils as cells_utils
from nova import compute
from nova import context
from nova import exception
from nova import objects
from nova.servicegroup.drivers import db as db_driver
from nova import test
from nova.tests import fixtures
from nova.tests.unit.api.openstack import fakes
from nova.tests.unit.objects import test_service
from nova.tests import uuidsentinel

# This is tied into the os-services API samples functional tests.
FAKE_UUID_COMPUTE_HOST1 = 'e81d66a4-ddd3-4aba-8a84-171d1cb4d339'


fake_services_list = [
    dict(test_service.fake_service,
         binary='nova-scheduler',
         host='host1',
         id=1,
         uuid=uuidsentinel.svc1,
         disabled=True,
         topic='scheduler',
         updated_at=datetime.datetime(2012, 10, 29, 13, 42, 2),
         created_at=datetime.datetime(2012, 9, 18, 2, 46, 27),
         last_seen_up=datetime.datetime(2012, 10, 29, 13, 42, 2),
         forced_down=False,
         disabled_reason='test1'),
    dict(test_service.fake_service,
         binary='nova-compute',
         host='host1',
         id=2,
         uuid=FAKE_UUID_COMPUTE_HOST1,
         disabled=True,
         topic='compute',
         updated_at=datetime.datetime(2012, 10, 29, 13, 42, 5),
         created_at=datetime.datetime(2012, 9, 18, 2, 46, 27),
         last_seen_up=datetime.datetime(2012, 10, 29, 13, 42, 5),
         forced_down=False,
         disabled_reason='test2'),
    dict(test_service.fake_service,
         binary='nova-scheduler',
         host='host2',
         id=3,
         uuid=uuidsentinel.svc3,
         disabled=False,
         topic='scheduler',
         updated_at=datetime.datetime(2012, 9, 19, 6, 55, 34),
         created_at=datetime.datetime(2012, 9, 18, 2, 46, 28),
         last_seen_up=datetime.datetime(2012, 9, 19, 6, 55, 34),
         forced_down=False,
         disabled_reason=None),
    dict(test_service.fake_service,
         binary='nova-compute',
         host='host2',
         id=4,
         uuid=uuidsentinel.svc4,
         disabled=True,
         topic='compute',
         updated_at=datetime.datetime(2012, 9, 18, 8, 3, 38),
         created_at=datetime.datetime(2012, 9, 18, 2, 46, 28),
         last_seen_up=datetime.datetime(2012, 9, 18, 8, 3, 38),
         forced_down=False,
         disabled_reason='test4'),
    # NOTE(rpodolyaka): API services are special case and must be filtered out
    dict(test_service.fake_service,
         binary='nova-osapi_compute',
         host='host2',
         id=5,
         uuid=uuidsentinel.svc5,
         disabled=False,
         topic=None,
         updated_at=None,
         created_at=datetime.datetime(2012, 9, 18, 2, 46, 28),
         last_seen_up=None,
         forced_down=False,
         disabled_reason=None),
    dict(test_service.fake_service,
         binary='nova-metadata',
         host='host2',
         id=6,
         uuid=uuidsentinel.svc6,
         disabled=False,
         topic=None,
         updated_at=None,
         created_at=datetime.datetime(2012, 9, 18, 2, 46, 28),
         last_seen_up=None,
         forced_down=False,
         disabled_reason=None),
    ]


class FakeRequest(object):
    environ = {"nova.context": context.get_admin_context()}
    GET = {}

    def __init__(self, version=os_wsgi.DEFAULT_API_VERSION):  # version='2.1'):
        super(FakeRequest, self).__init__()
        self.api_version_request = api_version.APIVersionRequest(version)

    def is_legacy_v2(self):
        return False


class FakeRequestWithService(FakeRequest):
    GET = {"binary": "nova-compute"}


class FakeRequestWithHost(FakeRequest):
    GET = {"host": "host1"}


class FakeRequestWithHostService(FakeRequest):
    GET = {"host": "host1", "binary": "nova-compute"}


def fake_service_get_all(services):
    def service_get_all(context, filters=None, set_zones=False,
                        all_cells=False):
        if set_zones or 'availability_zone' in filters:
            return availability_zones.set_availability_zones(context,
                                                             services)
        return services
    return service_get_all


def fake_db_api_service_get_all(context, disabled=None):
    return fake_services_list


def fake_db_service_get_by_host_binary(services):
    def service_get_by_host_binary(context, host, binary):
        for service in services:
            if service['host'] == host and service['binary'] == binary:
                return service
        raise exception.HostBinaryNotFound(host=host, binary=binary)
    return service_get_by_host_binary


def fake_service_get_by_host_binary(context, host, binary):
    fake = fake_db_service_get_by_host_binary(fake_services_list)
    return fake(context, host, binary)


def _service_get_by_id(services, value):
    for service in services:
        if service['id'] == value:
            return service
    return None


def fake_db_service_update(services):
    def service_update(context, service_id, values):
        service = _service_get_by_id(services, service_id)
        if service is None:
            raise exception.ServiceNotFound(service_id=service_id)
        service = copy.deepcopy(service)
        service.update(values)
        return service
    return service_update


def fake_service_update(context, service_id, values):
    fake = fake_db_service_update(fake_services_list)
    return fake(context, service_id, values)


def fake_utcnow():
    return datetime.datetime(2012, 10, 29, 13, 42, 11)


class ServicesTestV21(test.TestCase):
    service_is_up_exc = webob.exc.HTTPInternalServerError
    bad_request = exception.ValidationError
    wsgi_api_version = os_wsgi.DEFAULT_API_VERSION

    def _set_up_controller(self):
        self.controller = services_v21.ServiceController()

    def setUp(self):
        super(ServicesTestV21, self).setUp()

        self.ctxt = context.get_admin_context()
        self.host_api = compute.HostAPI()
        self._set_up_controller()
        self.controller.host_api.service_get_all = (
            mock.Mock(side_effect=fake_service_get_all(fake_services_list)))

        self.useFixture(utils_fixture.TimeFixture(fake_utcnow()))
        self.stub_out('nova.db.service_get_by_host_and_binary',
                      fake_db_service_get_by_host_binary(fake_services_list))
        self.stub_out('nova.db.service_update',
                      fake_db_service_update(fake_services_list))

        self.req = fakes.HTTPRequest.blank('')
        self.useFixture(fixtures.SingleCellSimple())

    def _process_output(self, services, has_disabled=False, has_id=False):
        return services

    def test_services_list(self):
        req = FakeRequest()
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-scheduler',
                    'host': 'host1',
                    'zone': 'internal',
                    'status': 'disabled',
                    'id': 1,
                    'state': 'up',
                    'disabled_reason': 'test1',
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 2)},
                    {'binary': 'nova-compute',
                     'host': 'host1',
                     'zone': 'nova',
                     'id': 2,
                     'status': 'disabled',
                     'disabled_reason': 'test2',
                     'state': 'up',
                     'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5)},
                    {'binary': 'nova-scheduler',
                     'host': 'host2',
                     'zone': 'internal',
                     'id': 3,
                     'status': 'enabled',
                     'disabled_reason': None,
                     'state': 'down',
                     'updated_at': datetime.datetime(2012, 9, 19, 6, 55, 34)},
                    {'binary': 'nova-compute',
                     'host': 'host2',
                     'zone': 'nova',
                     'id': 4,
                     'status': 'disabled',
                     'disabled_reason': 'test4',
                     'state': 'down',
                     'updated_at': datetime.datetime(2012, 9, 18, 8, 3, 38)}]}
        self._process_output(response)
        self.assertEqual(res_dict, response)

    def test_services_list_with_host(self):
        req = FakeRequestWithHost()
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-scheduler',
                    'host': 'host1',
                    'disabled_reason': 'test1',
                    'id': 1,
                    'zone': 'internal',
                    'status': 'disabled',
                    'state': 'up',
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 2)},
                   {'binary': 'nova-compute',
                    'host': 'host1',
                    'zone': 'nova',
                    'disabled_reason': 'test2',
                    'id': 2,
                    'status': 'disabled',
                    'state': 'up',
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5)}]}
        self._process_output(response)
        self.assertEqual(res_dict, response)

    def test_services_list_with_service(self):
        req = FakeRequestWithService()
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-compute',
                    'host': 'host1',
                    'disabled_reason': 'test2',
                    'id': 2,
                    'zone': 'nova',
                    'status': 'disabled',
                    'state': 'up',
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5)},
                    {'binary': 'nova-compute',
                     'host': 'host2',
                     'zone': 'nova',
                     'disabled_reason': 'test4',
                     'id': 4,
                     'status': 'disabled',
                     'state': 'down',
                     'updated_at': datetime.datetime(2012, 9, 18, 8, 3, 38)}]}
        self._process_output(response)
        self.assertEqual(res_dict, response)

    def test_services_list_with_host_service(self):
        req = FakeRequestWithHostService()
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-compute',
                    'host': 'host1',
                    'zone': 'nova',
                    'disabled_reason': 'test2',
                    'id': 2,
                    'status': 'disabled',
                    'state': 'up',
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5)}]}
        self._process_output(response)
        self.assertEqual(res_dict, response)

    def test_services_detail(self):
        req = FakeRequest()
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-scheduler',
                    'host': 'host1',
                    'zone': 'internal',
                    'status': 'disabled',
                    'id': 1,
                    'state': 'up',
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 2),
                    'disabled_reason': 'test1'},
                    {'binary': 'nova-compute',
                     'host': 'host1',
                     'zone': 'nova',
                     'status': 'disabled',
                     'state': 'up',
                     'id': 2,
                     'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5),
                     'disabled_reason': 'test2'},
                    {'binary': 'nova-scheduler',
                     'host': 'host2',
                     'zone': 'internal',
                     'status': 'enabled',
                     'id': 3,
                     'state': 'down',
                     'updated_at': datetime.datetime(2012, 9, 19, 6, 55, 34),
                     'disabled_reason': None},
                    {'binary': 'nova-compute',
                     'host': 'host2',
                     'zone': 'nova',
                     'id': 4,
                     'status': 'disabled',
                     'state': 'down',
                     'updated_at': datetime.datetime(2012, 9, 18, 8, 3, 38),
                     'disabled_reason': 'test4'}]}
        self._process_output(response, has_disabled=True)
        self.assertEqual(res_dict, response)

    def test_service_detail_with_host(self):
        req = FakeRequestWithHost()
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-scheduler',
                    'host': 'host1',
                    'zone': 'internal',
                    'id': 1,
                    'status': 'disabled',
                    'state': 'up',
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 2),
                    'disabled_reason': 'test1'},
                   {'binary': 'nova-compute',
                    'host': 'host1',
                    'zone': 'nova',
                    'id': 2,
                    'status': 'disabled',
                    'state': 'up',
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5),
                    'disabled_reason': 'test2'}]}
        self._process_output(response, has_disabled=True)
        self.assertEqual(res_dict, response)

    def test_service_detail_with_service(self):
        req = FakeRequestWithService()
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-compute',
                    'host': 'host1',
                    'zone': 'nova',
                    'id': 2,
                    'status': 'disabled',
                    'state': 'up',
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5),
                    'disabled_reason': 'test2'},
                    {'binary': 'nova-compute',
                     'host': 'host2',
                     'id': 4,
                     'zone': 'nova',
                     'status': 'disabled',
                     'state': 'down',
                     'updated_at': datetime.datetime(2012, 9, 18, 8, 3, 38),
                     'disabled_reason': 'test4'}]}
        self._process_output(response, has_disabled=True)
        self.assertEqual(res_dict, response)

    def test_service_detail_with_host_service(self):
        req = FakeRequestWithHostService()
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-compute',
                    'host': 'host1',
                    'zone': 'nova',
                    'status': 'disabled',
                    'id': 2,
                    'state': 'up',
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5),
                    'disabled_reason': 'test2'}]}
        self._process_output(response, has_disabled=True)
        self.assertEqual(res_dict, response)

    def test_services_detail_with_delete_extension(self):
        req = FakeRequest()
        res_dict = self.controller.index(req)

        response = {'services': [
            {'binary': 'nova-scheduler',
             'host': 'host1',
             'id': 1,
             'zone': 'internal',
             'disabled_reason': 'test1',
             'status': 'disabled',
             'state': 'up',
             'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 2)},
            {'binary': 'nova-compute',
             'host': 'host1',
             'id': 2,
             'zone': 'nova',
             'disabled_reason': 'test2',
             'status': 'disabled',
             'state': 'up',
             'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5)},
            {'binary': 'nova-scheduler',
             'host': 'host2',
             'disabled_reason': None,
             'id': 3,
             'zone': 'internal',
             'status': 'enabled',
             'state': 'down',
             'updated_at': datetime.datetime(2012, 9, 19, 6, 55, 34)},
            {'binary': 'nova-compute',
             'host': 'host2',
             'id': 4,
             'disabled_reason': 'test4',
             'zone': 'nova',
             'status': 'disabled',
             'state': 'down',
             'updated_at': datetime.datetime(2012, 9, 18, 8, 3, 38)}]}
        self._process_output(response, has_id=True)
        self.assertEqual(res_dict, response)

    def test_services_enable(self):
        def _service_update(context, service_id, values):
            self.assertIsNone(values['disabled_reason'])
            return dict(test_service.fake_service, id=service_id, **values)

        self.stub_out('nova.db.service_update', _service_update)

        body = {'host': 'host1', 'binary': 'nova-compute'}
        res_dict = self.controller.update(self.req, "enable", body=body)
        self.assertEqual(res_dict['service']['status'], 'enabled')
        self.assertNotIn('disabled_reason', res_dict['service'])

    def test_services_enable_with_invalid_host(self):
        body = {'host': 'invalid', 'binary': 'nova-compute'}
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.update,
                          self.req,
                          "enable",
                          body=body)

    def test_services_enable_with_unmapped_host(self):
        body = {'host': 'invalid', 'binary': 'nova-compute'}
        with mock.patch.object(self.controller.host_api,
                               'service_update') as m:
            m.side_effect = exception.HostMappingNotFound(name='something')
            self.assertRaises(webob.exc.HTTPNotFound,
                              self.controller.update,
                              self.req,
                              "enable",
                              body=body)

    def test_services_enable_with_invalid_binary(self):
        body = {'host': 'host1', 'binary': 'invalid'}
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.update,
                          self.req,
                          "enable",
                          body=body)

    def test_services_disable(self):
        body = {'host': 'host1', 'binary': 'nova-compute'}
        res_dict = self.controller.update(self.req, "disable", body=body)

        self.assertEqual(res_dict['service']['status'], 'disabled')
        self.assertNotIn('disabled_reason', res_dict['service'])

    def test_services_disable_with_invalid_host(self):
        body = {'host': 'invalid', 'binary': 'nova-compute'}
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.update,
                          self.req,
                          "disable",
                          body=body)

    def test_services_disable_with_invalid_binary(self):
        body = {'host': 'host1', 'binary': 'invalid'}
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.update,
                          self.req,
                          "disable",
                          body=body)

    def test_services_disable_log_reason(self):
        body = {'host': 'host1',
                'binary': 'nova-compute',
                'disabled_reason': 'test-reason',
                }
        res_dict = self.controller.update(self.req,
                                          "disable-log-reason",
                                          body=body)

        self.assertEqual(res_dict['service']['status'], 'disabled')
        self.assertEqual(res_dict['service']['disabled_reason'], 'test-reason')

    def test_mandatory_reason_field(self):
        body = {'host': 'host1',
                'binary': 'nova-compute',
               }
        self.assertRaises(webob.exc.HTTPBadRequest,
                self.controller.update, self.req, "disable-log-reason",
                body=body)

    def test_invalid_reason_field(self):
        reason = 'a' * 256
        body = {'host': 'host1',
                'binary': 'nova-compute',
                'disabled_reason': reason,
               }
        self.assertRaises(self.bad_request,
                self.controller.update, self.req, "disable-log-reason",
                body=body)

    def test_services_delete(self):

        compute = self.host_api.db.service_create(self.ctxt,
            {'host': 'fake-compute-host',
             'binary': 'nova-compute',
             'topic': 'compute',
             'report_count': 0})

        with mock.patch.object(self.controller.host_api,
                               'service_delete') as service_delete:
            self.controller.delete(self.req, compute.id)
            service_delete.assert_called_once_with(
                self.req.environ['nova.context'], compute.id)
            self.assertEqual(self.controller.delete.wsgi_code, 204)

    def test_services_delete_not_found(self):

        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.delete, self.req, 1234)

    def test_services_delete_invalid_id(self):

        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.controller.delete, self.req, 'abc')

    def test_services_delete_duplicate_service(self):
        with mock.patch.object(self.controller, 'host_api') as host_api:
            host_api.service_delete.side_effect = exception.ServiceNotUnique()
            self.assertRaises(webob.exc.HTTPBadRequest,
                              self.controller.delete, self.req, 1234)
            self.assertTrue(host_api.service_delete.called)

    # This test is just to verify that the servicegroup API gets used when
    # calling the API
    @mock.patch.object(db_driver.DbDriver, 'is_up', side_effect=KeyError)
    def test_services_with_exception(self, mock_is_up):
        req = FakeRequestWithHostService()
        self.assertRaises(self.service_is_up_exc, self.controller.index, req)


class ServicesTestV211(ServicesTestV21):
    wsgi_api_version = '2.11'

    def test_services_list(self):
        req = FakeRequest(self.wsgi_api_version)
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-scheduler',
                    'host': 'host1',
                    'zone': 'internal',
                    'status': 'disabled',
                    'id': 1,
                    'state': 'up',
                    'forced_down': False,
                    'disabled_reason': 'test1',
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 2)},
                    {'binary': 'nova-compute',
                     'host': 'host1',
                     'zone': 'nova',
                     'id': 2,
                     'status': 'disabled',
                     'disabled_reason': 'test2',
                     'state': 'up',
                     'forced_down': False,
                     'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5)},
                    {'binary': 'nova-scheduler',
                     'host': 'host2',
                     'zone': 'internal',
                     'id': 3,
                     'status': 'enabled',
                     'disabled_reason': None,
                     'state': 'down',
                     'forced_down': False,
                     'updated_at': datetime.datetime(2012, 9, 19, 6, 55, 34)},
                    {'binary': 'nova-compute',
                     'host': 'host2',
                     'zone': 'nova',
                     'id': 4,
                     'status': 'disabled',
                     'disabled_reason': 'test4',
                     'state': 'down',
                     'forced_down': False,
                     'updated_at': datetime.datetime(2012, 9, 18, 8, 3, 38)}]}
        self._process_output(response)
        self.assertEqual(res_dict, response)

    def test_services_list_with_host(self):
        req = FakeRequestWithHost(self.wsgi_api_version)
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-scheduler',
                    'host': 'host1',
                    'disabled_reason': 'test1',
                    'id': 1,
                    'zone': 'internal',
                    'status': 'disabled',
                    'state': 'up',
                    'forced_down': False,
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 2)},
                   {'binary': 'nova-compute',
                    'host': 'host1',
                    'zone': 'nova',
                    'disabled_reason': 'test2',
                    'id': 2,
                    'status': 'disabled',
                    'state': 'up',
                    'forced_down': False,
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5)}]}
        self._process_output(response)
        self.assertEqual(res_dict, response)

    def test_services_list_with_service(self):
        req = FakeRequestWithService(self.wsgi_api_version)
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-compute',
                    'host': 'host1',
                    'disabled_reason': 'test2',
                    'id': 2,
                    'zone': 'nova',
                    'status': 'disabled',
                    'state': 'up',
                    'forced_down': False,
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5)},
                    {'binary': 'nova-compute',
                     'host': 'host2',
                     'zone': 'nova',
                     'disabled_reason': 'test4',
                     'id': 4,
                     'status': 'disabled',
                     'state': 'down',
                     'forced_down': False,
                     'updated_at': datetime.datetime(2012, 9, 18, 8, 3, 38)}]}
        self._process_output(response)
        self.assertEqual(res_dict, response)

    def test_services_list_with_host_service(self):
        req = FakeRequestWithHostService(self.wsgi_api_version)
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-compute',
                    'host': 'host1',
                    'zone': 'nova',
                    'disabled_reason': 'test2',
                    'id': 2,
                    'status': 'disabled',
                    'state': 'up',
                    'forced_down': False,
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5)}]}
        self._process_output(response)
        self.assertEqual(res_dict, response)

    def test_services_detail(self):
        req = FakeRequest(self.wsgi_api_version)
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-scheduler',
                    'host': 'host1',
                    'zone': 'internal',
                    'status': 'disabled',
                    'id': 1,
                    'state': 'up',
                    'forced_down': False,
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 2),
                    'disabled_reason': 'test1'},
                    {'binary': 'nova-compute',
                     'host': 'host1',
                     'zone': 'nova',
                     'status': 'disabled',
                     'state': 'up',
                     'id': 2,
                     'forced_down': False,
                     'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5),
                     'disabled_reason': 'test2'},
                    {'binary': 'nova-scheduler',
                     'host': 'host2',
                     'zone': 'internal',
                     'status': 'enabled',
                     'id': 3,
                     'state': 'down',
                     'forced_down': False,
                     'updated_at': datetime.datetime(2012, 9, 19, 6, 55, 34),
                     'disabled_reason': None},
                    {'binary': 'nova-compute',
                     'host': 'host2',
                     'zone': 'nova',
                     'id': 4,
                     'status': 'disabled',
                     'state': 'down',
                     'forced_down': False,
                     'updated_at': datetime.datetime(2012, 9, 18, 8, 3, 38),
                     'disabled_reason': 'test4'}]}
        self._process_output(response, has_disabled=True)
        self.assertEqual(res_dict, response)

    def test_service_detail_with_host(self):
        req = FakeRequestWithHost(self.wsgi_api_version)
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-scheduler',
                    'host': 'host1',
                    'zone': 'internal',
                    'id': 1,
                    'status': 'disabled',
                    'state': 'up',
                    'forced_down': False,
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 2),
                    'disabled_reason': 'test1'},
                   {'binary': 'nova-compute',
                    'host': 'host1',
                    'zone': 'nova',
                    'id': 2,
                    'status': 'disabled',
                    'state': 'up',
                    'forced_down': False,
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5),
                    'disabled_reason': 'test2'}]}
        self._process_output(response, has_disabled=True)
        self.assertEqual(res_dict, response)

    def test_service_detail_with_service(self):
        req = FakeRequestWithService(self.wsgi_api_version)
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-compute',
                    'host': 'host1',
                    'zone': 'nova',
                    'id': 2,
                    'status': 'disabled',
                    'state': 'up',
                    'forced_down': False,
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5),
                    'disabled_reason': 'test2'},
                    {'binary': 'nova-compute',
                     'host': 'host2',
                     'id': 4,
                     'zone': 'nova',
                     'status': 'disabled',
                     'state': 'down',
                     'forced_down': False,
                     'updated_at': datetime.datetime(2012, 9, 18, 8, 3, 38),
                     'disabled_reason': 'test4'}]}
        self._process_output(response, has_disabled=True)
        self.assertEqual(res_dict, response)

    def test_service_detail_with_host_service(self):
        req = FakeRequestWithHostService(self.wsgi_api_version)
        res_dict = self.controller.index(req)

        response = {'services': [
                    {'binary': 'nova-compute',
                    'host': 'host1',
                    'zone': 'nova',
                    'status': 'disabled',
                    'id': 2,
                    'state': 'up',
                    'forced_down': False,
                    'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5),
                    'disabled_reason': 'test2'}]}
        self._process_output(response, has_disabled=True)
        self.assertEqual(res_dict, response)

    def test_services_detail_with_delete_extension(self):
        req = FakeRequest(self.wsgi_api_version)
        res_dict = self.controller.index(req)

        response = {'services': [
            {'binary': 'nova-scheduler',
             'host': 'host1',
             'id': 1,
             'zone': 'internal',
             'disabled_reason': 'test1',
             'status': 'disabled',
             'state': 'up',
             'forced_down': False,
             'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 2)},
            {'binary': 'nova-compute',
             'host': 'host1',
             'id': 2,
             'zone': 'nova',
             'disabled_reason': 'test2',
             'status': 'disabled',
             'state': 'up',
             'forced_down': False,
             'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5)},
            {'binary': 'nova-scheduler',
             'host': 'host2',
             'disabled_reason': None,
             'id': 3,
             'zone': 'internal',
             'status': 'enabled',
             'state': 'down',
             'forced_down': False,
             'updated_at': datetime.datetime(2012, 9, 19, 6, 55, 34)},
            {'binary': 'nova-compute',
             'host': 'host2',
             'id': 4,
             'disabled_reason': 'test4',
             'zone': 'nova',
             'status': 'disabled',
             'state': 'down',
             'forced_down': False,
             'updated_at': datetime.datetime(2012, 9, 18, 8, 3, 38)}]}
        self._process_output(response, has_id=True)
        self.assertEqual(res_dict, response)

    def test_force_down_service(self):
        req = FakeRequest(self.wsgi_api_version)
        req_body = {"forced_down": True,
                    "host": "host1", "binary": "nova-compute"}
        res_dict = self.controller.update(req, 'force-down', body=req_body)

        response = {
            "service": {
                "forced_down": True,
                "host": "host1",
                "binary": "nova-compute"
            }
        }
        self.assertEqual(response, res_dict)

    def test_force_down_service_with_string_forced_down(self):
        req = FakeRequest(self.wsgi_api_version)
        req_body = {"forced_down": "True",
                    "host": "host1", "binary": "nova-compute"}
        res_dict = self.controller.update(req, 'force-down', body=req_body)

        response = {
            "service": {
                "forced_down": True,
                "host": "host1",
                "binary": "nova-compute"
            }
        }
        self.assertEqual(response, res_dict)

    def test_force_down_service_with_invalid_parameter(self):
        req = FakeRequest(self.wsgi_api_version)
        req_body = {"forced_down": "Invalid",
                    "host": "host1", "binary": "nova-compute"}
        self.assertRaises(exception.ValidationError,
            self.controller.update, req, 'force-down', body=req_body)


class ServicesTestV252(ServicesTestV211):
    """This is a boundary test to ensure that 2.52 behaves the same as 2.11."""
    wsgi_api_version = '2.52'


class FakeServiceGroupAPI(object):
    def service_is_up(self, *args, **kwargs):
        return True

    def get_updated_time(self, *args, **kwargs):
        return mock.sentinel.updated_time


class ServicesTestV253(test.TestCase):
    """Tests for the 2.53 microversion in the os-services API."""

    def setUp(self):
        super(ServicesTestV253, self).setUp()
        self.controller = services_v21.ServiceController()
        self.controller.servicegroup_api = FakeServiceGroupAPI()
        self.req = fakes.HTTPRequest.blank(
            '', version=services_v21.UUID_FOR_ID_MIN_VERSION)

    def assert_services_equal(self, s1, s2):
        for k in ('binary', 'host'):
            self.assertEqual(s1[k], s2[k])

    def test_list_has_uuid_in_id_field(self):
        """Tests that a GET response includes an id field but the value is
        the service uuid rather than the id integer primary key.
        """
        service_uuids = [s['uuid'] for s in fake_services_list]
        with mock.patch.object(
                self.controller.host_api, 'service_get_all',
                side_effect=fake_service_get_all(fake_services_list)):
            resp = self.controller.index(self.req)

        for service in resp['services']:
            # Make sure a uuid field wasn't returned.
            self.assertNotIn('uuid', service)
            # Make sure the id field is one of our known uuids.
            self.assertIn(service['id'], service_uuids)
            # Make sure this service was in our known list of fake services.
            expected = next(iter(filter(
                lambda s: s['uuid'] == service['id'],
                fake_services_list)))
            self.assert_services_equal(expected, service)

    def test_delete_takes_uuid_for_id(self):
        """Tests that a DELETE request correctly deletes a service when a valid
        service uuid is provided for an existing service.
        """
        service = self.start_service(
            'compute', 'fake-compute-host').service_ref
        with mock.patch.object(self.controller.host_api,
                               'service_delete') as service_delete:
            self.controller.delete(self.req, service.uuid)
            service_delete.assert_called_once_with(
                self.req.environ['nova.context'], service.uuid)
            self.assertEqual(204, self.controller.delete.wsgi_code)

    def test_delete_uuid_not_found(self):
        """Tests that we get a 404 response when attempting to delete a service
        that is not found by the given uuid.
        """
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.delete, self.req, uuidsentinel.svc2)

    def test_delete_invalid_uuid(self):
        """Tests that the service uuid is validated in a DELETE request."""
        ex = self.assertRaises(webob.exc.HTTPBadRequest,
                               self.controller.delete, self.req, 1234)
        self.assertIn('Invalid uuid', six.text_type(ex))

    def test_update_invalid_service_uuid(self):
        """Tests that the service uuid is validated in a PUT request."""
        ex = self.assertRaises(webob.exc.HTTPBadRequest,
                               self.controller.update, self.req, 1234, body={})
        self.assertIn('Invalid uuid', six.text_type(ex))

    def test_update_policy_failed(self):
        """Tests that policy is checked with microversion 2.53."""
        rule_name = "os_compute_api:os-services"
        self.policy.set_rules({rule_name: "project_id:non_fake"})
        exc = self.assertRaises(
            exception.PolicyNotAuthorized,
            self.controller.update, self.req, uuidsentinel.service_uuid,
            body={})
        self.assertEqual(
            "Policy doesn't allow %s to be performed." % rule_name,
            exc.format_message())

    def test_update_service_not_found(self):
        """Tests that we get a 404 response if the service is not found by
        the given uuid when handling a PUT request.
        """
        self.assertRaises(webob.exc.HTTPNotFound, self.controller.update,
                          self.req, uuidsentinel.service_uuid, body={})

    def test_update_invalid_status(self):
        """Tests that jsonschema validates the status field in the request body
        and fails if it's not "enabled" or "disabled".
        """
        service = self.start_service(
            'compute', 'fake-compute-host').service_ref
        self.assertRaises(
            exception.ValidationError, self.controller.update, self.req,
            service.uuid, body={'status': 'invalid'})

    def test_update_disabled_no_reason_then_enable(self):
        """Tests disabling a service with no reason given. Then enables it
        to see the change in the response body.
        """
        service = self.start_service(
            'compute', 'fake-compute-host').service_ref
        resp = self.controller.update(self.req, service.uuid,
                                      body={'status': 'disabled'})
        expected_resp = {
            'service': {
                'status': 'disabled',
                'state': 'up',
                'binary': 'nova-compute',
                'host': 'fake-compute-host',
                'zone': 'nova',  # Comes from CONF.default_availability_zone
                'updated_at': mock.sentinel.updated_time,
                'disabled_reason': None,
                'id': service.uuid,
                'forced_down': False
            }
        }
        self.assertDictEqual(expected_resp, resp)

        # Now enable the service to see the response change.
        req = fakes.HTTPRequest.blank(
            '', version=services_v21.UUID_FOR_ID_MIN_VERSION)
        resp = self.controller.update(req, service.uuid,
                                      body={'status': 'enabled'})
        expected_resp['service']['status'] = 'enabled'
        self.assertDictEqual(expected_resp, resp)

    def test_update_enable_with_disabled_reason_fails(self):
        """Validates that requesting to both enable a service and set the
        disabled_reason results in a 400 BadRequest error.
        """
        service = self.start_service(
            'compute', 'fake-compute-host').service_ref
        ex = self.assertRaises(webob.exc.HTTPBadRequest,
                               self.controller.update, self.req, service.uuid,
                               body={'status': 'enabled',
                                     'disabled_reason': 'invalid'})
        self.assertIn("Specifying 'disabled_reason' with status 'enabled' "
                      "is invalid.", six.text_type(ex))

    def test_update_disabled_reason_and_forced_down(self):
        """Tests disabling a service with a reason and forcing it down is
        reflected back in the response.
        """
        service = self.start_service(
            'compute', 'fake-compute-host').service_ref
        resp = self.controller.update(self.req, service.uuid,
                                      body={'status': 'disabled',
                                            'disabled_reason': 'maintenance',
                                            # Also tests bool_from_string usage
                                            'forced_down': 'yes'})
        expected_resp = {
            'service': {
                'status': 'disabled',
                'state': 'up',
                'binary': 'nova-compute',
                'host': 'fake-compute-host',
                'zone': 'nova',  # Comes from CONF.default_availability_zone
                'updated_at': mock.sentinel.updated_time,
                'disabled_reason': 'maintenance',
                'id': service.uuid,
                'forced_down': True
            }
        }
        self.assertDictEqual(expected_resp, resp)

    def test_update_forced_down_invalid_value(self):
        """Tests that passing an invalid value for forced_down results in
        a validation error.
        """
        service = self.start_service(
            'compute', 'fake-compute-host').service_ref
        self.assertRaises(exception.ValidationError,
                          self.controller.update,
                          self.req, service.uuid,
                          body={'status': 'disabled',
                                'disabled_reason': 'maintenance',
                                'forced_down': 'invalid'})

    def test_update_forced_down_invalid_service(self):
        """Tests that you can't update a non-nova-compute service."""
        service = self.start_service(
            'scheduler', 'fake-scheduler-host').service_ref
        ex = self.assertRaises(webob.exc.HTTPBadRequest,
                               self.controller.update,
                               self.req, service.uuid,
                               body={'forced_down': True})
        self.assertEqual('Updating a nova-scheduler service is not supported. '
                         'Only nova-compute services can be updated.',
                         six.text_type(ex))

    def test_update_empty_body(self):
        """Tests that the caller gets a 400 error if they don't request any
        updates.
        """
        service = self.start_service('compute').service_ref
        ex = self.assertRaises(webob.exc.HTTPBadRequest,
                               self.controller.update,
                               self.req, service.uuid, body={})
        self.assertEqual("No updates were requested. Fields 'status' or "
                         "'forced_down' should be specified.",
                         six.text_type(ex))

    def test_update_only_disabled_reason(self):
        """Tests that the caller gets a 400 error if they only specify
        disabled_reason but don't also specify status='disabled'.
        """
        service = self.start_service('compute').service_ref
        ex = self.assertRaises(webob.exc.HTTPBadRequest,
                               self.controller.update, self.req, service.uuid,
                               body={'disabled_reason': 'missing status'})
        self.assertEqual("No updates were requested. Fields 'status' or "
                         "'forced_down' should be specified.",
                         six.text_type(ex))


class ServicesCellsTestV21(test.TestCase):

    def setUp(self):
        super(ServicesCellsTestV21, self).setUp()

        host_api = compute.cells_api.HostAPI()

        self._set_up_controller()
        self.controller.host_api = host_api

        self.useFixture(utils_fixture.TimeFixture(fake_utcnow()))

        services_list = []
        for service in fake_services_list:
            service = service.copy()
            del service['version']
            service_obj = objects.Service(**service)
            service_proxy = cells_utils.ServiceProxy(service_obj, 'cell1')
            services_list.append(service_proxy)

        host_api.cells_rpcapi.service_get_all = (
            mock.Mock(side_effect=fake_service_get_all(services_list)))

    def _set_up_controller(self):
        self.controller = services_v21.ServiceController()

    def _process_out(self, res_dict):
        for res in res_dict['services']:
            res.pop('disabled_reason')

    def test_services_detail(self):
        req = FakeRequest()
        res_dict = self.controller.index(req)
        utc = iso8601.iso8601.Utc()
        response = {'services': [
                    {'id': 'cell1@1',
                     'binary': 'nova-scheduler',
                     'host': 'cell1@host1',
                     'zone': 'internal',
                     'status': 'disabled',
                     'state': 'up',
                     'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 2,
                                                     tzinfo=utc)},
                    {'id': 'cell1@2',
                     'binary': 'nova-compute',
                     'host': 'cell1@host1',
                     'zone': 'nova',
                     'status': 'disabled',
                     'state': 'up',
                     'updated_at': datetime.datetime(2012, 10, 29, 13, 42, 5,
                                                     tzinfo=utc)},
                    {'id': 'cell1@3',
                     'binary': 'nova-scheduler',
                     'host': 'cell1@host2',
                     'zone': 'internal',
                     'status': 'enabled',
                     'state': 'down',
                     'updated_at': datetime.datetime(2012, 9, 19, 6, 55, 34,
                                                     tzinfo=utc)},
                    {'id': 'cell1@4',
                     'binary': 'nova-compute',
                     'host': 'cell1@host2',
                     'zone': 'nova',
                     'status': 'disabled',
                     'state': 'down',
                     'updated_at': datetime.datetime(2012, 9, 18, 8, 3, 38,
                                                     tzinfo=utc)}]}
        self._process_out(res_dict)
        self.assertEqual(response, res_dict)


class ServicesPolicyEnforcementV21(test.NoDBTestCase):

    def setUp(self):
        super(ServicesPolicyEnforcementV21, self).setUp()
        self.controller = services_v21.ServiceController()
        self.req = fakes.HTTPRequest.blank('')

    def test_update_policy_failed(self):
        rule_name = "os_compute_api:os-services"
        self.policy.set_rules({rule_name: "project_id:non_fake"})
        exc = self.assertRaises(
            exception.PolicyNotAuthorized,
            self.controller.update, self.req, fakes.FAKE_UUID,
            body={'host': 'host1',
                  'binary': 'nova-compute'})
        self.assertEqual(
            "Policy doesn't allow %s to be performed." % rule_name,
            exc.format_message())

    def test_delete_policy_failed(self):
        rule_name = "os_compute_api:os-services"
        self.policy.set_rules({rule_name: "project_id:non_fake"})
        exc = self.assertRaises(
            exception.PolicyNotAuthorized,
            self.controller.delete, self.req, fakes.FAKE_UUID)
        self.assertEqual(
            "Policy doesn't allow %s to be performed." % rule_name,
            exc.format_message())

    def test_index_policy_failed(self):
        rule_name = "os_compute_api:os-services"
        self.policy.set_rules({rule_name: "project_id:non_fake"})
        exc = self.assertRaises(
            exception.PolicyNotAuthorized,
            self.controller.index, self.req)
        self.assertEqual(
            "Policy doesn't allow %s to be performed." % rule_name,
            exc.format_message())
