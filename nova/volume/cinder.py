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
Handles all requests relating to volumes + cinder.
"""

import collections
import copy
import functools
import sys

from cinderclient import api_versions as cinder_api_versions
from cinderclient import client as cinder_client
from cinderclient import exceptions as cinder_exception
from keystoneauth1 import exceptions as keystone_exception
from keystoneauth1 import loading as ks_loading
from oslo_log import log as logging
from oslo_utils import encodeutils
from oslo_utils import excutils
from oslo_utils import strutils
import six

from nova import availability_zones as az
import nova.conf
from nova import exception
from nova.i18n import _
from nova.i18n import _LE
from nova.i18n import _LW
from nova import service_auth


CONF = nova.conf.CONF

LOG = logging.getLogger(__name__)

_SESSION = None


def reset_globals():
    """Testing method to reset globals.
    """
    global _SESSION
    _SESSION = None


def _check_microversion(url, microversion):
    """Checks to see if the requested microversion is supported by the current
    version of python-cinderclient and the volume API endpoint.

    :param url: Cinder API endpoint URL.
    :param microversion: Requested microversion. If not available at the given
        API endpoint URL, a CinderAPIVersionNotAvailable exception is raised.
    :returns: The microversion if it is available. This can be used to
        construct the cinder v3 client object.
    :raises: CinderAPIVersionNotAvailable if the microversion is not available.
    """
    max_api_version = cinder_client.get_highest_client_server_version(url)
    # get_highest_client_server_version returns a float which we need to cast
    # to a str and create an APIVersion object to do our version comparison.
    max_api_version = cinder_api_versions.APIVersion(str(max_api_version))
    # Check if the max_api_version matches the requested minimum microversion.
    if max_api_version.matches(microversion):
        # The requested microversion is supported by the client and the server.
        return microversion
    raise exception.CinderAPIVersionNotAvailable(version=microversion)


def cinderclient(context, microversion=None, skip_version_check=False):
    """Constructs a cinder client object for making API requests.

    :param context: The nova request context for auth.
    :param microversion: Optional microversion to check against the client.
        This implies that Cinder v3 is required for any calls that require a
        microversion. If the microversion is not available, this method will
        raise an CinderAPIVersionNotAvailable exception.
    :param skip_version_check: If True and a specific microversion is
        requested, the version discovery check is skipped and the microversion
        is used directly. This should only be used if a previous check for the
        same microversion was successful.
    """
    global _SESSION

    if not _SESSION:
        _SESSION = ks_loading.load_session_from_conf_options(
            CONF, nova.conf.cinder.cinder_group.name)

    url = None
    endpoint_override = None

    auth = service_auth.get_auth_plugin(context)
    service_type, service_name, interface = CONF.cinder.catalog_info.split(':')

    service_parameters = {'service_type': service_type,
                          'service_name': service_name,
                          'interface': interface,
                          'region_name': CONF.cinder.os_region_name}

    if CONF.cinder.endpoint_template:
        url = CONF.cinder.endpoint_template % context.to_dict()
        endpoint_override = url
    else:
        url = _SESSION.get_endpoint(auth, **service_parameters)

    # TODO(jamielennox): This should be using proper version discovery from
    # the cinder service rather than just inspecting the URL for certain string
    # values.
    version = cinder_client.get_volume_api_from_url(url)

    if version == '1':
        raise exception.UnsupportedCinderAPIVersion(version=version)

    if version == '2':
        if microversion is not None:
            # The Cinder v2 API does not support microversions.
            raise exception.CinderAPIVersionNotAvailable(version=microversion)
        LOG.warning("The support for the Cinder API v2 is deprecated, please "
                    "upgrade to Cinder API v3.")

    if version == '3':
        version = '3.0'
        # Check to see a specific microversion is requested and if so, can it
        # be handled by the backing server.
        if microversion is not None:
            if skip_version_check:
                version = microversion
            else:
                version = _check_microversion(url, microversion)

    return cinder_client.Client(version,
                                session=_SESSION,
                                auth=auth,
                                endpoint_override=endpoint_override,
                                connect_retries=CONF.cinder.http_retries,
                                global_request_id=context.global_id,
                                **service_parameters)


def _untranslate_volume_summary_view(context, vol):
    """Maps keys for volumes summary view."""
    d = {}
    d['id'] = vol.id
    d['status'] = vol.status
    d['size'] = vol.size
    d['availability_zone'] = vol.availability_zone
    d['created_at'] = vol.created_at

    # TODO(jdg): The calling code expects attach_time and
    #            mountpoint to be set. When the calling
    #            code is more defensive this can be
    #            removed.
    d['attach_time'] = ""
    d['mountpoint'] = ""
    d['multiattach'] = getattr(vol, 'multiattach', False)

    if vol.attachments:
        d['attachments'] = collections.OrderedDict()
        for attachment in vol.attachments:
            a = {attachment['server_id']:
                 {'attachment_id': attachment.get('attachment_id'),
                  'mountpoint': attachment.get('device')}
                 }
            d['attachments'].update(a.items())

        d['attach_status'] = 'attached'
    else:
        d['attach_status'] = 'detached'
    d['display_name'] = vol.name
    d['display_description'] = vol.description
    # TODO(jdg): Information may be lost in this translation
    d['volume_type_id'] = vol.volume_type
    d['snapshot_id'] = vol.snapshot_id
    d['bootable'] = strutils.bool_from_string(vol.bootable)
    d['volume_metadata'] = {}
    for key, value in vol.metadata.items():
        d['volume_metadata'][key] = value

    if hasattr(vol, 'volume_image_metadata'):
        d['volume_image_metadata'] = copy.deepcopy(vol.volume_image_metadata)

    return d


def _untranslate_snapshot_summary_view(context, snapshot):
    """Maps keys for snapshots summary view."""
    d = {}

    d['id'] = snapshot.id
    d['status'] = snapshot.status
    d['progress'] = snapshot.progress
    d['size'] = snapshot.size
    d['created_at'] = snapshot.created_at
    d['display_name'] = snapshot.name
    d['display_description'] = snapshot.description

    d['volume_id'] = snapshot.volume_id
    d['project_id'] = snapshot.project_id
    d['volume_size'] = snapshot.size

    return d


def _translate_attachment_ref(attachment_ref):
    """Building old style connection_info by adding the 'data' key back."""
    connection_info = attachment_ref.pop('connection_info', {})
    attachment_ref['connection_info'] = {}
    if connection_info.get('driver_volume_type'):
        attachment_ref['connection_info']['driver_volume_type'] = \
            connection_info['driver_volume_type']
    attachment_ref['connection_info']['data'] = {}
    for k, v in connection_info.items():
        if k != "driver_volume_type":
            attachment_ref['connection_info']['data'][k] = v
    return attachment_ref


def translate_cinder_exception(method):
    """Transforms a cinder exception but keeps its traceback intact."""
    @functools.wraps(method)
    def wrapper(self, ctx, *args, **kwargs):
        try:
            res = method(self, ctx, *args, **kwargs)
        except (cinder_exception.ConnectionError,
                keystone_exception.ConnectionError) as exc:
            err_msg = encodeutils.exception_to_unicode(exc)
            _reraise(exception.CinderConnectionFailed(reason=err_msg))
        except (keystone_exception.BadRequest,
                cinder_exception.BadRequest) as exc:
            err_msg = encodeutils.exception_to_unicode(exc)
            _reraise(exception.InvalidInput(reason=err_msg))
        except (keystone_exception.Forbidden,
                cinder_exception.Forbidden) as exc:
            err_msg = encodeutils.exception_to_unicode(exc)
            _reraise(exception.Forbidden(err_msg))
        return res
    return wrapper


def translate_volume_exception(method):
    """Transforms the exception for the volume but keeps its traceback intact.
    """
    def wrapper(self, ctx, volume_id, *args, **kwargs):
        try:
            res = method(self, ctx, volume_id, *args, **kwargs)
        except (keystone_exception.NotFound, cinder_exception.NotFound):
            _reraise(exception.VolumeNotFound(volume_id=volume_id))
        except cinder_exception.OverLimit as e:
            _reraise(exception.OverQuota(message=e.message))
        return res
    return translate_cinder_exception(wrapper)


def translate_attachment_exception(method):
    """Transforms the exception for the attachment but keeps its traceback intact.
    """
    def wrapper(self, ctx, attachment_id, *args, **kwargs):
        try:
            res = method(self, ctx, attachment_id, *args, **kwargs)
        except (keystone_exception.NotFound, cinder_exception.NotFound):
            _reraise(exception.VolumeAttachmentNotFound(
                attachment_id=attachment_id))
        return res
    return translate_cinder_exception(wrapper)


def translate_snapshot_exception(method):
    """Transforms the exception for the snapshot but keeps its traceback
       intact.
    """
    def wrapper(self, ctx, snapshot_id, *args, **kwargs):
        try:
            res = method(self, ctx, snapshot_id, *args, **kwargs)
        except (keystone_exception.NotFound, cinder_exception.NotFound):
            _reraise(exception.SnapshotNotFound(snapshot_id=snapshot_id))
        return res
    return translate_cinder_exception(wrapper)


def translate_mixed_exceptions(method):
    """Transforms exceptions that can come from both volumes and snapshots."""
    def wrapper(self, ctx, res_id, *args, **kwargs):
        try:
            res = method(self, ctx, res_id, *args, **kwargs)
        except (keystone_exception.NotFound, cinder_exception.NotFound):
            _reraise(exception.VolumeNotFound(volume_id=res_id))
        except cinder_exception.OverLimit:
            _reraise(exception.OverQuota(overs='snapshots'))
        return res
    return translate_cinder_exception(wrapper)


def _reraise(desired_exc):
    six.reraise(type(desired_exc), desired_exc, sys.exc_info()[2])


class API(object):
    """API for interacting with the volume manager."""

    @translate_volume_exception
    def get(self, context, volume_id):
        item = cinderclient(context).volumes.get(volume_id)
        return _untranslate_volume_summary_view(context, item)

    @translate_cinder_exception
    def get_all(self, context, search_opts=None):
        search_opts = search_opts or {}
        items = cinderclient(context).volumes.list(detailed=True,
                                                   search_opts=search_opts)

        rval = []

        for item in items:
            rval.append(_untranslate_volume_summary_view(context, item))

        return rval

    def check_attached(self, context, volume):
        if volume['status'] != "in-use":
            msg = _("volume '%(vol)s' status must be 'in-use'. Currently in "
                    "'%(status)s' status") % {"vol": volume['id'],
                                              "status": volume['status']}
            raise exception.InvalidVolume(reason=msg)

    def check_availability_zone(self, context, volume, instance=None):
        """Ensure that the availability zone is the same."""

        # TODO(walter-boring): move this check to Cinder as part of
        # the reserve call.
        if instance and not CONF.cinder.cross_az_attach:
            instance_az = az.get_instance_availability_zone(context, instance)
            if instance_az != volume['availability_zone']:
                msg = _("Instance %(instance)s and volume %(vol)s are not in "
                        "the same availability_zone. Instance is in "
                        "%(ins_zone)s. Volume is in %(vol_zone)s") % {
                            "instance": instance.uuid,
                            "vol": volume['id'],
                            'ins_zone': instance_az,
                            'vol_zone': volume['availability_zone']}
                raise exception.InvalidVolume(reason=msg)

    @translate_volume_exception
    def reserve_volume(self, context, volume_id):
        cinderclient(context).volumes.reserve(volume_id)

    @translate_volume_exception
    def unreserve_volume(self, context, volume_id):
        cinderclient(context).volumes.unreserve(volume_id)

    @translate_volume_exception
    def begin_detaching(self, context, volume_id):
        cinderclient(context).volumes.begin_detaching(volume_id)

    @translate_volume_exception
    def roll_detaching(self, context, volume_id):
        cinderclient(context).volumes.roll_detaching(volume_id)

    @translate_volume_exception
    def attach(self, context, volume_id, instance_uuid, mountpoint, mode='rw'):
        cinderclient(context).volumes.attach(volume_id, instance_uuid,
                                             mountpoint, mode=mode)

    @translate_volume_exception
    def detach(self, context, volume_id, instance_uuid=None,
               attachment_id=None):
        client = cinderclient(context)
        if attachment_id is None:
            volume = self.get(context, volume_id)
            if volume['multiattach']:
                attachments = volume.get('attachments', {})
                if instance_uuid:
                    attachment_id = attachments.get(instance_uuid, {}).\
                            get('attachment_id')
                    if not attachment_id:
                        LOG.warning(_LW("attachment_id couldn't be retrieved "
                                        "for volume %(volume_id)s with "
                                        "instance_uuid %(instance_id)s. The "
                                        "volume has the 'multiattach' flag "
                                        "enabled, without the attachment_id "
                                        "Cinder most probably cannot perform "
                                        "the detach."),
                                    {'volume_id': volume_id,
                                     'instance_id': instance_uuid})
                else:
                    LOG.warning(_LW("attachment_id couldn't be retrieved for "
                                    "volume %(volume_id)s. The volume has the "
                                    "'multiattach' flag enabled, without the "
                                    "attachment_id Cinder most probably "
                                    "cannot perform the detach."),
                                {'volume_id': volume_id})

        client.volumes.detach(volume_id, attachment_id)

    @translate_volume_exception
    def initialize_connection(self, context, volume_id, connector):
        try:
            connection_info = cinderclient(
                context).volumes.initialize_connection(volume_id, connector)
            connection_info['connector'] = connector
            return connection_info
        except cinder_exception.ClientException as ex:
            with excutils.save_and_reraise_exception():
                LOG.error(_LE('Initialize connection failed for volume '
                              '%(vol)s on host %(host)s. Error: %(msg)s '
                              'Code: %(code)s. Attempting to terminate '
                              'connection.'),
                          {'vol': volume_id,
                           'host': connector.get('host'),
                           'msg': six.text_type(ex),
                           'code': ex.code})
                try:
                    self.terminate_connection(context, volume_id, connector)
                except Exception as exc:
                    LOG.error(_LE('Connection between volume %(vol)s and host '
                                  '%(host)s might have succeeded, but attempt '
                                  'to terminate connection has failed. '
                                  'Validate the connection and determine if '
                                  'manual cleanup is needed. Error: %(msg)s '
                                  'Code: %(code)s.'),
                              {'vol': volume_id,
                               'host': connector.get('host'),
                               'msg': six.text_type(exc),
                               'code': (
                                exc.code if hasattr(exc, 'code') else None)})

    @translate_volume_exception
    def terminate_connection(self, context, volume_id, connector):
        return cinderclient(context).volumes.terminate_connection(volume_id,
                                                                  connector)

    @translate_cinder_exception
    def migrate_volume_completion(self, context, old_volume_id, new_volume_id,
                                  error=False):
        return cinderclient(context).volumes.migrate_volume_completion(
            old_volume_id, new_volume_id, error)

    @translate_volume_exception
    def create(self, context, size, name, description, snapshot=None,
               image_id=None, volume_type=None, metadata=None,
               availability_zone=None):
        client = cinderclient(context)

        if snapshot is not None:
            snapshot_id = snapshot['id']
        else:
            snapshot_id = None

        kwargs = dict(snapshot_id=snapshot_id,
                      volume_type=volume_type,
                      user_id=context.user_id,
                      project_id=context.project_id,
                      availability_zone=availability_zone,
                      metadata=metadata,
                      imageRef=image_id,
                      name=name,
                      description=description)

        item = client.volumes.create(size, **kwargs)
        return _untranslate_volume_summary_view(context, item)

    @translate_volume_exception
    def delete(self, context, volume_id):
        cinderclient(context).volumes.delete(volume_id)

    @translate_volume_exception
    def update(self, context, volume_id, fields):
        raise NotImplementedError()

    @translate_snapshot_exception
    def get_snapshot(self, context, snapshot_id):
        item = cinderclient(context).volume_snapshots.get(snapshot_id)
        return _untranslate_snapshot_summary_view(context, item)

    @translate_cinder_exception
    def get_all_snapshots(self, context):
        items = cinderclient(context).volume_snapshots.list(detailed=True)
        rvals = []

        for item in items:
            rvals.append(_untranslate_snapshot_summary_view(context, item))

        return rvals

    @translate_mixed_exceptions
    def create_snapshot(self, context, volume_id, name, description):
        item = cinderclient(context).volume_snapshots.create(volume_id,
                                                             False,
                                                             name,
                                                             description)
        return _untranslate_snapshot_summary_view(context, item)

    @translate_mixed_exceptions
    def create_snapshot_force(self, context, volume_id, name, description):
        item = cinderclient(context).volume_snapshots.create(volume_id,
                                                             True,
                                                             name,
                                                             description)

        return _untranslate_snapshot_summary_view(context, item)

    @translate_snapshot_exception
    def delete_snapshot(self, context, snapshot_id):
        cinderclient(context).volume_snapshots.delete(snapshot_id)

    @translate_cinder_exception
    def get_volume_encryption_metadata(self, context, volume_id):
        return cinderclient(context).volumes.get_encryption_metadata(volume_id)

    @translate_snapshot_exception
    def update_snapshot_status(self, context, snapshot_id, status):
        vs = cinderclient(context).volume_snapshots

        # '90%' here is used to tell Cinder that Nova is done
        # with its portion of the 'creating' state. This can
        # be removed when we are able to split the Cinder states
        # into 'creating' and a separate state of
        # 'creating_in_nova'. (Same for 'deleting' state.)

        vs.update_snapshot_status(
            snapshot_id,
            {'status': status,
             'progress': '90%'}
        )

    @translate_volume_exception
    def attachment_create(self, context, volume_id, instance_id,
                          connector=None):
        """Create a volume attachment. This requires microversion >= 3.27.

        :param context: The nova request context.
        :param volume_id: UUID of the volume on which to create the attachment.
        :param instance_id: UUID of the instance to which the volume will be
            attached.
        :param connector: host connector dict; if None, the attachment will
            be 'reserved' but not yet attached.
        :returns: a dict created from the
            cinderclient.v3.attachments.VolumeAttachment object with a backward
            compatible connection_info dict
        """
        try:
            attachment_ref = cinderclient(context, '3.27').attachments.create(
                volume_id, connector, instance_id)
            return _translate_attachment_ref(attachment_ref)
        except cinder_exception.ClientException as ex:
            with excutils.save_and_reraise_exception():
                LOG.error(('Create attachment failed for volume '
                           '%(volume_id)s. Error: %(msg)s Code: %(code)s'),
                          {'volume_id': volume_id,
                           'msg': six.text_type(ex),
                           'code': getattr(ex, 'code', None)},
                          instance_uuid=instance_id)

    @translate_attachment_exception
    def attachment_update(self, context, attachment_id, connector):
        """Updates the connector on the volume attachment. An attachment
        without a connector is considered reserved but not fully attached.

        :param context: The nova request context.
        :param attachment_id: UUID of the volume attachment to update.
        :param connector: host connector dict. This is required when updating
            a volume attachment. To terminate a connection, the volume
            attachment for that connection must be deleted.
        :returns: a dict created from the
            cinderclient.v3.attachments.VolumeAttachment object with a backward
            compatible connection_info dict
        """
        try:
            attachment_ref = cinderclient(
                context, '3.27', skip_version_check=True).attachments.update(
                    attachment_id, connector)
            return _translate_attachment_ref(attachment_ref.to_dict())
        except cinder_exception.ClientException as ex:
            with excutils.save_and_reraise_exception():
                LOG.error(('Update attachment failed for attachment '
                           '%(id)s. Error: %(msg)s Code: %(code)s'),
                          {'id': attachment_id,
                           'msg': six.text_type(ex),
                           'code': getattr(ex, 'code', None)})

    @translate_attachment_exception
    def attachment_delete(self, context, attachment_id):
        try:
            cinderclient(
                context, '3.27', skip_version_check=True).attachments.delete(
                    attachment_id)
        except cinder_exception.ClientException as ex:
            with excutils.save_and_reraise_exception():
                LOG.error(('Delete attachment failed for attachment '
                           '%(id)s. Error: %(msg)s Code: %(code)s'),
                          {'id': attachment_id,
                           'msg': six.text_type(ex),
                           'code': getattr(ex, 'code', None)})
