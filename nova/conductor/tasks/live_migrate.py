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

from oslo_log import log as logging
import oslo_messaging as messaging
import six

from nova.compute import power_state
from nova.conductor.tasks import base
import nova.conf
from nova import exception
from nova.i18n import _
from nova import objects
from nova.scheduler import utils as scheduler_utils
from nova import utils

LOG = logging.getLogger(__name__)
CONF = nova.conf.CONF


class LiveMigrationTask(base.TaskBase):
    def __init__(self, context, instance, destination,
                 block_migration, disk_over_commit, migration, compute_rpcapi,
                 servicegroup_api, scheduler_client, request_spec=None):
        super(LiveMigrationTask, self).__init__(context, instance)
        self.destination = destination
        self.block_migration = block_migration
        self.disk_over_commit = disk_over_commit
        self.migration = migration
        self.source = instance.host
        self.migrate_data = None

        self.compute_rpcapi = compute_rpcapi
        self.servicegroup_api = servicegroup_api
        self.scheduler_client = scheduler_client
        self.request_spec = request_spec

    def _execute(self):
        self._check_instance_is_active()
        self._check_host_is_up(self.source)

        if not self.destination:
            # Either no host was specified in the API request and the user
            # wants the scheduler to pick a destination host, or a host was
            # specified but is not forcing it, so they want the scheduler
            # filters to run on the specified host, like a scheduler hint.
            self.destination = self._find_destination()
            self.migration.dest_compute = self.destination
            self.migration.save()
        else:
            # This is the case that the user specified the 'force' flag when
            # live migrating with a specific destination host so the scheduler
            # is bypassed. There are still some minimal checks performed here
            # though.
            source_node, dest_node = self._check_requested_destination()
            # Now that we're semi-confident in the force specified host, we
            # need to copy the source compute node allocations in Placement
            # to the destination compute node. Normally select_destinations()
            # in the scheduler would do this for us, but when forcing the
            # target host we don't call the scheduler.
            # TODO(mriedem): In Queens, call select_destinations() with a
            # skip_filters=True flag so the scheduler does the work of claiming
            # resources on the destination in Placement but still bypass the
            # scheduler filters, which honors the 'force' flag in the API.
            self._claim_resources_on_destination(source_node, dest_node)

        # TODO(johngarbutt) need to move complexity out of compute manager
        # TODO(johngarbutt) disk_over_commit?
        return self.compute_rpcapi.live_migration(self.context,
                host=self.source,
                instance=self.instance,
                dest=self.destination,
                block_migration=self.block_migration,
                migration=self.migration,
                migrate_data=self.migrate_data)

    def rollback(self):
        # TODO(johngarbutt) need to implement the clean up operation
        # but this will make sense only once we pull in the compute
        # calls, since this class currently makes no state changes,
        # except to call the compute method, that has no matching
        # rollback call right now.
        pass

    def _check_instance_is_active(self):
        if self.instance.power_state not in (power_state.RUNNING,
                                             power_state.PAUSED):
            raise exception.InstanceInvalidState(
                    instance_uuid=self.instance.uuid,
                    attr='power_state',
                    state=self.instance.power_state,
                    method='live migrate')

    def _check_host_is_up(self, host):
        service = objects.Service.get_by_compute_host(self.context, host)

        if not self.servicegroup_api.service_is_up(service):
            raise exception.ComputeServiceUnavailable(host=host)

    def _check_requested_destination(self):
        """Performs basic pre-live migration checks for the forced host.

        :returns: tuple of (source ComputeNode, destination ComputeNode)
        """
        self._check_destination_is_not_source()
        self._check_host_is_up(self.destination)
        self._check_destination_has_enough_memory()
        source_node, dest_node = self._check_compatible_with_source_hypervisor(
            self.destination)
        self._call_livem_checks_on_host(self.destination)
        # Make sure the forced destination host is in the same cell that the
        # instance currently lives in.
        # NOTE(mriedem): This can go away if/when the forced destination host
        # case calls select_destinations.
        source_cell_mapping = self._get_source_cell_mapping()
        dest_cell_mapping = self._get_destination_cell_mapping()
        if source_cell_mapping.uuid != dest_cell_mapping.uuid:
            raise exception.MigrationPreCheckError(
                reason=(_('Unable to force live migrate instance %s '
                          'across cells.') % self.instance.uuid))
        return source_node, dest_node

    def _claim_resources_on_destination(self, source_node, dest_node):
        """Copies allocations from source node to dest node in Placement

        :param source_node: source ComputeNode where the instance currently
                            lives
        :param dest_node: destination ComputeNode where the instance is being
                          forced to live migrate.
        """
        reportclient = self.scheduler_client.reportclient
        # Get the current allocations for the source node and the instance.
        source_node_allocations = reportclient.get_allocations_for_instance(
            source_node.uuid, self.instance)
        if source_node_allocations:
            # Generate an allocation request for the destination node.
            alloc_request = {
                'allocations': [
                    {
                        'resource_provider': {
                            'uuid': dest_node.uuid
                        },
                        'resources': source_node_allocations
                    }
                ]
            }
            # The claim_resources method will check for existing allocations
            # for the instance and effectively "double up" the allocations for
            # both the source and destination node. That's why when requesting
            # allocations for resources on the destination node before we live
            # migrate, we use the existing resource allocations from the
            # source node.
            if reportclient.claim_resources(
                    self.instance.uuid, alloc_request,
                    self.instance.project_id, self.instance.user_id):
                LOG.debug('Instance allocations successfully created on '
                          'destination node %(dest)s: %(alloc_request)s',
                          {'dest': dest_node.uuid,
                           'alloc_request': alloc_request},
                          instance=self.instance)
            else:
                # We have to fail even though the user requested that we force
                # the host. This is because we need Placement to have an
                # accurate reflection of what's allocated on all nodes so the
                # scheduler can make accurate decisions about which nodes have
                # capacity for building an instance. We also cannot rely on the
                # resource tracker in the compute service automatically healing
                # the allocations since that code is going away in Queens.
                reason = (_('Unable to migrate instance %(instance_uuid)s to '
                            'host %(host)s. There is not enough capacity on '
                            'the host for the instance.') %
                          {'instance_uuid': self.instance.uuid,
                           'host': self.destination})
                raise exception.MigrationPreCheckError(reason=reason)
        else:
            # This shouldn't happen, but it could be a case where there are
            # older (Ocata) computes still so the existing allocations are
            # getting overwritten by the update_available_resource periodic
            # task in the compute service.
            # TODO(mriedem): Make this an error when the auto-heal
            # compatibility code in the resource tracker is removed.
            LOG.warning('No instance allocations found for source node '
                        '%(source)s in Placement. Not creating allocations '
                        'for destination node %(dest)s and assuming the '
                        'compute service will heal the allocations.',
                        {'source': source_node.uuid, 'dest': dest_node.uuid},
                        instance=self.instance)

    def _check_destination_is_not_source(self):
        if self.destination == self.source:
            raise exception.UnableToMigrateToSelf(
                    instance_id=self.instance.uuid, host=self.destination)

    def _check_destination_has_enough_memory(self):
        # TODO(mriedem): This method can be removed when the forced host
        # scenario is calling select_destinations() in the scheduler because
        # Placement will be used to filter allocation candidates by MEMORY_MB.
        # We likely can't remove it until the CachingScheduler is gone though
        # since the CachingScheduler does not use Placement.
        compute = self._get_compute_info(self.destination)
        free_ram_mb = compute.free_ram_mb
        total_ram_mb = compute.memory_mb
        mem_inst = self.instance.memory_mb
        # NOTE(sbauza): Now the ComputeNode object reports an allocation ratio
        # that can be provided by the compute_node if new or by the controller
        ram_ratio = compute.ram_allocation_ratio

        # NOTE(sbauza): Mimic the RAMFilter logic in order to have the same
        # ram validation
        avail = total_ram_mb * ram_ratio - (total_ram_mb - free_ram_mb)
        if not mem_inst or avail <= mem_inst:
            instance_uuid = self.instance.uuid
            dest = self.destination
            reason = _("Unable to migrate %(instance_uuid)s to %(dest)s: "
                       "Lack of memory(host:%(avail)s <= "
                       "instance:%(mem_inst)s)")
            raise exception.MigrationPreCheckError(reason=reason % dict(
                    instance_uuid=instance_uuid, dest=dest, avail=avail,
                    mem_inst=mem_inst))

    def _get_compute_info(self, host):
        return objects.ComputeNode.get_first_node_by_host_for_old_compat(
            self.context, host)

    def _check_compatible_with_source_hypervisor(self, destination):
        source_info = self._get_compute_info(self.source)
        destination_info = self._get_compute_info(destination)

        source_type = source_info.hypervisor_type
        destination_type = destination_info.hypervisor_type
        if source_type != destination_type:
            raise exception.InvalidHypervisorType()

        source_version = source_info.hypervisor_version
        destination_version = destination_info.hypervisor_version
        if source_version > destination_version:
            raise exception.DestinationHypervisorTooOld()
        return source_info, destination_info

    def _call_livem_checks_on_host(self, destination):
        try:
            self.migrate_data = self.compute_rpcapi.\
                check_can_live_migrate_destination(self.context, self.instance,
                    destination, self.block_migration, self.disk_over_commit)
        except messaging.MessagingTimeout:
            msg = _("Timeout while checking if we can live migrate to host: "
                    "%s") % destination
            raise exception.MigrationPreCheckError(msg)

    def _get_source_cell_mapping(self):
        """Returns the CellMapping for the cell in which the instance lives

        :returns: nova.objects.CellMapping record for the cell where
            the instance currently lives.
        :raises: MigrationPreCheckError - in case a mapping is not found
        """
        try:
            return objects.InstanceMapping.get_by_instance_uuid(
                self.context, self.instance.uuid).cell_mapping
        except exception.InstanceMappingNotFound:
            raise exception.MigrationPreCheckError(
                reason=(_('Unable to determine in which cell '
                          'instance %s lives.') % self.instance.uuid))

    def _get_destination_cell_mapping(self):
        """Returns the CellMapping for the destination host

        :returns: nova.objects.CellMapping record for the cell where
            the destination host is mapped.
        :raises: MigrationPreCheckError - in case a mapping is not found
        """
        try:
            return objects.HostMapping.get_by_host(
                self.context, self.destination).cell_mapping
        except exception.HostMappingNotFound:
            raise exception.MigrationPreCheckError(
                reason=(_('Unable to determine in which cell '
                          'destination host %s lives.') % self.destination))

    def _find_destination(self):
        # TODO(johngarbutt) this retry loop should be shared
        attempted_hosts = [self.source]
        image = utils.get_image_from_system_metadata(
            self.instance.system_metadata)
        filter_properties = {'ignore_hosts': attempted_hosts}
        if not self.request_spec:
            # NOTE(sbauza): We were unable to find an original RequestSpec
            # object - probably because the instance is old.
            # We need to mock that the old way
            request_spec = objects.RequestSpec.from_components(
                self.context, self.instance.uuid, image,
                self.instance.flavor, self.instance.numa_topology,
                self.instance.pci_requests,
                filter_properties, None, self.instance.availability_zone
            )
        else:
            request_spec = self.request_spec
            # NOTE(sbauza): Force_hosts/nodes needs to be reset
            # if we want to make sure that the next destination
            # is not forced to be the original host
            request_spec.reset_forced_destinations()
        scheduler_utils.setup_instance_group(self.context, request_spec)

        # We currently only support live migrating to hosts in the same
        # cell that the instance lives in, so we need to tell the scheduler
        # to limit the applicable hosts based on cell.
        cell_mapping = self._get_source_cell_mapping()
        LOG.debug('Requesting cell %(cell)s while live migrating',
                  {'cell': cell_mapping.identity},
                  instance=self.instance)
        if ('requested_destination' in request_spec and
                request_spec.requested_destination):
            request_spec.requested_destination.cell = cell_mapping
        else:
            request_spec.requested_destination = objects.Destination(
                cell=cell_mapping)

        host = None
        while host is None:
            self._check_not_over_max_retries(attempted_hosts)
            request_spec.ignore_hosts = attempted_hosts
            try:
                host = self.scheduler_client.select_destinations(self.context,
                        request_spec, [self.instance.uuid])[0]['host']
            except messaging.RemoteError as ex:
                # TODO(ShaoHe Feng) There maybe multi-scheduler, and the
                # scheduling algorithm is R-R, we can let other scheduler try.
                # Note(ShaoHe Feng) There are types of RemoteError, such as
                # NoSuchMethod, UnsupportedVersion, we can distinguish it by
                # ex.exc_type.
                raise exception.MigrationSchedulerRPCError(
                    reason=six.text_type(ex))
            try:
                self._check_compatible_with_source_hypervisor(host)
                self._call_livem_checks_on_host(host)
            except (exception.Invalid, exception.MigrationPreCheckError) as e:
                LOG.debug("Skipping host: %(host)s because: %(e)s",
                    {"host": host, "e": e})
                attempted_hosts.append(host)
                host = None
        return host

    def _check_not_over_max_retries(self, attempted_hosts):
        if CONF.migrate_max_retries == -1:
            return

        retries = len(attempted_hosts) - 1
        if retries > CONF.migrate_max_retries:
            if self.migration:
                self.migration.status = 'failed'
                self.migration.save()
            msg = (_('Exceeded max scheduling retries %(max_retries)d for '
                     'instance %(instance_uuid)s during live migration')
                   % {'max_retries': retries,
                      'instance_uuid': self.instance.uuid})
            raise exception.MaxRetriesExceeded(reason=msg)
