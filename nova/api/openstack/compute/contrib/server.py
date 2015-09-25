from nova.api.openstack import common
from nova import compute
from nova.api.openstack import extensions
from nova.openstack.common import log as logging
from nova.api.openstack import wsgi
from nova.sihuatech import rpcapi as compute_rpcapi

authorize = extensions.extension_authorizer('compute', 'server')

LOG = logging.getLogger(__name__)


class ServerController(wsgi.Controller):
    """the Server API Controller declearation"""

    def __init__(self, *args, **kwargs):
        self.compute_api = compute.API()
        self.compute_rpcapi = compute_rpcapi.ComputeAPI()
        super(ServerController, self).__init__(*args, **kwargs)

    @wsgi.action('guest_set_user_password')
    def guest_set_user_password(self, req, id,body):
        context = req.environ['nova.context']
        authorize(context)
        instance = common.get_instance(self.compute_api, context, id, want_objects=True)

        user_name = body['guest_set_user_password'].get('user_name')
        user_password = body['guest_set_user_password'].get('user_password')

        return self.compute_rpcapi.guest_set_user_password(context, instance,user_name, user_password)


class Server(extensions.ExtensionDescriptor):
    """Server ExtensionDescriptor implementation"""

    name = "server"
    alias = "server"
    namespace = "www.sihuatech.com"
    updated = "2015-09-24 00:00:01"

    def get_controller_extensions(self):
        controller = ServerController()
        extension = extensions.ControllerExtension(self, 'servers', controller)
        return [extension]
