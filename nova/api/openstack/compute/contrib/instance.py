from nova.api.openstack import common
from nova import compute
from nova.api.openstack import extensions
from nova.openstack.common import log as logging
from nova.api.openstack import wsgi
from nova.sihuatech import rpcapi as compute_rpcapi

authorize = extensions.extension_authorizer('compute', 'instance')

LOG = logging.getLogger(__name__)


class InstanceController(wsgi.Controller):
    """the Instance API Controller declearation"""

    def __init__(self, *args, **kwargs):
        self.compute_api = compute.API()
        self.compute_rpcapi = compute_rpcapi.ComputeAPI()
        super(InstanceController, self).__init__(*args, **kwargs)

    @wsgi.action('guest_live_update')
    def guest_live_update(self, req, id,body):
        vcpus = body['guest_live_update'].get("vcpus")
        memory_size = body['guest_live_update'].get("memory_size")
        context = req.environ['nova.context']
        authorize(context)
        instance = common.get_instance(self.compute_api, context, id, want_objects=True)
        return self.compute_rpcapi.guest_live_update(context, instance,vcpus,memory_size)


class Instance(extensions.ExtensionDescriptor):
    """Snapshot ExtensionDescriptor implementation"""

    name = "instance"
    alias = "instance"
    namespace = "www.sihuatech.com"
    updated = "2015-11-11 00:00:01"

    def get_controller_extensions(self):
        controller = InstanceController()
        extension = extensions.ControllerExtension(self, 'servers', controller)
        return [extension]
