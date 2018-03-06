# Copyright (c) 2015 EMC Corporation
# All Rights Reserved
#
# This software contains the intellectual property of EMC Corporation
# or is licensed to EMC Corporation from third parties.  Use of this
# software and the intellectual property contained therein is expressly
# limited to the terms and conditions of the License Agreement under which
# it is provided by or on behalf of EMC.
#

from six.moves import range
import time

from oslo_concurrency import processutils
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import excutils
from oslo_utils import units

from nova import exception
from nova.i18n import _
from nova import utils
from nova.virt import images
from nova.virt.libvirt import utils as libvirt_utils

try:
    import siolib
    from siolib import scaleio
    from siolib import utilities
except ImportError:
    siolib = None

LOG = logging.getLogger(__name__)
CONF = cfg.CONF

if siolib:
    CONF.register_group(siolib.SIOGROUP)
    CONF.register_opts(siolib.SIOOPTS, siolib.SIOGROUP)

VOLSIZE_MULTIPLE_GB = 8
MAX_VOL_NAME_LENGTH = 31
PROTECTION_DOMAIN_KEY = 'sio:pd_name'
STORAGE_POOL_KEY = 'sio:sp_name'
PROVISIONING_TYPE_KEY = 'sio:provisioning_type'
PROVISIONING_TYPES_MAP = {'thin': 'ThinProvisioned',
                          'thick': 'ThickProvisioned'}


def verify_volume_size(requested_size):
    """Verify that ScaleIO can have a volume with specified size.

    ScaleIO creates volumes in multiples of 8.
    :param requested_size: Size in bytes
    :return: True if the size fit to ScaleIO, False otherwise
    """
    if (not requested_size or
            requested_size % (units.Gi * VOLSIZE_MULTIPLE_GB)):
        raise exception.NovaException(
            _('Invalid disk size %s GB for the instance. The correct size '
              'must be multiple of 8 GB. Choose another flavor') %
            (requested_size / float(units.Gi)
             if isinstance(requested_size, int) else
             requested_size))


def choose_volume_size(requested_size):
    """Choose ScaleIO volume size to fit requested size.

    ScaleIO creates volumes in multiples of 8.
    :param requested_size: Size in bytes
    :return: The smallest allowed size in bytes of ScaleIO volume.
    """
    return -(-requested_size / (units.Gi * VOLSIZE_MULTIPLE_GB)) * units.Gi


def get_sio_volume_name(instance, disk_name):
    """Generate ScaleIO volume name for instance disk.

    ScaleIO restricts volume names to be unique, less than 32 symbols,
    consist of alphanumeric symbols only.
    Generated volume names start with a prefix, unique for the instance.
    This allows one to find all instance volumes among all ScaleIO volumes.
    :param instane: instance object
    :param disk_name: disk name (i.e. disk, disk.local, etc)
    :return: The generated name
    """
    sio_name = utilities.encode_base64(instance.uuid)
    if disk_name.startswith('disk.'):
        sio_name += disk_name[len('disk.'):]
    elif disk_name != 'disk':
        sio_name += disk_name
    if len(sio_name) > MAX_VOL_NAME_LENGTH:
        raise RuntimeError(_("Disk name '%s' is too long for ScaleIO") %
                           disk_name)
    return sio_name


def get_sio_snapshot_name(volume_name, snapshot_name):
    if snapshot_name == libvirt_utils.RESIZE_SNAPSHOT_NAME:
        return volume_name + '/~'
    sio_name = '%s/%s' % (volume_name, snapshot_name)
    if len(sio_name) > MAX_VOL_NAME_LENGTH:
        raise RuntimeError(_("Snapshot name '%s' is too long for ScaleIO") %
                           snapshot_name)
    return sio_name


def is_sio_volume_rescuer(volume_name):
    return volume_name.endswith('rescue')


def probe_partitions(device_path, run_as_root=False):
    """Method called to trigger OS and inform the OS of partition table changes

    When ScaleIO maps a volume, there is a delay in the time the OS trigger
    probes for partitions. This method will force that trigger so the OS
    will see the device partitions
    :param device_path: Full device path to probe
    :return: Nothing
    """
    try:
        utils.execute('partprobe', device_path, run_as_root=run_as_root)
    except processutils.ProcessExecutionError as exc:
        LOG.debug("Probing the device partitions has failed. (%s)", exc)


class SIODriver(object):
    """Backend image type driver for ScaleIO"""

    pd_name = None
    sp_name = None

    def __init__(self, extra_specs=None):
        """Initialize ScaleIODriver object.

        :param extra_specs: A dict of instance flavor extra specs
        :return: Nothing
        """
        if siolib is None:
            raise RuntimeError(_('ScaleIO python libraries not found'))

        if extra_specs:
            self.pd_name = extra_specs.get(PROTECTION_DOMAIN_KEY)
            if self.pd_name:
                self.pd_name = self.pd_name.encode('utf8')
            self.sp_name = extra_specs.get(STORAGE_POOL_KEY)
            if self.sp_name:
                self.sp_name = self.sp_name.encode('utf8')

        # IOCTL reference to ScaleIO API python library
        self.ioctx = scaleio.ScaleIO(conf_filepath="/etc/kolla/nova-compute/nova.conf",pd_name="default",
                                     sp_name="default",
                                     conf=CONF)

    def get_pool_info(self):
        """Return the total storage pool info."""

        used_bytes, total_bytes, free_bytes = (
            self.ioctx.storagepool_size(by_sds=True))
        return {'total': total_bytes,
                'free': free_bytes,
                'used': used_bytes}

    def create_volume(self, name, size, extra_specs):
        """Create a ScaleIO volume.

        :param name: Volume name to use
        :param size: Size of volume to create
        :param extra_specs: A dict of instance flavor extra specs
        :return: Nothing
        """
        ptype = extra_specs.get(PROVISIONING_TYPE_KEY)
        ptype = PROVISIONING_TYPES_MAP.get(ptype, ptype)
        # NOTE(ft): siolib does not raise an exception if the volume
        # already exists
        self.ioctx.create_volume(name, volume_size_gb=(size / units.Gi),
                                 provisioning_type=ptype)

    def remove_volume(self, name, ignore_mappings=False):
        """Deletes (removes) a ScaleIO volume.

        Removal of a volume erases all the data on the corresponding volume.

        :param name: String ScaleIO volume name to remove
        :param ignore_mappings: Remove even if the volume is mapped to SDCs
        :return: Nothing
        """
        vol_id = self.ioctx.get_volumeid(name)
        if vol_id:
            self.ioctx.delete_volume(vol_id, unmap_on_delete=ignore_mappings)

    def map_volume(self, name):
        """Connect to ScaleIO volume.

        Map ScaleIO volume to local block device

        :param name: String ScaleIO volume name to attach
        :return: Local attached volume path
        """
        vol_id = self.get_volume_id(name)
        self.ioctx.attach_volume(vol_id)
        path = self.ioctx.get_volumepath(vol_id)
        # NOTE(ft): siolib does not raise an exception if it failed to attach
        # the volume
        if not path:
            raise RuntimeError(_('Failed to attach disk volume %s') % name)

        return path

    def unmap_volume(self, name):
        """Disconnect from ScaleIO volume.

        Unmap ScaleIO volume from local block device

        :param name: String ScaleIO volume name to detach
        :return: Nothing
        """
        vol_id = self.ioctx.get_volumeid(name)
        if vol_id:
            self.ioctx.detach_volume(vol_id)

    def check_volume_exists(self, name):
        """Check if ScaleIO volume exists.

        :param name: String ScaleIO volume name to check
        :return: True if the volume exists, False otherwise
        """
        return bool(self.ioctx.get_volumeid(name))

    def get_volume_id(self, name):
        """Return the ScaleIO volume ID

        :param name: String ScaleIO volume name to retrieve id from
        :return: ScaleIO volume id
        """
        vol_id = self.ioctx.get_volumeid(name)
        if not vol_id:
            raise RuntimeError(_('Disk volume %s does not exist') % name)
        return vol_id

    def get_volume_name(self, vol_id):
        """Return the ScaleIO volume name.

        :param vol_id: String ScaleIO volume id to retrieve name from
        :return: ScaleIO volume name
        """
        vol_name = None
        try:
            vol_name = self.ioctx.get_volumename(vol_id)
        except AttributeError:
            # Workaround siolib bug if the volume does not exist
            pass

        if not vol_name:
            raise RuntimeError(_('Disk volume %s does not exist') % vol_id)

        return vol_name

    def get_volume_path(self, name):
        """Return the volume device path location.

        :param name: String ScaleIO volume name to get path information about
        :return: Local attached volume path, None if the volume does not exist
                 or is not connected
        """
        vol_id = self.get_volume_id(name)
        return self.ioctx.get_volumepath(vol_id)

    def get_volume_size(self, name):
        """Return the size of the ScaleIO volume

        :param name: String ScaleIO volume name to get path information about
        :return: Size of ScaleIO volume
        """
        vol_id = self.get_volume_id(name)
        vol_size = self.ioctx.get_volumesize(vol_id)
        return vol_size * units.Ki

    def import_image(self, source, dest):
        """Import glance image onto actual ScaleIO block device.

        :param source: Glance image source
        :param dest: Target ScaleIO block device
        :return: Nothing
        """
        info = images.qemu_img_info(source)
        images.convert_image(source, dest, info.file_format, 'raw',
                             run_as_root=True)
        # trigger OS probe of partition devices
        probe_partitions(device_path=dest, run_as_root=True)

    def export_image(self, source, dest, out_format):
        """Export ScaleIO volume.

        :param source: Local attached ScaleIO volume path to export from
        :param dest: Target path
        :param out_format: Output format (raw, qcow2, etc)
        :return: Nothing
        """
        images.convert_image(source, dest, 'raw', out_format, run_as_root=True)

    def extend_volume(self, name, new_size, extra_specs, orig_extra_specs):
        """Extend the size of a volume, honoring extra specs.

        This method is used primarily with openstack resize operation

        :param name: String ScaleIO volume name to extend
        :param new_size: Size of the volume to extend to
        :param extra_specs: A dict of instance flavor extra specs
        :param orig_extra_specs: A dict of original instance flavor extra specs
        :return: Nothing
        """
        if (extra_specs.get(PROTECTION_DOMAIN_KEY) ==
                orig_extra_specs.get(PROTECTION_DOMAIN_KEY) and
                extra_specs.get(STORAGE_POOL_KEY) ==
                orig_extra_specs.get(STORAGE_POOL_KEY)):
            if self.get_volume_size(name) == new_size:
                # extending is not required
                return
            vol_id = self.get_volume_id(name)
            self.ioctx.extend_volume(vol_id, new_size / units.Gi)
            # NOTE(ft): siolib does not raise an exception if it cannot extend
            # the volume
            if self.get_volume_size(name) != new_size:
                raise RuntimeError(_('Failed to extend disk volume %s') % name)
            # NOTE(ft): refresh size in OS
            vol_path = self.ioctx.get_volumepath(vol_id)
            if vol_path:
                # TODO(ft): this is a workaround to do not use drv_cfg to
                # refresh the size. To use drv_cfg we need to update rootwraps
                # filters, which requires changes for install tools (puppets)
                # as well. Currently we want to avoid this.
                self.ioctx.detach_volume(vol_id)
                for _tries in range(5):
                    vol_path = self.ioctx.get_volumepath(vol_id)
                    if not vol_path:
                        break
                    time.sleep(3)
                self.map_volume(name)
        else:
            tmp_name = name + '/#'
            self.create_volume(tmp_name, new_size, extra_specs)
            try:
                new_path = self.map_volume(tmp_name)
                vol_id = self.get_volume_id(name)
                old_path = self.ioctx.get_volumepath(vol_id)
                if old_path:
                    mapped = True
                else:
                    mapped = False
                    self.ioctx.attach_volume(vol_id)
                    old_path = self.ioctx.get_volumepath(vol_id)
                    if not old_path:
                        raise RuntimeError(
                            _('Failed to attach disk volume %s') % name)
                utils.execute('dd',
                              'if=%s' % old_path,
                              'of=%s' % new_path,
                              'bs=1M',
                              'iflag=direct',
                              run_as_root=True)
                self.remove_volume(name, ignore_mappings=True)
                if not mapped:
                    self.unmap_volume(tmp_name)
                new_id = self.get_volume_id(tmp_name)
                self.ioctx.rename_volume(new_id, name)
            except Exception:
                with excutils.save_and_reraise_exception():
                    self.remove_volume(tmp_name, ignore_mappings=True)

    def snapshot_volume(self, name, snapshot_name):
        """Snapshot a volume.

        :param name: String ScaleIO volume name to make a snapshot
        :param snapshot_name: String ScaleIO snapshot name to create
        :return: Nothing
        """
        vol_id = self.get_volume_id(name)
        snap_gid, _vol_list = self.ioctx.snapshot_volume(snapshot_name, vol_id)
        # NOTE(ft): siolib does not raise an exception if it cannot create
        # the snapshot
        if not snap_gid:
            if self.check_volume_exists(snapshot_name):
                self.remove_volume(snapshot_name, ignore_mappings=True)
                (snap_gid,
                 _vol_list) = self.ioctx.snapshot_volume(snapshot_name, vol_id)
                if snap_gid:
                    return
            raise RuntimeError(_('Failed to create snapshot of disk volume %s')
                               % name)

    def rollback_to_snapshot(self, name, snapshot_name):
        """Rollback a snapshot.

        :param name: String ScaleIO volume name to rollback to a snapshot
        :param snapshot_name: String ScaleIO snapshot name to rollback to
        :return: Nothing
        """
        snap_id = self.get_volume_id(snapshot_name)
        self.remove_volume(name, ignore_mappings=True)
        self.ioctx.rename_volume(snap_id, name)
        if not self.check_volume_exists(name):
            raise RuntimeError(_('Failed to rename snapshot %(snapshot)s '
                                 'to disk volume %(disk)s') %
                               {'disk': name,
                                'snapshot_name': snapshot_name})
        self.map_volume(name)

    def map_volumes(self, instance):
        """Map all instance volumes to its compute host.

        :param intance: Instance object
        :return: Nothing
        """
        volumes = self.ioctx.list_volume_names()
        prefix = utilities.encode_base64(instance.uuid)
        volumes = (vol for vol in volumes if vol.startswith(prefix))
        for volume in volumes:
            self.map_volume(volume)

    def cleanup_volumes(self, instance, unmap_only=False):
        """Cleanup all instance volumes.

        :param instance: Instance object
        :param unmap_only: Do not remove, only unmap from the instance host
        :return: Nothing
        """
        volumes = self.ioctx.list_volume_names()
        prefix = utilities.encode_base64(instance.uuid)
        volumes = (vol for vol in volumes if vol.startswith(prefix))
        for volume in volumes:
            if unmap_only:
                self.unmap_volume(volume)
            else:
                self.remove_volume(volume, ignore_mappings=True)

    def cleanup_rescue_volumes(self, instance):
        """Cleanup instance volumes used in rescue mode.

        :param instance: Instance object
        :return: Nothing
        """
        # NOTE(ft): We assume that only root disk is recreated in rescue mode.
        # With this assumption the code becomes more simple and fast.
        rescue_name = utilities.encode_base64(instance.uuid) + 'rescue'
        self.remove_volume(rescue_name, ignore_mappings=True)