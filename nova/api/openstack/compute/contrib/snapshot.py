from nova.api.openstack import common
from nova import compute
from nova.api.openstack import extensions
from nova.openstack.common import log as logging
from nova.api.openstack import wsgi
from nova.sihuatech.snapshot import rpcapi as compute_rpcapi

authorize = extensions.extension_authorizer('compute', 'snapshot')

LOG = logging.getLogger(__name__)


class SnapshotController(wsgi.Controller):
    """the SnapShot API Controller declearation"""

    def __init__(self, *args, **kwargs):
        self.compute_api = compute.API()
        self.compute_rpcapi = compute_rpcapi.ComputeAPI()
        super(SnapshotController, self).__init__(*args, **kwargs)

    @wsgi.action('snapshot_list')
    def snapshot_list(self, req, id,body):
        context = req.environ['nova.context']
        authorize(context)
        instance = common.get_instance(self.compute_api, context, id, want_objects=True)
        snapshotList= self.compute_rpcapi.snapshot_list(context, instance)
        return snapshotList

    @wsgi.action('snapshot_create')
    def snapshot_create(self, req, id, body):
        snapshot_name = body['snapshot_create'].get('snapshot_name')
        snapshot_desc = body['snapshot_create'].get('snapshot_desc')
        context = req.environ['nova.context']
        authorize(context)
        instance = common.get_instance(self.compute_api, context, id, want_objects=True)
        return self.compute_rpcapi.snapshot_create(context, instance, snapshot_name,snapshot_desc)

    @wsgi.action('snapshot_delete')
    def snapshot_delete(self, req, id, body):
        snapshot_name = body['snapshot_delete'].get('snapshot_name')
        context = req.environ['nova.context']
        authorize(context)
        instance = common.get_instance(compute.API(), context, id, want_objects=True)
        return self.compute_rpcapi.snapshot_delete(context, instance, snapshot_name)

    @wsgi.action('snapshot_revert')
    def snapshot_revert(self, req, id, body):
        snapshot_name = body['snapshot_revert'].get('snapshot_name')
        context = req.environ['nova.context']
        authorize(context)
        instance = common.get_instance(compute.API(), context, id, want_objects=True)
        return self.compute_rpcapi.snapshot_revert(context, instance, snapshot_name)

    @wsgi.action('snapshot_current')
    def snapshot_current(self, req, id, body):
        context = req.environ['nova.context']
        authorize(context)
        instance = common.get_instance(compute.API(), context, id, want_objects=True)
        return self.compute_rpcapi.snapshot_current(context, instance)


class Snapshot(extensions.ExtensionDescriptor):
    """Snapshot ExtensionDescriptor implementation"""

    name = "snapshot"
    alias = "snapshot"
    namespace = "www.sihuatech.com"
    updated = "2015-08-26 00:00:01"

    def get_controller_extensions(self):
        controller = SnapshotController()
        extension = extensions.ControllerExtension(self, 'servers', controller)
        return [extension]
