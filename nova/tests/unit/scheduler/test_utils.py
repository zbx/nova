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

import mock

from nova import objects
from nova.scheduler import utils
from nova import test


class TestUtils(test.NoDBTestCase):

    def _test_resources_from_request_spec(self, flavor, expected):
        fake_spec = objects.RequestSpec(flavor=flavor)
        resources = utils.resources_from_request_spec(fake_spec)
        self.assertEqual(expected, resources)

    def test_resources_from_request_spec(self):
        flavor = objects.Flavor(vcpus=1,
                                memory_mb=1024,
                                root_gb=10,
                                ephemeral_gb=5,
                                swap=0)
        expected_resources = {'VCPU': 1,
                              'MEMORY_MB': 1024,
                              'DISK_GB': 15}
        self._test_resources_from_request_spec(flavor, expected_resources)

    def test_resources_from_request_spec_with_no_disk(self):
        flavor = objects.Flavor(vcpus=1,
                                memory_mb=1024,
                                root_gb=0,
                                ephemeral_gb=0,
                                swap=0)
        expected_resources = {'VCPU': 1,
                              'MEMORY_MB': 1024}
        self._test_resources_from_request_spec(flavor, expected_resources)

    def test_get_resources_from_request_spec_custom_resource_class(self):
        flavor = objects.Flavor(vcpus=1,
                                memory_mb=1024,
                                root_gb=10,
                                ephemeral_gb=5,
                                swap=0,
                                extra_specs={"resources:CUSTOM_TEST_CLASS": 1})
        expected_resources = {"VCPU": 1,
                              "MEMORY_MB": 1024,
                              "DISK_GB": 15,
                              "CUSTOM_TEST_CLASS": 1}
        self._test_resources_from_request_spec(flavor, expected_resources)

    def test_get_resources_from_request_spec_override_flavor_amounts(self):
        flavor = objects.Flavor(vcpus=1,
                                memory_mb=1024,
                                root_gb=10,
                                ephemeral_gb=5,
                                swap=0,
                                extra_specs={
                                    "resources:VCPU": 99,
                                    "resources:MEMORY_MB": 99,
                                    "resources:DISK_GB": 99})
        expected_resources = {"VCPU": 99,
                              "MEMORY_MB": 99,
                              "DISK_GB": 99}
        self._test_resources_from_request_spec(flavor, expected_resources)

    def test_get_resources_from_request_spec_remove_flavor_amounts(self):
        flavor = objects.Flavor(vcpus=1,
                                memory_mb=1024,
                                root_gb=10,
                                ephemeral_gb=5,
                                swap=0,
                                extra_specs={
                                    "resources:VCPU": 0,
                                    "resources:DISK_GB": 0})
        expected_resources = {"MEMORY_MB": 1024}
        self._test_resources_from_request_spec(flavor, expected_resources)

    def test_get_resources_from_request_spec_bad_std_resource_class(self):
        flavor = objects.Flavor(vcpus=1,
                                memory_mb=1024,
                                root_gb=10,
                                ephemeral_gb=5,
                                swap=0,
                                extra_specs={
                                    "resources:DOESNT_EXIST": 0})
        fake_spec = objects.RequestSpec(flavor=flavor)
        with mock.patch("nova.scheduler.utils.LOG.warning") as mock_log:
            utils.resources_from_request_spec(fake_spec)
            mock_log.assert_called_once()
            args = mock_log.call_args[0]
            self.assertEqual(args[0], "Received an invalid ResourceClass "
                    "'%(key)s' in extra_specs.")
            self.assertEqual(args[1], {"key": "DOESNT_EXIST"})

    def test_get_resources_from_request_spec_bad_value(self):
        flavor = objects.Flavor(vcpus=1,
                                memory_mb=1024,
                                root_gb=10,
                                ephemeral_gb=5,
                                swap=0,
                                extra_specs={
                                    "resources:MEMORY_MB": "bogus"})
        fake_spec = objects.RequestSpec(flavor=flavor)
        with mock.patch("nova.scheduler.utils.LOG.warning") as mock_log:
            utils.resources_from_request_spec(fake_spec)
            mock_log.assert_called_once()

    def test_get_resources_from_request_spec_zero_cust_amt(self):
        flavor = objects.Flavor(vcpus=1,
                                memory_mb=1024,
                                root_gb=10,
                                ephemeral_gb=5,
                                swap=0,
                                extra_specs={
                                    "resources:CUSTOM_TEST_CLASS": 0})
        fake_spec = objects.RequestSpec(flavor=flavor)
        with mock.patch("nova.scheduler.utils.LOG.warning") as mock_log:
            utils.resources_from_request_spec(fake_spec)
            mock_log.assert_called_once()

    @mock.patch("nova.scheduler.utils._process_extra_specs")
    def test_process_extra_specs_called(self, mock_proc):
        flavor = objects.Flavor(vcpus=1,
                                memory_mb=1024,
                                root_gb=10,
                                ephemeral_gb=5,
                                swap=0,
                                extra_specs={"resources:CUSTOM_TEST_CLASS": 1})
        fake_spec = objects.RequestSpec(flavor=flavor)
        utils.resources_from_request_spec(fake_spec)
        mock_proc.assert_called_once()

    @mock.patch("nova.scheduler.utils._process_extra_specs")
    def test_process_extra_specs_not_called(self, mock_proc):
        flavor = objects.Flavor(vcpus=1,
                                memory_mb=1024,
                                root_gb=10,
                                ephemeral_gb=5,
                                swap=0)
        fake_spec = objects.RequestSpec(flavor=flavor)
        utils.resources_from_request_spec(fake_spec)
        mock_proc.assert_not_called()

    def test_process_missing_extra_specs_value(self):
        flavor = objects.Flavor(
                vcpus=1,
                memory_mb=1024,
                root_gb=10,
                ephemeral_gb=5,
                swap=0,
                extra_specs={"resources:CUSTOM_TEST_CLASS": ""})
        fake_spec = objects.RequestSpec(flavor=flavor)
        utils.resources_from_request_spec(fake_spec)

    @mock.patch('nova.compute.utils.is_volume_backed_instance',
                return_value=False)
    def test_resources_from_flavor_no_bfv(self, mock_is_bfv):
        flavor = objects.Flavor(vcpus=1, memory_mb=1024, root_gb=10,
                                ephemeral_gb=5, swap=1024,
                                extra_specs={})
        instance = objects.Instance()
        expected = {
            'VCPU': 1,
            'MEMORY_MB': 1024,
            'DISK_GB': 16,
        }
        actual = utils.resources_from_flavor(instance, flavor)
        self.assertEqual(expected, actual)

    @mock.patch('nova.compute.utils.is_volume_backed_instance',
                return_value=True)
    def test_resources_from_flavor_bfv(self, mock_is_bfv):
        flavor = objects.Flavor(vcpus=1, memory_mb=1024, root_gb=10,
                                ephemeral_gb=5, swap=1024,
                                extra_specs={})
        instance = objects.Instance()
        expected = {
            'VCPU': 1,
            'MEMORY_MB': 1024,
            'DISK_GB': 6,  # No root disk...
        }
        actual = utils.resources_from_flavor(instance, flavor)
        self.assertEqual(expected, actual)

    @mock.patch('nova.compute.utils.is_volume_backed_instance',
                return_value=False)
    def test_resources_from_flavor_with_override(self, mock_is_bfv):
        flavor = objects.Flavor(vcpus=1, memory_mb=1024, root_gb=10,
                                ephemeral_gb=5, swap=1024,
                                extra_specs={'resources:VCPU': '2'})
        instance = objects.Instance()
        expected = {
            'VCPU': 2,
            'MEMORY_MB': 1024,
            'DISK_GB': 16,
        }
        actual = utils.resources_from_flavor(instance, flavor)
        self.assertEqual(expected, actual)

    def test_merge_resources(self):
        resources = {
            'VCPU': 1, 'MEMORY_MB': 1024,
        }
        new_resources = {
            'VCPU': 2, 'MEMORY_MB': 2048, 'CUSTOM_FOO': 1,
        }
        doubled = {
            'VCPU': 3, 'MEMORY_MB': 3072, 'CUSTOM_FOO': 1,
        }
        saved_orig = dict(resources)
        utils.merge_resources(resources, new_resources)
        # Check to see that we've doubled our resources
        self.assertEqual(doubled, resources)
        # and then removed those doubled resources
        utils.merge_resources(resources, saved_orig, -1)
        self.assertEqual(new_resources, resources)

    def test_merge_resources_zero(self):
        """Test 0 value resources are ignored."""
        resources = {
            'VCPU': 1, 'MEMORY_MB': 1024,
        }
        new_resources = {
            'VCPU': 2, 'MEMORY_MB': 2048, 'DISK_GB': 0,
        }
        # The result should not include the zero valued resource.
        doubled = {
            'VCPU': 3, 'MEMORY_MB': 3072,
        }
        utils.merge_resources(resources, new_resources)
        self.assertEqual(doubled, resources)

    def test_merge_resources_original_zeroes(self):
        """Confirm that merging that result in a zero in the original
        excludes the zeroed resource class.
        """
        resources = {
            'VCPU': 3, 'MEMORY_MB': 1023, 'DISK_GB': 1,
        }
        new_resources = {
            'VCPU': 1, 'MEMORY_MB': 512, 'DISK_GB': 1,
        }
        merged = {
            'VCPU': 2, 'MEMORY_MB': 511,
        }
        utils.merge_resources(resources, new_resources, -1)
        self.assertEqual(merged, resources)
