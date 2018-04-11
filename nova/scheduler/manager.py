# Copyright (c) 2010 OpenStack Foundation
# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
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

"""
Scheduler Service
"""

import collections

from oslo_log import log as logging
import oslo_messaging as messaging
from oslo_serialization import jsonutils
from oslo_service import periodic_task
from stevedore import driver

import nova.conf
from nova import exception
from nova.i18n import _LI
from nova import manager
from nova import objects
from nova.objects import host_mapping as host_mapping_obj
from nova import quota
from nova.scheduler import client as scheduler_client
from nova.scheduler import utils


LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF

QUOTAS = quota.QUOTAS


def _host_state_obj_to_dict(host_state):
    limits = dict(host_state.limits)
    # The NUMATopologyFilter can set 'numa_topology' in the limits dict
    # to a NUMATopologyLimits object which we need to convert to a primitive
    # before this hits jsonutils.to_primitive(). We only check for that known
    # case specifically as we don't care about handling out of tree filters
    # or drivers injecting non-serializable things in the limits dict.
    if 'numa_topology' in limits:
        limits['numa_topology'] = limits['numa_topology'].obj_to_primitive()
    return {
        'host': host_state.host,
        'nodename': host_state.nodename,
        'limits': limits
    }


class SchedulerManager(manager.Manager):
    """Chooses a host to run instances on."""

    target = messaging.Target(version='4.4')

    _sentinel = object()

    def __init__(self, scheduler_driver=None, *args, **kwargs):
        client = scheduler_client.SchedulerClient()
        self.placement_client = client.reportclient
        if not scheduler_driver:
            scheduler_driver = CONF.scheduler.driver
        self.driver = driver.DriverManager(
                "nova.scheduler.driver",
                scheduler_driver,
                invoke_on_load=True).driver
        super(SchedulerManager, self).__init__(service_name='scheduler',
                                               *args, **kwargs)

    @periodic_task.periodic_task
    def _expire_reservations(self, context):
        QUOTAS.expire(context)

    @periodic_task.periodic_task(
        spacing=CONF.scheduler.discover_hosts_in_cells_interval,
        run_immediately=True)
    def _discover_hosts_in_cells(self, context):
        host_mappings = host_mapping_obj.discover_hosts(context)
        if host_mappings:
            LOG.info(_LI('Discovered %(count)i new hosts: %(hosts)s'),
                     {'count': len(host_mappings),
                      'hosts': ','.join(['%s:%s' % (hm.cell_mapping.name,
                                                    hm.host)
                                         for hm in host_mappings])})

    @periodic_task.periodic_task(spacing=CONF.scheduler.periodic_task_interval,
                                 run_immediately=True)
    def _run_periodic_tasks(self, context):
        self.driver.run_periodic_tasks(context)

    @messaging.expected_exceptions(exception.NoValidHost)
    def select_destinations(self, ctxt,
                            request_spec=None, filter_properties=None,
                            spec_obj=_sentinel, instance_uuids=None):
        """Returns destinations(s) best suited for this RequestSpec.

        The result should be a list of dicts with 'host', 'nodename' and
        'limits' as keys.
        """
        LOG.debug("Starting to schedule for instances: %s", instance_uuids)

        # TODO(sbauza): Change the method signature to only accept a spec_obj
        # argument once API v5 is provided.
        if spec_obj is self._sentinel:
            spec_obj = objects.RequestSpec.from_primitives(ctxt,
                                                           request_spec,
                                                           filter_properties)
        resources = utils.resources_from_request_spec(spec_obj)
        alloc_reqs_by_rp_uuid, provider_summaries = None, None
        if self.driver.USES_ALLOCATION_CANDIDATES:
            res = self.placement_client.get_allocation_candidates(resources)
            if res is None:
                # We have to handle the case that we failed to connect to the
                # Placement service and the safe_connect decorator on
                # get_allocation_candidates returns None.
                alloc_reqs, provider_summaries = None, None
            else:
                alloc_reqs, provider_summaries = res
            if not alloc_reqs:
                LOG.debug("Got no allocation candidates from the Placement "
                          "API. This may be a temporary occurrence as compute "
                          "nodes start up and begin reporting inventory to "
                          "the Placement service.")
                raise exception.NoValidHost(reason="")
            else:
                # Build a dict of lists of allocation requests, keyed by
                # provider UUID, so that when we attempt to claim resources for
                # a host, we can grab an allocation request easily
                alloc_reqs_by_rp_uuid = collections.defaultdict(list)
                for ar in alloc_reqs:
                    for rr in ar['allocations']:
                        rp_uuid = rr['resource_provider']['uuid']
                        alloc_reqs_by_rp_uuid[rp_uuid].append(ar)

        dests = self.driver.select_destinations(ctxt, spec_obj, instance_uuids,
            alloc_reqs_by_rp_uuid, provider_summaries)
        dest_dicts = [_host_state_obj_to_dict(d) for d in dests]
        return jsonutils.to_primitive(dest_dicts)

    def update_aggregates(self, ctxt, aggregates):
        """Updates HostManager internal aggregates information.

        :param aggregates: Aggregate(s) to update
        :type aggregates: :class:`nova.objects.Aggregate`
                          or :class:`nova.objects.AggregateList`
        """
        # NOTE(sbauza): We're dropping the user context now as we don't need it
        self.driver.host_manager.update_aggregates(aggregates)

    def delete_aggregate(self, ctxt, aggregate):
        """Deletes HostManager internal information about a specific aggregate.

        :param aggregate: Aggregate to delete
        :type aggregate: :class:`nova.objects.Aggregate`
        """
        # NOTE(sbauza): We're dropping the user context now as we don't need it
        self.driver.host_manager.delete_aggregate(aggregate)

    def update_instance_info(self, context, host_name, instance_info):
        """Receives information about changes to a host's instances, and
        updates the driver's HostManager with that information.
        """
        self.driver.host_manager.update_instance_info(context, host_name,
                                                      instance_info)

    def delete_instance_info(self, context, host_name, instance_uuid):
        """Receives information about the deletion of one of a host's
        instances, and updates the driver's HostManager with that information.
        """
        self.driver.host_manager.delete_instance_info(context, host_name,
                                                      instance_uuid)

    def sync_instance_info(self, context, host_name, instance_uuids):
        """Receives a sync request from a host, and passes it on to the
        driver's HostManager.
        """
        self.driver.host_manager.sync_instance_info(context, host_name,
                                                    instance_uuids)
